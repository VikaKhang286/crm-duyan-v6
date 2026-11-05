# Changelog có phiên bản để bàn giao Khang

Mỗi yêu cầu độc lập hoặc phiên mới có chỉnh mockup được cấp mã
`CL-YYYYMMDD-NNN`, tính ngày theo giờ Việt Nam. Ví dụ `CL-20261105-001`.
Codex mở bản nháp khi bắt đầu, ghi nội dung đã làm và chốt khi hoàn thành
phạm vi yêu cầu hoặc Duy An nói “xong/chốt/bàn giao”.

Nếu Duy An đang tiếp tục đúng một bản nháp đã được chỉ rõ, dùng tiếp mã đó.
Yêu cầu bổ sung sau khi đã chốt tạo mã mới và ghi mã trước; không sửa lại bản
mockup hoặc nội dung bàn giao cũ. Phiên chỉ hỏi thông tin, không có yêu cầu
chỉnh sửa, không cần tạo gói bàn giao rỗng.

## Mỗi mã chứa gì

```text
mockup/changelogs/CL-20261105-001/
  CHANGELOG.md       Nội dung gửi Khang, trước/sau, nghiệm thu, giới hạn
  change.json        Nội dung có cấu trúc để Codex soạn changelog
  manifest.json      Mã, phiên bản, nguồn Git và SHA-256 kiểm tra snapshot
  mockup/            Bản giao diện đã chốt, chạy độc lập
    index.html
    src/
    assets/
    scripts/serve.mjs
    package.json
```

`mockup/changelogs/README.md` là danh mục các mã. Bản đã chốt có Git tag riêng,
ví dụ **`mockup-CL-20261105-001`**. Tag trỏ tới commit chứa đúng changelog và
snapshot; chỉ báo đã có trên GitHub sau khi push và xác nhận ref remote.

Snapshot chỉ lấy `index.html`, `src/`, `assets/` và server để xem mẫu. Không
chép database, env, cookie, hồ sơ thật, Git metadata hoặc toàn bộ addon.
Ghi chú và dữ liệu lưu trong trình duyệt không tự xuất hiện trong snapshot:
Codex cần chuyển các yêu cầu có liên quan vào changelog trước khi chốt.

## Những gì Codex viết cho mỗi lần bàn giao

- Yêu cầu của Duy An và lý do thay đổi.
- Trước đây thế nào, mockup đã thay đổi thế nào.
- Màn hình và vai trò liên quan.
- Tiêu chí nghiệm thu cụ thể cho Khang, gồm trường hợp rỗng/lỗi/mobile khi có liên quan.
- File Odoo tham chiếu, dữ liệu/API/quyền thật cần triển khai.
- Đã kiểm tra gì, còn mô phỏng hoặc chưa kiểm tra gì.
- Mã changelog, mã trước và phiên bản mockup cố định.

Trạng thái `ready_for_handoff` nghĩa là đã đóng gói để Duy An xem và gửi.
Nó không tự chứng nhận Duy An đã duyệt deploy production. Khang ghi tiến độ,
PR/commit triển khai và ngày deploy vào `IMPLEMENTATION.md` bổ sung bên cạnh;
không sửa changelog, `change.json` hoặc snapshot đã chốt.

## Các lệnh cho Codex

```sh
npm --prefix mockup run change:new -- --title "Tên thay đổi" --request "Yêu cầu của Duy An"
```

Điền `change.json` của mã vừa tạo. Các trường `before`, `after`,
`acceptanceCriteria`, `validation` là danh sách mô tả bắt buộc; `screens`
và `roles` dùng key có trong `src/data.js`. Các trường `implementationNotes`
và `knownLimits` ghi phần Khang cần xử lý và các giới hạn đã biết.

```sh
npm --prefix mockup run change:finalize -- CL-20261105-001
npm --prefix mockup run change:verify -- CL-20261105-001
npm --prefix mockup run change:pack -- CL-20261105-001
```

Lệnh finalize kiểm tra source, ghi mã phiên bản, tạo snapshot và khóa bằng
SHA-256. Lệnh verify phát hiện chỉnh sửa/thêm/bớt file trong snapshot hoặc
thay nội dung changelog. Không finalize lại bản đã chốt; tạo mã mới.

Sau khi kiểm tra, chỉ commit các file của yêu cầu hiện tại, bao gồm
`src/version.js`, thư mục changelog mới và danh mục. Sau đó:

```sh
npm --prefix mockup run change:tag -- CL-20261105-001
git push origin HEAD refs/tags/mockup-CL-20261105-001
```

Tag không bao giờ bị ghi đè. `sourceBaseCommit` trong manifest là commit nền
trước khi chốt, không được giới thiệu là commit chính xác của toàn bộ working
tree khi còn sửa chưa commit. Bản để Khang đối chiếu là snapshot với checksum
và Git tag sau khi commit.

## Duy An gửi gì cho Khang

Cách thuận tiện nhất là gửi **`mockup/exports/CL-20261105-001.zip`**, do lệnh
`change:pack` tạo. ZIP có changelog, manifest, nội dung yêu cầu và mockup chạy
độc lập. Thư mục exports được bỏ qua trong Git, có thể tạo lại từ bất kỳ mã đã chốt.

Hoặc gửi link GitHub tới `CHANGELOG.md` của **đúng Git tag**. Changelog có link
mở snapshot tương ứng; không gửi link nhánh luôn thay đổi làm bản đã duyệt.

Khang giải nén, mở terminal trong thư mục `CL-.../mockup/`, chạy `npm run dev`,
vào `http://localhost:4173`. Nếu cổng đang dùng, chạy `PORT=4174 npm run dev`.
Không cần Odoo hay npm install để xem mẫu.

Khi server local hiện tại đang chạy, có thể xem snapshot ngay tại
`http://localhost:4173/changelogs/CL-20261105-001/mockup/#login`.
Build GitHub cũng kèm các snapshot đã chốt; bản nháp không vào bản publish.

Anh có thể nói: **“Tôi chỉnh xong rồi, chốt changelog và mockup để gửi Khang.”**
Codex trả lại mã, file changelog, ZIP và link phiên bản GitHub đã xác nhận.
