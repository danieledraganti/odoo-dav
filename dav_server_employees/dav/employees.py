"""Read-only CardDAV address book with the employees' work contact details.

Plain employees cannot read ``hr.employee`` (it holds private data), only the
``hr.employee.public`` view. Visibility is therefore decided with the public
model, and only the work fields rendered below are read from the employee.
"""
import datetime
import re
from urllib.parse import quote

import vobject

from odoo import fields

from odoo.tools.translate import LazyTranslate

from odoo.addons.dav_server.dav import vcard, xml
from odoo.addons.dav_server.dav.contacts import SYNC_LAG, make_token, parse_token
from odoo.addons.dav_server.dav.resource import DavError, Resource
from odoo.addons.dav_server.dav.xml import q

A = lambda n: q('A', n)  # noqa: E731
D = lambda n: q('D', n)  # noqa: E731
CS = lambda n: q('CS', n)  # noqa: E731

_ID_RE = re.compile(r'^(\d+)\.vcf$')


_lt = LazyTranslate(__name__)


class EmployeeBook(Resource):
    is_collection = True
    reports = (A('addressbook-multiget'), A('addressbook-query'), D('sync-collection'))

    @property
    def env(self):
        return self.ctx.env

    def _visible(self, domain=(), **kw):
        """Employees (sudo, for their work fields only) that the user sees in the public directory."""
        Employee = self.env['hr.employee'].sudo()
        candidates = Employee.search(list(domain), **kw)
        visible = set(self.env['hr.employee.public'].search([('id', 'in', candidates.ids)]).ids)
        return candidates.filtered(lambda e: e.id in visible)

    def display_name(self):
        return self.tr(_lt('Employees'))

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
        if name == A('addressbook-description'):
            return self.tr(_lt('Odoo employees'))
        return super().prop(name)

    def _item(self, employee):
        return EmployeeCard(self.ctx, self.path + ('%d.vcf' % employee.id,), employee, self)

    def _ctag(self):
        Employee = self.env['hr.employee'].sudo()
        last = Employee.with_context(active_test=False).search([], order='write_date desc', limit=1)
        return vcard.etag_of(len(self._visible()), last.write_date, self._current_token())

    def children(self):
        limit = self.ctx.param_int('max_contacts', 20000)
        employees = self._visible(limit=limit + 1, order='id')
        if len(employees) > limit:
            raise DavError(507, 'Too many employees for a DAV address book')
        return [self._item(e) for e in employees]

    def get_child(self, name):
        match = _ID_RE.match(name)
        if not match:
            return None
        employee = self._visible([('id', '=', int(match.group(1)))], limit=1)
        return self._item(employee) if employee else None

    def query(self, root):
        return self.children()

    # ----------------------------------------------------------------- sync
    def _clamp(self, since, latest):
        ceiling = fields.Datetime.now() - SYNC_LAG
        candidate = min(latest, ceiling) if latest else ceiling
        if since and candidate < since:
            candidate = since
        return candidate

    def _current_token(self):
        last = self.env['hr.employee'].sudo().with_context(active_test=False).search(
            [], order='write_date desc', limit=1).write_date
        tomb = self.env['dav.deletion'].sudo().search(
            [('model', '=', 'hr.employee')] + self.env['dav.deletion']._visible_domain(),
            order='deleted_at desc', limit=1).deleted_at
        return make_token(self._clamp(None, max([d for d in (last, tomb) if d], default=None)))

    def sync(self, token):
        since = parse_token(token)
        Employee = self.env['hr.employee'].sudo()
        base = [('write_date', '>', since)] if since else []
        changed = self._visible(base, order='id')
        gone, seen = [], [changed.mapped('write_date')]
        if since:
            archived = Employee.with_context(active_test=False).search([('active', '=', False)] + base)
            gone += ['%d.vcf' % e.id for e in archived]
            seen.append(archived.mapped('write_date'))
            tombs = self.env['dav.deletion'].sudo().search(
                [('model', '=', 'hr.employee'), ('deleted_at', '>', since)]
                + self.env['dav.deletion']._visible_domain())
            gone += ['%d.vcf' % t.res_id for t in tombs]
            seen.append(tombs.mapped('deleted_at'))
        latest = max([d for g in seen for d in g], default=None)
        hrefs = [self.href + quote(n, safe='@') for n in gone]
        return [self._item(e) for e in changed], hrefs, make_token(self._clamp(since, latest))


class EmployeeCard(Resource):

    def __init__(self, ctx, path, employee, book):
        super().__init__(ctx, path)
        self.employee = employee
        self.book = book

    def display_name(self):
        return self.employee.name

    def etag(self):
        e = self.employee
        return vcard.etag_of(e.id, e.write_date, e.department_id.name, e.company_id.name)

    def last_modified(self):
        return self.employee.write_date

    def content_type(self):
        return 'text/vcard; charset=utf-8'

    def prop(self, name):
        if name == A('address-data'):
            return self._render()
        return super().prop(name)

    def _render(self):
        e = self.employee
        card = vobject.vCard()
        given, family = vcard.split_name(e.name or '')
        card.add('n').value = vobject.vcard.Name(family=family, given=given)
        card.add('fn').value = e.name or ''
        card.add('uid').value = 'odoo-%s-employee-%d' % (self.ctx.db_uuid, e.id)
        org = [n for n in (e.company_id.name, e.department_id.name) if n]
        if org:
            card.add('org').value = org
        if e.job_title:
            card.add('title').value = e.job_title
        if e.work_email:
            child = card.add('email')
            child.value = e.work_email
            child.params = {'TYPE': ['INTERNET', 'WORK']}
        if e.work_phone:
            child = card.add('tel')
            child.value = e.work_phone
            child.params = {'TYPE': ['WORK', 'VOICE']}
        if e.mobile_phone:
            child = card.add('tel')
            child.value = e.mobile_phone
            child.params = {'TYPE': ['WORK', 'CELL']}
        if self.ctx.param_bool('vcard_photos', True):
            data = vcard.photo_bytes(e)
            if data:
                child = card.add('photo')
                child.value = data
                child.encoding_param = 'B'
                child.type_param = 'PNG' if data.startswith(b'\x89PNG') else 'JPEG'
        if e.write_date:
            card.add('rev').value = e.write_date.strftime('%Y-%m-%dT%H:%M:%SZ')
        return card.serialize()

    def read(self):
        return self._render().encode('utf-8'), self.content_type()
