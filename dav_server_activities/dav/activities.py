"""CalDAV VTODO view of the mail activities (to-dos, calls, meetings...) assigned to the user.

Editing the summary, note and due date is allowed; marking a VTODO completed
marks the activity done in Odoo (it then disappears from the list); DELETE
cancels it. New activities cannot be created here: they belong to a document.
"""
import datetime

import vobject

from odoo.tools import html2plaintext, plaintext2html

from odoo.addons.dav_server.dav.caldav import (
    CalendarCollection, check_text, from_naive_utc,
)
from odoo.addons.dav_server.dav.resource import DavError

HANDLED = {'UID', 'DTSTAMP', 'CREATED', 'LAST-MODIFIED', 'SUMMARY', 'DESCRIPTION', 'STATUS',
           'DUE', 'URL', 'CATEGORIES', 'PERCENT-COMPLETE', 'COMPLETED', 'PRIORITY'}


class ActivityCalendar(CalendarCollection):
    model = 'mail.activity'
    component = 'VTODO'
    display = 'Activities'

    def scope_domain(self):
        return [('user_id', '=', self.ctx.user.id)]

    def etag_parts(self, activity):
        return (activity.id, activity.write_date, activity.date_deadline)

    def _title(self, activity):
        summary = activity.summary or activity.activity_type_id.name or 'Activity'
        return '%s: %s' % (activity.res_name, summary) if activity.res_name else summary

    def _url(self, activity):
        base = (self.ctx.param('web_base_url') or
                self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').rstrip('/')
        if not (base and activity.res_model and activity.res_id):
            return ''
        return '%s/odoo/%s/%d' % (base, activity.res_model, activity.res_id)

    def build_ics(self, activity, mapping):
        uid = mapping.uid if mapping and mapping.uid else 'odoo-activity-%s-%d' % (self.ctx.db_uuid, activity.id)
        cal = vobject.iCalendar()
        todo = cal.add('vtodo')
        todo.add('uid').value = uid
        todo.add('dtstamp').value = from_naive_utc(activity.write_date)
        todo.add('created').value = from_naive_utc(activity.create_date)
        todo.add('last-modified').value = from_naive_utc(activity.write_date)
        todo.add('summary').value = self._title(activity)
        note = html2plaintext(activity.note or '').strip()
        if note:
            todo.add('description').value = note
        todo.add('status').value = 'NEEDS-ACTION'
        if activity.date_deadline:
            todo.add('due').value = activity.date_deadline
        if activity.activity_type_id:
            todo.add('categories').value = [activity.activity_type_id.name]
        url = self._url(activity)
        if url:
            todo.add('url').value = url
        text = cal.serialize()
        extra = (mapping.extra_lines if mapping else '') or ''
        if extra:
            text = text.replace('END:VTODO', extra + 'END:VTODO', 1)
        return text

    def apply_ics(self, activity, comp, mapping):
        if not activity:
            raise DavError(403, 'Activities are created from their document in Odoo')
        c = comp.contents

        def val(name):
            return c[name][0].value if name in c else None

        status = (val('status') or '').upper()
        if status == 'COMPLETED' or val('percent-complete') in ('100', 100):
            activity.action_feedback()
            return activity, ''
        if status == 'CANCELLED':
            raise DavError(403, 'Use DELETE to cancel an activity')

        vals = {}
        # the title shown to the client is "<document>: <summary>"; only changes to it are written back
        summary = check_text((val('summary') or '').strip())
        if summary and summary != self._title(activity):
            prefix = '%s: ' % activity.res_name if activity.res_name else ''
            vals['summary'] = summary[len(prefix):] if prefix and summary.startswith(prefix) else summary
        if 'description' in c:
            desc = (val('description') or '').strip()
            if desc != html2plaintext(activity.note or '').strip():
                vals['note'] = plaintext2html(desc) if desc else False
        due = val('due')
        if isinstance(due, datetime.datetime):
            due = due.date()
        if due and due != activity.date_deadline:
            vals['date_deadline'] = due
        if vals:
            activity.write(vals)
        return activity, ''

    def archive(self, activity):
        activity.unlink()
