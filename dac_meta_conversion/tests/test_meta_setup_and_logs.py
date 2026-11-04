import json
from datetime import timedelta
from unittest.mock import Mock, patch

import requests
from lxml import etree

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

API = 'odoo.addons.dac_meta_conversion.models.meta_api_log.requests'
EVENT_MODULE = 'odoo.addons.dac_meta_conversion.models.meta_conversion_event'


@tagged('post_install', '-at_install')
class TestMetaSetupAndLogs(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True, no_reset_password=True))
        cls.company = cls.env.company
        cls.company.write({
            'meta_conversion_app_id': False, 'meta_conversion_setup_path': 'new_app',
            'meta_conversion_enabled': False, 'meta_conversion_channel': 'messenger',
            'meta_conversion_dataset_id': '123456789', 'meta_conversion_page_id': '987654321',
            'meta_conversion_api_version': 'v26.0', 'meta_conversion_access_token': 'private-test-token',
            'meta_conversion_checked_at': False, 'meta_conversion_check_status': 'unchecked',
            'meta_conversion_check_fingerprint': False,
        })
        cls.partner = cls.env['res.partner'].create({'name': 'Audit test customer', 'phone': '0909333444'})
        cls.product = cls.env['product.product'].create({'name': 'Audit test product', 'list_price': 200000})
        cls.Log = cls.env['dac.meta.api.log']

    def _response(self, status, data):
        return Mock(status_code=status, json=Mock(return_value=data))

    def _settings(self):
        return self.env['res.config.settings'].create({'company_id': self.company.id})

    def _event(self):
        self.company.meta_conversion_enabled = True
        with patch(EVENT_MODULE + '.MetaConversionEvent._schedule_dispatch'):
            order = self.env['sale.order'].create({
                'partner_id': self.partner.id, 'meta_conversion_psid': '112233445566',
                'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 1, 'price_unit': 200000})],
            })
        event = order.meta_conversion_event_ids
        event.payload = event._prepare_payload()[0]
        event.attempt_count = 1
        return event

    def test_check_works_while_sending_is_disabled_and_never_posts(self):
        settings = self._settings()
        responses = [self._response(200, {'id': '123456789'}), self._response(200, {'data': [{'id': '123456789'}]})]
        with patch(API + '.get', side_effect=responses) as get, patch(API + '.post') as post:
            result = settings.action_meta_check_connection()
        self.assertEqual(result['params']['type'], 'success')
        self.assertEqual(settings.meta_conversion_check_status, 'ok')
        self.assertFalse(self.company.meta_conversion_enabled)
        self.assertEqual(get.call_count, 2)
        post.assert_not_called()
        self.assertEqual(len(self.Log.search([('company_id', '=', self.company.id)])), 2)

    def test_invalid_format_has_no_api_call_or_fake_api_log(self):
        self.company.meta_conversion_dataset_id = 'invalid'
        with patch(API + '.get') as get:
            result = self._settings().action_meta_check_connection()
        self.assertEqual(result['params']['type'], 'danger')
        self.assertEqual(self.company.meta_conversion_check_status, 'error')
        get.assert_not_called()
        self.assertFalse(self.Log.search([('company_id', '=', self.company.id)]))

    def test_invalid_token_keeps_rejection_log_without_disclosing_token(self):
        response = self._response(400, {'error': {'code': 190, 'error_subcode': 463,
                                              'message': 'Expired private-test-token', 'fbtrace_id': 'trace-expired'}})
        with patch(API + '.get', return_value=response):
            result = self._settings().action_meta_check_connection()
        self.assertEqual(result['params']['type'], 'danger')
        log = self.Log.search([('company_id', '=', self.company.id)], limit=1)
        self.assertEqual(log.state, 'rejected')
        self.assertEqual(log.error_code, '190')
        self.assertEqual(log.trace_id, 'trace-expired')
        self.assertNotIn('private-test-token', log.response_preview)
        self.assertNotIn('private-test-token', self.company.meta_conversion_check_message)

    def test_dataset_page_mismatch_is_reported(self):
        with patch(API + '.get', side_effect=[self._response(200, {'id': '123456789'}),
                                             self._response(200, {'id': '55555555'})]):
            result = self._settings().action_meta_check_connection()
        self.assertEqual(result['params']['type'], 'danger')
        self.assertEqual(self.company.meta_conversion_check_status, 'error')

    def test_page_permission_failure_is_partial_not_false_success(self):
        with patch(API + '.get', side_effect=[self._response(200, {'id': '123456789'}),
                                             self._response(403, {'error': {'code': 200, 'message': 'Permission required'}})]):
            result = self._settings().action_meta_check_connection()
        self.assertEqual(result['params']['type'], 'warning')
        self.assertEqual(self.company.meta_conversion_check_status, 'partial')
        self.assertIn('page_events', self.company.meta_conversion_check_message)

    def test_changed_credentials_make_previous_check_stale(self):
        self.company.meta_conversion_channel = 'chat'
        settings = self._settings()
        with patch(API + '.get', return_value=self._response(200, {'id': '123456789'})):
            settings.action_meta_check_connection()
        self.assertEqual(settings.meta_conversion_check_status, 'ok')
        settings.meta_conversion_access_token = 'changed-private-token'
        self.assertEqual(settings.meta_conversion_check_status, 'stale')

    def test_connection_network_error_is_persisted(self):
        with patch(API + '.get', side_effect=requests.ReadTimeout('private-test-token')):
            result = self._settings().action_meta_check_connection()
        self.assertEqual(result['params']['type'], 'danger')
        log = self.Log.search([('company_id', '=', self.company.id)], limit=1)
        self.assertEqual(log.state, 'network_error')
        self.assertNotIn('private-test-token', log.summary)

    def test_each_send_attempt_has_its_own_log(self):
        event = self._event()
        with patch(API + '.post', return_value=self._response(429, {'error': {'code': 4}})):
            self.assertEqual(event._send_payload(self.company)['state'], 'retry')
        event.attempt_count = 2
        with patch(API + '.post', return_value=self._response(200, {'events_received': 1, 'fbtrace_id': 'accepted-trace'})):
            self.assertEqual(event._send_payload(self.company)['state'], 'sent')
        logs = event.api_log_ids.sorted('id')
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs.mapped('attempt_number'), [1, 2])
        self.assertEqual(logs.mapped('state'), ['rejected', 'success'])
        self.assertEqual(logs[-1].trace_id, 'accepted-trace')
        self.assertEqual(logs[-1].events_received, 1)
        self.assertTrue(all(log.completed_at for log in logs))

    def test_payload_and_echoed_credentials_are_masked(self):
        event = self._event()
        response = {'error': {'code': 100, 'message': 'private-test-token user 112233445566'}}
        with patch(API + '.post', return_value=self._response(400, response)):
            event._send_payload(self.company)
        log = event.api_log_ids
        text = json.dumps([log.request_payload, log.response_payload, log.summary])
        self.assertNotIn('private-test-token', text)
        self.assertNotIn('112233445566', text)
        self.assertEqual(log.request_payload['data'][0]['custom_data']['value'], event.order_id.amount_total)
        self.assertEqual(log.request_payload['data'][0]['event_id'], event.event_id)
        self.assertEqual(len(log.request_digest), 64)

    def test_timeout_is_an_uncertain_send_log(self):
        event = self._event()
        with patch(API + '.post', side_effect=requests.ReadTimeout('secret detail')):
            self.assertEqual(event._send_payload(self.company)['state'], 'uncertain')
        self.assertEqual(event.api_log_ids.state, 'uncertain')
        self.assertEqual(event.api_log_ids.http_status, 0)
        self.assertNotIn('secret detail', event.api_log_ids.summary)

    def test_stale_send_reconciles_started_api_log_without_resending(self):
        event = self._event()
        event.write({'state': 'sending', 'last_attempt_at': fields.Datetime.now() - timedelta(minutes=20)})
        log = self.Log._start_call(self.company, 'purchase', 'POST', event.dataset_id + '/events', event.payload, event)
        with patch(EVENT_MODULE + '.dispatch_committed_events'):
            event._cron_dispatch()
        self.assertEqual(event.state, 'uncertain')
        self.assertEqual(log.state, 'uncertain')

    def test_managers_read_logs_but_cannot_edit_delete_or_see_payload(self):
        manager = new_test_user(self.env, login='meta_log_manager', groups='base.group_user,dac_erp.group_dac_erp_manager')
        event = self._event()
        log = self.Log._start_call(self.company, 'purchase', 'POST', event.dataset_id + '/events', event.payload, event)
        self.assertEqual(log.with_user(manager).read(['operation'])[0]['operation'], 'purchase')
        for operation in (lambda: log.with_user(manager).write({'summary': 'changed'}),
                          lambda: log.with_user(manager).unlink(),
                          lambda: log.with_user(manager).read(['request_payload'])):
            with self.assertRaises(AccessError):
                operation()

    def test_guided_settings_links_history_and_menu_views_compile(self):
        view = self.env['res.config.settings'].get_view(view_id=self.env.ref('base_setup.res_config_settings_view_form').id)
        root = etree.fromstring(view['arch'])
        app = root.xpath('//app[@name="dac_meta_conversion"]')[0]
        self.assertEqual(len(app.xpath('./block')), 8)
        self.assertTrue(app.xpath('.//div[contains(@class, "dac-meta-hero")]'))
        self.assertEqual(len(app.xpath('.//div[contains(@class, "dac-meta-stop")]')), 8)
        links = app.xpath('.//a/@href')
        self.assertGreaterEqual(len(links), 7)
        self.assertTrue(all(link.startswith('https://') for link in links))
        self.assertTrue(app.xpath('.//button[@name="action_meta_check_connection"]'))
        self.assertTrue(app.xpath('.//button[@name="action_meta_open_api_logs"]'))
        for xmlid in ('meta_api_log_form', 'meta_api_log_list', 'meta_api_log_search'):
            view = self.env.ref('dac_meta_conversion.' + xmlid)
            self.Log.get_view(view_id=view.id, view_type=view.type)
        self.assertTrue(self.env.ref('dac_meta_conversion.menu_meta_conversion_root').exists())

    def test_app_dashboard_link_uses_only_validated_app_id(self):
        settings = self._settings()
        with self.assertRaises(UserError):
            settings.action_meta_open_app_dashboard()
        settings.meta_conversion_app_id = '123456789012345'
        result = settings.action_meta_open_app_dashboard()
        self.assertEqual(result['url'], 'https://developers.facebook.com/apps/123456789012345/')
        self.assertEqual(result['target'], 'new')
        settings.meta_conversion_app_id = '12345/../../?token=anything'
        with self.assertRaises(UserError):
            settings.action_meta_open_app_dashboard()

    def test_app_id_is_optional_but_validated_when_supplied(self):
        self.company._check_meta_configuration(force=True)
        self.company.meta_conversion_app_id = 'https://developers.facebook.com/apps/12345/'
        with self.assertRaises(ValidationError):
            self.company._check_meta_configuration(force=True)

    def test_dataset_setup_path_is_derived_only_from_numeric_page_id(self):
        settings = self._settings()
        self.assertEqual(settings.meta_conversion_dataset_request, '/987654321/dataset')
        settings.meta_conversion_page_id = 'invalid/path'
        self.assertEqual(settings.meta_conversion_dataset_request, '/{PAGE_ID}/dataset')

    def test_changing_app_id_marks_connection_check_stale(self):
        self.company.meta_conversion_channel = 'chat'
        settings = self._settings()
        with patch(API + '.get', return_value=self._response(200, {'id': '123456789'})):
            settings.action_meta_check_connection()
        self.assertEqual(settings.meta_conversion_check_status, 'ok')
        settings.meta_conversion_app_id = '123456789012345'
        self.assertEqual(settings.meta_conversion_check_status, 'stale')
