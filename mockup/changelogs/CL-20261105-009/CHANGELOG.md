# CL-20261105-009 — Thu gọn thẻ Cần làm và gom nhãn trạng thái

| Thông tin | Giá trị |
| --- | --- |
| Ngày | 2026-11-05 (Việt Nam) |
| Người yêu cầu | Duy An |
| Người triển khai Odoo | Khang |
| Trạng thái | Sẵn sàng bàn giao; chờ Duy An duyệt triển khai |
| Phiên bản mockup | [mockup-CL-20261105-009](https://github.com/marketingduyan/odoo-crm-duyan-addons/tree/mockup-CL-20261105-009/mockup/changelogs/CL-20261105-009/mockup) |
| Bản trước | CL-20261105-008 |

## Yêu cầu của Duy An

Các thẻ công việc Cần làm cần gọn hơn; gom các nhãn chờ duyệt/quá hạn thành một nhãn trên thẻ, ngày hạn cùng hàng. Giữ cột Thông báo và thao tác hiện có.

## Trước thay đổi

- Thẻ Cần làm cao, lặp tên khách đã có trong tên task, có icon, dòng trạng thái riêng và nhãn quá hạn riêng cạnh ngày hạn.
- Một task có thể xuất hiện cả Chờ duyệt/Cần chỉnh sửa và Quá hạn bằng nhiều nhãn gây rối.

## Mockup đã thay đổi

- Thu gọn thẻ thành tên task, mã đơn/sản phẩm và một hàng gồm nhãn trạng thái với ngày hạn DD/MM. Bỏ dòng khách hàng lặp, icon trang trí và đường phân cách không cần thiết.
- Mỗi thẻ có đúng một nhãn. Ưu tiên Quá hạn cho task chưa xong; tiếp theo là Cần chỉnh sửa, Chờ duyệt, Thiết kế xong, Đến hạn hôm nay hoặc Cần làm/Đang làm.
- Trạng thái gốc vẫn có trong tooltip/nhãn truy cập và chi tiết task; bộ lọc vẫn dùng dữ liệu thật, không bị gộp theo nhãn hiển thị.
- Giữ bố cục Cần làm/Thông báo, nút chuông và các thao tác hiện có.

## Màn hình và vai trò

- Màn hình: design.
- Vai trò: design.

## Tiêu chí nghiệm thu để Khang triển khai

- [ ] Mỗi thẻ trong danh sách Cần làm chỉ có một nhãn trạng thái/cảnh báo; ngày hạn nằm cùng hàng nhãn.
- [ ] Task vừa chờ duyệt/cần chỉnh sửa vừa quá hạn hiện một nhãn Quá hạn; tooltip/chi tiết vẫn thể hiện đúng trạng thái gốc.
- [ ] Task đã xong không bị gắn quá hạn. Task đang chờ Sale duyệt hiện nhãn Chờ duyệt nếu chưa quá hạn.
- [ ] Tên task và mã đơn/sản phẩm vẫn dễ đọc; không lặp tên khách; tên hoặc sản phẩm dài cắt gọn, có tooltip đầy đủ.
- [ ] Ô thống kê và bộ lọc chờ duyệt/quá hạn vẫn trả đúng nhóm; bấm thẻ mở đúng task, không đổi trạng thái khi chỉ hiển thị.
- [ ] Desktop 1440×1000/mobile 390×844: thẻ dưới 120px với dữ liệu mẫu, không tràn ngang; cột Thông báo và nút chuông hoạt động.

## Hướng triển khai Odoo

- Áp dụng layout compact trong design_dashboard.xml/dac_task_dashboard.css khi Khang triển khai Odoo.
- Tách nhãn hiển thị ưu tiên khỏi trạng thái nghiệp vụ: quá hạn không ghi đè trạng thái cần sửa/chờ duyệt, domain KPI vẫn dựa theo dữ liệu gốc.
- Hạn trên thẻ hiển thị DD/MM; năm và hạn đầy đủ vẫn có ở trang chi tiết. Giữ nhãn truy cập/tooltip để xem trạng thái gốc khi nhãn Quá hạn được ưu tiên.
- Không sửa addon/deploy trong yêu cầu mockup này; giữ các đề xuất đã chốt trước.

File Odoo tham chiếu (xác minh trên nhánh triển khai):

- `addons/dac_erp/static/src/xml/backend/design_dashboard.xml`
- `addons/dac_erp/static/src/css/backend/dac_task_dashboard.css`
- `addons/home_menu/static/src/xml/backend/navbar.xml`
- `addons/home_menu/static/src/css/backend/home_menu.css`

## Đã kiểm tra

- baseline:check khớp 23 source UI Odoo trước sửa; giữ nguồn tham chiếu production cũ.
- Playwright kiểm tra thẻ <120px, một badge mỗi thẻ, ưu tiên Quá hạn cho task chờ duyệt/cần sửa, trạng thái gốc không đổi và bộ lọc chờ duyệt vẫn trả đúng nhóm.
- Kiểm tra task đã xong không quá hạn, mở task thấy trạng thái gốc; mobile không tràn ngang, click thông báo từ chuông vẫn mở task/chat đúng. Không ghi nhận lỗi JavaScript.
- Đã xem ảnh desktop 1440×1000 và mobile 390×844 ở test-results/mockup-design-compact-20261105.
- npm run check, baseline:update, build; verify bản trước và bản mới; đóng gói snapshot/ZIP.

## Giới hạn và phần còn cần làm

- Chỉ thay bố cục thẻ và nhãn hiển thị; đây là mockup local, chưa thay view hoặc nghiệp vụ Odoo.
- Nhãn Quá hạn được ưu tiên nên trạng thái gốc có thể không hiện trực tiếp trên thẻ; xem tooltip hoặc mở chi tiết.
- Chưa thử Safari/iOS hoặc thiết bị thật; không đối chiếu live production trong phiên.
- Bản và ZIP local, chưa commit/push, chưa có Git tag GitHub hoặc deploy Odoo.

## Mở đúng phiên bản mockup

1. Dùng thư mục `mockup/` đi cùng changelog này hoặc tải đúng Git tag `mockup-CL-20261105-009` sau khi Codex đã push.
2. Trong thư mục snapshot `mockup/`, chạy `npm run dev`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng `PORT=4174 npm run dev`.
3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.
4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file `IMPLEMENTATION.md` riêng khi bắt đầu đưa vào Odoo.

Snapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.
