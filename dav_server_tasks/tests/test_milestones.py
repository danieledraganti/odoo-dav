import datetime

from odoo.tests import tagged

from odoo.addons.dav_server.tests.common import DavCase

PATH = '/dav/calendars/%s/milestones/'


@tagged('post_install', '-at_install')
class TestMilestones(DavCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        project = cls.env['project.project'].create({'name': 'Website'})
        cls.milestone = cls.env['project.milestone'].create({
            'name': 'Launch', 'project_id': project.id,
            'deadline': datetime.date.today() + datetime.timedelta(days=10)})
        cls.no_deadline = cls.env['project.milestone'].create({'name': 'Someday', 'project_id': project.id})

    def test_listed_as_all_day_event(self):
        content = self.list_collection(PATH % 'dav_user').content
        self.assertIn(b'%d.ics' % self.milestone.id, content)
        self.assertNotIn(b'%d.ics' % self.no_deadline.id, content)
        res = self.dav('GET', PATH % 'dav_user' + '%d.ics' % self.milestone.id)
        self.assertIn(b'SUMMARY:Launch (Website)', res.content)
        self.assertIn(b'DTSTART;VALUE=DATE:', res.content)

    def test_read_only_and_deletion_reported(self):
        res = self.dav('DELETE', PATH % 'dav_manager' + '%d.ics' % self.milestone.id, login='dav_manager')
        self.assertEqual(res.status_code, 403)
        token = self.sync_report(PATH % 'dav_user').content.decode().split('<D:sync-token>')[1].split('<')[0]
        gone = self.milestone.id
        self.milestone.unlink()
        res = self.sync_report(PATH % 'dav_user', token=token)
        self.assertIn(b'%d.ics' % gone, res.content)
