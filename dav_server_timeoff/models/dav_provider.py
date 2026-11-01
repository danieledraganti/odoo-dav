from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.timeoff import TimeOffCalendar
        return super()._dav_mounts(ctx) + [TimeOffCalendar(ctx, ('calendars', ctx.login, 'timeoff'))]

    def _dav_features(self, ctx):
        return super()._dav_features(ctx) + ['calendar-access']
