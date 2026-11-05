# CL-20261105-001 — Bản nền DAC ERP và quy trình bàn giao theo phiên bản

| Thông tin | Giá trị |
| --- | --- |
| Ngày | 2026-11-05 (Việt Nam) |
| Người yêu cầu | Duy An |
| Người triển khai Odoo | Khang |
| Trạng thái | Sẵn sàng bàn giao; chờ Duy An duyệt triển khai |
| Phiên bản mockup | [mockup-CL-20261105-001](https://github.com/marketingduyan/odoo-crm-duyan-addons/tree/mockup-CL-20261105-001/mockup/changelogs/CL-20261105-001/mockup) |
| Bản trước | Bản nền đầu tiên |

## Yêu cầu của Duy An

Duy An cần changelog có mã riêng cho mỗi yêu cầu hoặc phiên chỉnh mockup, kèm phiên bản mockup cố định để gửi Khang triển khai sang Odoo. Bản bàn giao đầu tiên ghi nhận mẫu nền và luồng đăng nhập/chọn vai trò đang có.

## Trước thay đổi

- Đã có mockup DAC ERP với trang đăng nhập, chọn bốn vai trò và các màn hình nghiệp vụ mẫu.
- Lịch sử đang ghi chung trong docs/CHANGELOG.md và docs/HANDOFF.md, chưa có mã riêng hoặc snapshot cho từng yêu cầu.
- Bản JSON từ nút Bàn giao chỉ có dữ liệu và ghi chú trong trình duyệt; link nhánh GitHub thay đổi theo các lần sửa tiếp.

## Mockup đã thay đổi

- Mỗi yêu cầu độc lập hoặc phiên chỉnh mới được cấp mã CL-YYYYMMDD-NNN theo ngày Việt Nam; có thư mục, nội dung trước/sau, tiêu chí nghiệm thu và file Odoo tham chiếu.
- Khi hoàn thành yêu cầu hoặc Duy An báo chỉnh xong, Codex chốt bản mockup, kiểm tra checksum, tạo ZIP và Git tag mockup-CL-... để gửi Khang.
- Lưu bản nền hiện tại trong snapshot: đăng nhập mẫu → chọn Quản lý, Sale, Thiết kế hoặc Sản xuất → dashboard của vai trò tương ứng; gồm danh sách/chi tiết đơn, task, lịch, hội thoại, chấm công và thanh toán.
- Changelog/snapshot đã chốt giữ nguyên khi tiếp tục chỉnh; phiên sau tham chiếu mã trước. Snapshot có vùng lưu dữ liệu trình duyệt riêng.
- Nút Bàn giao hiển thị mã bản bàn giao gần nhất. Tải JSON được đổi tên thành tải ghi chú, phân biệt với ZIP chứa toàn bộ bản đã chốt.
- Bổ sung quy trình vào AGENTS.md và skill dac-ui-mockup để tự áp dụng cho các phiên sau; CI kiểm tra snapshot không bị sửa.

## Màn hình và vai trò

- Màn hình: login, roles, manager, sales, design, production, orders, order, tasks, calendar, conversations, attendance, payments.
- Vai trò: manager, sale, design, production.

## Tiêu chí nghiệm thu để Khang triển khai

- [ ] Gói bàn giao có CHANGELOG.md, manifest.json, change.json và mockup chạy độc lập; các file có cùng mã CL-20261105-001.
- [ ] Khang giải nén, vào thư mục mockup, chạy npm run dev và xem được giao diện mà không cần database/Odoo hoặc npm install.
- [ ] Mở trang gốc thấy đăng nhập mẫu; chọn từng vai trò đưa về đúng dashboard; tải lại giữ vai trò, đăng xuất về trang đăng nhập.
- [ ] Bản đã chốt có phiên bản Git mockup-CL-20261105-001; các chỉnh sửa sau không làm đổi snapshot hoặc di chuyển tag đó.
- [ ] Dữ liệu thử trong bản đang chỉnh không xuất hiện trong snapshot; dữ liệu khách hàng, đăng nhập và token production không nằm trong gói.
- [ ] Mỗi yêu cầu chức năng bàn giao sau này mô tả trước/sau, vai trò, nghiệm thu, API/dữ liệu/quyền cần thực hiện; Khang ghi PR/commit/deploy vào IMPLEMENTATION.md riêng.

## Hướng triển khai Odoo

- Bản này thiết lập quy trình bàn giao và lưu nền hiện tại; chưa yêu cầu sửa addon hoặc deploy Odoo trong phiên này. Các đề xuất UI-001 đến UI-005 trong HANDOFF.md giữ nguyên trạng thái chờ duyệt/phạm vi đã ghi.
- Khang dùng snapshot/tag để đối chiếu, không dùng link nhánh đang tiếp tục thay đổi làm phiên bản đã duyệt.
- Khi triển khai tính năng thật, chuyển HTML/JS mẫu sang XML/OWL/CSS trong các file tham chiếu, kiểm tra model/field/API và xử lý quyền theo tài khoản Odoo.
- Chọn vai trò và tài khoản demo trong mockup chỉ phục vụ xem giao diện. Trên Odoo phải dùng xác thực và vai trò người dùng được cấp thực tế; không sao chép cơ chế demo vào production.
- Chỉ triển khai phần đã được Duy An xác nhận cần đưa sang Odoo; không xem toàn bộ mẫu nền là yêu cầu viết lại ERP.

File Odoo tham chiếu (xác minh trên nhánh triển khai):

- `addons/dac_erp/views/login_version_view.xml`
- `addons/Chameleon/views/Chameleon.xml`
- `addons/dac_report/static/src/xml/backend/manager_dashboard.xml`
- `addons/dac_report/static/src/css/backend/manager_dashboard.css`
- `addons/dac_report/static/src/xml/backend/sale_dashboard.xml`
- `addons/dac_report/static/src/css/backend/dac_report.css`
- `addons/dac_erp/static/src/xml/backend/design_dashboard.xml`
- `addons/dac_erp/static/src/css/backend/dac_task_dashboard.css`
- `addons/dac_erp/static/src/xml/backend/production_dashboard.xml`
- `addons/dac_erp/views/sale_order_view.xml`
- `addons/dac_erp/static/src/css/backend/sale_order_form.css`
- `addons/dac_erp/static/src/css/backend/sale_order_vip.css`
- `addons/dac_erp/static/src/css/backend/conversation_widget.css`
- `addons/dac_erp/views/dac_work_task_views.xml`
- `addons/CRM_DAC/views/page_fm_conversation_views.xml`
- `addons/CRM_DAC/static/src/scss/backend/pancake_dashboard.scss`
- `addons/dac_attendance/static/src/xml/employee_dashboard.xml`
- `addons/dac_attendance/static/src/xml/manager_dashboard.xml`
- `addons/dac_erp/views/account_payment_view.xml`

## Đã kiểm tra

- npm run test:changelog: đạt kiểm tra cấp mã kế tiếp, ngăn ghi đè bản đã chốt, phát hiện sửa/thêm file, giữ snapshot khi source mới đổi, ZIP đọc được bằng thư viện ZIP độc lập và Git tag không bị di chuyển.
- npm run check: đạt kiểm tra cú pháp, dữ liệu mẫu, liên kết task và 13 map màn hình/file nguồn.
- Skill dac-ui-mockup: kiểm tra hợp lệ bằng quick_validate.
- Chromium desktop: đăng nhập và chọn cả bốn vai trò mở đúng dashboard; hộp Bàn giao hiện nút tải ghi chú JSON; không có lỗi JavaScript.
- npm run build: đạt; bản nháp chưa chốt được loại khỏi artifact publish.

## Giới hạn và phần còn cần làm

- Đây là mockup với dữ liệu giả, chưa nối API thật và chưa thay thế ACL/record rules của Odoo.
- Dashboard Thiết kế/nhận diện đã đối chiếu production ngày 05/11; các màn hình còn lại dựa trên source và bằng chứng đã ghi trong baseline.json, chưa khẳng định giống từng pixel của mọi màn hình production.
- Snapshot không tự mang theo ghi chú/dữ liệu đã sửa trong localStorage; cần chuyển yêu cầu vào changelog trước khi chốt.
- Đã push mã GitHub nhưng chưa có website GitHub Pages online vì chưa cấu hình quyền hosting; snapshot chạy được local.

## Mở đúng phiên bản mockup

1. Dùng thư mục `mockup/` đi cùng changelog này hoặc tải đúng Git tag `mockup-CL-20261105-001` sau khi Codex đã push.
2. Trong thư mục snapshot `mockup/`, chạy `npm run dev`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng `PORT=4174 npm run dev`.
3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.
4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file `IMPLEMENTATION.md` riêng khi bắt đầu đưa vào Odoo.

Snapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.
