# Triển khai Trợ lý DAC lên production — 02/11/2026

Đã cài `dac_zalo_assistant` **18.0.2.1.0** trên `production_crm` của
`https://crm.duyan.vn`, theo yêu cầu triển khai của chủ hệ thống. Mã ứng dụng
được triển khai từ commit `b12d15187b275c5e8a56a57e723cf24a71d063d0`, sau đó
nâng cấp icon từ commit `a61cb166ac2f8b2e304eff2302ede23f34ea0919`.

Odoo dừng lúc 11:51:35 và khởi động lại lúc 11:52:40, giờ Việt Nam để cài
addon. Lúc 13:50 có lần dừng ngắn để nâng cấp icon. Cấu hình bridge hoàn tất
lúc 11:53:51. Tài khoản Zalo thật chưa đăng nhập; chưa gửi tin hoặc gọi nhà
cung cấp AI thật.

Theo yêu cầu tiếp theo, menu đã được nâng cấp với icon chatbot. Phiên quét QR
đã khởi tạo lúc 13:51 giờ Việt Nam; QR đang hiển thị trong Odoo và được gửi
cho chủ hệ thống để quét. Trạng thái cuối lúc xác minh là `waiting_qr`.

## Cập nhật giao diện admin — 02/11/2026 16:10

Đã nâng addon lên **18.0.2.3.0** để sửa giao diện Cấu hình Trợ lý Zalo:

- Admin mở app sẽ vào Cấu hình; nhân viên vẫn vào mục xác thực của họ.
- Cấu hình chia thành các tab Kết nối, Trợ lý AI và Thông báo.
- QR xếp thành khung riêng bên dưới trạng thái, hướng dẫn không còn nằm cạnh ảnh.
- Thanh trạng thái rút về nhãn gọn, tránh tràn vùng nội dung.
- Khi bấm tạo QR, Odoo đợi bridge cập nhật trạng thái để ảnh xuất hiện ngay.

Database được sao lưu và kiểm tra catalog trước nâng cấp tại:
`/srv/odoo-deploy/backups/zalo-ui-fix-18.0.2.3.0-20261102-160855/production_crm.dump`
(70.270.246 byte). Hai file addon trước khi thay cũng nằm trong thư mục backup.
Bản triển khai và log: `/srv/odoo-deploy/releases/zalo-ui-6b9b3e3-20261102-fix`.

Sau nâng cấp, Odoo và trang HTTPS trả 200, QR vẫn hiển thị; ba tab đã được mở
kiểm tra trên trình duyệt. Phần mobile còn độ tràn ngang 4 px từ app shell Odoo.
Mã QR đang chờ quét; chưa xác nhận đăng nhập Zalo.

## Cách bắt đầu dùng

1. QR hiện đã được tạo và đang hoạt động. Quét QR đã gửi trong cuộc trò
   chuyện, bằng điện thoại đang đăng nhập tài khoản “tro ly DAC”. Nếu hết
   hạn, admin vào **Trợ lý Zalo → Cấu hình** rồi bấm **Lấy QR / cập nhật
   kết nối** để tạo mã mới.
2. Xác nhận đăng nhập trên điện thoại. Admin bấm **Lấy QR / cập nhật kết
   nối** lần nữa để lưu UID trợ lý và xác nhận trạng thái đã kết nối.
3. Mỗi nhân viên vào **Xác thực với trợ lý**, tạo mã rồi gửi lệnh
   `/xac-thuc DAC-...` cho tài khoản trợ lý. Chỉ nhân viên đã liên kết mới
   nhận được thông báo; không phát lại xác nhận của các đơn tạo trước đó.
4. Chọn nhóm nhận tại **Thông báo của tôi**. Admin có công tắc toàn hệ
   thống trong **Cấu hình → Thông báo nghiệp vụ**.
5. Nếu cần AI, admin nhập Base URL, API key và model trong cấu hình rồi
   kiểm tra kết nối. AI đang tắt; thông báo nghiệp vụ không cần AI.

Đã tạo tùy chọn mặc định cho 22 tài khoản nội bộ đang hoạt động, dùng
Asia/Ho_Chi_Minh, giờ yên lặng 19:00–07:00. Hai cron chạy mỗi phút. Chưa có
nhân viên nào liên kết tại thời điểm xác minh.

## Môi trường và bản sao lưu

- Addon thực chạy: `/srv/odoo-deploy/addons/dac_zalo_assistant`, phiên bản
  `18.0.2.3.0`; menu trỏ tới `static/description/icon.png`.
- Odoo: container `odoo-production`, image hiện hữu được giữ nguyên.
- Bridge: container `dac-zalo-bridge`, image `dac-zalo-bridge:b12d151`.
- Network: `bee-odoo-isolated`; không publish cổng bridge ra host/Internet.
- Odoo gọi `http://zalo-bridge:8095`; bridge gọi
  `http://odoo-production:8069`.
- Khóa dùng chung nằm trong `/opt/beeone-shared-secrets/zalo-bridge.env`,
  quyền `0600`, và trường cấu hình chỉ admin đọc được. Không có khóa thật
  trong Git. Session/SQLite dùng volume `dac-zalo-production-session`.
- Release, Compose và log triển khai:
  `/srv/odoo-deploy/releases/zalo-b12d151-20261102`.
- Sao lưu trước cài:
  `/srv/odoo-deploy/backups/zalo-b12d151-20261102` gồm database custom dump,
  toàn bộ Odoo data/filestore, source addon cũ, cấu hình và SHA-256.
  Catalog của database dump và integrity của hai archive gzip đã được
  kiểm tra. Dung lượng: dump 70.095.243 byte, Odoo data 165.849.475 byte,
  source 99.092.412 byte. Các file backup chỉ nằm trên máy chủ.

Chỉ cài addon mới; không upgrade các addon nền. 1.109 file sẵn có trên máy
chủ giữ nguyên hash; 18 file addon mới khớp release. Trước/sau xác minh đều
có 4.017 đơn hàng và 70 task. Không tạo đơn hoặc nhân viên thử trên production.

## Xác minh sau cài

- Module: `installed`, phiên bản `18.0.2.0.0`; kết nối và thông báo được bật,
  AI tắt, trạng thái Zalo `disconnected` chờ quét QR.
- Bridge `healthy`; `/health` trả 200, `/status` thiếu khóa trả 401. Odoo
  gọi `/status` có xác thực thành công. Không có cổng host được mở.
- Callback công khai `/dac_zalo/bridge/events` thiếu khóa trả 401.
- Hai cron active, chu kỳ một phút; đã chạy lúc 11:55:46 giờ Việt Nam.
- Sau nâng cấp icon và khởi tạo QR, cấu hình production trả về icon menu
  20.058 byte, QR đang lưu 16.801 byte, bridge `waiting_qr`, hai cron active,
  health check vẫn `pass`.
- Sao lưu riêng trước nâng cấp icon:
  `/srv/odoo-deploy/backups/zalo-icon-a61cb16-20261102/production_crm.dump`,
  catalog đã kiểm tra; file cũ của các thành phần addon được thay trong lần
  nâng cấp được lưu tại cùng thư mục.
- Mẫu tài khoản thực thuộc Sale/Design/Production/Manager: thấy menu, mở
  được view tùy chọn, chỉ tìm thấy preference của mình; đọc khóa bridge/AI
  bị từ chối. View admin có đầy đủ các nhóm thông báo.
- Health check và trang đăng nhập HTTPS trả 200; Chrome desktop/mobile
  không có page error hoặc tràn ngang. Resolver cục bộ từng lỗi phân giải;
  hai resolver 1.1.1.1 và 8.8.8.8 đều trả đúng IP máy chủ. Lượt kiểm tra HTTP
  và browser dùng hostname/TLS thật với IP được ánh xạ theo bản ghi đó.
- Không thấy ERROR/CRITICAL trong log Odoo ở cửa sổ kiểm tra sau restart.

Bước cấu hình đầu tiên gặp quyền đọc file khóa khi mount vào tiến trình
Odoo không chạy root; transaction đó không commit. Đã cấu hình lại bằng
cách tiến trình root đọc file rồi truyền riêng qua stdin tới Odoo shell,
giữ nguyên quyền `0600`; lần sau thành công. Bản sao lưu, log lỗi ban đầu
và log cấu hình thành công đều được giữ lại để truy vết.

Các kiểm thử tính năng trước triển khai nằm trong
[ZALO_VALIDATION.md](ZALO_VALIDATION.md). Chưa kiểm thử gửi hai chiều Zalo
thật, thông báo tạo đơn thật qua Zalo hoặc AI thật. Cần hoàn tất đăng nhập
và liên kết nhân viên trước khi kiểm tra các luồng đó.

Khi cần nâng cấp tiếp, dùng đúng mount/network đang chạy, sao lưu rồi nâng
cấp riêng addon bằng `-u dac_zalo_assistant`; Compose production nằm trong
release kể trên. Không dùng đường dẫn/network cũ trong tài liệu deploy
trước đây. Khi khôi phục, phải xét dữ liệu phát sinh sau bản backup trước
khi thay database/filestore; không tự restore đè một hệ thống đã mở lại.
