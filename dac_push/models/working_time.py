"""Tính hạn nhắc nhở theo giờ làm việc của công ty (giờ Việt Nam)."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Ho_Chi_Minh')
WORK_DAYS = (0, 1, 2, 3, 4, 5)  # thứ 2 → thứ 7 (Monday = 0)
WORK_SHIFTS = ((time(8, 0), time(12, 0)), (time(13, 30), time(17, 30)))


def add_working_minutes(start, minutes):
    """Thời điểm sau `minutes` phút *làm việc* kể từ `start`.

    `start` và kết quả là datetime naive theo UTC (đúng kiểu Odoo lưu). Thời gian ngoài ca (nghỉ trưa, sau 17:30,
    trước 8:00, Chủ nhật) không được tính: khách đến lúc 11:50 thì hạn là 13:50; đến lúc 20:00 thì tính từ 8:00 sáng
    ngày làm việc kế tiếp.
    """
    current = start.replace(tzinfo=timezone.utc).astimezone(TZ)
    remaining = timedelta(minutes=minutes)
    for _ in range(15):  # tối đa hai tuần phía trước, phòng cấu hình ca rỗng
        if current.weekday() in WORK_DAYS:
            for begin, end in WORK_SHIFTS:
                shift_begin = current.replace(hour=begin.hour, minute=begin.minute, second=0, microsecond=0)
                shift_end = current.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
                from_time = max(current, shift_begin)
                if from_time >= shift_end:
                    continue
                available = shift_end - from_time
                if remaining <= available:
                    return (from_time + remaining).astimezone(timezone.utc).replace(tzinfo=None)
                remaining -= available
        current = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start + timedelta(minutes=minutes)  # không đến được: nhắc theo giờ thường còn hơn không nhắc
