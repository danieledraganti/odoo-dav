"""Read-only CalDAV VEVENT view of project milestones (all-day events on their deadline)."""
import datetime

import vobject

from odoo import fields

from odoo.addons.dav_server.dav.caldav import CalendarCollection, from_naive_utc

CLOSED_WINDOW_DAYS = 90


class MilestoneCalendar(CalendarCollection):
    model = 'project.milestone'
    component = 'VEVENT'
    display = 'Milestones'
    read_only = True
    track_scope_exit = True

    def scope_domain(self):
        limit = fields.Date.today() - datetime.timedelta(days=CLOSED_WINDOW_DAYS)
        return [('deadline', '!=', False), '|', ('is_reached', '=', False), ('deadline', '>', limit)]

    def etag_parts(self, milestone):
        return (milestone.id, milestone.write_date, milestone.project_id.name)

    def build_ics(self, milestone, mapping):
        cal = vobject.iCalendar()
        ev = cal.add('vevent')
        ev.add('uid').value = 'odoo-milestone-%s-%d' % (self.ctx.db_uuid, milestone.id)
        ev.add('dtstamp').value = from_naive_utc(milestone.write_date)
        ev.add('created').value = from_naive_utc(milestone.create_date)
        ev.add('last-modified').value = from_naive_utc(milestone.write_date)
        ev.add('summary').value = '%s%s (%s)' % (
            '\u2713 ' if milestone.is_reached else '', milestone.name or '', milestone.project_id.name or '')
        ev.add('dtstart').value = milestone.deadline
        ev.add('dtend').value = milestone.deadline + datetime.timedelta(days=1)
        ev.add('status').value = 'CONFIRMED'
        ev.add('transp').value = 'TRANSPARENT'
        return cal.serialize()
