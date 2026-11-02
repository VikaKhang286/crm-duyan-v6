# Thông báo chủ động qua trợ lý DAC

Các nhóm thông báo trong đề xuất ban đầu đã được triển khai trong
`dac_zalo_assistant` phiên bản 18.0.2.6.0. Production đã cài addon và bridge
ngày 02/11/2026, đang chờ đăng nhập tài khoản trợ lý và liên kết nhân viên.
Xem [báo cáo triển khai](ZALO_PRODUCTION_2026-11-02.md).

**Bật/tắt và giờ nhận**

- Admin: **Trợ lý Zalo → Cấu hình → Thông báo nghiệp vụ**. Có công tắc chung
  và công tắc cho từng nhóm trên toàn hệ thống.
- Nhân viên: **Trợ lý Zalo → Thông báo của tôi**. Bật/tắt từng nhóm, tạm dừng
  đến một thời điểm, chọn múi giờ, giờ yên lặng và giờ nhận bản tin.
- Điều kiện nhận là cả admin và nhân viên đều cho phép. Tắt thông báo không
  tắt hội thoại, `/congviec` hoặc `/ai`.
- Mặc định dùng Asia/Ho_Chi_Minh, yên lặng 19:00–07:00, bản tin 07:30 và
  16:30. Các bản tin được lấy trong cửa sổ hai giờ sau giờ đã chọn; tin quá
  cũ sẽ hết hiệu lực. Nên chọn giờ bản tin ngoài giờ yên lặng.
- Tin khẩn chỉ được vượt giờ yên lặng nếu nhân viên bật tùy chọn đó. Công
  việc mức khẩn, đơn ưu tiên trong ngày và cảnh báo vận hành được đánh dấu
  khẩn; không vượt qua công tắc tắt thông báo hoặc thời gian tạm dừng.
- Các lệnh Zalo: `/tat-thongbao`, `/bat-thongbao`, `/tam-dung 60`,
  `/tiep-tuc`. Thời gian tạm dừng cho phép 1–10080 phút.

**Các nhóm đã thực hiện**

| Nhóm | Khi nào / người nhận |
|---|---|
| Tạo đơn thành công | Sau khi tạo đơn thành công, gửi Sale ở `user_id`: “Bạn đã tạo đơn … thành công.” Nếu người khác tạo hộ thì thông báo đơn được tạo và giao phụ trách, không ghi sai người tạo. |
| Trạng thái / phụ trách đơn | Chuyển tiến trình thật, hoàn tất/hủy/quay lại bước trước hoặc đổi Sale. Chỉ thay bước đang xem hay sửa ghi chú danh sách không phát thông báo workflow. |
| Được giao việc | Tạo task đã có người nhận, đổi người thực hiện, giao lead hoặc thêm thành viên sản xuất. Tránh lặp thông báo giao việc từ cả order và task tự sinh. |
| Tiến độ / trả lại việc | Đổi trạng thái task, bị chặn/bỏ chặn, đổi ưu tiên hoặc nội dung. Gửi người thực hiện và Sale liên quan. |
| Đổi hạn / yêu cầu | Đổi deadline, lịch nhắc, yêu cầu, bản vẽ, ưu tiên, lịch giao, phương thức bàn giao hoặc thông tin trễ sản xuất. |
| Sắp đến hạn | Nhắc người thực hiện trong hai giờ trước deadline của task; một lần/mốc hạn. Đổi hạn sẽ bỏ cảnh báo cũ. |
| Quá hạn | Tối đa một nhắc/task/người/ngày; sau 24 giờ báo thêm quản lý và Sale của đơn. Không cộng dồn hai ngày rồi gửi dồn sau giờ yên lặng. |
| Nhắc việc cá nhân | Đến `remind_at`, gửi chủ nhân. Dùng `/tam-dung` để hoãn nhận; chưa thay đổi deadline hoặc tạo thao tác `/xong` bằng chat. |
| Bàn giao | Đơn vào sản xuất; thiết kế/sản xuất hoàn tất hoặc bị yêu cầu kiểm tra lại. Gửi Sale và bộ phận kế tiếp được giao. |
| Giao hàng / lắp đặt | Khi chuyển vào bước bàn giao và nhắc lịch từ hôm qua đến ngày mai, nếu chưa xác nhận. Đơn chưa đặt lịch nhận tin chuyển bước, không bị nhắc hằng ngày vô hạn. |
| Thu cọc / công nợ | Một bản tổng hợp mỗi ngày trong giờ làm việc cho Sale phụ trách và quản lý có quyền. Kiểm tra các hóa đơn thật đã paid, gồm cả đơn hoàn thành còn phải thu; không giới hạn ở 200 đơn mới nhất. Không gửi cho người chỉ có vai trò Design/Production. |
| Trao đổi nội bộ | Người được nhắc và người phụ trách khi có trao đổi ở panel nội bộ; loại người viết. Không phát lại mọi log tự sinh trong chatter. |
| Nhân sự cần duyệt | Đơn nghỉ phép/điều chỉnh chấm công đang pending, gửi quản lý chấm công có quyền xem. |
| Kết quả duyệt nhân sự | Approved/refused/cancelled gửi riêng người làm đơn; gồm khoảng ngày, không chép lý do riêng tư vào Zalo. |
| Bản tin đầu/cuối ngày | Công việc đang mở theo quyền người nhận, hiển thị tối đa 10 tên và tổng số. Quản lý xem phạm vi họ được phép xem. |
| Cảnh báo vận hành | Token Pancake lỗi/hết hạn, Zalo mất phiên/sai tài khoản, tin gửi lỗi/chưa xác nhận, scanner lỗi. Gửi admin và tạo hoạt động cần làm trong Odoo làm kênh dự phòng. Khép hoạt động khi sự cố được xác nhận đã hết. |

Nhân sự chỉ được quét khi `dac_attendance` đã cài; không bắt buộc addon này
cho các tính năng đơn hàng và công việc. Trạng thái nhân sự được phát hiện
bằng quét mỗi phút, không sửa phương thức duyệt hay tự chấm công.

**Cơ chế gửi**

Sự kiện tạo/ghi order và task chỉ ghi outbox trong cùng transaction, không
chờ Zalo/AI khi người dùng lưu đơn. Lỗi outbox không đổi kết quả nghiệp vụ.
Không thêm commit vào phương thức nghiệp vụ. Thông báo lưu từ transaction
bị rollback cũng bị rollback.

Cron quét lịch theo trang để tránh đọc toàn bộ task/nhân viên mỗi phút.
Nhắc quá hạn cá nhân giới hạn cửa sổ 48 giờ và mốc bắt đầu bật tính năng để
tránh bắn lại lịch sử; bản tin công việc vẫn gồm task cũ còn mở. Công nợ được
gom theo người và truy vấn hóa đơn đã trả theo lô, không cần AI.

Trước lúc thực gửi, kiểm tra lại liên kết Zalo, tài khoản hoạt động, quyền đọc
bản ghi, người phụ trách, trạng thái, mốc hạn, công tắc và giờ nhận. Tin không
còn đúng được hủy. Nội dung được dựng lại theo dữ liệu hiện tại. Request ID
của thông báo dựa trên khóa sự kiện bền vững, kể cả khi worker rollback và
phải tạo lại dòng transport, để bridge không tự gửi hai lần.

**Theo dõi và giới hạn**

- **Nhật ký thông báo**: pending/queued/sent/cancelled/failed/uncertain.
  Nhân viên chỉ xem phần của mình; admin xem toàn bộ.
- “Đã gửi” chỉ là Zalo xác nhận API, không phải đã đọc hoặc đã làm việc.
  Tin chưa xác nhận gửi không được tự phát lại như một sự kiện mới.
- Giờ gửi phụ thuộc chu kỳ cron và khả năng kết nối. Khi lượng dữ liệu lớn,
  một vòng quét có thể cần nhiều lượt cron.
- Công việc vẫn dùng deadline hiện có của task; thông báo không tự sửa
  deadline, thay quy tắc thanh toán, duyệt nhân sự hoặc cấu hình OpenClaw.
  Nếu đang dùng nhiều kênh nhắc việc, chọn bật/tắt Zalo phù hợp.
- Các sự kiện thay đổi từ thời điểm nâng cấp được theo dõi; không phát lại
  hàng loạt xác nhận tạo đơn hoặc kết quả duyệt nhân sự cũ.

Xem [ZALO_ASSISTANT.md](ZALO_ASSISTANT.md) để kết nối và
[ZALO_VALIDATION.md](ZALO_VALIDATION.md) để xem kiểm thử và giới hạn xác minh.
