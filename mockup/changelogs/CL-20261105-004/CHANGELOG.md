# CL-20261105-004 — Thiết kế hai cột, chat preview và file cho Sản xuất

| Thông tin | Giá trị |
| --- | --- |
| Ngày | 2026-11-05 (Việt Nam) |
| Người yêu cầu | Duy An |
| Người triển khai Odoo | Khang |
| Trạng thái | Sẵn sàng bàn giao; chờ Duy An duyệt triển khai |
| Phiên bản mockup | [mockup-CL-20261105-004](https://github.com/marketingduyan/odoo-crm-duyan-addons/tree/mockup-CL-20261105-004/mockup/changelogs/CL-20261105-004/mockup) |
| Bản trước | CL-20261105-003 |

## Yêu cầu của Duy An

Sắp xếp lại giao diện Thiết kế trước: task hai cột, khung chat đơn giản với icon đính kèm, Ctrl/Cmd+V dán ảnh preview, ảnh có nút copy cho Sale; quản lý sản xuất xem và tải file thiết kế sau bàn giao. Sản xuất áp dụng cách tương tự ở bước sau.

## Trước thay đổi

- Dashboard Thiết kế hiển thị task thành hàng ngang toàn chiều rộng; chi tiết xếp mô tả, khu duyệt/bình luận và thông tin đơn thành một cột dài.
- Preview dùng form bình luận và trường chọn file riêng lớn; chưa dán ảnh từ clipboard hoặc copy ảnh cho Sale. File thiết kế chỉ hiện trong task, chưa có mục nhận file trên dashboard/trang đơn Sản xuất.

## Mockup đã thay đổi

- Dashboard chia thẻ task thành hai cột trên máy tính, một cột trên điện thoại; giữ KPI, bộ lọc/tìm kiếm và mục Thiết kế cần chỉnh sửa.
- Chi tiết task dùng hai cột: mô tả và file thiết kế bên trái, chat bên phải; thông tin đơn, khách hàng và bảng các dòng sản phẩm ở dưới; tiếp tục ẩn giá tiền.
- Khung chat gọn thay form cũ và bỏ các bước duyệt lặp lại. Icon kẹp giấy mở chọn ảnh; Ctrl/Cmd+V dán ảnh; ảnh nằm trong bản nháp có nút bỏ trước khi gửi. Enter gửi, Shift+Enter xuống dòng.
- Mỗi ảnh có nút Copy ảnh, chuyển ảnh thành PNG và ghi vào clipboard để Sale dán gửi khách hàng; nếu clipboard bị chặn hoặc chưa hỗ trợ thì báo rõ và giữ khả năng mở ảnh để sao chép thủ công.
- Giữ luồng Hoàn thành task, Sale góp ý làm task Cần chỉnh sửa, gửi preview mới, duyệt rồi bàn giao file. Tin nhắn/ảnh đã gửi vẫn lưu sau reload; bản nháp tách theo vai trò và task trong phiên, không lẫn nội dung khi đổi task.
- File thiết kế được duyệt có nút xem và tải bản gốc. Dashboard và trang đơn Sản xuất có File thiết kế đã bàn giao; Sản xuất có thể đọc task và tải file, không có thao tác gửi hoặc duyệt thiết kế.
- Chỉ file thuộc preview đang được duyệt xuất hiện ở mục nhận file Sản xuất. Góp ý mới thu hồi duyệt thì file cũ không còn hiện là bản hiện hành; Thiết kế vẫn thấy lịch sử phiên bản trước.
- Thêm file vector decal giả cho DEMO-006 trong bộ dữ liệu mới để thử luồng nhận file. Không thay snapshot CL-20261105-003 hoặc dữ liệu người dùng đã lưu.

## Màn hình và vai trò

- Màn hình: design, task, tasks, order, production, calendar.
- Vai trò: design, sale, manager, production.

## Tiêu chí nghiệm thu để Khang triển khai

- [ ] 1440×1000: danh sách task có hai thẻ trên cùng hàng; chi tiết có cột mô tả/file bên trái, khung chat bên phải; thông tin đơn và sản phẩm ở dưới. 390×844: xếp một cột, không tràn ngang ngoài khung bảng.
- [ ] Khung chat chỉ có icon đính kèm, ô nhập và icon gửi; chọn file hoặc Ctrl/Cmd+V thêm ảnh vào bản nháp, không tự gửi; bỏ ảnh không mất chữ đang gõ.
- [ ] Enter gửi tin nhắn/ảnh, Shift+Enter xuống dòng. Dán chữ thông thường hoạt động; bản nháp không lẫn khi đổi task hoặc vai trò; chỉ tin đã gửi được lưu qua tải lại.
- [ ] Bấm Copy ảnh ghi ảnh thật vào clipboard ở định dạng PNG, không chỉ ghi URL. Sale có thể dán lại ảnh; lỗi quyền clipboard hiển thị thông báo thay vì báo copy thành công.
- [ ] Gửi rỗng hoặc ảnh sai loại/trên 1 MB không tạo bình luận. Nếu localStorage không lưu được, không báo gửi thành công, giữ bản nháp và không đổi trạng thái task.
- [ ] Hoàn thành vẫn chuyển task sang Hoàn thành; góp ý từ Sale chuyển sang Cần chỉnh sửa, hiện trên dashboard; sau gửi preview mới và duyệt thì mở gửi file chính thức.
- [ ] Chọn vai trò Sản xuất trong mockup: thấy file đã bàn giao trên dashboard và đúng trang đơn; xem được ảnh/PDF hỗ trợ trong trình duyệt, tải đúng tên và nội dung gốc; không có nút duyệt, gửi chat hoặc gửi file.
- [ ] Góp ý tiếp sau duyệt làm file cũ mất khỏi danh sách file hiện hành của Sản xuất. Ảnh và file chưa được duyệt không được liệt kê là file đã bàn giao.
- [ ] Giữ luồng đăng nhập/chọn vai trò, các đề xuất đang có và các snapshot đã chốt. Không thay đổi bố cục task/chat Sản xuất trong phạm vi ưu tiên Thiết kế lần này.

## Hướng triển khai Odoo

- Khang chuyển bố cục/chức năng sang design_dashboard.xml, dac_task_dashboard.css và view task/order trong addon phù hợp; tham chiếu source map ở src/data.js. Không chép nguyên localStorage/schema demo vào production.
- Preview và file thiết kế phải lưu bằng attachment server, gắn task/order và phiên bản đã duyệt. Quản lý sản xuất được đọc/tải file của đơn được phân công; xác minh đúng nhóm quản lý sản xuất, ACL/record rules và quyền attachment/RPC/download.
- Vai trò Sản xuất trong mockup đại diện góc nhìn nhận file của quản lý sản xuất; việc hiển thị mẫu không có nghĩa là cấp quyền cho toàn bộ nhân viên sản xuất hoặc xem mọi đơn.
- Clipboard ảnh dùng ClipboardItem image/png; thao tác người dùng kích hoạt copy, paste đọc ảnh từ ClipboardEvent. Triển khai trên HTTPS, giữ fallback khi trình duyệt từ chối; không gửi ảnh ra ứng dụng khác tự động.
- Tách trạng thái hoàn thành task và duyệt preview; góp ý tạo vòng sửa mới, chỉ bản hiện hành đã duyệt mới được bàn giao cho sản xuất. Không tự chuyển trạng thái đơn hoặc chạy sản xuất khi gửi/duyệt file.
- Bản này tập trung Thiết kế và cầu nối nhận file. Bố cục chat/task hai cột tương tự cho Sản xuất được ghi nhận làm bước sau theo yêu cầu ưu tiên Thiết kế của Duy An.

File Odoo tham chiếu (xác minh trên nhánh triển khai):

- `addons/dac_erp/static/src/xml/backend/design_dashboard.xml`
- `addons/dac_erp/static/src/css/backend/dac_task_dashboard.css`
- `addons/dac_erp/views/dac_work_task_views.xml`
- `addons/dac_erp/views/sale_order_view.xml`
- `addons/dac_erp/static/src/css/backend/sale_order_form.css`
- `addons/dac_erp/static/src/css/backend/conversation_widget.css`
- `addons/dac_erp/static/src/xml/backend/production_dashboard.xml`

## Đã kiểm tra

- baseline:check trước sửa khớp 23 source Odoo UI tại checkout; đọc skill/HANDOFF/changelog/baseline; giữ nguồn production đối chiếu cũ, không đăng nhập production trong phiên.
- Playwright Chromium 1440×1000 kiểm tra hai cột thẻ task và hai cột chi tiết, không dropdown trạng thái thiết kế, không form upload preview lớn, không các bước duyệt lặp lại.
- Kiểm tra Copy ảnh thật qua Clipboard API, blob image/png có kích thước 960×580 đúng ảnh mẫu, nhấn Cmd+V dán ảnh vào bản nháp, gửi và tải lại giữ ảnh. Dán chữ, Enter và Shift+Enter đều hoạt động.
- Kiểm tra bỏ ảnh giữ chữ, ảnh sai loại/trên 1 MB bị từ chối, bản nháp tách theo task/vai trò, ảnh nháp cũ được bỏ khi task vừa được Sale duyệt.
- Kiểm tra hoàn thành → Sale góp ý → revision dashboard → preview mới → hoàn thành → duyệt → gửi file. Sản xuất đọc file từ dashboard, trang đơn và task; không có nút ghi/duyệt; tải xuống đúng byte file SVG gốc.
- Kiểm tra góp ý mới thu hồi duyệt, file cũ không hiện ở danh sách hiện hành Sản xuất; clipboard bị từ chối có thông báo đúng; giả lập quota localStorage giữ bản nháp và rollback, không báo đã gửi.
- Đã xem ảnh desktop/mobile tại test-results/mockup-design-chat-20261105; kiểm tra 390×844 một cột và không tràn trang. Không ghi nhận lỗi JavaScript trong luồng chính và các trường hợp lỗi đã kiểm tra.
- npm --prefix mockup run check; baseline:update; change:verify CL-20261105-003 và bản mới; build, snapshot độc lập và ZIP theo workflow.

## Giới hạn và phần còn cần làm

- Mockup chỉ lưu tin đã gửi, ảnh và file trong localStorage cùng trình duyệt; bản nháp chỉ giữ trong phiên tab. Không có truyền dữ liệu giữa tài khoản/máy, API Odoo hoặc thông báo thật.
- Giới hạn mẫu 1 MB mỗi ảnh/file và tổng dung lượng localStorage. Hết dung lượng sẽ giữ bản nháp và báo chưa gửi; production cần kho attachment và giới hạn phù hợp.
- Copy/paste thật đã thử trên Chromium macOS; chưa thử Safari/iOS hoặc điện thoại thật. Browser cần quyền clipboard; fallback mở ảnh để sao chép thủ công. Ảnh copy thành PNG tĩnh.
- Xem trước file hỗ trợ ảnh/PDF của trình duyệt; AI/CDR và định dạng thiết kế khác tải về bằng file gốc để mở trong phần mềm chuyên dụng.
- File vector DEMO-006 là dữ liệu giả có sẵn trong seed mới/snapshot; không ghi đè thiết kế và lịch sử người dùng đã lưu từ bản trước.
- Màn task/chat Sản xuất áp dụng cùng bố cục hai cột được để bước sau theo ưu tiên Thiết kế. Bản này đã có luồng quản lý sản xuất nhận/xem/tải file, chưa triển khai phân quyền Odoo thật.
- Phiên bản và ZIP mới có local, chưa commit/push hoặc tạo tag GitHub; chưa được duyệt deploy Odoo.

## Mở đúng phiên bản mockup

1. Dùng thư mục `mockup/` đi cùng changelog này hoặc tải đúng Git tag `mockup-CL-20261105-004` sau khi Codex đã push.
2. Trong thư mục snapshot `mockup/`, chạy `npm run dev`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng `PORT=4174 npm run dev`.
3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.
4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file `IMPLEMENTATION.md` riêng khi bắt đầu đưa vào Odoo.

Snapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.
