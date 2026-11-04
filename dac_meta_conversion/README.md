# Chuyển đổi Meta cho CRM Duy An

Nhân viên chốt mua trong Meta Business Suite, sau đó tạo đơn trong CRM.
Khi công ty bật tính năng, mỗi đơn mới có một sự kiện `Purchase` và được gửi
ngay sau khi giao dịch lưu đơn thành công. Không cần chờ xác nhận, đặt cọc hay
thanh toán. Đơn cũ không tự gửi khi bật tính năng hoặc chỉnh sửa.

## Cấu hình

Vào **Cài đặt → Chuyển đổi Meta**, chọn đúng công ty và nhập:

- **Dataset / Pixel ID** của doanh nghiệp.
- **Access Token Meta** có quyền gửi đến Dataset. Token Pancake không thay thế token Meta.
- **Facebook Page ID** khi dùng nguồn Messenger. Dataset phải được liên kết với Page đó.
- **Phiên bản Graph API**: mặc định `v26.0`, có thể đổi khi nâng phiên bản.
- **Mã sự kiện thử nghiệm**: lấy từ Test Events trong Events Manager. Để trống khi chạy thật.

Bật **Tự gửi Purchase khi tạo đơn**, lưu, rồi dùng **Kiểm tra kết nối**.
Nút này chỉ đọc Dataset, không phát sinh đơn hay chuyển đổi giả.
Đọc Dataset thành công chưa xác nhận quyền gửi hoặc khả năng phân bổ quảng cáo.

## Hai nguồn chuyển đổi

**Quảng cáo Messenger (Page ID + PSID)** là nguồn mặc định cho quảng cáo
click đến Messenger: `action_source=business_messaging`, `messaging_channel=messenger`.
Token của app cần quyền `page_events` theo hướng dẫn Meta. Các yêu cầu xét duyệt
app và quyền truy cập phụ thuộc cách doanh nghiệp tích hợp, cần kiểm tra trên
Meta App Dashboard của doanh nghiệp.

PSID là ID của khách trong một Page cụ thể, không phải số điện thoại,
UUID khách Pancake hay ID tài khoản Facebook chung. Odoo lấy PSID từ hội thoại
Facebook `INBOX` gắn với đơn. Nếu chưa gắn, hệ thống chỉ tự chọn khi có đúng
một hội thoại phù hợp của cùng khách, cùng Page và không phải hội thoại nội bộ.
Nếu có nhiều hội thoại, nhân viên cần dùng chức năng **Gắn Pancake** hiện có.
Có thể nhập PSID trực tiếp tại tab **Chuyển đổi Meta** của đơn.

Đồng bộ Pancake lưu `from.id` vào `sender_fm_id` và `type` vào
`conversation_type_fm`. Hội thoại đã đồng bộ từ trước cần đồng bộ metadata lại
để có hai trường này. Không suy diễn PSID từ UUID khách hàng hoặc ID hội thoại.
Không thể tự lấy PSID chỉ từ việc nhân viên mở Meta Business Suite hoặc nhập SĐT.

**Tin nhắn — so khớp bằng số điện thoại / email** gửi `action_source=chat`.
Dùng nguồn này khi chỉ có thông tin liên hệ và chuyển đổi diễn ra qua chat.
SĐT/email được chuẩn hóa rồi băm SHA-256. Số Việt Nam `0909333444` trở thành
`84909333444` trước khi băm. Số quốc tế khác cần mã quốc gia với `+` hoặc `00`.
Luồng này không thay thế PSID cho tối ưu mua hàng qua quảng cáo Messenger.
Không gửi IP/trình duyệt của nhân viên CRM làm định danh của người mua.

## Kiểm thử và chạy thật

1. Nhập Test Event Code và tạo một đơn thử có khách, sản phẩm, tổng tiền dương.
2. Với Messenger, gắn đúng hội thoại hoặc điền PSID thật của khách thử nghiệm.
3. Kiểm tra trạng thái trên đơn và dữ liệu trong **Events Manager → Test Events**.
4. Xác minh `Purchase`, giá trị và tiền tệ đúng. Theo tài liệu Meta, Test Event Code hỗ trợ quan sát trong Test Events; không bảo đảm sự kiện được tách khỏi nhắm mục tiêu và đo lường quảng cáo. Dùng Dataset thử nghiệm riêng nếu cần cách ly dữ liệu.
5. Xóa Test Event Code khi không cần đối chiếu nữa. Sự kiện sau đó không gắn mã kiểm tra.
6. Kiểm tra nguồn dữ liệu và lựa chọn tối ưu mua hàng phù hợp trong Ads Manager.
   Meta nhận sự kiện không đồng nghĩa mọi đơn đều được phân bổ về quảng cáo;
   việc ghi nhận còn phụ thuộc so khớp, tương tác quảng cáo, cửa sổ phân bổ và tài khoản.

Mã test, Dataset, Page và nguồn gửi được chụp lại khi tạo sự kiện. Đơn đã gửi
test không tự biến thành chuyển đổi thật sau khi xóa mã test.

## Trạng thái và chống trùng

- Mỗi đơn chỉ có một bản ghi sự kiện, ràng buộc bằng unique constraint.
- Payload được chụp trước lần gửi đầu, giữ cùng `event_id`, thời điểm, giá trị và đích nhận.
- Tạo hàng đợi cùng giao dịch tạo đơn; rollback thì không có sự kiện được gửi.
- Sau commit, worker dùng cursor riêng, khóa bản ghi và lưu trạng thái `sending`
  trước khi gọi mạng; cron xử lý phần còn lại mỗi phút.
- `Meta đã nhận` nghĩa là HTTP thành công và `events_received=1`.
- `Thiếu dữ liệu` giữ đơn trong CRM, cho biết lý do và kiểm tra lại định kỳ.
  Khi bổ sung khách, hội thoại, PSID hoặc dòng hàng trên đơn, hệ thống lên lịch kiểm tra ngay.
- HTTP 429 hoặc connect timeout được thử lại có giới hạn và tăng thời gian chờ.
- Read timeout, lỗi kết nối không rõ kết quả, HTTP 5xx hoặc phản hồi thành công
  không xác nhận số sự kiện đều vào **Cần đối soát với Meta**, không tự gửi lại.
- Worker bị ngắt sau lúc bắt đầu gửi cũng được chuyển sang cần đối soát sau 15 phút.
- Đơn hủy trước lần gửi được bỏ qua. Sự kiện quá 7 ngày bị dừng, không sửa giờ mua để gửi lại.

Meta nêu rõ Business Messaging CAPI không hỗ trợ loại trùng tự động.
Do đó không dùng `event_id` làm lý do gửi lại mù quáng sau timeout.
Nút gửi lại chỉ hoạt động với trạng thái chắc chắn chưa được nhận hoặc bị từ chối.
Đơn đã được Meta nhận không phát sinh Purchase lần hai khi sửa, đặt cọc hay thanh toán.
Thay đổi/hủy đơn sau khi đã gửi Purchase không tự sửa số liệu cũ trên Meta.

Token chỉ cho quản trị viên đọc và nhập; trường hiển thị được che.
Không ghi token hoặc toàn bộ phản hồi HTTP vào nhật ký/lỗi.
Quản lý xem sự kiện trong công ty được phép; sale xem trạng thái trên đơn có quyền truy cập.

## Hướng dẫn kết nối và lịch sử API (phiên bản 1.1)

Ứng dụng **Chuyển đổi Meta** có menu **Hướng dẫn kết nối**, **Lịch sử API** và
**Sự kiện chuyển đổi**. Giao diện cài đặt chia thành tám bước, có link mở trực tiếp
Business Suite, cài đặt doanh nghiệp, Events Manager, Meta App Dashboard và công cụ kiểm tra token.

Nút **Kiểm tra thông tin và kết nối** hoạt động khi tính năng gửi vẫn tắt.
Kiểm tra định dạng, đọc Dataset bằng token, và đọc `GET /{PAGE_ID}/dataset` để
đối chiếu Page – Dataset ở luồng Messenger. Kết quả và thời điểm được lưu lại.
Nếu thông tin thay đổi, kết quả cũ được đánh dấu cần kiểm tra lại.
Chỉ đọc thành công chưa chứng minh token được gửi Purchase hoặc chuyển đổi được phân bổ cho quảng cáo.
Kiểm tra không tự tạo sự kiện hay đơn hàng.

`dac.meta.api.log` lưu riêng từng lần GET kiểm tra và POST Purchase, gồm đơn hàng,
thứ tự lần gửi, thời gian, HTTP, số sự kiện Meta nhận, mã lỗi, mã truy vết,
payload / phản hồi đã che định danh và khóa. Không lưu Authorization header.
Nhật ký của lần gửi được lưu cùng trạng thái `sending` trước khi gọi mạng,
nên worker bị ngắt vẫn để lại bản ghi để đối soát. Timeout chưa rõ kết quả không tự gửi lại.
Quản lý chỉ đọc tóm tắt; quản trị viên đọc thêm payload đã che; không cấp quyền sửa/xóa log.
Lịch sử chi tiết bắt đầu từ lần nâng cấp này, không dựng lại giả các lần gọi trước đó.

## Triển khai và kiểm tra

Module: `dac_meta_conversion`, phụ thuộc `CRM_DAC`. Hai trường định danh nguồn
được khai báo trong extension. CRM chỉ ghi thêm định danh khi các trường tồn tại;
cần triển khai phần bổ sung đồng bộ cùng extension và khởi động lại Odoo.
Tính năng mặc định tắt, cấu hình tách theo công ty. Không chứa token thật trong mã.

Các bài kiểm thử Odoo nằm ở `tests/test_meta_conversion.py`; mock toàn bộ lệnh
gửi Meta. Dùng bản sao database, `--test-tags /dac_meta_conversion`,
`--test-enable --stop-after-init --max-cron-threads=0` và cổng HTTP riêng.

## Tài liệu đối chiếu ngày 02/11/2026

- [Meta: Conversions API for Business Messaging](https://developers.facebook.com/documentation/ads-commerce/conversions-api/business-messaging/)
- [Meta: Customer Information Parameters](https://developers.facebook.com/documentation/ads-commerce/conversions-api/parameters/customer-information-parameters/)
- [Meta: Server Event Parameters](https://developers.facebook.com/documentation/ads-commerce/conversions-api/parameters/server-event/)
- [Meta: Using the API](https://developers.facebook.com/documentation/ads-commerce/conversions-api/using-the-api/)
- [Meta Python Business SDK](https://github.com/facebook/facebook-python-business-sdk)
- [Pancake OpenAPI](https://developer.pancake.biz/openapi/openapi.yaml): endpoint avatar
  mô tả `from.id` của conversation là nguồn lấy PSID, cùng với `page_customers.psid`.


## Hướng dẫn Meta App (phiên bản 1.2)

Với luồng tự tích hợp Messenger CAPI, cần một app tích hợp Messenger có các quyền
Meta yêu cầu; có thể dùng lại app đáp ứng yêu cầu, không cần tạo app cho từng camp.
Trang thiết lập có lựa chọn tạo app mới / dùng app hiện có, App ID tham chiếu và
nút mở đúng app. App ID không thay Access Token và không được gửi trong Purchase.
CRM không yêu cầu App Secret, chưa có OAuth tự cấp/gia hạn token.

Tám bước: chuẩn bị doanh nghiệp/Page → tạo/chọn app → quyền/xét duyệt → lấy token
→ liên kết Dataset → kiểm tra kết nối → đối chiếu một đơn → bật/theo dõi. Có link
trực tiếp tới luồng tạo app, tài liệu quyền, Login for Business, token debugger,
Graph API Explorer, Events Manager và Ads Manager. Đường dẫn POST thiết lập
Dataset được tạo từ Page ID hợp lệ; CRM không tự chạy lệnh này.

Hướng dẫn phân biệt app/Business/Page/Dataset/PSID, nêu rõ quyền gửi sự kiện khác
quyền đọc hội thoại, và không coi API nhận thành công là doanh thu đã phân bổ.
Luồng URL Business Suite → PSID vẫn chưa được triển khai; hướng dẫn nói rõ giới hạn.

Tài liệu Meta đối chiếu ngày 02/11/2026:
- https://developers.facebook.com/documentation/development/create-an-app
- https://developers.facebook.com/documentation/ads-commerce/conversions-api/business-messaging/
- https://developers.facebook.com/documentation/facebook-login/facebook-login-for-business
- https://developers.facebook.com/docs/messenger-platform/get-started/
