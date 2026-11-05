# CL-20261105-005 — Thiết kế xong và chỉnh sửa tiếp sau bàn giao

| Thông tin | Giá trị |
| --- | --- |
| Ngày | 2026-11-05 (Việt Nam) |
| Người yêu cầu | Duy An |
| Người triển khai Odoo | Khang |
| Trạng thái | Sẵn sàng bàn giao; chờ Duy An duyệt triển khai |
| Phiên bản mockup | [mockup-CL-20261105-005](https://github.com/marketingduyan/odoo-crm-duyan-addons/tree/mockup-CL-20261105-005/mockup/changelogs/CL-20261105-005/mockup) |
| Bản trước | CL-20261105-004 |

## Yêu cầu của Duy An

Đổi nút Hoàn thành task thành Thiết kế xong: Thiết kế gửi preview rồi bấm nút. Task đã duyệt và gửi file vẫn có thể chỉnh sửa tiếp, giữ lịch sử preview/file và duyệt lại bản mới.

## Trước thay đổi

- Nút tác vụ thiết kế dùng nhãn Hoàn thành task; task không có preview vẫn có thể chuyển hoàn thành.
- Task đã được duyệt không có nút để Thiết kế chủ động mở sửa. Chỉ khi Sale gửi góp ý thì task mới mở lại vòng chỉnh sửa.

## Mockup đã thay đổi

- Đổi nút thành Thiết kế xong; sau gửi preview của vòng hiện tại mới bấm nút. Badge, KPI và bộ lọc/cột của vai trò Thiết kế dùng nhãn tương ứng.
- Task đã duyệt có nút Chỉnh sửa tiếp, kể cả đã gửi file thiết kế. Thiết kế chủ động mở vòng sửa mới không cần chờ Sale bình luận.
- Mở sửa đưa task về Cần chỉnh sửa, xuất hiện ở mục riêng trên dashboard; ghi thông báo vào chat, giữ mọi bình luận, ảnh và file cũ dưới dạng phiên bản trước.
- Mỗi vòng sửa yêu cầu preview mới, bấm Thiết kế xong, Sale duyệt lại rồi gửi file mới. Có thể tiếp tục mở sửa nhiều lần sau khi bàn giao.
- File cũ không còn là file hiện hành của Sản xuất sau khi mở vòng mới; Sản xuất nhận/tải đúng file mới khi được duyệt và bàn giao. Trạng thái đơn không tự đổi.
- Giữ giao diện hai cột, chat đính kèm/dán ảnh/copy ảnh, mô tả và các dòng sản phẩm, không hiện giá tiền cho Thiết kế.

## Màn hình và vai trò

- Màn hình: design, task, tasks, order, production.
- Vai trò: design, sale, manager, production.

## Tiêu chí nghiệm thu để Khang triển khai

- [ ] Thiết kế thấy nút Thiết kế xong thay Hoàn thành task tại chi tiết task và Việc của tôi; khi chưa có preview vòng hiện tại, nút chưa bật và có lời nhắc gửi preview.
- [ ] Sau gửi preview, bấm Thiết kế xong đưa task vào trạng thái hoàn tất/chờ Sale duyệt và ghi đúng thông báo trong chat.
- [ ] Đối với task đã duyệt trước hoặc sau bàn giao file, Thiết kế có nút Chỉnh sửa tiếp; Sản xuất không có nút này.
- [ ] Bấm Chỉnh sửa tiếp mở vòng sửa mới, có thể gửi ảnh preview mới, task xuất hiện trong mục Cần chỉnh sửa trên dashboard; tải lại không mất vòng sửa.
- [ ] Tin nhắn, preview cũ và file gốc giữ nguyên; file cũ đánh dấu phiên bản trước. Bản cũ không được dùng làm preview duyệt của vòng mới.
- [ ] Gửi preview mới → Thiết kế xong → Sale duyệt → gửi file mới: Sản xuất tải được đúng file mới, không nhận nhầm file cũ.
- [ ] Có thể Chỉnh sửa tiếp sau khi đã gửi file mới lần thứ hai; không giới hạn vòng sửa bằng số lần đã hoàn thành/bàn giao.
- [ ] Desktop 1440×1000 và mobile 390×844 giữ bố cục, không tràn trang; các snapshot đã chốt không thay đổi.

## Hướng triển khai Odoo

- Khang đổi nhãn nút và trạng thái hiển thị tương ứng trong view task/dashboard Thiết kế; giá trị trạng thái demo cần map theo model thực tế.
- Thiết kế được phân công có hành động mở sửa sau duyệt/bàn giao. Kiểm tra quyền ở server; việc đã gửi file không khóa task vĩnh viễn.
- Mỗi lần mở sửa tăng vòng/phiên bản, thu hồi duyệt hiện tại và yêu cầu ảnh preview mới; giữ attachment và lịch sử trước đó, không ghi đè file cũ.
- File hiện hành Sản xuất gắn với preview đang được duyệt và đã bàn giao. Không tự đổi trạng thái đơn hoặc kích hoạt sản xuất khi mở sửa/duyệt/gửi file.
- Giữ các yêu cầu CL-20261105-004 về chat/copy/paste/file, và kế hoạch áp dụng giao diện cho Sản xuất ở bước sau.

File Odoo tham chiếu (xác minh trên nhánh triển khai):

- `addons/dac_erp/static/src/xml/backend/design_dashboard.xml`
- `addons/dac_erp/static/src/css/backend/dac_task_dashboard.css`
- `addons/dac_erp/views/dac_work_task_views.xml`
- `addons/dac_erp/views/sale_order_view.xml`
- `addons/dac_erp/static/src/css/backend/sale_order_form.css`
- `addons/dac_erp/static/src/css/backend/conversation_widget.css`
- `addons/dac_erp/static/src/xml/backend/production_dashboard.xml`

## Đã kiểm tra

- baseline:check khớp 23 file UI Odoo tại checkout; giữ nguồn production tham chiếu cũ, không đăng nhập production trong phiên.
- Playwright: kiểm tra nhãn Thiết kế xong, nút chưa bật khi chưa có preview; mở task DEMO-006 đã duyệt và có file, bấm Chỉnh sửa tiếp, lịch sử/file cũ giữ nguyên, hiện revision trên dashboard và giữ sau reload.
- Kiểm tra Sản xuất không có nút mở sửa, không nhận file cũ là hiện hành; gửi preview sửa mới → Thiết kế xong → Sale duyệt → bàn giao file mới; Sản xuất tải file mới đúng byte.
- Kiểm tra mở sửa tiếp lần thứ hai sau bàn giao: vòng sửa tăng, cả hai file cũ còn trong lịch sử. Không ghi nhận lỗi JavaScript.
- Đã xem ảnh desktop 1440×1000 và mobile 390×844 tại test-results/mockup-design-reopen-20261105; kiểm tra mobile không tràn ngang.
- npm --prefix mockup run check; baseline:update; build; verify bản trước và snapshot mới; đóng gói ZIP.

## Giới hạn và phần còn cần làm

- Đây là mockup local: trạng thái, chat và file lưu trong cùng trình duyệt, không nối API hoặc phân quyền Odoo thật.
- Giữ giới hạn file 1 MB của bản trước; chưa kiểm tra trên Safari/iOS hoặc thiết bị thật, chưa đối chiếu live production trong phiên.
- Khi chủ động mở sửa, duyệt hiện tại được thu hồi ngay. File cũ vẫn trong lịch sử của Thiết kế, mục hiện hành Sản xuất chờ bản mới được duyệt và bàn giao.
- Bản mới và ZIP chỉ có local, chưa commit/push, chưa tạo tag GitHub hoặc deploy Odoo.

## Mở đúng phiên bản mockup

1. Dùng thư mục `mockup/` đi cùng changelog này hoặc tải đúng Git tag `mockup-CL-20261105-005` sau khi Codex đã push.
2. Trong thư mục snapshot `mockup/`, chạy `npm run dev`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng `PORT=4174 npm run dev`.
3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.
4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file `IMPLEMENTATION.md` riêng khi bắt đầu đưa vào Odoo.

Snapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.
