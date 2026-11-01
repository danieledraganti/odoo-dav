"""Read-only folder with the PDF of the posted customer invoices and credit notes the user can see."""
from odoo.addons.dav_server.dav.files import RecordFolder

REPORT = 'account.account_invoices'


class InvoiceFolder(RecordFolder):
    model = 'account.move'
    display = 'Customer invoices'

    def scope_domain(self):
        return [('move_type', 'in', ('out_invoice', 'out_refund')), ('state', '=', 'posted'),
                ('invoice_date', '>', self.window_start().date())]

    def record_label(self, invoice):
        return invoice.name

    def _stored(self, invoice):
        return invoice.invoice_pdf_report_id

    def size(self, invoice):
        return self._stored(invoice).file_size or None

    def content(self, invoice):
        stored = self._stored(invoice)
        if stored:
            return stored.raw
        pdf, _kind = self.env['ir.actions.report']._render_qweb_pdf(REPORT, invoice.ids)
        return pdf
