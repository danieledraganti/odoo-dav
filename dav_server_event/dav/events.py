"""Read-only CalDAV VEVENT view of the Odoo events (``event.event``) the user runs or is registered to."""
import datetime

import vobject

from odoo import fields
from odoo.tools import html2plaintext

from odoo.addons.dav_server.dav.caldav import CalendarCollection, from_naive_utc

PAST_WINDOW_DAYS = 180


class EventsCalendar(CalendarCollection):
    model = 'event.event'
    component = 'VEVENT'
    display = 'Odoo Events'
    read_only = True
    track_scope_exit = True

    def scope_domain(self):
        limit = fields.Datetime.now() - datetime.timedelta(days=PAST_WINDOW_DAYS)
        return ['|', ('user_id', '=', self.ctx.user.id),
                ('registration_ids.partner_id', 'in', [self.ctx.user.partner_id.id]),
                ('date_end', '>', limit)]

    def build_ics(self, event, mapping):
        cal = vobject.iCalendar()
        ev = cal.add('vevent')
        ev.add('uid').value = 'odoo-event-registration-%s-%d' % (self.ctx.db_uuid, event.id)
        ev.add('dtstamp').value = from_naive_utc(event.write_date)
        ev.add('created').value = from_naive_utc(event.create_date)
        ev.add('last-modified').value = from_naive_utc(event.write_date)
        ev.add('summary').value = event.name or ''
        ev.add('dtstart').value = from_naive_utc(event.date_begin)
        ev.add('dtend').value = from_naive_utc(event.date_end)
        description = html2plaintext(event.description or '').strip()
        if description:
            ev.add('description').value = description
        place = event.address_id.display_name if event.address_id else ''
        if place:
            ev.add('location').value = place
        ev.add('status').value = 'CONFIRMED'
        ev.add('transp').value = 'OPAQUE'
        return cal.serialize()
