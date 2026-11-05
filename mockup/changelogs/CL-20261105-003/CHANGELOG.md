# CL-20261105-003 — Mô tả thiết kế, duyệt ảnh preview và bàn giao file

| Thông tin | Giá trị |
| --- | --- |
| Ngày | 2026-11-05 (Việt Nam) |
| Người yêu cầu | Duy An |
| Người triển khai Odoo | Khang |
| Trạng thái | Sẵn sàng bàn giao; chờ Duy An duyệt triển khai |
| Phiên bản mockup | [mockup-CL-20261105-003](https://github.com/marketingduyan/odoo-crm-duyan-addons/tree/mockup-CL-20261105-003/mockup/changelogs/CL-20261105-003/mockup) |
| Bản trước | CL-20261105-002 |

## Yêu cầu của Duy An

Duy An xác nhận phiên trước vào nhầm. Đổi nhãn thành Mô tả thiết kế; có thông tin đơn gồm các dòng sản phẩm ở cuối; thêm bình luận để Thiết kế gửi ảnh preview ban đầu, Sale kiểm tra/duyệt, sau duyệt mới gửi file thiết kế; trạng thái task dùng nút Hoàn thành task, không dùng dropdown. Duy An làm rõ: cứ bấm hoàn thành; nếu Sale bình luận góp ý thì task tự chuyển sang Cần chỉnh sửa; dashboard Thiết kế có một mục task thiết kế cần chỉnh sửa.

## Trước thay đổi

- Chi tiết task dùng nhãn Mô tả chi tiết đơn hàng, trạng thái dạng dropdown và chỉ có một sản phẩm tóm tắt.
- Chưa có trao đổi/ảnh preview theo task, Sale duyệt, yêu cầu chỉnh sửa hoặc bàn giao file sau duyệt.

## Mockup đã thay đổi

- Chi tiết task đổi nhãn Mô tả thiết kế; thông tin đơn hàng có tên khách hàng và bảng các dòng sản phẩm ở cuối trang, không hiện giá tiền. DEMO-001 minh họa hai dòng sản phẩm.
- Thiết kế gửi bình luận kèm ảnh preview PNG/JPG/WebP/GIF, mở xem ảnh, giữ lịch sử và lưu mẫu trong trình duyệt. Task DEMO-002 có preview minh họa để thử ngay.
- Nút Hoàn thành task thay dropdown ở chi tiết, Việc của tôi và task thiết kế trong trang đơn. Bấm là chuyển task sang Hoàn thành; việc chờ Sale duyệt được theo dõi riêng, không chặn hoàn thành khi chưa có ảnh.
- Sale mở đơn → bấm tên task hoặc mở task từ Việc của tôi/lịch để kiểm tra ảnh và gửi góp ý. Mỗi góp ý làm task chuyển sang Cần chỉnh sửa, thu hồi duyệt hiện tại.
- Dashboard có KPI/bộ lọc Cần chỉnh sửa và mục Thiết kế cần chỉnh sửa riêng, hiển thị lời góp ý mới nhất. Thiết kế gửi preview mới rồi bấm hoàn thành lại.
- Sale chỉ duyệt ảnh preview của vòng hiện tại; sau duyệt Thiết kế mới có form gửi file chính thức. Góp ý tiếp làm file cũ được đánh dấu Phiên bản trước, giữ lịch sử.
- Chỉnh thao tác điều hướng để không render lại trễ làm mất nội dung đang gõ; giữ đăng nhập/chọn vai trò, nhận diện DAC, các thay đổi CL-20261105-002 và snapshot đã chốt.

## Màn hình và vai trò

- Màn hình: design, task, tasks, calendar, order, orders.
- Vai trò: design, sale, manager.

## Tiêu chí nghiệm thu để Khang triển khai

- [ ] Vai trò Thiết kế mở task thấy Mô tả thiết kế, khu trao đổi và thông tin đơn cuối trang; bảng có tất cả dòng sản phẩm, chất liệu, kích thước, số lượng; không có giá tiền.
- [ ] Thiết kế tải ảnh preview kèm hoặc không kèm bình luận; ảnh mở xem được, tên file và lịch sử lưu sau tải lại; bài gửi rỗng hoặc file không hợp lệ báo rõ và không tạo bình luận.
- [ ] Bấm Hoàn thành task lập tức ghi trạng thái Hoàn thành, kể cả chưa có preview. Không dùng dropdown hoặc kéo thả để đổi trạng thái task thiết kế.
- [ ] Sale bấm tên task thiết kế trong đơn để xem đúng ảnh/bình luận và gửi góp ý; góp ý tự chuyển task sang Cần chỉnh sửa, có KPI và mục riêng trên dashboard hiển thị nội dung góp ý.
- [ ] Có thể gửi bản preview sửa mới, bấm hoàn thành và được Sale duyệt lại; ảnh của vòng trước không đủ để duyệt vòng mới.
- [ ] Chỉ khi Sale duyệt mới cho Thiết kế gửi file chính thức; Sale xem/tải được file mẫu sau gửi; góp ý sau duyệt thu hồi khả năng gửi mới và đánh dấu file cũ thuộc phiên bản trước.
- [ ] Desktop 1440×1000 có 5 KPI trên một hàng; mobile 390×844 không tràn trang, bảng sản phẩm cuộn trong khung; menu mở/đóng được.
- [ ] Sản xuất vẫn dùng thao tác trạng thái hiện có; các snapshot CL-20261105-001 và CL-20261105-002 giữ nguyên.

## Hướng triển khai Odoo

- Khang triển khai task/review/comment/attachment thật vào dac.work.task và sale.order; tham chiếu design_dashboard.xml, dac_task_dashboard.css, dac_work_task_views.xml và sale_order_view.xml.
- Tách trạng thái hoàn thành công việc khỏi trạng thái duyệt thiết kế. Hoàn thành là Hoàn thành; góp ý từ Sale đưa về Cần chỉnh sửa. Không tự đổi trạng thái đơn hay kích hoạt sản xuất khi duyệt hoặc hoàn thành task.
- Gắn bình luận, người gửi, ảnh preview, phiên bản preview được duyệt và file chính thức theo task; thay đổi duyệt ở vòng mới không được áp dụng cho ảnh cũ. Nối danh tính nhân viên, Sale phụ trách và quyền duyệt theo Odoo thực tế.
- Áp dụng giới hạn/kiểm tra file, quyền đọc/ghi attachment, record rules và xử lý bình luận đồng thời ở server. localStorage và thao tác vai trò trong mockup không thay thế ACL.
- Bảng sản phẩm dùng tất cả sale.order.line được phép xem, không đưa đơn giá/tổng tiền vào view Thiết kế. Các trạng thái mẫu và schema designReview cần map theo model Odoo, không sao chép trực tiếp vào dữ liệu production.
- Phần này chỉ là mockup. Giữ các đề xuất UI-001 đến UI-006 và ghi bổ sung UI-007; không sửa addon, deploy hoặc gửi thông báo thật.

File Odoo tham chiếu (xác minh trên nhánh triển khai):

- `addons/dac_erp/static/src/xml/backend/design_dashboard.xml`
- `addons/dac_erp/static/src/css/backend/dac_task_dashboard.css`
- `addons/dac_erp/views/dac_work_task_views.xml`
- `addons/dac_erp/views/sale_order_view.xml`
- `addons/dac_erp/static/src/css/backend/sale_order_form.css`
- `addons/dac_erp/static/src/css/backend/conversation_widget.css`
- `addons/dac_erp/static/src/css/backend/sale_order_vip.css`

## Đã kiểm tra

- baseline:check khớp 23 file UI Odoo tại checkout; giữ nguồn đối chiếu production cũ, không đăng nhập production trong phiên.
- Playwright Chromium kiểm tra đổi nhãn, bảng hai dòng sản phẩm ở cuối, không giá tiền và không dropdown trạng thái task thiết kế.
- Kiểm tra gửi preview, bài gửi rỗng, ảnh sai loại hoặc trên 1 MB, lưu ảnh/bình luận sau reload, hoàn thành chuyển task sang Hoàn thành.
- Kiểm tra Sale mở task từ trang đơn, góp ý làm task thành Cần chỉnh sửa và có trên dashboard; KPI/bộ lọc đếm đúng; bản sửa mới → hoàn thành → duyệt → gửi file → reload giữ file.
- Kiểm tra góp ý sau duyệt thu hồi quyền gửi file mới, file cũ đánh dấu Phiên bản trước; ảnh preview cũ không được duyệt cho vòng mới; hoàn thành khi chưa có ảnh vẫn được, Sale chưa thể duyệt.
- Kiểm tra luồng Sản xuất còn dropdown trạng thái và không có cột Cần chỉnh sửa; menu mobile mở/đóng. Không ghi nhận lỗi JavaScript trong luồng chính.
- Đã xem ảnh desktop 1440×1000 và mobile 390×844 tại test-results/mockup-design-review-20261105; dashboard có 5 KPI trên một hàng desktop; không tràn ngang ngoài khung bảng.
- npm --prefix mockup run check; baseline:update; build; change:verify bản CL-20261105-002 và bản mới; gói ZIP và snapshot độc lập theo workflow.

## Giới hạn và phần còn cần làm

- Mô phỏng trong cùng trình duyệt: ảnh/file/bình luận/trạng thái lưu localStorage, không gửi đến tài khoản Sale hoặc máy khác. Đổi vai trò trên toolbar để thử luồng.
- Giới hạn mẫu là 1 MB mỗi file và tổng dung lượng theo localStorage trình duyệt; production cần kho file riêng và giới hạn phù hợp. Chỉ dùng dữ liệu/ảnh giả khi thử mockup.
- Các trạng thái mới chưa được triển khai Odoo. Luồng phân quyền, xử lý đồng thời, thông báo và lưu attachment server chưa có trong mockup.
- Chưa thử trên iOS/Safari hoặc điện thoại thật; không khẳng định parity với production. Không tự duyệt triển khai.
- Bản CL-20261105-003 và ZIP chỉ có local, chưa commit/push hoặc tạo tag GitHub.

## Mở đúng phiên bản mockup

1. Dùng thư mục `mockup/` đi cùng changelog này hoặc tải đúng Git tag `mockup-CL-20261105-003` sau khi Codex đã push.
2. Trong thư mục snapshot `mockup/`, chạy `npm run dev`; không cần npm install hoặc Odoo. Nếu đang chạy bản khác ở cổng 4173, dùng `PORT=4174 npm run dev`.
3. Đăng nhập bằng tài khoản mẫu điền sẵn, rồi chọn vai trò cần đối chiếu.
4. So sánh tiêu chí ở trên; ghi commit/PR triển khai vào file `IMPLEMENTATION.md` riêng khi bắt đầu đưa vào Odoo.

Snapshot chỉ chứa mã và dữ liệu mẫu đã chốt. Ghi chú/dữ liệu sửa trong localStorage không tự đi vào snapshot; Codex phải đưa yêu cầu cần bàn giao vào changelog trước khi chốt. Thao tác và quyền trong mockup cần Khang triển khai, kiểm tra trên Odoo.
