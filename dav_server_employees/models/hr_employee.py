from odoo import models


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    def unlink(self):
        self.env['dav.resource.name']._dav_on_unlink(self)
        return super().unlink()
