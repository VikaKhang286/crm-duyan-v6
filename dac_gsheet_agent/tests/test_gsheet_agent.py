import json
from datetime import date
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from odoo.tests import TransactionCase, tagged

from odoo.addons.dac_gsheet_agent.models import gsheet_client
from odoo.addons.dac_gsheet_agent.models.gsheet_client import (
    GSheetClient, a1_range, column_letter, parse_gid, parse_spreadsheet_id)
from odoo.addons.dac_gsheet_agent.models.gsheet_source import parse_amount, parse_date, norm_text
from odoo.addons.dac_gsheet_agent.models.llm_client import LLMClient, extract_json

SHEET_URL = 'https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit#gid=42'


def fake_table(rows):
    headers = ['Số ĐH', 'Khách hàng', 'SĐT', 'Tiến độ', 'Deadline']
    return {
        'headers': headers,
        'worksheet': 'Đơn xe đẩy',
        'rows': [{'row': i + 2, 'values': dict(zip(headers, r))} for i, r in enumerate(rows)],
    }


@tagged('dac_gsheet_agent', 'post_install', '-at_install')
class TestHelpers(TransactionCase):

    def test_parse_url_and_ranges(self):
        self.assertEqual(parse_spreadsheet_id(SHEET_URL), '1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789')
        self.assertEqual(parse_gid(SHEET_URL), 42)
        self.assertEqual(parse_spreadsheet_id('not a link'), '')
        self.assertEqual(column_letter(0), 'A')
        self.assertEqual(column_letter(25), 'Z')
        self.assertEqual(column_letter(26), 'AA')
        self.assertEqual(a1_range("Bob's tab", 'A1'), "'Bob''s tab'!A1")

    def test_normalizers(self):
        self.assertEqual(parse_date('05/11/2026'), date(2026, 11, 5))
        self.assertEqual(parse_date('2026-11-05 10:30'), date(2026, 11, 5))
        self.assertIsNone(parse_date('hôm qua'))
        self.assertEqual(parse_amount('1.250.000đ'), 1250000)
        self.assertEqual(parse_amount('1,250,000'), 1250000)
        self.assertEqual(norm_text('  Đang  Sản Xuất '), 'dang san xuat')

    def test_extract_json(self):
        self.assertEqual(extract_json('xx ```json\n{"a": 1}\n``` yy'), {'a': 1})
        self.assertEqual(extract_json('Kết quả: {"ops": []}'), {'ops': []})
        self.assertIsNone(extract_json('không có json'))

    def test_jwt_signing_and_readonly_guard(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
        info = {'client_email': 'bot@test.iam.gserviceaccount.com', 'private_key': pem}

        class Resp:
            status_code = 200

            @staticmethod
            def json():
                return {'access_token': 'tok-123', 'expires_in': 3600}

        gsheet_client._TOKEN_CACHE.clear()
        with patch.object(gsheet_client.requests, 'post', return_value=Resp()) as post:
            token = gsheet_client.get_access_token(info, gsheet_client.SCOPE_READ)
            self.assertEqual(token, 'tok-123')
            assertion = post.call_args.kwargs['data']['assertion']
            self.assertEqual(assertion.count('.'), 2)
            # gọi lần 2 dùng cache
            gsheet_client.get_access_token(info, gsheet_client.SCOPE_READ)
            self.assertEqual(post.call_count, 1)
        client = GSheetClient(info, 'abc', writable=False)
        with self.assertRaises(gsheet_client.GSheetError):
            client.batch_update_cells('Tab', [(2, 0, 'x')])


@tagged('dac_gsheet_agent', 'post_install', '-at_install')
class TestCheckAndAgent(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env['res.partner'].create({'name': 'Chị Lan Test', 'phone': '0909123456'})
        cls.order = cls.env['sale.order'].create({
            'partner_id': cls.partner.id,
            'order_type': 'cart',
            'order_number': 'XD-001',
            'production_deadline': date(2026, 11, 20),
        })
        cls.order_missing_on_sheet = cls.env['sale.order'].create({
            'partner_id': cls.partner.id, 'order_type': 'cart', 'order_number': 'XD-999',
        })
        cls.source = cls.env['dac.gsheet.source'].create({
            'name': 'Sheet test',
            'url': SHEET_URL,
            'worksheet_name': 'Đơn xe đẩy',
            'key_header': 'Số ĐH',
            'access_mode': 'write',
            'column_ids': [
                (0, 0, {'header': 'Số ĐH', 'odoo_field': 'order_number'}),
                (0, 0, {'header': 'SĐT', 'odoo_field': 'partner_phone'}),
                (0, 0, {'header': 'Tiến độ', 'odoo_field': 'order_state_custom'}),
                (0, 0, {'header': 'Deadline', 'odoo_field': 'production_deadline'}),
            ],
            'state_map_ids': [(0, 0, {'sheet_value': 'Đang SX', 'state': 'production'})],
        })
        cls.table = fake_table([
            ['XD-001', 'Chị Lan', '+84 909 123 456', 'Đang SX', '22/11/2026'],
            ['XD-002', 'Anh Minh', '0912000111', 'Báo giá', ''],
            ['XD-001', 'Chị Lan', '0909123456', 'Đang SX', '22/11/2026'],
            ['XD-003', 'Chú Ba', '0913000222', 'Chờ vật tư', ''],
        ])

    def _run(self, table=None):
        with patch.object(type(self.source), 'fetch_table', return_value=table or self.table):
            return self.env['dac.gsheet.check.run'].run_for_source(self.source)

    def test_check_finds_discrepancies(self):
        run = self._run()
        self.assertEqual(run.state, 'ok')
        kinds = run.discrepancy_ids.mapped('kind')
        self.assertIn('duplicate_key', kinds)
        self.assertIn('missing_in_odoo', kinds)
        self.assertIn('missing_in_sheet', kinds)
        mismatches = run.discrepancy_ids.filtered(lambda d: d.kind == 'field_mismatch')
        fields_lechs = set(mismatches.mapped('field_key'))
        # quotation (Odoo) ≠ production (sheet), deadline 20 ≠ 22; SĐT +84 khớp sau chuẩn hoá
        self.assertEqual(fields_lechs, {'order_state_custom', 'production_deadline'})
        missing_sheet = run.discrepancy_ids.filtered(lambda d: d.kind == 'missing_in_sheet')
        self.assertEqual(missing_sheet.order_id, self.order_missing_on_sheet)

    def test_ignored_discrepancy_not_reported_again(self):
        run = self._run()
        dup = run.discrepancy_ids.filtered(lambda d: d.kind == 'duplicate_key')
        dup.action_ignore()
        run2 = self._run()
        self.assertNotIn('duplicate_key', run2.discrepancy_ids.mapped('kind'))
        self.assertEqual(run2.new_discrepancy_count, 0)
        self.assertTrue(all(d.state in ('superseded', 'ignored') for d in run.discrepancy_ids))

    def test_proposal_apply_updates_order(self):
        session = self.env['dac.gsheet.agent.session'].create({
            'source_ids': [(6, 0, self.source.ids)], 'order_ids': [(6, 0, self.order.ids)]})
        msg = session._tool_propose_order_update(
            self.order.id, {'production_deadline': '2026-11-22'}, 'Sheet dòng 2 ghi 22/11')
        self.assertIn('đề xuất', msg)
        proposal = session.proposal_ids
        self.assertEqual(proposal.state, 'pending')
        self.assertEqual(self.order.production_deadline, date(2026, 11, 20), 'Chưa duyệt thì chưa ghi')
        proposal.action_apply()
        self.assertEqual(proposal.state, 'applied', proposal.error_message)
        self.assertEqual(self.order.production_deadline, date(2026, 11, 22))

    def test_proposal_rejects_forbidden_field(self):
        session = self.env['dac.gsheet.agent.session'].create({'source_ids': [(6, 0, self.source.ids)]})
        with self.assertRaises(Exception):
            session._execute_tool('propose_order_update', {
                'order_id': self.order.id, 'changes': {'amount_total': 1}, 'reason': 'x'})
        self.assertFalse(session.proposal_ids)

    def test_proposal_create_order(self):
        session = self.env['dac.gsheet.agent.session'].create({'source_ids': [(6, 0, self.source.ids)]})
        session._tool_propose_order_create('Có trên sheet dòng 3', partner_name='Anh Minh',
                                           partner_phone='0912000111', order_number='XD-002')
        proposal = session.proposal_ids
        proposal.action_apply()
        self.assertEqual(proposal.state, 'applied', proposal.error_message)
        self.assertEqual(proposal.order_id.order_type, 'cart')
        self.assertEqual(proposal.order_id.order_number, 'XD-002')

    def test_readonly_source_blocks_sheet_proposals(self):
        self.source.access_mode = 'read'
        session = self.env['dac.gsheet.agent.session'].create({'source_ids': [(6, 0, self.source.ids)]})
        self.assertNotIn('propose_sheet_update', [t['name'] for t in session._tools()])
        with self.assertRaises(Exception):
            session._execute_tool('propose_sheet_update', {
                'source_id': self.source.id, 'sheet_row': 2, 'values': {'Tiến độ': 'Xong'}, 'reason': 'x'})

    def test_source_outside_context_rejected(self):
        session = self.env['dac.gsheet.agent.session'].create({})
        with self.assertRaises(Exception):
            session._execute_tool('read_sheet', {'source_id': self.source.id})

    def test_rule_dedupe_and_auto_archive(self):
        Rule = self.env['dac.gsheet.agent.rule']
        rule, _msg = Rule.add_rule('Cột Tiến độ ghi "Đang SX" nghĩa là trạng thái production', 'status_mapping')
        same, msg = Rule.add_rule('Cột Tiến độ ghi "Đang SX" nghĩa là trạng thái production.', 'status_mapping')
        self.assertEqual(rule, same)
        self.assertEqual(rule.helpful_count, 1)
        self.assertIn('tương tự', msg)
        for _i in range(4):
            rule.mark(helpful=False)
        self.assertEqual(rule.state, 'archived')
        self.assertNotIn(rule, Rule.get_playbook(self.source))

    def test_reflection_applies_operations(self):
        Rule = self.env['dac.gsheet.agent.rule']
        old, _m = Rule.add_rule('Luôn khớp đơn theo tên khách hàng trước', 'matching')
        session = self.env['dac.gsheet.agent.session'].create({'source_ids': [(6, 0, self.source.ids)]})
        self.env['dac.gsheet.agent.message'].create({'session_id': session.id, 'role': 'user', 'content': 'Khớp theo SĐT'})
        answer = json.dumps({'insights': ['Nên khớp theo SĐT'], 'operations': [
            {'op': 'ADD', 'category': 'matching', 'scope': 'source', 'content': 'Khớp khách theo SĐT đã chuẩn hoá, không theo tên'},
            {'op': 'HARMFUL', 'rule_id': old.id},
        ]})
        with patch.object(LLMClient, '__init__', return_value=None), \
                patch.object(LLMClient, 'complete', return_value=answer):
            session.action_reflect()
        new_rule = Rule.search([('content', 'ilike', 'SĐT đã chuẩn hoá')])
        self.assertTrue(new_rule)
        self.assertEqual(new_rule.source_id, self.source)
        self.assertEqual(new_rule.origin, 'reflection')
        self.assertEqual(old.harmful_count, 1)
        self.assertTrue(session.reflected_at)

    def test_chat_loop_with_mocked_llm(self):
        """Mô phỏng một lượt Claude: gọi tool get_orders rồi trả lời."""
        self.env['ir.config_parameter'].set_param('dac_gsheet_agent.llm_api_key', 'test-key')
        self.env['ir.config_parameter'].set_param('dac_gsheet_agent.llm_provider', 'anthropic')
        responses = [
            {'stop_reason': 'tool_use', 'content': [
                {'type': 'thinking', 'thinking': '', 'signature': 'sig'},
                {'type': 'tool_use', 'id': 'toolu_1', 'name': 'get_orders', 'input': {'order_number': 'XD-001'}},
            ]},
            {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Đơn XD-001 đang ở Báo giá.'}]},
        ]
        sent = []

        def fake_post(self_client, body):
            sent.append(json.loads(json.dumps(body)))
            return responses[len(sent) - 1]

        session = self.env['dac.gsheet.agent.session'].create({'source_ids': [(6, 0, self.source.ids)]})
        with patch.object(LLMClient, '_anthropic_post', fake_post):
            payload = session.ui_send('Đơn XD-001 sao rồi?')
        self.assertEqual(payload['messages'][-1]['content'], 'Đơn XD-001 đang ở Báo giá.')
        self.assertIn('get_orders', payload['messages'][-1]['tools'])
        # vòng 2 phải gửi lại nguyên assistant content (gồm thinking) + tool_result trong 1 user message
        second = sent[1]['messages']
        self.assertEqual(second[-2]['content'][0]['type'], 'thinking')
        self.assertEqual(second[-1]['content'][0]['type'], 'tool_result')
        self.assertIn('XD-001', second[-1]['content'][0]['content'])
        self.assertIn('PLAYBOOK', sent[0]['system'])
        # lượt sau dựng lại lịch sử native từ DB
        history = session._native_history(LLMClient(self.source.get_llm_config()))
        self.assertEqual([m['role'] for m in history], ['user', 'assistant', 'user', 'assistant'])
