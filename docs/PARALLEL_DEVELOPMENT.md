# Phát triển song song

Dùng `VikaKhang286/crm-duyan-v6` làm repository chung. Mỗi tính năng có nhánh
riêng từ `origin/main` mới nhất; trao đổi phạm vi trước khi cùng sửa một addon.
Mốc source production đã đối chiếu ban đầu được ghi tại
[PRODUCTION_BASELINE_2026-11-02.md](PRODUCTION_BASELINE_2026-11-02.md).

**Bắt đầu một tính năng**

Trong checkout riêng của mỗi người:

```sh
git fetch origin
git switch -c codex/ten-tinh-nang origin/main
```

Dev khác dùng tên nhánh riêng, ví dụ `dev/ten-tinh-nang`. Mỗi người dùng
database thử nghiệm và cấu hình Odoo riêng. Nếu chạy trên cùng máy, cần
container/volume riêng và các cổng HTTP, gevent, PostgreSQL khác nhau;
chỉ đổi tên Compose project chưa đủ vì các cổng host trong compose vẫn cố định.

Không dùng database production để thử tính năng. Không commit cấu hình thật,
token/API key, backup database hoặc dữ liệu khách hàng.

**Chia việc và giữ hai cây addon đồng nhất**

Repository hiện có cùng addon ở root và dưới `addons/`. Compose local mount
`./addons`; source production được đối chiếu trong đợt baseline dùng các addon
ở root của thư mục triển khai. Cho tới khi có thay đổi cấu trúc riêng, cập nhật
cả hai vị trí trong cùng commit để tránh local và production chạy khác mã.

Chọn `addons/<module>/` làm nơi sửa trước, đưa đúng các file đã thay đổi sang
`<module>/`, rồi kiểm tra hai bản trước khi push. Ví dụ:

```sh
diff -qr -x __pycache__ -x '*.pyc' dac_erp addons/dac_erp
git diff --check
git status --short
```

Phân công theo tính năng/module. Với `sale.order`, workflow, thanh toán,
phân quyền, manifest hoặc form đơn hàng dùng chung, cần thống nhất file và
contract thay đổi. Khi thay API/model, báo rõ field, method và tác động tới
addon phụ thuộc trong PR.

**Tích hợp và triển khai**

1. Commit/push nhánh tính năng và mở PR vào `main`.
2. Cập nhật nhánh theo `origin/main` trước khi tích hợp; giải quyết xung đột
   trên nhánh tính năng và không force-push nhánh của người khác.
3. Kiểm thử trên database riêng: các test module bị ảnh hưởng, nâng cấp module
   khi cần, và các luồng giao diện/phân quyền liên quan. Kiểm tra cả hai bản addon.
4. Review, gộp PR, rồi triển khai commit cụ thể qua một đầu mối. Ghi lại commit
   đã triển khai và có backup phù hợp trước thay đổi schema/dữ liệu.

Thay đổi chỉ tài liệu không cần nâng cấp module. Mọi triển khai về sau phải
xác minh bind mount thực tế của container; không suy ra nguồn đang chạy từ
checkout hoặc remote cũ còn nằm trên máy chủ.
