from odoo import models


class CalendarEvent(models.Model):
    _inherit = 'calendar.event'

    def unlink(self):
        self.env['dav.resource.name']._dav_on_unlink(
            self, {e.id: (e.user_id | e.partner_ids.user_ids).ids for e in self})
        return super().unlink()
