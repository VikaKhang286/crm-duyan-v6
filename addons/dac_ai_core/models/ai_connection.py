"""Kết nối AI dùng chung cho cả hệ thống: nhập API một lần, mọi module gọi qua đây, tự chuyển kết nối dự phòng khi lỗi."""
import json
import logging
import re
import time
from urllib.parse import urlsplit

import requests

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)

KINDS = [('openai', 'Tương thích OpenAI (9router, OpenAI...)'), ('gemini', 'Google Gemini')]
GEMINI_API = 'https://generativelanguage.googleapis.com/v1beta'
MODEL_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$')
GEMINI_NOT_FOR_TEXT = re.compile(r'(tts|image|imagen|veo|audio|live|embedding|aqa|robotics|computer-use|native)', re.IGNORECASE)
TOOL_NAME = re.compile(r'^[a-z][a-z0-9_]{0,63}$')
COOLDOWN_SECONDS = 60  # kết nối vừa lỗi mạng/quá tải: 1 phút sau mới thử lại, trong lúc đó đi thẳng kết nối kế tiếp
CONNECTION_FIELDS = {'kind', 'base_url', 'api_key'}


def json_schema(schema):
    """Khuôn kiểu Gemini (OBJECT/STRING...) → JSON Schema (object/string...)."""
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key == 'type' and isinstance(value, str):
                out[key] = value.lower()
            elif key == 'properties' and isinstance(value, dict):
                out[key] = {name: json_schema(sub) for name, sub in value.items()}
            elif key == 'items':
                out[key] = json_schema(value)
            else:
                out[key] = value
        return out
    return schema


def gemini_schema(schema):
    """JSON Schema → khuôn Schema của Gemini (type chữ hoa; bỏ các khóa Gemini không nhận)."""
    if isinstance(schema, dict):
        out = {}
        for key, value in schema.items():
            if key in ('additionalProperties', '$schema', 'title', 'default'):
                continue
            if key == 'type' and isinstance(value, str):
                out[key] = value.upper()
            elif key == 'properties' and isinstance(value, dict):
                out[key] = {name: gemini_schema(sub) for name, sub in value.items()}
            elif key == 'items':
                out[key] = gemini_schema(value)
            else:
                out[key] = value
        return out
    return schema


class AiModel(models.Model):
    """Một model của kết nối. Tích "Dùng" nhiều model và kéo để sắp thứ tự: model đầu lỗi/quá tải thì dùng model kế tiếp."""
    _name = 'dac.ai.model'
    _description = 'Model AI của một kết nối'
    _order = 'sequence, id'
    _sql_constraints = [('connection_name_unique', 'unique(connection_id, name)', 'Model này đã có trong danh sách.')]

    connection_id = fields.Many2one('dac.ai.connection', string='Kết nối', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(string='Thứ tự', default=10)
    name = fields.Char(string='Mã model', required=True, readonly=True)
    label = fields.Char(string='Tên hiển thị', readonly=True)
    selected = fields.Boolean(string='Dùng', default=False, help='Tích các model muốn dùng; thứ tự từ trên xuống là thứ tự dự phòng.')
    verified = fields.Boolean(string='Gọi tool được', readonly=True, copy=False,
        help='Đặt bởi nút "Kiểm tra các model đã chọn". Chỉ model đã kiểm tra thành công mới được dùng.')
    ok_count = fields.Integer(string='Thành công', readonly=True, copy=False)
    fail_count = fields.Integer(string='Lỗi', readonly=True, copy=False)
    last_error = fields.Char(string='Lỗi gần nhất', readonly=True, copy=False)
    last_error_at = fields.Datetime(string='Lỗi lúc', readonly=True, copy=False)
    status = fields.Selection([('ready', 'Sẵn sàng'), ('untested', 'Chưa kiểm tra'), ('off', 'Không dùng')], compute='_compute_status',
        string='Tình trạng')

    @api.depends('name', 'label')
    def _compute_display_name(self):
        for model in self:
            model.display_name = model.name if not model.label or model.label == model.name else '%s — %s' % (model.name, model.label)

    @api.depends('selected', 'verified')
    def _compute_status(self):
        for model in self:
            model.status = 'off' if not model.selected else ('ready' if model.verified else 'untested')

    def _cooling(self):
        error = self.last_error or ''
        if not self.last_error_at or not any(mark in error for mark in ('kết nối được', '429', 'HTTP 5', 'quá tải')):
            return False
        return 0 <= (fields.Datetime.now() - self.last_error_at).total_seconds() < COOLDOWN_SECONDS

    def _note(self, error):
        """Ghi tình trạng (model + kết nối) bằng cursor riêng: nhiều cron cùng gọi AI không tranh nhau ghi một dòng trong
        giao dịch chính. Dòng chưa commit (vừa tạo) thì ghi luôn trong giao dịch hiện tại."""
        now = fields.Datetime.now()
        if error is None:
            queries = [('UPDATE dac_ai_model SET ok_count = COALESCE(ok_count, 0) + 1 WHERE id = %s', (self.id,)),
                       ('UPDATE dac_ai_connection SET ok_count = COALESCE(ok_count, 0) + 1, last_ok_at = %s WHERE id = %s', (now, self.connection_id.id))]
        else:
            queries = [('UPDATE dac_ai_model SET fail_count = COALESCE(fail_count, 0) + 1, last_error = %s, last_error_at = %s WHERE id = %s',
                        (error[:200], now, self.id)),
                       ('UPDATE dac_ai_connection SET fail_count = COALESCE(fail_count, 0) + 1, last_error = %s, last_error_at = %s WHERE id = %s',
                        ('%s: %s' % (self.name, error)[:200], now, self.connection_id.id))]
        for query, params in queries:
            done = False
            try:
                with self.env.registry.cursor() as cr:
                    cr.execute(query, params)
                    done = cr.rowcount > 0
            except Exception:  # noqa: BLE001 — bộ đếm không được làm hỏng việc gọi AI
                _logger.debug('Không ghi được tình trạng AI')
            if not done:
                self.env.cr.execute(query, params)
        self.invalidate_recordset(['ok_count', 'fail_count', 'last_error', 'last_error_at'])
        self.connection_id.invalidate_recordset(['ok_count', 'fail_count', 'last_ok_at', 'last_error', 'last_error_at'])


class AiConnection(models.Model):
    _name = 'dac.ai.connection'
    _description = 'Kết nối AI dùng chung'
    _order = 'sequence, id'

    name = fields.Char(string='Tên kết nối', required=True)
    sequence = fields.Integer(string='Thứ tự', default=10, help='Kết nối đầu danh sách dùng trước; hết model dùng được thì sang kết nối kế tiếp.')
    active = fields.Boolean(string='Đang dùng', default=True)
    kind = fields.Selection(KINDS, string='Loại', required=True, default='openai')
    base_url = fields.Char(string='Địa chỉ API (Base URL)', groups='base.group_system',
        help='Chỉ cho loại tương thích OpenAI, thường kết thúc bằng /v1, vd https://9router.duyan.vn/v1')
    api_key = fields.Char(string='API key', copy=False, groups='base.group_system')
    model_line_ids = fields.One2many('dac.ai.model', 'connection_id', string='Model')
    model_summary = fields.Char(string='Model đang dùng', compute='_compute_model_summary')
    ok_count = fields.Integer(string='Gọi thành công', readonly=True, copy=False)
    fail_count = fields.Integer(string='Gọi lỗi', readonly=True, copy=False)
    last_ok_at = fields.Datetime(string='Thành công gần nhất', readonly=True, copy=False)
    last_error = fields.Char(string='Lỗi gần nhất', readonly=True, copy=False)
    last_error_at = fields.Datetime(string='Lỗi lúc', readonly=True, copy=False)
    note = fields.Char(string='Ghi chú')
    state = fields.Selection([('ready', 'Sẵn sàng'), ('untested', 'Chưa kiểm tra'), ('off', 'Đang tắt')], compute='_compute_state', string='Tình trạng')

    @api.depends('model_line_ids.selected', 'model_line_ids.verified', 'model_line_ids.sequence')
    def _compute_model_summary(self):
        for connection in self:
            chosen = connection.model_line_ids.filtered('selected').sorted(lambda m: (m.sequence, m.id))
            connection.model_summary = ' → '.join(m.name + ('' if m.verified else ' (chưa kiểm tra)') for m in chosen) or False

    @api.depends('active', 'model_line_ids.selected', 'model_line_ids.verified')
    def _compute_state(self):
        for connection in self:
            connection.state = 'off' if not connection.active else ('ready' if connection._usable_models() else 'untested')

    @api.constrains('kind', 'base_url')
    def _check_url(self):
        for connection in self.sudo():
            if connection.kind != 'openai':
                continue
            parsed = urlsplit(connection.base_url or '')
            if not connection.base_url:
                raise ValidationError(_('Nhập địa chỉ API cho kết nối tương thích OpenAI.'))
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValidationError(_('Địa chỉ API phải là http(s)://máy-chủ/đường-dẫn, không chứa tài khoản, query hay fragment.'))

    @api.model_create_multi
    def create(self, vals_list):
        self._require_admin()
        return super().create(vals_list)

    def write(self, vals):
        result = super().write(vals)
        if CONNECTION_FIELDS & set(vals):
            # Đổi loại/địa chỉ/key: kết quả kiểm tra cũ không còn đúng
            self.sudo().model_line_ids.write({'verified': False})
        return result

    def _require_admin(self):
        if not self.env.su and not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Chỉ quản trị hệ thống được thay đổi kết nối AI.'))

    def _usable_models(self):
        self.ensure_one()
        return self.model_line_ids.filtered(lambda m: m.selected and m.verified).sorted(lambda m: (m.sequence, m.id))

    # ── API cho module khác ──────────────────────────────────────────────────

    @api.model
    def _chain(self):
        # sudo(): module gọi AI có thể chạy trong cron/webhook không có user; key chỉ system đọc được
        return self.sudo().search([('active', '=', True)]).filtered(lambda c: c._usable_models())

    @api.model
    def _ready(self):
        return bool(self._chain())

    @api.model
    def _json(self, system, text, schema, name='report', description='Trả kết quả phân tích.', timeout=(10, 60), user=None):
        """Bắt model gọi tool `name` → (dict tham số, loại kết nối 'openai'|'gemini'). Không kết nối nào trả được → UserError.
        `user`: nhân viên đang dùng trợ lý; nếu admin đã chọn "Model AI riêng" cho người đó thì dùng model đó trước."""
        if not TOOL_NAME.match(name or ''):
            raise UserError(self.env._('Tên tool không hợp lệ.'))
        return self._run(lambda c, m: c._tool_call(system, text, name, description, schema, timeout, m), user)

    @api.model
    def _chat(self, messages, max_tokens=1000, timeout=(10, 45), user=None):
        """messages: [{'role': 'system'|'user'|'assistant', 'content': str}] → (câu trả lời, loại kết nối). `user`: xem `_json`."""
        return self._run(lambda c, m: c._chat_call(messages, max_tokens, timeout, m), user)

    @api.model
    def _plan(self, user=None):
        """[(kết nối, model)] theo thứ tự thử: model riêng của nhân viên (nếu có) rồi thứ tự chung."""
        plan = [(connection, model) for connection in self._chain() for model in connection._usable_models()]
        # sudo(): trường "Model AI riêng" chỉ admin đọc được; người dùng trợ lý không cần quyền đó
        own = user.sudo().dac_ai_model_id if user else self.env['dac.ai.model']
        if own and own.selected and own.verified and own.connection_id.active:
            plan = [(own.connection_id, own)] + [(c, m) for c, m in plan if m != own]
        return plan

    def _run(self, call, user=None):
        """Thử lần lượt: kết nối theo thứ tự, trong mỗi kết nối các model đã chọn theo thứ tự."""
        errors = []
        plan = self._plan(user)
        if not plan:
            raise UserError(self.env._('Chưa có kết nối AI nào dùng được (Cài đặt → Kết nối AI dùng chung).'))
        for connection, model in plan:
            label = '%s/%s' % (connection.name, model.name)
            if model._cooling():
                errors.append('%s: đang tạm nghỉ sau lỗi' % label)
                continue
            try:
                result = call(connection, model)
            except UserError as error:
                _logger.warning('AI "%s" lỗi, thử model/kết nối kế tiếp: %s', label, error)
                model._note(str(error))
                errors.append('%s: %s' % (label, error))
                continue
            model._note(None)
            return result, connection.kind
        raise UserError(self.env._('Không kết nối AI nào trả lời được. %s') % '; '.join(errors)[:400])

    # ── Gọi từng loại ────────────────────────────────────────────────────────

    def _request(self, method, url, headers, payload=None, timeout=(10, 60)):
        try:
            response = requests.request(method, url, headers=headers, json=payload, timeout=timeout, allow_redirects=False)
        except requests.exceptions.RequestException:
            raise UserError(_('Không kết nối được tới nhà cung cấp AI.')) from None
        if response.status_code in (400, 401, 403):
            raise UserError(_('Nhà cung cấp AI từ chối yêu cầu (HTTP %s). Kiểm tra API key và model.') % response.status_code)
        if response.status_code == 429:
            raise UserError(_('Nhà cung cấp AI báo quá tải (HTTP 429).'))
        if response.status_code != 200:
            raise UserError(_('Nhà cung cấp AI chưa xử lý được yêu cầu (HTTP %s).') % response.status_code)
        try:
            result = response.json()
        except ValueError:
            raise UserError(_('Nhà cung cấp AI trả dữ liệu không hợp lệ.')) from None
        if not isinstance(result, dict):
            raise UserError(_('Nhà cung cấp AI trả dữ liệu không hợp lệ.'))
        return result

    def _model_name(self, model):
        name = model.name if model else ''
        if not name or not MODEL_NAME.match(name):
            raise UserError(_('Kết nối "%s" chưa chọn model.') % self.name)
        return name

    def _openai(self, payload, timeout, model):
        if not self.api_key:
            raise UserError(_('Kết nối "%s" chưa có API key.') % self.name)
        return self._request('POST', self.base_url.rstrip('/') + '/chat/completions', {'Authorization': 'Bearer ' + self.api_key},
                             dict(payload, model=self._model_name(model)), timeout)

    def _gemini(self, payload, timeout, model):
        if not self.api_key:
            raise UserError(_('Kết nối "%s" chưa có API key.') % self.name)
        # Key đi trong header (không nằm trong URL nên không lọt vào log)
        return self._request('POST', '%s/models/%s:generateContent' % (GEMINI_API, self._model_name(model)), {'x-goog-api-key': self.api_key},
                             payload, timeout)

    def _tool_call(self, system, text, name, description, schema, timeout, model):
        self.ensure_one()
        if self.kind == 'gemini':
            result = self._gemini({
                'systemInstruction': {'parts': [{'text': system}]},
                'contents': [{'role': 'user', 'parts': [{'text': text}]}],
                'tools': [{'functionDeclarations': [{'name': name, 'description': description, 'parameters': gemini_schema(json_schema(schema))}]}],
                'toolConfig': {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': [name]}},
                'generationConfig': {'temperature': 0},
            }, timeout, model)
            try:
                parts = result['candidates'][0]['content']['parts']
                data = next(p['functionCall'].get('args') for p in parts if isinstance(p, dict) and (p.get('functionCall') or {}).get('name') == name)
            except (KeyError, IndexError, TypeError, StopIteration, AttributeError):
                raise UserError(_('Model không gọi tool như yêu cầu (có thể nội dung bị chặn).')) from None
        else:
            result = self._openai({
                'temperature': 0,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': text}],
                'tools': [{'type': 'function', 'function': {'name': name, 'description': description, 'parameters': json_schema(schema)}}],
                'tool_choice': {'type': 'function', 'function': {'name': name}},
            }, timeout, model)
            try:
                calls = result['choices'][0]['message'].get('tool_calls') or []
                arguments = next(c for c in calls if (c.get('function') or {}).get('name') == name)['function'].get('arguments')
                data = json.loads(arguments) if isinstance(arguments, str) else arguments
            except (KeyError, IndexError, TypeError, ValueError, StopIteration, AttributeError):
                raise UserError(_('Model không gọi tool như yêu cầu.')) from None
        if not isinstance(data, dict):
            raise UserError(_('Model gọi tool nhưng tham số không hợp lệ.'))
        return data

    def _chat_call(self, messages, max_tokens, timeout, model):
        self.ensure_one()
        if self.kind == 'gemini':
            system = '\n\n'.join(m['content'] for m in messages if m.get('role') == 'system')
            contents = [{'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': [{'text': m['content']}]}
                        for m in messages if m.get('role') in ('user', 'assistant')]
            payload = {'contents': contents, 'generationConfig': {'maxOutputTokens': max_tokens}}
            if system:
                payload['systemInstruction'] = {'parts': [{'text': system}]}
            result = self._gemini(payload, timeout, model)
            try:
                answer = ''.join(p.get('text', '') for p in result['candidates'][0]['content']['parts'] if isinstance(p, dict))
            except (KeyError, IndexError, TypeError):
                answer = ''
        else:
            result = self._openai({'messages': messages, 'max_tokens': max_tokens}, timeout, model)
            try:
                answer = result['choices'][0]['message']['content']
            except (KeyError, IndexError, TypeError):
                answer = ''
        if not isinstance(answer, str) or not answer.strip():
            raise UserError(_('Model không trả câu trả lời.'))
        return answer.strip()

    # ── Giao diện ────────────────────────────────────────────────────────────

    def action_load_models(self):
        self.ensure_one()
        self._require_admin()
        connection = self.sudo()
        found = {}
        if connection.kind == 'gemini':
            token = None
            for _page in range(10):
                url = GEMINI_API + '/models?pageSize=200' + ('&pageToken=' + requests.utils.quote(token, safe='') if token else '')
                result = connection._request('GET', url, {'x-goog-api-key': connection.api_key or ''}, timeout=(10, 20))
                for item in result.get('models') or []:
                    name = str(item.get('name') or '').removeprefix('models/')
                    if MODEL_NAME.match(name) and 'generateContent' in (item.get('supportedGenerationMethods') or []) and not GEMINI_NOT_FOR_TEXT.search(name):
                        found[name] = str(item.get('displayName') or '')[:120]
                token = result.get('nextPageToken')
                if not token:
                    break
        else:
            result = connection._request('GET', connection.base_url.rstrip('/') + '/models', {'Authorization': 'Bearer ' + (connection.api_key or '')},
                                         timeout=(10, 20))
            for item in result.get('data') or []:
                name = str(item.get('id') or '') if isinstance(item, dict) else ''
                if MODEL_NAME.match(name):
                    found[name] = ''
        if not found:
            raise UserError(_('Nhà cung cấp không trả model nào. Kiểm tra lại địa chỉ và API key.'))
        Model = self.env['dac.ai.model']
        existing = {m.name: m for m in Model.search([('connection_id', '=', self.id)])}
        Model.create([{'connection_id': self.id, 'name': n, 'label': label, 'sequence': 100} for n, label in found.items() if n not in existing])
        for name, model in existing.items():
            if name not in found and not model.selected:
                model.unlink()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': self.name, 'message': _('Đã lấy %d model. Tích "Dùng" các model muốn dùng, kéo để sắp thứ tự, rồi bấm "Kiểm tra".') % len(found),
            'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_save(self):
        """Nút "Lưu" trên thanh đầu form (Odoo tự lưu bản ghi trước khi gọi nút)."""
        self.ensure_one()
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': self.name, 'message': _('Đã lưu.'), 'type': 'success', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    def action_test(self):
        """Thử gọi tool với từng model đã tích "Dùng"; model nào gọi được thì đánh dấu sẵn sàng."""
        self.ensure_one()
        self._require_admin()
        connection = self.sudo()
        chosen = connection.model_line_ids.filtered('selected').sorted(lambda m: (m.sequence, m.id))
        if not chosen:
            raise UserError(_('Tích "Dùng" ít nhất một model trong bảng Model (bấm "Lấy danh sách model" nếu bảng trống).'))
        results = []
        for model in chosen:
            started = time.monotonic()
            try:
                data = connection._tool_call(
                    'Bạn là bộ phân loại. Luôn trả kết quả bằng cách gọi tool.', 'Khách nhắn: "Báo giá giúp em 500 tờ rơi A5, chiều nay lấy gấp."',
                    'report_test', 'Báo mức độ gấp của tin khách.',
                    {'type': 'object', 'properties': {'level': {'type': 'string', 'enum': ['urgent', 'high', 'normal', 'none']},
                                                      'summary': {'type': 'string'}}, 'required': ['level']}, (10, 60), model)
                if data.get('level') not in ('urgent', 'high', 'normal', 'none'):
                    raise UserError(_('Model gọi tool nhưng trả giá trị không đúng khuôn.'))
                model.write({'verified': True, 'last_error': False, 'last_error_at': False})
                results.append('✓ %s (%.1f giây)' % (model.name, time.monotonic() - started))
            except UserError as error:
                model.write({'verified': False, 'last_error': str(error)[:200], 'last_error_at': fields.Datetime.now()})
                results.append('✗ %s: %s' % (model.name, error))
        ok = any(line.startswith('✓') for line in results)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Kiểm tra %s') % self.name, 'message': '\n'.join(results), 'sticky': not ok,
            'type': 'success' if ok else 'danger', 'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}
