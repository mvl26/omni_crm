# D1-03 ★ · Cổng tiếp nhận

**Phụ thuộc:** D1-02. **Thời lượng dự kiến:** 1 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/01-kien-truc.md (mục 2.1, 4, 7), docs/03-cong-tiep-nhan-va-hang-doi.md (mục 1, 2, 7), docs/02-mo-hinh-du-lieu.md mục 5.

Nhiệm vụ: xây cổng tiếp nhận chung cho mọi kênh — nhận webhook, xác thực, lưu tin gốc, chống nhận trùng, enqueue. CHƯA viết adapter kênh thật (Zalo ở D1-08, tổng đài ở Đợt 2) và CHƯA viết worker (D1-04).

Bước 0 — xác nhận G4: đọc frappe/utils/background_jobs.py của bench này, xác nhận `frappe.enqueue` hỗ trợ `job_id` + `deduplicate` + `enqueue_after_commit` và hành vi khi job cùng id đang chờ/đang chạy. Ghi vào docs/12 mục C. Nếu không hỗ trợ đúng như docs/03 mục 2 giả định: dùng phương án Redis SET NX nêu trong docs/03 và ghi ADR.

Việc cần làm:
1. `ingest/events.py`: dataclass `InboundEvent`, `OutboundRequest` đúng docs/01 mục 4; hàm `stable_message_id(*parts) -> str` (SHA-256) cho kênh không có mã tin.
2. `channels/base.py`: Protocol `ChannelAdapter` đúng docs/01 mục 4. `channels/registry.py`: `get_adapter(channel_type) -> ChannelAdapter`, cho phép đăng ký adapter (để test đăng ký adapter giả).
3. `ingest/gateway.py::save_inbound(event) -> str | None` đúng docs/03 mục 2. Hàm enqueue tách riêng `enqueue_identity(identity_key)` để worker và tác vụ quét dùng lại. Nếu enqueue lỗi (Redis chết), BẮT lỗi, ghi log, vẫn giữ bản ghi New (tác vụ quét sẽ đẩy lại) — tin đã lưu không được mất.
   - Tính `identity_key` và ghi vào bản ghi tin vào ngay khi lưu (docs/03 mục 2). Chuẩn hóa người gửi qua hàm `identity.normalize.normalize_sender(channel_type, raw)` — tạo hàm này với cài đặt tạm thời (`strip()`, email chữ thường); D1-05 thay bằng chuẩn hóa thật.
4. `api/webhooks.py`: hàm khung chung `_handle_webhook(channel_type)` thực hiện đủ 7 bước ở docs/03 mục 1 (giới hạn 1 MB, đọc raw body, tải kênh theo `ch`, xác thực, parse, lưu, commit, trả 200). Hàm `zalo()` whitelisted (allow_guest, chỉ POST) gọi khung này với "Zalo OA". Các lỗi trả mã đúng: 404 kênh không có, 401 sai xác thực, 413 quá lớn, 405 sai method. Lỗi parse: lưu bản ghi `event_type="unparsed"`, `status=Manual`, trả 200.
5. Ghi `Omni Channel.last_success_at` khi nhận thành công (tối đa 1 lần/phút để tránh ghi DB liên tục — dùng cache).
6. Không log payload, header chữ ký hay bí mật. Error Log chỉ ghi: kênh, loại lỗi, độ dài body, một mã tham chiếu.

Test (`tests/test_gateway.py`, `tests/test_webhooks.py`), dùng adapter giả đăng ký cho "Zalo OA":
- Lưu tin mới → 1 bản ghi New, có enqueue (mock `frappe.enqueue`, khẳng định tham số queue="omni", job_id đúng).
- Lưu cùng `external_message_id` 5 lần → 1 bản ghi, enqueue tối đa 1 lần cho các lần trùng.
- Kênh tắt → lưu, KHÔNG enqueue.
- Enqueue ném lỗi → bản ghi vẫn còn New, hàm không ném lỗi ra ngoài.
- Webhook: sai xác thực 401 không ghi DB; kênh không tồn tại 404; body 2 MB → 413; GET → 405; parse lỗi → 200 + bản ghi Manual.
- Một request có 3 sự kiện → 3 bản ghi.
- Đo thời gian: 200 request liên tiếp vào webhook (gọi hàm trực tiếp với request giả) có p95 < 200 ms trên máy dev (ghi số đo vào báo cáo; không làm test đỏ vì máy chậm, chỉ cảnh báo).

Hoàn thành khi: test xanh; docs/12 có G4; không còn TODO nào ngoài chỗ trỏ D1-05 cho chuẩn hóa.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. `curl -X POST "https://<dev>/api/method/omni_crm.api.webhooks.zalo?ch=khong-co"` → 404.
2. Trong báo cáo có số đo p95.
3. Đọc nhanh `gateway.py`: không có lệnh gọi API ngoài, không gọi AI.
