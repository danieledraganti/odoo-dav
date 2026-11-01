from odoo import models


class EventEvent(models.Model):
    _inherit = 'event.event'

    def unlink(self):
        self.env['dav.resource.name']._dav_on_unlink(self)
        return super().unlink()
