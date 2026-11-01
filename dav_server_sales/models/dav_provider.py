from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.sales import SaleOrderFolder
        mounts = super()._dav_mounts(ctx)
        if ctx.can_read('sale.order'):
            mounts.append(SaleOrderFolder(ctx, ('reports', 'sale-orders')))
        return mounts
