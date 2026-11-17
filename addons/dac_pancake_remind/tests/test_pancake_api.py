from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import requests

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.dac_pancake_remind.models.pancake_api import parse_time, redact

GET = 'odoo.addons.dac_pancake_remind.models.pancake_api.requests.get'
PAGE_ID = '1234567890'
T0 = datetime(2026, 11, 10, 3, 0, 0)  # 10:00 giờ VN, thứ ba


def response(payload, status=200):
    mock = MagicMock(status_code=status)
    mock.json.return_value = payload
    return mock


def conv(cid, updated='2026-11-10T03:00:00', inserted='2026-11-10T02:59:00', **extra):
    return dict({'id': cid, 'type': 'INBOX', 'page_id': PAGE_ID, 'customer_id': 'cus-' + cid, 'updated_at': updated,
                 'inserted_at': inserted, 'snippet': 'Cho mình hỏi giá', 'from': {'id': 'psid-' + cid, 'name': 'Khách ' + cid,
                 'picture': 'https://example.com/a.jpg'}}, **extra)


def msg(mid, at, who='customer', text='Cho mình hỏi giá', **extra):
    sender = {'id': 'psid-1', 'name': 'Khách'} if who == 'customer' else {'id': PAGE_ID, 'name': 'Shop', 'admin_id': 'a1', 'admin_name': 'Băng'}
    return dict({'id': mid, 'inserted_at': at, 'message': text, 'type': 'INBOX', 'page_id': PAGE_ID, 'from': sender, 'attachments': []}, **extra)


@tagged('post_install', '-at_install')
class TestPancakeApi(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.page = cls.env['dac.pancake.page'].create({'name': 'Duy An', 'page_fm_id': ' %s ' % PAGE_ID, 'page_access_token': 'tok-secret'})
        cls.Conversation = cls.env['dac.pancake.conversation']

    def fake_api(self, conversations, messages):
        calls = []

        def get(url, params=None, **kwargs):
            calls.append((url, dict(params)))
            if url.endswith('/messages'):
                return response({'success': True, 'messages': messages.get(params['conversation_id'], [])})
            # trang đầu trả danh sách, trang sau (có last_conversation_id) trả rỗng
            return response({'success': True, 'conversations': [] if params.get('last_conversation_id') else conversations})
        return get, calls

    def test_helpers(self):
        self.assertEqual(parse_time('2026-11-10T03:00:00.123'), datetime(2026, 11, 10, 3, 0, 0))
        self.assertEqual(parse_time('2026-11-10T10:00:00+07:00'), datetime(2026, 11, 10, 3, 0, 0))
        self.assertFalse(parse_time('rác'))
        self.assertNotIn('abc123', redact('invalid page_access_token=abc123&x=1'))
        self.assertEqual(self.page.page_fm_id, PAGE_ID, 'Page ID được bỏ khoảng trắng')

    def test_page_id_from_link_and_token(self):
        import base64
        import json
        from odoo.addons.dac_pancake_remind.models.pancake_api import page_ref, token_page_id

        def jwt(page):
            body = base64.urlsafe_b64encode(json.dumps({'id': page, 'timestamp': 1785094890}).encode()).decode().rstrip('=')
            return 'eyJhbGciOiJIUzI1NiJ9.%s.sig' % body

        self.assertEqual(page_ref(' https://pancake.vn/pzl_84915555018?c_id=abc '), 'pzl_84915555018')
        self.assertEqual(page_ref('pancake.vn/121932428506541/'), '121932428506541')
        self.assertEqual(page_ref('pzl_84915555018'), 'pzl_84915555018')
        self.assertEqual(token_page_id(jwt('pzl_157')), 'pzl_157')
        self.assertFalse(token_page_id('tok-secret'))
        Page = self.env['dac.pancake.page']
        # Zalo cá nhân: link trình duyệt dùng số điện thoại, token (và API) dùng mã khác → gọi API theo token, link mở theo mã đã dán
        zalo = Page.create({'name': 'Zalo', 'page_fm_id': 'https://pancake.vn/pzl_84915555018', 'page_access_token': jwt('pzl_157')})
        self.assertEqual((zalo.page_fm_id, zalo.page_slug), ('pzl_157', 'pzl_84915555018'))
        conversation = self.Conversation.create({'page_id': zalo.id, 'conversation_fm_id': 'c1'})
        self.assertEqual(conversation.external_url, 'https://pancake.vn/pzl_84915555018?c_id=c1')
        # Chỉ dán token cũng được; page đã lưu sai mã (trước bản này) tự sửa khi lưu lại
        only = Page.create({'name': 'Chỉ token', 'page_access_token': jwt('99887766')})
        self.assertEqual((only.page_fm_id, only.page_slug), ('99887766', False))
        old = Page.create({'name': 'Cũ', 'page_fm_id': 'pzl_8490000'})
        old.sudo().write({'page_access_token': jwt('pzl_222')})
        self.assertEqual((old.page_fm_id, old.page_slug, old.token_status), ('pzl_222', 'pzl_8490000', 'unchecked'))
        with self.assertRaises(UserError):
            Page.create({'name': 'Trống'})

    def test_history_crawl_stores_every_message_slim_and_counts_first_contact(self):
        """Lấy toàn bộ tin cũ: duyệt hết danh sách hội thoại (có phân trang), lấy đủ tin từng hội thoại 30 tin/lần; ảnh/file chỉ
        lưu link; lượt liên hệ tính từ tin đầu tiên của khách (dù rất cũ), ghi nhận cả khi shop nhắn trước."""
        old = [msg('h%02d' % i, (datetime(2025, 3, 1) + timedelta(hours=i)).isoformat(), 'staff' if i == 0 else 'customer') for i in range(45)]
        old[5]['attachments'] = [{'type': 'photo', 'url': 'https://content.pancake.vn/a.jpg', 'width': 9, 'data': 'BASE64…'},
                                 {'type': 'document', 'url': 'https://content.pancake.vn/b.pdf', 'name': 'bao-gia.pdf'}]
        newest_first = list(reversed(old))
        lists = {None: [conv('h1', updated='2026-11-01T00:00:00', inserted='2025-03-01T00:00:00')], 'h1': [conv('h2')], 'h2': []}

        def get(url, params=None, **kwargs):
            if url.endswith('/messages'):
                rows = newest_first if params['conversation_id'] == 'h1' else [msg('x1', '2026-11-10T02:59:00')]
                start = params['current_count']
                return response({'success': True, 'messages': list(reversed(rows[start:start + 30]))})
            return response({'success': True, 'conversations': lists[params.get('last_conversation_id')]})
        self.page.history_listed = False
        with patch(GET, side_effect=get):
            self.env['dac.pancake.page']._cron_crawl_history(budget=60)
        self.assertTrue(self.page.history_listed)
        h1 = self.Conversation.search([('conversation_fm_id', '=', 'h1')])
        self.assertEqual((h1.history_state, len(h1.message_ids)), ('done', 45))
        self.assertEqual(self.Conversation.search([('conversation_fm_id', '=', 'h2')]).history_state, 'done')
        with_files = h1.message_ids.filtered(lambda m: m.message_fm_id == 'h05')
        self.assertIn('photo: https://content.pancake.vn/a.jpg', with_files.attachment_urls)
        self.assertIn('document: https://content.pancake.vn/b.pdf (bao-gia.pdf)', with_files.attachment_urls)
        self.assertNotIn('BASE64', with_files.raw_json, 'không lưu dữ liệu ảnh/file, chỉ link')
        contact = self.env['dac.new.contact'].search([('conversation_id', '=', h1.id)])
        self.assertEqual((contact.contact_at, contact.staff_first, contact.counts_as_new), (datetime(2025, 3, 1, 1), True, True))
        # Đánh dấu nội bộ: không tính vào thống kê
        h1.action_mark_internal()
        self.assertEqual((contact.is_internal, contact.counts_as_new), (True, False))
        h1.action_unmark_internal()
        self.assertTrue(contact.counts_as_new)

    def test_history_crawl_resumes_where_it_stopped(self):
        rows = [msg('r%02d' % i, (datetime(2026, 1, 1) + timedelta(hours=i)).isoformat()) for i in range(70)][::-1]
        calls = []

        def get(url, params=None, **kwargs):
            calls.append(params.get('current_count'))
            return response({'success': True, 'messages': rows[params['current_count']:params['current_count'] + 30]})
        conversation = self.Conversation.create({'page_id': self.page.id, 'conversation_fm_id': 'r1'})
        with patch(GET, side_effect=get), patch('odoo.addons.dac_pancake_remind.models.pancake_api.time.monotonic', side_effect=[0, 999]):
            self.assertFalse(conversation._crawl_all_messages(deadline=10))
        self.assertEqual((conversation.history_offset, conversation.history_state), (30, 'pending'))
        with patch(GET, side_effect=get):
            self.assertTrue(conversation._crawl_all_messages(deadline=float('inf')))
        self.assertEqual(calls, [0, 30, 60])
        self.assertEqual((len(conversation.message_ids), conversation.history_state), (70, 'done'))

    def test_facebook_avatar_from_psid_and_image_without_times(self):
        from odoo.addons.dac_pancake_remind.models import chat_image
        row = conv('fb1', **{'from': {'id': '2901055609', 'name': 'Kiệt', 'email': '2901055609@facebook.com'},
                             'page_customer': {'psid': '2901055609', 'name': 'Kiệt'}})
        conversation, _new = self.Conversation._upsert(self.page, row)
        self.assertEqual(conversation.avatar_url, 'https://pages.fm/api/v1/pages/%s/avatar/2901055609' % PAGE_ID)
        zalo, _new = self.Conversation._upsert(self.page, conv('z1'))
        self.assertEqual(zalo.avatar_url, 'https://example.com/a.jpg', 'có link ảnh sẵn thì dùng link đó')
        page = chat_image.build_html('Kiệt', None, [{'side': 'in', 'text': 'Hi'}, {'side': 'out', 'text': 'Dạ'}], waiting_text='Khách nhắn lúc 11:57')
        self.assertNotIn('class="t"', page)
        self.assertNotIn('class="day"', page)
        self.assertIn('Khách nhắn lúc 11:57', page)

    def test_poll_stores_private_chats_and_their_messages_with_page_token(self):
        get, calls = self.fake_api(
            [conv('c1'), conv('c-cmt', type='COMMENT'), conv('pzl_g_1_2')],
            {'c1': [msg('m1', '2026-11-10T02:59:00'), msg('m2', '2026-11-10T03:00:00', 'staff', 'Dạ shop nghe')]})
        with patch(GET, side_effect=get):
            fetched = self.page._poll(T0 - timedelta(minutes=10), T0)
        conversation = self.Conversation.search([('page_id', '=', self.page.id)])
        self.assertEqual(conversation.mapped('conversation_fm_id'), ['c1'], 'bình luận và nhóm Zalo không lưu')
        self.assertEqual(fetched, conversation)
        self.assertEqual((conversation.customer_name, conversation.customer_fm_id, conversation.updated_at_fm), ('Khách c1', 'cus-c1', T0))
        self.assertEqual(conversation.external_url, 'https://pancake.vn/%s?c_id=c1' % PAGE_ID)
        self.assertEqual(conversation.message_ids.mapped('message_fm_id'), ['m1', 'm2'])
        self.assertEqual(conversation.message_ids[1].sender_name, 'Băng')
        self.assertTrue(all(params['page_access_token'] == 'tok-secret' and params['page_id'] == PAGE_ID for _url, params in calls))
        self.assertEqual(self.page.last_polled_at, T0)

        # Quét lại: hội thoại không đổi thì không tải lại tin; có cập nhật thì tải và chỉ thêm tin mới
        with patch(GET, side_effect=get) as mocked:
            self.page._poll(T0 - timedelta(minutes=10), T0)
        self.assertFalse([c for c in mocked.call_args_list if c.args[0].endswith('/messages')])
        get2, _calls = self.fake_api([conv('c1', updated='2026-11-10T03:05:00')],
            {'c1': [msg('m1', '2026-11-10T02:59:00'), msg('m3', '2026-11-10T03:05:00', text='Còn hàng không?')]})
        with patch(GET, side_effect=get2):
            self.page._poll(T0, T0 + timedelta(minutes=6))
        self.assertEqual(conversation.message_ids.mapped('message_fm_id'), ['m1', 'm2', 'm3'])

    def test_poll_error_is_recorded_without_token(self):
        from odoo.addons.dac_pancake_remind.models.pancake_api import PancakeError
        raised = False
        with patch(GET, return_value=response({'success': False, 'message': 'invalid page_access_token=tok-secret'}, 400)):
            try:  # không dùng assertRaises: nó rollback cả phần ghi lỗi vào page
                self.page._poll(T0 - timedelta(minutes=10), T0)
            except PancakeError:
                raised = True
        self.assertTrue(raised)
        self.assertIn('invalid', self.page.last_error)
        self.assertNotIn('tok-secret', self.page.last_error)

    def test_page_without_token_is_skipped_by_cron(self):
        bare = self.env['dac.pancake.page'].create({'name': 'Chưa token', 'page_fm_id': 'p-bare'})
        config = self.env['dac.pancake.remind.config']._get()
        config.write({'active': True, 'page_ids': [(6, 0, (self.page | bare).ids)]})
        get, calls = self.fake_api([conv('c9')], {'c9': [msg('m9', '2026-11-10T03:00:00')]})
        with patch(GET, side_effect=get), patch.object(fields.Datetime, 'now', return_value=T0 + timedelta(minutes=2)):
            self.env['dac.pancake.remind.wait']._cron_scan()
        self.assertTrue(calls)
        self.assertTrue(all(params['page_id'] == PAGE_ID for _url, params in calls), 'page chưa có token không gọi API')
        self.assertTrue(self.Conversation.search([('conversation_fm_id', '=', 'c9')]))

    def test_check_token_states(self):
        with patch(GET, return_value=response({'success': True, 'conversations': [conv('a'), conv('b')]})):
            self.page.action_check_token()
        self.assertEqual(self.page.token_status, 'valid')
        self.assertIn('2', self.page.token_message)
        with patch(GET, return_value=response({'success': False, 'message': 'access_token expired'})):
            self.page.action_check_token()
        self.assertEqual(self.page.token_status, 'invalid')
        with patch(GET, side_effect=requests.exceptions.ConnectionError('Connection refused')):
            self.page.action_check_token()
        self.assertEqual(self.page.token_status, 'error')
        self.page.page_access_token = 'tok-new'
        self.assertEqual(self.page.token_status, 'unchecked', 'đổi token thì phải kiểm tra lại')

    def test_token_only_visible_to_admins(self):
        manager = self.env['res.users'].create({'name': 'QL', 'login': 'pancake_mgr_test',
            'groups_id': [(6, 0, [self.env.ref('dac_erp.group_dac_erp_manager').id, self.env.ref('base.group_user').id])]})
        page = self.page.with_user(manager)
        self.assertTrue(page.has_token)
        self.assertNotIn('page_access_token', page.fields_get())
        with self.assertRaises(UserError):
            page.action_check_token()

    def test_old_conversation_is_not_a_new_customer(self):
        conversation = self.Conversation.create({'page_id': self.page.id, 'conversation_fm_id': 'old', 'customer_name': 'Khách cũ',
                                                 'inserted_at_fm': T0 - timedelta(days=30)})
        Wait = self.env['dac.pancake.remind.wait']
        self.assertFalse(Wait._is_new_customer(conversation, T0, ()))
        conversation.inserted_at_fm = T0 - timedelta(hours=1)
        self.assertTrue(Wait._is_new_customer(conversation, T0, ()))

    def test_partner_matched_by_phone_written_with_spaces(self):
        from odoo.addons.dac_pancake_remind.models.pancake_api import match_partner
        partner = self.env['res.partner'].create({'name': 'Chị Lan', 'phone': '+84 912.345.678'})
        self.assertEqual(match_partner(self.env, '0912345678'), partner)
        self.assertFalse(match_partner(self.env, '0912345679'))

    def test_legacy_flow_can_be_hidden_and_restored(self):
        config = self.env['dac.pancake.remind.config']._get()
        records = config._legacy_records()
        if not records:
            self.skipTest('DB thử không cài CRM_DAC')
        before = {xmlid: record.active for xmlid, record in records}
        config.action_disable_legacy()
        self.assertTrue(config.legacy_disabled)
        self.assertFalse(any(record.active for _xmlid, record in config._legacy_records()))
        config.action_enable_legacy()
        self.assertFalse(config.legacy_disabled)
        self.assertEqual({xmlid: record.active for xmlid, record in config._legacy_records()}, before, 'bật lại đúng như cũ')
