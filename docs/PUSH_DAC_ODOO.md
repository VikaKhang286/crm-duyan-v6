# Push DAC Odoo — đẩy dữ liệu duyan.vn về Odoo và báo vào Zalo

```
duyan.vn (plugin Push DAC Odoo)
   │  POST /dac_push/v1/events  (HMAC-SHA256, chống phát lại 300s)
   ▼
Odoo addon dac_push ── lưu dac.push.event (chống trùng) ── _handle_<event_type>
   │                                                          └─ web_lead.created → dac.web.lead
   ▼  dac.zalo.channel._notify('web_lead.created', text, key)
Trợ lý Zalo (dac_zalo_assistant) ── hàng đợi dac.zalo.channel.message ── bridge /send ── nhóm/chat Zalo
```

Không dùng OpenClaw. Tin đi qua bridge `zca-js` của Trợ lý Zalo, bằng tài khoản “tro ly DAC”.

## Cài đặt

1. **Odoo**: `-u dac_zalo_assistant` rồi `-i dac_push`. Vào **Push DAC Odoo → Cấu hình**, bật, bấm **Tạo khóa mới**,
   copy địa chỉ nhận và khóa.
2. **Bridge Zalo**: build lại `services/zalo-bridge` (có thêm `GET /threads` và `thread_type` khi gửi). Tài khoản trợ lý
   phải là thành viên của nhóm muốn nhận tin.
3. **WordPress**: chép `wp-content/plugins/push-dac-odoo/` lên duyan.vn, kích hoạt, vào **Cài đặt → Push DAC Odoo**,
   dán địa chỉ + khóa, bấm **Gửi thử kết nối**. Có thể đặt `DAC_PUSH_ODOO_URL` / `DAC_PUSH_ODOO_SECRET` trong `wp-config.php`.
4. **Chọn nơi nhận**: Odoo → **Trợ lý Zalo → Thông báo → Tìm nhóm / người Zalo** → gõ tên nhóm/người → **Tìm** → bật chọn → **Thêm**.
   Mở hội thoại vừa thêm, tab **Thông báo nhận**, tick loại thông báo (vd “Khách mới từ form website”). **Gửi tin thử** để kiểm tra.
   Quản lý toàn bộ ở **Trợ lý Zalo → Thông báo → Đích nhận thông báo**.

Nếu hosting không phân giải được tên miền Odoo (lỗi “Resolving timed out”; đã gặp với Hostinger vì resolver nội bộ không với tới
name server `zonedns.vn`), điền **IP máy chủ Odoo** (hoặc hằng số `DAC_PUSH_ODOO_IP`) để plugin bỏ qua bước tra DNS; tên miền và TLS giữ nguyên.
Nếu Odoo đổi IP phải cập nhật lại.

Production hiện chạy Bee Contact 2.4.38 (chưa có hub/event bus). Plugin dùng các hook `bcb_bee_form_submitted`,
`bcb_quote_success`, `bcb_callback_success`, `bcb_lead_created` nên chạy được cả bản cũ lẫn mới.

## Dữ liệu của mỗi lead (`web_lead.created`)

`name`, `phone`, `email`, `company`, `message` (đều có thể rỗng — form chỉ có số điện thoại vẫn hợp lệ, tin Zalo ghi rõ
“Khách để lại số điện thoại”), `items[]`, `page_url`, `page_title`, và:

- `fields[] {label, value}` — **mọi** thông tin khác khách đã nhập (ghi chú, yêu cầu bổ sung, kích thước…), nhãn lấy từ cấu hình form;
- `visited[] {url, title, at}` — các trang khách đã xem trong 24 giờ gần nhất (tối đa 15), lấy từ Bee CRM theo `client_id`
  nên không thêm cookie hay script theo dõi; không có Bee CRM thì để trống;
- `source{utm_source, utm_medium, utm_campaign, referrer, landing}` — nguồn truy cập.

Odoo lưu đầy đủ ở lead (tab “Thông tin khách đã nhập”, “Trang khách đã xem”, URL đầy đủ). **Tin Zalo gọn**, chỉ gồm:

```
**Khách yêu cầu tư vấn**          ← in đậm
👤 Tên (nếu có)
📞 0999999999
➕ Yêu cầu: hạng mục / sản phẩm / dịch vụ khách chọn (nếu có)
💬 Ghi chú hoặc yêu cầu thêm khách nhập (nếu có)
Xem chi tiết: crm.duyan.vn/lead/736
```

Link rút gọn `/lead/<id>` chuyển tới trang chi tiết của lead trong Odoo (chưa đăng nhập thì chuyển sang trang đăng nhập rồi quay lại; quyền xem
vẫn theo ACL). Chữ đậm dùng `styles` của zca-js: bridge chỉ nhận danh sách trắng (hiện chỉ `bold`) và kiểm tra phạm vi.

## Thêm loại dữ liệu mới

**WordPress** (plugin/theme khác):
```php
add_action('dac_push_register_sources', function () {
    DAC_Push::register_source('woo_orders', 'Đơn WooCommerce', 'Báo khi đơn được thanh toán', function () {
        add_action('woocommerce_payment_complete', function ($order_id) {
            DAC_Push::send('woo.order_paid', 'order:' . $order_id, array('order_id' => $order_id));
        });
    });
});
```
`event_id` phải ổn định cho một sự kiện (Odoo chống trùng theo nguồn + event_id).

**Odoo**: kế thừa `dac.push.event`, thêm method `_handle_woo_order_paid(self)` (dấu chấm → gạch dưới); đăng ký
`dac.zalo.event.type` (file data) để loại thông báo hiện trong màn hình chọn; gọi
`self.env['dac.zalo.channel']._notify(code, text, key)`. Sự kiện chưa có bộ xử lý được giữ ở trạng thái “Không có bộ xử lý”,
có thể **Xử lý lại** sau khi triển khai handler.

## Bảo mật

- Khóa lưu ở model `dac.push.config` (chỉ nhóm Settings đọc), không để trong `ir.config_parameter`.
- Route trả 503 khi chưa bật hoặc chưa có khóa; 401 khi sai chữ ký hoặc timestamp lệch quá 300 giây; 413 khi body > 64KB.
- Dữ liệu từ khách được làm sạch (bỏ ký tự điều khiển/NUL, cắt độ dài, giới hạn độ sâu) cả ở plugin lẫn Odoo.
- Nhật ký plugin chỉ giữ dữ liệu gốc khi chưa gửi được (để bấm **Gửi lại**); gửi xong thì xóa.
- Tin Zalo chứa số điện thoại khách: chỉ thêm các nhóm nội bộ.
- Tin gửi vào nhóm có trạng thái **Cần kiểm tra gửi** (bridge không xác nhận) sẽ không bao giờ tự gửi lại.

## Kiểm thử đã chạy (11/11/2026, DB `dac_zalo_test`)

- Odoo: `dac_push` 10 test + `dac_zalo_assistant` kênh/đích 10 test đạt.
- Bridge Node: 14 test đạt (3 test mới: tìm hội thoại + cache, giới hạn kết quả, gửi nhóm).
- Plugin PHP: 33 kiểm tra đạt (`php tests/run.php`).
- Hai đầu thật: code plugin → HTTP → Odoo: 2 lead + 1 ping xử lý; gửi lặp không tạo trùng; khóa sai → 401; NUL trong tên bị lọc.
- Giao diện (Chrome headless): tìm → chọn → thêm hội thoại, gán thông báo, cấu hình Push; không có lỗi JS.
- **Chưa kiểm tra**: gửi tin Zalo thật vào nhóm và tìm hội thoại bằng tài khoản Zalo thật (cần quét QR); cài lên production.
- Một test có sẵn của `dac_zalo_assistant` (`test_uncertain_delivery_is_not_silently_retried`) lỗi trên image `odoo:18.0` mới
  (harness test không chấp nhận `timeout` dạng tuple). Lỗi nằm ở code không đổi (`assistant.py`), có từ trước.

## Menu và giao diện (từ addon 18.0.1.1.0)

Sidebar chia 3 cấp: **Ứng dụng → Nhóm → Mục**, mỗi mục có icon (khai báo bằng `web_icon="fa-..."` trên `ir.ui.menu`,
sidebar Chameleon đọc trường này nên addon nào cũng tự đặt icon được).

- **Lead Website** → *Lead* (Lead từ website: kanban/danh sách/form) · *Kết nối* (Cấu hình kết nối, Dữ liệu nhận được).
- **Trợ lý Zalo** → *Kết nối* (Tài khoản & kết nối, Liên kết nhân viên) · *Thông báo* (Đích nhận thông báo, Tìm nhóm / người Zalo,
  Loại thông báo) · *Theo dõi* (Hội thoại, Theo dõi tin nhắn, Nhật ký thông báo) · *Của tôi* (Xác thực, Liên kết, Thông báo của tôi).

Màn **Tài khoản & kết nối** là trung tâm kết nối: thẻ trạng thái Zalo / AI / cầu nối / Website; Push tự thêm thẻ "Website duyan.vn".
