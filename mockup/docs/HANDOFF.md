# Bàn giao mockup DAC ERP

## Trách nhiệm

| Người | Trách nhiệm |
| --- | --- |
| Duy An | Quản lý sản phẩm, đề xuất/chỉnh UI, duyệt mẫu; code một số tính năng khi cần |
| Khang | Lập trình chính, đối chiếu nghiệp vụ, triển khai Odoo, kiểm tra và deploy production |

Trạng thái bản đầu ngày 05/11/2026: **Mẫu nền để Duy An bắt đầu sửa; chưa duyệt triển khai.**

Từ nay, tài liệu bàn giao chi tiết được lập theo mã trong
[mockup/changelogs](../changelogs/README.md), kèm snapshot và phiên bản riêng.
File này giữ nguyên bối cảnh và đề xuất chung. Mỗi yêu cầu/phiên chỉnh mới
mở mã mới; khi xong Codex tạo ZIP để Duy An gửi Khang.

## Bản nền

- Sidebar tối, điểm nhấn vàng DAC, nền sáng, thẻ KPI, bảng đơn và form hai cột.
- Dashboard Thiết kế đối chiếu production trực tiếp ngày 05/11/2026.
- Các màn hình sản xuất, danh sách/chi tiết đơn và task dựa trên bằng chứng
  production 04/11 và source trong repo. Không khẳng định parity trực tiếp
  với production cho các màn hình chưa xem trong phiên.
- Quản lý dùng cấu trúc “Điều phối CRM”, pipeline và trung tâm can thiệp theo XML.
- Chỉ có fixtures: không sao chép tài khoản, đơn hàng hoặc tin nhắn thật.

## Các điểm khác biệt đang thể hiện — chờ Duy An duyệt

| ID | Đề xuất / khác biệt | Tiêu chí nghiệm thu khi triển khai | Trạng thái |
| --- | --- | --- | --- |
| UI-001 | Mobile có thanh tên màn hình và nút menu; KPI dạng 2×2 | 390×844: mở/đóng menu, thấy tên màn hình, không tràn trang | Chờ duyệt |
| UI-002 | Form dòng sản phẩm mẫu hiện chất liệu/kích thước | Popup của vai trò thiết kế và sản xuất hiện đủ thông số được phép xem | Chờ duyệt |
| UI-003 | Toolbar chọn màn hình, vai trò, dữ liệu trống và bàn giao | Chỉ dùng trong công cụ duyệt mockup | Công cụ mockup |
| UI-004 | Tạo/sửa đơn, đổi trạng thái, chấm công và chat dùng dữ liệu local | Khang nối nghiệp vụ thật, kiểm tra ACL, record rules và tác động trước deploy | Mô phỏng |
| UI-005 | Trang đầu đăng nhập, sau đó chọn vai trò Quản lý / Sale / Thiết kế / Sản xuất | Đăng nhập mẫu → chọn vai trò → dashboard đúng vai trò; tải lại giữ view; đăng xuất về login | Duy An yêu cầu 05/11; đã dựng mockup |
| UI-006 | Dashboard nhân viên Thiết kế theo task; chi tiết có mô tả trước, thông tin đơn và tên khách hàng bên dưới; ẩn tiền trong danh sách/chi tiết đơn của Thiết kế | Desktop/mobile mở task từ dashboard, Việc của tôi và lịch; xem đủ mô tả/thông số; không có giá tiền trong luồng này | Đã dựng [CL-20261105-002](../changelogs/CL-20261105-002/CHANGELOG.md); chờ duyệt triển khai |
| UI-007 | Mô tả thiết kế, bình luận/ảnh preview, nút Hoàn thành task; Sale góp ý → Cần chỉnh sửa; duyệt mới gửi file chính thức; các dòng sản phẩm ở cuối | Hoàn thành là hoàn thành, không cần chờ duyệt; góp ý tạo lại việc cần chỉnh; có mục Thiết kế cần chỉnh sửa; chỉ duyệt preview của vòng hiện tại; không hiện tiền | Đã dựng [CL-20261105-003](../changelogs/CL-20261105-003/CHANGELOG.md); chờ duyệt triển khai |
| UI-008 | Thiết kế hai cột, chat gọn có icon đính kèm/dán ảnh và Copy ảnh; Sản xuất xem/tải file đã bàn giao | Thử copy ảnh thật và Ctrl/Cmd+V; giữ hoàn thành/góp ý/duyệt; quản lý sản xuất nhận đúng file gốc của phiên bản được duyệt | Đã dựng [CL-20261105-004](../changelogs/CL-20261105-004/CHANGELOG.md); chờ duyệt triển khai |
| UI-009 | Nút Thiết kế xong; task đã duyệt và gửi file vẫn Chỉnh sửa tiếp | Gửi preview trước khi bấm Thiết kế xong; mở lại sau duyệt/bàn giao nhiều lần, giữ lịch sử và duyệt lại bản mới | Đã dựng [CL-20261105-005](../changelogs/CL-20261105-005/CHANGELOG.md); chờ duyệt triển khai |
| UI-010 | Sidebar nhân viên Thiết kế 230px, 4 mục; Tổng quan có thống kê đơn/task và danh sách lọc theo từng ô | Tổng quan/Lịch thiết kế/Đơn hàng được giao/Chấm công; không có thanh ứng dụng, kế toán hoặc mục dư; mọi ô thống kê lọc đúng task, phân biệt số đơn và task | Đã dựng [CL-20261105-006](../changelogs/CL-20261105-006/CHANGELOG.md); chờ duyệt triển khai |
| UI-011 | Tên task: Thiết kế + Tên khách + DD/MM | Hiển thị thống nhất trên Tổng quan, task, lịch và đơn; ngày đang dùng ngày tạo task, giữ ngày khi mở sửa tiếp | Đã dựng [CL-20261105-007](../changelogs/CL-20261105-007/CHANGELOG.md); ngày tạo là giả định chờ Duy An điều chỉnh nếu cần |
| UI-012 | Tổng quan hai cột Cần làm (gồm cần chỉnh sửa) / Thông báo; nút chuông mở bảng thông báo trong ERP | Avatar người, icon hệ thống; tên người/task đậm, task ≤20 ký tự, nội dung ≤3 dòng; click mở đúng task/đơn, đọc/chưa đọc đồng bộ | Đã dựng [CL-20261105-008](../changelogs/CL-20261105-008/CHANGELOG.md); chờ duyệt triển khai |
| UI-013 | Thẻ Cần làm gọn, một nhãn trạng thái/cảnh báo và hạn cùng hàng | Không lặp tên khách/nhãn; ưu tiên Quá hạn nếu chồng trạng thái; giữ trạng thái gốc và bộ lọc | Đã dựng [CL-20261105-009](../changelogs/CL-20261105-009/CHANGELOG.md); chờ duyệt triển khai |
| UI-014 | Thời gian thông báo cùng dòng nội dung | Dạng “… · 5 phút”, không có hàng thời gian riêng; tổng văn bản và thời gian ≤3 dòng trong cột và bảng chuông | Đã dựng [CL-20261105-010](../changelogs/CL-20261105-010/CHANGELOG.md); chờ duyệt triển khai |

UI-008 ưu tiên màn Thiết kế theo chỉ đạo Duy An. Màn task/chat Sản xuất sẽ theo
cùng mẫu ở bước tiếp theo; bản này đã có mục nhận file thiết kế trên dashboard
và trang đơn Sản xuất. Vai trò Sản xuất trong mockup được dùng để duyệt góc nhìn
nhận file của quản lý sản xuất; Khang phải map đúng nhóm quản lý và đơn được phân
công trong Odoo, không hiểu là cấp quyền tải mọi file cho toàn bộ nhân viên.

## Yêu cầu mới

Codex ghi các mục sau trong `changelogs/CL-.../change.json` và tạo `CHANGELOG.md`
theo [quy trình changelog](CHANGELOG_WORKFLOW.md). Không chỉ ghi vào tài liệu chung:

```text
ID / màn hình:
Người đề xuất: Duy An
Hiện tại:
Muốn thay đổi:
Vai trò bị ảnh hưởng:
File mockup:
File Odoo tham chiếu: xem src/data.js hoặc nút Bàn giao
Tiêu chí nghiệm thu desktop/mobile:
Trạng thái: Đề xuất / Đã duyệt / Đang triển khai / Đã production
Commit mockup:
Commit triển khai và ngày deploy:
```

Ghi chú qua nút Bàn giao hiện lưu trong trình duyệt. Tải JSON và chuyển nội dung
cần thiết vào changelog trước khi chốt snapshot và gói ZIP gửi Khang.
Tool không tự gửi tin nhắn cho Khang.

## Khang đối chiếu khi triển khai

1. Chọn đúng commit mockup đã được Duy An duyệt.
2. Đọc map source của màn hình, kiểm tra lại XML ID/model thực tế trên nhánh triển khai.
3. Mockup dùng mã ngắn `quote/design/production/...`; chuyển sang giá trị field
   Odoo đang dùng, không sao chép mã trạng thái demo vào dữ liệu thật.
4. Kiểm tra luồng ghi dữ liệu và quyền của từng vai trò; thao tác hiện trong mẫu
   không có nghĩa là vai trò đó được cấp thêm quyền production.
5. Chụp lại desktop/mobile sau triển khai, ghi commit và cập nhật trạng thái.
