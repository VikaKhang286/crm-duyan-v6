import json
import re
from datetime import date, datetime, timedelta
from unittest.mock import patch

from odoo.exceptions import AccessError, UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

from odoo.addons.dac_chat_playbook.models import chat_stats as cs
from odoo.addons.dac_chat_playbook.models.chat_insight import LABEL_SCHEMA

LABEL_HINT = LABEL_SCHEMA['properties']['items']['items']['properties']['summary']

PAGE_ID = 'pzl_chat_playbook_page'
# Thứ ba 10/11/2026 10:00 giờ VN = 03:00 UTC (trong giờ làm việc); Chủ nhật 15/11/2026 20:00 VN = 13:00 UTC (ngoài giờ)
T_IN = datetime(2026, 11, 10, 3, 0, 0)
T_OUT = datetime(2026, 11, 15, 13, 0, 0)
JSON_CALL = 'odoo.addons.dac_ai_core.models.ai_connection.AiConnection._json'


def msg(at, role, text='', staff='', media=False, phone=False, images=()):
    return cs.Msg(at, role, staff, text, media, phone, images)


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestChatStats(TransactionCase):
    """Bộ máy thống kê (Python thuần)."""

    def test_turns_group_consecutive_staff_messages_and_split_after_gap(self):
        m = lambda minutes, role: msg(T_IN + timedelta(minutes=minutes), role, 'x', staff='Bang' if role == 'staff' else '')
        turns = cs.build_turns([m(0, 'customer'), m(1, 'customer'), m(4, 'staff'), m(5, 'staff'), m(40, 'staff'), m(41, 'customer')])
        self.assertEqual([t.role for t in turns], ['customer', 'staff', 'staff', 'customer'])
        self.assertEqual(len(turns[1].messages), 2, 'hai tin liền nhau trong 20 phút là một lượt')
        self.assertEqual(turns[1].response_minutes, 4.0, 'tính từ tin đầu tiên khách đang chờ')
        self.assertIsNone(turns[2].response_minutes, 'lượt nhân viên nhắn tiếp không phải trả lời khách')

    def test_bot_messages_are_ignored(self):
        turns = cs.build_turns([msg(T_IN, 'customer', 'hi'), msg(T_IN, 'bot', 'auto'), msg(T_IN + timedelta(minutes=2), 'staff', 'Dạ')])
        self.assertEqual([t.role for t in turns], ['customer', 'staff'])

    def test_analyzer_measures_style_outcome_and_after_hours(self):
        analyzer = cs.Analyzer(T_IN - timedelta(days=1), T_OUT + timedelta(days=1))
        # Hội thoại tốt trong giờ: trả lời sau 3 phút, 2 tin, hỏi kích thước, chốt bằng số tài khoản
        analyzer.add({'id': 1, 'inserted_at': T_IN}, [
            msg(T_IN, 'customer', 'Cho mình hỏi làm bảng hiệu'),
            msg(T_IN + timedelta(minutes=3), 'staff', 'Dạ em chào chị ạ', 'Tieu Bang'),
            msg(T_IN + timedelta(minutes=3, seconds=20), 'staff', 'Chị cho em xin kích thước bảng nha', 'Tieu Bang'),
            msg(T_IN + timedelta(minutes=10), 'customer', '3m x 1m, sdt 0912 345 678', phone=True),
            msg(T_IN + timedelta(minutes=15), 'staff', 'Dạ em gửi số tài khoản chị chuyển cọc giúp em nha', 'Tieu Bang'),
        ])
        # Khách nhắn Chủ nhật tối, không ai trả lời
        analyzer.add({'id': 2, 'inserted_at': T_OUT}, [msg(T_OUT, 'customer', 'alo shop')])
        report = analyzer.report()
        overview = report['overview']
        self.assertEqual(overview['new_conversations'], 2)
        self.assertEqual(overview['new_answered_pct'], 50.0)
        self.assertEqual(overview['new_after_hours_pct'], 50.0)
        self.assertEqual(overview['new_deal_pct'], 50.0)
        self.assertEqual(overview['new_phone_pct'], 50.0)
        self.assertEqual(overview['first_response_in_hours_median'], 3.0)
        staff = report['staff']
        self.assertFalse(staff, 'nhân viên dưới 5 lượt không lên bảng (ít mẫu)')
        info = {row['key']: row for row in report['info_questions']}
        self.assertEqual(info['size']['asked_pct'], 100.0)
        self.assertEqual(info['size']['median_turn'], 1)
        effects = {e['feature']: {r['value']: r for r in e['rows']} for e in report['effects']}
        self.assertEqual(effects['Số tin trong lượt đầu']['2 tin']['deal_pct'], 100.0)
        self.assertIn('Có', effects['Có lời chào'])
        self.assertEqual(report['new_by_weekday']['Chủ nhật'], 1)
        self.assertEqual(len(report['samples']), 1, 'chỉ hội thoại đã có nhân viên trả lời mới làm mẫu cho AI')
        sample = report['samples'][0]
        self.assertEqual((sample['new'], sample['deal'], sample['staff'], sample['messages']), (True, True, 'Tieu Bang', 5))
        self.assertEqual(sample['first_response'], 3.0)
        # Khách cũ (bắt đầu trước kỳ) vẫn làm mẫu, đánh dấu new=False
        analyzer.add({'id': 3, 'inserted_at': T_IN - timedelta(days=30)}, [
            msg(T_IN, 'customer', 'Đơn hôm trước sao rồi em'), msg(T_IN + timedelta(minutes=1), 'staff', 'Dạ đang in ạ', 'Dao')])
        self.assertFalse(analyzer.report()['samples'][-1]['new'])

    def test_transcript_masks_phone_and_marks_roles(self):
        text = cs.transcript([msg(T_IN, 'customer', 'số em 0912.345.678'), msg(T_IN + timedelta(minutes=2), 'staff', 'Dạ', 'Bang', media=True)])
        self.assertIn('[SĐT]', text)
        self.assertNotIn('0912', text)
        self.assertIn('+2p [NV Bang] Dạ [ảnh/file]', text)
        labelled = cs.transcript([msg(T_IN, 'customer', 'mẫu nè', images=['https://x/a.jpg', 'https://x/b.jpg'])],
                                 image_labels={'https://x/a.jpg': 'Ảnh 7.1'})
        self.assertIn('[KH] mẫu nè [Ảnh 7.1]', labelled)

    def test_pick_samples_modes(self):
        samples = [{'id': i, 'good': i % 2 == 0, 'engaged': i % 3 != 0, 'score': i, 'last_at': '2026-11-%02d 10:00:00' % (i + 1)}
                   for i in range(10)]
        self.assertEqual([s['id'] for s in cs.pick_samples(samples, 3, 'recent')], [9, 8, 7])
        self.assertEqual([s['id'] for s in cs.pick_samples(samples, 2, 'good')], [9, 8])
        self.assertTrue(all(not s['engaged'] for s in cs.pick_samples(samples, 3, 'bad')))

    def test_pick_samples_mixes_good_bad_and_after_hours(self):
        samples = [{'id': i, 'good': i < 10, 'deal': False, 'engaged': i % 3 != 0, 'after_hours': i % 4 == 0, 'staff_turns': 3, 'score': i % 7}
                   for i in range(40)]
        picked = cs.pick_samples(samples, 10)
        self.assertEqual(len(picked), 10)
        self.assertEqual(len({p['id'] for p in picked}), 10)
        self.assertTrue(any(p['good'] for p in picked) and any(not p['engaged'] for p in picked))


class ChatFixtures(TransactionCase):
    """Dữ liệu mẫu và hàm phụ dùng chung (không có test)."""


    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.env['dac.ai.connection'].search([]).write({'active': False})  # không gọi AI thật
        cls.page = cls.env['dac.pancake.page'].create({'name': 'Page phân tích', 'page_fm_id': PAGE_ID})
        cls.seq = 0
        # 25 hội thoại khách mới trong giờ, nhân viên "Tieu Bang" trả lời 1 tin ngắn trong 2 phút
        for i in range(25):
            start = T_IN + timedelta(minutes=10 * i)
            conv = cls.conversation('c%s' % i, start)
            cls.message(conv, start, 'customer', 'Cho em hỏi in decal')
            cls.message(conv, start + timedelta(minutes=2), 'staff', 'Dạ chị cho em xin kích thước ạ')
            if i % 5:  # 20/25 hội thoại chốt (đủ mẫu để đề xuất quy tắc từ thống kê)
                cls.message(conv, start + timedelta(minutes=5), 'customer', 'Dạ 1m x 2m')
                cls.message(conv, start + timedelta(minutes=8), 'staff', 'Dạ em gửi số tài khoản chị đặt cọc giúp em nha')
        internal = cls.conversation('noi-bo', T_IN)
        internal.is_internal = True
        cls.message(internal, T_IN, 'customer', 'tin nội bộ không được tính')

    @classmethod
    def conversation(cls, fm_id, start):
        return cls.env['dac.pancake.conversation'].create({
            'conversation_fm_id': fm_id, 'page_id': cls.page.id, 'customer_name': 'Khách %s' % fm_id, 'platform': 'Zalo',
            'inserted_at_fm': start, 'updated_at_fm': start + timedelta(hours=1)})

    @classmethod
    def message(cls, conversation, at, who, text):
        cls.seq += 1
        sender = {'id': 'pzl_u_%s' % conversation.id, 'name': conversation.customer_name}
        if who == 'staff':
            sender = {'id': PAGE_ID, 'name': 'Duy An', 'admin_id': 'a1', 'admin_name': 'Tieu Bang'}
        raw = {'id': 'm%s' % cls.seq, 'type': 'INBOX', 'page_id': PAGE_ID, 'from': sender, 'attachments': []}
        return cls.env['dac.pancake.message'].create({
            'message_fm_id': raw['id'], 'conversation_id': conversation.id, 'content_html': '<div>%s</div>' % text, 'type_content': 'text',
            'inserted_at': at, 'raw_json': json.dumps(raw), 'sender_name': sender['name']})

    def analysis(self, **vals):
        return self.env['dac.chat.analysis'].create(dict({
            'name': 'Thử', 'date_from': date(2026, 11, 9), 'date_to': date(2026, 11, 12), 'page_ids': [(6, 0, self.page.ids)],
            'summary_scope': 'this_analysis'}, **vals))

    def run_until_done(self, analysis, steps=20):
        analysis.write({'state': 'queued'})
        for _ in range(steps):
            if analysis.state not in ('queued', 'ai'):
                break
            analysis._process_step()
        return analysis

    def ai_connection(self, name='model-chi-dinh'):
        connection = self.env['dac.ai.connection'].create({'name': 'Thử', 'kind': 'openai', 'base_url': 'https://ai.example.com/v1',
                                                           'api_key': 'k', 'active': True})
        return self.env['dac.ai.model'].create({'connection_id': connection.id, 'name': name, 'selected': True, 'verified': True})

    @staticmethod
    def fake_labels(text):
        ids = [int(i) for i in re.findall(r'### Hội thoại (\d+)', text)]
        return {'items': [{'conversation_id': cid, 'summary': 'Khách hỏi in decal', 'product': 'decal', 'customer_kind': 'moi',
                           'intent': 'hoi_gia', 'outcome': 'chot_don' if n % 2 == 0 else 'mat_khach',
                           **({} if n % 2 == 0 else {'lost_reason': 'gia_cao'}), 'staff_name': 'Tieu Bang, Dao',
                           'info': {'size': '1m x 2m', 'bogus': 'x'}, 'scores': {'greeting': 4, 'discovery': 5, 'closing': 9},
                           'staff_improve': 'Chưa hỏi thời gian cần hàng'} for n, cid in enumerate(ids)] + [{'conversation_id': 999999}],
                'omit_defaults': True,
                'patterns': ['Hỏi kích thước ngay lượt đầu']}

    @staticmethod
    def openai_reply(name, args, call_id='c1', usage=None):
        reply = {'choices': [{'message': {'content': '', 'tool_calls': [
            {'id': call_id, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}}]}
        if usage:
            reply['usage'] = {'prompt_tokens': usage[0], 'completion_tokens': usage[1]}
        return reply


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestChatAnalysis(ChatFixtures):
    def test_stats_only_run_builds_report_staff_lines_and_stats_rules(self):
        analysis = self.run_until_done(self.analysis(ai_conversation_limit=0))
        self.assertEqual(analysis.state, 'done')
        self.assertEqual(analysis.new_conversation_count, 25, 'hội thoại nội bộ bị loại')
        line = analysis.staff_line_ids
        self.assertEqual(line.name, 'Tieu Bang')
        self.assertEqual(line.messages_per_turn, 1.0)
        self.assertEqual(line.new_first_replies, 25)
        self.assertAlmostEqual(line.new_deal_pct, 80.0)
        self.assertIn('Tieu Bang', analysis.report_html)
        self.assertIn('Kiểu trả lời đầu tiên', analysis.report_html)
        rules = analysis.rule_ids
        self.assertTrue(rules and set(rules.mapped('source')) == {'stats'} and set(rules.mapped('state')) == {'proposed'})
        self.assertIn('style', rules.mapped('category'), 'đủ 20 hội thoại tốt → đề xuất độ dài/số tin mỗi lượt')

    def test_designated_model_labels_coordinates_with_tools_then_writes_prompt(self):
        model = self.ai_connection()
        analysis = self.analysis(ai_model_id=model.id, ai_conversation_limit=4, ai_batch_size=2)
        calls, payloads = [], []

        def fake_tool_call(conn, system, text, name, description, schema, timeout, used_model):
            calls.append((name, used_model.name, text))
            if name == 'label_conversations':
                return self.fake_labels(text)
            rule_id = int(re.search(r'\[id (\d+)\] \([^)]*\) Hỏi kích thước sớm', text).group(1))
            return {'persona': 'Bạn là trợ lý chào khách của DAC, xưng em.', 'flow_greeting': 'Chỉ chào và hỏi khách cần gì.',
                    'flow_info': 'Chào, xin kích thước rồi tóm tắt.', 'flow_consult': 'Chào, xin kích thước, tư vấn sơ bộ.',
                    'after_hours': 'Hẹn {{next_working_time}}.', 'style_rules': ['Mỗi lượt 1 tin'], 'consult_guide': 'Alu bền ngoài trời.',
                    'info_fields': [{'key': 'Kích thước', 'label': 'Kích thước', 'required': True},
                                    {'key': 'size', 'label': 'Kích thước 2'}, {'label': ''}],
                    'examples': [{'situation': 'Chủ nhật', 'customer': 'alo', 'bot': ['Dạ em chào chị ạ']}],
                    'used_rule_ids': [rule_id, 999999]}

        def fake_openai(conn, payload, timeout, used_model):
            tool_names = [t['function']['name'] for t in payload.get('tools') or []]
            if tool_names == ['label_conversations']:  # bước đọc hội thoại (không có ảnh vẫn đi đường này để lấy số token)
                text = payload['messages'][-1]['content'][0]['text']
                calls.append(('label_conversations', used_model.name, text))
                return self.openai_reply('label_conversations', self.fake_labels(text), usage=(1000, 200))
            if tool_names == ['optimize_rules']:  # Agent tối ưu quy tắc
                calls.append(('optimize_rules', used_model.name, payload['messages'][-1]['content'][0]['text']))
                return self.openai_reply('optimize_rules', {'summary': 'Không có quy tắc trùng.', 'rules': []}, usage=(300, 50))
            payloads.append(payload)
            rounds = sum(1 for m in payload['messages'] if m.get('role') == 'assistant')
            if rounds == 0:
                return self.openai_reply('label_stats', {'group_by': 'lost_reason', 'filters': {'outcome': 'mat_khach'}}, usage=(500, 30))
            if rounds == 1:
                cid = analysis.insight_ids[:1].conversation_id.id
                return self.openai_reply('read_conversation', {'conversation_id': cid}, 'c2')
            return self.openai_reply('finish', {
                'summary': 'Nhân viên nhắn ngắn, hỏi kích thước sớm; mất khách chủ yếu vì giá.',
                'bot_scope': 'Bot lo phần chào và xin kích thước; khách hỏi đơn cũ chuyển người.',
                'rules': [{'category': 'info', 'title': 'Hỏi kích thước sớm', 'rule': 'Lượt đầu xin kích thước.', 'priority': 'should',
                           'example': 'Dạ chị cho em xin kích thước ạ, giá 1.200.000đ', 'evidence': 'Gọi 0912345678'},
                          {'category': 'không-có', 'title': 'Nhóm lạ', 'rule': 'Vẫn lưu, đưa về tư vấn', 'priority': 'x'},
                          {'title': 'Thiếu nội dung'}],
                'staff_reviews': [{'staff_name': 'tieu bang', 'overall': 4, 'strengths': 'Trả lời nhanh',
                                   'improvements': 'Hỏi thêm thời gian cần hàng', 'coaching': 'Hỏi hạn cần hàng ngay lượt 2'}]}, 'c3')

        with patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._tool_call', fake_tool_call), \
             patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._openai', fake_openai), \
             patch.object(type(self.env['dac.ai.model']), '_note', lambda *a, **k: None):
            self.run_until_done(analysis)
        self.assertEqual(analysis.state, 'done', analysis.error_message)
        self.assertEqual([c[0] for c in calls], ['label_conversations', 'label_conversations', 'optimize_rules', 'write_chatbot_prompt'])
        self.assertIn('Hỏi kích thước sớm', calls[2][2], 'Agent tối ưu đọc cả quy tắc AI vừa đề xuất')
        self.assertIn('ĐỊNH HƯỚNG CỦA QUẢN LÝ', calls[2][2])
        self.assertIn('nghỉ trưa', calls[3][2], 'Agent viết prompt đọc định hướng của quản lý')
        self.assertIn('Không có quy tắc trùng', analysis.rules_review_summary)
        self.assertTrue(all(c[1] == 'model-chi-dinh' for c in calls), 'chỉ dùng model được chỉ định')
        self.assertEqual(analysis.candidate_count, 20, 'bỏ hội thoại dưới 4 tin')
        # Nhãn từng hội thoại
        insights = analysis.insight_ids
        self.assertEqual(len(insights), 4, 'bỏ id hội thoại AI tự bịa')
        self.assertEqual(set(insights.mapped('staff_name')), {'Tieu Bang'})
        self.assertEqual(insights[0].score_closing, 0, 'điểm ngoài 1–5 bị bỏ')
        self.assertEqual(json.loads(insights[0].info_json), {'size': '1m x 2m'}, 'chỉ giữ khóa thông tin hợp lệ')
        self.assertAlmostEqual(insights[0].score_avg, 4.5)
        self.assertTrue(all(i.messages_until for i in insights))
        # Agent điều phối dùng tool rồi mới kết luận
        self.assertEqual(len(payloads), 3)
        self.assertIn('Không báo giá', payloads[0]['messages'][1]['content'], 'quy tắc an toàn đưa cho Agent')
        tool_output = [m for m in payloads[1]['messages'] if m.get('role') == 'tool'][0]['content']
        self.assertIn('Giá cao', tool_output, 'kết quả label_stats trả lại cho Agent')
        self.assertIn('transcript', [m for m in payloads[2]['messages'] if m.get('role') == 'tool'][-1]['content'])
        self.assertIn('read_conversation', analysis.coord_log)
        self.assertEqual(analysis.coord_rounds, 2)
        self.assertIn('Bot lo phần chào', analysis.coord_bot_scope)
        self.assertEqual(analysis.ai_summary, 'Nhân viên nhắn ngắn, hỏi kích thước sớm; mất khách chủ yếu vì giá.')
        # Đánh giá nhân viên
        line = analysis.staff_line_ids.filtered(lambda l: l.name == 'Tieu Bang')
        self.assertEqual((line.ai_conversations, line.ai_overall, line.ai_deal_pct), (4, 4, 50.0))
        self.assertEqual(line.ai_discovery, 5.0)
        self.assertEqual(line.ai_coaching, 'Hỏi hạn cần hàng ngay lượt 2')
        html = analysis.report_html
        self.assertIn('Đánh giá nhân viên sale', html)
        self.assertIn('Phân loại hội thoại', html)
        # Quy tắc AI
        ai_rules = analysis.rule_ids.filtered(lambda r: r.source == 'ai')
        self.assertEqual(len(ai_rules), 2, 'bỏ quy tắc thiếu nội dung')
        first = ai_rules.filtered(lambda r: r.name == 'Hỏi kích thước sớm')
        self.assertNotIn('1.200.000', first.example, 'câu mẫu không được chứa giá')
        self.assertNotIn('0912345678', first.evidence)
        self.assertEqual(ai_rules.filtered(lambda r: r.name == 'Nhóm lạ').category, 'consult')
        self.assertEqual(set(ai_rules.mapped('state')), {'proposed'}, 'AI chỉ đề xuất, chưa có hiệu lực')
        # Agent viết prompt có phạm vi chatbot
        self.assertIn('Bot lo phần chào', calls[-1][2])
        prompt = analysis.prompt_id
        self.assertEqual(sorted(analysis.prompt_ids.mapped('mode')), ['consult', 'greeting', 'info'], '3 bộ: Chào hỏi, Hỏi thông tin, Tư vấn')
        self.assertEqual(prompt.mode, 'consult')
        greeting = analysis.prompt_ids.filtered(lambda p: p.mode == 'greeting')
        basic = analysis.prompt_ids.filtered(lambda p: p.mode == 'info')
        self.assertEqual(greeting._info_list(), [])
        self.assertIn('KHÔNG xin thông tin chi tiết', greeting.system_prompt)
        self.assertIn('Chỉ chào và hỏi khách cần gì.', greeting.system_prompt)
        self.assertNotIn('Alu bền ngoài trời', greeting.system_prompt, 'bộ chào hỏi không tư vấn')
        self.assertEqual([f['key'] for f in basic._info_list()], ['kich_thuoc', 'size'], 'Hỏi thông tin: đủ danh sách')
        self.assertIn('đã đủ các thông tin bắt buộc', basic._runtime_prompt(T_IN))
        self.assertIn('Chào, xin kích thước rồi tóm tắt.', basic.system_prompt)
        # Kiến thức doanh nghiệp theo bộ: Chào hỏi chỉ thông tin chung, Tư vấn có đủ sản phẩm + chính sách
        greet_rt, info_rt, consult_rt = (p._runtime_prompt(T_IN) for p in (greeting, basic, prompt))
        self.assertIn('189C Nguyễn Chí Thanh', greet_rt)
        self.assertNotIn('Hộp đèn, LED', greet_rt)
        self.assertIn('Hộp đèn, LED', info_rt)
        self.assertNotIn('Bảo hành:', info_rt)
        self.assertIn('Bảo hành:', consult_rt)
        self.assertIn('KIẾN THỨC DOANH NGHIỆP', calls[-1][2], 'Agent viết prompt được đọc kiến thức')
        self.assertEqual(prompt.state, 'draft')
        self.assertEqual(prompt.rule_ids, first, 'chỉ quy tắc Agent chọn và có thật')
        self.assertEqual([f['key'] for f in prompt._info_list()], ['kich_thuoc', 'size'], 'key được làm sạch, bỏ dòng thiếu tên')
        runtime = prompt._runtime_prompt(T_OUT, 'Chị Lan')
        self.assertNotIn('{{', runtime, 'mọi biến đã được điền')
        self.assertIn('8:00 sáng mai (thứ 2, 16/11)', runtime, 'Chủ nhật tối → hẹn sáng thứ 2, do code tính')
        self.assertIn('Không báo giá', runtime, 'quy tắc cấm được code chèn nguyên văn')
        old = self.env['dac.chat.prompt'].create({'name': 'Cũ', 'system_prompt': 'x', 'state': 'draft'})
        old.action_activate()
        greeting.action_activate()
        prompt.action_activate()
        self.assertEqual((old.state, prompt.state, greeting.state), ('archived', 'active', 'active'), 'mỗi bộ một bản đang dùng')
        self.assertEqual(self.env['dac.chat.prompt']._active('greeting'), greeting)
        self.assertEqual(first.state, 'proposed', 'kích hoạt prompt không tự duyệt quy tắc đi kèm')
        # Nhật ký tiến độ
        self.assertEqual(analysis.progress_pct, 100)
        self.assertIn('Xong lô 1/2', analysis.progress_log)
        self.assertIn('Agent điều phối vòng 1/8', analysis.progress_log)
        self.assertIn('Đã có 3 bộ system prompt', analysis.progress_log)
        self.assertEqual((analysis.tokens_in, analysis.tokens_out), (2800, 480), 'cộng token 2 lô đọc + vòng Agent + rà soát')
        self.assertIn('1.000 token vào / 200 token ra', analysis.progress_log)
        self.assertIn('TỐI ĐA 15 từ', json.dumps(LABEL_HINT, ensure_ascii=False))
        self.assertNotIn('good_phrase', json.dumps(LABEL_SCHEMA), 'bớt trường để giảm token ra')
        self.assertEqual(set(insights.mapped('bot_fit')), {'mo_dau'})
        # Lần sau chỉ đọc hội thoại chưa đọc
        again = self.run_until_done(self.analysis(ai_conversation_limit=0))
        self.assertEqual(again.candidate_count, 16, '4 hội thoại đã đọc bị bỏ qua')
        everything = self.run_until_done(self.analysis(ai_conversation_limit=0, only_unread=False))
        self.assertEqual(everything.candidate_count, 20)

    def test_images_are_sent_to_vision_model_or_skipped(self):
        model = self.ai_connection()
        start = T_IN + timedelta(hours=5)
        conv = self.conversation('co-anh', start)
        self.message(conv, start, 'customer', 'Em muốn làm bảng như mẫu này')
        photo = self.message(conv, start + timedelta(seconds=30), 'customer', '')
        raw = json.loads(photo.raw_json)
        raw['attachments'] = [{'type': 'photo', 'url': 'https://content.pancake.vn/mau.jpg'}]
        photo.write({'raw_json': json.dumps(raw), 'type_content': 'photo'})
        self.message(conv, start + timedelta(minutes=2), 'staff', 'Dạ mẫu này làm alu được ạ')
        self.message(conv, start + timedelta(minutes=4), 'customer', 'Dạ ok')
        seen = {}

        def fake_openai(conn, payload, timeout, used_model):
            content = payload['messages'][-1]['content'] if payload['messages'] else ''
            tool_names = [t['function']['name'] for t in payload.get('tools') or []]
            if tool_names == ['review_rules']:
                return self.openai_reply('review_rules', {'summary': 'ok'})
            if isinstance(content, list) and any(part.get('type') == 'image_url' for part in content):  # gọi gán nhãn có ảnh
                seen['vision'] = content
                if seen.get('reject'):
                    raise UserError('Nhà cung cấp AI từ chối yêu cầu (HTTP 400).')
                text = content[0]['text']
                return self.openai_reply('label_conversations', dict(self.fake_labels(text), items=[
                    dict(self.fake_labels(text)['items'][0], image_facts='Ảnh mẫu bảng alu nền trắng')]))
            if tool_names == ['label_conversations']:  # lần gọi lại chỉ chữ
                return self.openai_reply('label_conversations', self.fake_labels(content[0]['text'] if isinstance(content, list) else content))
            return self.openai_reply('finish', {'summary': 'ok', 'rules': []})

        tool = lambda conn, system, text, name, *a: self.fake_labels(text) if name == 'label_conversations' else {'summary': 'x', 'rules': []}
        from odoo.exceptions import UserError
        for reject in (False, True):
            seen.clear()
            seen['reject'] = reject
            self.env['dac.chat.insight'].search([]).unlink()
            analysis = self.analysis(ai_model_id=model.id, ai_conversation_limit=1, read_mode='recent', customer_scope='all')
            with patch('odoo.addons.dac_chat_playbook.models.chat_analysis.fetch_image', return_value='QUJD'), \
                 patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._openai', fake_openai), \
                 patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._tool_call', tool), \
                 patch.object(type(self.env['dac.ai.model']), '_note', lambda *a, **k: None):
                self.run_until_done(analysis)
            self.assertEqual(analysis.state, 'done', analysis.error_message)
            insight = analysis.insight_ids
            self.assertEqual(insight.conversation_id, conv, 'chế độ "gần nhất" chọn hội thoại mới nhất')
            parts = seen['vision']
            self.assertIn('[Ảnh %s.1]' % conv.id, parts[0]['text'], 'đoạn chat đánh dấu vị trí ảnh')
            self.assertEqual(parts[-1], {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,QUJD'}})
            if reject:
                self.assertEqual((analysis.images_sent, insight.images_read), (0, 0), 'model không nhận ảnh → đọc chỉ chữ')
            else:
                self.assertEqual((analysis.images_sent, insight.images_read), (1, 1))
                self.assertEqual(insight.image_facts, 'Ảnh mẫu bảng alu nền trắng')

    def test_designated_model_failure_falls_back_to_shared_chain_or_fails(self):
        from odoo.exceptions import UserError
        model = self.ai_connection('model-hong')

        def broken(*args, **kwargs):
            raise UserError('HTTP 500')

        def fallback(self_, system, text, schema, name='report', **kw):
            if name == 'label_conversations':
                return self.fake_labels(text), 'openai'
            return {'summary': 'ok', 'rules': []}, 'openai'

        analysis = self.analysis(ai_model_id=model.id, ai_conversation_limit=2, ai_batch_size=2)
        with patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._tool_call', broken), \
             patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._openai', broken), \
             patch(JSON_CALL, fallback), patch.object(type(self.env['dac.ai.model']), '_note', lambda *a, **k: None):
            self.run_until_done(analysis)
        self.assertEqual(analysis.state, 'done', analysis.error_message)
        self.assertIn('thứ tự chung', analysis.ai_used_models)
        self.assertEqual(analysis.coord_failures, 1, 'Agent lỗi 2 lần → tổng hợp một lần không tool')
        self.assertEqual(analysis.ai_summary, 'ok')

        strict = self.analysis(ai_model_id=model.id, ai_conversation_limit=2, ai_batch_size=2, ai_fallback=False, only_unread=False)
        strict.write({'state': 'queued'})
        with patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._tool_call', broken), \
             patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._openai', broken), \
             patch.object(type(self.env['dac.ai.model']), '_note', lambda *a, **k: None):
            strict._process_step()  # thống kê
            with self.assertRaises(UserError):
                strict._process_step()  # lô AI: model lỗi, không cho dùng thứ tự chung

    def test_vision_retries_with_fewer_images_then_text_only(self):
        from odoo.addons.dac_chat_playbook.models import ai_agent
        from odoo.exceptions import UserError
        model = self.ai_connection()
        sizes = []

        def flaky(connection, item, system, text, images, *args):
            sizes.append(len(images))
            if len(images) > 8:
                raise UserError('Không kết nối được tới nhà cung cấp AI.')
            return {'items': []}, {'in': 10, 'out': 2}

        images = [('[Ảnh 1.%s] (hội thoại 1)' % i, 'QUJD') for i in range(12)]
        with patch.object(ai_agent, '_vision_once', flaky):
            data, label, sent, usage = ai_agent.vision_call(self.env, model, True, 's', 't', images, {}, 'x', 'd', (1, 1))
        self.assertEqual((sizes, sent, label, usage), ([12, 8], 8, model.display_name, {'in': 10, 'out': 2}),
                         'lỗi → thử lại với 8 ảnh, cùng model')
        sizes.clear()
        with patch.object(ai_agent, '_vision_once', side_effect=UserError('HTTP 400')), \
             patch('odoo.addons.dac_ai_core.models.ai_connection.AiConnection._tool_call', return_value={'items': []}) as text_call, \
             patch.object(type(self.env['dac.ai.model']), '_note', lambda *a, **k: None):
            data, label, sent, usage = ai_agent.vision_call(self.env, model, True, 's', 't', images, {}, 'x', 'd', (1, 1))
        self.assertEqual((sent, usage), (0, {}))
        self.assertIn('Ảnh không gửi được', text_call.call_args[0][1], 'cuối cùng đọc chỉ chữ')

    def test_batches_packed_by_image_count_and_limit(self):
        from psycopg2 import IntegrityError
        analysis = self.analysis(ai_batch_size=12, images_per_conversation=10)
        samples = [{'id': i, 'images': n} for i, n in enumerate([25, 10, 10, 10, 3, 0])]
        self.assertEqual([[s['id'] for s in b] for b in analysis._pack_batches(samples)], [[0, 1, 2], [3, 4, 5]],
                         'mỗi lô tối đa 30 ảnh (mỗi hội thoại tính tối đa 10)')
        analysis.read_images = False
        self.assertEqual(len(analysis._pack_batches(samples)), 1, 'không xem ảnh → chỉ chia theo số hội thoại')
        with self.assertRaises(IntegrityError), mute_logger('odoo.sql_db'), self.env.cr.savepoint():
            analysis.images_per_conversation = 11
            analysis.flush_recordset()

    def test_weekly_cron_creates_unread_run(self):
        template = self.analysis(ai_conversation_limit=0, auto_weekly=True, only_unread=False)
        with patch('odoo.addons.base.models.ir_cron.ir_cron._trigger'), patch.object(self.env.cr, 'commit'), \
             patch.object(self.env.cr, 'rollback'):
            self.env['dac.chat.analysis']._cron_weekly()
        run = self.env['dac.chat.analysis'].search([('name', 'like', 'Tự động tuần'), ('id', '>', template.id)])
        self.assertEqual(len(run), 1)
        self.assertEqual((run.state, run.only_unread, run.auto_weekly), ('queued', True, False))
        self.assertEqual((run.date_to - run.date_from).days, 6)
        self.assertEqual(run.page_ids, template.page_ids)

    def test_cron_marks_failure_without_raising(self):
        analysis = self.analysis(ai_conversation_limit=0)
        analysis.write({'state': 'queued'})
        with mute_logger('odoo.addons.dac_chat_playbook.models.chat_analysis'), \
             patch.object(type(analysis), '_compute_stats', side_effect=RuntimeError('hỏng')), \
             patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'):
            self.env['dac.chat.analysis']._cron_process()
        self.assertEqual(analysis.state, 'failed')
        self.assertIn('hỏng', analysis.error_message)

    def test_playbook_contains_only_approved_rules_grouped(self):
        Rule = self.env['dac.chat.rule']
        text = Rule._playbook_text()
        self.assertIn('## Không được làm', text)
        self.assertIn('Không báo giá', text, 'quy tắc an toàn mặc định đã duyệt')
        rule = Rule.create({'name': 'Chào bằng tên', 'category': 'greeting', 'content': 'Chào kèm tên khách.', 'example': 'Dạ em chào chị Lan ạ'})
        self.assertNotIn('Chào bằng tên', Rule._playbook_text(), 'đề xuất chưa duyệt chưa có hiệu lực')
        rule.action_approve()
        text = Rule._playbook_text()
        self.assertIn('## Chào hỏi', text)
        self.assertIn('Câu mẫu: Dạ em chào chị Lan ạ', text)
        self.assertLess(text.index('## Chào hỏi'), text.index('## Không được làm'))
        self.assertEqual(rule.approved_by_id, self.env.user)
        rule.action_reject()
        self.assertNotIn('Chào bằng tên', Rule._playbook_text())

    def test_run_button_queues_and_clears_old_proposals(self):
        analysis = self.run_until_done(self.analysis(ai_conversation_limit=0))
        proposed = analysis.rule_ids
        kept = proposed[:1]
        kept.action_approve()
        with patch('odoo.addons.base.models.ir_cron.ir_cron._trigger') as trigger:
            analysis.action_run()
        trigger.assert_called_once()
        self.assertEqual(analysis.state, 'queued')
        self.assertEqual(analysis.rule_ids, kept, 'giữ quy tắc đã duyệt, xóa đề xuất cũ')
        self.assertFalse(analysis.staff_line_ids)

    def test_manager_without_admin_rights_can_run_and_approve(self):
        manager = self.env['res.users'].create({
            'name': 'Quản lý thử', 'login': 'chat_playbook_manager',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('dac_erp.group_dac_erp_manager').id])]})
        self.assertFalse(manager.has_group('base.group_system'))
        analysis = self.analysis(ai_conversation_limit=0).with_user(manager)
        analysis.action_run()
        self.assertEqual(analysis.state, 'queued')
        analysis.sudo()._process_step()
        rule = analysis.rule_ids[:1]
        rule.action_approve()
        self.assertEqual(rule.approved_by_id, manager)
        self.assertIn(rule.name, self.env['dac.chat.rule'].with_user(manager)._playbook_text())


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestChatbotPrompt(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env['dac.ai.connection'].search([]).write({'active': False})  # không gọi AI thật / kết nối có sẵn trong DB

    def test_next_working_time_labels(self):
        self.assertEqual(cs.next_working(T_IN), (None, 'đang trong giờ làm việc'))
        self.assertEqual(cs.next_working(T_OUT)[1], '8:00 sáng mai (thứ 2, 16/11)')
        self.assertEqual(cs.next_working(datetime(2026, 11, 14, 11, 0))[1], '8:00 sáng thứ 2 (16/11)', 'tối thứ 7')
        self.assertEqual(cs.next_working(datetime(2026, 11, 10, 5, 30))[1], '13:30 chiều nay', 'nghỉ trưa thứ 3')
        self.assertEqual(cs.next_working(datetime(2026, 11, 10, 14, 0))[1], '8:00 sáng mai (thứ 4, 11/11)', '21:00 thứ 3')
        self.assertEqual(cs.next_working(T_OUT)[0], datetime(2026, 11, 16, 1, 0), 'mốc trả về là giờ UTC')

    def test_guard_blocks_price_bank_and_commitment(self):
        Prompt = self.env['dac.chat.prompt']
        ok = Prompt._guard({'messages': ['Dạ em chào chị ạ', 'Chị cần làm kích thước bao nhiêu ạ?'], 'handoff': False})
        self.assertEqual((ok['blocked'], ok['handoff']), ([], False))
        bad = Prompt._guard({'messages': ['Dạ em chào chị', 'Giá khoảng 1.500.000đ ạ', 'Chị chuyển khoản vào STK giúp em'], 'handoff': False})
        self.assertEqual(bad['messages'], ['Dạ em chào chị'])
        self.assertEqual(len(bad['blocked']), 2)
        self.assertTrue(bad['handoff'])
        only_bad = Prompt._guard({'messages': ['350k nha chị'], 'handoff': False})
        self.assertEqual(only_bad['messages'], ['Dạ em chuyển bạn phụ trách hỗ trợ mình ngay ạ.'])

    def test_sandbox_sends_history_and_shows_reply(self):
        prompt = self.env['dac.chat.prompt'].create({
            'name': 'Thử', 'system_prompt': '# Vai trò\nTrợ lý DAC. Bây giờ {{now}}; {{work_status}}; hẹn {{next_working_time}}.',
            'info_fields_json': json.dumps([{'key': 'size', 'label': 'Kích thước', 'required': True}])})
        seen = {}

        def fake_json(self_, system, text, schema, name='report', **kw):
            seen.update(system=system, text=text, schema=schema, name=name)
            return {'messages': ['Dạ em chào chị Lan ạ', 'Chị cần bảng kích thước bao nhiêu ạ?'], 'collected': {'size': ''},
                    'handoff': False}, 'openai'

        wizard = self.env['dac.chat.prompt.test'].create({
            'prompt_id': prompt.id, 'at': T_OUT, 'customer_name': 'Chị Lan',
            'conversation': 'KH: alo shop\nBot: Dạ em chào chị\nKH: em muốn làm bảng hiệu\nsố em 0912 345 678'})
        with patch(JSON_CALL, fake_json):
            wizard.action_send()
        self.assertEqual(seen['name'], 'reply_customer')
        self.assertIn('size', seen['schema']['properties']['collected']['properties'])
        self.assertIn('NGOÀI giờ', seen['system'])
        self.assertIn('8:00 sáng mai (thứ 2, 16/11)', seen['system'])
        self.assertIn('Khách: em muốn làm bảng hiệu số em [SĐT]', seen['text'], 'dòng không có tiền tố nối vào tin trước, SĐT được che')
        self.assertIn('Bot: Dạ em chào chị Lan ạ', wizard.reply)
        self.assertEqual(wizard.handoff, 'Không')
        wizard.action_continue()
        self.assertTrue(wizard.conversation.rstrip().endswith('KH:'))


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestChatDashboard(ChatFixtures):
    """Trang Tổng quan, Thiết lập, cửa sổ chạy nhanh (dùng lại dữ liệu mẫu của TestChatAnalysis)."""

    def test_new_analysis_takes_defaults_from_config(self):
        config = self.env['dac.chat.config']._get()
        model = self.ai_connection()
        config.write({'ai_model_id': model.id, 'days_back': 30, 'conversation_limit': 77, 'read_mode': 'recent',
                      'customer_scope': 'all', 'images_per_conversation': 4, 'batch_size': 6})
        self.assertEqual(self.env['dac.chat.config']._get(), config, 'một dòng thiết lập duy nhất')
        analysis = self.env['dac.chat.analysis'].create({'name': 'Mặc định'})
        self.assertEqual((analysis.ai_model_id, analysis.ai_conversation_limit, analysis.read_mode, analysis.customer_scope,
                          analysis.images_per_conversation, analysis.ai_batch_size), (model, 77, 'recent', 'all', 4, 6))
        self.assertEqual((analysis.date_to - analysis.date_from).days, 30)

    def test_run_wizard_estimates_and_starts(self):
        Wizard = self.env['dac.chat.run.wizard']
        wizard = Wizard.create({'period': '30', 'depth': 'quick'})
        self.assertIn('60 hội thoại', wizard.estimate)
        self.assertIn('Không gọi AI', Wizard.create({'depth': 'stats'}).estimate)
        with patch('odoo.addons.base.models.ir_cron.ir_cron._trigger'):
            action = wizard.action_start()
        analysis = self.env['dac.chat.analysis'].browse(action['res_id'])
        self.assertEqual((analysis.state, analysis.ai_conversation_limit), ('queued', 60))
        self.assertEqual((analysis.date_to - analysis.date_from).days, 30)
        self.assertIn('30 ngày qua', analysis.name)
        bad = Wizard.create({'period': 'custom', 'date_from': date(2026, 11, 10), 'date_to': date(2026, 11, 1)})
        with self.assertRaises(UserError):
            bad.action_start()

    def test_dashboard_data_empty_then_with_results(self):
        Analysis = self.env['dac.chat.analysis']
        Analysis.search([]).unlink()
        data = Analysis.dashboard_data()
        json.dumps(data)  # phải chuyển được sang JSON (trả về cho trình duyệt)
        self.assertFalse(data['latest'])
        self.assertFalse(data['ai'])
        self.assertEqual([s['key'] for s in data['steps']], ['ai', 'analysis', 'rules', 'prompt', 'bot'])
        self.assertFalse(next(s for s in data['steps'] if s['key'] == 'analysis')['done'])
        self.assertEqual([p['mode'] for p in data['prompts']], ['greeting', 'info', 'consult'])
        self.assertGreater(data['knowledge']['count'], 10, 'kiến thức từ duyan.vn đã nạp')
        # Đợt có AI: gán nhãn + đánh giá nhân viên
        analysis = self.run_until_done(self.analysis(ai_conversation_limit=0))
        convs = self.env['dac.pancake.conversation'].search([('page_id', '=', self.page.id), ('is_internal', '=', False)], limit=3)
        samples = {c.id: {'id': c.id, 'last_at': '2026-11-10 05:00:00', 'new': True} for c in convs}
        self.env['dac.chat.insight']._store(analysis, [
            {'conversation_id': c.id, 'summary': 's', 'product': 'decal', 'outcome': 'chot_don' if i else 'mat_khach',
             'lost_reason': 'tra_loi_cham', 'staff_name': 'Tieu Bang', 'scores': {'greeting': 4}} for i, c in enumerate(convs)],
            samples, 'model', {})
        analysis.write({'ai_reduced': True, 'ai_summary': 'Mất khách vì trả lời chậm.', 'coord_bot_scope': 'Bot chào.'})
        analysis._apply_staff_reviews([{'staff_name': 'Tieu Bang', 'overall': 4, 'coaching': 'Trả lời nhanh hơn'}])
        prompt = self.env['dac.chat.prompt'].create({'name': 'P', 'mode': 'greeting', 'system_prompt': 'x'})
        data = Analysis.dashboard_data()
        json.dumps(data)
        self.assertEqual(data['latest']['id'], analysis.id)
        self.assertEqual(data['latest']['kpis'][0]['value'], 25)
        self.assertEqual(data['ai']['summary'], 'Mất khách vì trả lời chậm.')
        outcome = {r['key']: r['count'] for r in data['ai']['outcome']}
        self.assertEqual(outcome, {'chot_don': 2, 'mat_khach': 1})
        self.assertEqual(data['ai']['lost'][0]['key'], 'tra_loi_cham')
        staff = data['ai']['staff'][0]
        self.assertEqual((staff['name'], staff['overall'], staff['coaching']), ('Tieu Bang', 4, 'Trả lời nhanh hơn'))
        self.assertTrue(next(s for s in data['steps'] if s['key'] == 'analysis')['done'])
        greeting = next(p for p in data['prompts'] if p['mode'] == 'greeting')
        self.assertEqual((greeting['draft']['id'], greeting['active']), (prompt.id, False))
        self.assertTrue(data['rules']['pending'], 'quy tắc từ thống kê đang chờ duyệt')
        action = Analysis.dashboard_open_insights(analysis.id, 'outcome', 'mat_khach')
        self.assertEqual(self.env['dac.chat.insight'].search(action['domain']).mapped('outcome'), ['mat_khach'])

    def test_weekly_cron_uses_config(self):
        config = self.env['dac.chat.config']._get()
        config.write({'auto_weekly': True, 'conversation_limit': 33})
        with patch('odoo.addons.base.models.ir_cron.ir_cron._trigger'), patch.object(self.env.cr, 'commit'), \
             patch.object(self.env.cr, 'rollback'):
            self.env['dac.chat.analysis']._cron_weekly()
        run = config.last_weekly_run_id
        self.assertEqual((run.state, run.only_unread, run.ai_conversation_limit), ('queued', True, 33))
        self.assertEqual((run.date_to - run.date_from).days, 6)


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestRuleReviewAndDemo(TransactionCase):
    def setUp(self):
        super().setUp()
        self.env['dac.ai.connection'].search([]).write({'active': False})  # không gọi AI thật / kết nối có sẵn trong DB

    def rule(self, name, state='proposed', **vals):
        return self.env['dac.chat.rule'].create(dict({'name': name, 'content': name, 'category': 'greeting', 'state': state, 'source': 'ai'}, **vals))

    def test_optimize_merges_auto_applies_safe_groups_and_waits_for_safety_rules(self):
        Rule = self.env['dac.chat.rule']
        a1 = self.rule('Tin dài 26 ký tự', 'approved', category='style')
        a2 = self.rule('Tin dưới 80 ký tự', 'approved', category='style')
        f1 = self.rule('Không báo giá', 'approved', category='forbidden', source='system')
        h1 = self.rule('Hỏi giá thì hẹn nhân viên', 'approved', category='handoff')
        a3 = self.rule('Thứ tự hỏi', 'approved', category='info')
        p1 = self.rule('Xin SĐT lượt 3', category='info')
        keep = self.rule('Chào ngay', 'approved')
        a4 = self.rule('Gửi ảnh mẫu', 'approved', category='consult')
        f2 = self.rule('Không nhận cọc', 'approved', category='forbidden')
        untouched = self.rule('Xưng em', 'approved', category='style')
        rules = a1 | a2 | f1 | h1 | a3 | p1 | keep | a4 | f2 | untouched
        data = {'summary': 'Gọn hơn.', 'rules': [
            {'source_ids': [a1.id, a2.id], 'title': 'Tin ngắn', 'rule': 'Mỗi tin 25–60 ký tự, tối đa 80. Giá 500.000đ', 'category': 'style',
             'priority': 'should', 'reason': 'Cùng chủ đề'},
            {'source_ids': [f1.id, h1.id], 'title': 'Không báo giá, hẹn nhân viên', 'rule': 'Không đưa số; xin thông tin rồi chuyển.',
             'category': 'forbidden', 'priority': 'must'},
            {'source_ids': [a3.id, p1.id], 'title': 'Thứ tự hỏi gọn', 'rule': 'Sản phẩm, kích thước, file, số lượng.', 'category': 'info',
             'priority': 'should'},
            {'source_ids': [keep.id], 'title': 'Chào ngay', 'rule': 'Chào ngay', 'category': 'greeting', 'priority': 'should'},
            {'source_ids': [a1.id], 'title': 'Trùng lần hai', 'rule': 'x', 'category': 'style', 'priority': 'should'},
            {'source_ids': [999999], 'title': 'Bịa', 'rule': 'Không có gốc', 'category': 'style', 'priority': 'should'},
        ], 'dropped': [{'rule_id': a4.id, 'reason': 'Chatbot chưa gửi được ảnh'}, {'rule_id': f2.id, 'reason': 'thử bỏ quy tắc an toàn'}]}
        result = Rule._apply_optimize(rules, data, 'model', {'in': 100, 'out': 20})
        log = result['log']
        style = a1.merged_into_id
        self.assertEqual((style.state, style.source, a1.state, a2.state, a2.merged_into_id), ('approved', 'optimized', 'rejected', 'rejected', style),
                         'nhóm thường, toàn quy tắc đã duyệt → áp dụng ngay')
        self.assertNotIn('500.000', style.content)
        safety = f1.merged_into_id
        self.assertEqual((safety.state, f1.state, h1.state), ('proposed', 'approved', 'approved'),
                         'nhóm an toàn: quy tắc cũ vẫn dùng tới khi quản lý duyệt quy tắc mới')
        info = a3.merged_into_id
        self.assertEqual((info.state, a3.state, p1.state, p1.merged_into_id), ('proposed', 'approved', 'rejected', info))
        self.assertEqual((keep.state, keep.merged_into_id.id), ('approved', False), 'quy tắc giữ nguyên câu chữ thì không đụng tới')
        self.assertEqual((a4.state, f2.state, untouched.state), ('rejected', 'approved', 'approved'),
                         'bỏ quy tắc thường; không tự bỏ quy tắc Cấm; id AI không nhắc thì giữ')
        self.assertEqual(len(log.created_ids), 3, 'không tạo quy tắc bịa hoặc dùng lại id đã gộp')
        self.assertEqual((log.state, log.before_count, log.after_count), ('done', 10, 6))
        self.assertIn('100 token vào / 20 token ra', result['message'])
        # Prompt dùng quy tắc mới thay cho quy tắc cũ đang chờ thay
        self.assertTrue(log.can_undo)
        safety.action_approve()
        self.assertEqual((f1.state, h1.state), ('rejected', 'rejected'), 'duyệt quy tắc mới → quy tắc cũ thôi dùng')
        info.action_reject()
        self.assertEqual((a3.state, a3.merged_into_id.id), ('approved', False), 'bỏ quy tắc mới → quy tắc cũ dùng tiếp')

    def test_optimize_undo_restores_rules(self):
        Rule = self.env['dac.chat.rule']
        a1, a2 = self.rule('Tin ngắn A', 'approved', category='style'), self.rule('Tin ngắn B', 'approved', category='style')
        a3 = self.rule('Gửi ảnh', 'approved', category='consult')
        result = Rule._apply_optimize(a1 | a2 | a3, {'summary': 'x', 'rules': [
            {'source_ids': [a1.id, a2.id], 'title': 'Tin ngắn', 'rule': 'Ngắn', 'category': 'style', 'priority': 'should'}],
            'dropped': [{'rule_id': a3.id, 'reason': 'không gửi được'}]})
        new = result['log'].created_ids
        self.assertEqual(new.state, 'approved')
        result['log'].action_undo()
        self.assertEqual([r.state for r in (a1, a2, a3)], ['approved'] * 3)
        self.assertFalse(a1.merged_into_id)
        self.assertFalse(new.active)
        self.assertEqual(result['log'].state, 'undone')
        with self.assertRaises(UserError):
            result['log'].action_undo()

    def test_optimize_button_runs_in_background(self):
        self.rule('A', 'approved')
        self.rule('B')
        action = self.env['dac.chat.rule'].action_ai_review()
        log = self.env['dac.chat.rule.optimize'].browse(action['params']['next']['res_id'])
        self.assertEqual(log.state, 'queued', 'bấm nút chỉ xếp lượt, AI chạy trong cron')
        seen = []

        def fake(rule_model, analysis=None, log=None):
            seen.append(log)
            log.write({'state': 'done', 'summary': 'ok'})
            return {'message': 'ok', 'usage': {}, 'log': log}

        with patch.object(type(self.env['dac.chat.rule']), '_ai_optimize', fake), \
             patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'):
            self.env['dac.chat.analysis']._cron_process()
        self.assertEqual((seen, log.state), ([log], 'done'))
        failing = self.env['dac.chat.rule.optimize'].create({'state': 'queued'})
        with patch.object(type(self.env['dac.chat.rule']), '_ai_optimize', side_effect=UserError('HTTP 500')), \
             patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'), \
             mute_logger('odoo.addons.dac_chat_playbook.models.chat_rule'):
            self.env['dac.chat.rule.optimize']._cron_run()
        self.assertEqual(failing.state, 'failed')
        self.assertIn('HTTP 500', failing.error_message)

    def test_channel_context_and_phone_rule(self):
        from odoo.addons.dac_chat_playbook.models.chat_prompt import channel_key
        self.assertEqual([channel_key(v) for v in ('Zalo', 'Facebook', 'facebook messenger', '', None)],
                         ['zalo', 'facebook', 'facebook', 'zalo', 'zalo'])
        prompt = self.env['dac.chat.prompt'].create({'name': 'Kênh', 'mode': 'info', 'state': 'draft',
                                                     'system_prompt': 'Kênh: {{channel}}', 'info_fields_json': '[]'})
        zalo = prompt._runtime_prompt(datetime(2026, 11, 10, 3, 0), 'Chị Lan', 'Zalo')
        facebook = prompt._runtime_prompt(datetime(2026, 11, 10, 3, 0), 'Chị Lan', 'facebook')
        self.assertIn('Kênh: Zalo', zalo)
        self.assertIn('KHÔNG xin số điện thoại', zalo)
        self.assertIn('Kênh: Facebook Messenger', facebook)
        self.assertIn('có thể xin số Zalo', facebook)
        self.assertNotIn('KHÔNG xin số điện thoại', facebook)

    def test_assemble_keeps_each_mode_in_scope(self):
        Prompt = self.env['dac.chat.prompt']
        data = {'persona': 'Trợ lý DAC.', 'flow_greeting': 'Chỉ chào.', 'flow_info': 'Xin thông tin.', 'flow_consult': 'Tư vấn.',
                'after_hours_greeting': 'Ngoài giờ: chỉ hỏi khách cần gì.', 'after_hours': 'Ngoài giờ: xin kích thước.',
                'consult_guide': 'Alu bền ngoài trời.', 'style_rules': ['Tin ngắn'],
                'info_fields': [{'key': 'size', 'label': 'Kích thước', 'required': True},
                                {'key': 'phone', 'label': 'SĐT', 'required': True, 'ask_example': 'Cho em xin số'}],
                'examples': [{'situation': 'Khách chào', 'customer': 'alo', 'bot': ['Dạ em chào anh/chị ạ'], 'modes': ['greeting', 'info', 'consult']},
                             {'situation': 'Hỏi giá', 'customer': 'giá sao', 'bot': ['Cho em xin kích thước'], 'modes': ['info', 'consult']},
                             {'situation': 'Ngoài giờ', 'customer': 'alo', 'bot': ['Bạn phụ trách phản hồi vào {{next_working_time}} ạ']}]}
        greeting, info, consult = (Prompt._assemble(data, m) for m in ('greeting', 'info', 'consult'))
        self.assertIn('Ngoài giờ: chỉ hỏi khách cần gì.', greeting)
        self.assertNotIn('xin kích thước', greeting)
        self.assertNotIn('Hỏi giá', greeting, 'đoạn mẫu của bộ khác không vào bộ Chào hỏi')
        self.assertIn('Hỏi giá', info)
        self.assertIn('{{next_working_time}}', greeting, 'câu mẫu giữ biến để code điền mốc thật')
        self.assertNotIn('thứ 2', greeting)
        self.assertNotIn('Alu bền', info, 'bộ Hỏi thông tin không tư vấn')
        self.assertIn('Alu bền', consult)
        self.assertIn('Kênh khách nhắn: {{channel}}', info)
        fields = Prompt._info_fields(data, 'info')
        self.assertEqual([(f['key'], f['required']) for f in fields], [('size', True), ('phone', False)])
        self.assertIn('Zalo thì KHÔNG xin', info)

    def test_demo_reply_returns_prompt_request_tokens_and_context(self):
        Prompt = self.env['dac.chat.prompt']
        prompt = Prompt.create({'name': 'Demo', 'mode': 'greeting', 'state': 'draft',
                                'system_prompt': '# Vai trò\nTrợ lý DAC. Hẹn {{next_working_time}}.'})
        options = Prompt.demo_options()
        self.assertIn(prompt.id, [p['id'] for p in options['prompts']])
        self.assertEqual([p['key'] for p in options['presets']], ['now', 'work', 'lunch', 'evening', 'saturday', 'sunday'])
        self.assertEqual((options['pricing']['in'], options['pricing']['out']), (0.10, 0.50))
        seen = {}

        def fake_once(connection, item, system, text, images, name, description, schema, timeout):
            seen.update(system=system, text=text, name=name)
            return {'messages': ['Dạ em chào chị ạ', 'Giá 300k nha chị'], 'handoff': False}, {'in': 12500, 'out': 180}

        connection = self.env['dac.ai.connection'].create({'name': 'Thử', 'kind': 'openai', 'base_url': 'https://ai.example.com/v1',
                                                           'api_key': 'k', 'active': True})
        model = self.env['dac.ai.model'].create({'connection_id': connection.id, 'name': 'bot', 'selected': True, 'verified': True})
        prompt.bot_model_id = model
        with patch('odoo.addons.dac_chat_playbook.models.ai_agent._vision_once', fake_once), \
             patch.object(type(self.env['dac.ai.model']), '_note', lambda *a, **k: None):
            result = Prompt.demo_reply(prompt.id, [['customer', 'alo shop'], ['bot', 'Dạ'], ['customer', 'làm bảng hiệu']],
                                       '2026-11-15 13:00:00', 'Chị Lan')
        self.assertEqual(result['usage'], {'in': 12500, 'out': 180})
        self.assertEqual(result['messages'], ['Dạ em chào chị ạ'])
        self.assertEqual(result['blocked'], ['Giá 300k nha chị'], 'bộ lọc vẫn chặn trong demo')
        self.assertTrue(result['handoff'])
        self.assertIn('8:00 sáng mai (thứ 2, 16/11)', result['system_prompt'])
        self.assertIn('Không báo giá', result['system_prompt'])
        self.assertEqual(result['system_prompt'], seen['system'])
        self.assertIn('Khách: làm bảng hiệu', result['user_text'])
        self.assertEqual(result['context']['next'], '8:00 sáng mai (thứ 2, 16/11)')
        self.assertEqual(result['prompt_chars'], len(result['system_prompt']))
        with self.assertRaises(UserError):
            Prompt.demo_reply(prompt.id, [['customer', 'a'], ['bot', 'b']], False, '')
        self.assertEqual(prompt.action_test()['tag'], 'dac_chat_demo')


REQUEST = 'odoo.addons.dac_ai_core.models.ai_connection.AiConnection._request'


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestTaskSettings(TransactionCase):
    """Màn hình "Model & thinking theo tác vụ": chọn model + mức thinking cho từng bước của workflow chatbot."""

    def setUp(self):
        super().setUp()
        self.env['dac.ai.connection'].search([]).write({'active': False})  # không gọi AI thật / kết nối có sẵn trong DB
        self.Task = self.env['dac.chat.task']
        self.sent = []

    def model(self, name='haiku', kind='openai'):
        connection = self.env['dac.ai.connection'].create({
            'name': 'Kết nối %s' % name, 'kind': kind, 'base_url': 'https://ai.example.com/v1' if kind == 'openai' else False,
            'api_key': 'k', 'active': True})
        return self.env['dac.ai.model'].create({'connection_id': connection.id, 'name': name, 'selected': True, 'verified': True})

    def fake_request(self, tool_name, args, kind='openai', usage=(120, 8)):
        """Thay `_request` của kết nối: ghi lại payload đã gửi và trả khuôn phản hồi gọi tool đúng loại kết nối."""
        def fake(connection, method, url, headers, payload=None, timeout=(10, 60)):
            self.sent.append(payload)
            if kind == 'gemini':
                return {'candidates': [{'content': {'parts': [{'functionCall': {'name': tool_name, 'args': args}}]}}]}
            return {'choices': [{'message': {'content': '', 'tool_calls': [
                {'id': 'c1', 'type': 'function', 'function': {'name': tool_name, 'arguments': json.dumps(args)}}]}}],
                'usage': {'prompt_tokens': usage[0], 'completion_tokens': usage[1]}}
        return patch(REQUEST, fake)

    def no_note(self):
        return patch.object(type(self.env['dac.ai.model']), '_note', lambda *a, **k: None)

    # ── Dòng thiết lập ───────────────────────────────────────────────────────
    def test_every_task_has_exactly_one_row_and_choices_survive_refresh(self):
        rows = self.Task._ensure_all()
        self.assertEqual(rows.mapped('code'), ['read', 'coordinate', 'review', 'write_prompt', 'improve_prompt', 'bot_greeting', 'bot_info', 'bot_consult'])
        self.assertEqual(self.Task._ensure_all(), rows, 'gọi lại không tạo thêm dòng')
        row = rows.filtered(lambda t: t.code == 'read')
        row.write({'thinking': 'low', 'name': 'Tên cũ'})
        self.Task._ensure_all()
        self.assertEqual((row.thinking, row.name), ('low', 'Đọc & gán nhãn hội thoại'), 'chọn của quản lý giữ nguyên, nhãn được làm mới')
        with self.assertRaises(UserError):
            self.Task._get('khong_co')

    def test_model_priority_task_then_caller_override_then_config_then_shared_chain(self):
        read, override, config_model, own = self.model('doc'), self.model('ghi-de'), self.model('mac-dinh'), self.model('rieng')
        config = self.env['dac.chat.config']._get()
        config.ai_model_id = False
        self.assertFalse(self.Task._resolve('read').model, 'chưa chọn gì → thứ tự kết nối chung')
        config.ai_model_id = config_model
        self.assertEqual(self.Task._resolve('read').model, config_model)
        self.assertFalse(self.Task._resolve('bot_consult', use_config_model=False).model, 'chatbot không lấy model của khâu phân tích')
        self.assertEqual(self.Task._resolve('read', override, False).model, override, 'ghi đè của đợt thắng model mặc định')
        self.assertFalse(self.Task._resolve('read', override, False).fallback)
        row = self.Task._get('read')
        row.write({'ai_model_id': own.id, 'ai_fallback': True})
        picked = self.Task._resolve('read', override, False)
        self.assertEqual((picked.model, picked.fallback), (own, True), 'model chọn ở dòng tác vụ thắng mọi ghi đè')
        self.assertEqual(self.Task._resolve('review').model, config_model, 'tác vụ khác không bị ảnh hưởng')
        self.assertNotEqual(read, own)

    def test_thinking_travels_in_context_only_when_chosen(self):
        model = self.model()
        task = self.Task._get('review')
        task.ai_model_id = model
        auto = self.Task._resolve('review')
        self.assertFalse(auto.effort)
        self.assertNotIn('dac_ai_effort', auto.env.context)
        task.thinking = 'high'
        high = self.Task._resolve('review')
        self.assertEqual(high.effort, 'high')
        self.assertEqual(high.env.context['dac_ai_effort'], 'high')
        self.assertEqual(high.model.env.context['dac_ai_effort'], 'high')
        self.assertNotIn('dac_ai_effort', self.env.context, 'không làm bẩn context của người gọi')

    # ── Đi ra dây ────────────────────────────────────────────────────────────
    def test_openai_request_carries_reasoning_effort_and_bare_model_name(self):
        from odoo.addons.dac_chat_playbook.models.ai_agent import ai_call
        model = self.model('cmc/claude-haiku-5-5')
        schema = {'type': 'object', 'properties': {'answer': {'type': 'string'}}}
        for thinking, expected in (('auto', None), ('none', 'none'), ('low', 'low'), ('max', 'max')):
            self.Task._get('read').write({'ai_model_id': model.id, 'thinking': thinking})
            ai = self.Task._resolve('read')
            self.sent.clear()
            with self.fake_request('report', {'answer': 'OK'}), self.no_note():
                data, _label = ai_call(ai.env, ai.model, ai.fallback, 'sys', 'text', schema, 'report', 'd', (1, 1))
            self.assertEqual(data, {'answer': 'OK'})
            self.assertEqual(self.sent[0].get('reasoning_effort'), expected, thinking)
            self.assertEqual(self.sent[0]['model'], 'cmc/claude-haiku-5-5', 'tên model giữ nguyên, không gắn hậu tố mức')

    def test_gemini_connection_ignores_thinking(self):
        from odoo.addons.dac_chat_playbook.models.ai_agent import ai_call
        model = self.model('gemini-3.5-flash', kind='gemini')
        self.Task._get('review').write({'ai_model_id': model.id, 'thinking': 'high'})
        ai = self.Task._resolve('review')
        self.assertIn('Gemini', self.Task._get('review').thinking_note)
        with self.fake_request('report', {'answer': 'OK'}, kind='gemini'), self.no_note():
            ai_call(ai.env, ai.model, ai.fallback, 'sys', 'text', {'type': 'object', 'properties': {}}, 'report', 'd', (1, 1))
        self.assertNotIn('reasoning_effort', json.dumps(self.sent[0]))

    def test_effort_is_not_sent_by_other_addons_calls(self):
        """Chỉ lời gọi qua `_resolve` mới mang thinking: gọi trực tiếp (module khác) không bị thêm tham số."""
        model = self.model()
        self.Task._get('read').write({'ai_model_id': model.id, 'thinking': 'max'})
        with self.fake_request('report', {'level': 'normal'}), self.no_note():
            model.connection_id._tool_call('s', 't', 'report', 'd', {'type': 'object', 'properties': {}}, (1, 1), model)
        self.assertNotIn('reasoning_effort', self.sent[0])

    # ── Các tác vụ dùng đúng thiết lập ───────────────────────────────────────
    def test_chatbot_reply_uses_its_own_mode_row(self):
        model = self.model('bot-nhanh')
        self.Task._get('bot_greeting').write({'ai_model_id': model.id, 'thinking': 'low'})
        prompt = self.env['dac.chat.prompt'].create({'name': 'Chào', 'mode': 'greeting', 'state': 'draft',
                                                     'system_prompt': '# Vai trò\nTrợ lý DAC. Hẹn {{next_working_time}}.'})
        with self.fake_request('reply_customer', {'messages': ['Dạ em chào chị ạ'], 'handoff': False}), self.no_note():
            result = prompt._reply([('customer', 'alo shop')], datetime(2026, 11, 10, 3, 0), 'Chị Lan')
        self.assertEqual(self.sent[0]['model'], 'bot-nhanh')
        self.assertEqual(self.sent[0]['reasoning_effort'], 'low')
        self.assertEqual((result['thinking'], result['messages']), ('low', ['Dạ em chào chị ạ']))
        options = self.env['dac.chat.prompt'].demo_options()
        label = next(p['model'] for p in options['prompts'] if p['id'] == prompt.id)
        self.assertIn('bot-nhanh', label)
        self.assertIn('thinking low', label, 'Demo chatbot hiện model và thinking thực sự dùng')

    def test_rule_review_uses_review_row(self):
        model = self.model('ra-soat')
        self.Task._get('review').write({'ai_model_id': model.id, 'thinking': 'xhigh'})
        Rule = self.env['dac.chat.rule']
        Rule.create([{'name': 'Chào ngay', 'content': 'Chào ngay', 'category': 'greeting', 'state': 'proposed', 'source': 'ai'},
                     {'name': 'Chào sớm', 'content': 'Chào sớm', 'category': 'greeting', 'state': 'proposed', 'source': 'ai'}])
        with self.fake_request('optimize_rules', {'summary': 'Không trùng', 'rules': []}), self.no_note():
            result = Rule._ai_optimize()
        self.assertEqual((self.sent[0]['model'], self.sent[0]['reasoning_effort']), ('ra-soat', 'xhigh'))
        self.assertIn('120 token vào / 8 token ra', result['message'])

    def test_analysis_tasks_follow_rows_and_keep_run_override_as_fallback(self):
        read, run_model = self.model('doc-tin'), self.model('rieng-cua-dot')
        analysis = self.env['dac.chat.analysis'].create({'name': 'Thử', 'ai_model_id': run_model.id})
        self.assertEqual(analysis._task_ai('read').model, run_model, 'chưa chọn model ở dòng tác vụ → dùng ghi đè của đợt (như trước)')
        self.Task._get('read').write({'ai_model_id': read.id, 'thinking': 'medium'})
        ai = analysis._task_ai('read')
        self.assertEqual((ai.model, ai.effort), (read, 'medium'))
        self.assertEqual(analysis._task_ai('coordinate').model, run_model, 'tác vụ khác vẫn theo ghi đè của đợt')

    def test_try_button_reports_model_thinking_and_tokens(self):
        model = self.model('thu')
        row = self.Task._get('bot_info')
        row.write({'ai_model_id': model.id, 'thinking': 'high'})
        with self.fake_request('report_test', {'answer': 'OK'}, usage=(55, 3)), self.no_note():
            action = row.action_test()
        self.assertEqual(self.sent[0]['reasoning_effort'], 'high')
        self.assertIn('thinking: Cao', action['params']['message'])
        self.assertIn('55 token vào / 3 token ra', action['params']['message'])
        with patch(REQUEST, side_effect=UserError('HTTP 403')), self.no_note(), self.assertRaises(UserError):
            row.action_test()

    # ── Giao diện và quyền ───────────────────────────────────────────────────
    def test_screen_menu_views_and_manager_rights(self):
        action = self.Task.action_open()
        self.assertEqual((action['res_model'], action['view_mode']), ('dac.chat.task', 'list,form'))
        self.assertTrue(self.env.ref('dac_chat_playbook.menu_chat_task'))
        for view_type in ('list', 'form'):
            self.assertTrue(self.Task.get_view(view_type=view_type)['arch'])
        self.assertEqual(self.env['dac.chat.config']._get().action_open_tasks()['res_model'], 'dac.chat.task')
        manager = self.env['res.users'].create({
            'name': 'Quản lý thử 2', 'login': 'chat_task_manager',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id, self.env.ref('dac_erp.group_dac_erp_manager').id])]})
        as_manager = self.Task.with_user(manager)
        row = as_manager.action_open() and as_manager._get('read').with_user(manager)
        row.write({'thinking': 'low'})
        self.assertEqual(self.Task._get('read').thinking, 'low', 'quản lý (không phải admin) chỉnh được thinking')
        with self.assertRaises(AccessError):
            as_manager.create({'code': 'read'})
        with self.assertRaises(AccessError):
            row.unlink()

    def test_ai_ready_counts_a_task_model_even_without_shared_chain(self):
        config = self.env['dac.chat.config']._get()
        config.ai_model_id = False
        config.invalidate_recordset()
        self.assertFalse(config.ai_ready)
        self.Task._get('review').ai_model_id = self.model()
        config.invalidate_recordset()
        self.assertTrue(config.ai_ready)


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestFullHistoryRead(ChatFixtures):
    def test_summary_scope_all_time_merges_previous_runs(self):
        Insight = self.env['dac.chat.insight']
        Insight.search([]).unlink()
        first = self.run_until_done(self.analysis(ai_conversation_limit=0))
        second = self.run_until_done(self.analysis(ai_conversation_limit=0, summary_scope='all_time'))
        convs = self.env['dac.pancake.conversation'].search([('page_id', '=', self.page.id), ('is_internal', '=', False)], limit=4)
        sample = lambda c: {c.id: {'id': c.id, 'last_at': '2026-11-10 05:00:00', 'new': True}}
        Insight._store(first, [{'conversation_id': convs[0].id, 'summary': 's', 'product': 'decal', 'outcome': 'chot_don', 'staff_name': 'Dao'},
                               {'conversation_id': convs[1].id, 'summary': 's', 'product': 'decal', 'outcome': 'chot_don', 'staff_name': 'Dao'}],
                       {**sample(convs[0]), **sample(convs[1])}, 'm', {})
        Insight._store(second, [{'conversation_id': convs[2].id, 'summary': 's', 'product': 'decal', 'outcome': 'mat_khach', 'staff_name': 'Dao'}],
                       sample(convs[2]), 'm', {})
        brief = second._coord_brief()
        self.assertIn('mọi hội thoại đã gán nhãn', brief)
        second._apply_staff_reviews([])
        line = second.staff_line_ids.filtered(lambda l: l.name == 'Dao')
        self.assertEqual(line.ai_conversations, 3, 'gộp nhãn của đợt trước')
        first._apply_staff_reviews([])
        self.assertEqual(first.staff_line_ids.filtered(lambda l: l.name == 'Dao').ai_conversations, 2, 'chỉ đợt này')

    def test_wizard_all_history_and_count_first(self):
        Wizard = self.env['dac.chat.run.wizard']
        wizard = Wizard.create({'period': 'all', 'depth': 'all'})
        self.assertIn('Đếm trước', wizard.estimate)
        before = self.env['dac.chat.analysis'].search_count([])
        wizard.action_count()
        self.assertTrue(wizard.counted)
        self.assertIn('hội thoại thỏa điều kiện chưa đọc', wizard.count_text)
        self.assertEqual(self.env['dac.chat.analysis'].search_count([]), before, 'đếm thử không để lại đợt nào')
        with patch('odoo.addons.base.models.ir_cron.ir_cron._trigger'):
            action = wizard.action_start()
        run = self.env['dac.chat.analysis'].browse(action['res_id'])
        self.env.cr.execute('SELECT MIN(inserted_at) FROM dac_pancake_message')
        self.assertEqual(run.date_from, self.env.cr.fetchone()[0].date(), 'toàn bộ lịch sử: từ tin cũ nhất')
        self.assertEqual((run.ai_conversation_limit, run.summary_scope), (100000, 'all_time'))


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestRunResilience(ChatFixtures):
    def test_duplicate_conversation_in_ai_reply_keeps_richer_item(self):
        analysis = self.analysis(ai_conversation_limit=0)
        conv = self.env['dac.pancake.conversation'].search([('page_id', '=', self.page.id), ('is_internal', '=', False)], limit=1)
        self.env['dac.chat.insight'].search([('conversation_id', '=', conv.id)]).unlink()
        full = {'conversation_id': conv.id, 'summary': 'Khách hỏi biển inox', 'product': 'bang_hieu', 'outcome': 'dang_trao_doi',
                'image_facts': 'Biển inox 48x30', 'info': {'size': '48x30cm'}, 'scores': {'greeting': 3, 'discovery': 2}, 'staff_name': 'Dao'}
        short = {'conversation_id': str(conv.id), 'summary': 'Hỏi giá', 'product': 'bang_hieu', 'outcome': 'dang_trao_doi'}
        count = self.env['dac.chat.insight']._store(analysis, [full, short], {conv.id: {'id': conv.id, 'last_at': '2026-11-10 05:00:00'}},
                                                    'm', {})
        insight = self.env['dac.chat.insight'].search([('conversation_id', '=', conv.id)])
        self.assertEqual((count, len(insight)), (1, 1))
        self.assertEqual((insight.image_facts, insight.score_discovery), ('Biển inox 48x30', 2), 'giữ bản đầy đủ hơn')

    def test_batch_error_is_retried_then_skipped_and_other_errors_fail(self):
        analysis = self.analysis(ai_conversation_limit=0)
        analysis.write({'state': 'ai', 'ai_batches_total': 3, 'ai_batches_done': 0, 'report_json': '{}'})
        analysis._handle_step_error(ValueError('trùng khoá'))
        self.assertEqual((analysis.state, analysis.ai_batches_done, analysis.batch_failures), ('ai', 0, 1), 'lần 1: thử lại lô đó')
        analysis._handle_step_error(ValueError('trùng khoá'))
        self.assertEqual((analysis.state, analysis.ai_batches_done, analysis.skipped_batches), ('ai', 1, 1), 'lần 2: bỏ qua lô, đọc tiếp')
        self.assertIn('Bỏ qua lô 1/3', analysis.progress_log)
        analysis.write({'ai_batches_done': 3})
        analysis._handle_step_error(ValueError('lỗi bước tổng hợp'))
        self.assertEqual(analysis.state, 'failed', 'lỗi ngoài bước đọc lô: dừng đợt')

    def test_resume_failed_run_keeps_progress(self):
        analysis = self.analysis(ai_conversation_limit=0)
        analysis.write({'state': 'failed', 'ai_batches_total': 398, 'ai_batches_done': 267, 'report_json': '{}', 'tokens_out': 1000,
                        'error_message': 'duplicate key'})
        with patch('odoo.addons.base.models.ir_cron.ir_cron._trigger') as trigger:
            analysis.action_resume()
        trigger.assert_called_once()
        self.assertEqual((analysis.state, analysis.ai_batches_done, analysis.tokens_out, analysis.error_message), ('ai', 267, 1000, False))
        self.assertIn('Chạy tiếp từ lô 268/398', analysis.progress_log)
        draft = self.analysis()
        with self.assertRaises(UserError):
            draft.action_resume()


GREET = 'odoo.addons.dac_chat_playbook.models.chat_greeting'


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestGreeting(TransactionCase):
    """Chào khách mới: soạn → người duyệt (Zalo) duyệt/góp ý → kiểm tra lại → gửi Pancake."""

    def setUp(self):
        super().setUp()
        self.env['dac.ai.connection'].search([]).write({'active': False})
        self.now = datetime.now().replace(microsecond=0)
        self.page = self.env['dac.pancake.page'].create({'name': 'Page chào', 'page_fm_id': 'pzl_greet_page'})
        zalo = self.env['dac.zalo.config']._config()
        zalo.write({'active': True, 'assistant_uid': 'acc-1', 'bridge_url': 'http://zalo-bridge:3000', 'bridge_token': 'x' * 40})
        self.channel = self.env['dac.zalo.channel'].create({'name': 'Dương Tuấn Kiệt', 'thread_id': 'u-kiet', 'thread_type': 'user'})
        self.config = self.env['dac.chat.config']._get()
        self.config.write({'greeting_active': True, 'greeting_approver_channel_id': self.channel.id})
        self.config.greeting_activated_at = self.now - timedelta(hours=1)
        self.prompt = self.env['dac.chat.prompt'].create({'name': 'Chào', 'mode': 'greeting', 'state': 'draft',
                                                          'system_prompt': 'Trợ lý DAC.', 'info_fields_json': '[]'})
        self.seq = 0
        self.replies = []  # (note) mỗi lần soạn
        self.sent = []
        self.answer = {'messages': ['Dạ em chào chị ạ', 'Chị đang cần làm gì ạ?'], 'handoff': False, 'handoff_reason': '',
                       'returning_customer': False, 'returning_reason': '', 'usage': {'in': 100, 'out': 20}}

    # ── dữ liệu ──────────────────────────────────────────────────────────────
    def conversation(self, name='Chị Lan', start=None):
        start = start or self.now - timedelta(minutes=5)
        self.seq += 1
        return self.env['dac.pancake.conversation'].create({
            'conversation_fm_id': 'pzl_u_greet_%s' % self.seq, 'page_id': self.page.id, 'customer_name': name, 'platform': 'Zalo',
            'inserted_at_fm': start, 'updated_at_fm': start})

    def message(self, conversation, at, who, text):
        self.seq += 1
        sender = {'id': 'cus_%s' % conversation.id, 'name': conversation.customer_name}
        if who == 'staff':
            sender = {'id': 'pzl_greet_page', 'name': 'Duy An', 'admin_id': 'a1', 'admin_name': 'Tieu Bang'}
        raw = {'id': 'gm%s' % self.seq, 'type': 'INBOX', 'page_id': 'pzl_greet_page', 'from': sender, 'attachments': []}
        return self.env['dac.pancake.message'].create({
            'message_fm_id': raw['id'], 'conversation_id': conversation.id, 'content_html': '<div>%s</div>' % text, 'type_content': 'text',
            'inserted_at': at, 'raw_json': json.dumps(raw), 'sender_name': sender['name']})

    def wait(self, conversation, since, kind='new'):
        return self.env['dac.pancake.remind.wait'].create({'conversation_id': conversation.id, 'since': since, 'customer_kind': kind})

    def patches(self):
        test = self

        def fake_reply(prompt, history, now, customer_name='', channel='zalo', note=''):
            test.replies.append({'note': note, 'history': history, 'channel': channel})
            return dict(test.answer, messages=list(test.answer['messages']))

        def fake_send(conversation, text):
            test.sent.append((conversation.id, text))
            return {'success': True}

        stack = [patch.object(type(self.env['dac.chat.prompt']), '_reply', fake_reply),
                 patch(GREET + '.pancake_send', fake_send),
                 patch(GREET + '.shutil.which', lambda name: None),
                 patch(GREET + '.time.sleep', lambda s: None),
                 patch.object(type(self.env['dac.pancake.conversation']), '_fetch_messages', lambda self: None)]
        from contextlib import ExitStack
        exit_stack = ExitStack()
        for item in stack:
            exit_stack.enter_context(item)
        return exit_stack

    def kiet(self, text, message_id=None):
        self.seq += 1
        return self.env['dac.zalo.message']._ingest('acc-1', [{'thread_id': 'u-kiet', 'message_id': message_id or 'z%s' % self.seq,
                                                               'text': text, 'direction': 'in'}])

    def queued(self):
        return self.env['dac.zalo.channel.message'].search([('channel_id', '=', self.channel.id)], order='id')

    def new_greeting(self, name='Chị Lan'):
        conv = self.conversation(name)
        self.message(conv, self.now - timedelta(minutes=5), 'customer', 'Shop ơi làm bảng hiệu')
        self.wait(conv, self.now - timedelta(minutes=5))
        self.env['dac.chat.greeting']._cron_run()
        return self.env['dac.chat.greeting'].search([('conversation_id', '=', conv.id)]), conv

    # ── test ─────────────────────────────────────────────────────────────────
    def test_new_customer_is_drafted_sent_to_approver_then_sent_after_approval(self):
        with self.patches():
            greeting, conv = self.new_greeting()
            self.assertEqual((greeting.state, greeting.version), ('waiting', 1))
            self.assertEqual(self.replies[0]['channel'], 'zalo')
            ask = self.queued()
            self.assertEqual(len(ask), 1)
            self.assertIn('G%s' % greeting.id, ask.text)
            self.assertIn('1. Dạ em chào chị ạ', ask.text)
            self.assertIn('"duyệt"', ask.text)
            self.env['dac.chat.greeting']._cron_run()
            self.assertEqual(self.env['dac.chat.greeting'].search_count([('conversation_id', '=', conv.id)]), 1, 'không soạn trùng')
            self.kiet('Duyệt nha', 'zz1')
            self.kiet('Duyệt nha', 'zz1')  # bridge gửi lại lô cũ
        self.assertEqual(greeting.state, 'sent')
        self.assertEqual([t for _c, t in self.sent], ['Dạ em chào chị ạ', 'Chị đang cần làm gì ạ?'], 'gửi đúng 1 lần, đủ 2 tin')
        self.assertEqual(greeting.reply_ids.mapped('action'), ['approve'])
        self.assertIn('✅', self.queued()[-1].text)

    def test_returning_customer_is_not_greeted(self):
        with self.patches():
            old = self.conversation('Anh Cũ')
            self.message(old, self.now - timedelta(days=3), 'customer', 'hôm trước hỏi decal')
            self.message(old, self.now - timedelta(minutes=5), 'customer', 'em ơi chị gửi file nè')
            self.wait(old, self.now - timedelta(minutes=5))
            ongoing = self.conversation('Chị Đang Trao Đổi')
            self.message(ongoing, self.now - timedelta(minutes=4), 'customer', 'ok em')
            self.wait(ongoing, self.now - timedelta(minutes=4), kind='ongoing')
            self.answer['returning_customer'], self.answer['returning_reason'] = True, 'khách nhắc file đơn hôm qua'
            ai = self.conversation('Khách AI')
            self.message(ai, self.now - timedelta(minutes=3), 'customer', 'như hôm qua nha em')
            self.wait(ai, self.now - timedelta(minutes=3))
            self.env['dac.chat.greeting']._cron_run()
        greetings = self.env['dac.chat.greeting'].search([('conversation_id', 'in', (old | ongoing | ai).ids)])
        self.assertEqual(set(greetings.mapped('state')), {'returning'})
        by_conv = {g.conversation_id: g.returning_reason for g in greetings}
        self.assertIn('hội thoại đã có tin từ', by_conv[old])
        self.assertIn('đang trao đổi', by_conv[ongoing])
        self.assertIn('AI', by_conv[ai])
        self.assertFalse(self.queued(), 'khách đã liên hệ: không gửi người duyệt')
        self.assertFalse(self.sent)

    def test_feedback_is_saved_and_reply_redrafted(self):
        with self.patches():
            greeting, _conv = self.new_greeting()
            self.answer['messages'] = ['Dạ em chào chị ạ', 'Chị cần làm gì ạ?']
            self.kiet('nhắn dài quá, tách 2 tin ngắn')
        self.assertEqual((greeting.state, greeting.version), ('waiting', 2))
        self.assertIn('nhắn dài quá', self.replies[-1]['note'])
        feedback = self.env['dac.chat.feedback'].search([('greeting_id', '=', greeting.id)])
        self.assertEqual(feedback.mode, 'greeting')
        self.assertEqual(len(self.queued()), 2, 'gửi bản soạn lại cho người duyệt')
        self.assertIn('Đã ghi nhận góp ý', self.queued()[-1].text)
        runtime = self.prompt._runtime_prompt(self.now)
        self.assertIn('GÓP Ý CỦA NGƯỜI DUYỆT', runtime)
        self.assertIn('tách 2 tin ngắn', runtime)
        self.assertFalse(self.sent)

    def test_recheck_before_send_staff_or_customer_changes(self):
        with self.patches():
            greeting, conv = self.new_greeting()
            self.message(conv, self.now - timedelta(minutes=1), 'staff', 'Dạ chị cần làm bảng hiệu kích thước nhiêu ạ')
            self.kiet('ok')
            self.assertEqual(greeting.state, 'staff_replied', 'nhân viên đã nhắn → bot không gửi')
            other, conv2 = self.new_greeting('Anh Hai')
            self.message(conv2, self.now - timedelta(minutes=1), 'customer', 'làm 2 cái nha')
            self.kiet('duyệt G%s' % other.id)
            self.assertEqual((other.state, other.version), ('waiting', 2), 'khách nhắn thêm → soạn lại, xin duyệt lại')
            self.assertIn('Khách vừa nhắn thêm', self.queued()[-1].text)
            self.kiet('[Tin nhắn chat.sticker]')
            self.assertEqual(other.state, 'waiting', 'sticker không phải duyệt')
            self.kiet('bỏ')
            self.assertEqual(other.state, 'cancelled')
        self.assertFalse(self.sent)

    def test_disabled_or_old_waits_do_nothing_and_other_dms_pass_through(self):
        with self.patches():
            self.config.greeting_active = False
            greeting, _conv = self.new_greeting()
            self.assertFalse(greeting, 'tắt thì không soạn')
            self.config.write({'greeting_active': True})
            self.assertGreater(self.config.greeting_activated_at, self.now - timedelta(minutes=1), 'bật lại → chỉ chào khách nhắn sau mốc bật')
            greeting, _conv = self.new_greeting('Khách Trước Khi Bật')
            self.assertFalse(greeting, 'khách nhắn trước lúc bật thì không chào')
        events = [{'thread_id': 'u-khac', 'message_id': 'x1', 'text': 'alo', 'direction': 'in'},
                  {'thread_id': 'u-kiet', 'message_id': 'x2', 'text': 'duyệt', 'direction': 'in'}]
        rest = self.env['dac.chat.greeting']._ingest_approver('acc-1', events)
        self.assertEqual([e['message_id'] for e in rest], ['x1'], 'tin người khác vẫn đi luồng trợ lý Zalo')
        self.assertEqual(self.env['dac.chat.greeting']._ingest_approver('acc-khac', events), events)

    def test_pancake_send_request_shape(self):
        from odoo.addons.dac_chat_playbook.models.chat_greeting import pancake_send
        from odoo.addons.dac_pancake_remind.models.pancake_api import PancakeError
        conv = self.conversation()
        seen = {}

        class Response:
            status_code = 200

            def __init__(self, data):
                self.data = data

            def json(self):
                return self.data

        def fake_post(url, params=None, json=None, headers=None, timeout=None):
            seen.update(url=url, params=params, json=json, timeout=timeout)
            return Response({'success': True, 'id': 'msg1'})

        with patch(GREET + '.requests.post', fake_post), \
             patch.object(type(self.page), '_token', lambda page: 'TOKEN'):
            pancake_send(conv, 'Dạ em chào chị ạ')
        self.assertEqual(seen['url'], 'https://pages.fm/api/public_api/v1/pages/pzl_greet_page/conversations/%s/messages' % conv.conversation_fm_id)
        self.assertEqual(seen['params'], {'page_access_token': 'TOKEN'})
        self.assertEqual(seen['json'], {'action': 'reply_inbox', 'message': 'Dạ em chào chị ạ'})
        with patch(GREET + '.requests.post', lambda *a, **k: Response({'success': False, 'message': 'bad token=TOKEN'})), \
             patch.object(type(self.page), '_token', lambda page: 'TOKEN'), self.assertRaises(PancakeError):
            pancake_send(conv, 'x')

    def test_test_page_send_and_greeting_use_inactive_page(self):
        self.config.write({'test_page_fm_id': 'pzl_test_page_9', 'test_page_token': 'tok-test', 'test_message': 'Tin thử hệ thống'})
        rows = [{'id': 'pzl_u_test_conv', 'page_id': 'pzl_test_page_9', 'from': {'id': 'pzl_u_1', 'name': 'Anh Test'},
                 'updated_at': '2026-11-23T03:00:00', 'inserted_at': '2026-11-20T03:00:00'}]
        Page = type(self.env['dac.pancake.page'])
        test = self

        def fake_fetch(conversation):
            if test.sent:  # tin bot vừa gửi hiện trên Pancake như tin của page
                test.message(conversation, test.now, 'staff', 'Tin thử hệ thống')

        with self.patches(), patch.object(Page, '_list_conversations', lambda page, since, until, max_pages=0: rows), \
             patch.object(Page, '_resolve_ids', staticmethod(lambda entered, token: (entered, False))), \
             patch.object(type(self.env['dac.pancake.conversation']), '_fetch_messages', fake_fetch):
            self.config.action_test_send()
            page = self.env['dac.pancake.page'].with_context(active_test=False).search([('page_fm_id', '=', 'pzl_test_page_9')])
            self.assertEqual((page.active, page.history_enabled), (False, False), 'page thử không bị luồng nhắc/lấy tin quét')
            self.assertEqual([t for _c, t in self.sent], ['Tin thử hệ thống'])
            self.assertIn('Gửi OK', self.config.test_result)
            self.assertIn('tin NHÂN VIÊN', self.config.test_result)
            conv = self.env['dac.pancake.conversation'].search([('conversation_fm_id', '=', 'pzl_u_test_conv')])
            self.message(conv, self.now - timedelta(days=2), 'customer', 'tin cũ của hội thoại thử')
            self.config.action_test_greeting()
            greeting = self.env['dac.chat.greeting'].search([('conversation_id', '=', conv.id)])
            self.assertEqual((greeting.is_test, greeting.state), (True, 'waiting'), 'thử nghiệm: luôn coi là khách mới')
            self.assertIn('THỬ NGHIỆM', self.queued()[-1].text)

    def test_test_page_uses_new_token_of_existing_page(self):
        real = self.env['dac.pancake.page'].create({'name': 'Facebook thật', 'page_fm_id': '9990001112223'})
        real.sudo().page_access_token = 'old-token'
        self.config.write({'test_page_fm_id': 'https://pancake.vn/9990001112223', 'test_page_token': 'new-token'})
        Page = type(self.env['dac.pancake.page'])
        rows = [{'id': '9990001112223_99', 'page_id': '9990001112223', 'from': {'id': '99', 'name': 'Khách FB'}}]
        used = []

        def listing(page, since, until, max_pages=0):
            used.append(page.sudo().page_access_token)
            return rows

        with self.patches(), patch.object(Page, '_list_conversations', listing), \
             patch.object(Page, '_resolve_ids', staticmethod(lambda entered, token: ('9990001112223', False))):
            self.config.action_test_send()
        self.assertEqual(used, ['new-token'], 'dùng token vừa nhập, không dùng token cũ đã hết hạn')
        self.assertEqual((real.sudo().page_access_token, real.active), ('new-token', True), 'page đang chạy nhận token mới, vẫn hoạt động')

    def test_never_reveal_bot_identity(self):
        Prompt = self.env['dac.chat.prompt']
        result = Prompt._guard({'messages': ['Dạ em chào chị ạ, em là trợ lý của Duy An nha', 'Chị đang cần làm gì ạ?',
                                             'Bên em có ai hỗ trợ chị liền ạ']})
        self.assertEqual(result['messages'], ['Chị đang cần làm gì ạ?', 'Bên em có ai hỗ trợ chị liền ạ'], 'chữ "ai" thường không bị chặn')
        self.assertEqual(len(result['identity_blocked']), 1)
        self.assertEqual(Prompt._guard({'messages': ['Đây là tin AI tự soạn', 'Em là chatbot ạ']})['identity_blocked'],
                         ['Đây là tin AI tự soạn', 'Em là chatbot ạ'])
        self.assertFalse(result['handoff'], 'chặn câu lộ AI không phải lý do chuyển nhân viên')
        self.assertIn('KHÔNG tự giới thiệu', self.prompt._runtime_prompt(self.now))
        calls = []

        def fake_reply(prompt, history, now, customer_name='', channel='zalo', note=''):
            calls.append(note)
            if len(calls) == 1:
                return {'messages': ['Chị cần gì ạ?'], 'identity_blocked': ['Em là trợ lý của Duy An'], 'handoff': False,
                        'returning_customer': False, 'usage': {'in': 10, 'out': 2}}
            return {'messages': ['Dạ em chào chị ạ', 'Chị cần làm gì ạ?'], 'identity_blocked': [], 'handoff': False,
                    'returning_customer': False, 'usage': {'in': 10, 'out': 3}}

        with self.patches(), patch.object(type(Prompt), '_reply', fake_reply):
            greeting, _conv = self.new_greeting()
        self.assertEqual(len(calls), 2, 'bị chặn vì lộ AI → soạn lại một lần')
        self.assertIn('bị chặn vì tự giới thiệu', calls[1])
        self.assertEqual(greeting._messages(), ['Dạ em chào chị ạ', 'Chị cần làm gì ạ?'])
        self.assertEqual((greeting.tokens_in, greeting.tokens_out), (20, 5))

    def test_late_feedback_is_saved_and_previewed(self):
        with self.patches():
            greeting, conv = self.new_greeting()
            self.message(conv, self.now - timedelta(minutes=1), 'staff', 'Dạ chị cần gì ạ')
            self.env['dac.chat.greeting']._cron_run()
            self.assertEqual(greeting.state, 'staff_replied')
            self.answer['messages'] = ['Dạ em chào chị ạ', 'Chị đang cần làm gì ạ?']
            self.kiet('Chưa duyệt, không cần giới thiệu em là trợ lý, chào tự nhiên')
        feedback = self.env['dac.chat.feedback'].search([('greeting_id', '=', greeting.id)])
        self.assertTrue(feedback, 'góp ý tới muộn vẫn được lưu')
        self.assertEqual(greeting.state, 'staff_replied', 'không mở lại lời chào đã đóng')
        last = self.queued()[-1].text
        self.assertIn('Đã ghi nhận góp ý', last)
        self.assertIn('Bản soạn lại theo góp ý', last)
        self.assertIn('1. Dạ em chào chị ạ', last)
        self.assertNotIn('Dạ chị cần gì ạ', [t for _r, t in self.replies[-1]['history']], 'bản xem lại dùng đoạn chat lúc soạn bản gốc')
        self.assertFalse(self.sent)


@tagged('post_install', '-at_install', 'dac_chat_playbook')
class TestTraining(TransactionCase):
    """Huấn luyện & kiểm thử: góp ý từng câu bot → Agent tối ưu prompt → bản nháp mới."""

    def setUp(self):
        super().setUp()
        self.env['dac.ai.connection'].search([]).write({'active': False})
        self.Prompt = self.env['dac.chat.prompt']
        self.prompt = self.Prompt.create({'name': 'Gốc', 'mode': 'info', 'state': 'draft', 'info_fields_json': '[]',
                                          'system_prompt': '# Vai trò\nTrợ lý DAC xưng em.\n# Bối cảnh\n- Bây giờ: {{now}}\n- Kênh: {{channel}}\n' + 'x' * 300})

    def test_feedback_then_agent_rewrites_into_new_draft(self):
        data = self.Prompt.demo_feedback_add(self.prompt.id, 'Đừng hỏi số điện thoại khi khách nhắn Zalo', 'làm bảng hiệu', 'Chị cho em xin SĐT nha')
        self.assertEqual((data['pending'], data['items'][0]['bot']), (1, 'Chị cho em xin SĐT nha'))
        self.assertIn('góp ý: Đừng hỏi số điện thoại', self.prompt._runtime_prompt(datetime(2026, 11, 10, 3, 0)), 'chưa tối ưu: chèn vào prompt')
        other = self.Prompt.demo_feedback_add(self.prompt.id, 'tạm bỏ', '', '')['items'][0]
        self.assertEqual(self.Prompt.demo_feedback_toggle(other['id'], self.prompt.id)['pending'], 1, 'tắt góp ý → không tính')
        job = self.Prompt.demo_optimize(self.prompt.id)
        new = self.Prompt.browse(job['id'])
        self.assertEqual((new.state, new.parent_id, new.mode), ('queued', self.prompt, 'info'))
        seen = {}

        def fake_call(env, model, fallback, system, text, schema, name, description, timeout):
            seen.update(text=text, effort=env.context.get('dac_ai_effort'), timeout=timeout)
            return ({'system_prompt': '# Vai trò\nXưng em, không xin SĐT khách Zalo.\n# Bối cảnh\n- Bây giờ: {{now}}\n- Kênh: {{channel}}\n' + 'y' * 300,
                     'changes': ['Bỏ xin SĐT với khách Zalo (góp ý 1)']}, 'model-x')

        with patch('odoo.addons.dac_chat_playbook.models.chat_prompt.ai_call', fake_call), \
             patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'):
            self.Prompt._cron_optimize()
        self.assertIn('Chị cho em xin SĐT nha', seen['text'])
        self.assertEqual(seen['effort'], 'max', 'cải tiến dùng thinking tối đa (dòng tác vụ "Cải tiến prompt từ góp ý")')
        self.assertEqual(self.env['dac.chat.task']._get('improve_prompt').thinking, 'max')
        self.assertIn('thinking max', new.generated_by)
        self.assertNotIn('tạm bỏ', seen['text'])
        self.assertEqual(new.state, 'draft')
        self.assertIn('không xin SĐT', new.system_prompt)
        self.assertIn('Bỏ xin SĐT', new.optimize_note)
        self.assertEqual(self.prompt.state, 'draft', 'bản cũ giữ nguyên')
        self.assertNotIn('góp ý: Đừng hỏi', new._runtime_prompt(datetime(2026, 11, 10, 3, 0)), 'góp ý đã viết vào bản mới thì không chèn lặp')
        self.assertIn('góp ý: Đừng hỏi', self.prompt._runtime_prompt(datetime(2026, 11, 10, 3, 0)))
        self.assertEqual(self.Prompt.demo_feedback(new.id)['pending'], 0)
        with self.assertRaises(UserError):
            self.Prompt.demo_optimize(new.id)

    def test_invalid_rewrite_fails_without_touching_parent(self):
        self.Prompt.demo_feedback_add(self.prompt.id, 'Ngắn hơn', 'a', 'b')
        new = self.Prompt.browse(self.Prompt.demo_optimize(self.prompt.id)['id'])
        with patch('odoo.addons.dac_chat_playbook.models.chat_prompt.ai_call', lambda *a, **k: ({'system_prompt': 'quá ngắn', 'changes': []}, 'm')), \
             patch.object(self.env.cr, 'commit'), patch.object(self.env.cr, 'rollback'):
            self.Prompt._cron_optimize()
        self.assertEqual(new.state, 'failed')
        self.assertIn('không hợp lệ', new.error_message)
        self.assertEqual(self.Prompt.demo_prompt_state(new.id)['state'], 'failed')
