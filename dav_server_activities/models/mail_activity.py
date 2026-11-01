from odoo import models


class MailActivity(models.Model):
    _inherit = 'mail.activity'

    def unlink(self):
        self.env['dav.resource.name']._dav_on_unlink(self, {a.id: [a.user_id.id] for a in self})
        return super().unlink()
