from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.invoices import InvoiceFolder
        mounts = super()._dav_mounts(ctx)
        if ctx.can_read('account.move'):
            mounts.append(InvoiceFolder(ctx, ('reports', 'invoices')))
        return mounts
