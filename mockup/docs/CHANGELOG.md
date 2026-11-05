# Lịch sử mockup

Các bản bàn giao có mã và snapshot nằm trong [danh mục changelog](../changelogs/README.md).

## 05/11/2026 — CL-20261105-010: Thời gian thông báo cùng dòng

- Đặt thời gian ngay sau nội dung dạng “… · 5 phút”, bỏ hàng thời gian riêng.
- Giữ tối đa 3 dòng tính cả thời gian, áp dụng cột Thông báo và bảng chuông trên desktop/mobile.
- Xem [changelog và snapshot](../changelogs/CL-20261105-010/CHANGELOG.md).

## 05/11/2026 — CL-20261105-009: Thu gọn thẻ Cần làm

- Bỏ thông tin khách hàng lặp lại; mỗi thẻ giữ tên task, mã đơn/sản phẩm, một nhãn và ngày hạn cùng hàng.
- Gom trạng thái/cảnh báo thành một nhãn, ưu tiên Quá hạn; không đổi dữ liệu trạng thái hay bộ lọc.
- Đã kiểm tra desktop/mobile và mở task/thông báo; xem [changelog và snapshot](../changelogs/CL-20261105-009/CHANGELOG.md).

## 05/11/2026 — CL-20261105-008: Cần làm và Thông báo

- Hai cột Cần làm / Thông báo; gộp task cần chỉnh sửa vào Cần làm, giữ lọc theo các ô thống kê.
- Thẻ thông báo kiểu Facebook: avatar/icon, tên đậm, tên task tối đa 20 ký tự, nội dung tối đa 3 dòng và thời gian tương đối.
- Nút chuông mở bảng trong ERP; dùng chung danh sách, đọc/chưa đọc, đánh dấu tất cả và liên kết task/đơn; module `src/notifications.js`.
- Xem [changelog và snapshot](../changelogs/CL-20261105-008/CHANGELOG.md).

## 05/11/2026 — CL-20261105-007: Tên task Thiết kế

- Tên theo mẫu Thiết kế + Tên khách + DD/MM; dùng ngày tạo task.
- Đồng bộ tên hiển thị/tìm kiếm trên Tổng quan, chi tiết, lịch và đơn; giữ dữ liệu trình duyệt cũ, ngày trong tên không đổi khi mở sửa.
- Xem [changelog và snapshot](../changelogs/CL-20261105-007/CHANGELOG.md).

## 05/11/2026 — CL-20261105-006: Sidebar Thiết kế và Tổng quan

- Sidebar Thiết kế còn 4 mục, rộng 230px, bỏ rail ứng dụng và các mục không thuộc vai trò; giữ menu trên mọi màn hình được phép.
- Tổng quan có 6 ô thống kê, số đơn tính không trùng mã; bấm ô lọc/cuộn tới đúng danh sách task, có nhóm đang xem và trạng thái rỗng.
- Lọc dữ liệu mẫu theo người được giao, giữ chat/preview/file, thu gọn sidebar và menu mobile; xem [changelog và snapshot](../changelogs/CL-20261105-006/CHANGELOG.md).

## 05/11/2026 — CL-20261105-005: Thiết kế xong và chỉnh sửa tiếp

- Đổi nút Hoàn thành task thành Thiết kế xong; gửi preview rồi mới bấm nút để chờ Sale duyệt.
- Task đã duyệt, kể cả đã bàn giao file, có nút Chỉnh sửa tiếp; mở vòng mới và giữ mọi preview/file cũ.
- Đã kiểm tra hai vòng mở lại, gửi preview mới, duyệt lại và Sản xuất tải đúng file mới; xem [changelog và snapshot](../changelogs/CL-20261105-005/CHANGELOG.md).

## 05/11/2026 — CL-20261105-004: Thiết kế hai cột và chat gọn

- Dashboard hiển thị thẻ task hai cột; chi tiết có mô tả/file bên trái, chat bên phải; thông tin đơn và sản phẩm ở dưới.
- Icon đính kèm và Ctrl/Cmd+V thêm ảnh vào khung chat; có bản nháp ảnh, bỏ ảnh, Enter gửi, Shift+Enter xuống dòng.
- Nút Copy ảnh ghi ảnh PNG vào clipboard thật để Sale copy/paste; giữ luồng hoàn thành → góp ý/cần chỉnh sửa → duyệt → bàn giao file.
- Sản xuất có mục xem/tải file đã bàn giao ở dashboard và trang đơn; bản cũ bị rút duyệt không hiện là file hiện hành.
- Đã kiểm tra trình duyệt desktop/mobile, copy/paste thật và tải file đúng byte; xem [changelog và snapshot](../changelogs/CL-20261105-004/CHANGELOG.md).

## 05/11/2026 — CL-20261105-003: Trao đổi và duyệt thiết kế

- Đổi nhãn thành Mô tả thiết kế; thông tin đơn và bảng nhiều dòng sản phẩm ở cuối.
- Bình luận kèm ảnh preview, nút Hoàn thành task thay dropdown; Sale góp ý tự chuyển task sang Cần chỉnh sửa và hiển thị ở mục riêng trên dashboard.
- Sau Sale duyệt mới gửi file thiết kế; góp ý tiếp hủy duyệt hiện tại và yêu cầu preview mới, giữ lịch sử.
- Đã kiểm tra toàn bộ luồng trong trình duyệt desktop/mobile; xem [changelog và snapshot](../changelogs/CL-20261105-003/CHANGELOG.md).

## 05/11/2026 — CL-20261105-002: Công việc nhân viên Thiết kế

- Dashboard ưu tiên danh sách task với tên việc, khách hàng, trạng thái, hạn và tìm/lọc.
- Chi tiết task đặt mô tả đơn trước, thông tin đơn và thông số sản phẩm bên dưới.
- Ẩn giá tiền trong danh sách/chi tiết đơn của Thiết kế; luồng task từ lịch và Việc của tôi dùng cùng trang chi tiết.
- Đã kiểm tra trình duyệt desktop/mobile; xem [changelog và snapshot](../changelogs/CL-20261105-002/CHANGELOG.md).

## 05/11/2026 — CL-20261105-001: Bàn giao theo phiên bản

- Tạo thư mục riêng cho mỗi yêu cầu/phiên, với mã `CL-YYYYMMDD-NNN`.
- Chốt snapshot có checksum, ZIP chạy độc lập và Git tag gắn với từng changelog.
- Cập nhật quy trình/skill để các phiên sau tạo và chốt changelog khi Duy An chỉnh xong.
- Bản đầu lưu lại nền DAC ERP và luồng đăng nhập/chọn vai trò đang có.

## 05/11/2026 — Đăng nhập và chọn vai trò

- Trang đầu mở màn hình đăng nhập theo nhận diện DAC, điền sẵn tài khoản mẫu.
- Sau đăng nhập, chọn Quản lý, Sale, Thiết kế hoặc Sản xuất để vào dashboard tương ứng.
- Nhớ vai trò trong tab khi tải lại; có chọn lại vai trò và đăng xuất, giữ dữ liệu đã chỉnh.
- Khởi động localhost theo yêu cầu để Duy An xem và chỉnh giao diện.

## 05/11/2026 — Bản nền

- Tạo web mockup độc lập với 11 màn hình chính của DAC ERP.
- Đối chiếu trực tiếp Dashboard Thiết kế và sidebar production trong ngày.
- Tạo dữ liệu giả, thao tác đơn hàng/task/chat/chấm công và lưu localStorage.
- Thêm giao diện máy tính/điện thoại, trạng thái trống và công cụ bàn giao.
- Thêm baseline source, skill cập nhật phiên mới và workflow build GitHub.
- Các đề xuất bố cục mobile trong HANDOFF đang chờ Duy An duyệt.
