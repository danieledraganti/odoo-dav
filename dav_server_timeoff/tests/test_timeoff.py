import datetime

from odoo.tests import tagged

from odoo.addons.dav_server.tests.common import DavCase

PATH = '/dav/calendars/%s/timeoff/'


@tagged('post_install', '-at_install')
class TestTimeOff(DavCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': 'Dav User', 'user_id': cls.dav_manager.id})
        cls.status = cls.env['hr.leave.type'].create({'name': 'Secret Illness', 'requires_allocation': False})
        day = datetime.date.today() + datetime.timedelta(days=7)
        cls.leave = cls.env['hr.leave'].sudo().with_context(
            leave_fast_create=True, mail_notrack=True).create({
                'employee_id': cls.employee.id, 'holiday_status_id': cls.status.id,
                'request_date_from': day, 'request_date_to': day})
        cls.leave.sudo().write({'state': 'validate'})

    def test_listed_and_rendered(self):
        res = self.list_collection(PATH % 'dav_manager', 'dav_manager')
        self.assertEqual(res.status_code, 207)
        self.assertIn(b'%d.ics' % self.leave.id, res.content)
        res = self.dav('GET', PATH % 'dav_manager' + '%d.ics' % self.leave.id, login='dav_manager')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'BEGIN:VEVENT', res.content)
        self.assertIn(b'Dav User - Secret Illness', res.content)  # own leave shows its type

    def test_read_only(self):
        ics = (b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x\r\nSUMMARY:x\r\n'
               b'DTSTART:20300101T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n')
        for login in ('dav_manager',):
            res = self.dav('PUT', PATH % login + 'new.ics', login=login, body=ics)
            self.assertEqual(res.status_code, 403)
            res = self.dav('PUT', PATH % login + '%d.ics' % self.leave.id, login=login, body=ics)
            self.assertEqual(res.status_code, 403)
            res = self.dav('DELETE', PATH % login + '%d.ics' % self.leave.id, login=login)
            self.assertEqual(res.status_code, 403)

    def test_refused_leave_reported_as_gone(self):
        token = self.sync_report(PATH % 'dav_manager', 'dav_manager').content.decode().split('<D:sync-token>')[1].split('<')[0]
        self.leave.sudo().write({'state': 'refuse'})
        res = self.sync_report(PATH % 'dav_manager', 'dav_manager', token=token)
        self.assertIn(b'%d.ics' % self.leave.id, res.content)
        self.assertIn(b'404', res.content)
        self.assertNotIn(b'%d.ics' % self.leave.id, self.list_collection(PATH % 'dav_manager', 'dav_manager').content)
