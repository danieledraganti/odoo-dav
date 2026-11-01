from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.events import EventCalendar
        return super()._dav_mounts(ctx) + [EventCalendar(ctx, ('calendars', ctx.login, 'events'))]

    def _dav_features(self, ctx):
        return super()._dav_features(ctx) + ['calendar-access']
