"""Phần dùng chung khi nhắc qua Zalo: mức độ gấp + icon, giờ làm việc, chọn hội thoại nhận, tag đúng người."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from odoo.addons.dac_push.models.working_time import WORK_DAYS, WORK_SHIFTS, add_working_minutes
from odoo.addons.dac_zalo_assistant.models.channels import MAX_MENTIONS, utf16_len

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
DAY_START_REMIND = time(8, 30)  # khách nhắn ngoài giờ: đã có trong tin tổng 8:00, nhắc riêng từ 8:30

# Mức độ gấp do AI đánh giá (và tự nâng theo thời gian chờ)
LEVELS = [('urgent', '🚨 Khẩn'), ('high', '⚠️ Chú ý'), ('normal', '🔔 Thường'), ('none', '⚪ Không cần trả lời')]
ICONS = {'urgent': '🚨', 'high': '⚠️', 'normal': '🔔', 'none': '⚪'}
HEADLINE = {'urgent': '🚨 KHẨN', 'high': '⚠️ CHÚ Ý', 'normal': '🔔', 'none': '⚪'}
RANK = {'none': 0, 'normal': 1, 'high': 2, 'urgent': 3}
ESCALATE_HIGH = 60     # chờ quá 1 giờ làm việc → ít nhất "Chú ý"
ESCALATE_URGENT = 120  # chờ quá 2 giờ làm việc → "Khẩn"


def local(moment):
    return moment.replace(tzinfo=timezone.utc).astimezone(TZ)


def utc(day, at):
    return datetime.combine(day, at, tzinfo=TZ).astimezone(timezone.utc).replace(tzinfo=None)


def working_minutes_between(start, end):
    """Số phút làm việc (thứ 2 → thứ 7, 8:00–12:00 và 13:30–17:30) giữa hai mốc naive UTC."""
    if not start or not end or end <= start:
        return 0
    begin, stop = local(start), local(end)
    total, day = 0.0, begin.date()
    for _ in range(40):
        if day > stop.date():
            break
        if day.weekday() in WORK_DAYS:
            for shift_begin, shift_end in WORK_SHIFTS:
                low = max(datetime.combine(day, shift_begin, tzinfo=TZ), begin)
                high = min(datetime.combine(day, shift_end, tzinfo=TZ), stop)
                if high > low:
                    total += (high - low).total_seconds() / 60
        day += timedelta(days=1)
    return int(total)


def due_after(since, minutes):
    """Hạn nhắc = `minutes` phút làm việc sau `since`; khách nhắn ngoài giờ (đêm qua, sáng sớm) thì không sớm hơn 8:30."""
    due = add_working_minutes(since, minutes)
    start = add_working_minutes(since, 0)
    if start - since > timedelta(minutes=1):
        start_local = local(start)
        if start_local.time() == WORK_SHIFTS[0][0]:
            due = max(due, utc(start_local.date(), DAY_START_REMIND))
    return due


def waited_text(minutes):
    minutes = max(0, int(minutes))
    if minutes < 60:
        return '%d phút' % minutes
    hours, rest = divmod(minutes, 60)
    return '%d giờ %d phút' % (hours, rest) if rest else '%d giờ' % hours


def escalate(level, waited):
    """Mức gấp hiển thị: mức AI đánh giá, nâng dần khi khách chờ lâu."""
    level = level if level in RANK else 'normal'
    if level == 'none':
        return level
    if waited >= ESCALATE_URGENT:
        return 'urgent'
    if waited >= ESCALATE_HIGH and RANK[level] < RANK['high']:
        return 'high'
    return level


class Builder:
    """Ghép tin có tag tên ở giữa: vị trí tag tính theo ký tự UTF-16 như Zalo."""

    def __init__(self):
        self.parts, self.mentions, self.pos = [], [], 0

    def add(self, text):
        self.parts.append(text)
        self.pos += utf16_len(text)
        return self

    def tags(self, targets):
        """`targets`: [(thành viên nhóm hoặc None, tên)]. Có thành viên → tag thật (Zalo báo cho người đó);
        không có (người đó không ở nhóm này, vd nhóm thử) → chỉ ghi "@tên" bằng chữ thường."""
        for index, (member, name) in enumerate(targets):
            if index:
                self.add(' ')
            token = '@' + ' '.join((name or 'ai đó').split())
            if member and len(self.mentions) < MAX_MENTIONS:
                self.mentions.append({'pos': self.pos, 'len': utf16_len(token), 'uid': member.uid})
            self.add(token)
        return self

    def result(self):
        return ''.join(self.parts), self.mentions or None


def event_types(env, codes):
    # sudo(): luồng nền không có user; chỉ đọc danh mục loại thông báo
    Type = env['dac.zalo.event.type'].sudo()
    return [t for t in (Type.search([('code', '=', code)], limit=1) for code in codes) if t]


def channels_for(env, config, codes, is_test=False):
    """Hội thoại Zalo nhận tin: đăng ký loại thông báo đầu tiên trong `codes` có người đăng ký (loại sau là dự phòng).

    Chưa được duyệt gửi nhóm thật (`remind_real_channels` tắt) thì CHỈ hội thoại "Thử nghiệm" nhận, kể cả dữ liệu thật."""
    Channel = env['dac.zalo.channel'].sudo()  # sudo(): xem event_types
    only_test = True if not config.remind_real_channels else bool(is_test)
    for event_type in event_types(env, codes):
        channels = Channel.search([('active', '=', True), ('event_type_ids', 'in', event_type.id), ('is_test', '=', only_test)])
        if channels:
            return channels, event_type
    return Channel, None


def fallback_targets(channel, types):
    """Người được cài "Nhắc tên" cho loại thông báo (dùng khi chưa xác định được nhân viên phụ trách)."""
    for event_type in types:
        members = channel._mention_members(event_type)
        if members:
            return [(member, member.name or member.uid) for member in members]
    return []


def staff_targets(channel, staff):
    """Nhân viên Pancake đã ghép với Zalo → [(thành viên nhóm hoặc None, tên)]; người chưa ghép thì bỏ qua."""
    out, seen = [], set()
    if channel.thread_type != 'group':
        return out
    for person in staff:
        if not person.zalo_uid:
            continue
        member = channel.member_ids.filtered(lambda m, uid=person.zalo_uid: m.uid == uid)[:1]
        key = person.zalo_uid
        if key in seen:
            continue
        seen.add(key)
        out.append((member or None, member.name if member else (person.zalo_name or person.name)))
    return out[:MAX_MENTIONS]


def targets_for(channel, staff, types):
    """Tag nhân viên phụ trách; chưa xác định được (hoặc chưa ghép Zalo) thì tag những người được cài sẵn."""
    return staff_targets(channel, staff) or (fallback_targets(channel, types) if channel.thread_type == 'group' else [])


def send(env, config, codes, key, build, is_test=False, image=None, reply_to=None):
    """Xếp hàng tin cho từng hội thoại nhận. `build(channel, types)` → (text, mentions) hoặc None (bỏ qua hội thoại đó).
    `reply_to`: {channel_id: tin đã gửi} để tin mới hiện dạng trả lời tin đó."""
    channels, event_type = channels_for(env, config, codes, is_test)
    types = event_types(env, codes)
    Message = env['dac.zalo.channel.message']
    queued = Message
    for channel in channels:
        built = build(channel, types)
        if not built:
            continue
        text, mentions = built
        previous = (reply_to or {}).get(channel.id)
        queued |= channel._queue(text, '%s:%s' % (key, channel.id), event_type, mentions=mentions, image=image,
                                 reply_to=previous if previous and not image else None)
    if queued:
        env['dac.zalo.channel']._trigger_send()
    return queued
