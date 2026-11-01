from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.articles import KnowledgeRoot
        return super()._dav_mounts(ctx) + [KnowledgeRoot(ctx, ('Knowledge',))]
