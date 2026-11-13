import json
import logging
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError

from .agent_proposal import ORDER_CREATE_FIELDS, normalize_order_changes
from .agent_rule import CATEGORIES
from .gsheet_check import display, order_field_value
from .gsheet_client import GSheetError
from .gsheet_source import FIELD_SPECS, ORDER_STATES, norm_text
from .llm_client import LLMClient, LLMError, extract_json

_logger = logging.getLogger(__name__)

TURN_TIME_BUDGET = 100  # giây cho một lượt chat (Odoo limit_time_real mặc định 120s)
MAX_HISTORY_TURNS = 20
ROW_LIMIT = 200


# ----------------------------------------------------------------------
# Định nghĩa tool (JSON Schema dùng chung cho Claude và OpenAI-compatible)
# ----------------------------------------------------------------------
def _obj(props, required=()):
    return {'type': 'object', 'properties': props, 'required': list(required), 'additionalProperties': False}


TOOL_READ_SHEET = {
    'name': 'read_sheet',
    'description': 'Đọc dữ liệu từ một file Google Sheet trong ngữ cảnh. Trả về danh sách cột và các dòng '
                   '(kèm số dòng thật trên sheet). Dùng start_row/limit để phân trang.',
    'parameters': _obj({
        'source_id': {'type': 'integer', 'description': 'ID file sheet (xem phần NGỮ CẢNH).'},
        'start_row': {'type': 'integer', 'description': 'Số dòng sheet bắt đầu (mặc định: dòng đầu dữ liệu).'},
        'limit': {'type': 'integer', 'description': 'Số dòng tối đa (≤200, mặc định 50).'},
        'columns': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Chỉ lấy các cột này.'},
    }, ['source_id']),
}
TOOL_SEARCH_SHEET = {
    'name': 'search_sheet',
    'description': 'Tìm các dòng trong sheet chứa chuỗi cần tìm (không phân biệt hoa thường/dấu). '
                   'Dùng để tìm theo số đơn, tên khách, số điện thoại.',
    'parameters': _obj({
        'source_id': {'type': 'integer'},
        'query': {'type': 'string'},
        'column': {'type': 'string', 'description': 'Chỉ tìm trong cột này (tuỳ chọn).'},
        'limit': {'type': 'integer', 'description': 'Mặc định 20.'},
    }, ['source_id', 'query']),
}
TOOL_GET_ORDERS = {
    'name': 'get_orders',
    'description': 'Lấy thông tin đơn hàng xe đẩy trên Odoo: theo danh sách ID, hoặc tìm theo số đặt hàng, '
                   'tên/SĐT khách, trạng thái.',
    'parameters': _obj({
        'order_ids': {'type': 'array', 'items': {'type': 'integer'}},
        'order_number': {'type': 'string'},
        'customer': {'type': 'string', 'description': 'Tên hoặc SĐT khách.'},
        'state': {'type': 'string', 'enum': [s for s, _l in ORDER_STATES]},
        'limit': {'type': 'integer', 'description': 'Mặc định 20, tối đa 50.'},
    }),
}
TOOL_RUN_CHECK = {
    'name': 'run_check',
    'description': 'Chạy kiểm tra tự động (so khớp tất định) giữa một file sheet và toàn bộ đơn xe đẩy. '
                   'Trả về tóm tắt và danh sách chênh lệch kèm id để dùng khi đề xuất.',
    'parameters': _obj({'source_id': {'type': 'integer'}}, ['source_id']),
}
TOOL_PROPOSE_ORDER_UPDATE = {
    'name': 'propose_order_update',
    'description': 'Đề xuất sửa một đơn xe đẩy trên Odoo. Người dùng phải bấm Duyệt mới được ghi. '
                   'Trường được sửa: order_number, date (YYYY-MM-DD), production_deadline (YYYY-MM-DD), '
                   'order_state_custom (quotation|deposit|production|installation|delivery|payment|completed|cancel), '
                   'cart_design_link.',
    'parameters': _obj({
        'order_id': {'type': 'integer'},
        'changes': {'type': 'object', 'description': '{tên_trường: giá_trị_mới}'},
        'reason': {'type': 'string', 'description': 'Lý do ngắn gọn, nêu bằng chứng (dòng sheet nào).'},
        'source_id': {'type': 'integer'},
        'sheet_row': {'type': 'integer'},
        'discrepancy_ids': {'type': 'array', 'items': {'type': 'integer'}},
    }, ['order_id', 'changes', 'reason']),
}
TOOL_PROPOSE_ORDER_CREATE = {
    'name': 'propose_order_create',
    'description': 'Đề xuất tạo đơn xe đẩy mới trên Odoo từ một dòng sheet (khách được tìm theo SĐT, '
                   'chưa có thì tạo mới). Người dùng phải duyệt.',
    'parameters': _obj({
        'partner_name': {'type': 'string'},
        'partner_phone': {'type': 'string'},
        'order_number': {'type': 'string'},
        'date': {'type': 'string', 'description': 'YYYY-MM-DD'},
        'production_deadline': {'type': 'string', 'description': 'YYYY-MM-DD'},
        'note': {'type': 'string', 'description': 'Nội dung đơn / sản phẩm / ghi chú từ sheet.'},
        'reason': {'type': 'string'},
        'source_id': {'type': 'integer'},
        'sheet_row': {'type': 'integer'},
        'discrepancy_ids': {'type': 'array', 'items': {'type': 'integer'}},
    }, ['reason']),
}
TOOL_PROPOSE_SHEET_UPDATE = {
    'name': 'propose_sheet_update',
    'description': 'Đề xuất sửa các ô của MỘT dòng trên Google Sheet (chỉ file có quyền Chỉnh sửa). '
                   'values dùng đúng tên cột trên sheet.',
    'parameters': _obj({
        'source_id': {'type': 'integer'},
        'sheet_row': {'type': 'integer'},
        'values': {'type': 'object', 'description': '{tên_cột: giá_trị_mới}'},
        'reason': {'type': 'string'},
        'discrepancy_ids': {'type': 'array', 'items': {'type': 'integer'}},
    }, ['source_id', 'sheet_row', 'values', 'reason']),
}
TOOL_PROPOSE_SHEET_APPEND = {
    'name': 'propose_sheet_append',
    'description': 'Đề xuất thêm một dòng mới vào cuối sheet (chỉ file có quyền Chỉnh sửa), ví dụ đơn có '
                   'trên Odoo nhưng thiếu trên sheet.',
    'parameters': _obj({
        'source_id': {'type': 'integer'},
        'values': {'type': 'object', 'description': '{tên_cột: giá_trị}'},
        'reason': {'type': 'string'},
        'discrepancy_ids': {'type': 'array', 'items': {'type': 'integer'}},
    }, ['source_id', 'values', 'reason']),
}
TOOL_REMEMBER_RULE = {
    'name': 'remember_rule',
    'description': 'Ghi nhớ một quy tắc/cách làm để lần sau làm tốt hơn: khi người dùng dạy, sửa lỗi của bạn, '
                   'hoặc bạn phát hiện quy ước của file. Viết ngắn, cụ thể, có thể hành động được. '
                   'Không ghi dữ liệu nhất thời của một đơn.',
    'parameters': _obj({
        'content': {'type': 'string'},
        'category': {'type': 'string', 'enum': [c for c, _l in CATEGORIES]},
        'source_id': {'type': 'integer', 'description': 'Chỉ áp dụng cho file này (bỏ trống = mọi file).'},
        'taught_by_user': {'type': 'boolean', 'description': 'true nếu người dùng trực tiếp yêu cầu ghi nhớ.'},
    }, ['content', 'category']),
}
TOOL_RATE_RULE = {
    'name': 'rate_rule',
    'description': 'Đánh giá một quy tắc trong playbook sau khi dùng: helpful=true nếu giúp làm đúng, '
                   'false nếu sai/gây nhầm.',
    'parameters': _obj({
        'rule_id': {'type': 'integer'},
        'helpful': {'type': 'boolean'},
    }, ['rule_id', 'helpful']),
}

SYSTEM_PROMPT = """Bạn là trợ lý đồng bộ đơn hàng XE ĐẨY của công ty DAC (in ấn / thiết kế), làm việc trong Odoo.
Nhân sự đang theo dõi đơn trên Google Sheet song song với Odoo. Nhiệm vụ của bạn:
1. So khớp đơn hàng xe đẩy trên Odoo với dữ liệu trong các file Google Sheet ở phần NGỮ CẢNH.
2. Phát hiện chênh lệch (thiếu đơn, lệch trạng thái/tiến độ, lệch ngày, lệch khách) và đề xuất cách đồng bộ.
3. Trả lời câu hỏi về tiến độ đơn.

Cách làm việc:
- Luôn dùng tool để đọc dữ liệu thật; không bịa số đơn, dòng sheet hay giá trị.
- Mọi thay đổi đều qua tool propose_* — người dùng sẽ bấm Duyệt. Không nói "đã cập nhật" khi mới chỉ đề xuất.
- File "Chỉ xem" thì không đề xuất ghi sheet. Khi không chắc dữ liệu bên nào đúng, hỏi lại thay vì đoán.
- Mỗi đề xuất nêu bằng chứng: dòng sheet số mấy, giá trị cũ → mới. Gộp nhiều trường của cùng một đơn vào một đề xuất.
- Trạng thái Odoo: quotation=Báo giá, deposit=Đặt cọc, production=Sản xuất, installation=Thi công, delivery=Giao hàng,
  payment=Thu tiền, completed=Hoàn thành, cancel=Hủy. Đơn xe đẩy thường không qua Thi công.
- Đổi trạng thái trên Odoo có thể bị chặn bởi nghiệp vụ (ví dụ chưa có hoá đơn cọc) — nếu đề xuất bị lỗi, giải thích cho người dùng.

Tự học:
- PLAYBOOK bên dưới là các quy tắc đã rút ra từ những lần làm việc trước. Áp dụng chúng và trích dẫn [R<id>] khi dùng.
- Khi người dùng dạy cách làm, sửa sai cho bạn, hoặc bạn nhận ra quy ước của file (ví dụ cách ghi trạng thái, màu,
  định dạng số đơn), gọi remember_rule ngay. Khi một quy tắc giúp đúng/sai, gọi rate_rule.

Trả lời bằng tiếng Việt, ngắn gọn, dùng gạch đầu dòng khi liệt kê."""


class DacGsheetAgentSession(models.Model):
    _name = 'dac.gsheet.agent.session'
    _description = 'Phiên chat AI Agent Google Sheet'
    _order = 'last_activity_at desc, id desc'

    name = fields.Char(string='Tiêu đề', default=lambda self: _('Phiên mới'))
    user_id = fields.Many2one('res.users', string='Người dùng', required=True,
                              default=lambda self: self.env.user, index=True)
    order_ids = fields.Many2many('sale.order', string='Đơn hàng xe đẩy (ngữ cảnh)')
    source_ids = fields.Many2many('dac.gsheet.source', string='File Google Sheet (ngữ cảnh)')
    message_ids = fields.One2many('dac.gsheet.agent.message', 'session_id', string='Tin nhắn')
    proposal_ids = fields.One2many('dac.gsheet.agent.proposal', 'session_id', string='Đề xuất')
    rule_ids = fields.One2many('dac.gsheet.agent.rule', 'session_id', string='Quy tắc học được')
    is_auto = fields.Boolean(string='Phiên tự động')
    last_activity_at = fields.Datetime(default=fields.Datetime.now, index=True)
    reflected_at = fields.Datetime(string='Rút kinh nghiệm lúc')
    reflection_log = fields.Text(string='Kết quả rút kinh nghiệm', readonly=True)
    pending_count = fields.Integer(compute='_compute_pending_count', string='Đề xuất chờ duyệt')

    def _compute_pending_count(self):
        for rec in self:
            rec.pending_count = len(rec.proposal_ids.filtered(lambda p: p.state == 'pending'))

    # ------------------------------------------------------------------
    # API cho giao diện chat (OWL)
    # ------------------------------------------------------------------
    @api.model
    def ui_bootstrap(self, order_ids=None, source_ids=None, session_id=None):
        """Mở giao diện: trả về danh sách phiên + phiên đang mở."""
        session = self.browse(session_id).exists() if session_id else self.browse()
        if not session and (order_ids or source_ids):
            session = self.create({
                'order_ids': [(6, 0, order_ids or [])],
                'source_ids': [(6, 0, source_ids or [])],
            })
        if not session:
            session = self.search([('user_id', '=', self.env.uid), ('is_auto', '=', False)], limit=1)
        if not session:
            session = self.create({'source_ids': [(6, 0, self.env['dac.gsheet.source'].search([], limit=1).ids)]})
        return {
            'sessions': self.ui_list_sessions(),
            'available_sources': [
                {'id': s.id, 'name': s.name, 'access_mode': s.access_mode}
                for s in self.env['dac.gsheet.source'].search([])
            ],
            'current': session.ui_payload(),
        }

    @api.model
    def ui_list_sessions(self):
        sessions = self.search([], limit=40)
        return [{
            'id': s.id, 'name': s.name, 'is_auto': s.is_auto, 'pending': s.pending_count,
            'last': s.last_activity_at and fields.Datetime.context_timestamp(s, s.last_activity_at).strftime('%d/%m %H:%M'),
        } for s in sessions]

    @api.model
    def ui_new_session(self, order_ids=None, source_ids=None):
        session = self.create({
            'order_ids': [(6, 0, order_ids or [])],
            'source_ids': [(6, 0, source_ids or [])],
        })
        return {'sessions': self.ui_list_sessions(), 'current': session.ui_payload()}

    def ui_payload(self):
        self.ensure_one()
        visible = self.message_ids.filtered(lambda m: m.role in ('user', 'assistant', 'note'))
        return {
            'id': self.id,
            'name': self.name,
            'is_auto': self.is_auto,
            'orders': [{'id': o.id, 'name': o.display_name,
                        'number': o.order_number or '', 'partner': o.partner_id.name or '',
                        'state': display('order_state_custom', o.order_state_custom)} for o in self.order_ids],
            'sources': [{'id': s.id, 'name': s.name, 'access_mode': s.access_mode} for s in self.source_ids],
            'messages': [m.as_ui_dict() for m in visible],
            'proposals': [p.as_ui_dict() for p in self.proposal_ids.sorted('id')],
            'rules_learned': [{'id': r.id, 'name': r.name, 'state': r.state} for r in self.rule_ids],
            'reflected_at': bool(self.reflected_at),
        }

    def ui_set_context(self, order_ids, source_ids):
        self.ensure_one()
        self.write({'order_ids': [(6, 0, order_ids or [])], 'source_ids': [(6, 0, source_ids or [])]})
        return self.ui_payload()

    @api.model
    def ui_search_orders(self, query='', limit=12):
        domain = [('order_type', '=', 'cart')]
        if query:
            domain += ['|', '|', '|', ('name', 'ilike', query), ('order_number', 'ilike', query),
                       ('partner_id.name', 'ilike', query), ('partner_id.phone', 'ilike', query)]
        orders = self.env['sale.order'].search(domain, limit=limit, order='date desc, id desc')
        return [{'id': o.id, 'name': o.display_name, 'number': o.order_number or '',
                 'partner': o.partner_id.name or '',
                 'state': display('order_state_custom', o.order_state_custom)} for o in orders]

    def ui_send(self, text):
        self.ensure_one()
        text = (text or '').strip()
        if not text:
            return self.ui_payload()
        self._chat(text)
        return self.ui_payload()

    def ui_proposal_action(self, proposal_id, action, reason=None):
        self.ensure_one()
        proposal = self.proposal_ids.filtered(lambda p: p.id == proposal_id)
        if not proposal:
            raise UserError(_('Không tìm thấy đề xuất.'))
        if action == 'apply':
            proposal.action_apply()
        elif action == 'reject':
            proposal.action_reject(reason)
        elif action == 'retry':
            proposal.action_retry()
        self.last_activity_at = fields.Datetime.now()
        return self.ui_payload()

    def ui_apply_all(self):
        self.ensure_one()
        self.proposal_ids.filtered(lambda p: p.state == 'pending').action_apply()
        return self.ui_payload()

    def ui_reflect(self):
        self.ensure_one()
        self.action_reflect()
        return self.ui_payload()

    def ui_rename(self, name):
        self.ensure_one()
        self.name = (name or '').strip() or self.name
        return self.ui_list_sessions()

    def ui_delete(self):
        self.unlink()
        return self.env['dac.gsheet.agent.session'].ui_list_sessions()

    # ------------------------------------------------------------------
    # Vòng chat
    # ------------------------------------------------------------------
    def _llm(self):
        try:
            return LLMClient(self.env['dac.gsheet.source'].get_llm_config())
        except LLMError as exc:
            raise UserError(str(exc)) from exc

    def _tools(self):
        self.ensure_one()
        tools = [TOOL_READ_SHEET, TOOL_SEARCH_SHEET, TOOL_GET_ORDERS, TOOL_RUN_CHECK,
                 TOOL_PROPOSE_ORDER_UPDATE, TOOL_PROPOSE_ORDER_CREATE]
        if any(s.access_mode == 'write' for s in self.source_ids):
            tools += [TOOL_PROPOSE_SHEET_UPDATE, TOOL_PROPOSE_SHEET_APPEND]
        tools += [TOOL_REMEMBER_RULE, TOOL_RATE_RULE]
        return tools

    def _build_system(self):
        self.ensure_one()
        Rule = self.env['dac.gsheet.agent.rule']
        rules = Rule.get_playbook(self.source_ids)
        if rules:
            rules.write({'last_used_at': fields.Datetime.now()})
        ctx = ['# NGỮ CẢNH', '## 1. Đơn hàng xe đẩy đang chọn']
        if self.order_ids:
            for o in self.order_ids[:30]:
                ctx.append('- order_id=%s | %s | số ĐH: %s | khách: %s | SĐT: %s | trạng thái: %s | deadline SX: %s' % (
                    o.id, o.name, o.order_number or '-', o.partner_id.name or '-',
                    order_field_value(o, 'partner_phone') or '-',
                    display('order_state_custom', o.order_state_custom),
                    display('production_deadline', o.production_deadline) or '-'))
        else:
            ctx.append('(Chưa chọn đơn cụ thể — làm việc với toàn bộ đơn xe đẩy, dùng get_orders để tra cứu.)')
        ctx.append('## 2. File Google Sheet')
        if self.source_ids:
            for s in self.source_ids:
                columns = ', '.join('"%s"→%s' % (c.header, c.odoo_field) for c in s.column_ids) or '(chưa ánh xạ)'
                ctx.append('- source_id=%s | "%s" | quyền: %s | tab: %s | dòng tiêu đề: %s | cột khoá: %s' % (
                    s.id, s.name, 'CHỈNH SỬA' if s.access_mode == 'write' else 'CHỈ XEM',
                    s.worksheet_name or '(tab đầu)', s.header_row, s.key_header or '(chưa chọn)'))
                ctx.append('  Các cột: %s' % (s.header_preview or '(chưa đọc — gọi read_sheet)'))
                ctx.append('  Ánh xạ: %s' % columns)
                if s.state_map_ids:
                    ctx.append('  Ánh xạ trạng thái: %s' % ', '.join(
                        '"%s"→%s' % (m.sheet_value, m.state) for m in s.state_map_ids))
                if s.ai_notes:
                    ctx.append('  Mô tả: %s' % s.ai_notes.strip())
        else:
            ctx.append('(Chưa chọn file — nhắc người dùng chọn file sheet ở thanh ngữ cảnh.)')
        today = fields.Date.context_today(self)
        return '%s\n\n# PLAYBOOK (quy tắc đã học)\n%s\n\n%s\n\nHôm nay: %s. Người dùng: %s.' % (
            SYSTEM_PROMPT, Rule.render_playbook(rules), '\n'.join(ctx), today.strftime('%d/%m/%Y'), self.user_id.name)

    def _native_history(self, client):
        """Dựng lại lịch sử native từ DB (các lượt gần nhất)."""
        self.ensure_one()
        msgs = self.message_ids.filtered(lambda m: m.role in ('user', 'assistant')).sorted('id')
        user_idx = [i for i, m in enumerate(msgs) if m.role == 'user']
        if len(user_idx) > MAX_HISTORY_TURNS:
            msgs = msgs[user_idx[-MAX_HISTORY_TURNS]:]
        history = []
        for m in msgs:
            if m.role == 'user':
                history.append(client.user_message(m.content))
                continue
            raw = m.raw_json and m.provider == client.provider and json.loads(m.raw_json)
            if raw:
                history.extend(raw)
            elif m.content:
                history.append(client.assistant_text_message(m.content))
        return history

    def _chat(self, text):
        self.ensure_one()
        if self.user_id != self.env.user and not self.env.user.has_group('dac_erp.group_dac_erp_manager'):
            raise AccessError(_('Đây không phải phiên của bạn.'))
        client = self._llm()
        Message = self.env['dac.gsheet.agent.message']
        history = self._native_history(client)
        user_msg = Message.create({'session_id': self.id, 'role': 'user', 'content': text})
        if self.name in (_('Phiên mới'), 'Phiên mới'):
            self.name = text[:60]
        history.append(client.user_message(text))

        try:
            result = client.run(self._build_system(), history, self._tools(), self._execute_tool,
                                max_rounds=10, deadline=time.time() + TURN_TIME_BUDGET)
        except LLMError as exc:
            Message.create({'session_id': self.id, 'role': 'note', 'content': '⚠️ %s' % exc})
            self.last_activity_at = fields.Datetime.now()
            return user_msg

        notes = ['🔧 %s%s' % (c['name'], ' (lỗi)' if c['is_error'] else '') for c in result['tool_calls']]
        assistant = Message.create({
            'session_id': self.id, 'role': 'assistant',
            'content': result['text'] or _('(Đã xử lý xong.)'),
            'raw_json': json.dumps(result['new_messages'], ensure_ascii=False),
            'provider': client.provider,
            'tool_summary': ', '.join(notes),
        })
        turn_proposals = self.proposal_ids.filtered(lambda p: not p.message_id)
        if turn_proposals:
            turn_proposals.write({'message_id': assistant.id})
            # Nguồn cho phép ghi thẳng (không cần duyệt) → áp dụng luôn.
            auto = turn_proposals.filtered(
                lambda p: p.kind in ('sheet_update', 'sheet_append') and not p.source_id.sheet_write_needs_approval)
            auto.action_apply()
        self.last_activity_at = fields.Datetime.now()
        return assistant

    # ------------------------------------------------------------------
    # Thực thi tool
    # ------------------------------------------------------------------
    def _source(self, source_id):
        source = self.source_ids.filtered(lambda s: s.id == int(source_id or 0))
        if not source:
            raise UserError('source_id=%s không nằm trong ngữ cảnh phiên. Các file hợp lệ: %s' % (
                source_id, ', '.join('%s (%s)' % (s.id, s.name) for s in self.source_ids) or 'không có'))
        return source

    def _execute_tool(self, name, args):
        handler = getattr(self, '_tool_%s' % name, None)
        if not handler:
            raise UserError('Tool không tồn tại: %s' % name)
        # savepoint: lỗi SQL trong một tool không làm hỏng transaction của cả lượt chat
        with self.env.cr.savepoint():
            try:
                return handler(**(args or {}))
            except TypeError as exc:
                raise UserError('Tham số không hợp lệ cho %s: %s' % (name, exc)) from exc
            except GSheetError as exc:
                raise UserError(str(exc)) from exc

    def _tool_read_sheet(self, source_id, start_row=None, limit=50, columns=None):
        source = self._source(source_id)
        table = source.fetch_table()
        source.headers_json = json.dumps(table['headers'], ensure_ascii=False)
        rows = table['rows']
        if start_row:
            rows = [r for r in rows if r['row'] >= int(start_row)]
        limit = max(1, min(int(limit or 50), ROW_LIMIT))
        picked = rows[:limit]
        if columns:
            picked = [{'row': r['row'], 'values': {c: r['values'].get(c, '') for c in columns}} for r in picked]
        return {
            'worksheet': table['worksheet'], 'headers': table['headers'],
            'total_rows': len(table['rows']), 'returned': len(picked),
            'next_start_row': rows[limit]['row'] if len(rows) > limit else None,
            'rows': picked,
        }

    def _tool_search_sheet(self, source_id, query, column=None, limit=20):
        source = self._source(source_id)
        table = source.fetch_table()
        needle = norm_text(query)
        digits = ''.join(ch for ch in str(query) if ch.isdigit())
        found = []
        for row in table['rows']:
            cells = [row['values'].get(column, '')] if column else row['values'].values()
            for cell in cells:
                cell_norm = norm_text(cell)
                cell_digits = ''.join(ch for ch in str(cell) if ch.isdigit())
                if needle and needle in cell_norm or (len(digits) >= 6 and digits[-9:] in cell_digits):
                    found.append(row)
                    break
            if len(found) >= min(int(limit or 20), ROW_LIMIT):
                break
        return {'headers': table['headers'], 'matches': found, 'count': len(found)}

    def _tool_get_orders(self, order_ids=None, order_number=None, customer=None, state=None, limit=20):
        domain = [('order_type', '=', 'cart')]
        if order_ids:
            domain.append(('id', 'in', [int(i) for i in order_ids]))
        if order_number:
            domain.append(('order_number', 'ilike', order_number))
        if customer:
            domain += ['|', '|', ('partner_id.name', 'ilike', customer),
                       ('partner_id.phone', 'ilike', customer[-9:]), ('partner_id.mobile', 'ilike', customer[-9:])]
        if state:
            domain.append(('order_state_custom', '=', state))
        orders = self.env['sale.order'].search(domain, limit=min(int(limit or 20), 50), order='date desc, id desc')
        return {'count': len(orders), 'orders': [{
            'order_id': o.id, 'name': o.name, 'order_number': o.order_number or None,
            'customer': o.partner_id.name, 'phone': order_field_value(o, 'partner_phone') or None,
            'date': display('date', order_field_value(o, 'date')),
            'state': o.order_state_custom, 'state_label': display('order_state_custom', o.order_state_custom),
            'production_deadline': display('production_deadline', o.production_deadline) or None,
            'amount_total': o.amount_total, 'sale': o.user_id.name or None,
            'designer': o.user_id_design.name or None,
            'cart_design_link': o.cart_design_link or None,
            'lines': [{'product': l.name, 'qty': l.product_uom_qty} for l in o.order_line[:10]],
        } for o in orders]}

    def _tool_run_check(self, source_id):
        source = self._source(source_id)
        run = self.env['dac.gsheet.check.run'].run_for_source(source, trigger='agent')
        return run.as_agent_payload()

    def _new_proposal(self, vals):
        return self.env['dac.gsheet.agent.proposal'].create(dict(vals, session_id=self.id))

    def _tool_propose_order_update(self, order_id, changes, reason, source_id=None, sheet_row=None, discrepancy_ids=None):
        order = self.env['sale.order'].browse(int(order_id)).exists()
        if not order or order.order_type != 'cart':
            raise UserError('order_id=%s không phải đơn xe đẩy hợp lệ.' % order_id)
        try:
            normalize_order_changes(changes)
        except ValueError as exc:
            raise UserError(str(exc)) from exc
        source = self._source(source_id) if source_id else self.env['dac.gsheet.source']
        proposal = self._new_proposal({
            'kind': 'order_update', 'order_id': order.id, 'source_id': source.id or False,
            'sheet_row': sheet_row or 0, 'reason': reason,
            'payload_json': json.dumps({'changes': changes, 'discrepancy_ids': discrepancy_ids or []}, ensure_ascii=False),
        })
        return 'Đã tạo đề xuất #%s (chờ người dùng duyệt).' % proposal.id

    def _tool_propose_order_create(self, reason, source_id=None, sheet_row=None, discrepancy_ids=None, **values):
        payload = {k: values.get(k) for k in ORDER_CREATE_FIELDS if values.get(k)}
        if not (payload.get('partner_name') or payload.get('partner_phone')):
            raise UserError('Cần partner_name hoặc partner_phone.')
        if payload.get('order_number') and self.env['sale.order'].search_count(
                [('order_number', '=', payload['order_number'])]):
            raise UserError('Số đặt hàng %s đã tồn tại trên Odoo — dùng propose_order_update.' % payload['order_number'])
        source = self._source(source_id) if source_id else self.env['dac.gsheet.source']
        payload['discrepancy_ids'] = discrepancy_ids or []
        proposal = self._new_proposal({
            'kind': 'order_create', 'source_id': source.id or False, 'sheet_row': sheet_row or 0,
            'reason': reason, 'payload_json': json.dumps(payload, ensure_ascii=False),
        })
        return 'Đã tạo đề xuất #%s tạo đơn mới (chờ duyệt).' % proposal.id

    def _check_sheet_values(self, source, values):
        if source.access_mode != 'write':
            raise UserError('File "%s" chỉ có quyền XEM — không thể đề xuất ghi.' % source.name)
        headers = json.loads(source.headers_json or '[]') or source.fetch_table()['headers']
        unknown = [h for h in (values or {}) if h not in headers]
        if unknown:
            raise UserError('Không có cột: %s. Các cột: %s' % (', '.join(unknown), ', '.join(headers)))
        if not values:
            raise UserError('values trống.')

    def _tool_propose_sheet_update(self, source_id, sheet_row, values, reason, discrepancy_ids=None):
        source = self._source(source_id)
        self._check_sheet_values(source, values)
        if int(sheet_row) <= source.header_row:
            raise UserError('Không được sửa dòng tiêu đề.')
        current = next((r['values'] for r in source.fetch_table()['rows'] if r['row'] == int(sheet_row)), {})
        proposal = self._new_proposal({
            'kind': 'sheet_update', 'source_id': source.id, 'sheet_row': int(sheet_row), 'reason': reason,
            'payload_json': json.dumps({
                'values': values, 'current': {h: current.get(h, '') for h in values},
                'discrepancy_ids': discrepancy_ids or []}, ensure_ascii=False),
        })
        tail = '' if source.sheet_write_needs_approval else ' File cho phép ghi thẳng — sẽ được ghi ngay cuối lượt.'
        return 'Đã tạo đề xuất #%s sửa dòng %s.%s' % (proposal.id, sheet_row, tail)

    def _tool_propose_sheet_append(self, source_id, values, reason, discrepancy_ids=None):
        source = self._source(source_id)
        self._check_sheet_values(source, values)
        proposal = self._new_proposal({
            'kind': 'sheet_append', 'source_id': source.id, 'reason': reason,
            'payload_json': json.dumps({'values': values, 'discrepancy_ids': discrepancy_ids or []}, ensure_ascii=False),
        })
        return 'Đã tạo đề xuất #%s thêm dòng mới.' % proposal.id

    def _tool_remember_rule(self, content, category, source_id=None, taught_by_user=False):
        source = self._source(source_id) if source_id else self.env['dac.gsheet.source']
        _rule, msg = self.env['dac.gsheet.agent.rule'].add_rule(
            content, category, source_id=source.id or False,
            origin='user' if taught_by_user else 'agent', session=self)
        return msg

    def _tool_rate_rule(self, rule_id, helpful):
        rule = self.env['dac.gsheet.agent.rule'].browse(int(rule_id)).exists()
        if not rule:
            raise UserError('Không có quy tắc R%s.' % rule_id)
        rule.mark(bool(helpful))
        return 'Đã ghi nhận.'

    # ------------------------------------------------------------------
    # Reflector: rút kinh nghiệm sau phiên
    # ------------------------------------------------------------------
    def _transcript(self, limit_chars=24000):
        self.ensure_one()
        parts = []
        for m in self.message_ids.sorted('id'):
            if m.role == 'user':
                parts.append('NGƯỜI DÙNG: %s' % m.content)
            elif m.role == 'assistant':
                tools = (' [tool: %s]' % m.tool_summary) if m.tool_summary else ''
                parts.append('AGENT%s: %s' % (tools, m.content))
        for p in self.proposal_ids:
            outcome = {'applied': 'ĐƯỢC DUYỆT', 'rejected': 'BỊ TỪ CHỐI', 'failed': 'LỖI KHI ÁP DỤNG',
                       'pending': 'chưa xử lý'}[p.state]
            extra = p.reject_reason or p.error_message or ''
            parts.append('ĐỀ XUẤT #%s (%s): %s — lý do AI: %s — kết quả: %s %s' % (
                p.id, p.kind, p.summary, p.reason or '', outcome, extra))
        text = '\n'.join(parts)
        return text[-limit_chars:]

    REFLECT_PROMPT = """Bạn là bộ phận REFLECTOR + CURATOR của một AI agent đồng bộ đơn xe đẩy giữa Odoo và Google Sheet.
Đọc nhật ký phiên làm việc và PLAYBOOK hiện tại. Rút ra bài học có thể tái sử dụng để lần sau agent làm đúng
và nhanh hơn: quy ước của file sheet, cách ánh xạ trạng thái, cách khớp khách/đơn, lỗi đã mắc, ý muốn của người dùng.
Tín hiệu mạnh nhất: đề xuất BỊ TỪ CHỐI (kèm lý do) hoặc LỖI → quy tắc cần tránh; người dùng sửa agent → quy tắc mới.

Chỉ trả về JSON đúng dạng:
{"insights": ["..."], "operations": [
  {"op": "ADD", "category": "matching|status_mapping|sheet_structure|data_format|workflow|pitfall|preference",
   "scope": "global|source", "content": "quy tắc ngắn gọn, cụ thể, hành động được"},
  {"op": "HELPFUL", "rule_id": 12}, {"op": "HARMFUL", "rule_id": 7},
  {"op": "UPDATE", "rule_id": 5, "content": "nội dung đã chỉnh"}
]}
Quy định: tối đa 6 operations; không ADD điều đã có trong playbook (dùng HELPFUL); không ghi dữ liệu nhất thời của
một đơn cụ thể; nếu không có gì đáng học trả {"insights": [], "operations": []}."""

    def action_reflect(self):
        for session in self:
            if not session.message_ids.filtered(lambda m: m.role == 'user'):
                continue
            client = session._llm()
            Rule = self.env['dac.gsheet.agent.rule']
            playbook = Rule.render_playbook(Rule.get_playbook(session.source_ids))
            prompt = '# PLAYBOOK HIỆN TẠI\n%s\n\n# NHẬT KÝ PHIÊN\n%s' % (playbook, session._transcript())
            try:
                answer = client.complete(self.REFLECT_PROMPT, prompt)
            except LLMError as exc:
                session.reflection_log = 'Lỗi: %s' % exc
                continue
            data = extract_json(answer) or {}
            log = Rule.apply_operations(
                data.get('operations'), session=session,
                default_source_id=session.source_ids[:1].id if len(session.source_ids) == 1 else False)
            insights = data.get('insights') or []
            session.write({
                'reflected_at': fields.Datetime.now(),
                'reflection_log': '\n'.join(['• %s' % i for i in insights] + log) or 'Không có bài học mới.',
            })
            self.env['dac.gsheet.agent.message'].create({
                'session_id': session.id, 'role': 'note',
                'content': '🧠 Rút kinh nghiệm: %s' % ('; '.join(log) if log else 'không có quy tắc mới'),
            })
        return True

    @api.model
    def _cron_reflect_sessions(self):
        """Cron hằng đêm: rút kinh nghiệm các phiên có hoạt động mới. KHÔNG raise."""
        idle_before = fields.Datetime.now() - timedelta(minutes=30)
        sessions = self.search([('last_activity_at', '<', idle_before)])
        sessions = sessions.filtered(lambda s: not s.reflected_at or s.reflected_at < s.last_activity_at)
        for session in sessions[:30]:
            try:
                with self.env.cr.savepoint():
                    session.with_user(session.user_id).action_reflect()
            except Exception as exc:  # cron không được raise
                _logger.exception('Reflect session %s failed', session.id)
                session.reflection_log = 'Lỗi: %s' % exc
            self.env.cr.commit()

    # ------------------------------------------------------------------
    # Phân tích tự động sau khi cron kiểm tra
    # ------------------------------------------------------------------
    @api.model
    def start_auto_review(self, run):
        source = run.source_id
        user = source.responsible_user_id or self.env.ref('base.user_admin')
        session = self.with_user(user).create({
            'name': _('Tự động: %s') % run.name,
            'user_id': user.id,
            'source_ids': [(6, 0, source.ids)],
            'is_auto': True,
        })
        prompt = _('Lần kiểm tra tự động #%(run)s vừa phát hiện %(new)s chênh lệch mới với file "%(src)s". '
                   'Gọi run_check không cần thiết — dữ liệu ở đây:\n%(data)s\n\n'
                   'Hãy phân tích, áp dụng playbook, và tạo đề xuất đồng bộ cho những chênh lệch chắc chắn. '
                   'Liệt kê những trường hợp cần người kiểm tra.') % {
            'run': run.id, 'new': run.new_discrepancy_count, 'src': source.name,
            'data': json.dumps(run.as_agent_payload(limit=80), ensure_ascii=False)}
        session._chat(prompt)
        return session


class DacGsheetAgentMessage(models.Model):
    _name = 'dac.gsheet.agent.message'
    _description = 'Tin nhắn AI Agent'
    _order = 'id'

    session_id = fields.Many2one('dac.gsheet.agent.session', required=True, ondelete='cascade', index=True)
    role = fields.Selection([
        ('user', 'Người dùng'), ('assistant', 'AI'), ('note', 'Hệ thống'),
    ], required=True)
    content = fields.Text()
    raw_json = fields.Text(help='Lịch sử native của provider (gồm tool calls) để gửi lại nguyên vẹn.')
    provider = fields.Char()
    tool_summary = fields.Char()

    def as_ui_dict(self):
        self.ensure_one()
        return {
            'id': self.id, 'role': self.role, 'content': self.content or '',
            'tools': self.tool_summary or '',
            'time': fields.Datetime.context_timestamp(self, self.create_date).strftime('%H:%M') if self.create_date else '',
        }
