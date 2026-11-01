from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.activities import ActivityCalendar
        return super()._dav_mounts(ctx) + [ActivityCalendar(ctx, ('calendars', ctx.login, 'activities'))]

    def _dav_features(self, ctx):
        return super()._dav_features(ctx) + ['calendar-access']
