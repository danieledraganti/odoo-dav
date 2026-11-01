"""Shared CalDAV machinery: a calendar collection backed by an Odoo model."""
import datetime
import logging
import re
import uuid
from urllib.parse import quote

import vobject
from dateutil.tz import tzutc
from vobject.base import ParseError

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError

from . import vcard, xml
from .contacts import SYNC_LAG, make_token, parse_token
from .resource import DavError, Resource
from .xml import q

_logger = logging.getLogger(__name__)

C = lambda n: q('C', n)  # noqa: E731
D = lambda n: q('D', n)  # noqa: E731
CS = lambda n: q('CS', n)  # noqa: E731

MAX_ICS_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 1024  # indexed text columns overflow PostgreSQL btree rows above ~2.7 KB
_NAME_RE = re.compile(r'^[A-Za-z0-9._@=+-]{1,120}\.ics$')
_ID_RE = re.compile(r'^(\d+)\.ics$')
UTC = tzutc()  # vobject can't guess a TZID for datetime.timezone.utc


# ------------------------------------------------------------ date helpers

def check_text(value):
    if value and len(value) > MAX_TEXT_CHARS:
        raise DavError(403, 'Value too long', (C('valid-calendar-data'),))
    return value


def to_utc_naive(value, tz_name):
    """iCalendar date/datetime -> naive UTC datetime (floating = user's tz)."""
    import pytz
    if value is None:
        return None
    if not isinstance(value, datetime.datetime):  # a DATE
        value = datetime.datetime.combine(value, datetime.time(0))
    if value.tzinfo is None:
        zone = pytz.timezone(tz_name) if tz_name else pytz.UTC
        value = zone.localize(value)
    return value.astimezone(pytz.UTC).replace(tzinfo=None)


def from_naive_utc(value):
    return value.replace(tzinfo=UTC) if value else None


def parse_ics(data, component):
    """Parse a calendar object. Returns (vcalendar, main component)."""
    if isinstance(data, bytes):
        try:
            data = data.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise DavError(403, 'iCalendar must be UTF-8', (C('valid-calendar-data'),)) from exc
    if len(data) > MAX_ICS_BYTES:
        raise DavError(413, 'Calendar object too large')
    try:
        cal = vobject.readOne(data)
    except (ParseError, StopIteration, ValueError, AttributeError) as exc:
        raise DavError(403, 'Invalid iCalendar data', (C('valid-calendar-data'),)) from exc
    if cal.name.upper() != 'VCALENDAR':
        raise DavError(403, 'Not an iCalendar object', (C('valid-calendar-data'),))
    mains = [c for c in cal.getChildren() if c.name.upper() == component]
    if len(mains) != 1:
        raise DavError(403, 'Exactly one %s expected' % component, (C('valid-calendar-object-resource'),))
    return cal, mains[0]


def extra_lines_of(comp, handled):
    """Serialize every child of `comp` whose name is not in `handled`."""
    return ''.join(child.serialize() for child in comp.getChildren()
                   if child.name.upper() not in handled)


def touch(record):
    """Bump write_date without ORM side effects (DAV-only data changed)."""
    record.env.cr.execute(
        'UPDATE "%s" SET write_date = (now() AT TIME ZONE \'UTC\') WHERE id = %%s' % record._table,
        [record.id])
    record.invalidate_recordset(['write_date'])


class CalendarCollection(Resource):
    is_collection = True
    writable = True
    reports = (C('calendar-multiget'), C('calendar-query'), D('sync-collection'))

    model = None            # Odoo model name
    component = None        # 'VTODO' | 'VEVENT'
    display = 'Calendar'
    max_items = 20000

    # ----------------------------------------------- to implement in children
    def scope_domain(self):
        """Domain selecting the records of this collection (without `active`)."""
        raise NotImplementedError

    def build_ics(self, record, mapping):
        raise NotImplementedError

    def apply_ics(self, record, comp, mapping):
        """Create (record=None) or update a record from the VTODO/VEVENT.

        Must return ``(record, extra_lines)``.
        """
        raise NotImplementedError

    def etag_parts(self, record):
        return (record.id, record.write_date)

    def archive(self, record):
        record.write({'active': False})

    # ---------------------------------------------------------------- basics
    @property
    def env(self):
        return self.ctx.env

    def display_name(self):
        return self.display

    def resource_types(self):
        return [xml.el(D('collection')), xml.el(C('calendar'))]

    def allprop_names(self):
        return super().allprop_names() + [CS('getctag'), D('sync-token')]

    def prop(self, name):
        if name == CS('getctag'):
            return self._ctag()
        if name == D('sync-token'):
            return self._current_token()
        if name == C('supported-calendar-component-set'):
            return xml.el(C('comp'), name=self.component)
        if name == C('supported-calendar-data'):
            return xml.el(C('calendar-data'), content_type='text/calendar', version='2.0')
        if name == C('max-resource-size'):
            return str(MAX_ICS_BYTES)
        if name == C('calendar-description'):
            return self.display
        return super().prop(name)

    def _domain(self):
        return [('active', '=', True)] + self.scope_domain()

    # --------------------------------------------------------------- mapping
    def _maps(self, records):
        cache = self.ctx.__dict__.setdefault('_dav_maps', {}).setdefault(self.model, {})
        missing = [rid for rid in records.ids if rid not in cache]
        if missing:
            for rid in missing:
                cache[rid] = None
            for m in self.env['dav.resource.name'].sudo().search(
                    [('model', '=', self.model), ('res_id', 'in', missing)]):
                cache[m.res_id] = m
        return cache

    def _resource_name(self, record, mapping):
        return mapping.resource_name if mapping and mapping.resource_name else '%d.ics' % record.id

    def _item(self, record):
        mapping = self._maps(record).get(record.id)
        return CalendarObject(self.ctx, self.path + (self._resource_name(record, mapping),),
                              record, mapping, self)

    # ------------------------------------------------------------------ list
    def _records(self):
        Model = self.env[self.model]
        records = Model.search(self._domain(), limit=self.max_items + 1, order='id')
        if len(records) > self.max_items:
            raise DavError(507, 'Too many items for a DAV calendar')
        return records

    def children(self):
        records = self._records()
        self._maps(records)
        return [self._item(r) for r in records]

    def get_child(self, name):
        Model = self.env[self.model]
        record = Model
        match = _ID_RE.match(name)
        if match:
            record = Model.search(self._domain() + [('id', '=', int(match.group(1)))], limit=1)
            mapping = self._maps(record).get(record.id) if record else None
            if mapping and mapping.resource_name and mapping.resource_name != name:
                record = Model
        elif _NAME_RE.match(name):
            mapping = self.env['dav.resource.name'].sudo().search(
                [('model', '=', self.model), ('resource_name', '=', name)], limit=1)
            if mapping:
                record = Model.search(self._domain() + [('id', '=', mapping.res_id)], limit=1)
        return self._item(record) if record else None

    def query(self, root):
        return self.children()

    # ---------------------------------------------------------------- writing
    def create_child(self, name, data, content_type, request_info):
        if not _NAME_RE.match(name):
            raise DavError(403, 'Invalid resource name', (C('valid-calendar-object-resource'),))
        _cal, comp = parse_ics(data, self.component)
        try:
            record, extra = self.apply_ics(None, comp, None)
        except (AccessError, UserError, ValidationError) as exc:
            raise DavError(403, str(exc))
        uid = str(comp.contents['uid'][0].value) if 'uid' in comp.contents else str(uuid.uuid4())
        self.env['dav.resource.name'].sudo().create({
            'model': self.model, 'res_id': record.id, 'resource_name': name,
            'uid': uid[:255], 'extra_lines': extra or False})
        touch(record)
        self.ctx.__dict__.get('_dav_maps', {}).pop(self.model, None)
        return self._item(record)

    # ------------------------------------------------------------------ sync
    def _ctag(self):
        Model = self.env[self.model]
        last = Model.with_context(active_test=False).search(
            self.scope_domain(), order='write_date desc', limit=1)
        return vcard.etag_of(Model.search_count(self._domain()), last.write_date, self._current_token())

    def _clamp(self, since, latest):
        ceiling = fields.Datetime.now() - SYNC_LAG
        candidate = min(latest, ceiling) if latest else ceiling
        if since and candidate < since:
            candidate = since
        return candidate

    def _changes(self, since):
        Model = self.env[self.model]
        base = [('write_date', '>', since)] if since else []
        changed = Model.search(self._domain() + base, order='id')
        gone, seen = [], [changed.mapped('write_date')]
        if since:
            archived = Model.with_context(active_test=False).search(
                [('active', '=', False)] + self.scope_domain() + base)
            names = {m.res_id: m.resource_name for m in self.env['dav.resource.name'].sudo().search(
                [('model', '=', self.model), ('res_id', 'in', archived.ids)])}
            gone += [names.get(r.id) or '%d.ics' % r.id for r in archived]
            seen.append(archived.mapped('write_date'))
            tombs = self.env['dav.deletion'].sudo().search(
                [('model', '=', self.model), ('deleted_at', '>', since)]
                + self.env['dav.deletion']._visible_domain())
            gone += [t.resource_name or '%d.ics' % t.res_id for t in tombs]
            seen.append(tombs.mapped('deleted_at'))
        return changed, gone, max([d for g in seen for d in g], default=None)

    def _current_token(self):
        Model = self.env[self.model]
        last = Model.with_context(active_test=False).search(
            self.scope_domain(), order='write_date desc', limit=1).write_date
        tomb = self.env['dav.deletion'].sudo().search(
            [('model', '=', self.model)] + self.env['dav.deletion']._visible_domain(),
            order='deleted_at desc', limit=1).deleted_at
        latest = max([d for d in (last, tomb) if d], default=None)
        return make_token(self._clamp(None, latest))

    def sync(self, token):
        since = parse_token(token)
        changed, gone, latest = self._changes(since)
        self._maps(changed)
        hrefs = [self.href + quote(n, safe='@') for n in gone]
        return [self._item(r) for r in changed], hrefs, make_token(self._clamp(since, latest))


class CalendarObject(Resource):
    writable = True

    def __init__(self, ctx, path, record, mapping, collection):
        super().__init__(ctx, path)
        self.record = record
        self.mapping = mapping
        self.collection = collection

    def display_name(self):
        return self.record.display_name

    def etag(self):
        extra = (self.mapping.write_date if self.mapping else '')
        return vcard.etag_of(*self.collection.etag_parts(self.record), extra)

    def last_modified(self):
        return self.record.write_date

    def content_type(self):
        return 'text/calendar; charset=utf-8; component=%s' % self.collection.component

    def prop(self, name):
        if name == C('calendar-data'):
            return self._render()
        return super().prop(name)

    def _render(self):
        return self.collection.build_ics(self.record, self.mapping)

    def read(self):
        return self._render().encode('utf-8'), self.content_type()

    def put(self, data, content_type, request_info):
        _cal, comp = parse_ics(data, self.collection.component)
        try:
            _rec, extra = self.collection.apply_ics(self.record, comp, self.mapping)
        except (AccessError, UserError, ValidationError) as exc:
            raise DavError(403, str(exc))
        Map = self.ctx.env['dav.resource.name'].sudo()
        uid = str(comp.contents['uid'][0].value)[:255] if 'uid' in comp.contents else None
        vals = {'extra_lines': extra or False, 'uid': uid}
        if self.mapping:
            changed = {k: v for k, v in vals.items() if self.mapping[k] != v}
            if changed:
                self.mapping.write(changed)
                touch(self.record)
        else:
            Map.create(dict(vals, model=self.collection.model, res_id=self.record.id))
            touch(self.record)
        self.ctx.__dict__.get('_dav_maps', {}).pop(self.collection.model, None)

    def delete(self):
        try:
            self.collection.archive(self.record)
        except (AccessError, UserError, ValidationError) as exc:
            raise DavError(403, str(exc))
