"""CardDAV: principal, address book and contact resources."""
import calendar
import datetime
import logging
import re

import vobject

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import plaintext2html, html2plaintext
from odoo.tools.translate import LazyTranslate

from . import vcard, xml
from .resource import NOT_FOUND, DavError, Resource
from .xml import q

_logger = logging.getLogger(__name__)

_lt = LazyTranslate(__name__)
A = lambda n: q('A', n)  # noqa: E731
D = lambda n: q('D', n)  # noqa: E731
CS = lambda n: q('CS', n)  # noqa: E731

SYNC_LAG = datetime.timedelta(seconds=5)
TOKEN_PREFIX = 'urn:odoo-dav:sync:'
_NAME_RE = re.compile(r'^[A-Za-z0-9._@=+-]{1,120}\.vcf$')
_ID_RE = re.compile(r'^(\d+)\.vcf$')


def _to_micro(dt):
    return calendar.timegm(dt.timetuple()) * 1_000_000 + dt.microsecond


def _from_micro(value):
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(microseconds=value)


def make_token(dt):
    return '%s%d' % (TOKEN_PREFIX, _to_micro(dt))


def parse_token(token):
    if not token:
        return None
    if not token.startswith(TOKEN_PREFIX):
        raise DavError(403, 'Invalid sync token', (D('valid-sync-token'),))
    try:
        return _from_micro(int(token[len(TOKEN_PREFIX):]))
    except (ValueError, OverflowError):
        raise DavError(403, 'Invalid sync token', (D('valid-sync-token'),))


class Principal(Resource):
    is_collection = True

    def display_name(self):
        return self.ctx.user.name

    def resource_types(self):
        return [xml.el(D('collection')), xml.el(D('principal'))]

    def allprop_names(self):
        return super().allprop_names() + [A('addressbook-home-set')]

    def prop(self, name):
        if name == A('addressbook-home-set'):
            return xml.href('%s/addressbooks/%s/' % (self.ctx.prefix, vcard_quote(self.ctx.login)))
        if name == q('C', 'calendar-home-set') and self.ctx.has_path('calendars', self.ctx.login):
            return xml.href('%s/calendars/%s/' % (self.ctx.prefix, vcard_quote(self.ctx.login)))
        if name == D('principal-URL'):
            return xml.href(self.ctx.principal_href)
        return super().prop(name)


class AddressBook(Resource):
    is_collection = True
    writable = True
    reports = (A('addressbook-multiget'), A('addressbook-query'), D('sync-collection'))

    # ------------------------------------------------------------ plumbing
    @property
    def env(self):
        return self.ctx.env

    def _domain(self):
        return [('active', '=', True), '|', ('is_company', '=', True), ('type', '=', 'contact')]

    def _extras_for(self, partners):
        cache = self.ctx.__dict__.setdefault('_vcard_extras', {})
        missing = partners.filtered(lambda p: p.id not in cache)
        if missing:
            for pid in missing.ids:
                cache[pid] = None
            for extra in self.env['dav.vcard.extra'].search([('partner_id', 'in', missing.ids)]):
                cache[extra.partner_id.id] = extra
        return cache

    def _contact(self, partner):
        extras = self._extras_for(partner)
        return Contact(self.ctx, self.path + (self._resource_name(partner, extras.get(partner.id)),),
                       partner, self)

    @staticmethod
    def _resource_name(partner, extra):
        return (extra.resource_name if extra and extra.resource_name else '%d.vcf' % partner.id)

    # ----------------------------------------------------------- properties
    def display_name(self):
        return self.tr(_lt('Contacts'))

    def resource_types(self):
        return [xml.el(D('collection')), xml.el(A('addressbook'))]

    def allprop_names(self):
        return super().allprop_names() + [CS('getctag'), D('sync-token')]

    def prop(self, name):
        if name == CS('getctag'):
            return self._ctag()
        if name == D('sync-token'):
            return self._current_token()
        if name == A('supported-address-data'):
            return xml.el(A('address-data-type'), content_type='text/vcard', version='3.0')
        if name == A('max-resource-size'):
            return str(vcard.MAX_VCARD_BYTES)
        if name == A('addressbook-description'):
            return self.tr(_lt('Odoo contacts'))
        return super().prop(name)

    def _ctag(self):
        Partner = self.env['res.partner']
        last = Partner.with_context(active_test=False).search([], order='write_date desc', limit=1)
        count = Partner.search_count(self._domain())
        cats = self.env['res.partner.category'].with_context(active_test=False).search(
            [], order='write_date desc', limit=1)
        return vcard.etag_of(count, last.write_date, cats.write_date, self._current_token())

    # -------------------------------------------------------------- children
    def _partners(self):
        limit = self.ctx.param_int('max_contacts', 20000)
        partners = self.env['res.partner'].search(self._domain(), limit=limit + 1, order='id')
        if len(partners) > limit:
            raise DavError(507, 'Too many contacts for a DAV address book')
        return partners

    def children(self):
        partners = self._partners()
        self._extras_for(partners)
        return [self._contact(p) for p in partners]

    def get_child(self, name):
        Partner = self.env['res.partner']
        partner = Partner
        match = _ID_RE.match(name)
        if match:
            partner = Partner.search(self._domain() + [('id', '=', int(match.group(1)))], limit=1)
            if partner:
                extra = self.env['dav.vcard.extra'].search([('partner_id', '=', partner.id)], limit=1)
                if extra and extra.resource_name and extra.resource_name != name:
                    partner = Partner  # addressed by its client-chosen name only
        elif _NAME_RE.match(name):
            extra = self.env['dav.vcard.extra'].search([('resource_name', '=', name)], limit=1)
            partner = Partner.search(self._domain() + [('id', '=', extra.partner_id.id)], limit=1) if extra else Partner
        if not partner:
            return None
        return self._contact(partner)

    def query(self, root):
        return self.children()

    # --------------------------------------------------------------- writing
    def create_child(self, name, data, content_type, request_info):
        if not _NAME_RE.match(name):
            raise DavError(403, 'Invalid contact name', (A('valid-address-data'),))
        parsed = _parse(data)
        partner = apply_card(self.ctx, None, parsed, resource_name=name)
        return self._contact(partner)

    # ----------------------------------------------------------------- sync
    def _sync_domains(self, since):
        Partner = self.env['res.partner']
        base = [('write_date', '>', since)] if since else []
        changed = Partner.search(self._domain() + base, order='id')
        gone, seen = [], [changed.mapped('write_date')]
        if since:
            archived = Partner.with_context(active_test=False).search(
                [('active', '=', False), '|', ('is_company', '=', True), ('type', '=', 'contact')] + base)
            extras = self.env['dav.vcard.extra'].search([('partner_id', 'in', archived.ids)])
            names = {e.partner_id.id: e.resource_name for e in extras}
            gone += [names.get(p.id) or '%d.vcf' % p.id for p in archived]
            seen.append(archived.mapped('write_date'))
            tombs = self.env['dav.deletion'].sudo().search(
                [('model', '=', 'res.partner'), ('deleted_at', '>', since)]
                + self.env['dav.deletion']._visible_domain())
            gone += [t.resource_name or '%d.vcf' % t.res_id for t in tombs]
            seen.append(tombs.mapped('deleted_at'))
        latest = max([d for group in seen for d in group], default=None)
        return changed, gone, latest

    def _clamp_token(self, since, latest):
        ceiling = fields.Datetime.now() - SYNC_LAG
        candidate = min(latest, ceiling) if latest else ceiling
        if since and candidate < since:
            candidate = since
        return candidate

    def _current_token(self):
        Partner = self.env['res.partner']
        latest = max(
            Partner.with_context(active_test=False).search(
                [], order='write_date desc', limit=1).write_date or datetime.datetime.min,
            self.env['dav.deletion'].sudo().search(
                self.env['dav.deletion']._visible_domain(), order='deleted_at desc', limit=1).deleted_at
            or datetime.datetime.min)
        return make_token(self._clamp_token(None, latest if latest > datetime.datetime.min else None))

    def sync(self, token):
        since = parse_token(token)
        changed, gone, latest = self._sync_domains(since)
        self._extras_for(changed)
        new = self._clamp_token(since, latest)
        gone_hrefs = [self.href + vcard_quote(n) for n in gone]
        return [self._contact(p) for p in changed], gone_hrefs, make_token(new)


def vcard_quote(name):
    from urllib.parse import quote
    return quote(name, safe='@')


class Contact(Resource):
    writable = True

    def __init__(self, ctx, path, partner, book):
        super().__init__(ctx, path)
        self.partner = partner
        self.book = book

    @property
    def extra(self):
        return self.book._extras_for(self.partner).get(self.partner.id)

    def display_name(self):
        return self.partner.name or self.partner.display_name

    def etag(self):
        p = self.partner
        extra = self.extra
        return vcard.etag_of(
            p.id, p.write_date, p.parent_id.name, p.parent_id.write_date,
            ','.join(sorted(p.category_id.mapped('display_name'))),
            extra.write_date if extra else '')

    def last_modified(self):
        return self.partner.write_date

    def content_type(self):
        return 'text/vcard; charset=utf-8'

    def prop(self, name):
        if name == A('address-data'):
            return self._render()
        return super().prop(name)

    def _render(self):
        return vcard.build(self.partner, self.extra, self.ctx.db_uuid, self.ctx.param_bool('vcard_photos', True))

    def read(self):
        return self._render().encode('utf-8'), self.content_type()

    # --------------------------------------------------------------- writing
    def put(self, data, content_type, request_info):
        apply_card(self.ctx, self.partner, _parse(data))
        return self

    def delete(self):
        p = self.partner
        if p.is_company or p.user_ids or p.child_ids or p == self.ctx.user.partner_id:
            raise DavError(403, 'This contact cannot be deleted through DAV')
        try:
            p.write({'active': False})
        except (AccessError, UserError, ValidationError) as exc:
            raise DavError(403, str(exc))


def _parse(data):
    try:
        return vcard.parse(data)
    except vcard.VCardError as exc:
        raise DavError(403, str(exc), (A('valid-address-data'),))


# ----------------------------------------------------------------------------
# Writing a vCard into Odoo. Only an allow-list of fields is ever written;
# `parent_id` is only set by exact (normalised) match of ORG on a visible
# company, commercial fields are never written.
# ----------------------------------------------------------------------------

def _norm(text):
    return ' '.join((text or '').split()).casefold()


def _find_company(env, org):
    norm = _norm(org)
    if not norm:
        return env['res.partner']
    token = norm.split(' ')[0]
    candidates = env['res.partner'].search([('is_company', '=', True), ('name', 'ilike', token)])
    return candidates.filtered(lambda c: _norm(c.name) == norm)


def _address_tuple(partner):
    return tuple(partner[f] if not hasattr(partner[f], 'id') else partner[f].id
                 for f in ('street', 'street2', 'city', 'zip', 'state_id', 'country_id'))


def _can_link(partner, company):
    """A standalone contact may be attached to a company only if that does not
    overwrite data of its own (Odoo copies the company's address and
    commercial fields onto the child)."""
    if not partner:
        return True
    Partner = partner.env['res.partner']
    for fname in Partner._address_fields():
        mine, theirs = partner[fname], company[fname]
        if mine and mine != theirs:
            return False
    for fname in Partner._synced_commercial_fields():
        mine, theirs = partner[fname], company[fname]
        if mine and mine != theirs:
            return False
    return True


def _resolve_country(env, text):
    if not text:
        return env['res.country']
    return env['res.country'].search(['|', ('name', '=ilike', text), ('code', '=ilike', text)], limit=1)


def _resolve_state(env, text, country):
    if not text:
        return env['res.country.state']
    domain = ['|', ('name', '=ilike', text), ('code', '=ilike', text)]
    if country:
        domain.append(('country_id', '=', country.id))
    return env['res.country.state'].search(domain, limit=1)


def apply_card(ctx, partner, parsed, resource_name=None):
    env = ctx.env
    if parsed.is_company or (partner and (partner.is_company or partner.type != 'contact')):
        raise DavError(403, 'Companies and non-contact addresses are read-only through DAV')
    create = not partner
    Partner = env['res.partner']
    vals = {}

    def put(fname, value):
        if isinstance(value, str) and len(value) > vcard.MAX_TEXT_CHARS:
            raise DavError(403, 'Value too long for %s' % fname, (A('valid-address-data'),))
        if create or partner[fname] != value:
            vals[fname] = value

    put('name', parsed.fn)
    put('email', parsed.email or False)
    put('phone', parsed.phone or False)
    put('function', parsed.title or False)
    put('website', parsed.url or False)

    # ----- comment: only touched when the client sends a NOTE that differs
    if parsed.note is not None:
        current = html2plaintext(partner.comment or '').strip() if partner else ''
        if parsed.note != current:
            vals['comment'] = plaintext2html(parsed.note) if parsed.note else False

    # ----- company link (ORG)
    extra_lines = list(parsed.extra)
    current_parent = partner.parent_id if partner else Partner
    effective_org = (current_parent.name or partner.company_name or '') if partner else ''
    new_parent = current_parent
    if parsed.org and _norm(parsed.org) != _norm(effective_org):
        matches = _find_company(env, parsed.org)
        if len(matches) == 1 and (current_parent or _can_link(partner, matches)):
            new_parent = matches
            vals['parent_id'] = matches.id
            vals['company_name'] = False
        else:
            if len(matches) > 1:
                _logger.info('DAV: ambiguous ORG %r for partner %s; not linked', parsed.org, partner.id if partner else 'new')
            if not current_parent:
                vals['company_name'] = parsed.org
            else:
                _logger.info('DAV: ORG change %r ignored for linked partner %s', parsed.org, partner.id)
    elif not parsed.org and not current_parent and partner and partner.company_name:
        vals['company_name'] = False

    # ----- address: never written on contacts that follow their company
    if new_parent:
        if parsed.adr_raw:
            theirs = _address_tuple(new_parent)
            mine = (parsed.adr['street'], parsed.adr['street2'], parsed.adr['city'], parsed.adr['zip'])
            if tuple(x or '' for x in theirs[:4]) != mine:
                extra_lines.append(parsed.adr_raw)
    else:
        adr = parsed.adr or {}
        country = _resolve_country(env, adr.get('country'))
        state = _resolve_state(env, adr.get('state'), country)
        put('street', adr.get('street') or False)
        put('street2', adr.get('street2') or False)
        put('city', adr.get('city') or False)
        put('zip', adr.get('zip') or False)
        put('country_id', country.id or False)
        put('state_id', state.id or False)

    # ----- categories: only when the card carries a CATEGORIES property
    if parsed.categories:
        by_name = {}
        for cat in env['res.partner.category'].search([]):
            for key in {_norm(cat.display_name), _norm(cat.name)}:
                by_name[key] = by_name.get(key, env['res.partner.category']) | cat
        ids, unknown = set(), []
        for name in parsed.categories:
            hit = by_name.get(_norm(name))
            if hit and len(hit) == 1:
                ids.add(hit.id)
            else:
                unknown.append(name)
        vals['category_id'] = [(6, 0, sorted(ids))]
        if unknown:
            card = vobject.vCard()
            card.add('categories').value = unknown
            extra_lines.append(card.contents['categories'][0].serialize())

    # ----- photo: only when present and different from what we serve
    if parsed.photo and ctx.param_bool('vcard_photos', True):
        if create or parsed.photo != vcard.photo_bytes(partner):
            cleaned = vcard.clean_photo(parsed.photo)
            if cleaned:
                vals['image_1920'] = cleaned

    if create:
        if len(env.user.company_ids) == 1:
            vals['company_id'] = env.user.company_id.id
        vals.setdefault('is_company', False)
        vals.setdefault('type', 'contact')
        try:
            partner = Partner.create(vals)
        except (AccessError, UserError, ValidationError) as exc:
            raise DavError(403, str(exc))
    elif vals:
        try:
            partner.write(vals)
        except (AccessError, UserError, ValidationError) as exc:
            raise DavError(403, str(exc))

    # ----- DAV-only data
    extra_vals = {
        'uid': parsed.uid,
        'lines': ''.join(extra_lines) or False,
        'email_params': parsed.email_params if parsed.email else False,
        'phone_params': parsed.phone_params if parsed.phone else False,
        'adr_params': parsed.adr_params if (parsed.adr and not new_parent) else False,
    }
    extra = env['dav.vcard.extra'].search([('partner_id', '=', partner.id)], limit=1)
    if extra:
        changed = {k: v for k, v in extra_vals.items() if extra[k] != v}
        if changed:
            extra.write(changed)
    elif any(extra_vals.values()) or resource_name:
        extra_vals.update(partner_id=partner.id, resource_name=resource_name)
        extra = env['dav.vcard.extra'].create(extra_vals)
    ctx.__dict__.setdefault('_vcard_extras', {})[partner.id] = extra or None
    partner._dav_touch()
    return partner
