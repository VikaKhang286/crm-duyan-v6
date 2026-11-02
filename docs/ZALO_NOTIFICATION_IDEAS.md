# Đề xuất thông báo cho “tro ly DAC”

Giai đoạn 1 chỉ có kênh hội thoại, xác thực, AI và hàng đợi gửi. Các thông báo
trong bảng sau là đề xuất dựa trên model hiện có, chưa gắn hook hoặc bật cron.

| Ưu tiên | Sự kiện | Người nhận | Nội dung hữu ích |
|---|---|---|---|
| P1 | Được giao/chuyển người phụ trách task | `dac.work.task.assigned_user_id` mới | Tên việc, mã đơn, hạn, mức ưu tiên, link mở việc |
| P1 | Sắp đến hạn, đến `remind_at`, hoặc quá hạn | Nhân viên phụ trách | Nhắc trước 2 giờ, nhắc đúng giờ tự đặt; gom việc quá hạn vào bản tin |
| P1 | Đổi hạn hoặc yêu cầu quan trọng | Người đang phụ trách thiết kế/sản xuất | Giá trị cũ → mới, người thay đổi, lý do và link đơn |
| P1 | Bàn giao thiết kế → sản xuất | Người/nhóm sản xuất được giao | Link bản vẽ mới nhất, yêu cầu sản xuất, ngày hoàn tất |
| P1 | Việc thiết kế/sản xuất hoàn tất hoặc bị trả lại | Sale phụ trách và bộ phận kế tiếp | Việc nào hoàn tất/cần sửa, ghi chú, bước tiếp theo |
| P1 | Nhắc việc cá nhân đã đặt | Chủ nhân task `is_personal_reminder` | Nội dung và thời điểm tự đặt; cho phép tắt/hoãn trong giai đoạn sau |
| P2 | Sắp giao hàng hoặc thi công/lắp đặt | Người phụ trách và Sale | Thời gian dự kiến, nhánh fulfillment, thông tin liên hệ tối thiểu được phép |
| P2 | Chưa thu cọc hoặc còn tiền phải thu | Sale phụ trách; quản lý nhận tổng hợp | Mã đơn, khoản còn thu, tuổi công nợ, link đơn; không gửi tài chính cho Design/Production |
| P2 | Có trao đổi nội bộ cần phản hồi | Người được nhắc hoặc phụ trách trực tiếp | Trích đoạn ngắn và link; không phát tán toàn bộ chatter |
| P2 | Đơn nghỉ phép/điều chỉnh chấm công cần duyệt | Người duyệt | Nhân viên, khoảng ngày, loại yêu cầu và link duyệt; chưa duyệt bằng chat |
| P2 | Kết quả duyệt nghỉ phép/điều chỉnh | Người làm đơn | Chấp nhận/từ chối và lý do; chỉ gửi riêng người liên quan |
| P3 | Tổng hợp đầu ngày và cuối ngày | Từng nhân viên; quản lý theo phạm vi | 07:30 việc hôm nay, 16:30 việc còn mở/quá hạn, theo Asia/Ho_Chi_Minh |
| P3 | Tích hợp ngừng chạy | Admin vận hành | Token Pancake hết hạn, Zalo mất phiên, queue gửi lỗi; có kênh dự phòng ngoài Zalo khi chính Zalo lỗi |

**Ánh xạ vào hệ thống hiện tại**

- `dac.work.task`: `assigned_user_id`, `state`, `deadline`, `remind_at`,
  `priority`, `task_type`, `order_id`, các snapshot không chứa tiền. Đã có
  cơ chế nhắc OpenClaw và timestamps chống lặp; khi thêm Zalo cần tính cả
  chuyện trùng nhắc giữa các kênh, không bật hai cron gửi cùng một nội dung.
- `sale.order`: người phụ trách Sale/Design/Production, `production_group_ids`,
  các deadline, `production_done`, `fulfillment_method`, ngày giao và trạng
  thái workflow. Cần dùng chuyển trạng thái đã commit, không theo lần mở form
  hay thay `workflow_review_state` vốn chỉ là chế độ xem.
- Cọc/công nợ: đối chiếu trạng thái thanh toán thực tế, không chỉ dựa vào
  việc có nhập `deposit_amount`; tái sử dụng logic cọc và quyền tài chính
  hiện có. Cần xử lý cả hoàn tiền, hủy đơn và thay đổi sau đối soát.
- `dac.attendance.leave` và `dac.attendance.amendment`: các trạng thái
  `draft`, `pending`, `approved`, `refused`, `cancelled` (tùy model). Gửi thay đổi kết quả duyệt;
  chấm công GPS/camera vẫn thực hiện trong luồng hiện có, không tự chấm công
  chỉ vì nhân viên nhắn Zalo.

**Quy tắc nên thống nhất trước giai đoạn 2**

1. Mỗi nhân viên chọn loại thông báo, giờ yên lặng và bản tin tổng hợp. Mặc
   định yên lặng 19:00–07:00 giờ Việt Nam; tin khẩn ngoài giờ cần được chọn
   riêng. Không coi mọi thay đổi đơn hàng là khẩn.
2. Khóa chống trùng theo loại sự kiện + record + người nhận + phiên bản/mốc
   hạn. Kiểm tra trạng thái và quyền truy cập một lần nữa ngay trước khi gửi;
   hủy pending notification khi việc đã xong, đổi người hoặc nhân viên nghỉ.
3. Nhắc quá hạn ưu tiên gom; tối đa một nhắc chủ động/task/người/ngày, với
   ngưỡng escalation rõ cho quản lý thay vì ping lặp theo cron.
4. Mẫu thông báo dựa trên dữ liệu Odoo, không cần AI cho các sự kiện đơn giản.
   AI có thể tóm tắt bản tin sau, nhưng không tự quyết định người nhận/quyền.
5. Mặc định chỉ gửi mã đơn, tên việc và link Odoo cần đăng nhập; hạn chế số
   điện thoại, địa chỉ, tiền và dữ liệu nhân sự trong chat. Người nhận chỉ
   được nhận phạm vi thông tin theo vai trò của họ.
6. “Đã gửi” chưa có nghĩa “đã đọc/đã làm”. Nếu cần xác nhận hoặc hành động
   `/xong`, `/hoan`, phải thiết kế xác nhận, kiểm tra quyền và nhật ký riêng.

Đề xuất triển khai trước: **giao việc + nhắc hạn + bàn giao + bản tin cá nhân**.
Sau khi vận hành ổn, mở rộng sang tài chính, nhân sự và thao tác bằng chat.
