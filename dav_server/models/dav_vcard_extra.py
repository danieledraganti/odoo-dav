from odoo import fields, models


class DavVcardExtra(models.Model):
    """vCard data of a partner that has no Odoo field to live in.

    Stored verbatim so that a DAV round trip never loses information
    (second phone, IM, birthday, ...).
    """
    _name = 'dav.vcard.extra'
    _description = 'Unmapped vCard properties'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)
    resource_name = fields.Char(index=True, help="Resource name (*.vcf) chosen by the DAV client.")
    uid = fields.Char(help="UID chosen by the DAV client.")
    lines = fields.Text(help="Raw vCard property lines that are not mapped to Odoo fields.")
    email_params = fields.Char()
    phone_params = fields.Char()
    adr_params = fields.Char()

    _partner_uniq = models.Constraint('UNIQUE (partner_id)', 'One extra record per partner.')
