"""CalDAV VTODO view of the project.task records assigned to the user."""
import datetime

import vobject

from odoo import fields
from odoo.tools import html2plaintext, plaintext2html

from odoo.addons.dav_server.dav.caldav import (
    CalendarCollection, check_text, extra_lines_of, from_naive_utc, to_utc_naive,
)
from odoo.addons.dav_server.dav.vcard import etag_of

CLOSED = ('1_done', '1_canceled')
HANDLED = {'UID', 'DTSTAMP', 'CREATED', 'LAST-MODIFIED', 'SUMMARY', 'DESCRIPTION', 'STATUS',
           'PRIORITY', 'DUE', 'COMPLETED', 'PERCENT-COMPLETE', 'CATEGORIES', 'X-ODOO-PROJECT'}
CLOSED_WINDOW_DAYS = 90


class TaskCalendar(CalendarCollection):
    model = 'project.task'
    component = 'VTODO'
    display = 'Tasks'

    def scope_domain(self):
        limit = fields.Datetime.now() - datetime.timedelta(days=CLOSED_WINDOW_DAYS)
        return [('user_ids', 'in', [self.ctx.user.id]),
                '|', ('state', 'not in', CLOSED), ('write_date', '>', limit)]

    def etag_parts(self, task):
        return (task.id, task.write_date, task.project_id.name,
                ','.join(sorted(task.tag_ids.mapped('name'))))

    # ------------------------------------------------------------------ build
    def build_ics(self, task, mapping):
        uid = mapping.uid if mapping and mapping.uid else 'odoo-task-%s-%d' % (self.ctx.db_uuid, task.id)
        cal = vobject.iCalendar()
        todo = cal.add('vtodo')
        todo.add('uid').value = uid
        todo.add('dtstamp').value = from_naive_utc(task.write_date)
        todo.add('created').value = from_naive_utc(task.create_date)
        todo.add('last-modified').value = from_naive_utc(task.write_date)
        todo.add('summary').value = task.name or ''
        description = html2plaintext(task.description or '').strip()
        if description:
            todo.add('description').value = description
        if task.state == '1_done':
            todo.add('status').value = 'COMPLETED'
            todo.add('completed').value = from_naive_utc(task.date_end or task.write_date)
            todo.add('percent-complete').value = '100'
        elif task.state == '1_canceled':
            todo.add('status').value = 'CANCELLED'
        else:
            todo.add('status').value = 'NEEDS-ACTION'
        todo.add('priority').value = '1' if task.priority == '1' else '0'
        if task.date_deadline:
            todo.add('due').value = from_naive_utc(task.date_deadline)
        tags = task.tag_ids.mapped('name')
        if tags:
            todo.add('categories').value = tags
        if task.project_id:
            todo.add('x-odoo-project').value = task.project_id.name
        text = cal.serialize()
        extra = (mapping.extra_lines if mapping else '') or ''
        if extra:
            text = text.replace('END:VTODO', extra + 'END:VTODO', 1)
        return text

    # ------------------------------------------------------------------ apply
    def apply_ics(self, task, comp, mapping):
        env = self.ctx.env
        tz = env.user.tz
        c = comp.contents

        def val(name):
            return c[name][0].value if name in c else None

        vals = {}
        summary = check_text((val('summary') or '').strip())
        if not summary:
            summary = task.name if task else 'Untitled'
        if not task or task.name != summary:
            vals['name'] = summary

        if 'description' in c:
            desc = (val('description') or '').strip()
            current = html2plaintext(task.description or '').strip() if task else ''
            if desc != current:
                vals['description'] = plaintext2html(desc) if desc else False

        if 'priority' in c:
            try:
                prio = int(val('priority'))
            except (TypeError, ValueError):
                prio = 0
            new = '1' if 1 <= prio <= 4 else '0'
            if not task or task.priority != new:
                vals['priority'] = new

        due = to_utc_naive(val('due'), tz) if 'due' in c else False
        if not task or task.date_deadline != (due or False):
            vals['date_deadline'] = due or False

        # state: only transitions between "open" and done/cancelled are written;
        # Odoo's finer open states (changes requested, approved, waiting) are kept
        status = (val('status') or '').upper()
        percent = val('percent-complete')
        if status == 'COMPLETED' or (percent in ('100', 100) and not status):
            target = '1_done'
        elif status == 'CANCELLED':
            target = '1_canceled'
        elif status in ('NEEDS-ACTION', 'IN-PROCESS') or (task and 'status' in c):
            target = None if (task and task.state not in CLOSED) else '01_in_progress'
        else:
            target = None
        if target and (not task or task.state != target):
            vals['state'] = target

        extra = extra_lines_of(comp, HANDLED)
        if 'categories' in c:
            names = []
            for node in c['categories']:
                names += node.value if isinstance(node.value, list) else [node.value]
            tags = env['project.tags'].search([])
            by_name = {}
            for tag in tags:
                by_name.setdefault(tag.name.casefold(), env['project.tags'])
                by_name[tag.name.casefold()] |= tag
            ids, unknown = set(), []
            for name in names:
                hit = by_name.get((name or '').strip().casefold())
                if hit and len(hit) == 1:
                    ids.add(hit.id)
                elif name and name.strip():
                    unknown.append(name.strip())
            vals['tag_ids'] = [(6, 0, sorted(ids))]
            if unknown:
                card = vobject.iCalendar()
                todo = card.add('vtodo')
                todo.add('categories').value = unknown
                extra += todo.contents['categories'][0].serialize()

        if task:
            if vals:
                task.write(vals)
        else:
            vals.setdefault('name', summary)
            vals['user_ids'] = [(6, 0, [env.user.id])]
            vals.setdefault('project_id', False)
            task = env['project.task'].create(vals)
        return task, extra
