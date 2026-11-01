from urllib.parse import unquote

from odoo.tests import tagged

from odoo.addons.dav_server.tests.common import DavCase

FOLDER = '/dav/reports/sale-orders/'
LOGIN = 'dav_manager'


@tagged('post_install', '-at_install')
class TestSalesPdf(DavCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref('sales_team.group_sale_salesman')
        cls.dav_user.write({'group_ids': [(4, group.id)]})
        cls.dav_manager.write({'group_ids': [(4, group.id)]})
        cls.order = cls.env['sale.order'].sudo().create({
            'user_id': cls.dav_manager.id,
            'partner_id': cls.env['res.partner'].create({'name': 'Customer'}).id})

    def _name(self):
        return self.env['sale.order'].browse(self.order.id).display_name

    def test_listed_by_name_and_id(self):
        content = unquote(self.list_collection(FOLDER, LOGIN).content.decode())
        self.assertIn('[%d].pdf' % self.order.id, content)

    def test_download_pdf(self):
        name = self.order.name.replace('/', '_') + ' [%d].pdf' % self.order.id
        res = self.dav('GET', FOLDER + name, login=LOGIN)
        self.assertEqual(res.status_code, 200)
        self.assertIn('pdf', res.headers['Content-Type'])
        self.assertTrue(res.content)

    def test_wrong_name_or_id_not_found(self):
        self.assertEqual(self.dav('GET', FOLDER + 'whatever [%d].pdf' % self.order.id, login=LOGIN).status_code, 404)
        self.assertEqual(self.dav('GET', FOLDER + '../x [1].pdf', login=LOGIN).status_code, 404)

    def test_read_only(self):
        name = self.order.name.replace('/', '_') + ' [%d].pdf' % self.order.id
        for method in ('PUT', 'DELETE', 'MKCOL'):
            res = self.dav(method, FOLDER + name, login=LOGIN, body=b'x' if method == 'PUT' else None)
            self.assertIn(res.status_code, (403, 405, 409), method)
        self.assertTrue(self.order.exists())

    def test_hidden_without_sales_rights(self):
        res = self.list_collection(FOLDER, 'plain_user')
        self.assertEqual(res.status_code, 403)  # plain_user has no DAV role at all
        self.dav_user.write({'group_ids': [(3, self.env.ref('sales_team.group_sale_salesman').id)]})
        res = self.list_collection(FOLDER, 'dav_user')
        self.assertEqual(res.status_code, 404)
