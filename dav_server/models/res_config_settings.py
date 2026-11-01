from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dav_allow_insecure = fields.Boolean(
        string='Allow insecure DAV', config_parameter='dav_server.allow_insecure')
    dav_max_upload_mb = fields.Integer(
        string='DAV max upload (MB)', config_parameter='dav_server.max_upload_mb', default=100)
    dav_max_contacts = fields.Integer(
        string='DAV max contacts', config_parameter='dav_server.max_contacts', default=20000)
    dav_vcard_photos = fields.Boolean(
        string='DAV vCard photos', config_parameter='dav_server.vcard_photos', default=True)
