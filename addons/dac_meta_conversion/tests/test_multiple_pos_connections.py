from copy import deepcopy
import hashlib
import json
from unittest.mock import Mock, patch

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

EVENT = 'odoo.addons.dac_meta_conversion.models.meta_conversion_event.MetaConversionEvent'
POS_EVENT = 'odoo.addons.dac_meta_conversion.models.pancake_provider.MetaConversionEvent'
HTTP = 'odoo.addons.dac_pancake_connector.models.api_log.requests.request'


@tagged('post_install', '-at_install')
class TestMultiplePosConnections(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, no_reset_password=True))
        cls.company = cls.env.company
        cls.company.meta_conversion_enabled = False
        cls.company.meta_conversion_provider = 'pancake_pos'
        cls.Connection = cls.env['dac.pancake.pos.connection']
        cls.a = cls.Connection.create({'name': 'Print shop', 'company_id': cls.company.id,
            'pancake_pos_api_key': 'only-key-a', 'pancake_pos_shop_id': '111',
            'pancake_pos_page_id': '99111', 'pancake_pos_summary_sku': 'A-SERVICE',
            'pancake_pos_warehouse_id': 'A-WAREHOUSE', 'pancake_pos_capi_ready': True,
            'pancake_pos_silent_ready': True, 'pancake_pos_product_ready': True,
            'pancake_pos_check_status': 'ok'})
        cls.b = cls.a.copy({'name': 'Cart shop', 'pancake_pos_api_key': 'only-key-b',
            'pancake_pos_shop_id': '222', 'pancake_pos_page_id': '99222',
            'pancake_pos_summary_sku': 'B-SERVICE', 'pancake_pos_warehouse_id': 'B-WAREHOUSE'})
        for connection in cls.a | cls.b:
            connection.write({'pancake_pos_check_status': 'ok', 'pancake_pos_check_fingerprint': connection._pancake_fingerprint()})
        cls.company.pancake_pos_connection_id = cls.a
        cls.partner = cls.env['res.partner'].create({'name': 'Multi shop buyer'})
        cls.product = cls.env['product.product'].create({'name': 'Multi shop service', 'type': 'service'})
        cls.pricelist = cls.env['product.pricelist'].create({'name': 'Multi shop VND', 'currency_id': cls.env.ref('base.VND').id})
        cls.conversations = {}
        for connection in cls.a | cls.b:
            page = cls.env['page.fm.page'].create({'name': connection.name, 'page_fm_id_str': connection.pancake_pos_page_id})
            cls.conversations[connection.id] = cls.env['page.fm.conversation'].create({
                'conversation_fm_id': 'multi-inbox-' + connection.pancake_pos_shop_id,
                'page_fm_page_id': page.id, 'conv_page_fm_id': connection.pancake_pos_page_id,
                'platform_fm': 'Facebook', 'conversation_type_fm': 'INBOX', 'partner_id': cls.partner.id})

    def _event(self, connection=None):
        self.company.meta_conversion_enabled = True
        target = connection or self.company.pancake_pos_connection_id
        values = {'partner_id': self.partner.id, 'pricelist_id': self.pricelist.id,
            'conversation_id': self.conversations[target.id].id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'price_unit': 345000,
                'product_uom_qty': 1, 'tax_id': [(5, 0, 0)]})]}
        if connection:
            values['pancake_pos_connection_id'] = connection.id
        with patch(EVENT + '._schedule_dispatch'):
            order = self.env['sale.order'].create(values)
        return order.meta_conversion_event_ids

    def test_default_connection_is_pinned_and_per_order_override_works(self):
        first = self._event()
        second = self._event(self.b)
        self.assertEqual(first.pancake_connection_id, self.a)
        self.assertEqual(second.pancake_connection_id, self.b)
        self.assertEqual(first.pancake_shop_id, '111')
        self.assertEqual(second.pancake_shop_id, '222')
        self.company.pancake_pos_connection_id = self.b
        payload, reason = first._pancake_payload()
        self.assertFalse(reason)
        self.assertEqual(payload['shop_id'], 111)
        self.assertEqual(payload['items'][0]['variation_id'], 'A-SERVICE')
        self.assertEqual(first.order_id.pancake_pos_connection_id, self.a)

    def test_route_cannot_be_changed_after_enqueue(self):
        event = self._event(self.a)
        with self.assertRaises(ValidationError):
            event.order_id.pancake_pos_connection_id = self.b

    def test_api_uses_explicit_key_and_log_connection_after_default_switch(self):
        self.company.pancake_pos_connection_id = self.b
        with patch(HTTP, return_value=Mock(status_code=200, json=lambda: {'success': True, 'data': []})) as http:
            result = self.env['dac.pancake.api.log']._request(self.company, 'Pinned lookup', 'GET',
                'shops/111/orders', connection=self.a)
        self.assertEqual(http.call_args.kwargs['params']['api_key'], 'only-key-a')
        self.assertEqual(result['log'].pos_connection_id, self.a)
        self.assertEqual(result['log'].pos_shop_id, '111')
        self.assertNotIn('only-key-a', str(result['log'].read()))

    def test_wrong_shop_and_log_cannot_use_another_connection(self):
        Log = self.env['dac.pancake.api.log']
        with patch(HTTP) as http:
            with self.assertRaises(ValueError):
                Log._request(self.company, 'Wrong shop', 'GET', 'shops/222/orders', connection=self.a)
            log = Log._start(self.company, 'A lookup', 'GET', 'shops', connection=self.a)
            with self.assertRaises(ValueError):
                Log._request(self.company, 'Wrong log', 'GET', 'shops', log=log, connection=self.b)
        http.assert_not_called()

    def test_single_shop_key_discovers_id_without_replacing_default(self):
        connection = self.Connection.create({'name': 'New cart shop', 'company_id': self.company.id, 'pancake_pos_api_key': 'new-key'})
        with patch(HTTP, return_value=Mock(status_code=200, json=lambda: {'success': True, 'shops': [
                {'id': 333, 'name': 'Xưởng xe đẩy gấp gọn', 'pages': [{'id': '99333', 'platform': 'facebook'}]}]})):
            connection.action_check_connection()
        self.assertEqual(connection.pancake_pos_shop_id, '333')
        self.assertEqual(connection.pancake_pos_shop_name, 'Xưởng xe đẩy gấp gọn')
        self.assertEqual(self.company.pancake_pos_connection_id, self.a)
        self.assertEqual(connection.pancake_pos_check_status, 'ok')

    def test_account_key_with_many_shops_requires_explicit_shop_id(self):
        connection = self.Connection.create({'name': 'Account key', 'pancake_pos_api_key': 'account-key'})
        with patch(HTTP, return_value=Mock(status_code=200, json=lambda: {'success': True, 'shops': [
                {'id': 111, 'name': 'Print', 'pages': []}, {'id': 222, 'name': 'Cart', 'pages': []}]})):
            connection.action_check_connection()
        self.assertFalse(connection.pancake_pos_shop_id)
        self.assertIn('111', connection.available_shops)
        self.assertIn('222', connection.available_shops)
        self.assertEqual(connection.pancake_pos_check_status, 'error')

    def test_stale_check_and_archived_connection_block_export(self):
        self.a._check_pancake_export()
        self.a.pancake_pos_page_id = '99999'
        self.assertFalse(self.a.check_is_current)
        with self.assertRaises(ValidationError):
            self.a._check_pancake_export()
        self.a.active = False
        with self.assertRaises(ValidationError):
            self.a._check_pancake_export()

    def test_manager_can_choose_metadata_but_cannot_read_key_or_edit(self):
        user = new_test_user(self.env, login='multi_pos_manager', groups='base.group_user,dac_erp.group_dac_erp_manager')
        self.a.with_user(user).read(['name', 'pancake_pos_shop_id'])
        with self.assertRaises(AccessError):
            self.a.with_user(user).read(['pancake_pos_api_key'])
        with self.assertRaises(AccessError):
            self.a.with_user(user).write({'name': 'Changed'})
        with self.assertRaises(AccessError):
            self.a.with_user(user).action_check_connection()

    def test_cross_company_default_and_order_are_rejected(self):
        other = self.env['res.company'].create({'name': 'Other POS company'})
        connection = self.Connection.create({'name': 'Other connection', 'company_id': other.id, 'pancake_pos_api_key': 'other-key'})
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.company.pancake_pos_connection_id = connection
        with self.assertRaises(ValueError), patch(HTTP) as http:
            self.env['dac.pancake.api.log']._request(self.company, 'Cross company', 'GET', 'shops', connection=connection)
        http.assert_not_called()
        order = self._event().order_id
        with self.assertRaises(ValidationError):
            order.pancake_pos_connection_id = connection

    def test_legacy_migration_is_idempotent_and_maps_old_pos_logs(self):
        self.company.write({'pancake_pos_connection_id': False, 'pancake_pos_api_key': 'legacy-key',
            'pancake_pos_shop_id': '444', 'pancake_pos_page_id': '99444'})
        log = self.env['dac.pancake.api.log']._start(self.company, 'Legacy read', 'GET', 'shops', connection=False)
        before = self.Connection.search_count([])
        self.Connection._migrate_legacy_connections()
        migrated = self.company.pancake_pos_connection_id
        self.assertTrue(migrated)
        self.assertEqual(migrated.pancake_pos_api_key, 'legacy-key')
        self.assertEqual(log.pos_connection_id, migrated)
        self.assertEqual(log.pos_shop_id, '444')
        self.Connection._migrate_legacy_connections()
        self.assertEqual(self.Connection.search_count([]), before + 1)

    def test_pinned_connection_dispatch_ignores_default_change(self):
        event = self._event(self.a)
        payload, reason = event._pancake_payload()
        self.assertFalse(reason)
        self.company.pancake_pos_connection_id = self.b
        base = {'id': 888, 'shop_id': 111, 'custom_id': payload['custom_id'], 'page_id': '99111',
            'conversation_id': payload['conversation_id'], 'order_currency': 'VND', 'total_price': 345000, 'status': 0}
        replies = [{'ok': True, 'data': base}, {'ok': True, 'data': base}, {'ok': True, 'data': dict(base, status=1)}]
        with patch(POS_EVENT + '._pancake_find', return_value=(None, True)), patch(POS_EVENT + '._pancake_call', side_effect=replies):
            event._deliver()
        self.assertEqual(event.state, 'pos_sent')
        self.assertEqual(event.pancake_connection_id, self.a)

    def test_legacy_validation_does_not_borrow_another_connections_readiness(self):
        self.company.pancake_pos_connection_id = self.a
        self.company.pancake_pos_capi_ready = False
        self.a._check_pancake_export()
        with self.assertRaises(ValidationError):
            self.company._check_pancake_legacy_export()

    def _use_quick_entry(self):
        self.a.write({'pancake_pos_item_mode': 'one_time',
            'pancake_pos_item_name': 'Giá trị đơn Odoo',
            'pancake_pos_summary_sku': False, 'pancake_pos_product_ready': False})
        self.a.pancake_pos_check_fingerprint = self.a._pancake_fingerprint()

    def _quick_remote(self, event, payload, status=0):
        return {'id': 888, 'shop_id': 111, 'custom_id': payload['custom_id'],
            'page_id': event.page_id, 'conversation_id': payload['conversation_id'],
            'order_currency': 'VND', 'total_price': 345000, 'status': status,
            'items': deepcopy(payload['items'])}

    def test_quick_entry_needs_no_catalog_product_or_inventory_confirmation(self):
        self._use_quick_entry()
        self.a._check_pancake_export()
        event = self._event(self.a)
        payload, reason = event._pancake_payload()
        self.assertFalse(reason)
        item = payload['items'][0]
        self.assertTrue(item['one_time_product'])
        self.assertFalse(item['product_id'])
        self.assertFalse(item['variation_id'])
        self.assertEqual(item['quantity'], 1)
        self.assertEqual(item['variation_info']['name'], 'Giá trị đơn Odoo — ' + event.order_id.name)
        self.assertEqual(item['variation_info']['retail_price'], 345000)
        self.assertEqual(payload['status'], 0)
        self.assertFalse(payload['is_smc'])

    def test_quick_entry_still_requires_capi_silence_warehouse_and_name(self):
        self._use_quick_entry()
        for field, value in [('pancake_pos_capi_ready', False), ('pancake_pos_silent_ready', False),
                ('pancake_pos_warehouse_id', False), ('pancake_pos_item_name', '   ')]:
            before = self.a[field]
            self.a[field] = value
            self.a.pancake_pos_check_fingerprint = self.a._pancake_fingerprint()
            with self.assertRaises(ValidationError):
                self.a._check_pancake_export()
            self.a[field] = before
        self.a.pancake_pos_check_fingerprint = self.a._pancake_fingerprint()
        self.a._check_pancake_export()

    def test_quick_entry_remote_line_must_match_before_confirmation(self):
        self._use_quick_entry()
        event = self._event(self.a)
        payload, _ = event._pancake_payload()
        event.payload = payload
        row = self._quick_remote(event, payload)
        self.assertTrue(event._pancake_validate_remote(row))
        for path, value in [('items', []), ('items', [None]),
                ('one_time_product', False), ('quantity', 2), ('is_bonus_product', True),
                ('discount_each_product', 10), ('name', 'Another order'), ('retail_price', 1)]:
            changed = deepcopy(row)
            if path == 'items':
                changed[path] = value
            elif path in ('name', 'retail_price'):
                changed['items'][0]['variation_info'][path] = value
            else:
                changed['items'][0][path] = value
            with self.subTest(field=path):
                self.assertFalse(event._pancake_validate_remote(changed))

    def test_quick_entry_verifies_draft_then_confirms_once(self):
        self._use_quick_entry()
        event = self._event(self.a)
        payload, _ = event._pancake_payload()
        replies = [{'ok': True, 'data': self._quick_remote(event, payload, status)}
            for status in (0, 0, 1)]
        with patch(POS_EVENT + '._pancake_find', return_value=(None, True)), \
                patch(POS_EVENT + '._pancake_call', side_effect=replies) as call:
            event._deliver()
            event._deliver()
        self.assertEqual([c.args[1] for c in call.call_args_list], ['POST', 'GET', 'PUT'])
        self.assertTrue(all('/products' not in c.args[2] for c in call.call_args_list))
        self.assertEqual(event.state, 'pos_sent')

    def test_quick_entry_mismatch_stops_before_confirming(self):
        self._use_quick_entry()
        event = self._event(self.a)
        payload, _ = event._pancake_payload()
        bad = self._quick_remote(event, payload)
        bad['items'][0]['one_time_product'] = False
        replies = [{'ok': True, 'data': self._quick_remote(event, payload)}, {'ok': True, 'data': bad}]
        with patch(POS_EVENT + '._pancake_find', return_value=(None, True)), \
                patch(POS_EVENT + '._pancake_call', side_effect=replies) as call:
            event._deliver()
        self.assertEqual(event.state, 'uncertain')
        self.assertEqual([c.args[1] for c in call.call_args_list], ['POST', 'GET'])

    def test_quick_entry_mode_and_name_require_new_connection_check(self):
        self.a.pancake_pos_item_mode = 'one_time'
        self.assertFalse(self.a.check_is_current)
        self.a.pancake_pos_check_fingerprint = self.a._pancake_fingerprint()
        self.a.pancake_pos_item_name = 'New label'
        self.assertFalse(self.a.check_is_current)

    def test_sku_fingerprint_compatibility_keeps_existing_deliveries_valid(self):
        from odoo.addons.dac_pancake_connector.models.pos_connection import CONFIG_FIELDS
        original = hashlib.sha256(json.dumps([self.a.active] + [self.a[name] for name in CONFIG_FIELDS]).encode()).hexdigest()
        self.assertEqual(self.a._pancake_fingerprint(), original)
        self.a.pancake_pos_item_name = 'Unused in SKU mode'
        self.assertEqual(self.a._pancake_fingerprint(), original)

    def test_exact_note_reference_handles_pos_ignoring_custom_order_id(self):
        self._use_quick_entry()
        event = self._event(self.a)
        payload, _ = event._pancake_payload()
        event.payload = payload
        row = self._quick_remote(event, payload)
        row.update(custom_id=None, note=payload['custom_id'])
        self.assertTrue(event._pancake_validate_remote(row))
        for changed in [dict(row, note='prefix ' + row['note']), dict(row, note=row['note'] + ' suffix'),
                dict(row, custom_id='another-order'), dict(row, note='')]:
            self.assertFalse(event._pancake_validate_remote(changed))

    def test_note_reference_is_reconciled_without_creating_another_order(self):
        self._use_quick_entry()
        event = self._event(self.a)
        payload, _ = event._pancake_payload()
        event.order_id.pancake_export_reference = payload['custom_id']
        row = self._quick_remote(event, payload, status=1)
        row.update(custom_id=None, note=payload['custom_id'])
        with patch.object(type(self.env['dac.pancake.api.log']), '_request',
                return_value={'ok': True, 'data': {'data': [row], 'total_entries': 1}}):
            self.assertEqual(event._pancake_find(), (row, True))

    def test_note_reference_callback_links_original_and_preserves_crm_lines(self):
        self._use_quick_entry()
        event = self._event(self.a)
        payload, _ = event._pancake_payload()
        order = event.order_id
        order.write({'pancake_export_reference': payload['custom_id'], 'pancake_export_shop_id': '111'})
        original_lines = order.order_line.ids
        row = self._quick_remote(event, payload)
        row.update(custom_id=None, note=payload['custom_id'])
        before = self.env['sale.order'].search_count([])
        self.assertEqual(self.env['sale.order'].action_sync_single_pancake_order(row), order)
        self.assertEqual(order.pancake_order_id, '888')
        self.assertEqual(order.order_line.ids, original_lines)
        self.assertEqual(self.env['sale.order'].search_count([]), before)
