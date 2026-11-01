from odoo import models


class HrLeave(models.Model):
    _inherit = 'hr.leave'

    def unlink(self):
        self.env['dav.resource.name']._dav_on_unlink(self)
        return super().unlink()
