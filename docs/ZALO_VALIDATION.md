# Kiểm thử trợ lý Zalo — 02/11/2026

Đã cài/nâng cấp addon trên database Docker riêng `dac_zalo_test`, dùng image
`odoo:18.0`. Không dùng database production hoặc môi trường của dev khác.

**Kết quả**

- 14 test Odoo: `0 failed, 0 error(s) of 14 tests`. Bao gồm liên kết dùng một
  lần/hết hạn, từ chối mạo danh, chống nhập trùng, ACL/record rules, thu hồi
  liên kết, cách ly hội thoại/công việc trong prompt, AI lỗi, giới hạn ngày,
  lịch sử và trạng thái gửi không chắc chắn.
- 11 test Node: 11 pass, 0 fail. Bao gồm xác thực khóa, lọc DM, SQLite giữ dữ
  liệu qua restart, callback chỉ được xóa sau ACK, request ID chống gửi trùng,
  kết quả gửi không chắc chắn, timeout, QR/credential permissions và logout
  trong lúc đang ghi phiên đăng nhập; sự kiện của tài khoản đăng nhập nhầm
  không chặn hàng đợi của tài khoản trợ lý hiện tại.
- Image Docker của bridge build thành công; HTTP smoke test xác minh health,
  từ chối request không có khóa, status không trả cookie và chặn gửi khi chưa
  đăng nhập tài khoản.
- HTTP callback Odoo: request không xác thực → 401; sai tài khoản trợ lý →
  409; payload không hợp lệ → 400; lô hợp lệ → 200. Tin người chưa liên kết
  không tạo hội thoại trong Odoo.
- Luồng xuyên suốt giả lập: wizard tạo mã → HTTP callback → liên kết người
  dùng → hội thoại ẩn mã + một phản hồi chờ gửi; gửi lại callback không tạo
  bản sao. Không phát tin qua tài khoản Zalo thật trong kiểm tra này.
- Chrome headless: màn hình cấu hình hiển thị ở 1440×1000 và 390×844;
  không có page error hoặc tràn ngang. Nút cập nhật trạng thái gọi được bridge
  nội bộ mà không yêu cầu đăng nhập Zalo thật.
- Python/XML/JavaScript parse, tham chiếu manifest, hai bản addon root và
  `addons/`, Compose config và `git diff --check` được kiểm tra.

**Chạy lại**

```sh
cd services/zalo-bridge
npm ci --ignore-scripts
npm test
```

Trên database phát triển đã cài `dac_erp`, chạy Odoo với addon path của checkout:

```sh
odoo -d <database-test-rieng> -i dac_zalo_assistant --stop-after-init \
  --test-enable --test-tags /dac_zalo_assistant --max-cron-threads=0
```

Nếu addon đã được cài, dùng `-u dac_zalo_assistant` thay `-i`. Bổ sung config/
tham số PostgreSQL của môi trường thử nghiệm; không thay tên database bằng
production.

**Lưu ý database trắng**

Các addon sẵn có tham chiếu XML IDs của dashboard/menu trước file khai báo
chúng. Đây là lỗi bootstrap của hệ thống nền, không phải thay đổi của addon
Zalo. Đợt kiểm thử đã tạo các ID menu/client action nền trong database riêng
trước khi cài `dac_erp` và addon Zalo; các định nghĩa thật của `dac_erp` vẫn
được áp dụng khi cài. Không sửa source hoặc thứ tự manifest của addon cũ.

Có thể dùng `scripts/zalo_test_bootstrap.py` qua `odoo shell` sau khi đã tạo
database test với `base,web`. Script chỉ cho phép database tên chính xác
`dac_zalo_test`, đọc source tại `/mnt/extra-addons` và commit các bản ghi nền
chỉ trong database test đó. Không dùng script trên database thật. Test ở trên
cài `dac_erp` và addon Zalo, không yêu cầu cài `CRM_DAC` hoặc `dac_report` mới
trên database trắng. Đây chưa phải kiểm thử nâng cấp toàn bộ hệ thống nền.

**Chưa xác minh bằng dịch vụ thật**

Chưa quét QR, đăng nhập tài khoản “tro ly DAC”, gửi tin Zalo thật hoặc gọi
nhà cung cấp AI bằng API key thật. Các test dùng UID/token giả và mock AI.
Sau khi triển khai môi trường được chọn, cần kiểm thử với hai tài khoản nhân
viên: xác thực, nhắn hai chiều, kiểm tra mất/kết nối lại, rồi bật AI và kiểm
tra một câu hỏi thật. Kiểm tra media và tải toàn bộ lịch sử nằm ngoài phạm
vi bản đầu; thông báo nghiệp vụ tự động cũng chưa được bật.
