import hashlib
from datetime import timedelta, timezone
from unittest.mock import Mock, patch

import requests

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase, new_test_user

MODULE = 'odoo.addons.dac_meta_conversion.models.meta_conversion_event'


@tagged('post_install', '-at_install')
class TestMetaConversion(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.company = cls.env.company
        cls.company.write({
            'meta_conversion_provider': 'meta_direct',
            'meta_conversion_enabled': False,
            'meta_conversion_dataset_id': '123456789',
            'meta_conversion_page_id': '987654321',
            'meta_conversion_access_token': 'test-secret-never-send',
            'meta_conversion_api_version': 'v26.0',
            'meta_conversion_channel': 'messenger',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Meta test customer', 'phone': '0909333444',
            'email': '  BUYER@example.com  ', 'country_id': cls.env.ref('base.vn').id,
        })
        cls.product = cls.env['product.product'].create({'name': 'Meta test product', 'list_price': 100000})
        cls.page = cls.env['page.fm.page'].create({'name': 'Meta test page', 'page_fm_id_str': '987654321'})
        cls.conversation = cls.env['page.fm.conversation'].create({
            'conversation_fm_id': 'meta-test-inbox', 'page_fm_page_id': cls.page.id,
            'partner_id': cls.partner.id, 'sender_fm_id': '112233445566',
            'conversation_type_fm': 'INBOX', 'platform_fm': 'Facebook',
        })
        cls.Event = cls.env['dac.meta.conversion.event']

    def setUp(self):
        super().setUp()
        self.schedule = self.enterContext(patch.object(type(self.Event), '_schedule_dispatch'))

    def _order(self, **values):
        vals = {
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {'product_id': self.product.id, 'product_uom_qty': 2, 'price_unit': 100000})],
        }
        vals.update(values)
        return self.env['sale.order'].create(vals)

    def _event(self, **values):
        self.company.meta_conversion_enabled = True
        return self._order(**values).meta_conversion_event_ids

    def _response(self, status, body):
        return Mock(status_code=status, ok=200 <= status < 400, json=Mock(return_value=body))

    def test_disabled_does_not_enqueue_or_backfill(self):
        order = self._order()
        self.assertFalse(order.meta_conversion_event_ids)
        self.company.meta_conversion_enabled = True
        order.write({'note': 'Edited after enabling'})
        self.assertFalse(order.meta_conversion_event_ids)

    def test_new_order_enqueues_once_and_edit_does_not_duplicate(self):
        event = self._event(conversation_id=self.conversation.id)
        self.assertEqual(len(event), 1)
        event.order_id.write({'note': 'Another save'})
        self.assertEqual(len(event.order_id.meta_conversion_event_ids), 1)
        self.assertTrue(self.schedule.called)

    def test_messenger_payload_uses_psid_and_actual_total(self):
        event = self._event(conversation_id=self.conversation.id)
        payload, reason = event._prepare_payload()
        self.assertFalse(reason)
        data = payload['data'][0]
        self.assertEqual(data['event_name'], 'Purchase')
        self.assertEqual(data['action_source'], 'business_messaging')
        self.assertEqual(data['messaging_channel'], 'messenger')
        self.assertEqual(data['user_data']['page_scoped_user_id'], '112233445566')
        self.assertEqual(data['user_data']['page_id'], '987654321')
        self.assertEqual(data['custom_data']['value'], event.order_id.amount_total)
        self.assertEqual(data['user_data']['ph'], [hashlib.sha256(b'84909333444').hexdigest()])
        self.assertEqual(data['user_data']['em'], [hashlib.sha256(b'buyer@example.com').hexdigest()])
        self.assertEqual(data['event_time'], int(event.event_time.replace(tzinfo=timezone.utc).timestamp()))

    def test_unambiguous_partner_conversation_is_selected(self):
        event = self._event()
        self.assertFalse(event._prepare_payload()[1])

    def test_ambiguous_conversation_requires_explicit_link(self):
        self.conversation.copy({'conversation_fm_id': 'meta-test-second', 'sender_fm_id': '99887766',
                                'conversation_type_fm': 'INBOX'})
        event = self._event()
        self.assertTrue(event._prepare_payload()[1])
        event.order_id.conversation_id = self.conversation
        self.assertFalse(event._prepare_payload()[1])

    def test_comment_zalo_wrong_page_and_customer_are_not_messenger(self):
        event = self._event(conversation_id=self.conversation.id)
        for vals in ({'conversation_type_fm': 'COMMENT'}, {'platform_fm': 'Zalo'},
                     {'conv_page_fm_id': '111111'}, {'sender_fm_id': 'pancake-customer-uuid'}):
            with self.subTest(vals=vals), self.cr.savepoint():
                original = {key: self.conversation[key] for key in vals}
                self.conversation.write(vals)
                self.assertTrue(event._prepare_payload()[1])
                self.conversation.write(original)

    def test_manual_psid_works_without_pancake(self):
        self.conversation.partner_id = False
        event = self._event(meta_conversion_psid='44556677')
        payload, reason = event._prepare_payload()
        self.assertFalse(reason)
        self.assertEqual(payload['data'][0]['user_data']['page_scoped_user_id'], '44556677')

    def test_chat_uses_hashed_contact_and_no_messenger_fields(self):
        self.company.meta_conversion_channel = 'chat'
        event = self._event()
        payload, reason = event._prepare_payload()
        self.assertFalse(reason)
        self.assertEqual(payload['data'][0]['action_source'], 'chat')
        self.assertNotIn('messaging_channel', payload['data'][0])
        self.assertNotIn('page_scoped_user_id', payload['data'][0]['user_data'])

    def test_missing_contact_or_zero_total_waits(self):
        self.company.meta_conversion_channel = 'chat'
        self.partner.write({'phone': False, 'mobile': False, 'email': False})
        event = self._event()
        self.assertTrue(event._prepare_payload()[1])
        self.partner.phone = '0909333444'
        event.order_id.order_line.write({'price_unit': 0})
        self.assertTrue(event._prepare_payload()[1])

    def test_test_code_and_accepted_state(self):
        self.company.meta_conversion_test_code = 'TEST42'
        event = self._event()
        event.payload = event._prepare_payload()[0]
        self.assertEqual(event.payload['test_event_code'], 'TEST42')
        with patch(MODULE + '.requests.post', return_value=self._response(200, {'events_received': 1, 'fbtrace_id': 'trace'})) as post:
            result = event._send_payload(self.company)
        self.assertEqual(result['state'], 'test_sent')
        self.assertNotIn('test-secret', post.call_args.args[0])
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_success_and_definite_rejection_do_not_expose_token(self):
        event = self._event()
        with patch(MODULE + '.requests.post', return_value=self._response(200, {'events_received': 1})):
            self.assertEqual(event._send_payload(self.company)['state'], 'sent')
        with patch(MODULE + '.requests.post', return_value=self._response(400, {'error': {'code': 190, 'message': 'test-secret-never-send'}})):
            result = event._send_payload(self.company)
        self.assertEqual(result['state'], 'error')
        self.assertNotIn('test-secret', result['message'])

    def test_ambiguous_http_and_network_outcomes_are_not_automatically_retried(self):
        event = self._event()
        for error in (requests.ReadTimeout(), requests.ConnectionError()):
            with patch(MODULE + '.requests.post', side_effect=error):
                self.assertEqual(event._send_payload(self.company)['state'], 'uncertain')
        for status, body in ((500, {}), (200, {}), (200, {'events_received': 0})):
            with patch(MODULE + '.requests.post', return_value=self._response(status, body)):
                self.assertEqual(event._send_payload(self.company)['state'], 'uncertain')

    def test_rate_limit_and_connect_timeout_have_bounded_retry(self):
        event = self._event()
        event.attempt_count = 1
        with patch(MODULE + '.requests.post', return_value=self._response(429, {})):
            self.assertEqual(event._send_payload(self.company)['state'], 'retry')
        with patch(MODULE + '.requests.post', side_effect=requests.ConnectTimeout()):
            self.assertEqual(event._send_payload(self.company)['state'], 'retry')
        event.attempt_count = 5
        with patch(MODULE + '.requests.post', side_effect=requests.ConnectTimeout()):
            self.assertEqual(event._send_payload(self.company)['state'], 'error')

    def test_deliver_claims_once_and_does_not_resend(self):
        event = self._event(conversation_id=self.conversation.id)
        with patch.object(self.cr, 'commit'), patch(MODULE + '.requests.post', return_value=self._response(200, {'events_received': 1})) as post:
            event._deliver()
            event._deliver()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(event.state, 'sent')
        with self.assertRaises(UserError):
            event.order_id.action_meta_retry()

    def test_cancelled_old_and_changed_dataset_never_send(self):
        for mode in ('cancel', 'old', 'dataset'):
            event = self._event(conversation_id=self.conversation.id)
            if mode == 'cancel':
                event.order_id.order_state_custom = 'cancel'
            elif mode == 'old':
                event.event_time = fields.Datetime.now() - timedelta(days=8)
            else:
                event.dataset_id = '555555'
            with patch(MODULE + '.requests.post') as post:
                event._deliver()
            post.assert_not_called()
            self.assertIn(event.state, ('cancelled', 'error'))

    def test_configuration_requires_real_key_shapes(self):
        self.company.meta_conversion_enabled = True
        self.company.meta_conversion_dataset_id = 'not-an-id'
        with self.assertRaises(ValidationError):
            self.company._check_meta_configuration()

    def test_waiting_event_resumes_when_identity_is_added(self):
        event = self._event()
        event.write({'state': 'waiting', 'next_attempt_at': fields.Datetime.now() + timedelta(minutes=5)})
        event.order_id.write({'meta_conversion_psid': '44556677'})
        self.assertEqual(event.state, 'pending')
        self.assertLessEqual(event.next_attempt_at, fields.Datetime.now())

    def test_rejected_event_can_retry_but_uncertain_event_cannot(self):
        event = self._event()
        event.state = 'error'
        event.order_id.action_meta_retry()
        self.assertEqual(event.state, 'pending')
        event.state = 'uncertain'
        with self.assertRaises(UserError):
            event.order_id.action_meta_retry()

    def test_invalid_mobile_falls_back_to_phone(self):
        self.partner.mobile = 'not a number'
        event = self._event()
        self.assertEqual(event._contact_identity()['ph'], [hashlib.sha256(b'84909333444').hexdigest()])

    def test_pancake_metadata_preserves_psid_separately_from_customer_uuid(self):
        response = self._response(200, {'success': True, 'conversations': [{
            'id': 'meta-sync-inbox', 'page_id': '987654321', 'type': 'INBOX',
            'customer_id': 'pancake-customer-uuid',
            'from': {'id': '99881122', 'name': 'Sync test'},
            'updated_at': '2026-11-02T01:00:00Z',
        }]})
        empty = self._response(200, {'success': True, 'conversations': []})
        with patch.object(type(self.page), '_generate_page_specific_access_token', return_value='fake-page-token'), \
                patch('odoo.addons.CRM_DAC.models.page_fm_models.requests.get', side_effect=[response, empty]):
            values = self.page._fetch_conversations_for_page_record('fake-main-token', raise_on_error=True)
        self.assertEqual(values[0]['sender_fm_id'], '99881122')
        self.assertEqual(values[0]['customer_fm_id'], 'pancake-customer-uuid')
        self.assertEqual(values[0]['conversation_type_fm'], 'INBOX')

    def test_settings_accept_credentials_together_and_reject_invalid_save(self):
        settings = self.env['res.config.settings'].create({
            'company_id': self.company.id, 'meta_conversion_enabled': True,
            'meta_conversion_dataset_id': '123456789', 'meta_conversion_page_id': '987654321',
            'meta_conversion_access_token': 'test-settings-token',
            'meta_conversion_api_version': 'v26.0', 'meta_conversion_channel': 'messenger',
        })
        settings.set_values()
        self.assertEqual(self.company.meta_conversion_access_token, 'test-settings-token')
        settings.meta_conversion_dataset_id = 'invalid'
        with self.assertRaises(ValidationError):
            settings.set_values()

    def test_sales_user_can_see_status_but_not_secrets_or_queue_payload(self):
        user = new_test_user(self.env, login='meta_conversion_sales', groups='base.group_user,dac_erp.group_dac_erp_sale')
        event = self._event(conversation_id=self.conversation.id)
        order = event.order_id
        order.user_id = user
        self.assertEqual(order.with_user(user).meta_conversion_state, 'pending')
        with self.assertRaises(AccessError):
            self.company.with_user(user).read(['meta_conversion_access_token'])
        with self.assertRaises(AccessError):
            event.with_user(user).read(['payload'])
