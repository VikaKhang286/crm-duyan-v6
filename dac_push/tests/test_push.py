import hashlib
import hmac
import json
import time
from datetime import timedelta

from odoo import fields
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

    def test_lead_message_tags_the_people_configured_for_new_leads(self):
        event_type = self.env.ref('dac_push.event_type_web_lead_created')
        member = self.env['dac.zalo.channel.member'].create([
            {'channel_id': self.channel.id, 'uid': '111', 'name': 'An'}, {'channel_id': self.channel.id, 'uid': '222', 'name': 'Bình'}])
        self.env['dac.zalo.channel.mention'].create({'channel_id': self.channel.id, 'event_type_id': event_type.id, 'member_ids': [(6, 0, member.ids)]})
        self.events._receive(envelope('tag1'))
        message = self.channel.message_ids
        self.assertTrue(message.text.endswith('\n@An @Bình'))
        self.assertEqual([m['uid'] for m in message.mentions], ['111', '222'])
        self.assertEqual(message.styles, [{'start': 0, 'len': len('Khách yêu cầu tư vấn'), 'style': 'bold'}], 'the bold title is unchanged')

    def _lead_with_sent_message(self, event_id='rem1', mention=True):
        if mention:
            event_type = self.env.ref('dac_push.event_type_web_lead_created')
            members = self.env['dac.zalo.channel.member'].create([
                {'channel_id': self.channel.id, 'uid': '111', 'name': 'An'}, {'channel_id': self.channel.id, 'uid': '222', 'name': 'Bình'}])
            self.env['dac.zalo.channel.mention'].create({'channel_id': self.channel.id, 'event_type_id': event_type.id, 'member_ids': [(6, 0, members.ids)]})
        event, _ = self.events._receive(envelope(event_id))
        lead = self.env['dac.web.lead'].search([('event_id', '=', event.id)])
        lead.zalo_message_ids.write({'state': 'sent', 'external_message_id': 'zm-original'})
        return lead

    def test_new_lead_gets_a_working_hours_deadline_and_remembers_its_zalo_messages(self):
        lead = self._lead_with_sent_message('rem0', mention=False)
        self.assertIn(self.channel.message_ids, lead.zalo_message_ids, 'every channel that received the lead is remembered')
        self.assertTrue(lead.remind_due_at and lead.remind_due_at > lead.received_at)
        self.assertFalse(lead.reminded_at)

    def test_overdue_lead_is_reminded_once_as_a_reply_that_tags_the_people_for_leads(self):
        lead = self._lead_with_sent_message('rem1')
        original = lead.zalo_message_ids.filtered(lambda m: m.channel_id == self.channel)  # DB thử còn nhóm khác cùng nhận lead
        leads = self.env['dac.web.lead']
        leads._cron_remind_uncontacted()
        self.assertEqual(len(self.channel.message_ids), 1, 'not due yet: nothing is sent')
        lead.remind_due_at = fields.Datetime.now() - timedelta(minutes=1)
        leads._cron_remind_uncontacted()
        reminder = self.channel.message_ids - original
        self.assertEqual(len(reminder), 1)
        self.assertEqual(reminder.text, '@An @Bình Bạn đã liên hệ khách chưa ạ')
        self.assertEqual(reminder.mentions, [{'pos': 0, 'len': 3, 'uid': '111'}, {'pos': 4, 'len': 5, 'uid': '222'}])
        self.assertEqual(reminder.reply_to_id, original, 'the reminder replies to the original lead message')
        self.assertTrue(lead.reminded_at)
        leads._cron_remind_uncontacted()
        self.assertEqual(len(self.channel.message_ids), 2, 'one reminder only')
        from unittest.mock import patch
        config = self.env['dac.zalo.config']._config()
        config.write({'active': True, 'assistant_uid': 'bot'})
        with patch.object(type(config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'zm2', 'quoted': True}) as bridge:
            reminder._send(config)
        payload = bridge.call_args.args[1]
        self.assertEqual(payload['quote_message_id'], 'zm-original')
        self.assertEqual(payload['mentions'], reminder.mentions)
        self.assertEqual(reminder.state, 'sent')

    def test_no_reminder_once_someone_decided_or_when_there_is_no_deadline(self):
        decided = self._lead_with_sent_message('rem2', mention=False)
        decided.remind_due_at = fields.Datetime.now() - timedelta(minutes=5)
        decided._set_contact_state('unreachable')
        old = self._lead_with_sent_message('rem3', mention=False)
        old.remind_due_at = False  # lead tạo trước khi có chức năng nhắc
        self.env['dac.web.lead']._cron_remind_uncontacted()
        self.assertFalse(decided.reminded_at or old.reminded_at)
        self.assertEqual(len(self.channel.message_ids), 2, 'only the two original lead messages exist')
        self.assertFalse(self.env['dac.zalo.channel.message'].search([('reply_to_id', '!=', False)]), 'no reply anywhere')

    def test_reminder_waits_for_an_unsent_original_and_works_without_tagged_people(self):
        lead = self._lead_with_sent_message('rem4', mention=False)
        lead.zalo_message_ids.state = 'queued'
        lead.remind_due_at = fields.Datetime.now() - timedelta(minutes=1)
        self.env['dac.web.lead']._cron_remind_uncontacted()
        self.assertFalse(lead.reminded_at, 'the original message has not left yet: nothing to reply to, try again later')
        lead.zalo_message_ids.state = 'sent'
        self.env['dac.web.lead']._cron_remind_uncontacted()
        original = lead.zalo_message_ids.filtered(lambda m: m.channel_id == self.channel)
        reminder = self.channel.message_ids - original
        self.assertEqual((reminder.text, reminder.mentions, reminder.reply_to_id), ('Bạn đã liên hệ khách chưa ạ', False, original))

    def test_working_time_skips_lunch_nights_and_sundays(self):
        from datetime import datetime, timezone
        from ..models.working_time import TZ, add_working_minutes

        def due(start):
            utc = datetime.strptime(start, '%Y-%m-%d %H:%M').replace(tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)
            return add_working_minutes(utc, 30).replace(tzinfo=timezone.utc).astimezone(TZ).strftime('%a %H:%M')

        self.assertEqual(due('2026-11-12 09:00'), 'Thu 09:30')
        self.assertEqual(due('2026-11-12 11:50'), 'Thu 13:50', '10 minutes in the morning + 20 after lunch')
        self.assertEqual(due('2026-11-12 12:10'), 'Thu 14:00', 'lunch break is not counted')
        self.assertEqual(due('2026-11-12 17:20'), 'Fri 08:20')
        self.assertEqual(due('2026-11-12 20:00'), 'Fri 08:30', 'evening leads count from the next morning')
        self.assertEqual(due('2026-11-14 17:45'), 'Mon 08:30', 'Saturday is a working day, Sunday is not')
        self.assertEqual(due('2026-11-15 10:00'), 'Mon 08:30')

    def test_push_config_lists_lead_recipients_and_opens_the_picker_with_the_lead_event(self):
        config = self.env['dac.push.config']._config()
        self.assertIn(self.channel, config.lead_channel_ids)
        self.assertEqual(config.lead_channel_count, len(config.lead_channel_ids))
        self.channel.active = False
        self.assertIn(self.channel, config.lead_channel_ids, 'paused recipients are still listed')
        from unittest.mock import patch
        with patch.object(type(self.env['dac.zalo.config']), '_bridge_request', return_value={'threads': []}):
            action = config.action_add_lead_channel()
        wizard = self.env[action['res_model']].browse(action['res_id'])
        self.assertEqual(wizard.event_codes, 'web_lead.created')
        self.assertEqual(config.action_open_lead_channels()['domain'], [('event_type_ids.code', '=', 'web_lead.created')])

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
        self.assertTrue(text.endswith('Xem chi tiết: %s/lead/0901234567-%s' % (base.split('://')[-1], lead.sudo().access_token)),
                        'link = số điện thoại của lead + mã ngẫu nhiên')
        self.assertNotIn('/lead/%s' % lead.id, text, 'link does not use the guessable sequential id')
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


    def _lead_with_token(self, event_id):
        body = json.dumps(envelope(event_id, name='Chị Hoa', phone='0933939856', message='Gọi giúp em <b>gấp</b>')).encode()
        self.post(body, sign(body))
        return self.env['dac.web.lead'].search([], order='id desc', limit=1)

    def test_public_link_shows_only_the_lead_without_login(self):
        lead = self._lead_with_token('pub1')
        token = lead.sudo().access_token
        self.assertTrue(token and not token.isdigit())
        page = self.url_open('/lead/%s' % token)
        self.assertEqual(page.status_code, 200)
        html = page.text
        self.assertTrue(html.startswith('<!DOCTYPE html>'))
        self.assertEqual(html.count('DOCTYPE'), 1)
        self.assertIn('0933939856', html)
        self.assertIn('Chị Hoa', html)
        self.assertIn('Đã liên hệ', html)
        self.assertIn('Liên hệ không được', html)
        self.assertNotIn('disabled="disabled"', html, 'undecided leads keep both buttons active')
        self.assertNotIn('<b>gấp</b>', html, 'customer text is escaped')
        self.assertNotIn('/web/assets', html, 'standalone page, no Odoo backend assets')
        self.assertIn('noindex', page.headers['X-Robots-Tag'])

    def test_public_buttons_set_contact_state(self):
        lead = self._lead_with_token('pub2')
        token = lead.sudo().access_token
        self.assertEqual(lead.contact_state, 'new')
        done = self.url_open('/lead/%s/contact' % token, data={'result': 'contacted'}, allow_redirects=False)
        self.assertEqual(done.status_code, 303)
        lead.invalidate_recordset()
        self.assertEqual((lead.contact_state, bool(lead.contact_state_at)), ('contacted', True))
        self.assertIn('Đã liên hệ —', self.url_open('/lead/%s' % token).text)

    def test_decided_lead_has_locked_buttons_and_ignores_a_second_click(self):
        lead = self._lead_with_token('pub2b')
        token = lead.sudo().access_token
        self.url_open('/lead/%s/contact' % token, data={'result': 'unreachable'})
        lead.invalidate_recordset()
        self.assertEqual(lead.contact_state, 'unreachable')
        first_at = lead.contact_state_at
        html = self.url_open('/lead/%s' % token).text
        self.assertEqual(html.count('disabled="disabled"'), 2, 'both buttons are locked once someone decided')
        self.assertIn('Liên hệ không được —', html)
        # bấm đúp / gửi lại / mở link cũ rồi bấm: giữ nguyên quyết định đầu tiên
        self.url_open('/lead/%s/contact' % token, data={'result': 'contacted'})
        lead.invalidate_recordset()
        self.assertEqual((lead.contact_state, lead.contact_state_at), ('unreachable', first_at))

    def test_link_slug_starts_with_the_phone_and_old_token_only_links_still_work(self):
        lead = self._lead_with_token('slug1')
        token = lead.sudo().access_token
        self.assertEqual(lead._link_slug(), '0933939856-%s' % token)
        page = self.url_open('/lead/0933939856-%s' % token)
        self.assertEqual(page.status_code, 200)
        self.assertIn('/lead/0933939856-%s/contact' % token, page.text, 'the buttons post to the same link the visitor used')
        self.assertEqual(self.url_open('/lead/%s' % token).status_code, 200, 'links sent before the change keep working')
        alone = self.url_open('/lead/0933939856', allow_redirects=False)  # chỉ có số = route cũ theo số thứ tự: đòi đăng nhập
        self.assertIn('/web/login', alone.headers.get('Location', ''), 'the phone alone never shows the lead')
        self.assertEqual(self.url_open('/lead/0933939856-wrongtoken').status_code, 404)
        done = self.url_open('/lead/0933939856-%s/contact' % token, data={'result': 'contacted'}, allow_redirects=False)
        self.assertEqual(done.status_code, 303)
        self.assertTrue(done.headers['Location'].endswith('/lead/0933939856-%s?done=1' % token))
        lead.invalidate_recordset()
        self.assertEqual(lead.contact_state, 'contacted')

    def test_slug_normalises_phones_and_skips_unusable_ones(self):
        lead = self._lead_with_token('slug2')
        token = lead.sudo().access_token
        for phone, expected in (('+84933939856', '0933939856'), ('0933 939 856', '0933939856'), ('123', None), ('', None)):
            lead.sudo().phone = phone
            self.assertEqual(lead._link_slug(), '%s-%s' % (expected, token) if expected else token, phone)

    def test_public_link_rejects_unknown_tokens_and_bad_results(self):
        lead = self._lead_with_token('pub3')
        token = lead.sudo().access_token
        self.assertEqual(self.url_open('/lead/does-not-exist').status_code, 404)
        self.assertEqual(self.url_open('/lead/does-not-exist/contact', data={'result': 'contacted'}).status_code, 404)
        self.assertEqual(self.url_open('/lead/%s/contact' % token, data={'result': 'hack'}).status_code, 400)
        self.assertEqual(self.url_open('/lead/%s/contact' % token, data={'other': '1'}).status_code, 400)
        lead.invalidate_recordset()
        self.assertEqual(lead.contact_state, 'new')

    def test_logged_in_staff_are_sent_to_the_full_form(self):
        lead = self._lead_with_token('pub4')
        self.authenticate('admin', 'admin')
        response = self.url_open('/lead/%s' % lead.sudo().access_token, allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertTrue(response.headers['Location'].endswith('action-dac_push.web_lead_action/%d' % lead.id))

    def test_old_leads_without_token_get_one_on_demand_and_tokens_are_unique(self):
        lead = self._lead_with_token('pub5')
        self.env.cr.execute('UPDATE dac_web_lead SET access_token = NULL WHERE id = %s', [lead.id])
        lead.invalidate_recordset()
        token = lead._get_access_token()
        self.assertTrue(token)
        self.assertEqual(lead._get_access_token(), token, 'stable once issued')
        other = self._lead_with_token('pub6')
        self.assertNotEqual(other.sudo().access_token, token)

    def test_migration_gives_every_old_lead_its_own_token(self):
        import importlib.util
        import pathlib
        path = pathlib.Path(__file__).resolve().parents[1] / 'migrations' / '18.0.1.2.1' / 'pre-migration.py'
        spec = importlib.util.spec_from_file_location('dac_push_pre_migration', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        first, second = self._lead_with_token('mig1'), self._lead_with_token('mig2')
        # DB thật 1.2.0 không có ràng buộc này (không tạo được); DDL của PostgreSQL rollback cùng giao dịch test
        self.env.cr.execute('ALTER TABLE dac_web_lead DROP CONSTRAINT IF EXISTS dac_web_lead_access_token_unique')
        self.env.cr.execute('UPDATE dac_web_lead SET access_token = %s WHERE id IN %s', ['SHARED-TOKEN', (first.id, second.id)])
        self.env.cr.execute('UPDATE dac_web_lead SET access_token = NULL WHERE id = %s', [self._lead_with_token('mig3').id])
        module.migrate(self.env.cr, '18.0.1.2.0')
        self.env.cr.execute('SELECT count(*), count(DISTINCT access_token), count(access_token) FROM dac_web_lead')
        total, distinct, filled = self.env.cr.fetchone()
        self.assertEqual((total, filled), (total, total), 'no lead is left without a token')
        self.assertEqual(distinct, total, 'every token is unique')
