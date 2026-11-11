import hashlib
import hmac
import json
import time

from odoo.tests import HttpCase, tagged
from odoo.tests.common import TransactionCase

SECRET = 'unit-test-secret'


def sign(body, ts=None):
    ts = str(ts or int(time.time()))
    digest = hmac.new(SECRET.encode(), ts.encode() + b'.' + body, hashlib.sha256).hexdigest()
    return {'X-DAC-Timestamp': ts, 'X-DAC-Signature': digest, 'Content-Type': 'application/json'}


def envelope(event_id='e1', event_type='web_lead.created', **data):
    data = {'name': 'Anh A', 'phone': '0901 234 567', 'form_type': 'quote', 'items': ['In bạt', 'Standee'],
            'message': 'Cần báo giá gấp', 'site': 'duyan.vn', **data}
    return {'event_id': event_id, 'event_type': event_type, 'source': 'duyan.vn', 'data': data}


@tagged('post_install', '-at_install')
class TestPushEvents(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.events = cls.env['dac.push.event']
        cls.channel = cls.env['dac.zalo.channel'].create({
            'name': 'Nhóm Sale', 'thread_id': 'G-push', 'thread_type': 'group',
            'event_type_ids': [(6, 0, cls.env.ref('dac_push.event_type_web_lead_created').ids)]})

    def test_web_lead_creates_lead_and_queues_zalo_message(self):
        event, created = self.events._receive(envelope())
        self.assertTrue(created)
        self.assertEqual(event.state, 'processed')
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertEqual((lead.phone, lead.items, lead.form_type), ('0901234567', 'In bạt, Standee', 'quote'))
        message = self.channel.message_ids
        self.assertEqual(len(message), 1)
        self.assertEqual(message.state, 'queued')
        self.assertEqual(message.text.splitlines(), [
            'Khách yêu cầu tư vấn', '👤 Anh A', '📞 0901234567', '➕ Yêu cầu: In bạt, Standee', '💬 Cần báo giá gấp',
            'Xem chi tiết: %s' % lead._short_link()])

    def test_duplicate_event_is_idempotent(self):
        first, _ = self.events._receive(envelope('dup'))
        second, created = self.events._receive(envelope('dup'))
        self.assertFalse(created)
        self.assertEqual(first, second)
        self.assertEqual(self.env['dac.web.lead'].search_count([('event_id', '=', first.id)]), 1)
        self.assertEqual(len(self.channel.message_ids), 1)

    def test_lead_keeps_entered_fields_visited_pages_and_source(self):
        visited = [{'url': 'https://duyan.vn/?utm_source=google', 'title': 'Trang chủ', 'at': '2026-11-11 10:00:00'},
                   {'url': 'https://duyan.vn/in-an', 'title': 'In ấn', 'at': '2026-11-11 10:04:00'},
                   {'url': 'https://example.org/ngoai', 'title': '', 'at': '2026-11-11 10:05:00'}]
        event, _ = self.events._receive(envelope(
            'rich1', name='Chị D', message='Cần giao trước thứ 6', page_title='Báo giá', page_url='https://duyan.vn/bao-gia',
            fields=[{'label': 'Dịch vụ cần làm', 'value': 'In bạt'}, {'label': 'Kích thước', 'value': '2x3m'},
                    {'label': 'Trống', 'value': ''}, 'rác', {'label': 'A\x00B', 'value': 'ok'}],
            visited=visited, source={'utm_source': 'google', 'utm_medium': 'cpc', 'utm_campaign': 'in-an'}))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertEqual(lead.extra_info.splitlines(), ['Dịch vụ cần làm: In bạt', 'Kích thước: 2x3m', 'AB: ok'])
        self.assertEqual(lead.visited_urls.splitlines(), ['10:00 — https://duyan.vn/?utm_source=google — Trang chủ', '10:04 — https://duyan.vn/in-an — In ấn', '10:05 — https://example.org/ngoai'])
        self.assertEqual((lead.source_info, lead.page_title, lead.phone_only), ('google / cpc / in-an', 'Báo giá', False))
        # Tin Zalo gọn: chi tiết (trang đã xem, nguồn...) nằm ở trang của lead, không lặp trong tin
        text = self.channel.message_ids.text
        self.assertEqual(text.splitlines(), [
            'Khách yêu cầu tư vấn', '👤 Chị D', '📞 0901234567',
            '➕ Yêu cầu: In bạt, Standee; Dịch vụ cần làm: In bạt; Kích thước: 2x3m; AB: ok',
            '💬 Cần giao trước thứ 6', 'Xem chi tiết: %s' % lead._short_link()])
        for absent in ('utm_', 'duyan.vn/in-an', 'Đã xem', 'Gửi từ'):
            self.assertNotIn(absent, text)

    def test_zalo_message_has_bold_title_and_short_link(self):
        event, _ = self.events._receive(envelope('fmt1'))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        text, styles = lead._zalo_message()
        self.assertEqual(styles, [{'start': 0, 'len': len('Khách yêu cầu tư vấn'), 'style': 'bold'}])
        self.assertEqual(text[:styles[0]['len']], 'Khách yêu cầu tư vấn')
        self.assertEqual(self.channel.message_ids.styles, styles)
        base = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        self.assertTrue(text.endswith('Xem chi tiết: %s/lead/%s' % (base.split('://')[-1], lead.id)))
        self.assertNotIn('http', text.splitlines()[-1], 'link is shown without a scheme to stay short')

    def test_phone_only_form_is_valid_and_reads_cleanly(self):
        event, _ = self.events._receive({'event_id': 'ph1', 'event_type': 'web_lead.created', 'source': 'duyan.vn',
            'data': {'site': 'duyan.vn', 'form_type': 'callback', 'phone': '0955 666 777', 'page_url': 'https://duyan.vn/lien-he',
                     'visited': [{'url': 'https://duyan.vn/lien-he', 'title': 'Liên hệ', 'at': '2026-11-11 10:00:00'}]}})
        self.assertEqual(event.state, 'processed')
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertTrue(lead.phone_only)
        self.assertEqual(self.channel.message_ids.text.splitlines(), ['Khách yêu cầu tư vấn', '📞 0955666777', 'Xem chi tiết: %s' % lead._short_link()])
        self.assertEqual(lead.visited_urls, '10:00 — https://duyan.vn/lien-he — Liên hệ')

    def test_product_url_fields_are_left_out_of_the_request_line(self):
        event, _ = self.events._receive(envelope(
            'prod1', items=[], message='', fields=[{'label': 'Sản phẩm/Danh mục', 'value': 'Standee'},
                                                    {'label': 'Trang sản phẩm', 'value': 'https://duyan.vn/standee'}]))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertIn('Trang sản phẩm: https://duyan.vn/standee', lead.extra_info)
        lines = self.channel.message_ids.text.splitlines()
        self.assertIn('➕ Yêu cầu: Sản phẩm/Danh mục: Standee', lines)
        self.assertFalse([ln for ln in lines if 'duyan.vn/standee' in ln])
        self.assertFalse([ln for ln in lines if ln.startswith('💬')], 'no note line when the customer wrote none')

    def test_full_visited_urls_are_kept_in_the_lead_even_when_long(self):
        long_url = 'https://duyan.vn/?gad_source=2&gad_campaignid=24120075564&gbraid=0AAAAACdEIguj0C' + 'x' * 200
        visited = [{'url': 'https://duyan.vn/p%s/?a=1&b=2#frag' % i, 'title': 'P%s' % i, 'at': '2026-11-11 10:%02d:00' % i} for i in range(14)]
        visited.append({'url': long_url, 'title': 'Dài', 'at': '2026-11-11 10:14:00'})
        event, _ = self.events._receive(envelope('long1', visited=visited, page_url='https://duyan.vn/bao-gia/?x=1&y=2'))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        lines = lead.visited_urls.splitlines()
        self.assertEqual(len(lines), 15)
        self.assertIn('https://duyan.vn/p0/?a=1&b=2#frag', lines[0])  # không cắt tham số / fragment
        self.assertIn(long_url, lines[14])
        self.assertLess(len(self.channel.message_ids.text), 500, 'Zalo message stays compact however long the visit history is')

    def test_junk_in_new_fields_never_breaks_the_event(self):
        event, _ = self.events._receive(envelope('junk1', fields='not-a-list', visited={'a': 1}, source='x'))
        self.assertEqual(event.state, 'processed')
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertFalse(lead.extra_info or lead.visited_urls or lead.source_info)

    def test_endpoint_url_is_https_for_public_hosts_only(self):
        icp = self.env['ir.config_parameter'].sudo()
        config = self.env['dac.push.config']._config()
        for base, expected in (('http://crm.example.vn', 'https://crm.example.vn'), ('https://crm.example.vn/', 'https://crm.example.vn'),
                               ('http://localhost:8069', 'http://localhost:8069')):
            icp.set_param('web.base.url', base)
            config.invalidate_recordset(['endpoint_url'])
            self.assertEqual(config.endpoint_url, expected + '/dac_push/v1/events')

    def test_system_ping_is_processed_without_side_effects(self):
        event, _ = self.events._receive(envelope('ping1', 'system.ping'))
        self.assertEqual(event.state, 'processed')
        self.assertFalse(self.channel.message_ids)
        self.assertFalse(self.env['dac.web.lead'].search([('event_id', '=', event.id)]))

    def test_unknown_event_type_is_kept_but_ignored(self):
        event, _ = self.events._receive(envelope('u1', 'woo.order_paid'))
        self.assertEqual(event.state, 'ignored')
        self.assertIn('_handle_woo_order_paid', event.error_message)

    def test_future_event_types_plug_in_with_a_handler_method(self):
        handled = []
        type(self.events)._handle_unit_probe = lambda rec: handled.append(rec.payload)
        self.addCleanup(lambda: delattr(type(self.events), '_handle_unit_probe'))
        event, _ = self.events._receive(envelope('p1', 'unit.probe', note='x'))
        self.assertEqual(event.state, 'processed')
        self.assertEqual(handled[0]['note'], 'x')

    def test_handler_failure_is_recorded_and_can_be_reprocessed(self):
        calls = {'n': 0}

        def flaky(rec):
            calls['n'] += 1
            if calls['n'] == 1:
                raise ValueError('boom')
        type(self.events)._handle_unit_flaky = flaky
        self.addCleanup(lambda: delattr(type(self.events), '_handle_unit_flaky'))
        event, _ = self.events._receive(envelope('f1', 'unit.flaky'))
        self.assertEqual((event.state, event.error_message), ('failed', 'boom'))
        event.action_reprocess()
        self.assertEqual(event.state, 'processed')

    def test_hostile_text_is_cleaned_and_truncated(self):
        event, _ = self.events._receive(envelope('h1', name='A\x00\x07B' + 'x' * 500, message='m' * 5000))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        self.assertNotIn('\x00', lead.customer_name)
        self.assertEqual((len(lead.customer_name), len(lead.message)), (200, 2000))


@tagged('post_install', '-at_install')
class TestPushController(HttpCase):
    def setUp(self):
        super().setUp()
        self.config = self.env['dac.push.config']._config()
        self.config.sudo().write({'active': True, 'secret': SECRET})

    def post(self, body, headers):
        return self.url_open('/dac_push/v1/events', data=body, headers=headers)

    def test_signed_request_is_accepted_once(self):
        body = json.dumps(envelope('c1')).encode()
        first = self.post(body, sign(body))
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {'ok': True, 'duplicate': False, 'state': 'processed'})
        self.assertTrue(self.post(body, sign(body)).json()['duplicate'])

    def test_bad_signature_and_stale_timestamp_are_rejected(self):
        body = json.dumps(envelope('c2')).encode()
        tampered = sign(body)
        tampered['X-DAC-Signature'] = '0' * 64
        self.assertEqual(self.post(body, tampered).status_code, 401)
        self.assertEqual(self.post(body, sign(body, ts=int(time.time()) - 1000)).status_code, 401)
        self.assertEqual(self.post(body, {'Content-Type': 'application/json'}).status_code, 401)
        self.assertFalse(self.env['dac.push.event'].search([('event_id', '=', 'c2')]))

    def test_closed_when_disabled_or_without_secret(self):
        body = json.dumps(envelope('c3')).encode()
        self.config.sudo().active = False
        self.assertEqual(self.post(body, sign(body)).status_code, 503)
        self.config.sudo().write({'active': True, 'secret': False})
        self.assertEqual(self.post(body, sign(body)).status_code, 503)

    def test_malformed_bodies_are_400(self):
        for raw in (b'not json', b'[1,2]', json.dumps({'event_type': 'x'}).encode()):
            self.assertEqual(self.post(raw, sign(raw)).status_code, 400)

    def test_short_lead_link_redirects_to_the_lead_form_after_login(self):
        body = json.dumps(envelope('link1')).encode()
        self.post(body, sign(body))
        lead = self.env['dac.web.lead'].search([], order='id desc', limit=1)
        anonymous = self.url_open('/lead/%d' % lead.id, allow_redirects=False)
        self.assertEqual(anonymous.status_code, 303)
        self.assertIn('/web/login', anonymous.headers['Location'], 'anonymous visitors are sent to the login page, never shown the lead')
        self.authenticate('admin', 'admin')
        response = self.url_open('/lead/%d' % lead.id, allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(response.headers['Location'].split('?')[0].rsplit('/odoo/', 1)[-1], 'action-dac_push.web_lead_action/%d' % lead.id)
