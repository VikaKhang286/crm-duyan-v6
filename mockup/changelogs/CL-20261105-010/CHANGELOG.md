# CL-20261105-010 — Thời gian thông báo cùng dòng nội dung

| Thông tin | Giá trị |
| --- | --- |
| Ngày | 2026-11-05 (Việt Nam) |
| Người yêu cầu | Duy An |
| Người triển khai Odoo | Khang |
| Trạng thái | Sẵn sàng bàn giao; chờ Duy An duyệt triển khai |
| Phiên bản mockup | [mockup-CL-20261105-010](https://github.com/marketingduyan/odoo-crm-duyan-addons/tree/mockup-CL-20261105-010/mockup/changelogs/CL-20261105-010/mockup) |
| Bản trước | CL-20261105-009 |

## Yêu cầu của Duy An

Đưa thời gian thông báo nằm cùng dòng với text để tiết kiệm diện tích, áp dụng cho cột Thông báo và bảng chuông; giữ giới hạn tối đa 3 dòng.

## Trước thay đổi

- Thời gian thông báo nằm trên một hàng riêng dưới nội dung, làm mỗi thẻ cao hơn.

## Mockup đã thay đổi

- Đưa thời gian vào cùng dòng ngay sau nội dung, ngăn bằng dấu chấm giữa: … · 5 phút.
- Cắt ngắn snippet có tính cả chiều rộng thời gian để toàn bộ văn bản và thời gian tối đa 3 dòng; giữ thời gian nhìn thấy và nằm cùng dòng với phần cuối tin nhắn.
- Áp dụng chung cột Thông báo và bảng chuông qua notifications.js; giữ các thao tác đọc/chưa đọc và mở task.

## Màn hình và vai trò

- Màn hình: design, task, orders, order, calendar, attendance.
- Vai trò: design.

## Tiêu chí nghiệm thu để Khang triển khai

- [ ] Thời gian hiển thị inline sau nội dung, không còn hàng riêng, có dấu · phân cách.
- [ ] Nội dung dài cắt snippet bằng ... và vẫn hiện thời gian, toàn bộ văn bản kèm thời gian không quá 3 dòng.
- [ ] Cột Thông báo và bảng chuông áp dụng cùng kiểu trên 1440×1000 và 390×844; không tràn trang.
- [ ] Tên người/task in đậm, task ≤20 ký tự, avatar/icon giữ nguyên; click thông báo mở đúng task và đánh dấu đã đọc.

## Hướng triển khai Odoo

- Khang đặt time inline trong vùng giới hạn 3 dòng của component thông báo; thuật toán cắt snippet phải tính cả thời gian, không chỉ phần tin nhắn.
- Giữ phần thời gian không bị tách dòng khỏi ký tự cuối của snippet bằng khoảng trắng không ngắt; không làm thay đổi timestamp/sự kiện hoặc trạng thái đọc.
- Chỉ đổi mockup, chưa sửa addon hoặc deploy Odoo.

File Odoo tham chiếu (xác minh trên nhánh triển khai):

- `addons/dac_erp/static/src/xml/backend/design_dashboard.xml`
- `addons/dac_erp/static/src/css/backend/dac_task_dashboard.css`
- `addons/home_menu/static/src/xml/backend/navbar.xml`
- `addons/home_menu/static/src/css/backend/home_menu.css`
- `addons/dac_erp/views/dac_work_task_views.xml`
- `addons/dac_erp/views/sale_order_view.xml`
- `addons/dac_erp/static/src/css/backend/sale_order_form.css`
- `addons/dac_erp/static/src/css/backend/sale_order_vip.css`
- `addons/dac_erp/static/src/css/backend/conversation_widget.css`
- `addons/dac_attendance/static/src/xml/employee_dashboard.xml`
- `addons/dac_attendance/static/src/xml/manager_dashboard.xml`

## Đã kiểm tra

- baseline:check khớp 23 file UI Odoo; giữ nguồn đối chiếu production cũ.
- Playwright Chromium kiểm tra time inline cùng dòng với ký tự cuối snippet, luôn nhìn thấy trong vùng ≤3 dòng, trên cột và bảng chuông ở desktop/mobile.
- Kiểm tra không tràn trang và click thông báo mở task/chat, trạng thái đã đọc vẫn đúng. Không ghi nhận lỗi JavaScript.
- Đã xem ảnh desktop 1440×1000 và mobile 390×844 tại test-results/mockup-inline-time-20261105.
- npm run check, baseline:update, build; verify bản trước và bản mới; đóng gói snapshot/ZIP.

## Giới hạn và phần còn cần làm

- Mockup local và các giới hạn dữ liệu/thông báo của CL-20261105-008 được giữ; chưa có API/realtime production.
- Mới kiểm tra Chromium giả lập, chưa thử Safari/iOS hoặc điện thoại thật; không truy cập live production trong phiên.
- Bản và ZIP local, chưa commit/push, chưa có Git tag GitHub hoặc deploy Odoo.

## Mở đúng phiên bản mockup

1. Dùng thư mục `mockup/` đi cùng changelog này hoặc tải đúng Git tag `mockup-CL-20261105-010` sau khi Codex đã push.
2. Trong thư mục snapshot `mockup/`, chạy `npm run dev`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng `PORT=4174 npm run dev`.
3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.
4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file `IMPLEMENTATION.md` riêng khi bắt đầu đưa vào Odoo.

Snapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.
