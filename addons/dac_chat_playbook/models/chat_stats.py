"""Bộ máy thống kê cách nhân viên nhắn tin với khách (Python thuần, không phụ thuộc Odoo để test và chạy thử trên bản sao DB).

Đầu vào: từng hội thoại (thông tin + danh sách tin đã phân loại customer/staff/bot, sắp theo thời gian).
Đầu ra: dict JSON được (tổng quan, theo nhân viên, hiệu quả từng kiểu nhắn, câu mở đầu, từ ngữ, câu hỏi lấy thông tin,
giờ khách nhắn, danh sách hội thoại mẫu tốt/chưa tốt để đưa cho AI đọc).

Định nghĩa:
* Lượt (turn) của nhân viên: các tin nhân viên gửi liền nhau, không có tin khách xen giữa, cách nhau không quá TURN_GAP.
* Hội thoại mới: hội thoại bắt đầu trong kỳ và tin đầu tiên là của khách.
* Hiệu quả: "chốt đơn" (`deal`: nhân viên gửi số tài khoản / xin cọc, khách báo đã chuyển khoản / chốt, hoặc có đơn Odoo có
  giá) hoặc khách để lại số điện thoại (`phone`). Phụ: "đã báo giá" (`quoted`) và "khách phản hồi" (`engaged`: khách nhắn lại
  sau lượt trả lời đầu tiên của nhân viên). Đây là tương quan, không phải nhân quả.
"""
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
WORK_DAYS = (0, 1, 2, 3, 4, 5)
WORK_SHIFTS = ((time(8, 0), time(12, 0)), (time(13, 30), time(17, 30)))
TURN_GAP = timedelta(minutes=20)
MAX_RESPONSE = timedelta(days=3)
WEEKDAYS = ('Thứ 2', 'Thứ 3', 'Thứ 4', 'Thứ 5', 'Thứ 6', 'Thứ 7', 'Chủ nhật')
UNKNOWN_STAFF = 'Gửi từ điện thoại (không rõ tên)'

PHONE_RE = re.compile(r'(?<!\d)(?:\+?84|0)(?:[\s.\-]?\d){9}(?!\d)')
EMOJI_RE = re.compile('[\U0001F300-\U0001FAFF☀-➿]')
WORD_RE = re.compile(r'[^\W\d_]+', re.UNICODE)
TAG_RE = re.compile(r'<[^>]+>')

# Thông tin nhân viên hay hỏi khách (khớp trên chữ đã bỏ dấu, chữ thường)
INFO_TOPICS = (
    ('phone', 'Số điện thoại / Zalo', r'\b(sdt|so dien thoai|so dt|so phone|xin so|so zalo|zalo cua|ket ban zalo)\b'),
    ('name', 'Tên / cách xưng hô', r'\b(ten (anh|chi|ban|cong ty|shop|cua hang)|xung ho|anh chi ten)\b'),
    ('product', 'Sản phẩm / hạng mục cần làm', r'\b(can (lam|in|dat) (gi|loai)|lam loai nao|in gi|san pham gi|hang muc)\b'),
    ('size', 'Kích thước', r'\b(kich thuoc|kt|size|dai rong|chieu (dai|rong|cao)|ngang bao nhieu|cao bao nhieu|bao nhieu (cm|met|m))\b'),
    ('quantity', 'Số lượng', r'\b(so luong|bao nhieu (cai|tam|to|bo|hop|chiec|cuon)|may (cai|tam|to|bo|hop|chiec))\b'),
    ('material', 'Chất liệu', r'\b(chat lieu|loai giay|giay gi|in tren|mica|alu|bat|decal|formex|hiflex)\b'),
    ('design', 'File / thiết kế / nội dung', r'\b(file|thiet ke|mau co san|logo|noi dung|maket)\b'),
    ('deadline', 'Thời gian cần hàng', r'\b(khi nao (can|lay)|bao gio (can|lay)|ngay may|can gap|can truoc|lay hang khi nao|can luc nao)\b'),
    ('address', 'Địa chỉ / khu vực', r'\b(dia chi|o dau|khu vuc|giao (ve|toi|den) dau|quan nao|tinh nao|lap dat o)\b'),
    ('budget', 'Ngân sách', r'\b(ngan sach|tam gia|khoang bao nhieu tien|du tinh)\b'),
)
INFO_PATTERNS = [(key, label, re.compile(pattern)) for key, label, pattern in INFO_TOPICS]
GREETING_RE = re.compile(r'\b(xin chao|chao (anh|chi|ban|em|quy khach|a|c)|hello|\bhi\b)')
POLITE_RE = re.compile(r'(^|\s)(da|a)(\s|$|[.!?,])')  # "dạ" / "ạ" sau khi bỏ dấu
QUESTION_RE = re.compile(r'\?|\b(cho (em|chi|minh) xin|khong|chua|nao|gi|bao nhieu|may|dau|sao|the nao|nhi)'
                         r'( (a|anh|chi|em|ban|nha|vay|ha|nhe))*\s*[.!]*$')
# Kết quả của hội thoại (đo trên chữ đã bỏ dấu)
QUOTE_RE = re.compile(r'\b(bao gia|\d{1,3}(?:[.,]\d{3})+ ?(d|vnd|dong)?|\d+ ?(k|tr|trieu|nghin|ngan)\b|gia (la|chi|chua|tron))')
DEAL_STAFF_RE = re.compile(r'\b(so tai khoan|stk|tam ung|dat coc|coc truoc|chuyen khoan (giup|cho|vao|qua))')
DEAL_CUSTOMER_RE = re.compile(r'\b(da (ck|chuyen khoan|chuyen)|ck (roi|xong|nhe|nha)|chuyen khoan (roi|xong)|dat coc|coc (roi|nha)|chot (nha|nhe|don|luon)|lam luon|len don)\b')
MARKERS = (  # từ/cụm đặc trưng giọng nhắn (đếm trên chữ CÓ dấu, chữ thường)
    'dạ', 'ạ', 'nha', 'nhé', 'em', 'anh', 'chị', 'mình', 'bạn', 'quý khách', 'cảm ơn', 'xin lỗi', 'ok', 'vâng', 'ơi',
)
STOPWORDS = frozenset('và của là có cho thì mà với các những được này đó em anh chị a ạ dạ nha nhé ok vâng ko k thì rồi'.split())


def fold(text):
    """Bỏ dấu tiếng Việt, chữ thường, gom khoảng trắng."""
    text = (text or '').replace('đ', 'd').replace('Đ', 'D')
    text = unicodedata.normalize('NFD', text)
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return ' '.join(text.lower().split())


def plain(html):
    text = TAG_RE.sub(' ', html or '')
    for entity, char in (('&nbsp;', ' '), ('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'), ('&quot;', '"'), ('&#39;', "'")):
        text = text.replace(entity, char)
    return ' '.join(text.split())


def mask_phones(text):
    return PHONE_RE.sub('[SĐT]', text or '')


def local(moment):
    return moment.replace(tzinfo=timezone.utc).astimezone(TZ)


def in_work_hours(moment, work_days=WORK_DAYS, shifts=WORK_SHIFTS, holidays=()):
    """moment: naive UTC. `holidays`: các ngày nghỉ lễ (date, hoặc dict date → tên)."""
    loc = local(moment)
    if loc.weekday() not in work_days or loc.date() in holidays:
        return False
    now = loc.time()
    return any(begin <= now < end for begin, end in shifts)


def _median(values):
    return round(statistics.median(values), 1) if values else None


def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def _quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


class Msg:
    __slots__ = ('at', 'role', 'staff', 'text', 'media', 'phone', 'images')

    def __init__(self, at, role, staff='', text='', media=False, phone=False, images=()):
        self.at, self.role, self.staff, self.text, self.media, self.phone = at, role, staff, text or '', media, phone
        self.images = tuple(images or ())  # link ảnh (https) của tin


class Turn:
    __slots__ = ('role', 'messages', 'staff', 'wait_from')

    def __init__(self, role, first, wait_from=None):
        self.role, self.messages, self.staff, self.wait_from = role, [first], first.staff or UNKNOWN_STAFF, wait_from

    @property
    def start(self):
        return self.messages[0].at

    @property
    def texts(self):
        return [m.text for m in self.messages if m.text]

    @property
    def joined(self):
        return ' / '.join(self.texts)

    @property
    def response_minutes(self):
        if not self.wait_from:
            return None
        delta = self.start - self.wait_from
        return round(delta.total_seconds() / 60.0, 1) if timedelta(0) <= delta <= MAX_RESPONSE else None


def build_turns(messages):
    """Gom tin thành lượt. Tin bot bị bỏ qua (không tính là nhân viên, không cắt lượt khách)."""
    turns = []
    waiting_since = None
    for msg in messages:
        if msg.role not in ('customer', 'staff'):
            continue
        last = turns[-1] if turns else None
        if last and last.role == msg.role and (msg.role == 'customer' or msg.at - last.messages[-1].at <= TURN_GAP):
            last.messages.append(msg)
            if msg.role == 'staff' and last.staff == UNKNOWN_STAFF and msg.staff:
                last.staff = msg.staff
            continue
        if msg.role == 'customer':
            if waiting_since is None:
                waiting_since = msg.at
            turns.append(Turn('customer', msg))
        else:
            turns.append(Turn('staff', msg, wait_from=waiting_since))
            waiting_since = None
    return turns


def bucket(value, edges, labels):
    for edge, label in zip(edges, labels):
        if value <= edge:
            return label
    return labels[-1]


MSG_BUCKETS = ((1, 2, 3), ('1 tin', '2 tin', '3 tin', '4+ tin'))
CHAR_BUCKETS = ((30, 80, 160), ('≤ 30 ký tự', '31–80 ký tự', '81–160 ký tự', '> 160 ký tự'))
RESPONSE_BUCKETS = ((5, 15, 60), ('≤ 5 phút', '6–15 phút', '16–60 phút', '> 60 phút'))


class Analyzer:
    """Cộng dồn từng hội thoại bằng `add()`, xong gọi `report()`."""

    def __init__(self, period_start, period_end, work_days=WORK_DAYS, shifts=WORK_SHIFTS, sample_cap=4000):
        self.start, self.end = period_start, period_end
        self.work_days, self.shifts = work_days, shifts
        self.sample_cap = sample_cap
        self.totals = Counter()
        self.staff = defaultdict(lambda: {
            'conversations': set(), 'turns': 0, 'messages': 0, 'text_messages': 0, 'media': 0, 'chars': [], 'turn_sizes': Counter(),
            'questions': 0, 'polite_turns': 0, 'emoji': 0, 'response_in_hours': [], 'first_turns': 0, 'first_phone': 0,
            'first_engaged': 0, 'first_ordered': 0, 'first_quoted': 0, 'first_deal': 0,
        })
        self.effects = defaultdict(lambda: defaultdict(Counter))
        self.openers = defaultdict(Counter)
        self.opener_examples = {}
        self.customer_openers = Counter()
        self.bigrams, self.trigrams, self.markers = Counter(), Counter(), Counter()
        self.info = {key: Counter() for key, _label, _p in INFO_PATTERNS}
        self.info_turn_index = defaultdict(list)
        self.hours = Counter()
        self.weekday = Counter()
        self.first_response_in, self.first_response_out = [], []
        self.samples = []
        self.good_first = {'turn_sizes': [], 'chars': [], 'response': []}

    # ── cộng dồn ─────────────────────────────────────────────────────────────
    def add(self, conversation, messages):
        """conversation: dict(id, inserted_at, ordered); messages: list[Msg] đã sắp theo thời gian, chỉ trong kỳ."""
        messages = [m for m in messages if m.role in ('customer', 'staff', 'bot')]
        if not messages:
            return
        self.totals['conversations'] += 1
        for msg in messages:
            self.totals['msg_' + msg.role] += 1
        turns = build_turns(messages)
        staff_turns = [t for t in turns if t.role == 'staff']
        for turn in staff_turns:
            self._add_staff_turn(conversation, turn)

        started = conversation.get('inserted_at') or messages[0].at
        first = next((m for m in messages if m.role in ('customer', 'staff')), None)
        is_new = bool(first and first.role == 'customer' and self.start <= started < self.end)
        outcome = self._add_new_conversation(conversation, messages, turns, staff_turns) if is_new else None
        # Mẫu cho AI đọc: mọi hội thoại đã có nhân viên trả lời (khách mới và khách cũ)
        if staff_turns and len(self.samples) < self.sample_cap:
            outcome = outcome or self._outcome(conversation, messages, turns, staff_turns)
            score = 3 * outcome['deal'] + 2 * outcome['phone'] + outcome['quoted'] + outcome['engaged'] + min(len(staff_turns), 4) * 0.1
            self.samples.append({
                'id': conversation['id'], 'new': is_new, 'good': outcome['good'], 'deal': outcome['deal'], 'engaged': outcome['engaged'],
                'after_hours': outcome['after_hours'], 'staff_turns': len(staff_turns), 'score': round(score, 2),
                'messages': len(messages), 'staff': staff_turns[0].staff, 'first_response': outcome['first_response'],
                'last_at': str(messages[-1].at.replace(microsecond=0)), 'images': sum(len(m.images) for m in messages),
            })

    def _outcome(self, conversation, messages, turns, staff_turns):
        """Kết quả của một hội thoại (đo bằng code)."""
        first_customer = next((m for m in messages if m.role == 'customer'), messages[0])
        phone = any(m.phone for m in messages if m.role == 'customer')
        ordered = bool(conversation.get('ordered'))
        quoted = any(m.role == 'staff' and m.text and QUOTE_RE.search(fold(m.text)) for m in messages)
        deal = ordered or any(m.text and (DEAL_STAFF_RE if m.role == 'staff' else DEAL_CUSTOMER_RE).search(fold(m.text)) for m in messages)
        engaged, minutes = False, None
        if staff_turns:
            index = turns.index(staff_turns[0])
            engaged = any(t.role == 'customer' for t in turns[index + 1:])
            minutes = staff_turns[0].response_minutes
        return {'after_hours': not in_work_hours(first_customer.at, self.work_days, self.shifts), 'phone': phone, 'ordered': ordered,
                'quoted': quoted, 'deal': deal, 'good': deal or phone, 'engaged': engaged, 'first_response': minutes}

    def _add_staff_turn(self, conversation, turn):
        stat = self.staff[turn.staff]
        stat['conversations'].add(conversation['id'])
        stat['turns'] += 1
        stat['messages'] += len(turn.messages)
        stat['turn_sizes'][min(len(turn.messages), 4)] += 1
        polite = False
        for msg in turn.messages:
            if msg.media:
                stat['media'] += 1
            if not msg.text:
                continue
            stat['text_messages'] += 1
            stat['chars'].append(len(msg.text))
            folded = fold(msg.text)
            if QUESTION_RE.search(folded):
                stat['questions'] += 1
            if POLITE_RE.search(folded):
                polite = True
            stat['emoji'] += len(EMOJI_RE.findall(msg.text))
            self._add_vocabulary(msg.text)
        stat['polite_turns'] += polite
        minutes = turn.response_minutes
        if minutes is not None and turn.wait_from and in_work_hours(turn.wait_from, self.work_days, self.shifts):
            stat['response_in_hours'].append(minutes)

    def _add_vocabulary(self, text):
        lowered = text.lower()
        words = [w for w in WORD_RE.findall(lowered)]
        for marker in MARKERS:
            if re.search(r'(?<!\w)%s(?!\w)' % re.escape(marker), lowered):
                self.markers[marker] += 1
        self.totals['vocab_messages'] += 1
        for size, counter in ((2, self.bigrams), (3, self.trigrams)):
            for i in range(len(words) - size + 1):
                gram = words[i:i + size]
                if all(w in STOPWORDS for w in gram):
                    continue
                counter[' '.join(gram)] += 1

    def _add_new_conversation(self, conversation, messages, turns, staff_turns):
        """Cộng số liệu hội thoại khách mới; trả kết quả (dict của `_outcome`)."""
        self.totals['new_conversations'] += 1
        first_customer = next(m for m in messages if m.role == 'customer')
        loc = local(first_customer.at)
        outcome = self._outcome(conversation, messages, turns, staff_turns)
        after_hours, phone, ordered = outcome['after_hours'], outcome['phone'], outcome['ordered']
        quoted, deal, good = outcome['quoted'], outcome['deal'], outcome['good']
        self.hours[loc.hour] += 1
        self.weekday[loc.weekday()] += 1
        self.totals['new_after_hours'] += after_hours
        self.totals['new_phone'] += phone
        self.totals['new_ordered'] += ordered
        self.totals['new_quoted'] += quoted
        self.totals['new_deal'] += deal
        customer_text = ' / '.join(t for t in (m.text for m in turns[0].messages) if t) if turns else ''
        if customer_text:
            self.customer_openers[normalize_opener(customer_text)] += 1
        if not staff_turns:
            self.totals['new_unanswered'] += 1
            return outcome
        self.totals['new_answered'] += 1
        first = staff_turns[0]
        engaged, minutes = outcome['engaged'], outcome['first_response']
        self.totals['new_engaged'] += engaged
        if minutes is not None:
            (self.first_response_out if after_hours else self.first_response_in).append(minutes)

        stat = self.staff[first.staff]
        stat['first_turns'] += 1
        stat['first_phone'] += phone
        stat['first_engaged'] += engaged
        stat['first_ordered'] += ordered
        stat['first_quoted'] += quoted
        stat['first_deal'] += deal

        # Đặc điểm lượt trả lời đầu → hiệu quả
        texts = first.texts
        folded = fold(first.joined)
        avg_chars = statistics.mean(len(t) for t in texts) if texts else 0
        asked_phone_early = any(INFO_PATTERNS[0][2].search(fold(t.joined)) for t in staff_turns[:3])
        features = {
            'Số tin trong lượt đầu': bucket(len(first.messages), *MSG_BUCKETS),
            'Độ dài trung bình mỗi tin (lượt đầu)': bucket(avg_chars, *CHAR_BUCKETS) if texts else 'Chỉ gửi ảnh/file',
            'Có lời chào': 'Có' if GREETING_RE.search(folded) else 'Không',
            'Có "dạ"/"ạ"': 'Có' if POLITE_RE.search(folded) else 'Không',
            'Lượt đầu có câu hỏi': 'Có' if any(QUESTION_RE.search(fold(t)) for t in texts) else 'Không',
            'Lượt đầu có gửi ảnh/file': 'Có' if any(m.media for m in first.messages) else 'Không',
            'Xin SĐT/Zalo trong 3 lượt đầu': 'Có' if asked_phone_early else 'Không',
            'Khách nhắn lúc': 'Ngoài giờ làm việc' if after_hours else 'Trong giờ làm việc',
        }
        if minutes is not None and not after_hours:
            features['Thời gian trả lời đầu (trong giờ)'] = bucket(minutes, *RESPONSE_BUCKETS)
        for feature, value in features.items():
            counter = self.effects[feature][value]
            counter['n'] += 1
            counter['engaged'] += engaged
            counter['phone'] += phone
            counter['ordered'] += ordered
            counter['quoted'] += quoted
            counter['deal'] += deal

        if texts:
            key = normalize_opener(first.joined)
            self.openers[key].update(n=1, phone=phone, engaged=engaged, deal=deal)
            self.opener_examples.setdefault(key, mask_phones(first.joined)[:200])

        asked = set()
        for i, turn in enumerate(staff_turns):
            folded_turn = fold(turn.joined)
            for key, _label, pattern in INFO_PATTERNS:
                if key not in asked and pattern.search(folded_turn):
                    asked.add(key)
                    self.info[key]['asked'] += 1
                    self.info[key]['asked_phone'] += phone
                    self.info_turn_index[key].append(i + 1)

        if good:
            self.good_first['turn_sizes'].append(len(first.messages))
            self.good_first['chars'].extend(len(t) for t in texts)
            if minutes is not None and not after_hours:
                self.good_first['response'].append(minutes)
        return outcome

    # ── kết quả ──────────────────────────────────────────────────────────────
    def report(self, top=25):
        t = self.totals
        new, answered = t['new_conversations'], t['new_answered']
        overview = {
            'conversations': t['conversations'], 'messages_customer': t['msg_customer'], 'messages_staff': t['msg_staff'],
            'messages_bot': t['msg_bot'], 'new_conversations': new, 'new_answered_pct': _pct(answered, new),
            'new_after_hours_pct': _pct(t['new_after_hours'], new), 'new_phone_pct': _pct(t['new_phone'], new),
            'new_ordered_pct': _pct(t['new_ordered'], new), 'new_quoted_pct': _pct(t['new_quoted'], new),
            'new_deal_pct': _pct(t['new_deal'], new), 'new_engaged_pct': _pct(t['new_engaged'], answered),
            'first_response_in_hours_median': _median(self.first_response_in),
            'first_response_after_hours_median': _median(self.first_response_out),
        }
        staff_rows = []
        for name, s in self.staff.items():
            if s['turns'] < 5:
                continue
            sizes = s['turn_sizes']
            staff_rows.append({
                'name': name, 'conversations': len(s['conversations']), 'turns': s['turns'], 'messages': s['messages'],
                'messages_per_turn': round(s['messages'] / s['turns'], 2),
                'turn_1': _pct(sizes[1], s['turns']), 'turn_2': _pct(sizes[2], s['turns']), 'turn_3': _pct(sizes[3], s['turns']),
                'turn_4plus': _pct(sizes[4], s['turns']),
                'chars_avg': round(statistics.mean(s['chars']), 1) if s['chars'] else None, 'chars_median': _median(s['chars']),
                'chars_p90': _quantile(s['chars'], 0.9), 'question_pct': _pct(s['questions'], s['text_messages']),
                'polite_pct': _pct(s['polite_turns'], s['turns']), 'emoji_per_100': round(100.0 * s['emoji'] / s['text_messages'], 1) if s['text_messages'] else None,
                'media_pct': _pct(s['media'], s['messages']), 'response_in_hours_median': _median(s['response_in_hours']),
                'new_first_replies': s['first_turns'], 'new_phone_pct': _pct(s['first_phone'], s['first_turns']),
                'new_engaged_pct': _pct(s['first_engaged'], s['first_turns']), 'new_ordered_pct': _pct(s['first_ordered'], s['first_turns']),
                'new_quoted_pct': _pct(s['first_quoted'], s['first_turns']), 'new_deal_pct': _pct(s['first_deal'], s['first_turns']),
            })
        staff_rows.sort(key=lambda r: -r['turns'])

        effects = []
        for feature, values in self.effects.items():
            rows = [{'value': value, 'n': c['n'], 'engaged_pct': _pct(c['engaged'], c['n']), 'phone_pct': _pct(c['phone'], c['n']),
                     'quoted_pct': _pct(c['quoted'], c['n']), 'deal_pct': _pct(c['deal'], c['n'])} for value, c in values.items()]
            rows.sort(key=lambda r: r['value'])
            effects.append({'feature': feature, 'rows': rows})

        openers = sorted(({'text': self.opener_examples.get(k, k), 'n': c['n'], 'phone_pct': _pct(c['phone'], c['n']), 'deal_pct': _pct(c['deal'], c['n']),
                           'engaged_pct': _pct(c['engaged'], c['n'])} for k, c in self.openers.items() if c['n'] >= 2),
                         key=lambda r: -r['n'])[:top]
        info = [{'key': key, 'label': label, 'asked_pct': _pct(self.info[key]['asked'], answered),
                 'phone_pct_when_asked': _pct(self.info[key]['asked_phone'], self.info[key]['asked']),
                 'median_turn': _median(self.info_turn_index[key])} for key, label, _p in INFO_PATTERNS]
        info.sort(key=lambda r: -(r['asked_pct'] or 0))
        vocab_total = t['vocab_messages']
        good = self.good_first
        return {
            'period': {'start': self.start.isoformat(), 'end': self.end.isoformat()},
            'overview': overview,
            'staff': staff_rows,
            'effects': effects,
            'good_first_turn': {
                'conversations': len(good['turn_sizes']), 'messages_per_turn_median': _median(good['turn_sizes']),
                'chars_median': _median(good['chars']), 'chars_p75': _quantile(good['chars'], 0.75),
                'response_median': _median(good['response']),
            },
            'openers': openers,
            'customer_openers': [{'text': k, 'n': n} for k, n in self.customer_openers.most_common(top) if n >= 2],
            'info_questions': info,
            'markers_per_100': {m: round(100.0 * n / vocab_total, 1) for m, n in self.markers.most_common()} if vocab_total else {},
            'top_bigrams': [[g, n] for g, n in self.bigrams.most_common(40)],
            'top_trigrams': [[g, n] for g, n in self.trigrams.most_common(30)],
            'new_by_hour': {str(h): self.hours[h] for h in range(24)},
            'new_by_weekday': {WEEKDAYS[d]: self.weekday[d] for d in range(7)},
            'samples': self.samples,
        }


def normalize_opener(text):
    """Gom các câu mở đầu giống nhau: chữ thường, số → #, bỏ SĐT, cắt 120 ký tự."""
    text = mask_phones(text).lower()
    text = re.sub(r'\d+', '#', text)
    text = re.sub(r'[!.…~]+', '', text)
    return ' '.join(text.split())[:120]


def pick_samples(samples, limit, mode='mix'):
    """Chọn hội thoại cho AI đọc.
    mix: ~60% tốt (điểm cao nhất), ~25% chưa tốt (khách không phản hồi), ~15% ngoài giờ · recent: gần nhất ·
    good: ưu tiên hội thoại tốt · bad: ưu tiên khách không phản hồi / không chốt."""
    if limit <= 0:
        return []
    if mode == 'recent':
        return sorted(samples, key=lambda s: s.get('last_at') or '', reverse=True)[:limit]
    if mode == 'good':
        return sorted(samples, key=lambda s: (-s['score'], s.get('last_at') or ''))[:limit]
    if mode == 'bad':
        return sorted(samples, key=lambda s: (s['engaged'], s['good'], s['score']))[:limit]
    good = sorted((s for s in samples if s['good'] and s['staff_turns'] >= 2), key=lambda s: -s['score'])
    bad = [s for s in samples if not s['engaged']]
    after = [s for s in samples if s['after_hours'] and s['staff_turns']]
    chosen, seen = [], set()

    def take(pool, count):
        if count <= 0 or not pool:
            return
        # Lấy cách quãng cho đa dạng, rồi lấp tiếp bằng phần còn lại nếu trùng/thiếu
        step = max(1, len(pool) // count)
        for item in pool[::step] + [p for i, p in enumerate(pool) if i % step]:
            if len(chosen) >= limit or count <= 0:
                break
            if item['id'] not in seen:
                seen.add(item['id'])
                chosen.append(item)
                count -= 1

    take(good, round(limit * 0.6))
    take(bad, round(limit * 0.25))
    take(after, limit - len(chosen))
    take(sorted(samples, key=lambda s: -s['score']), limit - len(chosen))
    return chosen


def transcript(messages, started_label='', max_messages=30, max_chars=2200, image_labels=None):
    """Đoạn chat gọn cho AI đọc: [KH]/[NV tên], mốc phút từ tin đầu, SĐT đã che."""
    lines, used = [], 0
    if not messages:
        return ''
    t0 = messages[0].at
    for msg in messages[:max_messages]:
        if msg.role not in ('customer', 'staff'):
            continue
        who = 'KH' if msg.role == 'customer' else 'NV %s' % (msg.staff or '?')
        body = mask_phones(msg.text)[:400] if msg.text else ''
        labels = [image_labels[u] for u in msg.images if image_labels and u in image_labels]
        if labels:
            body = (body + ' ' if body else '') + ' '.join('[%s]' % l for l in labels)
        elif msg.media:
            body = (body + ' ' if body else '') + '[ảnh/file]'
        minutes = int((msg.at - t0).total_seconds() // 60)
        line = '+%sp [%s] %s' % (minutes, who, body or '[không có chữ]')
        if used + len(line) > max_chars:
            lines.append('…')
            break
        lines.append(line)
        used += len(line)
    head = 'Bắt đầu: %s' % started_label if started_label else ''
    return '\n'.join(([head] if head else []) + lines)


def weekday_label(moment):
    loc = local(moment)
    return '%s %s' % (WEEKDAYS[loc.weekday()], loc.strftime('%d/%m %H:%M'))


def _part_of_day(hour):
    return 'sáng' if hour < 12 else ('chiều' if hour < 18 else 'tối')


def next_working(moment, work_days=WORK_DAYS, shifts=WORK_SHIFTS, holidays=()):
    """Mốc bắt đầu làm việc kế tiếp sau `moment` (naive UTC) → (mốc naive UTC hoặc None nếu đang trong giờ, nhãn tiếng Việt).

    Nhãn để chatbot nói với khách, vd "13:30 chiều nay", "8:00 sáng mai (thứ 7, 21/11)", "8:00 sáng thứ 2 (23/11)".
    Bỏ qua các ngày trong `holidays` (nghỉ lễ)."""
    if in_work_hours(moment, work_days, shifts, holidays):
        return None, 'đang trong giờ làm việc'
    loc = local(moment)
    for ahead in range(45):
        day = (loc + timedelta(days=ahead)).date()
        if day.weekday() not in work_days or day in holidays:
            continue
        for begin, _end in shifts:
            start = datetime.combine(day, begin, tzinfo=TZ)
            if start <= loc:
                continue
            clock = '%d:%02d' % (begin.hour, begin.minute)
            part = _part_of_day(begin.hour)
            if ahead == 0:
                label = '%s %s nay' % (clock, part)
            elif ahead == 1:
                label = '%s %s mai (%s, %s)' % (clock, part, WEEKDAYS[day.weekday()].lower(), day.strftime('%d/%m'))
            else:
                label = '%s %s %s (%s)' % (clock, part, WEEKDAYS[day.weekday()].lower(), day.strftime('%d/%m'))
            return start.astimezone(timezone.utc).replace(tzinfo=None), label
    return None, 'ngày làm việc kế tiếp'


def now_label(moment):
    loc = local(moment)
    return '%s, %s lúc %s' % (WEEKDAYS[loc.weekday()], loc.strftime('%d/%m/%Y'), loc.strftime('%H:%M'))
