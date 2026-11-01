from datetime import timedelta

from odoo import api, fields, models


class DavDeletion(models.Model):
    """Tombstones of hard-deleted records, needed by DAV sync-collection."""
    _name = 'dav.deletion'
    _description = 'DAV deletion tombstone'

    model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True)
    resource_name = fields.Char()
    deleted_at = fields.Datetime(required=True, default=fields.Datetime.now, index=True)
    restricted = fields.Boolean(help='Only the users in `user_ids` may see this tombstone.')
    user_ids = fields.Many2many('res.users', 'dav_deletion_user_rel', 'deletion_id', 'user_id')

    @api.model
    def _record(self, model, id_names, owners=None):
        """:param id_names: dict {record id: DAV resource name or False}
        :param owners: optional dict {record id: user ids who could see the record};
            when given, the tombstone is only reported to those users"""
        if id_names:
            self.sudo().create([
                {'model': model, 'res_id': rid, 'resource_name': name or False,
                 'restricted': owners is not None,
                 'user_ids': [(6, 0, (owners or {}).get(rid, []))]}
                for rid, name in id_names.items()])

    @api.model
    def _visible_domain(self):
        """Domain (to be used with sudo) of the tombstones the current user may see."""
        return ['|', ('restricted', '=', False), ('user_ids', 'in', [self.env.uid])]

    @api.autovacuum
    def _gc_dav_deletion(self):
        limit = fields.Datetime.now() - timedelta(days=90)
        self.sudo().search([('deleted_at', '<', limit)]).unlink()
