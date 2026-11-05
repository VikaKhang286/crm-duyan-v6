# CL-20261105-002 — Dashboard nhân viên Thiết kế và chi tiết task đơn giản

| Thông tin | Giá trị |
| --- | --- |
| Ngày | 2026-11-05 (Việt Nam) |
| Người yêu cầu | Duy An |
| Người triển khai Odoo | Khang |
| Trạng thái | Sẵn sàng bàn giao; chờ Duy An duyệt triển khai |
| Phiên bản mockup | [mockup-CL-20261105-002](https://github.com/marketingduyan/odoo-crm-duyan-addons/tree/mockup-CL-20261105-002/mockup/changelogs/CL-20261105-002/mockup) |
| Bản trước | CL-20261105-001 |

## Yêu cầu của Duy An

Làm dashboard nhân viên Thiết kế rõ ràng hơn; bấm task hiện mô tả chi tiết, thông tin đơn hàng bên dưới và tên khách hàng; không hiển thị giá tiền.

## Trước thay đổi

- Dashboard Thiết kế có danh sách đơn ưu tiên và biểu đồ trạng thái; chưa trình bày task và khách hàng thành từng dòng rõ ràng.
- Bấm task từ Việc của tôi hoặc lịch mở trang đơn tổng quát, có khối tiền, thao tác sửa và in báo giá.

## Mockup đã thay đổi

- Dashboard Công việc thiết kế dùng danh sách task toàn chiều ngang: tên việc, mã đơn, sản phẩm, khách hàng, trạng thái và hạn; ưu tiên hạn gần nhất, nhãn đỏ cho task quá hạn chưa hoàn thành.
- Bốn KPI lọc Cần làm / Quá hạn / Chờ duyệt / Hoàn thành; bộ lọc trạng thái và ô tìm theo tên task, mã đơn, khách hàng hoặc sản phẩm.
- Trang #task/T1 hiển thị tên task, hạn và trạng thái; tiếp theo là mô tả chi tiết đơn giữ xuống dòng, rồi thông tin đơn gồm mã đơn, tên khách hàng, Sale phụ trách, hạn đơn và chất liệu/kích thước/số lượng sản phẩm.
- Nhân viên Thiết kế mở cùng trang chi tiết từ dashboard, Việc của tôi và lịch. Danh sách/chi tiết đơn của vai trò này ẩn tổng tiền, khối thanh toán, tạo/sửa đơn và in báo giá.
- Bố cục mobile xếp từng task thành thẻ, mô tả và thông tin đơn thành một cột; giữ sidebar DAC tối, điểm nhấn vàng và luồng đăng nhập/chọn vai trò.
- Bổ sung mô tả giả nhiều đoạn cho DEMO-001 và DEMO-002 để duyệt nội dung; giữ bản chốt CL-20261105-001.

## Màn hình và vai trò

- Màn hình: design, task, tasks, calendar, orders, order.
- Vai trò: design.

## Tiêu chí nghiệm thu để Khang triển khai

- [ ] Đăng nhập → chọn Thiết kế → thấy danh sách task được giao; mỗi task hiện tên việc, mã đơn, tên khách hàng, trạng thái và hạn.
- [ ] KPI và bộ lọc trả đúng tập task; tìm theo khách hàng/mã đơn/tên việc; không có dữ liệu hoặc không có kết quả hiển thị lời nhắc rõ ràng.
- [ ] Bấm task trên dashboard, Việc của tôi hoặc lịch mở đúng task và đúng đơn; task không tồn tại không tự mở đơn khác.
- [ ] Chi tiết task: mô tả chi tiết của đơn ở trên; thông tin đơn, tên khách hàng, sản phẩm, chất liệu, kích thước và số lượng ở dưới; mô tả giữ xuống dòng.
- [ ] Đơn chưa có mô tả hiển thị lời nhắc liên hệ Sale; không giả tạo nội dung mô tả.
- [ ] Không hiện giá tiền, tạm tính, vận chuyển, công nợ hoặc nút in báo giá trong luồng Thiết kế nêu trên; danh sách đơn không có cột Tổng tiền.
- [ ] Đổi trạng thái task cập nhật số lượng/bộ lọc và giữ sau tải lại; không tự đổi trạng thái đơn.
- [ ] 1440×1000 và 390×844 không tràn ngang ở dashboard/chi tiết task; menu mobile mở/đóng được; Sale vẫn xem tiền trên trang đơn.

## Hướng triển khai Odoo

- Sửa OWL/XML/CSS Dashboard Thiết kế trong addons/dac_erp/static/src/xml/backend/design_dashboard.xml và addons/dac_erp/static/src/css/backend/dac_task_dashboard.css; tham chiếu nguồn của task/order trong src/data.js.
- Khang nối task thật dac.work.task với order_id, partner_id, assigned_user_id, deadline, state và mô tả đơn; lọc assigned_user_id theo nhân viên đăng nhập, kiểm tra ACL/record rules thực tế.
- Chỉ dùng các trường phi tài chính trong view dành cho Thiết kế. Việc ẩn UI mockup không phải bảo vệ dữ liệu tài chính qua RPC/export; kiểm tra quyền trường/xuất và các đường truy cập khác khi triển khai Odoo.
- Các trạng thái Chưa thực hiện / Đang thực hiện / Chờ duyệt / Hoàn thành là fixture mockup; xác minh mapping và quy trình duyệt trong Odoo trước khi nối thao tác ghi.
- Bản mới dùng thiết kế đề xuất theo yêu cầu Duy An; giữ các mục UI-001 đến UI-005 và snapshot CL-20261105-001. Chỉ thay mockup, không sửa addon hoặc deploy.

File Odoo tham chiếu (xác minh trên nhánh triển khai):

- `addons/dac_erp/static/src/xml/backend/design_dashboard.xml`
- `addons/dac_erp/static/src/css/backend/dac_task_dashboard.css`
- `addons/dac_erp/views/dac_work_task_views.xml`
- `addons/dac_erp/views/sale_order_view.xml`
- `addons/dac_erp/static/src/css/backend/sale_order_form.css`
- `addons/dac_erp/static/src/css/backend/sale_order_vip.css`
- `addons/dac_erp/static/src/css/backend/conversation_widget.css`

## Đã kiểm tra

- baseline:check trước sửa khớp 23 source Odoo UI tại checkout; đã đọc HANDOFF, changelog trước và xem ảnh production test-results/role-permissions/design-dashboard.png (04/11).
- Playwright Chromium desktop 1440×1000: đăng nhập/chọn vai trò, KPI, lọc, tìm, kết quả rỗng, mở đúng task, thứ tự mô tả rồi thông tin đơn, tên khách hàng, đổi trạng thái và tải lại, hoàn thành và cập nhật danh sách.
- Playwright kiểm tra entry point Việc của tôi và lịch, luồng danh sách/chi tiết đơn Thiết kế không có khối tiền; kiểm tra Sale vẫn thấy Tổng tiền/Còn thanh toán và dashboard Sản xuất còn hoạt động.
- Playwright kiểm tra task không tồn tại, mô tả trống, dashboard trống, menu mobile; không ghi nhận lỗi JavaScript.
- Đã xem ảnh desktop 1440×1000 và mobile cảm ứng 390×844, kiểm tra không tràn ngang; ảnh local ở test-results/mockup-design-20261105/.
- npm --prefix mockup run check và npm --prefix mockup run build; baseline:update ghi fingerprint sau đối chiếu, giữ nguồn production cũ và đánh dấu đây là đề xuất UI mới.

## Giới hạn và phần còn cần làm

- Dữ liệu và đăng nhập đều giả; đổi trạng thái chỉ lưu localStorage. Danh sách thiết kế demo dùng chung nhân viên Thiết kế mẫu, chưa có API hoặc kiểm tra quyền thật.
- Không truy cập lại production trong phiên; đối chiếu ảnh cũ 04/11 và source checkout. Kiểm tra mobile trên Chromium giả lập, chưa thử Safari/iOS hoặc điện thoại thật.
- Demo có một sản phẩm mỗi đơn; khi triển khai cần hiển thị tất cả dòng sản phẩm phù hợp nghiệp vụ và quyền nhân viên.
- Phạm vi ẩn tiền là dashboard/task và danh sách/chi tiết đơn Thiết kế. Các màn hình khác và công cụ duyệt vai trò vẫn giữ để review, không phải hệ thống phân quyền production.
- Đã đóng gói local để duyệt, chưa được Duy An duyệt triển khai Odoo, chưa commit/push hoặc có Git tag trên GitHub.

## Mở đúng phiên bản mockup

1. Dùng thư mục `mockup/` đi cùng changelog này hoặc tải đúng Git tag `mockup-CL-20261105-002` sau khi Codex đã push.
2. Trong thư mục snapshot `mockup/`, chạy `npm run dev`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng `PORT=4174 npm run dev`.
3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.
4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file `IMPLEMENTATION.md` riêng khi bắt đầu đưa vào Odoo.

Snapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.
