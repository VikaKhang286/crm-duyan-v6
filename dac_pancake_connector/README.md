# Kết nối Pancake

Ứng dụng độc lập `dac_pancake_connector` gom thiết lập Chat/POS, kiểm tra kết nối và lịch sử API dùng chung. Phụ thuộc `CRM_DAC`; các model, XML ID, quyền, cron và dữ liệu đồng bộ cũ vẫn thuộc module cũ để giữ tương thích. Bản này không gỡ hoặc chuyển quyền sở hữu model cũ.

## Thiết lập

Vào **Kết nối Pancake → Thiết lập kết nối**. Token Chat dùng chung với cấu hình cũ (`page_fm.access_token`); lưu qua luồng settings CRM hiện có. API key/Shop ID POS theo công ty. Khi cài mới extension, hook chỉ sao chép `pancake.api_key` và `pancake.shop_id` cũ vào công ty chính; không tự chia sẻ cho mọi công ty, không xoay/xóa token, không gửi đơn.

Nút kiểm tra Chat chỉ GET danh sách Page; lỗi 103 hiển thị hướng dẫn cập nhật token. Nút kiểm tra POS chỉ GET danh sách shop và kiểm tra Page trong shop nếu đã nhập. Kết quả đọc không chứng minh quyền ghi đơn, cấu hình CAPI, SKU, kho hoặc tắt thông báo.

Chuẩn bị Page Facebook, kho POS và SKU dịch vụ tổng giá trị. Hoàn tất trên POS: gửi Purchase khi **Đã xác nhận**, tắt tự gửi hóa đơn và các quy tắc thông báo/Botcake liên quan, xác minh SKU không trừ kho hàng thật. Những ô xác nhận trong CRM là xác nhận của quản trị viên, không phải kết quả API tự kiểm chứng. Sau khi lưu cấu hình, kiểm tra kết nối lại.

## Đơn và webhook

`dac_meta_conversion` sử dụng kết nối để xuất đơn. `sale.order.pancake_export_reference` giữ tham chiếu ổn định; callback có cùng `custom_id` và `shop_id` chỉ gắn mã POS vào đơn gốc, không ghi đè dòng hàng CRM hay tạo thêm đơn. Hook tùy chọn trong vòng nhập POS cũ xử lý tương tự cho đồng bộ hàng loạt. Không đổi hành vi nhập đơn POS thông thường.

Các helper lấy API key/shop cho luồng nhập đơn cũ ưu tiên cấu hình công ty. Chỉ công ty chính có fallback sang tham số toàn cục cũ.

## Nhật ký và phân quyền

`dac.pancake.api.log` ghi phương thức, URL không query/token, thao tác, mã đơn, HTTP, mã lỗi số, thời gian và SHA-256 payload. Không lưu raw response, exception, khóa, thông tin khách hay nội dung hội thoại. Quản lý/admin đọc được log trong công ty được phép; không cấp quyền sửa, tạo hoặc xóa log qua giao diện. Các thao tác thiết lập/kiểm tra yêu cầu admin.

Tài liệu: https://pos.pages.fm/developers/api/en và https://developer.pancake.biz/.
