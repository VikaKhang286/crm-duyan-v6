from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


@tagged('post_install', '-at_install')
class TestSecurityAndWorkflowReview(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sale_a = new_test_user(
            cls.env,
            login='workflow_review_sale_a',
            groups='base.group_user,dac_erp.group_dac_erp_sale_all',
        )
        cls.sale_b = new_test_user(
            cls.env,
            login='workflow_review_sale_b',
            groups='base.group_user,dac_erp.group_dac_erp_sale_all',
        )
        cls.design = new_test_user(
            cls.env,
            login='workflow_review_design',
            groups='base.group_user,dac_erp.group_dac_erp_design',
        )
        cls.production = new_test_user(
            cls.env,
            login='workflow_review_production',
            groups='base.group_user,dac_erp.group_dac_erp_production',
        )
        cls.partner = cls.env['res.partner'].create({'name': 'Review isolation partner'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'user_id': cls.sale_a.id,
            'order_state_custom': 'production',
            'fulfillment_method': 'delivery',
        })

    def test_operational_roles_cannot_read_system_parameters(self):
        for user in (self.sale_a, self.design, self.production):
            with self.assertRaises(AccessError):
                self.env['ir.config_parameter'].with_user(user).search_read([], ['key'], limit=1)

    def test_operational_user_can_load_public_branding_only(self):
        params = self.env['ir.config_parameter'].sudo()
        params.set_param('change_odoo_theme_header_color.website_title', 'DAC Test')
        params.set_param('change_odoo_theme_header_color.image_1024', 'public-image')
        params.set_param('page_fm.access_token', 'must-not-be-exposed')

        branding = self.env['res.users'].with_user(
            self.sale_a
        ).get_chameleon_public_branding()

        self.assertEqual(branding, {
            'website_title': 'DAC Test',
            'image_1024': 'public-image',
        })
        self.assertNotIn('must-not-be-exposed', str(branding))

    def test_review_state_is_isolated_per_user(self):
        self.order.with_user(self.sale_a).write({'workflow_review_state': 'quotation'})

        # Browse through independent environments, matching separate HTTP requests.
        order_a = self.env['sale.order'].with_user(self.sale_a).browse(self.order.id)
        order_b = self.env['sale.order'].with_user(self.sale_b).browse(self.order.id)

        self.assertEqual(order_a.workflow_review_state, 'quotation')
        self.assertEqual(order_b.workflow_review_state, 'production')
        self.assertEqual(self.order.order_state_custom, 'production')

    def test_future_review_step_is_rejected(self):
        order = self.order.with_user(self.sale_a)
        order.write({'order_state_custom': 'deposit'})
        with self.assertRaises(UserError):
            order.write({'workflow_review_state': 'production'})

    def test_real_transition_clears_review_preferences(self):
        self.order.with_user(self.sale_a).write({'workflow_review_state': 'quotation'})
        self.order.with_user(self.sale_b).write({'workflow_review_state': 'deposit'})
        self.assertEqual(
            self.env['dac.workflow.review.preference'].sudo().search_count([
                ('order_id', '=', self.order.id),
            ]),
            2,
        )

        self.order.write({'order_state_custom': 'delivery'})
        self.assertFalse(
            self.env['dac.workflow.review.preference'].sudo().search([
                ('order_id', '=', self.order.id),
            ])
        )
