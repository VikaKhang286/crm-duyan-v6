from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

EVENT = 'odoo.addons.dac_meta_conversion.models.meta_conversion_event.MetaConversionEvent'
POS_EVENT = 'odoo.addons.dac_meta_conversion.models.pancake_provider.MetaConversionEvent'
HTTP = 'odoo.addons.dac_pancake_connector.models.api_log.requests.request'


@tagged('post_install', '-at_install')
class TestPancakeProvider(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, no_reset_password=True))
        cls.company = cls.env.company
        cls.company.write({'meta_conversion_enabled': False, 'meta_conversion_provider': 'pancake_pos',
            'pancake_pos_api_key': 'private-pos-key', 'pancake_pos_shop_id': '123',
            'pancake_pos_page_id': '987654321', 'pancake_pos_summary_sku': 'CRM-SERVICE',
            'pancake_pos_warehouse_id': 'warehouse-service', 'pancake_pos_capi_ready': True,
            'pancake_pos_silent_ready': True, 'pancake_pos_product_ready': True,
            'pancake_pos_check_status': 'ok'})
        cls.company.pancake_pos_check_fingerprint = cls.company._pancake_fingerprint()
        cls.partner = cls.env['res.partner'].create({'name': 'POS test customer', 'phone': '0909333444'})
        cls.product = cls.env['product.product'].create({'name': 'POS test product', 'type': 'service'})
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'POS test VND', 'currency_id': cls.env.ref('base.VND').id})
        cls.page = cls.env['page.fm.page'].create({'name': 'POS test page', 'page_fm_id_str': '987654321'})
        cls.conversation = cls.env['page.fm.conversation'].create({'conversation_fm_id': 'pos-test-inbox',
            'page_fm_page_id': cls.page.id, 'conv_page_fm_id': '987654321',
            'platform_fm': 'Facebook', 'conversation_type_fm': 'INBOX',
            'partner_id': cls.partner.id, 'customer_fm_id': 'pos-test-customer'})

    def _order(self, **extra):
        self.company.meta_conversion_enabled = True
        vals = {'partner_id': self.partner.id, 'pricelist_id': self.pricelist.id,
            'conversation_id': self.conversation.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1,
                'price_unit': 210000, 'tax_id': [(5, 0, 0)]})]}
        vals.update(extra)
        with patch(EVENT + '._schedule_dispatch'):
            return self.env['sale.order'].create(vals)

    def _prepare(self):
        order = self._order()
        event = order.meta_conversion_event_ids
        payload, reason = event._pancake_payload()
        self.assertFalse(reason)
        return event, payload

    def _remote(self, event, payload, status=0, **extra):
        row = {'id': 456, 'system_id': 789, 'shop_id': 123, 'custom_id': payload['custom_id'],
            'page_id': event.page_id, 'conversation_id': payload['conversation_id'],
            'order_currency': 'VND', 'total_price': event.order_id.amount_total, 'status': status}
        row.update(extra)
        return {'ok': True, 'data': row, 'status': 200, 'state': 'success'}

    def test_provider_snapshot_and_total_payload(self):
        event, payload = self._prepare()
        self.assertEqual(event.delivery_provider, 'pancake_pos')
        self.assertEqual(payload['items'][0]['variation_info']['retail_price'], event.order_id.amount_total)
        self.assertEqual(payload['items'][0]['quantity'], 1)
        self.assertEqual(payload['status'], 0)
        self.assertFalse(payload['is_smc'])
        self.assertEqual(payload['conversation_id'], self.conversation.conversation_fm_id)
        self.assertNotIn('access_token', str(payload))
        self.company.meta_conversion_provider = 'meta_direct'
        self.assertEqual(event.delivery_provider, 'pancake_pos')

    def test_success_creates_once_verifies_total_then_confirms(self):
        event, payload = self._prepare()
        replies = [self._remote(event, payload), self._remote(event, payload), self._remote(event, payload, status=1)]
        with patch(POS_EVENT + '._pancake_find', return_value=(None, True)), \
                patch(POS_EVENT + '._pancake_call', side_effect=replies) as call:
            event._deliver()
            event._deliver()
        self.assertEqual([item.args[1] for item in call.call_args_list], ['POST', 'GET', 'PUT'])
        self.assertEqual(event.state, 'pos_sent')
        self.assertEqual(event.order_id.meta_conversion_state, 'pos_sent')
        self.assertEqual(event.order_id.pancake_order_id, '456')
        self.assertIn('chưa có xác nhận trực tiếp từ Meta', event.message)

    def test_amount_mismatch_never_confirms(self):
        event, payload = self._prepare()
        replies = [self._remote(event, payload), self._remote(event, payload, total_price=1)]
        with patch(POS_EVENT + '._pancake_find', return_value=(None, True)), \
                patch(POS_EVENT + '._pancake_call', side_effect=replies) as call:
            event._deliver()
        self.assertEqual(event.state, 'uncertain')
        self.assertEqual(call.call_count, 2)

    def test_remote_fees_currency_and_identity_must_match(self):
        event, payload = self._prepare()
        event.payload = payload
        for values in [{'shipping_fee': 10000}, {'total_discount': 1}, {'surcharge': 5},
                {'order_currency': 'USD'}, {'page_id': '111'}, {'conversation_id': 'wrong'},
                {'shop_id': 789}, {'custom_id': 'wrong'}]:
            self.assertFalse(event._pancake_validate_remote(self._remote(event, payload, **values)['data']))

    def test_read_search_requires_exact_reference_and_complete_page(self):
        event, payload = self._prepare()
        event.order_id.pancake_export_reference = payload['custom_id']
        Log = type(self.env['dac.pancake.api.log'])
        for response, complete in [({'data': [], 'total_pages': 1}, True),
                ({'data': [], 'total_pages': 2}, False), ({'data': []}, False)]:
            with patch.object(Log, '_request', return_value={'ok': True, 'data': response}):
                self.assertEqual(event._pancake_find(), (None, complete))

    def test_pos_requires_readiness_and_fresh_check(self):
        self.company._check_pancake_export()
        for field in ['pancake_pos_capi_ready', 'pancake_pos_silent_ready', 'pancake_pos_product_ready']:
            self.company[field] = False
            with self.assertRaises(ValidationError):
                self.company._check_pancake_export()
            self.company[field] = True
        self.company.pancake_pos_summary_sku = 'CHANGED'
        with self.assertRaises(ValidationError):
            self.company._check_pancake_export()

    def test_create_timeout_never_blindly_reposts(self):
        event, payload = self._prepare()
        with patch(POS_EVENT + '._pancake_find', return_value=(None, True)), \
                patch(POS_EVENT + '._pancake_call', return_value={'ok': False, 'data': {}, 'state': 'uncertain'}) as call:
            event._deliver()
            event._deliver()
        self.assertEqual(event.state, 'uncertain')
        self.assertEqual(call.call_count, 1)
        with self.assertRaises(UserError):
            event.order_id.action_meta_retry()

    def test_confirm_timeout_does_not_confirm_twice(self):
        event, payload = self._prepare()
        replies = [self._remote(event, payload), self._remote(event, payload), {'ok': False, 'data': {}, 'state': 'uncertain'}]
        with patch(POS_EVENT + '._pancake_find', return_value=(None, True)), \
                patch(POS_EVENT + '._pancake_call', side_effect=replies) as call:
            event._deliver()
            event._deliver()
        self.assertEqual(event.state, 'uncertain')
        self.assertEqual(call.call_count, 3)

    def test_failed_lookup_cannot_authorize_create(self):
        event, payload = self._prepare()
        with patch(POS_EVENT + '._pancake_find', return_value=(None, False)), patch(POS_EVENT + '._pancake_call') as call:
            event._deliver()
        self.assertEqual(event.state, 'waiting')
        call.assert_not_called()

    def test_existing_draft_requires_manual_reconciliation(self):
        event, payload = self._prepare()
        with patch(POS_EVENT + '._pancake_find', return_value=(self._remote(event, payload)['data'], True)), patch(POS_EVENT + '._pancake_call') as call:
            event._deliver()
        call.assert_not_called()
        self.assertEqual(event.state, 'uncertain')

    def test_readonly_reconciliation_proves_matching_confirmed_order(self):
        event, payload = self._prepare()
        event.write({'payload': payload, 'state': 'uncertain'})
        event.order_id.write({'pancake_export_reference': payload['custom_id'], 'pancake_export_shop_id': '123'})
        with patch(POS_EVENT + '._pancake_find', return_value=(self._remote(event, payload, status=1)['data'], True)), patch(POS_EVENT + '._pancake_call') as call:
            event.order_id.action_pancake_reconcile_order()
        call.assert_not_called()
        self.assertEqual(event.state, 'pos_sent')

    def test_imported_order_never_exports_or_enqueues_purchase(self):
        order = self._order(pancake_order_id='1234')
        self.assertFalse(order.meta_conversion_event_ids)

    def test_return_webhook_links_original_without_replacing_lines(self):
        event, payload = self._prepare()
        order = event.order_id
        order.write({'pancake_export_reference': payload['custom_id'], 'pancake_export_shop_id': '123'})
        original_lines = order.order_line.ids
        callback = dict(self._remote(event, payload)['data'], items=[])
        before = self.env['sale.order'].search_count([])
        linked = self.env['sale.order'].action_sync_single_pancake_order(callback)
        self.assertEqual(linked, order)
        self.assertEqual(linked.order_line.ids, original_lines)
        self.assertEqual(self.env['sale.order'].search_count([]), before)
        self.assertEqual(order.pancake_order_id, '456')
        with self.assertRaises(ValidationError):
            self.env['sale.order'].action_sync_single_pancake_order(dict(callback, shop_id=999))

    def test_bulk_import_also_preserves_exported_order(self):
        event, payload = self._prepare()
        order = event.order_id
        order.write({'pancake_export_reference': payload['custom_id'], 'pancake_export_shop_id': '123'})
        self.env['ir.config_parameter'].sudo().set_param('pancake.last_sync_time', '')
        before, lines = self.env['sale.order'].search_count([]), order.order_line.ids
        response = Mock(status_code=200, json=lambda: {'data': [self._remote(event, payload)['data']]})
        with patch('odoo.addons.dac_erp.models.sale_order_sever_copy.requests.get', return_value=response):
            self.env['sale.order'].action_sync_pancake_all_orders()
        self.assertEqual(self.env['sale.order'].search_count([]), before)
        self.assertEqual(order.order_line.ids, lines)
        self.assertEqual(order.pancake_order_id, '456')

    def test_wrong_customer_page_channel_or_ambiguous_conversation_waits(self):
        event, payload = self._prepare()
        for vals in [{'platform_fm': 'Instagram'}, {'conversation_type_fm': 'COMMENT'}, {'conv_page_fm_id': '111'}]:
            with self.subTest(vals=vals):
                with self.env.cr.savepoint():
                    old = {key: self.conversation[key] for key in vals}
                    self.conversation.write(vals)
                    self.assertTrue(event._pancake_payload()[1])
                    self.conversation.write(old)

    def test_stale_configuration_blocks_export(self):
        event, payload = self._prepare()
        self.company.pancake_pos_shop_id = '987'
        with patch(POS_EVENT + '._pancake_call') as call:
            event._deliver()
        call.assert_not_called()
        self.assertEqual(event.state, 'waiting')

    def test_connection_check_never_creates_orders_and_masks_keys(self):
        settings = self.env['res.config.settings'].create({'company_id': self.company.id})
        with patch(HTTP, return_value=Mock(status_code=200, json=lambda: {'success': True, 'shops': [
            {'id': 123, 'pages': [{'id': '987654321'}]}]})) as http:
            settings.action_pancake_connector_check_pos()
        self.assertEqual(http.call_args.args[0], 'GET')
        self.assertEqual(self.company.pancake_pos_check_status, 'ok')
        log = self.env['dac.pancake.api.log'].search([], limit=1)
        self.assertNotIn('private-pos-key', str(log.read()))
        self.assertNotIn('api_key', log.endpoint)

    def test_log_timeout_and_html_success_are_uncertain(self):
        Log = self.env['dac.pancake.api.log']
        for response in [requests.ReadTimeout('https://fake?api_key=private-pos-key'),
                Mock(status_code=200, json=Mock(side_effect=ValueError))]:
            with patch(HTTP, side_effect=response if isinstance(response, Exception) else None,
                    return_value=None if isinstance(response, Exception) else response):
                result = Log._request(self.company, 'Test create', 'POST', 'shops/123/orders', payload={'custom_id': 'test'})
            self.assertEqual(result['state'], 'uncertain')
            self.assertNotIn('private-pos-key', str(result['log'].read()))

    def test_log_permissions_and_company_secrets(self):
        manager = new_test_user(self.env, login='pos_log_manager', groups='base.group_user,dac_erp.group_dac_erp_manager')
        log = self.env['dac.pancake.api.log'].sudo()._start(self.company, 'Read check', 'GET', 'shops')
        log.with_user(manager).read(['summary'])
        with self.assertRaises(AccessError):
            log.with_user(manager).write({'summary': 'modified'})
        with self.assertRaises(AccessError):
            self.company.with_user(manager).read(['pancake_pos_api_key'])

    def test_setup_views_compile(self):
        arch = etree.fromstring(self.env['res.config.settings'].get_view(view_id=self.env.ref('base_setup.res_config_settings_view_form').id)['arch'])
        app = arch.xpath('//app[@name="dac_pancake_connector"]')
        self.assertTrue(app)
        self.assertTrue(app[0].xpath('.//field[@name="pancake_pos_api_key"][@password="True"]'))
        self.assertTrue(arch.xpath('//field[@name="meta_conversion_provider"]'))
