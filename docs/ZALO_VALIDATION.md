# Kiểm thử trợ lý Zalo — 02/11/2026

Đã cài/nâng cấp addon trên database Docker riêng `dac_zalo_test`, dùng image
`odoo:18.0`. Không dùng database production hoặc môi trường của dev khác.

**Kết quả**

- 52 test trợ lý/thông báo Odoo: 0 failed, 0 errors. Bao gồm xác thực,
  kiểm soát hội thoại/AI, xác nhận tạo đơn, giao việc, trạng thái, nhắc hạn,
  bàn giao, phân quyền tài chính, nhân sự, bản tin, cảnh báo, giờ yên lặng,
  quyền bật/tắt, báo quá hạn 24 giờ đến Sale/quản lý và request ID bền vững
  khi transaction transport rollback.
  Đã chạy với cả `dac_attendance`, `CRM_DAC`, `dac_report`, `dac_openclaw`
  được cài trong database thử nghiệm.
- 9 test hook OpenClaw: 0 failed, 0 errors; các hook tạo/chuyển bước đơn và
  tạo/giao/hoàn tất task vẫn phát sự kiện.
- Hồi quy DAC ERP: 25 ca trước thay đổi có 2 failure và 2 error sẵn có; sau
  thay đổi gặp đúng bốn tên ca này, không có failure mới trong tập chọn.
  Lượt kết hợp cuối có 86 ca (52 trợ lý + 9 OpenClaw + 25 ERP), nên **không kết luận toàn
  bộ hệ thống đã xanh**. Chi tiết bốn ca nằm bên dưới.
- Đã merge `origin/main` tại `9616d4a` vào nhánh tính năng và nâng cấp các
  addon thay đổi trước kiểm thử tích hợp. Không ghi đè cập nhật navbar,
  phân quyền chấm công hoặc định tuyến trang chủ của dev khác.
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
- Chrome headless: màn hình cấu hình hiển thị ở desktop/mobile. Kiểm tra
  mới trên màn hình **Thông báo của tôi** 1440×1100 và 390×844: checkbox được
  lưu thật; tắt tạo đơn → tạo đơn qua phiên Sale không có event; bật lại →
  đơn mới có đúng một event cho Sale đó. Không có page error hoặc tràn ngang.
  Cron bị tắt trong bài UI nên không gửi tin qua tài khoản Zalo thật.
  Sau merge main, đã mở lại cấu hình với đủ 17 công tắc và tùy chọn cá nhân
  trên mobile: không có page error hoặc tràn ngang.
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

Các addon sẵn có tham chiếu XML IDs của dashboard/menu/action trước file khai báo
chúng. Đây là lỗi bootstrap của hệ thống nền, không phải thay đổi của addon
Zalo. Đợt kiểm thử đã tạo các ID menu/client/server action nền trong database riêng
trước khi cài `dac_erp` và addon Zalo; các định nghĩa thật của `dac_erp` vẫn
được áp dụng khi cài. Không sửa source hoặc thứ tự manifest của addon cũ.

Có thể dùng `scripts/zalo_test_bootstrap.py` qua `odoo shell` sau khi đã tạo
database test với `base,web`. Script chỉ cho phép database tên chính xác
`dac_zalo_test`, đọc source tại `/mnt/extra-addons` và commit các bản ghi nền
chỉ trong database test đó. Không dùng script trên database thật. Test ở trên
cài `dac_erp`, addon Zalo và `dac_attendance`. Lượt kiểm tra bổ sung đã cài
`CRM_DAC`, `dac_report`, `dac_openclaw` với các ID nền trước đó. Các định
nghĩa thật của module vẫn được nạp, không sửa source của các addon này. Đây
chưa phải kiểm thử nâng cấp trên bản sao dữ liệu production.

**Chưa xác minh bằng dịch vụ thật**

Chưa quét QR, đăng nhập tài khoản “tro ly DAC”, gửi tin Zalo thật hoặc gọi
nhà cung cấp AI bằng API key thật. Các test dùng UID/token giả và mock AI.
Sau khi triển khai môi trường được chọn, cần kiểm thử với hai tài khoản nhân
viên: xác thực, nhắn hai chiều, kiểm tra mất/kết nối lại, rồi bật AI và kiểm
tra một câu hỏi thật. Kiểm tra media và tải toàn bộ lịch sử nằm ngoài phạm
vi bản đầu. Thông báo nghiệp vụ đã được thực hiện và kiểm thử, nhưng chưa
triển khai hoặc gửi qua production.


**Bốn ca hồi quy nền chưa sửa trong phạm vi này**

- `TestProductionWorkflow.test_delivery_address_wizard_allows_customer_pickup`
- `TestProductionWorkflow.test_delivery_address_wizard_confirms_delivery_with_address`
- `TestProductionWorkflow.test_delivery_defaults_cod_to_remaining_after_deposit`
- `TestProductionWorkflow.test_delivery_keeps_manually_entered_cod`

Hai ca wizard kỳ vọng action `reload` nhưng luồng hiện tại không trả tag đó;
hai ca COD thiết lập cọc mà không có đúng một hóa đơn cọc nên bị logic cọc
hiện tại từ chối. Đã tái hiện trước khi nạp hook thông báo và so sánh đúng
tên/loại lỗi sau thay đổi. Không sửa test hoặc logic nghiệp vụ cũ để làm xanh
báo cáo.
