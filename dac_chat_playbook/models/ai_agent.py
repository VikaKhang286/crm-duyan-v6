"""Lớp gọi AI của addon, dựa trên kết nối dùng chung (dac_ai_core):

* `ai_call`: một lần gọi bắt buộc trả về đúng một tool (chỉ chữ), qua model chỉ định hoặc thứ tự chung.
* `vision_call`: như trên nhưng gửi kèm ảnh (base64), cho model đọc được ảnh (Claude, Gemini…). Model không nhận ảnh → gọi lại chỉ chữ.
* `AgentLoop`: vòng Agent nhiều bước có tool (truy vấn nhãn, đọc lại hội thoại…). Trạng thái lưu được (JSON) để mỗi lượt cron
  chạy MỘT bước rồi commit, không vượt giới hạn thời gian của worker.

Dùng phần truyền tải `_openai` / `_gemini` của dac.ai.connection (key, địa chỉ, xử lý lỗi HTTP) và tự dựng payload.
"""
import base64
import io
import json
import logging
from urllib.parse import urlsplit

import requests

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 4 * 1024 * 1024
VISION_TIMEOUT = (10, 180)  # gọi có nhiều ảnh lâu hơn gọi chữ
VISION_RETRY_IMAGES = 8     # lần thử lại: chỉ gửi bấy nhiêu ảnh đầu
IMAGE_WIDTH = 768
TOOL_RESULT_CHARS = 9000


def _model_plan(env, model, fallback):
    """[(kết nối, model)] theo thứ tự thử: model chỉ định trước, rồi (nếu cho phép) thứ tự chung."""
    plan = []
    model = model.sudo()  # sudo(): key AI chỉ admin đọc được; người bấm nút không cần thấy key
    if model:
        plan.append((model.connection_id, model))
    if fallback or not model:
        plan += [(c, m) for c, m in env['dac.ai.connection']._plan() if m != model]
    return plan


def ai_call(env, model, fallback, system, text, schema, name, description, timeout):
    """Gọi model (dac.ai.model, có thể rỗng) bắt buộc gọi tool `name`; lỗi thì dùng thứ tự chung nếu `fallback`.
    Trả (dict, nhãn model đã dùng)."""
    model = model.sudo()
    if model:
        try:
            data = model.connection_id._tool_call(system, text, name, description, schema, timeout, model)
            model._note(None)
            return data, model.display_name
        except UserError as error:
            model._note(str(error))
            if not fallback:
                raise
            _logger.warning('Model %s lỗi, dùng thứ tự chung: %s', model.name, error)
    data, kind = env['dac.ai.connection']._json(system, text, schema, name=name, description=description, timeout=timeout)
    return data, 'thứ tự chung (%s)' % kind


def fetch_image(url):
    """Tải ảnh https (≤ 4 MB), thu về JPEG rộng ≤ 768px → chuỗi base64, lỗi → None. Không ghi URL đầy đủ vào log."""
    from PIL import Image
    if not url or urlsplit(url).scheme != 'https':
        return None
    try:
        with requests.get(url, timeout=(5, 15), stream=True) as response:
            response.raise_for_status()
            blob = response.raw.read(MAX_IMAGE_BYTES + 1, decode_content=True)
        if not blob or len(blob) > MAX_IMAGE_BYTES:
            return None
        with Image.open(io.BytesIO(blob)) as image:
            image = image.convert('RGB')
            if image.width > IMAGE_WIDTH:
                image = image.resize((IMAGE_WIDTH, max(1, round(image.height * IMAGE_WIDTH / image.width))))
            out = io.BytesIO()
            image.save(out, 'JPEG', quality=80)
            return base64.b64encode(out.getvalue()).decode()
    except (requests.RequestException, OSError, ValueError):
        _logger.info('Không tải được ảnh từ %s', urlsplit(url).netloc)
        return None


def _json_schema(connection, schema):
    from odoo.addons.dac_ai_core.models.ai_connection import gemini_schema, json_schema
    return gemini_schema(json_schema(schema)) if connection.kind == 'gemini' else json_schema(schema)


def usage_of(result):
    """{'in': token vào, 'out': token ra} từ phản hồi OpenAI-compatible hoặc Gemini (thiếu thì 0)."""
    if not isinstance(result, dict):
        return {'in': 0, 'out': 0}
    usage = result.get('usage') or {}
    meta = result.get('usageMetadata') or {}
    return {'in': int(usage.get('prompt_tokens') or meta.get('promptTokenCount') or 0),
            'out': int(usage.get('completion_tokens') or 0) or int(meta.get('candidatesTokenCount') or 0) + int(meta.get('thoughtsTokenCount') or 0)}


def _vision_once(connection, model, system, text, images, name, description, schema, timeout):
    """Một lần gọi có ảnh (images: [(nhãn, base64 JPEG)]) bắt buộc gọi tool `name` → (dict, usage)."""
    if connection.kind == 'gemini':
        parts = [{'text': text}]
        for label, data in images:
            parts += [{'text': label}, {'inlineData': {'mimeType': 'image/jpeg', 'data': data}}]
        result = connection._gemini({
            'systemInstruction': {'parts': [{'text': system}]},
            'contents': [{'role': 'user', 'parts': parts}],
            'tools': [{'functionDeclarations': [{'name': name, 'description': description, 'parameters': _json_schema(connection, schema)}]}],
            'toolConfig': {'functionCallingConfig': {'mode': 'ANY', 'allowedFunctionNames': [name]}},
            'generationConfig': {'temperature': 0},
        }, timeout, model)
        try:
            parts = result['candidates'][0]['content']['parts']
            data = next(p['functionCall'].get('args') for p in parts if isinstance(p, dict) and (p.get('functionCall') or {}).get('name') == name)
        except (KeyError, IndexError, TypeError, StopIteration, AttributeError):
            raise UserError('Model không gọi tool như yêu cầu.') from None
    else:
        content = [{'type': 'text', 'text': text}]
        for label, data in images:
            content += [{'type': 'text', 'text': label}, {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + data}}]
        result = connection._openai({
            'temperature': 0,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
            'tools': [{'type': 'function', 'function': {'name': name, 'description': description, 'parameters': _json_schema(connection, schema)}}],
            'tool_choice': {'type': 'function', 'function': {'name': name}},
        }, timeout, model)
        try:
            calls = result['choices'][0]['message'].get('tool_calls') or []
            arguments = next(c for c in calls if (c.get('function') or {}).get('name') == name)['function'].get('arguments')
            data = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (KeyError, IndexError, TypeError, ValueError, StopIteration, AttributeError):
            raise UserError('Model không gọi tool như yêu cầu.') from None
    if not isinstance(data, dict):
        raise UserError('Model gọi tool nhưng tham số không hợp lệ.')
    return data, usage_of(result)


def vision_call(env, model, fallback, system, text, images, schema, name, description, timeout):
    """Gọi có ảnh. Trả (dict, nhãn model, số ảnh đã gửi được, usage {'in','out'}).

    Chỉ gửi ảnh cho model chính (model chỉ định, hoặc model đầu tiên của thứ tự chung): model dự phòng thường không đọc được ảnh.
    Lỗi → thử lại với ít ảnh hơn → cuối cùng gọi chỉ chữ (lúc đó mới dùng thứ tự dự phòng)."""
    plan = _model_plan(env, model, fallback)
    errors = []
    if plan:
        connection, item = plan[0]
        attempts = [images] + ([images[:VISION_RETRY_IMAGES]] if len(images) > VISION_RETRY_IMAGES else [])
        for batch in attempts:
            try:
                data, usage = _vision_once(connection, item, system, text, batch, name, description, schema,
                                           VISION_TIMEOUT if batch else timeout)
                item._note(None)
                return data, item.display_name, len(batch), usage
            except UserError as error:
                errors.append('%s (%s ảnh): %s' % (item.name, len(batch), error))
                _logger.info('Model %s không xử lý được %s ảnh: %s', item.name, len(batch), error)
    _logger.warning('Model chính lỗi (%s), gọi lại chỉ chữ qua thứ tự dự phòng', '; '.join(errors)[:300])
    note = '\n\n(Ảnh không gửi được cho AI; chỉ dựa vào chữ.)' if images else ''
    data, label = ai_call(env, model, fallback, system, text + note, schema, name, description, timeout)
    return data, label, 0, {}


class AgentLoop:
    """Vòng Agent có tool, chạy từng bước. state (JSON được):
    {'kind': 'openai'|'gemini', 'model_id': id, 'messages': [...native...], 'rounds': n, 'done': bool, 'result': {...}}"""

    def __init__(self, env, system, tools, executor, finish_name, max_rounds=10, timeout=(10, 90)):
        self.env, self.system, self.tools, self.executor = env, system, tools, executor
        self.finish_name, self.max_rounds, self.timeout = finish_name, max_rounds, timeout

    def start(self, model, fallback, first_text):
        plan = _model_plan(self.env, model, fallback)
        if not plan:
            raise UserError('Chưa có kết nối AI nào dùng được (Cài đặt → Kết nối AI dùng chung).')
        connection, item = plan[0]
        if connection.kind == 'gemini':
            messages = [{'role': 'user', 'parts': [{'text': first_text}]}]
        else:
            messages = [{'role': 'user', 'content': first_text}]
        return {'kind': connection.kind, 'model_id': item.id, 'model_label': item.display_name, 'messages': messages, 'rounds': 0,
                'done': False, 'result': None}

    def _model(self, state):
        model = self.env['dac.ai.model'].sudo().browse(state['model_id']).exists()
        if not model:
            raise UserError('Model AI của Agent không còn.')
        return model.connection_id, model

    def step(self, state):
        """Một vòng: gọi model, chạy các tool nó gọi. Trả state mới (done=True khi model gọi tool kết thúc)."""
        connection, model = self._model(state)
        state = dict(state, rounds=state['rounds'] + 1)
        force = state['rounds'] >= self.max_rounds
        if state['kind'] == 'gemini':
            state = self._step_gemini(connection, model, state, force)
        else:
            state = self._step_openai(connection, model, state, force)
        model._note(None)
        return state

    def _declarations(self, connection):
        return [{'name': t['name'], 'description': t['description'], 'parameters': _json_schema(connection, t['parameters'])}
                for t in self.tools]

    def _run_tool(self, name, args):
        if name == self.finish_name:
            return None
        try:
            output = self.executor(name, args or {})
        except Exception as error:  # noqa: BLE001 — lỗi tool trả lại cho model tự xử lý
            output = 'LỖI: %s' % error
        if not isinstance(output, str):
            output = json.dumps(output, ensure_ascii=False, default=str)
        return output[:TOOL_RESULT_CHARS]

    def _step_openai(self, connection, model, state, force):
        payload = {'temperature': 0, 'messages': [{'role': 'system', 'content': self.system}] + state['messages'],
                   'tools': [{'type': 'function', 'function': d} for d in self._declarations(connection)],
                   'tool_choice': {'type': 'function', 'function': {'name': self.finish_name}} if force else 'required'}
        result = connection._openai(payload, self.timeout, model)
        state['last_usage'] = usage_of(result)
        try:
            message = result['choices'][0]['message'] or {}
        except (KeyError, IndexError, TypeError):
            raise UserError('Model trả dữ liệu không hợp lệ.') from None
        calls = message.get('tool_calls') or []
        state['messages'].append({k: v for k, v in (('role', 'assistant'), ('content', message.get('content') or ''),
                                                    ('tool_calls', calls or None)) if v is not None})
        if not calls:
            state['messages'].append({'role': 'user', 'content': 'Hãy dùng tool để tra cứu, hoặc gọi %s để trả kết quả.' % self.finish_name})
            return state
        for call in calls:
            fn = call.get('function') or {}
            try:
                args = json.loads(fn.get('arguments') or '{}')
            except ValueError:
                args = {}
            if fn.get('name') == self.finish_name:
                state.update(done=True, result=args if isinstance(args, dict) else {})
                return state
            state['messages'].append({'role': 'tool', 'tool_call_id': call.get('id'), 'content': self._run_tool(fn.get('name'), args)})
        return state

    def _step_gemini(self, connection, model, state, force):
        config = {'mode': 'ANY'}
        if force:
            config['allowedFunctionNames'] = [self.finish_name]
        result = connection._gemini({'systemInstruction': {'parts': [{'text': self.system}]}, 'contents': state['messages'],
                                     'tools': [{'functionDeclarations': self._declarations(connection)}],
                                     'toolConfig': {'functionCallingConfig': config}, 'generationConfig': {'temperature': 0}},
                                    self.timeout, model)
        state['last_usage'] = usage_of(result)
        try:
            content = result['candidates'][0]['content']
            parts = content.get('parts') or []
        except (KeyError, IndexError, TypeError, AttributeError):
            raise UserError('Model không trả nội dung (có thể bị chặn).') from None
        state['messages'].append({'role': 'model', 'parts': parts})  # giữ nguyên (kể cả chữ ký suy nghĩ) để gửi lại
        calls = [p['functionCall'] for p in parts if isinstance(p, dict) and p.get('functionCall')]
        if not calls:
            state['messages'].append({'role': 'user', 'parts': [{'text': 'Hãy dùng tool để tra cứu, hoặc gọi %s.' % self.finish_name}]})
            return state
        responses = []
        for call in calls:
            if call.get('name') == self.finish_name:
                state.update(done=True, result=call.get('args') or {})
                return state
            responses.append({'functionResponse': {'name': call.get('name'), 'response': {'result': self._run_tool(call.get('name'), call.get('args'))}}})
        state['messages'].append({'role': 'user', 'parts': responses})
        return state
