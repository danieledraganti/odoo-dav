from odoo import models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    def unlink(self):
        self.env['dav.resource.name']._dav_on_unlink(self, {t.id: t.user_ids.ids for t in self})
        return super().unlink()
