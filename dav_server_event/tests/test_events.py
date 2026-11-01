import datetime

from odoo.tests import tagged

from odoo.addons.dav_server.tests.common import DavCase

PATH = '/dav/calendars/%s/odoo-events/'


@tagged('post_install', '-at_install')
class TestEvents(DavCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref('event.group_event_user')
        cls.dav_user.write({'group_ids': [(4, group.id)]})
        cls.dav_manager.write({'group_ids': [(4, group.id)]})
        start = datetime.datetime.now() + datetime.timedelta(days=3)
        Event = cls.env['event.event'].sudo()
        cls.mine = Event.create({'name': 'Mine', 'date_begin': start, 'date_end': start + datetime.timedelta(hours=2),
                                 'user_id': cls.dav_user.id})
        cls.other = Event.create({'name': 'Other', 'date_begin': start, 'date_end': start + datetime.timedelta(hours=2),
                                  'user_id': cls.dav_manager.id})
        cls.registered = Event.create({'name': 'Registered', 'date_begin': start,
                                       'date_end': start + datetime.timedelta(hours=2),
                                       'user_id': cls.dav_manager.id})
        cls.env['event.registration'].sudo().create({
            'event_id': cls.registered.id, 'partner_id': cls.dav_user.partner_id.id, 'name': 'Dav User'})

    def test_only_own_or_registered_events(self):
        content = self.list_collection(PATH % 'dav_user').content
        self.assertIn(b'%d.ics' % self.mine.id, content)
        self.assertIn(b'%d.ics' % self.registered.id, content)
        self.assertNotIn(b'%d.ics' % self.other.id, content)

    def test_render_and_read_only(self):
        res = self.dav('GET', PATH % 'dav_user' + '%d.ics' % self.mine.id)
        self.assertIn(b'SUMMARY:Mine', res.content)
        res = self.dav('DELETE', PATH % 'dav_manager' + '%d.ics' % self.other.id, login='dav_manager')
        self.assertEqual(res.status_code, 403)
