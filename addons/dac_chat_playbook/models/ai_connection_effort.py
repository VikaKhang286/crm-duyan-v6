"""Gắn mức thinking vào yêu cầu gửi tới kết nối kiểu OpenAI (9router…), và gọi dạng streaming khi cần.

Mức thinking của tác vụ đi theo context `dac_ai_effort` (đặt bởi `dac.chat.task._resolve`) nên không phải đổi chữ ký các hàm gọi AI
của `dac_ai_core` dùng chung. Chỉ kết nối kiểu OpenAI nhận `reasoning_effort`; kết nối Gemini không đi qua `_openai` nên tự bỏ qua.

Context `dac_ai_stream`: gọi `stream: true`. 9router chạy sau Cloudflare, yêu cầu không trả byte nào trong ~100 giây bị cắt (HTTP 524);
thinking tối đa + viết lại cả prompt thường lâu hơn thế. Streaming gửi dần từng phần nên kết nối không bị cắt. Kết quả được ghép lại
đúng khuôn trả lời thường của /chat/completions (choices[0].message.tool_calls, usage).
"""
import json
import time

import requests

from odoo import _, models
from odoo.exceptions import UserError

from .chat_task import EFFORTS

STREAM_DEADLINE = 600  # giây, tổng thời gian tối đa của một lượt streaming
STREAM_MAX_TOKENS = 32000  # chặn suy nghĩ vô hạn (mặc định 9router 64k: thinking tối đa đốt hết mà không trả kết quả)


def merge_stream(lines):
    """Ghép các dòng SSE `data: {...}` của /chat/completions (stream) thành một phản hồi thường."""
    content, calls, usage = [], {}, None
    for raw in lines:
        line = raw.decode('utf-8', 'replace') if isinstance(raw, bytes) else str(raw or '')
        if not line.startswith('data:'):
            continue
        data = line[5:].strip()
        if not data or data == '[DONE]':
            continue
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if not isinstance(chunk, dict):
            continue
        if chunk.get('error'):
            raise UserError(_('Nhà cung cấp AI báo lỗi giữa chừng: %s') % str(chunk['error'])[:200])
        if isinstance(chunk.get('usage'), dict):
            usage = chunk['usage']
        for choice in chunk.get('choices') or []:
            delta = (choice or {}).get('delta') or {}
            if isinstance(delta.get('content'), str):
                content.append(delta['content'])
            for call in delta.get('tool_calls') or []:
                slot = calls.setdefault(call.get('index', 0), {'id': '', 'name': '', 'arguments': []})
                slot['id'] = call.get('id') or slot['id']
                function = call.get('function') or {}
                slot['name'] = function.get('name') or slot['name']
                if isinstance(function.get('arguments'), str):
                    slot['arguments'].append(function['arguments'])
    message = {'role': 'assistant', 'content': ''.join(content)}
    if calls:
        message['tool_calls'] = [{'id': slot['id'] or 'call_%s' % index, 'type': 'function',
                                  'function': {'name': slot['name'], 'arguments': ''.join(slot['arguments'])}}
                                 for index, slot in sorted(calls.items())]
    result = {'choices': [{'message': message}]}
    if usage:
        result['usage'] = usage
    return result


class AiConnectionEffort(models.Model):
    _inherit = 'dac.ai.connection'

    def _openai(self, payload, timeout, model):
        effort = self.env.context.get('dac_ai_effort')
        if effort in EFFORTS and 'reasoning_effort' not in payload:
            payload = dict(payload, reasoning_effort=effort)
        if self.env.context.get('dac_ai_stream'):
            return self._openai_stream(payload, timeout, model)
        return super()._openai(payload, timeout, model)

    def _openai_stream(self, payload, timeout, model):
        if not self.api_key:
            raise UserError(_('Kết nối "%s" chưa có API key.') % self.name)
        body = dict(payload, model=self._model_name(model), stream=True, stream_options={'include_usage': True})
        body.setdefault('max_tokens', STREAM_MAX_TOKENS)
        started = time.monotonic()
        try:
            # timeout đọc tính giữa hai lần nhận byte; streaming nhận đều nên chỉ cần đủ cho một khoảng lặng
            with requests.post(self.base_url.rstrip('/') + '/chat/completions', json=body, stream=True, allow_redirects=False,
                               headers={'Authorization': 'Bearer ' + self.api_key, 'Accept': 'text/event-stream'},
                               timeout=(timeout[0], max(timeout[1], 120))) as response:
                if response.status_code in (400, 401, 403):
                    raise UserError(_('Nhà cung cấp AI từ chối yêu cầu (HTTP %s). Kiểm tra API key và model.') % response.status_code)
                if response.status_code == 429:
                    raise UserError(_('Nhà cung cấp AI báo quá tải (HTTP 429).'))
                if response.status_code != 200:
                    raise UserError(_('Nhà cung cấp AI chưa xử lý được yêu cầu (HTTP %s).') % response.status_code)

                def lines():
                    for line in response.iter_lines():
                        if time.monotonic() - started > STREAM_DEADLINE:
                            raise UserError(_('AI trả lời quá %s giây.') % STREAM_DEADLINE)
                        yield line

                return merge_stream(lines())
        except requests.exceptions.RequestException:
            raise UserError(_('Không kết nối được tới nhà cung cấp AI.')) from None
