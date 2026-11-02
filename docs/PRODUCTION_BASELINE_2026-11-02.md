# Mốc mã production — 02/11/2026

Repository chuẩn: `VikaKhang286/crm-duyan-v6`, nhánh `main`.
Commit đã đối chiếu: `ab61f5b529d67bf6d429250abfdb7d462697ab43`
(`update`, 02/11/2026 08:55:38, giờ Việt Nam).

Commit này đã đưa hai thay đổi giao diện đơn hàng đang có trên production vào
cả bản addon root và bản tương ứng dưới `addons/`:

- `dac_erp/static/src/css/backend/sale_order_form.css`
- `dac_erp/views/sale_order_ui_simplify_view.xml`

Đã so sánh SHA-256 của toàn bộ file thuộc tám addon root với source mà
container production đang mount. Phạm vi gồm mã, XML/CSV, tài liệu addon và
ảnh/static assets; bỏ qua cache Python, `.git` và `.DS_Store`.

| Addon | File ngoài `tests/` | Khớp production |
|---|---:|---:|
| CRM_DAC | 67 | 67 |
| Chameleon | 27 | 27 |
| dac_attendance | 39 | 39 |
| dac_erp | 186 | 186 |
| dac_openclaw | 17 | 17 |
| dac_report | 24 | 24 |
| home_menu | 16 | 16 |
| sale_ai_invoice_reader | 12 | 12 |
| **Tổng** | **388** | **388** |

Các con số trên đếm file có trong GitHub; có một file production dư được nêu
riêng bên dưới. Cả tám bản addon root và dưới `addons/` cũng được kiểm tra
đồng nhất trong checkout GitHub. Chi tiết và thời điểm lấy mẫu nằm trong
[production-baseline-2026-11-02.json](production-baseline-2026-11-02.json).

**Các khác biệt được giữ nguyên có chủ đích**

- Sáu file test của `dac_erp` khác production: `tests/__init__.py`,
  `test_cart_pricing.py`, `test_customer_address.py`, `test_payment_workflow.py`,
  `test_production_workflow.py`, `test_sprint1_task_workflow.py`.
- Bốn test chỉ có trên GitHub: `test_deposit_summary.py`,
  `test_deposit_sync.py`, `test_res_users_home_action.py`,
  `test_sale_back_permission.py`.
- Production có thêm `Chameleon/models/res_users.py`. File này không được
  import trong `models/__init__.py`; method `get_chameleon_ui_settings` trong
  đó không có caller JS. Chưa nhập file chưa được sử dụng này vào source chuẩn.

Giữ bộ test đang có trên GitHub; không chép đè bằng test trên production và
không xóa các test mới. Khi gộp hai nhánh tính năng, chạy các test liên quan
trên database thử nghiệm riêng.

**Giới hạn của mốc đối chiếu**

Đây là mốc đồng nhất source để phát triển tiếp, không chứng nhận toàn bộ
nghiệp vụ hoạt động đúng. Không đồng bộ database, cấu hình bí mật, trạng thái
cron hoặc dữ liệu khách hàng vào Git. Có source addon cũng không chứng minh
addon đã được cài trong database.

Đợt đồng bộ chỉ tạo checkout, nhánh và tài liệu; không cập nhật source đang
chạy, nâng cấp module hoặc restart production. Các lỗi vận hành/phân quyền
phát hiện trong audit trước cần bản sửa riêng.

Luồng phát triển tiếp theo: [PARALLEL_DEVELOPMENT.md](PARALLEL_DEVELOPMENT.md).
