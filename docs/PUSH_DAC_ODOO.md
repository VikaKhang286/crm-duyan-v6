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
Xem chi tiết: crm.duyan.vn/lead/0999999999-Ab3xK9pQ2wLz
```

Link `/lead/<số điện thoại>-<mã ngẫu nhiên>` **ai có link đều mở được, không cần đăng nhập**. Số điện thoại chỉ để nhìn là biết lead nào
(`+84…` đổi về `0…`; SĐT không dùng được thì link chỉ có mã). **Mã** mới là thứ bảo vệ: 72 bit, lưu ở `dac.web.lead.access_token`
(chỉ quản trị hệ thống đọc được); không dùng riêng số điện thoại làm khóa vì đoán được và một khách có thể có nhiều lead. Link chỉ
có số (`/lead/0999999999`) không mở được lead. Link kiểu `/lead/<mã>` đã gửi trước đây vẫn dùng được. Trang công khai chỉ có thông tin của lead và hai nút:
**Đã liên hệ** (nút chính, xanh) và **Liên hệ không được** (nút phụ, đỏ). Bấm nút ghi `contact_state` + `contact_state_at`
rồi hiện lại trang với trạng thái mới và **khóa cả hai nút** (bấm đúp hay gửi lại cũng giữ quyết định đầu tiên; muốn đổi thì nhân
viên sửa trong Odoo). Nhân viên đã đăng nhập và có quyền xem lead thì được chuyển thẳng sang
form đầy đủ trong Odoo (cũng có hai nút này ở đầu form, cột trạng thái ở danh sách và bộ lọc "Chưa liên hệ"). Trang công khai gửi
`X-Robots-Tag: noindex`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.

Lưu ý bảo mật: ai nhận được link đều xem được SĐT khách và đổi được trạng thái, nên đừng đăng link ra ngoài nhóm Zalo nhận tin. Lead
cũ (tạo trước khi có mã) được cấp mã khi lần đầu cần tới. Link kiểu cũ `/lead/<số>` (tin Zalo đã gửi trước đây) vẫn dùng được nhưng
bắt đăng nhập. Chữ đậm dùng `styles` của zca-js: bridge chỉ nhận danh sách trắng (hiện chỉ `bold`) và kiểm tra phạm vi.

## Nhắc nhở lead chưa được liên hệ

Lead mới có `remind_due_at` = 30 **phút làm việc** sau lúc nhận (`models/working_time.py`: thứ 2–thứ 7, 8:00–12:00 và 13:30–17:30,
giờ Việt Nam; nghỉ trưa, ngoài giờ và Chủ nhật không tính — khách đến 11:50 thì hạn 13:50, đến 20:00 thì tính từ 8:00 sáng hôm sau).
Cron "Lead website: nhắc lead quá hạn chưa liên hệ" (2 phút/lần, `dac.web.lead._cron_remind_uncontacted`) tìm lead quá hạn mà
`contact_state = new` và nhắc **một lần**: tin `@người_1 @người_2 Bạn đã liên hệ khách chưa ạ` được gửi dưới dạng **trả lời đúng tin
báo lead** trong từng nhóm đã nhận lead; những người được nhắc là người đã cài ở "Nhắc tên" cho thông báo lead (không cài ai thì vẫn
nhắc nhưng không gắn tên). Nếu tin báo lead chưa gửi đi được (bridge lỗi) thì chờ và thử lại ở lần cron sau, quá 24 giờ thì bỏ. Lead
tạo trước khi có chức năng (`remind_due_at` trống) không bị nhắc. Giờ làm việc và 30 phút là hằng số `REMIND_AFTER_MINUTES`,
`WORK_SHIFTS`, `WORK_DAYS`.

Kỹ thuật "trả lời tin": bridge ghi lại dữ liệu mọi tin do chính tài khoản đã gửi (lấy từ echo của Zalo vì `selfListen` bật) trong
bảng `quotes` 30 ngày; `/send` nhận thêm `quote_message_id` (= `message_id` bridge đã trả khi gửi tin gốc) và gửi kèm khung trích
dẫn của zca-js. Không tìm thấy dữ liệu tin gốc (vd tin gửi trước khi nâng cấp bridge) thì vẫn gửi nhắc, không có khung trích dẫn, và
phản hồi có `quoted: false`.

## Chọn nơi nhận lead và nhắc tên

**Lead website → Cấu hình kết nối → Nơi nhận lead trên Zalo → Thêm cuộc hội thoại nhận lead**: Odoo tự liệt kê mọi hội thoại của tài
khoản Zalo trợ lý; tick nhóm hoặc người cần nhận rồi Thêm. Hội thoại mới được bật sẵn thông báo “Khách mới từ form website”. Với nhóm,
mở form hội thoại (nút **Chọn người nhắc tên / quản lý nơi nhận**), tab **Thông báo nhận → Nhắc tên**, thêm dòng cho loại thông báo
lead và chọn một hoặc nhiều thành viên; họ sẽ được `@tag` ở dòng cuối tin lead. Chi tiết kỹ thuật: [ZALO_ASSISTANT.md](ZALO_ASSISTANT.md).

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
