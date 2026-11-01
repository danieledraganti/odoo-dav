"""Read-only CalDAV VEVENT view of the time off the user is allowed to see."""
import datetime

import vobject

from odoo import fields

from odoo.addons.dav_server.dav.caldav import CalendarCollection, from_naive_utc

PAST_WINDOW_DAYS = 180
OPEN_STATES = ('confirm', 'validate1', 'validate')


class TimeOffCalendar(CalendarCollection):
    model = 'hr.leave'
    component = 'VEVENT'
    display = 'Time Off'
    read_only = True
    track_scope_exit = True

    def scope_domain(self):
        limit = fields.Datetime.now() - datetime.timedelta(days=PAST_WINDOW_DAYS)
        return [('state', 'in', OPEN_STATES), ('date_to', '>', limit)]

    def etag_parts(self, leave):
        return (leave.id, leave.write_date, leave.state)

    def _summary(self, leave):
        user = self.ctx.user
        who = leave.employee_id.name or ''
        # the leave type can be sensitive: only the person and time-off officers see it
        if leave.employee_id.user_id == user or user.has_group('hr_holidays.group_hr_holidays_responsible'):
            return '%s - %s' % (who, leave.holiday_status_id.name or 'Time Off')
        return '%s - Time Off' % who

    def build_ics(self, leave, mapping):
        uid = 'odoo-leave-%s-%d' % (self.ctx.db_uuid, leave.id)
        cal = vobject.iCalendar()
        ev = cal.add('vevent')
        ev.add('uid').value = uid
        ev.add('dtstamp').value = from_naive_utc(leave.write_date)
        ev.add('created').value = from_naive_utc(leave.create_date)
        ev.add('last-modified').value = from_naive_utc(leave.write_date)
        ev.add('summary').value = self._summary(leave)
        if leave.request_unit_half or leave.request_unit_hours or not leave.request_date_from:
            ev.add('dtstart').value = from_naive_utc(leave.date_from)
            ev.add('dtend').value = from_naive_utc(leave.date_to)
        else:
            ev.add('dtstart').value = leave.request_date_from
            ev.add('dtend').value = (leave.request_date_to or leave.request_date_from) + datetime.timedelta(days=1)
        ev.add('status').value = 'CONFIRMED' if leave.state == 'validate' else 'TENTATIVE'
        ev.add('transp').value = 'OPAQUE'
        ev.add('class').value = 'PUBLIC'
        return cal.serialize()
