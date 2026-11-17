import base64

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user


_PREVIEW_PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a7xkAAAAASUVORK5CYII='
)


@tagged('post_install', '-at_install')
class TestDesignPreviewWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.designer = new_test_user(cls.env, login='test_preview_designer', groups='base.group_user,dac_erp.group_dac_erp_design')
        cls.sale = new_test_user(cls.env, login='test_preview_sale', groups='base.group_user,dac_erp.group_dac_erp_sale_all')
        cls.production = new_test_user(cls.env, login='test_preview_production', groups='base.group_user,dac_erp.group_dac_erp_production')
        cls.partner = cls.env['res.partner'].create({'name': 'Preview workflow customer'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'user_id': cls.sale.id,
            'user_id_design': cls.designer.id,
            'order_state_custom': 'deposit',
            'fulfillment_method': 'delivery',
        })

    def _preview(self, name='preview.png', payload=_PREVIEW_PNG):
        order = self.order.with_user(self.designer)
        order.action_post_design_conversation_with_images([{
            'name': name, 'mimetype': 'image/png',
            'data': base64.b64encode(payload).decode(),
        }])
        return order

    def _submit_and_approve(self):
        self._preview().action_submit_design_link()
        self.order.with_user(self.sale).action_approve_design()

    def test_full_preview_approval_revision_and_final_handover(self):
        designer_order = self._preview()
        self.assertTrue(self.order.design_preview_sent)
        preview = self.env['dac.design.conversation.message'].sudo().search([
            ('order_id', '=', self.order.id), ('attachment_ids.name', '=', 'preview.png')
        ], limit=1)
        self.assertEqual(base64.b64decode(preview.attachment_ids.datas), _PREVIEW_PNG)
        designer_order.action_submit_design_link()
        self.assertEqual(self.order.design_review_state, 'pending')
        self.assertTrue(self.order.design_done)
        self.order.with_user(self.sale).action_approve_design()
        self.assertEqual(self.order.design_review_state, 'approved')
        self.assertEqual(self.order.design_approved_user_id, self.sale)
        designer_order.action_continue_design_revision()
        self.assertEqual(self.order.design_review_state, 'revision')
        self.assertFalse(self.order.design_preview_sent)
        with self.assertRaises(UserError):
            designer_order.action_submit_design_link()
        self._preview('preview-v2.png').action_submit_design_link()
        self.assertEqual(self.order.design_review_state, 'pending')
        self.order.with_user(self.sale).action_approve_design()
        self.assertEqual(self.order.design_review_state, 'approved')
        designer_order.write({'design_final_file': base64.b64encode(b'final content'), 'design_final_file_filename': 'final.ai'})
        designer_order.action_send_design_final_file()
        self.assertTrue(self.order.design_final_file_sent)
        self.assertFalse(self.order.design_final_file)
        message = self.order.message_ids.filtered(lambda m: 'Đã gửi file thiết kế để bàn giao cho Sản xuất.' in (m.body or ''))[:1]
        self.assertEqual(message.attachment_ids.name, 'final.ai')
        self.assertEqual(base64.b64decode(message.attachment_ids.datas), b'final content')
        self.assertEqual(self.order.design_final_file_count, 1)

    def test_guards_and_permissions(self):
        designer_order = self.order.with_user(self.designer)
        sale_order = self.order.with_user(self.sale)
        with self.assertRaises(UserError):
            designer_order.action_submit_design_link()
        with self.assertRaises(UserError):
            sale_order.action_approve_design()
        with self.assertRaises(UserError):
            designer_order.action_continue_design_revision()
        with self.assertRaises(UserError):
            designer_order.action_send_design_final_file()
        self._preview().action_submit_design_link()
        with self.assertRaises(UserError):
            designer_order.action_approve_design()
        with self.assertRaises(UserError):
            self.order.with_user(self.production).action_approve_design()
        with self.assertRaises(UserError):
            designer_order.action_send_design_final_file()
        sale_order.action_approve_design()
        with self.assertRaises(UserError):
            sale_order.action_continue_design_revision()
        with self.assertRaises(UserError):
            designer_order.action_send_design_final_file()
        with self.assertRaises(UserError):
            sale_order.action_send_design_final_file()

    def test_sale_note_requires_a_new_preview(self):
        self._submit_and_approve()
        sale_order = self.order.with_user(self.sale)
        sale_order.write({'design_conversation_draft': 'Vui lòng sửa màu'})
        sale_order.action_post_design_conversation()
        self.assertEqual(self.order.design_review_state, 'revision')
        self.assertFalse(self.order.design_preview_sent)
        with self.assertRaises(UserError):
            self.order.with_user(self.designer).action_submit_design_link()
        self._preview('revised.png').action_submit_design_link()
        self.assertEqual(self.order.design_review_state, 'pending')

    def test_read_status_is_per_user_and_persists_across_sessions(self):
        order_model = self.env['sale.order'].with_user(self.designer)
        feed = order_model.dac_get_dashboard_design()
        key = next(item['key'] for item in feed['design_notifications']
                   if item['order_id'] == self.order.id and item['key'].startswith('assignment-task-'))
        self.assertNotIn(key, feed['design_read_notifications'])
        self.assertIn(key, order_model.dac_mark_design_notifications_read([key, 'not-visible']))
        self.assertNotIn('not-visible', self.designer.sudo().dac_design_read_notification_keys)
        new_session = self.env['sale.order'].with_user(self.designer).with_context(test_other_device=True)
        self.assertIn(key, new_session.dac_get_dashboard_design()['design_read_notifications'])
        other = new_test_user(self.env, login='other_preview_designer', groups='base.group_user,dac_erp.group_dac_erp_design')
        self.assertNotIn(key, self.env['sale.order'].with_user(other).dac_get_dashboard_design()['design_read_notifications'])
        self.assertNotIn(key, self.env['sale.order'].with_user(other).dac_mark_design_notifications_read([key]))

    def test_notification_task_names_limited_without_changing_task(self):
        task = self.order.task_ids.filtered(lambda item: item.task_type == 'design')[:1]
        long_name = 'Thiết kế khách hàng tên rất dài 01/11'
        task.name = long_name
        feed = self.env['sale.order'].with_user(self.designer).dac_get_dashboard_design()
        item = next(item for item in feed['design_notifications'] if item['key'] == f'assignment-task-{task.id}')
        self.assertIn(long_name[:19] + '…', item['title'])
        self.assertEqual(len(long_name[:19] + '…'), 20)
        self.assertEqual(task.name, long_name)
