"""Read-only folder with the PDF of the quotations and sales orders the user can see."""
from odoo.tools.translate import LazyTranslate

from odoo.addons.dav_server.dav.files import RecordFolder

REPORT = 'sale.action_report_saleorder'


_lt = LazyTranslate(__name__)


class SaleOrderFolder(RecordFolder):
    model = 'sale.order'
    display = _lt('Quotations and orders')

    def scope_domain(self):
        return [('state', 'in', ('draft', 'sent', 'sale')), ('date_order', '>', self.window_start())]

    def record_label(self, order):
        return order.name

    def etag_parts(self, order):
        return (order.id, order.write_date, order.state)

    def content(self, order):
        pdf, _kind = self.env['ir.actions.report']._render_qweb_pdf(REPORT, order.ids)
        return pdf
