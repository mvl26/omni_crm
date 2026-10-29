# D1-04 ★ · Hàng đợi, worker, thử lại, tác vụ quét

**Phụ thuộc:** D1-03. **Thời lượng dự kiến:** 1,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/03-cong-tiep-nhan-va-hang-doi.md (toàn bộ, đặc biệt mục 3–7), docs/01-kien-truc.md mục 5.

Nhiệm vụ: xây worker xử lý tin vào theo từng khách, có gộp tin, khóa, thử lại, và tác vụ quét. Phần định danh khách và ghi CRM CHƯA làm: dùng hai điểm nối có cài đặt tối thiểu, các prompt sau thay thế:
- `identity/resolver.py::resolve(msgs) -> tuple[str, str | None]` (identity name, lead name): tạm thời chỉ get_or_create Omni Identity theo identity_key, chưa gắn Lead (trả None).
- `crm_bridge/timeline.py::append(lead, msgs) -> None`: tạm thời không làm gì khi lead là None.
Ghi chú `# Thay thế ở D1-05` / `# Thay thế ở D1-06` tại hai chỗ này.

Bước 0 — xác nhận G5, G6 trên bench này: cú pháp lấy khóa Redis qua `frappe.cache` ở v15; cách khai báo hàng đợi riêng `omni` trong common_site_config.json và chạy worker cho nó. Ghi vào docs/12 mục C. Cấu hình hàng đợi `omni` cho môi trường dev (không đụng site khác ngoài {{SITE_TEST}} và bench dev).

Việc cần làm:
1. `pipeline/locks.py`: context manager `identity_lock(identity_key, timeout=120, wait=30)`; không lấy được khóa thì ném `LockBusy`.
2. `pipeline/retry.py`: `mark_retry(docs, error, permanent_hint=False)` dùng chung cho tin vào và tin ra, theo bảng docs/03 mục 5 (đọc `max_attempts` mặc định 3, `retry_backoff_minutes` mặc định "1,5": phần tử n = chờ sau lỗi thứ n); phân loại lỗi tạm thời (`TransientError`, lỗi mạng, `frappe.QueryTimeoutError`, `TimestampMismatchError`, deadlock) và lỗi khác; ghi `last_error` đã lọc bí mật (hàm `redact(text)` loại token/khóa dạng chuỗi dài, các giá trị Password đang cấu hình).
3. `pipeline/worker.py::process_identity(identity_key)` đúng mã giả docs/03 mục 3: debounce đọc từ Settings (test truyền 0), khóa, vòng tối đa 5 lần, batch_id, commit tại các điểm đã nêu, rollback khi lỗi. `LockBusy` → enqueue lại chính nó sau 30 giây (dùng `frappe.enqueue(..., enqueue_after_commit=True)` với tham số trì hoãn nếu có; nếu không, đánh dấu để tác vụ quét đẩy lại — chọn cách và ghi ADR ngắn).
   - Tách hàm `_process_batch(identity_key, msgs, batch_id)` thuần để test.
   - Điểm nối hậu xử lý cho Đợt 2: hàm `post_batch_hooks(batch_id, lead)` hiện trống, có docstring.
4. `pipeline/sweeper.py`: `requeue_stuck()` đúng docs/03 mục 6 (mục 1–3 và 5; mục 4 về tin ra để trống có docstring, D1-09 bổ sung); tìm tin theo `identity_key`; ghi số lượng vào cache để màn hình giám sát (D1-10) đọc. Bật lịch `*/5 * * * *` trong hooks.py.
5. API cho quản trị: `api/admin.py::reprocess(inbound_names: list)` (chỉ Omni Admin) đưa tin Manual/Retry về New và enqueue.

Test (`tests/test_worker.py`, `tests/test_retry.py`, `tests/test_sweeper.py`, `tests/test_locks.py`) — chạy worker đồng bộ:
- 3 tin New của một khách → 1 lượt, cùng batch_id, đều Done.
- Tin mới đến khi đang xử lý (giả lập bằng cách chèn bản ghi trong lúc `_process_batch` chạy) → vòng sau xử lý tiếp, không bỏ sót.
- Lỗi tạm thời 2 lần rồi thành công → Done, attempts=2, next_attempt_at đúng khoảng.
- Lỗi 3 lần → Manual.
- Rollback thật: lỗi giữa chừng thì không có ghi dở dang (kiểm tra Identity không bị tạo nửa vời nếu lỗi xảy ra sau khi tạo — hoặc chứng minh việc tạo lại là an toàn).
- Hai lần gọi `process_identity` song song cho cùng khách (thread) → chỉ một lượt xử lý thật, lượt kia LockBusy và được hẹn lại.
- Sweeper: Processing kẹt → Retry; Retry đến hạn → enqueue; New mồ côi > 2 phút → enqueue.
- `reprocess` bởi Sales User → bị từ chối.
- `redact` loại được token giả dài 40 ký tự và giá trị Password đang lưu.

Hoàn thành khi: test xanh; chạy tay trên dev: bật worker `bench worker --queue omni`, gửi 20 tin giả qua webhook (adapter giả hoặc script), tất cả Done trong < 1 phút (dán log/kết quả truy vấn đếm theo trạng thái).

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Trong báo cáo có truy vấn đếm trạng thái: 20 Done, 0 New.
2. Tắt worker, gửi tin, chờ 6 phút, bật worker: tin vẫn được xử lý (tác vụ quét hoạt động).
3. Có ADR ngắn cho cách hẹn lại khi khóa bận.
