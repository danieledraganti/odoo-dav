from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.events import EventsCalendar
        mounts = super()._dav_mounts(ctx)
        if ctx.can_read('event.event'):
            mounts.append(EventsCalendar(ctx, ('calendars', ctx.login, 'odoo-events')))
        return mounts

    def _dav_features(self, ctx):
        return super()._dav_features(ctx) + ['calendar-access']
