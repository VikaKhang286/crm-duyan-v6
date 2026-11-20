import logging
from datetime import datetime, time, timedelta, timezone

from odoo import api, fields, models
from odoo.addons.dac_erp.models.phone_utils import normalize_phone_vn
from odoo.addons.dac_push.models.working_time import TZ, WORK_DAYS

from . import zalo_out
from .remind_wait import MAX_AGE
from .zalo_out import ICONS

_logger = logging.getLogger(__name__)

# Các mốc gửi thống kê khách liên hệ (kèm tin nhắc tổng); mỗi mốc tính từ mốc trước: 17:00 hôm trước → 8:00, 8:00 → 11:30, 11:30 → 17:00
STATS_SLOTS = (time(8, 0), time(11, 30), time(17, 0))
PLATFORM_LABELS = {'zalo': 'Zalo', 'facebook': 'Messenger', 'messenger': 'Messenger', 'instagram': 'Instagram'}


def _local(moment):
    return moment.replace(tzinfo=timezone.utc).astimezone(TZ)


def _utc(day, at):
    return datetime.combine(day, at, tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)


def stats_window(slot):
    """(bắt đầu, kết thúc) của thống kê gửi ở mốc `slot` (naive UTC), hoặc None nếu mốc này không gửi thống kê (vd 14:00)."""
    local = _local(slot)
    if local.time().replace(second=0, microsecond=0) not in STATS_SLOTS:
        return None
    for back in range(0, 9):  # mốc thống kê ngay trước, trong ngày làm việc (8:00 thứ 2 → 17:00 thứ 7)
        day = local.date() - timedelta(days=back)
        if day.weekday() not in WORK_DAYS:
            continue
        for at in reversed(STATS_SLOTS):
            moment = _utc(day, at)
            if moment < slot:
                return moment, slot
    return slot - timedelta(days=1), slot


class WebLeadPancake(models.Model):
    _inherit = 'dac.web.lead'

    pancake_conversation_ids = fields.Many2many('dac.pancake.conversation', 'dac_web_lead_pancake_conversation_rel', 'lead_id', 'conversation_id',
        string='Hội thoại Pancake cùng số điện thoại', readonly=True, copy=False)
    pancake_match_note = fields.Char(string='Trùng với khách đã liên hệ', readonly=True, copy=False,
        help='Số điện thoại này đã có trên Pancake (hoặc đã điền form trước đó): khách đã liên hệ từ trước, không phải khách mới.')

    @api.model_create_multi
    def create(self, vals_list):
        leads = super().create(vals_list)
        for lead in leads:
            try:
                with self.env.cr.savepoint():
                    lead._check_pancake()
            except Exception:  # noqa: BLE001 — kiểm tra trùng/thống kê không bao giờ được làm hỏng việc nhận lead
                _logger.exception('Không kiểm tra được số điện thoại của lead %s trên Pancake', lead.id)
        return leads

    def _check_pancake(self):
        """Đối chiếu số điện thoại lead với Pancake (và lead cũ) rồi ghi vào thống kê khách mới."""
        self.ensure_one()
        number = normalize_phone_vn(self.phone)
        pages = self.env['dac.pancake.remind.wait']._pages(self.env['dac.pancake.remind.config']._get())
        conversations = self.env['dac.pancake.phone']._conversations_for(number, pages) if number else self.env['dac.pancake.conversation']
        contact = self.env['dac.new.contact']._record_lead(self)
        vals = {'pancake_conversation_ids': [(6, 0, conversations.ids)]}
        if contact.seen_before:
            vals['pancake_match_note'] = contact.seen_note
        self.write(vals)

    def _pancake_match_line(self):
        """Dòng cảnh báo trong tin Zalo khi số này đã nhắn Pancake: "⚠️ Số này đã nhắn Pancake: Tên — link"."""
        self.ensure_one()
        shown = self.pancake_conversation_ids[:2]
        if not shown:
            return ''
        return '⚠️ Số này đã nhắn Pancake trước đó: ' + '; '.join(
            '%s — %s' % (c.customer_name or 'Khách', c._short_url() or c.page_id.name) for c in shown)

    def _zalo_message(self):
        text, styles = super()._zalo_message()
        line = self._pancake_match_line() if self.pancake_conversation_ids else ''
        if line:
            head, _, last = text.rpartition('\n')  # dòng cuối là "Xem chi tiết: link": chèn cảnh báo ngay trước nó
            text = '%s\n%s\n%s' % (head, line, last) if head else '%s\n%s' % (line, last)
        return text, styles

    @api.model
    def _summary_stats(self, slot):
        """Thống kê khách liên hệ mới (lead website + Pancake, không tính nội bộ/test/trùng số) trong khoảng từ mốc trước."""
        stats = super()._summary_stats(slot)
        window = stats_window(slot)
        config = self.env['dac.pancake.remind.config']._get()
        if not window or not config.active or not self.env['dac.pancake.remind.scenario']._on('stats'):
            return stats
        start, end = window
        # sudo(): cron không có user; chỉ đếm số lượt và chọn hội thoại Zalo nhận thống kê
        contacts = self.env['dac.new.contact'].sudo().search([('contact_at', '>=', start), ('contact_at', '<', end), ('counts_as_new', '=', True)])
        counts = {}
        for contact in contacts:
            label = 'Form web' if contact.source == 'web_lead' else PLATFORM_LABELS.get((contact.platform or '').lower(), 'Pancake')
            counts[label] = counts.get(label, 0) + 1
        order = ['Form web', 'Zalo', 'Messenger', 'Instagram', 'Pancake']
        waiting = self.env['dac.pancake.remind.wait'].search_count([('state', '=', 'waiting'), ('is_test', '=', False)])
        lines = ['📊 Khách liên hệ mới %s → %s: %d' % (_local(start).strftime('%H:%M %d/%m'), _local(end).strftime('%H:%M %d/%m'), len(contacts))]
        if contacts:
            lines.append(' · '.join('%s: %d' % (label, counts[label]) for label in order if counts.get(label)))
        if waiting:
            lines.append('Đang có %d khách Pancake chờ trả lời' % waiting)
        Channel = self.env['dac.zalo.channel'].sudo()
        types = self.env.ref('dac_push.event_type_web_lead_created') | self.env.ref('dac_pancake_remind.event_type_pancake_unanswered')
        # Chưa được duyệt gửi nhóm thật thì chỉ gửi vào hội thoại thử nghiệm
        channels = Channel.search([('active', '=', True), ('is_test', '=', not config.stats_real_channels), ('event_type_ids', 'in', types.ids)])
        if not channels:
            return stats
        text = '\n'.join(lines)
        if stats:
            return {'text': stats['text'] + '\n' + text, 'channels': stats['channels'] | channels}
        return {'text': text, 'channels': channels}

    @api.model
    def _summary_entries(self, slot):
        """Khách Pancake còn chờ trả lời: cùng vào tin nhắc tổng với lead, mỗi dòng "Tên: link Pancake"."""
        entries = super()._summary_entries(slot)
        config = self.env['dac.pancake.remind.config']._get()
        if not config.active:
            return entries
        Wait = self.env['dac.pancake.remind.wait']
        now = fields.Datetime.now()
        waits = Wait.search([('state', 'in', ('waiting', 'done')), ('since', '<', slot), ('since', '>=', now - MAX_AGE)], order='since, id')
        targets = {}
        for wait in waits:
            if wait._answered_since():
                continue  # đã có người trả lời nhưng chưa kịp đóng bản ghi
            if wait.is_test not in targets:
                # Chưa duyệt gửi nhóm thật thì chỉ hội thoại "Thử nghiệm" nhận (kể cả khách thật) — xem zalo_out.channels_for
                targets[wait.is_test] = zalo_out.channels_for(self.env, config, ('pancake.unanswered',), wait.is_test)
            channels, event_type = targets[wait.is_test]
            conversation = wait.conversation_id.sudo()
            icon = ICONS.get(wait.level, '') if wait.level in ('urgent', 'high') else ''
            for channel in channels:
                entries.append({'channel': channel, 'at': wait.since, 'event_type': event_type,
                                'line': '%s%s: %s' % (icon + ' ' if icon else '', conversation.customer_name or 'Khách',
                                                      conversation._short_url() or conversation.page_id.name)})
        return entries
