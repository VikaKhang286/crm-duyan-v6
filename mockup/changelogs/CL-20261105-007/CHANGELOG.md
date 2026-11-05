# CL-20261105-007 — Tên task Thiết kế theo khách hàng và ngày tạo

| Thông tin | Giá trị |
| --- | --- |
| Ngày | 2026-11-05 (Việt Nam) |
| Người yêu cầu | Duy An |
| Người triển khai Odoo | Khang |
| Trạng thái | Sẵn sàng bàn giao; chờ Duy An duyệt triển khai |
| Phiên bản mockup | [mockup-CL-20261105-007](https://github.com/marketingduyan/odoo-crm-duyan-addons/tree/mockup-CL-20261105-007/mockup/changelogs/CL-20261105-007/mockup) |
| Bản trước | CL-20261105-006 |

## Yêu cầu của Duy An

Tên task thiết kế theo mẫu Thiết kế + Tên khách + DD/MM; hiển thị thống nhất ở Tổng quan, chi tiết task, lịch và đơn hàng. Phần ngày tạm dùng ngày tạo task.

## Trước thay đổi

- Task Thiết kế dùng các tên mô tả khác nhau như Thiết kế bản vẽ sản xuất, Duyệt bản thiết kế bảng hiệu; không có quy tắc tên kèm khách hàng/ngày.

## Mockup đã thay đổi

- Đổi tên task thiết kế theo mẫu Thiết kế + Tên khách + DD/MM, ví dụ Thiết kế Khách hàng mẫu 01 05/11. Phần ngày tạm dùng ngày tạo task.
- Thêm createdDate cho fixture thiết kế; tên được hiển thị và tìm kiếm thống nhất trên Tổng quan, mục cần chỉnh sửa, chi tiết task, Việc của tôi, lịch và đơn của các vai trò liên quan.
- Dữ liệu cũ trong trình duyệt vẫn được hiển thị theo quy tắc mới mà không xóa lịch sử, trạng thái, ảnh hoặc file. Ngày tạo giữ nguyên khi mở sửa tiếp; tên task Sản xuất không thay đổi.
- Ghi chú JSON xuất task dùng tên đã định dạng để đồng nhất với giao diện.

## Màn hình và vai trò

- Màn hình: design, task, tasks, calendar, order.
- Vai trò: design, sale, manager, production.

## Tiêu chí nghiệm thu để Khang triển khai

- [ ] Task Thiết kế hiển thị đúng mẫu Thiết kế + Tên khách + DD/MM, ví dụ Thiết kế Khách hàng mẫu 01 05/11.
- [ ] Tên thống nhất trên Tổng quan, chi tiết, lịch, trang đơn và mục cần chỉnh sửa; tìm theo cả tên mới trả đúng task.
- [ ] Dữ liệu trình duyệt từ bản trước vẫn hiển thị tên mới sau tải lại, không mất trạng thái/chat/file.
- [ ] Mở sửa task đã duyệt không đổi ngày tạo trong tên; task Sản xuất giữ tên hiện có.
- [ ] 390×844 không tràn trang; giữ sidebar và luồng thiết kế của CL-20261105-006.

## Hướng triển khai Odoo

- Khang đặt tên task Thiết kế theo tên khách và ngày tạo task theo giờ Việt Nam, định dạng DD/MM; áp dụng thống nhất cho form, kanban, calendar và dashboard.
- DD/MM là ngày tạo task đang được giả định trong bản mockup, Duy An có thể điều chỉnh sang ngày khác. Không dùng deadline hoặc ngày mở sửa lại làm ngày tạo.
- Dữ liệu demo cũ thiếu createdDate dùng createdDate từ fixture khớp id/order, rồi ngày đơn làm fallback. Khi triển khai phải dùng ngày tạo thật và đối chiếu dữ liệu cũ, không lấy ngày fixture.
- Task không phải Thiết kế không đổi tên; giữ id, phân công, trạng thái duyệt, lịch sử và attachment khi áp dụng quy tắc tên.

File Odoo tham chiếu (xác minh trên nhánh triển khai):

- `addons/dac_erp/static/src/xml/backend/design_dashboard.xml`
- `addons/dac_erp/static/src/css/backend/dac_task_dashboard.css`
- `addons/home_menu/static/src/xml/backend/navbar.xml`
- `addons/home_menu/static/src/css/backend/home_menu.css`
- `addons/dac_erp/views/dac_work_task_views.xml`
- `addons/dac_erp/views/sale_order_view.xml`
- `addons/dac_erp/static/src/css/backend/sale_order_form.css`
- `addons/dac_erp/static/src/css/backend/conversation_widget.css`

## Đã kiểm tra

- baseline:check khớp 23 file Odoo UI tại checkout; giữ nguồn đối chiếu production cũ, không đăng nhập lại production.
- Playwright kiểm tra tên mới và tìm kiếm, mở chi tiết/reload, lịch, đơn của Thiết kế/Sale, mục cần chỉnh sửa sau mở lại task đã duyệt; dữ liệu cũ trong localStorage giữ nguyên và task Sản xuất không đổi tên.
- Kiểm tra desktop 1440×1000, mobile 390×844 không tràn ngang; đã xem ảnh tại test-results/mockup-design-task-name-20261105. Không ghi nhận lỗi JavaScript.
- npm run check, baseline:update, build; kiểm tra snapshot bản trước và bản mới; đóng gói ZIP.

## Giới hạn và phần còn cần làm

- Ngày trong tên đang dùng ngày tạo task theo giả định đã nêu, người dùng mới chỉ quy định định dạng DD/MM.
- Tên trong dữ liệu trình duyệt cũ không bị ghi đè để giữ bản nháp; UI và bản xuất ghi chú áp dụng quy tắc tên mới.
- Chỉ thay mockup; chưa sửa addon, dữ liệu Odoo hoặc deploy. Phiên bản/ZIP local, chưa commit/push hoặc tạo tag GitHub.

## Mở đúng phiên bản mockup

1. Dùng thư mục `mockup/` đi cùng changelog này hoặc tải đúng Git tag `mockup-CL-20261105-007` sau khi Codex đã push.
2. Trong thư mục snapshot `mockup/`, chạy `npm run dev`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng `PORT=4174 npm run dev`.
3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.
4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file `IMPLEMENTATION.md` riêng khi bắt đầu đưa vào Odoo.

Snapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.
