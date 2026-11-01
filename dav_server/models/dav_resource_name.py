from odoo import api, fields, models


class DavResourceName(models.Model):
    """DAV bookkeeping for generic (non-partner) records: the resource name
    chosen by the client, its iCalendar UID and the unmapped iCalendar lines.

    This table holds no business data; it is read and written with sudo, while
    the underlying records are always handled with the DAV user's rights.
    """
    _name = 'dav.resource.name'
    _description = 'DAV resource name / unmapped data'

    model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    resource_name = fields.Char(index=True)
    uid = fields.Char()
    extra_lines = fields.Text()

    _res_uniq = models.Constraint('UNIQUE (model, res_id)', 'One DAV mapping per record.')
    _name_uniq = models.Constraint('UNIQUE (model, resource_name)', 'Resource name already used.')

    @api.model
    def _dav_on_unlink(self, records, owners=None):
        """Call from ``unlink`` overrides *before* ``super().unlink()``.

        :param owners: optional dict {record id: user ids who could see it}; the
            deletion is then only reported to those users by sync-collection.
        """
        maps = self.sudo().search([('model', '=', records._name), ('res_id', 'in', records.ids)])
        names = {m.res_id: m.resource_name for m in maps}
        self.env['dav.deletion']._record(records._name, {rid: names.get(rid) for rid in records.ids}, owners)
        maps.unlink()
