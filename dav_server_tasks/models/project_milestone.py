from odoo import models


class ProjectMilestone(models.Model):
    _inherit = 'project.milestone'

    def unlink(self):
        self.env['dav.resource.name']._dav_on_unlink(self)
        return super().unlink()
