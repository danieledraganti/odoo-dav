from odoo import models


class DavProvider(models.AbstractModel):
    _inherit = 'dav.provider'

    def _dav_mounts(self, ctx):
        from ..dav.employees import EmployeeBook
        return super()._dav_mounts(ctx) + [EmployeeBook(ctx, ('addressbooks', ctx.login, 'employees'))]
