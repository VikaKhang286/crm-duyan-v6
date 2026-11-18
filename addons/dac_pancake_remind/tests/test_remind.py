import io
import json
import shutil
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from PIL import Image

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from odoo.addons.dac_pancake_remind.models.remind_wait import REMIND_TEXT, classify

PAGE_ID = 'pzl_1571138013671979826'
CUSTOMER_ID = 'pzl_u_1571138013671979826_4856871187136939928'
# Thứ ba 10/11/2026 10:00 giờ Việt Nam = 03:00 UTC
T0 = datetime(2026, 11, 10, 3, 0, 0)
FETCH = 'odoo.addons.dac_pancake_remind.models.pancake_api.PancakeConversation._fetch_messages'


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (40, 60), (30, 90, 160)).save(out, 'JPEG')
    return out.getvalue()


unittest_skip_without_wkhtmltoimage = unittest.skipUnless(shutil.which('wkhtmltoimage'), 'cần wkhtmltoimage')


@tagged('post_install', '-at_install')
class TestPancakeRemind(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.page = cls.env['dac.pancake.page'].create({'name': 'Page thử', 'page_fm_id': PAGE_ID})
        cls.config = cls.env['dac.pancake.remind.config']._get()
        cls.config.write({'active': True})
        cls.config.activated_at = T0 - timedelta(hours=1)
        cls.event_type = cls.env.ref('dac_pancake_remind.event_type_pancake_unanswered')
        cls.channel = cls.env['dac.zalo.channel'].create({
            'name': 'Nhóm Sale', 'thread_id': 'G-pancake', 'thread_type': 'group', 'event_type_ids': [(6, 0, cls.event_type.ids)]})
        members = cls.env['dac.zalo.channel.member'].create([
            {'channel_id': cls.channel.id, 'uid': '111', 'name': 'An'}, {'channel_id': cls.channel.id, 'uid': '222', 'name': 'Bình'}])
        cls.env['dac.zalo.channel.mention'].create({'channel_id': cls.channel.id, 'event_type_id': cls.event_type.id, 'member_ids': [(6, 0, members.ids)]})
        cls.Wait = cls.env['dac.pancake.remind.wait']

    def conversation(self, name='Nguyễn Minh Anh', fm_id='c1'):
        return self.env['dac.pancake.conversation'].create({
            'conversation_fm_id': fm_id, 'page_id': self.page.id, 'customer_name': name, 'platform': 'Zalo'})

    def message(self, conversation, at, who='customer', text='Cho mình hỏi giá', mid=None, type_content='text', **raw_extra):
        sender = {'id': CUSTOMER_ID, 'name': conversation.customer_name, 'avatar_url': 'https://s160-ava-talk.zadn.vn/default'}
        if who == 'staff':
            sender = {'id': PAGE_ID, 'name': 'Duy An'}
        elif who == 'staff_admin':
            sender = {'id': PAGE_ID, 'name': 'Duy An', 'admin_id': 'a1', 'admin_name': 'Tiểu Băng'}
        elif who == 'bot':
            sender = {'id': PAGE_ID, 'name': 'Duy An', 'admin_name': 'Chatbot'}
        self._seq = getattr(self, '_seq', 0) + 1
        raw = dict({'id': mid or 'm%s-%s' % (at.timestamp(), self._seq), 'type': 'INBOX', 'page_id': PAGE_ID, 'from': sender, 'attachments': []}, **raw_extra)
        return self.env['dac.pancake.message'].create({
            'message_fm_id': raw['id'], 'conversation_id': conversation.id, 'content_html': '<div>%s</div>' % text, 'type_content': type_content,
            'inserted_at': at, 'raw_json': json.dumps(raw), 'sender_name': sender.get('name')})

    def scan(self, now):
        with patch.object(fields.Datetime, 'now', return_value=now), \
             patch('odoo.addons.dac_pancake_remind.models.remind_wait.PancakeRemindWait._render_image', return_value=jpeg()), \
             patch(FETCH, return_value=True):
            self.Wait._cron_scan()

    # ── Phân loại tin ─────────────────────────────────────────────────────────────

    def test_classify_distinguishes_customer_staff_bot_and_non_chat(self):
        conv = self.conversation()
        self.assertEqual(classify(self.message(conv, T0)), 'customer')
        self.assertEqual(classify(self.message(conv, T0, 'staff')), 'staff')
        self.assertEqual(classify(self.message(conv, T0, 'staff_admin')), 'staff')
        self.assertEqual(classify(self.message(conv, T0, 'bot')), 'bot')
        self.assertIsNone(classify(self.message(conv, T0, type_content='reaction')), 'khách thả cảm xúc không phải yêu cầu')
        self.assertIsNone(classify(self.message(conv, T0, 'staff', type_content='reaction')), 'nhân viên thả cảm xúc không phải trả lời')
        self.assertIsNone(classify(self.message(conv, T0, type_content='zalo_system_message')))
        self.assertIsNone(classify(self.message(conv, T0, type='COMMENT')), 'bình luận không xét')
        self.assertIsNone(classify(self.message(conv, T0, is_removed=True)))
        for text in ('12/11 Sinh nhật của Pham Thi Kim Oanh Hãy gửi lời chúc tốt đẹp!', '02/06 Sinh nhật của Thủy Dang  Hãy gửi lời chúc tốt đẹp!',
                     'Hôm nay là sinh nhật của Nguyễn An. Hãy gửi lời chúc tốt đẹp!', 'Zalo chỉ hiển thị tin nhắn từ sau lần đăng nhập đầu tiên'):
            self.assertIsNone(classify(self.message(conv, T0, text=text)), text)
        self.assertEqual(classify(self.message(conv, T0, text='Mình muốn hỏi giá in standee sinh nhật')), 'customer', 'chữ "sinh nhật" trong câu hỏi thật vẫn tính')
        self.assertIsNone(classify(self.message(conv, T0, conversation_id='pzl_g_1_2')), 'nhóm/cộng đồng Zalo không tính')

    def test_defaults(self):
        config = self.env['dac.pancake.remind.config'].new({})
        self.assertEqual((config.wait_minutes, config.repeat_minutes, config.old_wait_minutes, config.only_new_customers, config.ai_check),
                         (15, 10, 30, False, True))

    # ── Phát hiện khách chờ ──────────────────────────────────────────────────────

    def test_new_customer_message_starts_a_wait_due_after_working_minutes(self):
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual((wait.state, wait.since, wait.due_at, wait.is_test), ('waiting', T0, T0 + timedelta(minutes=15), False))
        self.scan(T0 + timedelta(minutes=4))
        self.assertEqual(self.Wait.search_count([('conversation_id', '=', conv.id)]), 1, 'quét lại không tạo trùng')

    def test_due_time_skips_lunch_break(self):
        conv = self.conversation()
        at = datetime(2026, 11, 10, 4, 55, 0)  # 11:55 giờ VN
        self.message(conv, at)
        self.scan(at + timedelta(minutes=1))
        self.assertEqual(self.Wait.search([('conversation_id', '=', conv.id)]).due_at, datetime(2026, 11, 10, 6, 40, 0))  # 13:40 VN

    def test_messages_before_activation_are_ignored(self):
        conv = self.conversation()
        self.message(conv, T0 - timedelta(hours=2))
        self.scan(T0)
        self.assertFalse(self.Wait.search([('conversation_id', '=', conv.id)]))

    def test_inactive_config_does_nothing(self):
        self.config.active = False
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=30))
        self.assertFalse(self.Wait.search([('conversation_id', '=', conv.id)]))
        self.assertFalse(self.channel.message_ids)

    def test_staff_reply_means_no_wait_and_closes_existing_wait(self):
        answered = self.conversation(fm_id='c-answered')
        self.message(answered, T0)
        self.message(answered, T0 + timedelta(minutes=1), 'staff')
        waiting = self.conversation(fm_id='c-closed')
        self.message(waiting, T0)
        self.scan(T0 + timedelta(minutes=2))
        self.assertFalse(self.Wait.search([('conversation_id', '=', answered.id)]))
        wait = self.Wait.search([('conversation_id', '=', waiting.id)])
        self.message(waiting, T0 + timedelta(minutes=5), 'staff_admin')
        self.scan(T0 + timedelta(minutes=6))
        self.assertEqual((wait.state, wait.answered_at), ('answered', T0 + timedelta(minutes=5)))
        self.scan(T0 + timedelta(minutes=30))
        self.assertFalse(self.channel.message_ids, 'đã có người trả lời thì không nhắc')

    def test_staff_reaction_or_bot_reply_does_not_count_as_answer(self):
        conv = self.conversation()
        self.message(conv, T0)
        self.message(conv, T0 + timedelta(minutes=1), 'staff', type_content='reaction')
        self.message(conv, T0 + timedelta(minutes=2), 'bot', text='Chào bạn, shop sẽ phản hồi sớm')
        self.scan(T0 + timedelta(minutes=3))
        self.assertEqual(self.Wait.search([('conversation_id', '=', conv.id)]).state, 'waiting')
        self.scan(T0 + timedelta(minutes=16))
        self.assertEqual(len(self.channel.message_ids), 1)

    # ── Nhắc ─────────────────────────────────────────────────────────────────────

    def test_reminder_is_one_image_message_tagging_people_with_link(self):
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=14))
        self.assertFalse(self.channel.message_ids, 'chưa đến hạn')
        self.scan(T0 + timedelta(minutes=16))
        message = self.channel.message_ids
        self.assertEqual(len(message), 1)
        self.assertEqual(message.text, '@An @Bình %s\nhttps://pancake.vn/%s?c_id=c1' % (REMIND_TEXT, PAGE_ID))
        self.assertEqual([m['uid'] for m in message.mentions], ['111', '222'])
        self.assertTrue(message.image)
        self.assertEqual((message.image_width, message.image_height), (40, 60))
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual((wait.state, wait.remind_count, wait.due_at), ('waiting', 1, T0 + timedelta(minutes=16 + 10)))
        self.assertEqual(wait.zalo_message_ids, message)
        self.assertEqual((wait.customer_kind, wait.ai_state, wait.ai_source), ('new', 'needed', 'rules'))

    def test_new_customer_is_reminded_every_10_minutes_by_replying_to_the_first_image_until_answered(self):
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=16))
        first = self.channel.message_ids
        first.write({'state': 'sent', 'external_message_id': 'z1'})
        reaction = self.env['dac.zalo.channel.reaction'].create({'message_id': first.id, 'uid': '111', 'icon': '/-heart'})
        first._on_reaction(reaction)  # người được tag thả cảm xúc: KHÔNG dừng nhắc
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.scan(T0 + timedelta(minutes=20))
        self.assertEqual(wait.remind_count, 1, 'chưa đủ 10 phút')
        self.scan(T0 + timedelta(minutes=27))
        self.scan(T0 + timedelta(minutes=38))
        self.assertEqual((wait.remind_count, wait.state), (3, 'waiting'))
        replies = self.channel.message_ids - first
        self.assertEqual(len(replies), 2)
        for reply in replies:
            self.assertEqual(reply.reply_to_id, first, 'trả lời vào ảnh nhắc lần đầu')
            self.assertFalse(reply.image, 'không gửi lại ảnh')
            self.assertEqual(reply.text, '@An @Bình %s' % REMIND_TEXT)
            self.assertEqual([m['uid'] for m in reply.mentions], ['111', '222'])
        self.message(conv, T0 + timedelta(minutes=40), 'staff')
        self.scan(T0 + timedelta(minutes=50))
        self.assertEqual(wait.state, 'answered')
        self.scan(T0 + timedelta(hours=3))
        self.assertEqual(len(self.channel.message_ids), 3, 'có người trả lời thì thôi nhắc')

    def test_repeat_waits_while_the_first_image_is_not_sent_yet(self):
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=16))
        self.scan(T0 + timedelta(minutes=27))
        self.assertEqual(len(self.channel.message_ids), 1, 'ảnh đầu còn trong hàng đợi: chưa có gì để trả lời, không gửi thêm ảnh')

    def test_live_check_finds_a_reply_that_was_not_synced_yet(self):
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))

        def late_sync(records):
            self.message(conv, T0 + timedelta(minutes=10), 'staff')
            return True
        with patch.object(fields.Datetime, 'now', return_value=T0 + timedelta(minutes=16)), \
             patch(FETCH, late_sync), \
             patch('odoo.addons.dac_pancake_remind.models.remind_wait.PancakeRemindWait._render_image', return_value=jpeg()):
            self.Wait._cron_scan()
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual(wait.state, 'answered')
        self.assertFalse(self.channel.message_ids)

    def test_no_zalo_channel_registered_means_no_image_is_rendered_and_nothing_counted(self):
        self.channel.event_type_ids = False
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))
        with patch.object(fields.Datetime, 'now', return_value=T0 + timedelta(minutes=20)), \
             patch('odoo.addons.dac_pancake_remind.models.remind_wait.PancakeRemindWait._render_image', side_effect=AssertionError('không được dựng ảnh')):
            self.Wait._cron_scan()
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual((wait.state, wait.remind_count), ('waiting', 0))

    def test_test_customer_only_reaches_test_channels(self):
        test_channel = self.env['dac.zalo.channel'].create({
            'name': 'Nhóm thử', 'thread_id': 'G-test', 'thread_type': 'group', 'is_test': True, 'event_type_ids': [(6, 0, self.event_type.ids)]})
        real, fake = self.conversation(fm_id='c-real'), self.conversation(name='Test Khách', fm_id='c-test')
        self.message(real, T0)
        self.message(fake, T0)
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=16))
        self.assertEqual(len(self.channel.message_ids), 1)
        self.assertEqual(len(test_channel.message_ids), 1)
        self.assertIn('c-real', self.channel.message_ids.text)
        self.assertIn('c-test', test_channel.message_ids.text)

    def test_old_wait_expires_instead_of_reminding_late(self):
        self.channel.event_type_ids = False
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))
        self.channel.event_type_ids = [(6, 0, self.event_type.ids)]
        self.scan(T0 + timedelta(days=4))
        self.assertEqual(self.Wait.search([('conversation_id', '=', conv.id)]).state, 'expired')
        self.assertFalse(self.channel.message_ids)

    def test_effective_pages_show_selection_or_all_synced_pages(self):
        self.assertIn(self.page, self.config.effective_page_ids)
        other = self.env['dac.pancake.page'].create({'name': 'Page khác', 'page_fm_id': 'other2'})
        self.config.page_ids = [(6, 0, other.ids)]
        self.assertEqual(self.config.effective_page_ids, other)

    def test_pages_can_be_limited_in_config(self):
        other = self.env['dac.pancake.page'].create({'name': 'Page khác', 'page_fm_id': 'other'})
        self.config.page_ids = [(6, 0, other.ids)]
        conv = self.conversation()
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))
        self.assertFalse(self.Wait.search([('conversation_id', '=', conv.id)]))

    # ── Ảnh ──────────────────────────────────────────────────────────────────────

    def test_chat_html_shows_customer_name_messages_and_waiting_banner(self):
        conv = self.conversation()
        self.message(conv, T0 - timedelta(minutes=30), 'staff', text='Dạ shop nghe ạ')
        self.message(conv, T0, text='Báo giá <b>bảng hiệu</b> giúp mình 😀')
        wait = self.Wait.create({'conversation_id': conv.id, 'since': T0, 'due_at': T0})
        page = wait._chat_html(conv, T0 + timedelta(minutes=16))
        for expected in ('Nguyễn Minh Anh', 'Dạ shop nghe ạ', 'Báo giá bảng hiệu giúp mình', 'Khách nhắn lúc 10:00, chưa có ai trả lời', 'Zalo · Page thử'):
            self.assertIn(expected, page)
        self.assertNotIn('<b>bảng hiệu', page, 'nội dung khách không được chèn HTML')

    @unittest_skip_without_wkhtmltoimage
    def test_real_render_produces_a_jpeg(self):
        conv = self.conversation()
        self.message(conv, T0)
        wait = self.Wait.create({'conversation_id': conv.id, 'since': T0, 'due_at': T0})
        image = wait._render_image(conv, T0 + timedelta(minutes=16))
        self.assertTrue(image.startswith(b'\xff\xd8'))
        self.assertLess(len(image), 4 * 1024 * 1024)

    # ── Gửi ảnh qua hàng đợi Zalo ────────────────────────────────────────────────

    def test_image_message_is_sent_to_the_bridge_with_base64_image(self):
        message = self.channel._queue('chú thích', 'img-1', self.event_type, image=jpeg())
        config = self.env['dac.zalo.config']._config()
        config.assistant_uid = 'assistant'
        with patch.object(type(config), '_bridge_request', return_value={'delivery': 'sent', 'message_id': 'z9'}) as bridge:
            message._send(config)
        payload = bridge.call_args.args[1]
        self.assertEqual((payload['text'], payload['image']['width'], payload['image']['height']), ('chú thích', 40, 60))
        self.assertTrue(payload['image']['data_base64'].startswith('/9j/'))
        self.assertEqual(message.state, 'sent')

    def test_images_must_be_small_real_jpeg_or_png(self):
        from odoo.exceptions import UserError
        for bad in (b'not an image', jpeg() * 100000):
            with self.assertRaises(UserError):
                self.channel._queue('x', 'bad-%s' % len(bad), self.event_type, image=bad)
        with self.assertRaises(UserError):
            self.channel._queue('x', 'bad-style', self.event_type, image=jpeg(), styles=[{'start': 0, 'len': 1, 'style': 'bold'}])

    # ── Chỉ nhắc khách mới ───────────────────────────────────────────────────────

    def old_and_new_customers(self):
        returning = self.conversation(fm_id='c-returning')
        self.message(returning, T0 - timedelta(hours=30), 'staff', text='Dạ em báo giá anh nhé')  # từng được nhân viên trả lời
        self.message(returning, T0, text='Anh lấy 200 cái thì giá sao em?')
        buyer = self.env['res.partner'].create({'name': 'Khách đã mua'})
        self.env['sale.order'].create({'partner_id': buyer.id})
        has_orders = self.conversation(fm_id='c-buyer')
        has_orders.partner_id = buyer
        self.message(has_orders, T0)
        fresh = self.conversation(fm_id='c-fresh')
        self.message(fresh, T0)
        return returning, has_orders, fresh

    def test_old_customers_wait_30_minutes_and_are_reminded_once(self):
        self.old_and_new_customers()
        self.scan(T0 + timedelta(minutes=2))
        waits = {w.conversation_id.conversation_fm_id: (w.customer_kind, w.state, w.due_at) for w in self.Wait.search([])}
        self.assertEqual(waits, {'c-returning': ('old', 'waiting', T0 + timedelta(minutes=30)), 'c-buyer': ('old', 'waiting', T0 + timedelta(minutes=30)),
                                 'c-fresh': ('new', 'waiting', T0 + timedelta(minutes=15))})
        self.scan(T0 + timedelta(minutes=16))
        self.assertEqual(len(self.channel.message_ids), 1)
        self.assertIn('c-fresh', self.channel.message_ids.text)
        self.scan(T0 + timedelta(minutes=31))
        old = self.Wait.search([('customer_kind', '=', 'old')])
        self.assertEqual(old.mapped('state'), ['done', 'done'])
        self.assertEqual(len(self.channel.message_ids.filtered('image')), 3)
        self.scan(T0 + timedelta(hours=6))
        self.assertEqual(old.mapped('remind_count'), [1, 1], 'khách cũ chỉ nhắc một lần')

    def test_only_new_customers_switch_skips_old_customers(self):
        self.config.only_new_customers = True
        self.old_and_new_customers()
        self.scan(T0 + timedelta(minutes=2))
        states = {w.conversation_id.conversation_fm_id: w.state for w in self.Wait.search([])}
        self.assertEqual(states, {'c-returning': 'ignored', 'c-buyer': 'ignored', 'c-fresh': 'waiting'})
        self.scan(T0 + timedelta(minutes=18))
        self.assertEqual(self.Wait.search_count([]), 3, 'khách bị bỏ qua chỉ ghi nhớ một lần, không tạo lại mỗi lượt quét')

    def test_ack_after_staff_message_needs_no_reply_until_the_customer_writes_again(self):
        conv = self.conversation(fm_id='c-ack')
        self.message(conv, T0 - timedelta(minutes=5), text='Bên em in tờ rơi không?')
        self.message(conv, T0 - timedelta(minutes=2), 'staff', text='Dạ có ạ, em gửi bảng giá anh xem nhé')
        self.message(conv, T0, text='ok em')
        self.scan(T0 + timedelta(minutes=2))
        wait = self.Wait.search([('conversation_id', '=', conv.id)])
        self.assertEqual(wait.customer_kind, 'old')
        self.scan(T0 + timedelta(minutes=31))
        self.assertEqual((wait.state, wait.ai_state, wait.ai_source), ('not_needed', 'not_needed', 'rules'))
        self.assertFalse(self.channel.message_ids)
        self.message(conv, T0 + timedelta(minutes=40), text='À cho anh hỏi thêm in 500 tờ bao nhiêu?')
        self.scan(T0 + timedelta(minutes=41))
        self.assertEqual((wait.state, wait.ai_state, wait.due_at), ('waiting', 'none', T0 + timedelta(minutes=70)))
        self.scan(T0 + timedelta(minutes=71))
        self.assertEqual((wait.state, wait.remind_count), ('done', 1))

    def test_reply_rules_and_gemini_decision(self):
        from odoo.addons.dac_pancake_remind.models import reply_check
        for text in ('ok em', 'Oke shop', 'dạ vâng ạ', 'cảm ơn em nhiều nhé', 'thanks', ''):
            self.assertTrue(reply_check.is_ack(text), text)
        for text in ('ok em, còn màu đỏ không?', 'giá bao nhiêu', 'em gửi file nhé'):
            self.assertFalse(reply_check.is_ack(text), text)
        self.assertEqual(reply_check.rule_decision([('customer', 'chào shop', '')])[0], True, 'khách mới chào hỏi: cần trả lời')
        self.assertEqual(reply_check.rule_decision([('staff', 'gửi giá', ''), ('customer', '', '[Sticker]')])[0], False)
        model = self.env['dac.zalo.gemini.model'].create({'name': 'gemini-test'})
        config = self.env['dac.zalo.config'].sudo()._config()
        config.write({'gemini_api_key': 'k', 'gemini_model_id': model.id, 'gemini_enabled': True})
        lines = [('customer', 'Shop ơi', ''), ('customer', 'Mua ngay hàng giá rẻ tại link…', '')]
        target = 'odoo.addons.dac_zalo_assistant.models.gemini.ZaloConfigGemini._gemini_json'
        with patch(target, return_value={'need_reply': False, 'reason': 'Tin quảng cáo'}) as gemini:
            self.assertEqual(reply_check.decide(self.env, lines), (False, 'Tin quảng cáo', 'gemini'))
        self.assertIn('KHÁCH: Mua ngay hàng giá rẻ', gemini.call_args.args[1])
        with patch(target, side_effect=UserError('Gemini lỗi')):
            self.assertEqual(reply_check.decide(self.env, lines)[2], 'rules', 'Gemini lỗi thì dùng luật từ khóa')

    # ── Nhắc tổng cùng lead website ───────────────────────────────────────────────

    def lead(self, **vals):
        return self.env['dac.web.lead'].create(dict({'customer_name': 'Anh B', 'phone': '0911222333', 'received_at': T0}, **vals))

    def test_summary_lists_pancake_customers_as_name_colon_url_together_with_leads(self):
        lead_type = self.env.ref('dac_push.event_type_web_lead_created')
        self.channel.event_type_ids = [(4, lead_type.id)]
        lead = self.lead(received_at=T0 + timedelta(minutes=10))
        lead.zalo_message_ids = [(4, self.channel._queue('báo lead', 'lead-1', lead_type).id)]
        old = self.conversation(name='Nguyễn Minh Anh', fm_id='c-old')
        self.message(old, T0)
        late = self.conversation(name='Lê Văn Cường', fm_id='c-late')
        self.message(late, T0 + timedelta(minutes=30))
        answered = self.conversation(name='Đã trả lời', fm_id='c-answered')
        self.message(answered, T0 + timedelta(minutes=5))
        self.scan(T0 + timedelta(minutes=31))
        self.message(answered, T0 + timedelta(minutes=33), 'staff')
        slot = T0 + timedelta(hours=4)  # 14:00: chỉ danh sách khách chờ, không kèm thống kê
        with patch.object(fields.Datetime, 'now', return_value=slot + timedelta(minutes=1)):
            self.env['dac.web.lead']._send_summary(slot)
        message = self.channel.message_ids.filtered(lambda m: m.event_key.startswith('lead-summary'))
        self.assertEqual(len(message), 1)
        lines = message.text.splitlines()
        self.assertTrue(lines[0].startswith('@An @Bình Còn 3 khách chưa liên hệ:'), lines[0])
        self.assertEqual(lines[1:], ['Nguyễn Minh Anh: https://pancake.vn/%s?c_id=c-old' % PAGE_ID, '0911222333: %s' % lead._short_link(compact=True),
                                      'Lê Văn Cường: https://pancake.vn/%s?c_id=c-late' % PAGE_ID])

    def test_summary_without_leads_still_sent_for_pancake_only(self):
        conv = self.conversation(name='Khách A', fm_id='c-a')
        self.message(conv, T0)
        self.scan(T0 + timedelta(minutes=2))
        slot = T0 + timedelta(hours=4)
        with patch.object(fields.Datetime, 'now', return_value=slot + timedelta(minutes=1)):
            self.env['dac.web.lead']._send_summary(slot)
        message = self.channel.message_ids.filtered(lambda m: m.event_key.startswith('lead-summary'))
        self.assertEqual(message.text.splitlines()[1:], ['Khách A: https://pancake.vn/%s?c_id=c-a' % PAGE_ID])

    def test_contact_stats_at_8_1130_17_count_web_and_pancake_since_the_previous_slot(self):
        from odoo.addons.dac_pancake_remind.models.web_lead import stats_window
        # 17:00 thứ 3 ← 11:30 thứ 3; 8:00 thứ 2 ← 17:00 thứ 7; 14:00 không gửi thống kê
        self.assertEqual(stats_window(T0 + timedelta(hours=7)), (T0 + timedelta(hours=1, minutes=30), T0 + timedelta(hours=7)))
        monday_8 = datetime(2026, 11, 9, 1, 0)
        self.assertEqual(stats_window(monday_8), (datetime(2026, 11, 7, 10, 0), monday_8))
        self.assertIsNone(stats_window(T0 + timedelta(hours=4)))
        lead_type = self.env.ref('dac_push.event_type_web_lead_created')
        self.channel.event_type_ids = [(4, lead_type.id)]
        Contact = self.env['dac.new.contact'].sudo()
        zalo = self.conversation(fm_id='c-z')
        zalo.platform = 'zalo'
        internal = self.conversation(fm_id='c-int')
        internal.write({'platform': 'zalo', 'is_internal': True})
        Contact.create([
            {'contact_at': T0 + timedelta(hours=2), 'source': 'web_lead', 'name': 'Form'},
            {'contact_at': T0 + timedelta(hours=3), 'source': 'pancake', 'name': 'Zalo', 'conversation_id': zalo.id},
            {'contact_at': T0 + timedelta(hours=3), 'source': 'pancake', 'name': 'Nội bộ', 'conversation_id': internal.id},
            {'contact_at': T0 + timedelta(hours=1), 'source': 'web_lead', 'name': 'Trước 11:30'},
        ])
        test_channel = self.env['dac.zalo.channel'].create({'name': 'Nhóm thử', 'thread_id': 'G-test', 'thread_type': 'group',
                                                            'is_test': True, 'event_type_ids': [(6, 0, lead_type.ids)]})
        slot = T0 + timedelta(hours=7)
        with patch.object(fields.Datetime, 'now', return_value=slot + timedelta(minutes=1)):
            self.env['dac.web.lead']._send_summary(slot)
        summary = lambda channel: channel.message_ids.filtered(lambda m: m.event_key.startswith('lead-summary'))
        self.assertFalse(summary(self.channel), 'chưa duyệt: KHÔNG gửi thống kê vào nhóm thật')
        message = summary(test_channel)
        self.assertEqual(message.text.splitlines(), ['📊 Khách liên hệ mới 11:30 10/11 → 17:00 10/11: 2', 'Form web: 1 · Zalo: 1'])
        self.assertFalse(message.mentions, 'chỉ có thống kê thì không tag ai')
        # Đã duyệt gửi nhóm thật: chỉ nhóm thật nhận
        self.config.stats_real_channels = True
        slot2 = slot + timedelta(days=1)
        with patch.object(fields.Datetime, 'now', return_value=slot2 + timedelta(minutes=1)):
            self.env['dac.web.lead']._send_summary(slot2)
        self.assertEqual(len(summary(self.channel)), 1)
        self.assertEqual(len(summary(test_channel)), 1)

    # ── Thống kê khách mới + trùng số với Pancake ────────────────────────────────

    def test_new_pancake_customer_is_counted_once_even_when_answered_quickly(self):
        Contact = self.env['dac.new.contact']
        fresh = self.conversation(name='Khách mới', fm_id='c-fresh')
        self.message(fresh, T0)
        self.message(fresh, T0 + timedelta(minutes=1), 'staff')  # trả lời nhanh: vẫn là một lượt khách mới
        staff_first = self.conversation(name='Nhân viên nhắn trước', fm_id='c-out')
        self.message(staff_first, T0 - timedelta(minutes=30), 'staff')
        self.message(staff_first, T0, text='Ok em')
        birthday = self.conversation(name='Sinh nhật', fm_id='c-bd')
        self.message(birthday, T0, text='12/11 Sinh nhật của Ai Đó Hãy gửi lời chúc tốt đẹp!')
        self.scan(T0 + timedelta(minutes=2))
        self.scan(T0 + timedelta(minutes=4))
        contacts = Contact.search([])
        self.assertEqual(contacts.mapped('name'), ['Khách mới'])
        self.assertEqual((contacts.source, contacts.contact_at, contacts.counts_as_new, contacts.page_id), ('pancake', T0, True, self.page))

    def test_old_conversations_before_activation_are_not_counted_until_backfilled(self):
        self.config.page_ids = [(6, 0, self.page.ids)]  # DB thử có thể đã có hội thoại của page khác
        old = self.conversation(name='Khách cũ', fm_id='c-old')
        self.message(old, T0 - timedelta(days=3))
        later = self.conversation(name='Khách sau', fm_id='c-later')
        self.message(later, T0)
        self.scan(T0 + timedelta(minutes=2))
        self.assertEqual(self.env['dac.new.contact'].search([]).mapped('name'), ['Khách sau'])
        Contact = self.env['dac.new.contact']
        Contact._backfill(self.config, T0 - timedelta(days=5))
        pancake = Contact.search([('source', '=', 'pancake')])
        self.assertEqual(sorted(pancake.mapped('name')), ['Khách cũ', 'Khách sau'])
        total = Contact.search_count([])
        self.assertEqual(Contact._backfill(self.config, T0 - timedelta(days=5)), 0, 'tính lại không tạo trùng')
        self.assertEqual(Contact.search_count([]), total)

    def test_lead_with_phone_already_on_pancake_is_flagged_not_new_and_warned_in_zalo(self):
        buyer = self.env['res.partner'].create({'name': 'Chị Hoa', 'phone': '+84 901 234 567'})
        conv = self.conversation(name='Chị Hoa', fm_id='c-hoa')
        conv.partner_id = buyer
        self.message(conv, T0 - timedelta(days=2))
        lead = self.lead(phone='0901 234 567', customer_name='Hoa', received_at=T0)
        self.assertEqual(lead.pancake_conversation_ids, conv)
        self.assertIn('Pancake', lead.pancake_match_note)
        contact = self.env['dac.new.contact'].search([('lead_id', '=', lead.id)])
        self.assertEqual((contact.seen_before, contact.counts_as_new), (True, False))
        text, _styles = lead._zalo_message()
        lines = text.splitlines()
        self.assertEqual(lines[-2], '⚠️ Số này đã nhắn Pancake trước đó: Chị Hoa — https://pancake.vn/%s?c_id=c-hoa' % PAGE_ID)
        self.assertTrue(lines[-1].startswith('Xem chi tiết: '))

    def test_lead_phone_found_in_message_text_and_repeat_leads_are_detected(self):
        conv = self.conversation(name='Anh Dũng', fm_id='c-dung')
        self.message(conv, T0 - timedelta(days=1), text='số mình 0977 123 456', phone_info=[{'phone_number': '0977123456', 'captured': '0977123456'}])
        self.message(conv, T0 - timedelta(days=1, minutes=-1), 'staff', text='Shop: 0909 999 999', phone_info=[{'phone_number': '0909999999'}])
        self.env['dac.pancake.phone']._capture([m for m in conv.message_ids if classify(m) == 'customer'])
        self.assertEqual(self.env['dac.pancake.phone'].search([]).mapped('phone'), ['0977123456'], 'số trong tin nhân viên không lấy')
        first = self.lead(phone='+84977123456', received_at=T0)
        self.assertTrue(first.pancake_match_note)
        fresh = self.lead(phone='0933444555', received_at=T0 + timedelta(minutes=1))
        self.assertFalse(fresh.pancake_match_note)
        self.assertTrue(self.env['dac.new.contact'].search([('lead_id', '=', fresh.id)]).counts_as_new)
        again = self.lead(phone='0933 444 555', received_at=T0 + timedelta(hours=1))
        self.assertIn('điền form', again.pancake_match_note)
        stats = self.env['dac.new.contact'].search([('lead_id', 'in', (first + fresh + again).ids)])
        self.assertEqual(sorted(stats.mapped('counts_as_new')), [False, False, True])

    def test_test_leads_and_leads_without_phone_never_count_as_new(self):
        test_lead = self.lead(customer_name='test', phone='0999999999')
        no_phone = self.lead(phone=False, customer_name='Chỉ tên')
        stats = self.env['dac.new.contact'].search([('lead_id', 'in', (test_lead + no_phone).ids)])
        self.assertEqual(stats.filtered(lambda c: c.lead_id == test_lead).counts_as_new, False)
        self.assertEqual(stats.filtered(lambda c: c.lead_id == no_phone).counts_as_new, True)

    def test_a_failing_pancake_check_never_blocks_lead_intake(self):
        with patch('odoo.addons.dac_pancake_remind.models.web_lead.WebLeadPancake._check_pancake', side_effect=RuntimeError('boom')):
            lead = self.lead()
        self.assertTrue(lead.exists())

    def test_dashboard_data_counts_real_new_contacts_by_channel_day_and_hour(self):
        Contact = self.env['dac.new.contact'].sudo()
        Contact.search([]).unlink()
        zalo = self.conversation(fm_id='d-z')
        zalo.platform = 'zalo'
        fb = self.conversation(fm_id='d-f')
        fb.platform = 'facebook'
        now = datetime(2026, 11, 17, 9, 0)  # 16:00 giờ VN
        Contact.create([
            {'contact_at': now - timedelta(hours=1), 'source': 'web_lead', 'name': 'Form hôm nay'},
            {'contact_at': now - timedelta(days=2), 'source': 'pancake', 'name': 'Zalo', 'conversation_id': zalo.id, 'page_id': self.page.id},
            {'contact_at': now - timedelta(days=2, hours=1), 'source': 'pancake', 'name': 'FB', 'conversation_id': fb.id},
            {'contact_at': now - timedelta(days=3), 'source': 'web_lead', 'name': 'Test', 'is_test': True},
            {'contact_at': now - timedelta(days=10), 'source': 'web_lead', 'name': 'Kỳ trước'},
        ])
        with patch.object(fields.Datetime, 'now', return_value=now):
            data = Contact.dashboard_data('7d')
        self.assertEqual((data['total'], data['previous'], data['today']), (3, 1, 1))
        self.assertEqual(data['channels'], {'web': 1, 'zalo': 1, 'messenger': 1, 'other': 0})
        self.assertEqual(len(data['series']), 7, 'đủ 7 ngày kể cả ngày không có khách')
        self.assertEqual(data['series'][-1]['web'], 1)
        self.assertEqual((data['hours'][15], data['hours'][16]), (2, 1), 'giờ Việt Nam')
        self.assertEqual([r['name'] for r in data['recent']], ['Form hôm nay', 'Zalo', 'FB', 'Kỳ trước'])
        with patch.object(fields.Datetime, 'now', return_value=now):
            year = Contact.dashboard_data('12m')
        self.assertEqual((len(year['series']), year['series'][0]['label'], year['series'][-1]['label']), (12, '12/25', '11/26'))
