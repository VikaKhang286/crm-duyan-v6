# Bàn giao giao diện Thiết kế cho Khang

Repo nhận: **VikaKhang286/crm-duyan-v6**. Nhánh mockup: **feat/ui-mockup-workflow**.
Bản bàn giao: **CL-20261105-010**, tag **mockup-CL-20261105-010**.

## Mở đúng phiên bản

```sh
git fetch origin --tags
git switch feat/ui-mockup-workflow
npm --prefix mockup run dev
```

Mở http://localhost:4173, đăng nhập mẫu đã điền sẵn, chọn **Thiết kế**.
Nếu cổng đã dùng: `PORT=4174 npm --prefix mockup run dev`.
Không cần npm install hoặc Odoo để xem mockup.

Bản cố định của gói mới nhất nằm ở
[changelogs/CL-20261105-010/mockup](../changelogs/CL-20261105-010/mockup).
Đọc [danh mục toàn bộ thay đổi](../changelogs/README.md) từ CL-20261105-002
đến CL-20261105-010; CL-20261105-010 là thay đổi cuối, các yêu cầu trước vẫn có hiệu lực.

Có thể tạo ZIP chạy độc lập:

```sh
npm --prefix mockup run change:pack -- CL-20261105-010
```

ZIP nằm tại `mockup/exports/CL-20261105-010.zip`, được bỏ qua trong Git.

## Phạm vi giao diện cần triển khai

- Sidebar nhân viên Thiết kế: Tổng quan, Lịch thiết kế, Đơn hàng được giao, Chấm công.
- Tổng quan hai cột: Cần làm (gồm cần chỉnh sửa) và Thông báo; KPI bấm để lọc đúng task.
- Task đặt tên Thiết kế + Tên khách + DD/MM; ngày đang dùng ngày tạo task.
- Thẻ task gọn, một nhãn trạng thái/cảnh báo và ngày hạn cùng hàng.
- Chi tiết task hai cột mô tả/file và chat; thông tin đơn, khách hàng và các dòng sản phẩm ở dưới; Thiết kế không thấy giá tiền.
- Chat có icon đính kèm, Ctrl/Cmd+V dán ảnh, Copy ảnh để Sale gửi khách hàng.
- Gửi preview → Thiết kế xong → Sale duyệt → bàn giao file; Sale góp ý hoặc Thiết kế mở sửa tiếp sẽ tạo vòng sửa mới, giữ lịch sử.
- Quản lý sản xuất xem/tải file đang được duyệt và đã bàn giao.
- Thông báo có avatar/icon, tên người/task in đậm, tên task tối đa 20 ký tự, tối đa 3 dòng tính cả thời gian inline; chuông dùng chung đọc/chưa đọc.

Tiêu chí chi tiết nằm trong [HANDOFF](HANDOFF.md) và các changelog tương ứng.
HTML/JS là mẫu duyệt giao diện; Khang triển khai vào OWL/XML/CSS/model phù hợp,
kiểm tra API, quyền, trạng thái, attachment và thông báo thật trước deploy.

## Nguồn và tính bất biến

Gói này lấy từ commit nguồn `60e11822dc7ff60c83f22d6fd898e809f2296e9c`
của repo `marketingduyan/odoo-crm-duyan-addons`, gồm bản Thiết kế đã chốt đến CL-20261105-010.
Các sửa local khác đang làm không được đưa vào bản bàn giao này.

Snapshot, manifest và changelog cũ được giữ nguyên checksum. Vì vậy một số
liên kết và trường `repositoryUrl` trong các bản đã chốt vẫn chỉ về repo nguồn;
đó là nguồn gốc của snapshot. Dùng tag và thư mục trên repo nhận nêu ở đầu tài liệu
để Khang đối chiếu tại repo này. Không sửa các bản đã chốt để đổi nguồn gốc.

`baseline.json` là fingerprint checkout Odoo ở phiên tạo mẫu, không chứng nhận
UI giống production của repo nhận. Khi triển khai, đối chiếu các file trong
`screens.*.sources` ở `src/data.js` trên nhánh thực tế của Khang.
Bản mockup chỉ có dữ liệu giả, đăng nhập và lưu trữ trong trình duyệt; chưa triển khai Odoo.
