"""Nhãn AI cho từng hội thoại (một dòng / hội thoại, ghi đè khi đọc lại): phân loại, kết quả, lý do mất khách, thông tin đã lấy,
thông tin từ ảnh, chấm điểm nhân viên. Đây cũng là "bộ nhớ đã đọc" để lần sau chỉ đọc hội thoại mới / có tin mới.

Các hàm `_tool_*` là tool cho Agent điều phối truy vấn khi tổng hợp."""
import json
import re

from odoo import api, fields, models

PRODUCTS = [('bang_hieu', 'Bảng hiệu'), ('hop_den', 'Hộp đèn'), ('chu_noi', 'Chữ nổi'), ('in_an', 'In ấn (tờ rơi, card, catalogue…)'),
            ('decal', 'Decal / tem nhãn'), ('posm', 'POSM / standee / kệ'), ('su_kien', 'Backdrop / sự kiện'), ('xe_day', 'Xe đẩy bán hàng'),
            ('website', 'Website / marketing'), ('thiet_ke', 'Thiết kế'), ('khac', 'Khác')]
CUSTOMER_KINDS = [('moi', 'Khách mới'), ('cu', 'Khách cũ quay lại'), ('doi_tac', 'Đối tác / nội bộ'), ('rac', 'Rác / không liên quan')]
INTENTS = [('hoi_gia', 'Hỏi giá'), ('tu_van', 'Cần tư vấn'), ('dat_hang', 'Đặt hàng'), ('theo_doi_don', 'Theo dõi đơn'),
           ('khieu_nai', 'Khiếu nại'), ('khac', 'Khác')]
OUTCOMES = [('chot_don', 'Chốt đơn'), ('cho_khach', 'Đã báo giá, chờ khách'), ('dang_trao_doi', 'Đang trao đổi'),
            ('khach_im_lang', 'Khách im lặng'), ('mat_khach', 'Mất khách'), ('khong_lien_quan', 'Không liên quan')]
LOST_REASONS = [('gia_cao', 'Giá cao'), ('tra_loi_cham', 'Trả lời chậm'), ('thieu_thong_tin', 'Thiếu thông tin / tư vấn chưa tới'),
                ('tham_khao', 'Khách chỉ tham khảo'), ('ngoai_pham_vi', 'Ngoài phạm vi'), ('khong_kip', 'Không kịp thời gian'),
                ('khong_ro', 'Không rõ'), ('none', 'Không mất khách')]
SENTIMENTS = [('tich_cuc', 'Tích cực'), ('binh_thuong', 'Bình thường'), ('kho_chiu', 'Khó chịu')]
BOT_FIT = [('mo_dau', 'Bot làm được phần mở đầu'), ('mot_phan', 'Bot làm được một phần'), ('khong', 'Cần người ngay')]
SCORES = ('greeting', 'discovery', 'consulting', 'closing', 'clarity', 'attitude')
SCORE_LABELS = {'greeting': 'Chào hỏi', 'discovery': 'Khai thác nhu cầu', 'consulting': 'Tư vấn', 'closing': 'Chốt / bước tiếp theo',
                'clarity': 'Rõ ràng, chính tả', 'attitude': 'Thái độ'}
INFO_KEYS = ('size', 'quantity', 'material', 'deadline', 'area', 'budget', 'design_file')
GROUPABLE = ('product', 'customer_kind', 'intent', 'outcome', 'lost_reason', 'sentiment', 'bot_fit', 'staff_name', 'after_hours', 'is_new')


def _main_staff(name):
    """AI đôi khi ghi nhiều người ("Tieu Bang, Dao"): lấy người đầu tiên làm nhân viên chính."""
    name = re.split(r'\s*(?:,|/|&|\+|;| và )\s*', str(name or '').strip())[0].strip()
    return name[:80] if name and name != '?' else ''


def _richness(item):
    """Số trường có nội dung — dùng để chọn bản đầy đủ hơn khi AI trả trùng một hội thoại."""
    return sum(1 for value in item.values() if value not in (None, '', [], {}, 0, False)) + len(item.get('info') or {}) \
        + len([v for v in (item.get('scores') or {}).values() if v])


def _dedupe(items):
    """AI đôi khi trả hai phần tử cho cùng một conversation_id: giữ phần tử nhiều thông tin hơn (giữ thứ tự xuất hiện đầu)."""
    best, order = {}, []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = item.get('conversation_id')
        try:
            key = int(key)
        except (TypeError, ValueError):
            continue
        if key not in best:
            order.append(key)
            best[key] = item
        elif _richness(item) > _richness(best[key]):
            best[key] = item
    return [best[key] for key in order]


def _keys(selection):
    return [key for key, _label in selection]


# Khuôn nhãn cho AI (bước đọc hội thoại)
LABEL_SCHEMA = {
    'type': 'object',
    'properties': {'items': {'type': 'array', 'description': 'Một phần tử cho MỖI hội thoại trong lô.', 'items': {
        'type': 'object',
        'properties': {
            'conversation_id': {'type': 'integer'},
            'summary': {'type': 'string', 'description': 'TỐI ĐA 15 từ: khách cần gì, kết quả.'},
            'product': {'type': 'string', 'enum': _keys(PRODUCTS)},
            'customer_kind': {'type': 'string', 'enum': _keys(CUSTOMER_KINDS)},
            'intent': {'type': 'string', 'enum': _keys(INTENTS)},
            'outcome': {'type': 'string', 'enum': _keys(OUTCOMES)},
            'lost_reason': {'type': 'string', 'enum': _keys(LOST_REASONS), 'description': 'CHỈ ghi khi outcome là mat_khach hoặc khach_im_lang.'},
            'info': {'type': 'object', 'description': 'CHỈ ghi khóa có dữ liệu, mỗi giá trị tối đa 6 từ; bỏ trống khóa chưa có.',
                     'properties': {k: {'type': 'string'} for k in INFO_KEYS}},
            'image_facts': {'type': 'string', 'description': 'TỐI ĐA 25 từ, chỉ dữ kiện phục vụ báo giá (sản phẩm, kích thước, chất liệu, nội dung, mặt bằng); trống nếu không có ảnh hoặc ảnh không có gì đáng kể. Không mô tả từng ảnh.'},
            'bot_fit': {'type': 'string', 'enum': _keys(BOT_FIT), 'description': 'Chatbot chào khách có thể lo phần nào. Bỏ qua nếu là mo_dau.'},
            'staff_name': {'type': 'string', 'description': 'MỘT tên nhân viên chính (như trong đoạn chat, "?" nếu không rõ).'},
            'scores': {'type': 'object', 'description': 'Chấm nhân viên 1–5 (0 nếu không đủ dữ kiện).',
                       'properties': {k: {'type': 'integer'} for k in SCORES}},
            'staff_improve': {'type': 'string', 'description': 'Điểm cần cải thiện, TỐI ĐA 10 từ; bỏ qua nếu không có.'},
        },
        'required': ['conversation_id', 'summary', 'product', 'outcome'],
    }}},
    'required': ['items'],
}


class ChatInsight(models.Model):
    _name = 'dac.chat.insight'
    _description = 'Nhãn AI của hội thoại'
    _order = 'read_at desc, id desc'
    _rec_name = 'customer_name'

    conversation_id = fields.Many2one('dac.pancake.conversation', string='Hội thoại', required=True, ondelete='cascade', index=True)
    customer_name = fields.Char(related='conversation_id.customer_name', string='Khách')
    external_url = fields.Char(related='conversation_id.external_url', string='Link Pancake')
    analysis_id = fields.Many2one('dac.chat.analysis', string='Đọc ở đợt', ondelete='set null', index=True)
    read_at = fields.Datetime(string='Đọc lúc', index=True)
    messages_until = fields.Datetime(string='Đã đọc tới tin lúc', help='Có tin mới sau mốc này thì hội thoại được coi là "chưa đọc".')
    model_used = fields.Char(string='Model')
    images_read = fields.Integer(string='Số ảnh AI đã xem')
    # Số liệu do code tính
    is_new = fields.Boolean(string='Khách mới (trong kỳ)')
    after_hours = fields.Boolean(string='Khách nhắn ngoài giờ')
    first_response_minutes = fields.Float(string='Trả lời đầu sau (phút)', digits=(8, 1))
    deal_detected = fields.Boolean(string='Có dấu hiệu chốt (STK/cọc)')
    # Nhãn AI
    summary = fields.Text(string='Tóm tắt')
    product = fields.Selection(PRODUCTS, string='Sản phẩm', index=True)
    customer_kind = fields.Selection(CUSTOMER_KINDS, string='Loại khách')
    intent = fields.Selection(INTENTS, string='Mục đích')
    outcome = fields.Selection(OUTCOMES, string='Kết quả', index=True)
    lost_reason = fields.Selection(LOST_REASONS, string='Lý do mất khách')
    sentiment = fields.Selection(SENTIMENTS, string='Thái độ khách')
    urgent = fields.Boolean(string='Cần gấp')
    info_json = fields.Text(string='Thông tin đã lấy (JSON)')
    info_count = fields.Integer(string='Số thông tin đã lấy')
    image_facts = fields.Text(string='Dữ kiện từ ảnh')
    bot_fit = fields.Selection(BOT_FIT, string='Phù hợp chatbot')
    staff_name = fields.Char(string='Nhân viên', index=True)
    score_greeting = fields.Integer(string='Chào hỏi')
    score_discovery = fields.Integer(string='Khai thác nhu cầu')
    score_consulting = fields.Integer(string='Tư vấn')
    score_closing = fields.Integer(string='Chốt / bước tiếp')
    score_clarity = fields.Integer(string='Rõ ràng')
    score_attitude = fields.Integer(string='Thái độ')
    score_avg = fields.Float(string='Điểm TB', digits=(4, 2), compute='_compute_score_avg', store=True, aggregator='avg')
    staff_good = fields.Text(string='Làm tốt')
    staff_improve = fields.Text(string='Cần cải thiện')
    good_phrase = fields.Char(string='Câu hay nên học')

    _sql_constraints = [('conversation_unique', 'unique(conversation_id)', 'Mỗi hội thoại chỉ có một bản nhãn.')]

    @api.depends(*['score_%s' % k for k in SCORES])
    def _compute_score_avg(self):
        for insight in self:
            values = [insight['score_%s' % k] for k in SCORES if 1 <= (insight['score_%s' % k] or 0) <= 5]
            insight.score_avg = round(sum(values) / len(values), 2) if values else 0

    def action_open_pancake(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_url', 'url': self.external_url or '#', 'target': 'new'}

    # ── Ghi nhãn từ AI ───────────────────────────────────────────────────────
    @api.model
    def _store(self, analysis, items, samples, model_label, images_by_conversation):
        """Ghi/ghi đè nhãn cho các hội thoại trong lô. samples: {id: dict số liệu code}. Trả số hội thoại đã ghi."""
        valid = {'product': _keys(PRODUCTS), 'customer_kind': _keys(CUSTOMER_KINDS), 'intent': _keys(INTENTS), 'outcome': _keys(OUTCOMES),
                 'lost_reason': _keys(LOST_REASONS), 'sentiment': _keys(SENTIMENTS), 'bot_fit': _keys(BOT_FIT)}
        existing = {i.conversation_id.id: i for i in self.search([('conversation_id', 'in', list(samples))])}
        now = fields.Datetime.now()
        creates, count = [], 0
        for item in _dedupe(items):
            if not isinstance(item, dict):
                continue
            try:
                conversation_id = int(item.get('conversation_id'))
            except (TypeError, ValueError):
                continue
            sample = samples.get(conversation_id)
            if not sample:
                continue  # AI trả id không có trong lô
            info = {k: str(v).strip()[:120] for k, v in (item.get('info') or {}).items() if k in INFO_KEYS and str(v or '').strip()}
            scores = item.get('scores') if isinstance(item.get('scores'), dict) else {}
            vals = {
                'analysis_id': analysis.id, 'read_at': now, 'messages_until': sample.get('last_at'), 'model_used': model_label,
                'images_read': images_by_conversation.get(conversation_id, 0), 'is_new': bool(sample.get('new')),
                'after_hours': bool(sample.get('after_hours')), 'first_response_minutes': sample.get('first_response') or 0,
                'deal_detected': bool(sample.get('deal')), 'summary': str(item.get('summary') or '')[:1000],
                'urgent': bool(item.get('urgent')), 'info_json': json.dumps(info, ensure_ascii=False) if info else False,
                'info_count': len(info), 'image_facts': str(item.get('image_facts') or '')[:1000] or False,
                'staff_name': _main_staff(item.get('staff_name')) or sample.get('staff') or '?',
                'staff_good': str(item.get('staff_good') or '')[:500] or False,
                'staff_improve': str(item.get('staff_improve') or '')[:500] or False,
                'good_phrase': str(item.get('good_phrase') or '')[:250] or False,
            }
            for field, keys in valid.items():
                vals[field] = item.get(field) if item.get(field) in keys else False
            # Trường AI được phép bỏ qua khi là giá trị mặc định
            if not vals['bot_fit']:
                vals['bot_fit'] = 'mo_dau'
            if not vals['lost_reason'] and vals['outcome'] not in ('mat_khach', 'khach_im_lang'):
                vals['lost_reason'] = 'none'
            for key in SCORES:
                try:
                    score = int(scores.get(key) or 0)
                except (TypeError, ValueError):
                    score = 0
                vals['score_%s' % key] = score if 1 <= score <= 5 else 0
            if conversation_id in existing:
                existing[conversation_id].write(vals)
            else:
                creates.append(dict(vals, conversation_id=conversation_id))
            count += 1
        if creates:
            self.create(creates)
        return count

    # ── Tool cho Agent điều phối ─────────────────────────────────────────────
    def _domain(self, analysis, scope, filters):
        domain = [('analysis_id', '=', analysis.id)] if scope != 'all_time' else []
        for key, value in (filters or {}).items():
            if key in GROUPABLE and value not in (None, ''):
                if key in ('after_hours', 'is_new'):
                    value = value in (True, 'true', 'True', 1, '1', 'co', 'yes')
                domain.append((key, '=', value))
        return domain

    @api.model
    def _tool_label_stats(self, analysis, group_by='outcome', filters=None, scope='this_analysis'):
        """Đếm theo một nhãn: số hội thoại, % chốt, điểm nhân viên TB, thời gian trả lời đầu TB."""
        if group_by not in GROUPABLE:
            return 'group_by phải là một trong: %s' % ', '.join(GROUPABLE)
        rows = self._read_group(self._domain(analysis, scope, filters), [group_by],
                                ['__count', 'score_avg:avg', 'first_response_minutes:avg'])
        labels = dict(self._fields[group_by].selection) if self._fields[group_by].type == 'selection' else {}
        deals = dict(self._read_group(self._domain(analysis, scope, filters) + [('outcome', '=', 'chot_don')], [group_by], ['__count']))
        total = sum(r[1] for r in rows) or 1
        out = []
        for value, count, score, response in sorted(rows, key=lambda r: -r[1]):
            out.append({'value': labels.get(value, value) if value not in (False, None) else '(trống)', 'key': value, 'conversations': count,
                        'share_pct': round(100.0 * count / total, 1), 'deal_pct': round(100.0 * deals.get(value, 0) / count, 1) if count else 0,
                        'staff_score_avg': round(score or 0, 2), 'first_response_avg_min': round(response or 0, 1)})
        return out

    @api.model
    def _tool_staff_scorecard(self, analysis, scope='this_analysis'):
        """Mỗi nhân viên: số hội thoại, điểm TB từng tiêu chí, % chốt, ví dụ điểm tốt / cần cải thiện."""
        insights = self.search(self._domain(analysis, scope, {}))
        by_staff = {}
        for insight in insights:
            by_staff.setdefault(insight.staff_name or '?', []).append(insight.id)
        out = []
        for name, ids in sorted(by_staff.items(), key=lambda kv: -len(kv[1])):
            group = self.browse(ids)
            row = {'staff': name, 'conversations': len(group),
                   'deal_pct': round(100.0 * len(group.filtered(lambda i: i.outcome == 'chot_don')) / len(group), 1),
                   'lost_pct': round(100.0 * len(group.filtered(lambda i: i.outcome == 'mat_khach')) / len(group), 1)}
            for key in SCORES:
                values = [v for v in group.mapped('score_%s' % key) if v]
                row[SCORE_LABELS[key]] = round(sum(values) / len(values), 2) if values else None
            row['làm tốt (ví dụ)'] = [t for t in group.mapped('staff_good') if t][:5]
            row['cần cải thiện (ví dụ)'] = [t for t in group.mapped('staff_improve') if t][:5]
            out.append(row)
        return out

    @api.model
    def _tool_search(self, analysis, filters=None, scope='this_analysis', limit=8):
        limit = max(1, min(int(limit or 8), 15))
        return [{'conversation_id': i.conversation_id.id, 'summary': i.summary, 'product': i.product, 'outcome': i.outcome,
                 'lost_reason': i.lost_reason, 'staff': i.staff_name, 'after_hours': i.after_hours, 'score_avg': i.score_avg,
                 'image_facts': i.image_facts or ''}
                for i in self.search(self._domain(analysis, scope, filters), limit=limit)]
