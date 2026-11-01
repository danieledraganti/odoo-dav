from odoo.tests import tagged

from odoo.addons.dav_server.tests.common import DavCase

PATH = '/dav/calendars/dav_manager/tasks/'


@tagged('post_install', '-at_install')
class TestTasks(DavCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.task = cls.env['project.task'].create({'name': 'Ship it', 'user_ids': [(6, 0, [cls.dav_manager.id])]})

    def _get(self):
        return self.dav('GET', PATH + '%d.ics' % self.task.id, login='dav_manager')

    def test_task_links_back_to_odoo(self):
        res = self._get()
        self.assertEqual(res.status_code, 200)
        self.assertIn('URL:', res.text)
        self.assertIn('/odoo/project.task/%d' % self.task.id, res.text.replace('\r\n ', ''))

    def test_client_link_wins_and_is_kept(self):
        res = self.dav('PUT', PATH + '%d.ics' % self.task.id, login='dav_manager', body=(
            'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VTODO\r\nUID:u1\r\nSUMMARY:Ship it\r\n'
            'URL:https://example.com/ticket/42\r\nEND:VTODO\r\nEND:VCALENDAR\r\n'))
        self.assertIn(res.status_code, (201, 204))
        text = self._get().text
        self.assertIn('https://example.com/ticket/42', text)
        self.assertEqual(text.count('URL:'), 1)

    def test_tokens_from_before_the_url_was_added_force_a_full_resync(self):
        old = 'urn:odoo-dav:sync:1700000000000000'
        res = self.sync_report(PATH, 'dav_manager', old)
        self.assertEqual(res.status_code, 403)
        self.assertIn(b'valid-sync-token', res.content)
        self.assertEqual(self.sync_report(PATH, 'dav_manager').status_code, 207)  # from the beginning
        token = self.sync_report(PATH, 'dav_manager').text.split('<D:sync-token>')[1].split('<')[0]
        self.assertIn('sync:2.', token)
        self.assertEqual(self.sync_report(PATH, 'dav_manager', token).status_code, 207)
