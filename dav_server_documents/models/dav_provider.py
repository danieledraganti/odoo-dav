from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.docs import DocsRoot
        return super()._dav_mounts(ctx) + [DocsRoot(ctx, ('Files',))]

    def _dav_features(self, ctx):
        return super()._dav_features(ctx) + ['2']
