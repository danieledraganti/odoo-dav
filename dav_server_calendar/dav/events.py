"""CalDAV VEVENT view of the calendar events the user organises or attends.

Every ``calendar.event`` row is exposed as its own VEVENT (recurring series
appear as individual occurrences, without RRULE). Creation and edition are
limited to non-recurring events; recurring ones are read-only so that a client
can never corrupt a series.
"""
import datetime

import vobject

from odoo import fields
from odoo.tools import html2plaintext, plaintext2html
from odoo.tools.translate import LazyTranslate

from odoo.addons.dav_server.dav.caldav import (
    CalendarCollection, check_text, extra_lines_of, from_naive_utc, to_utc_naive,
)
from odoo.addons.dav_server.dav.resource import DavError

HANDLED = {'UID', 'DTSTAMP', 'CREATED', 'LAST-MODIFIED', 'SUMMARY', 'DESCRIPTION', 'LOCATION',
           'DTSTART', 'DTEND', 'DURATION', 'TRANSP', 'CLASS', 'STATUS', 'ORGANIZER', 'ATTENDEE',
           'VALARM', 'SEQUENCE'}
PAST_WINDOW_DAYS = 180
PARTSTAT = {'accepted': 'ACCEPTED', 'declined': 'DECLINED', 'tentative': 'TENTATIVE',
            'needsAction': 'NEEDS-ACTION'}
CLASS = {'public': 'PUBLIC', 'private': 'PRIVATE', 'confidential': 'CONFIDENTIAL'}
CLASS_REV = {v: k for k, v in CLASS.items()}


_lt = LazyTranslate(__name__)


class EventCalendar(CalendarCollection):
    model = 'calendar.event'
    component = 'VEVENT'
    display = _lt('Calendar')

    def scope_domain(self):
        limit = fields.Datetime.now() - datetime.timedelta(days=PAST_WINDOW_DAYS)
        return ['|', ('partner_ids', 'in', [self.ctx.user.partner_id.id]),
                ('user_id', '=', self.ctx.user.id),
                ('stop', '>', limit)]

    def etag_parts(self, event):
        return (event.id, event.write_date,
                ','.join('%s:%s' % (a.partner_id.id, a.state) for a in event.attendee_ids))

    # ------------------------------------------------------------------ build
    def build_ics(self, event, mapping):
        uid = mapping.uid if mapping and mapping.uid else 'odoo-event-%s-%d' % (self.ctx.db_uuid, event.id)
        cal = vobject.iCalendar()
        ev = cal.add('vevent')
        ev.add('uid').value = uid
        ev.add('dtstamp').value = from_naive_utc(event.write_date)
        ev.add('created').value = from_naive_utc(event.create_date)
        ev.add('last-modified').value = from_naive_utc(event.write_date)
        ev.add('summary').value = event.name or ''
        if event.allday:
            ev.add('dtstart').value = event.start_date
            ev.add('dtend').value = event.stop_date + datetime.timedelta(days=1)
        else:
            ev.add('dtstart').value = from_naive_utc(event.start)
            ev.add('dtend').value = from_naive_utc(event.stop)
        description = html2plaintext(event.description or '').strip()
        if description:
            ev.add('description').value = description
        if event.location:
            ev.add('location').value = event.location
        ev.add('status').value = 'CONFIRMED'
        ev.add('transp').value = 'TRANSPARENT' if event.show_as == 'free' else 'OPAQUE'
        ev.add('class').value = CLASS.get(event.privacy or 'public', 'PUBLIC')
        if event.user_id and event.user_id.email:
            org = ev.add('organizer')
            org.value = 'mailto:%s' % event.user_id.email
            org.params['CN'] = [event.user_id.name]
        for att in event.attendee_ids:
            if not att.email:
                continue
            node = ev.add('attendee')
            node.value = 'mailto:%s' % att.email
            node.params['CN'] = [att.common_name or att.email]
            node.params['PARTSTAT'] = [PARTSTAT.get(att.state, 'NEEDS-ACTION')]
        for alarm in event.alarm_ids:
            if not alarm.duration_minutes:
                continue
            al = ev.add('valarm')
            al.add('action').value = 'DISPLAY'
            al.add('description').value = alarm.name
            al.add('trigger').value = datetime.timedelta(minutes=-alarm.duration_minutes)
        text = cal.serialize()
        extra = (mapping.extra_lines if mapping else '') or ''
        if extra:
            text = text.replace('END:VEVENT', extra + 'END:VEVENT', 1)
        return text

    # ------------------------------------------------------------------ apply
    def apply_ics(self, event, comp, mapping):
        env = self.ctx.env
        c = comp.contents
        if 'rrule' in c or 'recurrence-id' in c:
            raise DavError(403, 'Recurring events cannot be written through DAV')
        if event and (event.recurrence_id or event.recurrency):
            raise DavError(403, 'Recurring events are read-only through DAV')
        if event and event.user_id != env.user:
            raise DavError(403, 'Only the organiser can modify an event through DAV')
        if 'dtstart' not in c:
            raise DavError(403, 'DTSTART is required')
        tz = env.user.tz

        def val(name):
            return c[name][0].value if name in c else None

        vals = {}
        name = check_text((val('summary') or '').strip()) or (event.name if event else 'Untitled')
        if not event or event.name != name:
            vals['name'] = name

        start_raw = val('dtstart')
        allday = not isinstance(start_raw, datetime.datetime)
        if allday:
            start_date = start_raw
            end_raw = val('dtend')
            if end_raw is not None and not isinstance(end_raw, datetime.datetime):
                stop_date = max(end_raw - datetime.timedelta(days=1), start_date)
            else:
                stop_date = start_date
            new = {'allday': True, 'start_date': start_date, 'stop_date': stop_date,
                   'start': datetime.datetime.combine(start_date, datetime.time(8)),
                   'stop': datetime.datetime.combine(stop_date, datetime.time(18))}
        else:
            start = to_utc_naive(start_raw, tz)
            if 'dtend' in c:
                stop = to_utc_naive(val('dtend'), tz)
            elif 'duration' in c:
                stop = start + val('duration')
            else:
                stop = start + datetime.timedelta(hours=1)
            if stop < start:
                raise DavError(403, 'DTEND is before DTSTART')
            new = {'allday': False, 'start': start, 'stop': stop}
        if not event or any(event[k] != v for k, v in new.items()):
            vals.update(new)

        for ical, fname in (('description', 'description'), ('location', 'location')):
            raw = (val(ical) or '').strip()
            current = (html2plaintext(event.description or '').strip() if fname == 'description' and event
                       else ((event.location or '') if event else ''))
            if raw != current:
                vals[fname] = (plaintext2html(raw) if fname == 'description' else raw) or False

        if 'transp' in c:
            show_as = 'free' if (val('transp') or '').upper() == 'TRANSPARENT' else 'busy'
            if not event or event.show_as != show_as:
                vals['show_as'] = show_as
        if 'class' in c and (val('class') or '').upper() in CLASS_REV:
            privacy = CLASS_REV[val('class').upper()]
            if not event or event.privacy != privacy:
                vals['privacy'] = privacy

        # reminders: matched against the existing calendar.alarm records
        wanted = set()
        for al in comp.contents.get('valarm', []):
            trig = al.contents.get('trigger')
            if trig and isinstance(trig[0].value, datetime.timedelta):
                wanted.add(int(-trig[0].value.total_seconds() // 60))
        alarms = env['calendar.alarm'].search([('duration_minutes', 'in', sorted(wanted))]) if wanted \
            else env['calendar.alarm']
        by_minutes = {}
        for alarm in alarms:
            by_minutes.setdefault(alarm.duration_minutes, alarm)
        ids = sorted({a.id for a in by_minutes.values()})
        if not event or sorted(event.alarm_ids.ids) != ids:
            vals['alarm_ids'] = [(6, 0, ids)]

        extra = extra_lines_of(comp, HANDLED)
        if event:
            if vals:
                event.write(vals)
        else:
            vals['user_id'] = env.user.id
            vals['partner_ids'] = [(6, 0, [env.user.partner_id.id])]
            event = env['calendar.event'].with_context(
                no_mail_to_attendees=True, mail_notrack=True).create(vals)
        return event, extra

    def archive(self, event):
        if event.user_id != self.ctx.user:
            raise DavError(403, 'Only the organiser can delete an event through DAV')
        if event.recurrence_id or event.recurrency:
            raise DavError(403, 'Recurring events are read-only through DAV')
        event.write({'active': False})
