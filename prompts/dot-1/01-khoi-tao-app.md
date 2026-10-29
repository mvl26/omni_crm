# D1-01 · Khởi tạo app `omni_crm`

**Phụ thuộc:** bench Frappe v15 có app `crm`, site test riêng. **Thời lượng dự kiến:** nửa ngày.

```
Bạn là agent phát triển cho Miyano Việt Nam. Nhiệm vụ: khởi tạo app Frappe mới `omni_crm` trong bench {{BENCH}}, đặt nền cho toàn bộ dự án tích hợp email, Zalo OA, tổng đài với Frappe CRM.

Bộ kit đặc tả nằm ở {{KIT}}. Đọc trước: {{KIT}}/CLAUDE.md, {{KIT}}/README.md, {{KIT}}/docs/00-tong-quan.md, {{KIT}}/docs/01-kien-truc.md.

Việc cần làm:
1. Kiểm tra môi trường, ghi kết quả vào báo cáo:
   - `bench version` → Frappe phải là v15; app `crm` đã có trong bench.
   - Site {{SITE_TEST}} tồn tại, đã cài `crm`. Không có thì DỪNG và báo, không tự tạo site.
   - Python, MariaDB, Redis đang chạy (`bench doctor`).
2. Tạo app: `bench new-app omni_crm` với Title "Omni CRM", Description "Tích hợp đa kênh email, Zalo OA, tổng đài với Frappe CRM, kèm bot AI", Publisher "Miyano Việt Nam", License {{LICENSE}}. Nếu lệnh hỏi tương tác, trả lời đúng các giá trị trên.
3. Chép từ bộ kit vào gốc repo app (`apps/omni_crm/`): `CLAUDE.md`, `README.md` (đổi tên thành `KIT_README.md`), thư mục `docs/`, `prompts/`, `eval/`, `mau/`. Không chép gì khác.
4. Đảm bảo module "Omni CRM" có trong `modules.txt`.
5. Dựng khung thư mục Python đúng `docs/01-kien-truc.md` mục 3 (các gói có `__init__.py`, tệp rỗng có docstring một dòng nói vai trò). Chưa viết logic.
6. `hooks.py`: thêm `required_apps = ["crm"]`, khung `doc_events`, `scheduler_events`, `fixtures` theo `docs/01` mục 6 nhưng ĐỂ DƯỚI DẠNG CHÚ THÍCH (chưa kích hoạt) — các prompt sau sẽ bật từng mục.
7. Tạo hai Role qua fixtures: `Omni Admin`, `Omni Knowledge Editor` (desk access = 1). Thêm fixture Role vào `hooks.py` (lọc đúng 2 role này).
8. Cài app lên {{SITE_TEST}}: `bench --site {{SITE_TEST}} install-app omni_crm`, rồi `migrate`.
9. Thiết lập chất lượng code: kiểm tra app mới đã có `.pre-commit-config.yaml` (ruff) và workflow CI trong `.github/workflows/`. Nếu workflow CI có sẵn: sửa để cài cả app `crm` (cùng nhánh version-15 hoặc bản tương thích) trước khi chạy test `omni_crm`. Nếu không có: tạo workflow tối thiểu làm việc đó. Không cần chạy được trên GitHub ngay; cần đúng cú pháp.
10. Viết test khói `omni_crm/tests/test_smoke.py`: app đã cài, module "Omni CRM" tồn tại, 2 role tồn tại, `crm` đã cài.
11. Git: khởi tạo nếu chưa có, commit đầu tiên `chore: khởi tạo app omni_crm`. Nếu có {{REPO_URL}}: thêm remote, KHÔNG push (người duyệt sẽ push).

Ràng buộc:
- Không sửa app `crm`, `frappe`.
- Không tạo DocType nào ở bước này.
- Không đưa bí mật vào repo.

Hoàn thành khi:
- `bench --site {{SITE_TEST}} run-tests --app omni_crm` qua (dán output).
- `bench --site {{SITE_TEST}} migrate` sạch.
- Cây thư mục khớp `docs/01` mục 3 (dán `tree -L 3 omni_crm/omni_crm` hoặc tương đương).

Kết thúc bằng báo cáo theo mẫu ở `prompts/chung/00-cach-dung-prompt.md` mục 4.
```

## Người duyệt kiểm tra

1. Mở `apps/omni_crm/CLAUDE.md` — có đúng nội dung bộ kit.
2. `bench --site <site_test> list-apps` có `omni_crm` và `crm`.
3. Test khói xanh trong báo cáo.
4. `{{LICENSE}}` anh đã chọn trước khi chạy (ví dụ `agpl-3.0` hoặc giấy phép nội bộ — tham khảo pháp chế vì app `crm` dùng AGPL-3.0).
