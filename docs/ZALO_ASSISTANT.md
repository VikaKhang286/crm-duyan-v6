# Trợ lý DAC qua Zalo

Addon `dac_zalo_assistant` và dịch vụ `services/zalo-bridge` tạo kênh chat
giữa nhân viên và tài khoản cá nhân “tro ly DAC”. Phần này phát triển riêng,
không sửa workflow đơn hàng hoặc addon của dev khác.

Production đã cài bản 18.0.2.0.0 ngày 02/11/2026 và bridge đang chạy; đang
chờ đăng nhập Zalo bằng QR. Chi tiết môi trường, sao lưu và kiểm tra:
[ZALO_PRODUCTION_2026-11-02.md](ZALO_PRODUCTION_2026-11-02.md).

**Cách sử dụng**

1. Admin bật kết nối trong **Trợ lý Zalo → Cấu hình**, nhập địa chỉ bridge và
   khóa kết nối giống biến môi trường `ZALO_BRIDGE_TOKEN` của dịch vụ.
2. Bấm **Đăng nhập Zalo bằng QR**, rồi **Lấy QR / cập nhật kết nối**. Quét QR
   bằng tài khoản “tro ly DAC” trên điện thoại và xác nhận đăng nhập. Bấm cập
   nhật lần nữa để xác nhận UID của tài khoản trợ lý trong Odoo.
3. Nhân viên kết bạn/cho phép nhắn tin với đúng tài khoản trợ lý trước, sau
   đó đăng nhập Odoo, mở **Trợ lý Zalo → Xác thực với trợ lý**,
   tạo mã và nhắn nguyên lệnh `/xac-thuc DAC-...` cho tài khoản trợ lý.
   Mã có hiệu lực 10 phút, dùng một lần; mã mới thay mã cũ. Không tự liên kết
   qua tên Zalo, số điện thoại hoặc AI. Một Zalo chỉ gắn với một người dùng.
4. `/congviec` xem tối đa 10 việc đang mở được giao trực tiếp cho mình;
   `/trogiup` xem hướng dẫn; `/ai <câu hỏi>` gọi AI nếu admin đã bật.
5. Nhân viên đọc hội thoại của mình trong Odoo; quản lý DAC và admin có thể
   giám sát các hội thoại trợ lý. **Nhắn qua trợ lý** đưa tin vào hàng đợi.
   Cron xử lý hàng đợi và AI mỗi phút. Nhân viên hủy liên kết tại **Liên kết
   của tôi**; khi hủy hoặc tài khoản Odoo bị khóa, trợ lý ngừng nhận/gửi cho họ.

**Cấu hình AI**

Admin nhập Base URL (bao gồm `/v1` nếu nhà cung cấp dùng đường dẫn này), API key
và tên model. Tích hợp gửi `POST <base-url>/chat/completions` với `messages` và
`max_tokens`; không yêu cầu SDK OpenAI hoặc một tên model cố định. Bấm kiểm tra
kết nối để gửi một câu hỏi thử.

Mặc định AI chỉ chạy khi có `/ai`; có tùy chọn trả lời tự động. Tối đa 50 lần
xử lý AI/người/ngày UTC, gồm cả yêu cầu thất bại; mỗi câu trả lời tối đa 1000
token theo tham số gửi nhà cung cấp. Khóa được lưu trong model cấu hình riêng,
chỉ nhóm Settings được đọc; không lưu vào `ir.config_parameter` mà các vai trò
DAC hiện có quyền đọc rộng.

AI chỉ nhận tối đa 12 tin nhắn trong cùng hội thoại, mỗi tin tối đa 4000 ký tự,
và tùy chọn tối đa 10 việc giao trực tiếp cho nhân viên. Truy vấn công việc dùng
quyền của nhân viên, không dùng quyền admin. Nhà cung cấp AI nhận những dữ liệu
này; không đưa thông tin thanh toán/khách hàng không cần thiết vào prompt.
AI chưa có công cụ ghi dữ liệu, thay trạng thái đơn hay thực hiện thanh toán.

**Triển khai dịch vụ**

`zca-js` được khóa ở phiên bản 2.2.0 và có `package-lock.json`. Node.js 24 được
dùng để có hàng đợi SQLite bền vững. File cookie/IMEI/user-agent chỉ nằm trong
volume session, quyền file `0600`, không được trả qua API và không commit Git.
Chỉ chạy **một** bridge cho một tài khoản; không scale nhiều replica.

Theo [tài liệu zca-js](https://github.com/RFS-ADRENO/zca-js), đây là thư viện
không chính thức cho tài khoản cá nhân; tác giả lưu ý rủi ro tài khoản bị khóa
và giới hạn một web listener/tài khoản. Không mở cùng tài khoản trên Zalo Web
trong khi bot hoạt động. Dùng điện thoại để quét/xác nhận QR.

Trong checkout mới, cấu hình Odoo local riêng và đặt `ZALO_BRIDGE_TOKEN` thành
chuỗi ngẫu nhiên ít nhất 32 ký tự trong môi trường (không commit). Sau đó:

```sh
docker compose -p dac-zalo-dev -f docker-compose.yml -f compose.zalo.yml up -d --build
```

Đổi các cổng host trong cấu hình local nếu đang có một stack Odoo khác chạy
cùng máy; project name không tự đổi cổng. Compose bổ sung không publish cổng
bridge ra Internet. Odoo dùng `http://zalo-bridge:8095`; bridge dùng
`http://web:8069` trong cùng network. Với server dùng tên container/network
khác, cấu hình URL và network theo thực tế trước khi chạy.

Cài/nâng cấp addon **trên database phát triển riêng**. Sau đó thiết lập cấu hình
bằng admin. Giao diện mặc định tắt kết nối và AI; chưa có cookie hay API key
thật trong mã. HTTP không có TLS chỉ phù hợp mạng Docker riêng hoặc loopback;
đường kết nối qua mạng khác cần HTTPS.

**Độ tin cậy và giới hạn đồng bộ**

- Đồng bộ tin nhắn riêng (DM), cả hai chiều, của nhân viên đã liên kết. Không
  nhập nhóm Zalo hoặc hội thoại của người chưa xác thực vào Odoo.
- Nội dung text được lưu; ảnh/file/sticker hiện lưu nhãn loại tin, chưa tải
  nội dung đính kèm hoặc gửi ảnh/file. Chưa đồng bộ sửa/thu hồi tin hoặc phản
  ứng. Không coi đây là đồng bộ đầy đủ media và mọi sự kiện Zalo.
- Nhận live và một lô lịch sử gần đây do Zalo trả khi nối listener. Chỉ nhập
  lịch sử từ lúc liên kết và không gọi AI/trả lời tự động cho tin lịch sử.
  Đây không phải export toàn bộ lịch sử Zalo; Zalo có thể không trả đủ tin
  trong khoảng bot offline lâu.
- SQLite giữ callback chưa được Odoo xác nhận, kể cả khi restart; hàng đợi
  tối đa 10000 sự kiện. Khi đầy hoặc lỗi ghi, trạng thái chuyển lỗi để vận hành
  kiểm tra. Payload của người chưa xác thực có thể tạm nằm ở spool, rồi bị
  loại bỏ khi Odoo xử lý và xác nhận; không thành hội thoại nhân viên.
- Odoo dùng khóa sự kiện duy nhất; bridge dùng request ID và fingerprint bền
  vững để ngăn gửi lại do timeout HTTP. Trường hợp Zalo có thể đã nhận tin nhưng
  không trả xác nhận được đánh dấu **Cần kiểm tra gửi**, không tự gửi lại.
- Admin theo dõi lỗi trên màn hình tin nhắn. Mất mạng nội bộ được thử lại tối
  đa 5 lần với backoff; AI lỗi được ghi trạng thái và phản hồi ngắn, không lộ
  body lỗi nhà cung cấp hoặc token.
- Sau khi listener bị đóng, kiểm tra trạng thái, bấm ngắt kết nối rồi đăng
  nhập lại nếu thư viện không tự nối lại. Không tự đăng nhập vòng lặp khi tài
  khoản cần xác nhận.
- Chưa có tự xóa hội thoại Odoo hoặc lịch retention cho metadata gửi; cần
  thống nhất thời hạn lưu khi triển khai chính thức. Sao lưu volume cookie
  và database phải được bảo vệ như thông tin đăng nhập.

Thông báo nghiệp vụ đã được triển khai, có bật/tắt chung và theo từng nhân
viên. Nâng cấp addon rồi chọn các nhóm cần nhận; hướng dẫn chi tiết: [ZALO_NOTIFICATION_IDEAS.md](ZALO_NOTIFICATION_IDEAS.md).

Kết quả và cách chạy kiểm thử: [ZALO_VALIDATION.md](ZALO_VALIDATION.md).
