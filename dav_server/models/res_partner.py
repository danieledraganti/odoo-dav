from odoo import models


class ResPartner(models.Model):
    _inherit = 'res.partner'

    _write_date_idx = models.Index('(write_date)')

    def unlink(self):
        names = {
            extra.partner_id.id: extra.resource_name
            for extra in self.env['dav.vcard.extra'].sudo().search([('partner_id', 'in', self.ids)])
        }
        id_names = {pid: names.get(pid) for pid in self.ids}
        result = super().unlink()
        self.env['dav.deletion']._record('res.partner', id_names)
        return result

    def _dav_touch(self):
        """Bump write_date without triggering ORM side effects (used when only
        DAV-side data changed, so that ETag / sync-token change)."""
        if self.ids:
            self.env.cr.execute(
                "UPDATE res_partner SET write_date = (now() AT TIME ZONE 'UTC') WHERE id IN %s",
                [tuple(self.ids)])
            self.invalidate_recordset(['write_date'])
