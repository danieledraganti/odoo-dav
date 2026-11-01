import base64

from urllib.parse import unquote

from odoo import fields
from odoo.tests import tagged

from odoo.addons.dav_server.tests.common import DavCase

FOLDER = '/dav/Reports/Invoices/'


@tagged('post_install', '-at_install')
class TestInvoicePdf(DavCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref('account.group_account_invoice')
        cls.dav_user.write({'group_ids': [(4, group.id)]})
        cls.dav_manager.write({'group_ids': [(4, group.id)]})
        cls.invoice = cls.env['account.move'].sudo().create({
            'move_type': 'out_invoice', 'invoice_date': fields.Date.today(),
            'partner_id': cls.env['res.partner'].create({'name': 'Customer'}).id,
            'invoice_line_ids': [(0, 0, {'name': 'Service', 'quantity': 1, 'price_unit': 100})]})
        cls.draft = cls.env['account.move'].sudo().create({
            'move_type': 'out_invoice', 'invoice_date': fields.Date.today(),
            'partner_id': cls.invoice.partner_id.id,
            'invoice_line_ids': [(0, 0, {'name': 'Draft', 'quantity': 1, 'price_unit': 1})]})
        cls.invoice.action_post()
        cls.attachment = cls.env['ir.attachment'].sudo().create({
            'name': 'invoice.pdf', 'mimetype': 'application/pdf', 'res_model': 'account.move',
            'res_id': cls.invoice.id, 'res_field': 'invoice_pdf_report_file', 'datas': base64.b64encode(b'%PDF-1.4 stored')})
        cls.invoice.invalidate_recordset()

    def _name(self):
        return self.invoice.name.replace('/', '_') + ' [%d].pdf' % self.invoice.id

    def test_only_posted_invoices_listed(self):
        content = unquote(self.list_collection(FOLDER, 'dav_manager').content.decode())
        self.assertIn('[%d].pdf' % self.invoice.id, content)
        self.assertNotIn('[%d].pdf' % self.draft.id, content)

    def test_serves_the_stored_pdf(self):
        res = self.dav('GET', FOLDER + self._name(), login='dav_manager')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b'%PDF-1.4 stored')

    def test_read_only(self):
        res = self.dav('DELETE', FOLDER + self._name(), login='dav_manager')
        self.assertIn(res.status_code, (403, 405))
        self.assertTrue(self.invoice.exists())
