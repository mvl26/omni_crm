# D1-02 ★ · Mô hình dữ liệu Đợt 1

**Phụ thuộc:** D1-01. **Thời lượng dự kiến:** 1 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/01-kien-truc.md, docs/02-mo-hinh-du-lieu.md (đọc kỹ toàn bộ), docs/04-dinh-danh-va-chong-trung.md mục 4, docs/09-cong-tac-va-cau-hinh.md, docs/12 mục A (Q7 — thời hạn lưu: dùng mặc định nếu chưa chốt).

Nhiệm vụ: tạo các DocType và custom field của ĐỢT 1, đúng đặc tả.

Bước 0 — xác nhận giả định G1 (docs/12 mục B):
- Đọc {{BENCH}}/apps/crm/crm/fcrm/doctype/crm_lead/crm_lead.json, crm_call_log, crm_task, crm_note, crm_lead_status (và DocType liên quan Deal).
- Ghi vào docs/12 mục C: tên các trường SĐT, email, tên, tổ chức, nguồn, người phụ trách, trạng thái, cờ đã chuyển Deal; danh sách trạng thái Lead mặc định; trường tham chiếu (reference_doctype/name hay khác) của Call Log, Task, Note.
- Nếu có trường của CRM trùng ý nghĩa với custom field dự kiến, KHÔNG tạo trùng; ghi chú và dùng trường có sẵn.

Bước 1 — tạo DocType (module "Omni CRM"), chỉ những cái thuộc Đợt 1:
- Omni Settings (Single), Omni Channel, Omni Identity, Omni Inbound Message, Omni Outbound Message, Omni Duplicate Candidate.
- Chỉ tạo các trường KHÔNG ghi chú Đợt 2/Đợt 3 trong docs/02. Cụ thể: Omni Settings bỏ khối "Bot (Đợt 2)" (kể cả `score_rules`, `hot_lead_threshold`, các `*_prompt_version`, `kb_version`), `missed_call_callback_minutes`, `callback_fallback_user`, `holidays`, `recording_enabled`, `stt_*`, `bot_active_hours`, các trường handoff, `fit_*` và `prospect_import_enabled`/`fit_scoring_enabled` (D1-13), `response_sla_minutes`/`care_session_gap_hours`/`team_lead_role`/`holidays` (D1-12); Omni Channel bỏ `agent_map`; Omni Identity bỏ `bot_paused_until`; Omni Outbound Message bỏ `bot_draft`. Các trường Đợt 1 dễ sót, phải có: `Omni Inbound Message.identity_key`, `.communication`; `Omni Outbound Message.next_attempt_at`; `Omni Identity.phone_requested_at`; `Omni Settings.assignment_fallback_users`, `raw_payload_retention_days`, `recording_retention_days`.
- Đặt tên bản ghi: Omni Inbound Message `OIN-.YYYY.-.########`, Omni Outbound Message `OOUT-.YYYY.-.########`, Omni Duplicate Candidate `ODUP-.YYYY.-.#####`, Omni Identity autoname hash (khóa nghiệp vụ là `identity_key` duy nhất), Omni Channel theo `channel_name`.
- Ràng buộc duy nhất ở mức DB cho: `Omni Inbound Message.dedupe_key`, `Omni Identity.identity_key`, cặp (`lead_a`, `lead_b`) của Omni Duplicate Candidate (dùng `on_doctype_update` + `frappe.db.add_unique`).
- Chỉ mục ghép theo docs/02 mục 5 qua `on_doctype_update` + `frappe.db.add_index`.
- Bật `track_changes` cho Omni Settings, Omni Channel.
- Quyền theo docs/02 mục 16 (phần Đợt 1): ở mức DocType, Sales User chỉ có quyền Đọc; việc tạo tin ra và quyết định nghi trùng đi qua API whitelisted (prompt sau) có kiểm quyền theo Lead.
- Trường Password: không bao giờ hiện trong list view, không in trong `__repr__`/log.
- Controller: validate cơ bản (ví dụ `Omni Channel` loại Zalo phải có app id; `Omni Duplicate Candidate` tự sắp `lead_a < lead_b`; `retry_backoff_minutes` là danh sách số nguyên dương).

Bước 2 — Custom Field trên CRM Lead (fixtures, module "Omni CRM"): các trường Đợt 1 ở docs/02 mục 15: `omni_first_channel`, `omni_last_inbound_at`, `omni_manual_fields`, `omni_phone_e164`, `omni_email_norm`, `omni_merged_into`. Ẩn các trường kỹ thuật; `omni_phone_e164`, `omni_email_norm` có search_index. Chọn `insert_after` hợp lý dựa trên bước 0. Bật fixtures Custom Field trong hooks.py (lọc module "Omni CRM"). Custom field trên Communication/Comment (`omni_inbound_message`, `omni_outbound_message`) CHƯA tạo ở đây — D1-06 tạo sau khi chọn cách hiển thị.

Bước 3 — `flags.py`: cài đặt các hàm đọc công tắc của Đợt 1 theo docs/09 mục 2 (`channel_enabled`, `outbound_enabled`, `sandbox`). Mặc định Omni Settings sau khi cài: `sandbox_mode = 1`.

Bước 4 — test (`omni_crm/tests/test_models.py`, `test_flags.py`):
- Tạo/xóa từng DocType; ràng buộc duy nhất thật sự chặn ở DB (bắt đúng loại lỗi).
- Duplicate Candidate tạo với lead_a > lead_b được sắp lại; tạo cặp đảo bị chặn.
- Custom field tồn tại trên CRM Lead sau migrate.
- Password không xuất hiện trong `as_dict()` khi lấy qua API người dùng thường (kiểm tra bằng frappe.get_doc với user Sales User).
- Công tắc: tắt kênh thì `channel_enabled` trả False ngay sau khi lưu (cache được xóa).

Ràng buộc: không tạo DocType Đợt 2/3; không sửa app `crm`.

Hoàn thành khi:
- `bench --site {{SITE_TEST}} migrate` sạch, `run-tests --app omni_crm` xanh (dán output).
- docs/12 mục C có dòng G1 kèm đường dẫn tệp đã đọc.
- `bench --site {{SITE_TEST}} export-fixtures --app omni_crm` tạo đúng fixtures, đã commit.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Mở Desk → Omni Channel: tạo một kênh Zalo thử, trường bí mật hiện dạng ẩn.
2. Mở một CRM Lead trong Desk: có các trường `omni_*` (ẩn các trường kỹ thuật).
3. Đọc mục C của `docs/12`: tên trường CRM đã được xác nhận cụ thể, không mơ hồ.
