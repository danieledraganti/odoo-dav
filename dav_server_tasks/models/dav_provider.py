from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.milestones import MilestoneCalendar
        from ..dav.tasks import TaskCalendar
        return super()._dav_mounts(ctx) + [TaskCalendar(ctx, ('calendars', ctx.login, 'tasks')),
                                           MilestoneCalendar(ctx, ('calendars', ctx.login, 'milestones'))]

    def _dav_features(self, ctx):
        return super()._dav_features(ctx) + ['calendar-access']
