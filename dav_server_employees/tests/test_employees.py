from odoo.tests import tagged

from odoo.addons.dav_server.tests.common import DavCase

BOOK = '/dav/addressbooks/%s/employees/'


@tagged('post_install', '-at_install')
class TestEmployees(DavCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.dept = cls.env['hr.department'].create({'name': 'Sales'})
        cls.emp = cls.env['hr.employee'].create({
            'name': 'Mario Rossi', 'work_email': 'mario@example.com', 'work_phone': '+39 02 111',
            'mobile_phone': '+39 333 222', 'job_title': 'Buyer', 'department_id': cls.dept.id,
            'private_phone': '+39 999 SECRET'})

    def test_listed_and_rendered(self):
        self.assertIn(b'%d.vcf' % self.emp.id, self.list_collection(BOOK % 'dav_user').content)
        res = self.dav('GET', BOOK % 'dav_user' + '%d.vcf' % self.emp.id)
        self.assertEqual(res.status_code, 200)
        for needle in (b'FN:Mario Rossi', b'mario@example.com', b'Buyer', b'+39 333 222'):
            self.assertIn(needle, res.content)
        self.assertNotIn(b'SECRET', res.content)

    def test_read_only(self):
        card = 'BEGIN:VCARD\r\nVERSION:3.0\r\nFN:X\r\nEND:VCARD\r\n'
        for method, name in (('PUT', 'new.vcf'), ('PUT', '%d.vcf' % self.emp.id), ('DELETE', '%d.vcf' % self.emp.id)):
            res = self.dav(method, BOOK % 'dav_manager' + name, login='dav_manager', body=card if method == 'PUT' else None)
            self.assertIn(res.status_code, (403, 405), (method, name))
        self.assertTrue(self.emp.exists())

    def test_departure_reported_as_gone(self):
        token = self.sync_report(BOOK % 'dav_user').content.decode().split('<D:sync-token>')[1].split('<')[0]
        self.emp.write({'active': False})
        res = self.sync_report(BOOK % 'dav_user', token=token)
        self.assertIn(b'%d.vcf' % self.emp.id, res.content)
        self.assertIn(b'404', res.content)
