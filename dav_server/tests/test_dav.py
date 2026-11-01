from odoo.tests import tagged

from .common import DavCase

PROPFIND_BODY = """<?xml version="1.0"?>
<D:propfind xmlns:D="DAV:"><D:prop><D:displayname/><D:getetag/><D:resourcetype/></D:prop></D:propfind>"""

XXE = """<?xml version="1.0"?>
<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<D:propfind xmlns:D="DAV:"><D:prop><D:displayname>&xxe;</D:displayname></D:prop></D:propfind>"""

VCARD = (
    "BEGIN:VCARD\r\nVERSION:3.0\r\nUID:client-uid-1\r\nFN:Mario Rossi\r\nN:Rossi;Mario;;;\r\n"
    "ORG:{org}\r\nTITLE:CTO\r\nEMAIL;TYPE=INTERNET,WORK:mario@example.com\r\n"
    "TEL;TYPE=CELL:+39 333 1234567\r\nTEL;TYPE=WORK:+39 02 1234567\r\n"
    "NOTE:Hello\r\nX-CUSTOM:keep me\r\nEND:VCARD\r\n"
)


@tagged('post_install', '-at_install')
class TestDavAuth(DavCase):

    def test_no_credentials_challenge(self):
        res = self.dav('PROPFIND', '/dav/', key=None, headers={'Depth': '0'})
        self.assertEqual(res.status_code, 401)
        self.assertIn('Basic', res.headers.get('WWW-Authenticate', ''))

    def test_wrong_key(self):
        res = self.dav('PROPFIND', '/dav/', key='0' * 40, headers={'Depth': '0'})
        self.assertEqual(res.status_code, 401)

    def test_valid_basic_and_bearer(self):
        for auth in ('basic', 'bearer'):
            res = self.dav('PROPFIND', '/dav/', auth=auth, headers={'Depth': '0'}, body=PROPFIND_BODY)
            self.assertEqual(res.status_code, 207, auth)

    def test_login_must_match_key_owner(self):
        res = self.dav('PROPFIND', '/dav/', login='dav_manager', key=self.keys['dav_user'],
                       headers={'Depth': '0'})
        self.assertEqual(res.status_code, 401)

    def test_user_without_dav_group_forbidden(self):
        res = self.dav('PROPFIND', '/dav/', login='plain_user', headers={'Depth': '0'})
        self.assertEqual(res.status_code, 403)

    def test_https_required_by_default(self):
        self.env['ir.config_parameter'].sudo().set_param('dav_server.allow_insecure', 'False')
        res = self.dav('PROPFIND', '/dav/', headers={'Depth': '0'})
        self.assertEqual(res.status_code, 403)

    def test_session_cookie_is_not_enough(self):
        self.authenticate('dav_user', 'dav_user')
        res = self.opener.request('PROPFIND', self.base_url() + '/dav/', headers={'Depth': '0'},
                                  allow_redirects=False)
        self.assertEqual(res.status_code, 401)


@tagged('post_install', '-at_install')
class TestDavEngine(DavCase):

    def test_depth_infinity_refused(self):
        res = self.dav('PROPFIND', '/dav/', headers={'Depth': 'infinity'})
        self.assertEqual(res.status_code, 403)
        self.assertIn(b'propfind-finite-depth', res.content)

    def test_xxe_rejected(self):
        res = self.dav('PROPFIND', '/dav/', headers={'Depth': '0'}, body=XXE)
        self.assertEqual(res.status_code, 400)
        self.assertNotIn(b'root:', res.content)

    def test_path_traversal_rejected(self):
        res = self.dav('PROPFIND', '/dav/addressbooks/../..', headers={'Depth': '0'})
        self.assertIn(res.status_code, (400, 404))

    def test_options_advertises_dav(self):
        res = self.dav('OPTIONS', '/dav/')
        self.assertEqual(res.status_code, 200)
        self.assertIn('addressbook', res.headers['DAV'])

    def test_other_users_collections_not_reachable(self):
        res = self.dav('PROPFIND', '/dav/addressbooks/dav_manager/contacts/', headers={'Depth': '0'})
        self.assertEqual(res.status_code, 404)

    def test_read_only_user_cannot_write(self):
        res = self.dav('PUT', '/dav/addressbooks/dav_user/contacts/x.vcf',
                       body=VCARD.format(org=''), headers={'Content-Type': 'text/vcard'})
        self.assertEqual(res.status_code, 403)


@tagged('post_install', '-at_install')
class TestCardDav(DavCase):

    BOOK = '/dav/addressbooks/dav_manager/contacts/'

    def _put(self, name, org='', login='dav_manager', headers=None):
        hdrs = {'Content-Type': 'text/vcard'}
        hdrs.update(headers or {})
        return self.dav('PUT', self.BOOK + name, login=login, body=VCARD.format(org=org), headers=hdrs)

    def test_create_read_update_delete(self):
        res = self._put('abc.vcf')
        self.assertEqual(res.status_code, 201, res.text)
        partner = self.env['res.partner'].search([('email', '=', 'mario@example.com')])
        self.assertEqual(len(partner), 1)
        self.assertEqual(partner.name, 'Mario Rossi')
        self.assertEqual(partner.function, 'CTO')
        self.assertEqual(partner.phone, '+39 02 1234567')   # WORK tel, not the CELL one
        # unmapped data survives the round trip
        res = self.dav('GET', self.BOOK + 'abc.vcf', login='dav_manager')
        self.assertEqual(res.status_code, 200)
        self.assertIn('+39 333 1234567', res.text)
        self.assertIn('X-CUSTOM:keep me', res.text)
        self.assertIn('UID:client-uid-1', res.text)
        # If-None-Match: * on an existing resource
        res = self._put('abc.vcf', headers={'If-None-Match': '*'})
        self.assertEqual(res.status_code, 412)
        # update with stale ETag
        res = self._put('abc.vcf', headers={'If-Match': '"stale"'})
        self.assertEqual(res.status_code, 412)
        # delete archives
        res = self.dav('DELETE', self.BOOK + 'abc.vcf', login='dav_manager')
        self.assertEqual(res.status_code, 204)
        self.assertFalse(partner.active)

    def test_org_links_to_unique_company(self):
        company = self.env['res.partner'].create({'name': 'Acme Srl', 'is_company': True,
                                                   'street': 'Via Roma 1', 'city': 'Milano'})
        self.assertEqual(self._put('a.vcf', org='  acme   SRL ').status_code, 201)
        partner = self.env['res.partner'].search([('email', '=', 'mario@example.com')])
        self.assertEqual(partner.parent_id, company)
        self.assertEqual(company.street, 'Via Roma 1')   # company data untouched

    def test_org_ambiguous_or_unknown_is_not_linked(self):
        self.env['res.partner'].create([{'name': 'Twin Co', 'is_company': True}] * 2)
        self._put('a.vcf', org='Twin Co')
        partner = self.env['res.partner'].search([('email', '=', 'mario@example.com')])
        self.assertFalse(partner.parent_id)
        self.assertEqual(partner.company_name, 'Twin Co')

    def test_companies_are_read_only(self):
        company = self.env['res.partner'].create({'name': 'Acme', 'is_company': True})
        res = self.dav('PUT', self.BOOK + '%d.vcf' % company.id, login='dav_manager',
                       body=VCARD.format(org=''), headers={'Content-Type': 'text/vcard'})
        self.assertEqual(res.status_code, 403)
        res = self.dav('DELETE', self.BOOK + '%d.vcf' % company.id, login='dav_manager')
        self.assertEqual(res.status_code, 403)

    def test_user_partners_cannot_be_deleted(self):
        res = self.dav('DELETE', self.BOOK + '%d.vcf' % self.dav_user.partner_id.id, login='dav_manager')
        self.assertEqual(res.status_code, 403)

    def test_sync_collection_reports_deletions(self):
        self._put('a.vcf')
        body = ('<?xml version="1.0"?><D:sync-collection xmlns:D="DAV:"><D:sync-token/>'
                '<D:sync-level>1</D:sync-level><D:prop><D:getetag/></D:prop></D:sync-collection>')
        res = self.dav('REPORT', self.BOOK, login='dav_manager', body=body)
        self.assertEqual(res.status_code, 207)
        self.assertIn(b'a.vcf', res.content)
        self.assertIn(b'sync-token', res.content)

    def _sync(self, login, token='urn:odoo-dav:sync:0'):
        body = ('<?xml version="1.0"?><D:sync-collection xmlns:D="DAV:"><D:sync-token>%s</D:sync-token>'
                '<D:sync-level>1</D:sync-level><D:prop><D:getetag/></D:prop></D:sync-collection>' % token)
        return self.dav('REPORT', '/dav/addressbooks/%s/contacts/' % login, login=login, body=body)

    def test_restricted_tombstone_only_for_its_users(self):
        Deletion = self.env['dav.deletion']
        Deletion._record('res.partner', {987654: 'secret.vcf'}, {987654: [self.dav_manager.id]})
        Deletion._record('res.partner', {987655: 'public.vcf'})
        res = self._sync('dav_manager')
        self.assertIn(b'secret.vcf', res.content)
        res = self._sync('dav_user')
        self.assertNotIn(b'secret.vcf', res.content)
        self.assertIn(b'public.vcf', res.content)

    def test_range_requests(self):
        self._put('r.vcf')
        url = self.BOOK + 'r.vcf'
        full = self.dav('GET', url, login='dav_manager')
        self.assertEqual(full.headers['Accept-Ranges'], 'bytes')
        size = len(full.content)

        res = self.dav('GET', url, login='dav_manager', headers={'Range': 'bytes=0-9'})
        self.assertEqual(res.status_code, 206)
        self.assertEqual(res.content, full.content[:10])
        self.assertEqual(res.headers['Content-Range'], 'bytes 0-9/%d' % size)
        self.assertEqual(res.headers['Content-Length'], '10')

        res = self.dav('GET', url, login='dav_manager', headers={'Range': 'bytes=5-'})
        self.assertEqual(res.content, full.content[5:])
        res = self.dav('GET', url, login='dav_manager', headers={'Range': 'bytes=-7'})
        self.assertEqual(res.content, full.content[-7:])
        res = self.dav('GET', url, login='dav_manager', headers={'Range': 'bytes=0-999999'})
        self.assertEqual(res.status_code, 206)
        self.assertEqual(res.content, full.content)

        res = self.dav('GET', url, login='dav_manager', headers={'Range': 'bytes=%d-' % size})
        self.assertEqual(res.status_code, 416)
        # unsupported / malformed / stale If-Range: the whole body, as the RFC allows
        for hdr in ({'Range': 'bytes=0-1,5-6'}, {'Range': 'bytes=abc'}, {'Range': 'lines=1-2'},
                    {'Range': 'bytes=0-1', 'If-Range': '"stale"'}):
            res = self.dav('GET', url, login='dav_manager', headers=hdr)
            self.assertEqual((res.status_code, res.content), (200, full.content), hdr)

    def test_multiget_returns_only_requested(self):
        self._put('one.vcf')
        self._put('two.vcf', headers={})
        body = ('<?xml version="1.0"?><C:addressbook-multiget xmlns:D="DAV:" '
                'xmlns:C="urn:ietf:params:xml:ns:carddav"><D:prop><D:getetag/></D:prop>'
                '<D:href>%sone.vcf</D:href></C:addressbook-multiget>' % self.BOOK)
        res = self.dav('REPORT', self.BOOK, login='dav_manager', body=body)
        self.assertEqual(res.status_code, 207)
        self.assertIn(b'one.vcf', res.content)
        self.assertEqual(res.content.count(b'<D:response>'), 1)

    def test_overlong_value_rejected(self):
        card = 'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:%s\r\nEND:VCARD\r\n' % ('A' * 5000)
        res = self.dav('PUT', self.BOOK + 'long.vcf', login='dav_manager', body=card,
                       headers={'Content-Type': 'text/vcard'})
        self.assertEqual(res.status_code, 403)

    def test_acl_hidden_partner_is_invisible(self):
        partner = self.env['res.partner'].create({'name': 'Hidden', 'company_id': self.env.company.id})
        other = self.env['res.company'].create({'name': 'Other Co'})
        partner.company_id = other
        res = self.dav('GET', self.BOOK + '%d.vcf' % partner.id, login='dav_manager')
        self.assertEqual(res.status_code, 404)
