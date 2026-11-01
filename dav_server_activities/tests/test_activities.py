from odoo.tests import tagged

from odoo.addons.dav_server.tests.common import DavCase

PATH = '/dav/calendars/%s/activities/'


@tagged('post_install', '-at_install')
class TestActivities(DavCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        partner = cls.env['res.partner'].create({'name': 'Acme'})
        todo = cls.env.ref('mail.mail_activity_data_todo')

        def activity(user, summary):
            return cls.env['mail.activity'].sudo().create({
                'res_model_id': cls.env['ir.model']._get_id('res.partner'), 'res_id': partner.id,
                'activity_type_id': todo.id, 'user_id': user.id, 'summary': summary})
        cls.mine = activity(cls.dav_manager, 'Call back')
        cls.theirs = activity(cls.dav_user, 'Not yours')

    def _url(self, activity):
        return PATH % 'dav_manager' + '%d.ics' % activity.id

    def test_only_own_activities(self):
        content = self.list_collection(PATH % 'dav_manager', 'dav_manager').content
        self.assertIn(b'%d.ics' % self.mine.id, content)
        self.assertNotIn(b'%d.ics' % self.theirs.id, content)
        res = self.dav('GET', PATH % 'dav_manager' + '%d.ics' % self.theirs.id, login='dav_manager')
        self.assertEqual(res.status_code, 404)

    def test_render(self):
        res = self.dav('GET', self._url(self.mine), login='dav_manager')
        self.assertEqual(res.status_code, 200)
        self.assertIn(b'SUMMARY:Acme: Call back', res.content)
        self.assertIn(b'/odoo/res.partner/', res.content)

    def test_edit_summary_and_due(self):
        res = self.dav('PUT', self._url(self.mine), login='dav_manager', body=(
            'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:u1\r\nSUMMARY:Acme: Call tomorrow\r\n'
            'DUE;VALUE=DATE:20301231\r\nEND:VTODO\r\nEND:VCALENDAR\r\n'))
        self.assertIn(res.status_code, (201, 204))
        self.mine.invalidate_recordset()
        self.assertEqual(self.mine.summary, 'Call tomorrow')
        self.assertEqual(str(self.mine.date_deadline), '2030-12-31')

    def test_complete_marks_done_and_reports_gone(self):
        token = self.sync_report(PATH % 'dav_manager', 'dav_manager').content.decode() \
            .split('<D:sync-token>')[1].split('<')[0]
        res = self.dav('PUT', self._url(self.mine), login='dav_manager', body=(
            'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:u1\r\nSUMMARY:x\r\n'
            'STATUS:COMPLETED\r\nEND:VTODO\r\nEND:VCALENDAR\r\n'))
        self.assertIn(res.status_code, (201, 204))
        self.mine.invalidate_recordset()
        self.assertFalse(self.mine.active)  # Odoo archives done activities
        res = self.sync_report(PATH % 'dav_manager', 'dav_manager', token)
        self.assertIn(b'%d.ics' % self.mine.id, res.content)

    def test_cannot_create(self):
        res = self.dav('PUT', PATH % 'dav_manager' + 'new.ics', login='dav_manager', body=(
            'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:n\r\nSUMMARY:x\r\nEND:VTODO\r\nEND:VCALENDAR\r\n'))
        self.assertEqual(res.status_code, 403)
