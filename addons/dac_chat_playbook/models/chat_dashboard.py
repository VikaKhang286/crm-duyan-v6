"""Dữ liệu cho trang Tổng quan "Trung tâm Chatbot AI" (client action dac_chat_dashboard): một lần gọi trả đủ số liệu."""
import json

from odoo import api, fields, models

from .chat_insight import SCORE_LABELS, SCORES
from .chat_prompt import MODES
from .chat_rule import CATEGORIES

RUNNING = ('queued', 'ai')


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0


class ChatAnalysisDashboard(models.Model):
    _inherit = 'dac.chat.analysis'

    @api.model
    def dashboard_data(self):
        Analysis = self.env['dac.chat.analysis']
        Rule = self.env['dac.chat.rule']
        Prompt = self.env['dac.chat.prompt']
        config = self.env['dac.chat.config']._get()
        latest = Analysis.search([('state', '=', 'done')], order='finished_at desc, id desc', limit=1)
        latest_ai = Analysis.search([('state', '=', 'done'), ('ai_reduced', '=', True)], order='finished_at desc, id desc', limit=1)
        running = Analysis.search([('state', 'in', RUNNING)], order='id desc')

        rule_counts = dict(Rule._read_group([], ['state'], ['__count']))
        pending = Rule.search([('state', '=', 'proposed')], order='priority_order, category_order, id', limit=5)
        categories = dict(CATEGORIES)
        prompts = []
        for mode, label in MODES:
            active = Prompt.search([('state', '=', 'active'), ('mode', '=', mode)], limit=1)
            draft = Prompt.search([('state', '=', 'draft'), ('mode', '=', mode)], order='id desc', limit=1)
            prompts.append({
                'mode': mode, 'label': label,
                'active': {'id': active.id, 'name': active.name, 'model': active.bot_model_id.display_name or 'Thứ tự chung',
                           'activated_at': self._fmt_dt(active.activated_at), 'by': active.activated_by_id.name or ''} if active else False,
                'draft': {'id': draft.id, 'name': draft.name} if draft else False,
            })

        ai_ready = bool(config.ai_model_id) or self.env['dac.ai.connection']._ready() or self.env['dac.chat.task']._any_model()
        active_count = sum(1 for p in prompts if p['active'])
        steps = [
            {'key': 'ai', 'label': 'Kết nối AI', 'done': ai_ready,
             'detail': (config.ai_model_id.display_name or 'Thứ tự kết nối chung') if ai_ready else 'Chưa có model AI dùng được',
             'cta': 'settings'},
            {'key': 'analysis', 'label': 'Phân tích tin nhắn', 'done': bool(latest_ai),
             'detail': ('Gần nhất: %s' % self._fmt_dt(latest_ai.finished_at)) if latest_ai else 'Chưa chạy đợt có AI',
             'cta': 'run'},
            {'key': 'rules', 'label': 'Duyệt quy tắc', 'done': bool(rule_counts.get('approved')) and not rule_counts.get('proposed'),
             'detail': ('%s quy tắc chờ duyệt' % rule_counts['proposed']) if rule_counts.get('proposed') else
                       ('%s quy tắc đang dùng' % rule_counts.get('approved', 0)), 'cta': 'rules'},
            {'key': 'prompt', 'label': 'Kích hoạt system prompt', 'done': active_count > 0,
             'detail': '%s/3 bộ đang dùng' % active_count, 'cta': 'prompts'},
            {'key': 'bot', 'label': 'Chatbot trả lời khách', 'done': False, 'soon': True, 'detail': 'Sắp có', 'cta': False},
        ]
        return {
            'steps': steps,
            'config': {'model': config.ai_model_id.display_name or 'Thứ tự kết nối chung', 'auto_weekly': config.auto_weekly,
                       'days_back': config.days_back, 'limit': config.conversation_limit,
                       'images': config.images_per_conversation if config.read_images else 0},
            'running': [{'id': a.id, 'name': a.name, 'progress': a.progress_pct, 'step': a.step_message or '', 'state': a.state}
                        for a in running],
            'latest': latest._dashboard_latest() if latest else False,
            'ai': latest_ai._dashboard_ai() if latest_ai else False,
            'rules': {'proposed': rule_counts.get('proposed', 0), 'approved': rule_counts.get('approved', 0),
                      'rejected': rule_counts.get('rejected', 0),
                      'pending': [{'id': r.id, 'name': r.name, 'content': r.content, 'evidence': r.evidence or '',
                                   'category': categories.get(r.category, r.category), 'priority': r.priority,
                                   'source': dict(r._fields['source'].selection).get(r.source)} for r in pending]},
            'prompts': prompts,
            'knowledge': {'count': self.env['dac.chat.knowledge'].search_count([]),
                          'chars': sum(self.env['dac.chat.knowledge'].search([]).mapped('char_count'))},
            'recent': [{'id': a.id, 'name': a.name, 'state': a.state, 'state_label': dict(a._fields['state'].selection)[a.state],
                        'progress': a.progress_pct, 'finished': self._fmt_dt(a.finished_at), 'conversations': a.conversation_count,
                        'labelled': a.insight_count, 'rules': a.rule_count, 'model': a.ai_model_id.display_name or ''}
                       for a in Analysis.search([], order='id desc', limit=6)],
        }

    @api.model
    def _fmt_dt(self, value):
        if not value:
            return ''
        return fields.Datetime.context_timestamp(self.with_context(tz='Asia/Ho_Chi_Minh'), value).strftime('%d/%m/%Y %H:%M')

    def _dashboard_latest(self):
        """Số liệu thống kê của đợt (toàn bộ tin trong kỳ)."""
        self.ensure_one()
        report = json.loads(self.report_json or '{}')
        o = report.get('overview') or {}
        return {
            'id': self.id, 'name': self.name, 'range': '%s – %s' % (self.date_from.strftime('%d/%m/%Y'), self.date_to.strftime('%d/%m/%Y')),
            'finished': self._fmt_dt(self.finished_at),
            'kpis': [
                {'key': 'new', 'label': 'Hội thoại khách mới', 'value': o.get('new_conversations', 0), 'unit': '',
                 'note': '%s hội thoại có tin trong kỳ' % o.get('conversations', 0)},
                {'key': 'deal', 'label': 'Chốt đơn', 'value': o.get('new_deal_pct'), 'unit': '%',
                 'note': 'Gửi STK / cọc / chuyển khoản / có đơn'},
                {'key': 'engaged', 'label': 'Khách nhắn lại', 'value': o.get('new_engaged_pct'), 'unit': '%',
                 'note': 'Sau lượt trả lời đầu tiên'},
                {'key': 'after', 'label': 'Nhắn ngoài giờ', 'value': o.get('new_after_hours_pct'), 'unit': '%',
                 'note': 'Phần chatbot nên lo'},
                {'key': 'resp_in', 'label': 'Trả lời đầu (trong giờ)', 'value': o.get('first_response_in_hours_median'), 'unit': ' phút',
                 'note': 'Trung vị'},
                {'key': 'resp_out', 'label': 'Trả lời đầu (ngoài giờ)', 'value': o.get('first_response_after_hours_median'),
                 'unit': ' phút', 'note': 'Trung vị'},
            ],
        }

    def _dashboard_ai(self):
        """Nhãn AI + đánh giá nhân viên + kết luận của đợt có AI."""
        self.ensure_one()
        Insight = self.env['dac.chat.insight']

        def bars(group_by, filters=None, top=8):
            rows = Insight._tool_label_stats(self, group_by, filters)[:top]
            return [{'key': r['key'], 'label': r['value'], 'count': r['conversations'], 'share': r['share_pct'],
                     'deal': r['deal_pct']} for r in rows]

        staff = []
        for line in self.staff_line_ids.filtered('ai_conversations').sorted(lambda l: -l.ai_conversations):
            staff.append({
                'id': line.id, 'name': line.name, 'conversations': line.ai_conversations, 'overall': line.ai_overall,
                'deal': line.ai_deal_pct, 'lost': line.ai_lost_pct, 'response': line.response_in_hours_median or None,
                'scores': [{'label': SCORE_LABELS[k], 'value': line['ai_%s' % k]} for k in SCORES],
                'strengths': line.ai_strengths or '', 'improvements': line.ai_improvements or '', 'coaching': line.ai_coaching or '',
            })
        return {
            'id': self.id, 'name': self.name, 'finished': self._fmt_dt(self.finished_at), 'summary': self.ai_summary or '',
            'bot_scope': self.coord_bot_scope or '', 'labelled': self.insight_count, 'images': self.images_sent,
            'tokens_in': self.tokens_in, 'tokens_out': self.tokens_out, 'model': self.ai_used_models or '',
            'outcome': bars('outcome'), 'lost': bars('lost_reason', {'outcome': 'mat_khach'}) or bars('lost_reason'),
            'product': bars('product'), 'bot_fit': bars('bot_fit'), 'staff': staff,
        }

    @api.model
    def dashboard_open_insights(self, analysis_id, field=None, value=None):
        """Mở danh sách nhãn hội thoại đã lọc (bấm vào một cột biểu đồ)."""
        action = self.env['ir.actions.actions']._for_xml_id('dac_chat_playbook.chat_insight_action')
        domain = [('analysis_id', '=', analysis_id)]
        if field in ('outcome', 'lost_reason', 'product', 'bot_fit', 'staff_name') and value is not None:
            domain.append((field, '=', value or False))
        action['domain'] = domain
        return action
