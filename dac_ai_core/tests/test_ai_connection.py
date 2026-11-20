import json
from unittest.mock import MagicMock, patch

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import new_test_user, tagged
from odoo.tests.common import TransactionCase

from odoo.addons.dac_ai_core.models.ai_connection import gemini_schema, json_schema

REQUEST = 'odoo.addons.dac_ai_core.models.ai_connection.requests.request'
SCHEMA = {'type': 'object', 'properties': {'level': {'type': 'string', 'enum': ['urgent', 'normal']},
                                           'items': {'type': 'array', 'items': {'type': 'integer'}}}, 'required': ['level']}


def response(status=200, body=None):
    mock = MagicMock(status_code=status)
    mock.json.return_value = body if body is not None else {}
    return mock


def openai_tool(name, arguments):
    return response(body={'choices': [{'message': {'tool_calls': [{'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments)}}]}}]})


def gemini_tool(name, args):
    return response(body={'candidates': [{'content': {'parts': [{'functionCall': {'name': name, 'args': args}}]}}]})


@tagged('post_install', '-at_install')
class TestAiConnection(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Connection = cls.env['dac.ai.connection']
        Connection.with_context(active_test=False).search([]).unlink()
        cls.router = Connection.create({'name': '9router', 'kind': 'openai', 'base_url': 'https://router.example/v1', 'api_key': 'k-router', 'sequence': 1})
        cls.gemini = Connection.create({'name': 'Gemini', 'kind': 'gemini', 'api_key': 'k-gemini', 'sequence': 2})
        Model = cls.env['dac.ai.model']
        cls.gpt = Model.create({'connection_id': cls.router.id, 'name': 'gpt-test', 'sequence': 1, 'selected': True, 'verified': True})
        cls.gpt_backup = Model.create({'connection_id': cls.router.id, 'name': 'gpt-backup', 'sequence': 2, 'selected': True, 'verified': True})
        cls.unused = Model.create({'connection_id': cls.router.id, 'name': 'gpt-unused', 'sequence': 3})
        Model.create({'connection_id': cls.gemini.id, 'name': 'gemini-test', 'selected': True, 'verified': True})

    def test_schema_conversion_both_ways(self):
        gemini = gemini_schema(SCHEMA)
        self.assertEqual((gemini['type'], gemini['properties']['level']['type'], gemini['properties']['items']['items']['type']),
                         ('OBJECT', 'STRING', 'INTEGER'))
        self.assertEqual(json_schema(gemini), SCHEMA)

    def test_first_connection_must_call_the_tool(self):
        with patch(REQUEST, return_value=openai_tool('report', {'level': 'urgent'})) as request:
            self.assertEqual(self.env['dac.ai.connection']._json('hệ thống', 'nội dung', gemini_schema(SCHEMA), name='report'),
                             ({'level': 'urgent'}, 'openai'))
        method, url = request.call_args.args
        payload = request.call_args.kwargs['json']
        self.assertEqual((method, url, request.call_args.kwargs['headers']), ('POST', 'https://router.example/v1/chat/completions',
                                                                             {'Authorization': 'Bearer k-router'}))
        self.assertEqual(payload['tool_choice'], {'type': 'function', 'function': {'name': 'report'}})
        self.assertEqual(payload['tools'][0]['function']['parameters'], SCHEMA)
        self.assertEqual(payload['model'], 'gpt-test')
        self.assertEqual((self.router.ok_count, self.gpt.ok_count), (1, 1))
        self.assertEqual(self.router.model_summary, 'gpt-test → gpt-backup')

    def test_model_1_fails_then_model_2_of_the_same_connection_answers(self):
        models = []

        def fake(method, url, json=None, **kwargs):
            models.append(json['model'])
            return response(429) if json['model'] == 'gpt-test' else openai_tool('report', {'level': 'high'})
        with patch(REQUEST, side_effect=fake):
            self.assertEqual(self.env['dac.ai.connection']._json('s', 't', SCHEMA), ({'level': 'high'}, 'openai'))
            self.env['dac.ai.connection']._json('s', 't', SCHEMA)
        self.assertEqual(models, ['gpt-test', 'gpt-backup', 'gpt-backup'], 'model 1 quá tải: lần sau đi thẳng model 2; model không tích không dùng')
        self.assertIn('429', self.gpt.last_error)
        self.assertEqual(self.gpt_backup.ok_count, 2)

    def test_all_models_of_a_connection_fail_then_the_next_connection_is_used(self):
        calls = []

        def fake(method, url, **kwargs):
            calls.append(url)
            return response(503) if 'router' in url else gemini_tool('report', {'level': 'normal'})
        with patch(REQUEST, side_effect=fake):
            self.assertEqual(self.env['dac.ai.connection']._json('s', 't', SCHEMA), ({'level': 'normal'}, 'gemini'))
            self.assertIn('HTTP 503', self.router.last_error)
            self.env['dac.ai.connection']._json('s', 't', SCHEMA)
        self.assertEqual(sum('router' in u for u in calls), 2, 'thử cả 2 model của 9router một lần, lần sau bỏ qua')
        self.assertTrue(calls[-1].endswith('/models/gemini-test:generateContent'))
        self.assertNotIn('k-gemini', calls[-1], 'key Gemini đi trong header, không nằm trong URL')

    def test_gemini_request_forces_the_function(self):
        self.router.active = False
        with patch(REQUEST, return_value=gemini_tool('report', {'level': 'urgent'})) as request:
            self.env['dac.ai.connection']._json('s', 't', SCHEMA, name='report')
        payload = request.call_args.kwargs['json']
        self.assertEqual(payload['toolConfig'], {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': ['report']}})
        self.assertEqual(payload['tools'][0]['functionDeclarations'][0]['parameters']['type'], 'OBJECT')

    def test_answer_without_tool_call_is_an_error_and_all_failing_raises(self):
        plain = response(body={'choices': [{'message': {'content': '{"level": "urgent"}'}}], 'candidates': [{'finishReason': 'SAFETY'}]})
        with patch(REQUEST, return_value=plain):
            with self.assertRaisesRegex(UserError, 'Không kết nối AI nào'):
                self.env['dac.ai.connection']._json('s', 't', SCHEMA)
            with self.assertRaisesRegex(UserError, 'không gọi tool'):
                self.router._tool_call('s', 't', 'report', 'mô tả', SCHEMA, (1, 1), self.gpt)

    def test_only_selected_and_tested_models_are_used(self):
        Connection = self.env['dac.ai.connection']
        self.router.write({'api_key': 'khac'})
        self.assertEqual((self.gpt.verified, self.gpt_backup.verified, self.router.state), (False, False, 'untested'), 'đổi key phải kiểm tra lại')
        self.gemini.active = False
        self.assertFalse(Connection._ready())
        with self.assertRaisesRegex(UserError, 'Chưa có kết nối AI'):
            Connection._json('s', 't', SCHEMA)

        def fake(method, url, json=None, **kwargs):
            return openai_tool('report_test', {'level': 'urgent'}) if json['model'] == 'gpt-backup' else response(400)
        with patch(REQUEST, side_effect=fake) as request:
            result = self.router.action_test()
        self.assertEqual(request.call_count, 2, 'chỉ kiểm tra model đã tích Dùng')
        self.assertIn('✓ gpt-backup', result['params']['message'])
        self.assertIn('✗ gpt-test', result['params']['message'])
        self.assertEqual((self.gpt.status, self.gpt_backup.status, self.unused.status), ('untested', 'ready', 'off'))
        self.assertTrue(Connection._ready())
        self.assertEqual(self.router.action_save()['params']['message'], 'Đã lưu.')
        (self.gpt | self.gpt_backup).write({'selected': False})
        self.assertFalse(Connection._ready())
        with self.assertRaisesRegex(UserError, 'Tích "Dùng"'):
            self.router.action_test()

    def test_admin_can_pick_a_model_per_employee(self):
        user = new_test_user(self.env, login='nv_ai', groups='base.group_user')
        user.dac_ai_model_id = self.gpt_backup
        Connection = self.env['dac.ai.connection']
        self.assertEqual([m.name for _c, m in Connection._plan(user)], ['gpt-backup', 'gpt-test', 'gemini-test'])
        self.assertEqual([m.name for _c, m in Connection._plan()], ['gpt-test', 'gpt-backup', 'gemini-test'])
        models = []

        def fake(method, url, json=None, **kwargs):
            models.append(json.get('model') if json else url)
            if json and json.get('model') == 'gpt-backup':
                return response(503)
            return response(body={'choices': [{'message': {'content': 'ok'}}]})
        with patch(REQUEST, side_effect=fake):
            self.assertEqual(Connection.with_user(user)._chat([{'role': 'user', 'content': 'hi'}], user=user), ('ok', 'openai'))
        self.assertEqual(models, ['gpt-backup', 'gpt-test'], 'model riêng lỗi thì quay về thứ tự chung')
        with self.assertRaises(AccessError):
            user.with_user(user).read(['dac_ai_model_id'])

    def test_chat_both_kinds(self):
        with patch(REQUEST, return_value=response(body={'choices': [{'message': {'content': ' Xin chào '}}]})):
            self.assertEqual(self.env['dac.ai.connection']._chat([{'role': 'user', 'content': 'hi'}]), ('Xin chào', 'openai'))
        self.router.active = False
        with patch(REQUEST, return_value=response(body={'candidates': [{'content': {'parts': [{'text': 'Chào bạn'}]}}]})) as request:
            answer = self.env['dac.ai.connection']._chat([{'role': 'system', 'content': 'ngắn'}, {'role': 'user', 'content': 'hi'}])
        self.assertEqual(answer, ('Chào bạn', 'gemini'))
        self.assertEqual(request.call_args.kwargs['json']['systemInstruction'], {'parts': [{'text': 'ngắn'}]})

    def test_load_models_and_access(self):
        listing = {'data': [{'id': 'cx/gpt-5.2'}, {'id': 'gemini-3-flash'}, {'id': 'bad name'}]}
        with patch(REQUEST, return_value=response(body=listing)):
            self.router.action_load_models()
        names = set(self.env['dac.ai.model'].search([('connection_id', '=', self.router.id)]).mapped('name'))
        self.assertEqual(names, {'cx/gpt-5.2', 'gemini-3-flash', 'gpt-test', 'gpt-backup'}, 'giữ model đang tích Dùng, bỏ model không tích đã biến mất')
        self.assertFalse(self.env['dac.ai.model'].search([('name', '=', 'cx/gpt-5.2')]).selected, 'model mới lấy về chưa tích')
        user = new_test_user(self.env, login='ai_user', groups='base.group_user')
        with self.assertRaises(AccessError):
            self.env['dac.ai.connection'].with_user(user).search([])
        with self.assertRaises(ValidationError):
            self.env['dac.ai.connection'].create({'name': 'x', 'kind': 'openai', 'base_url': 'ftp://x', 'api_key': 'k'})
