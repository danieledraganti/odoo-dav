import base64
import datetime

from odoo.tests.common import HttpCase


_DEFAULT = object()


class DavCase(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['ir.config_parameter'].sudo().set_param('dav_server.allow_insecure', 'True')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.dav_user = Users.create({
            'name': 'Dav User', 'login': 'dav_user', 'email': 'dav_user@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('dav_server.group_dav_user').id])],
        })
        cls.dav_manager = Users.create({
            'name': 'Dav Manager', 'login': 'dav_manager', 'email': 'dav_manager@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id,
                                  cls.env.ref('dav_server.group_dav_manager').id,
                                  cls.env.ref('base.group_partner_manager').id])],
        })
        cls.plain_user = Users.create({
            'name': 'Plain User', 'login': 'plain_user', 'email': 'plain@example.com',
            'group_ids': [(6, 0, [cls.env.ref('base.group_user').id])],
        })
        cls.keys = {u.login: cls._make_key(u) for u in (cls.dav_user, cls.dav_manager, cls.plain_user)}

    @classmethod
    def _make_key(cls, user):
        expiry = datetime.datetime.now() + datetime.timedelta(days=1)
        return cls.env['res.users.apikeys'].with_user(user).sudo()._generate(None, 'test', expiry)

    # ------------------------------------------------------------------ http
    def dav(self, method, path, login='dav_user', key=_DEFAULT, body=None, headers=None, auth='basic'):
        hdrs = dict(headers or {})
        if key is _DEFAULT:
            key = self.keys.get(login)
        if auth == 'basic' and key is not None:
            hdrs['Authorization'] = 'Basic ' + base64.b64encode(('%s:%s' % (login, key)).encode()).decode()
        elif auth == 'bearer' and key is not None:
            hdrs['Authorization'] = 'Bearer ' + key
        if isinstance(body, str):
            body = body.encode()
        return self.opener.request(method, self.base_url() + path, data=body, headers=hdrs,
                                   allow_redirects=False, timeout=30)

    # ------------------------------------------------------------ helpers
    def sync_report(self, path, login='dav_user', token='urn:odoo-dav:sync:0'):
        body = ('<?xml version="1.0"?><D:sync-collection xmlns:D="DAV:"><D:sync-token>%s</D:sync-token>'
                '<D:sync-level>1</D:sync-level><D:prop><D:getetag/></D:prop></D:sync-collection>' % token)
        return self.dav('REPORT', path, login=login, body=body)

    def list_collection(self, path, login='dav_user'):
        return self.dav('PROPFIND', path, login=login, headers={'Depth': '1'},
                        body='<?xml version="1.0"?><D:propfind xmlns:D="DAV:"><D:prop><D:getetag/></D:prop></D:propfind>')
