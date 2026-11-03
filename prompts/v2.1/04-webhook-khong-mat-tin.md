# V21-04 ★ · Webhook không làm mất tin khi chờ khóa

**Phụ thuộc:** V21-01. **Thời lượng dự kiến:** 1 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md mục F4, docs/03 (cổng tiếp nhận), api/webhooks.py và gateway (mã lưu tin), mọi ADR liên quan khóa/webhook trong docs/adr/, docs/nghiem-thu/v2.md mục 3–4.

Tạo nhánh v21/04-webhook-khong-mat-tin từ {{BASE_BRANCH}}.

Nhiệm vụ: làm đúng F4.
1. Tìm nguyên nhân webhook phải chờ khóa (omni_webhook_lock_wait_seconds, trả 503). Viết lại phần lưu tin để chỉ chèn một Omni Inbound Message, chống trùng bằng ràng buộc duy nhất dedupe_key (bắt lỗi trùng khóa → coi là đã nhận, trả như phản hồi thành công của kênh), không giữ khóa nào khác. Viết test đồng thời: 20 luồng gửi cùng một sự kiện → đúng 1 bản ghi, không 503.
2. Nếu vẫn còn trường hợp không ghi được DB: hàng dự phòng. Chọn một trong hai cách (danh sách Redis có lưu xuống đĩa; tệp nối thêm trong thư mục private của site, mỗi dòng một JSON), ghi ADR "Đã quyết" với lý do, và cách dọn. Lưu payload thô + header cần cho chữ ký + thời điểm nhận + kênh. Trả phản hồi thành công của kênh.
3. Tác vụ quét mỗi phút đọc hàng dự phòng, kiểm chữ ký, chèn lại vào Omni Inbound Message (chống trùng theo dedupe_key), xóa dòng đã chèn. Chèn lỗi thì giữ lại, thử lần sau.
4. Chỉ trả 5xx khi cả DB lẫn hàng dự phòng đều hỏng; khi đó log mức nghiêm trọng + cảnh báo ngay.
5. Màn giám sát: số tin trong hàng dự phòng, tuổi tin cũ nhất; cảnh báo khi > 10 phút. Thêm vào preflight sau (V21-08 sẽ đọc hàm đếm này: viết hàm public spool_count()).
6. Test: DB khóa (giả lập lỗi lock wait), DB mất kết nối, Redis/tệp dự phòng hỏng, chèn lại đúng một lần, chữ ký sai trong hàng dự phòng bị loại.
7. Chạy lại kịch bản tải V2-08: đếm phản hồi 503 (phải bằng 0 do chờ khóa), p95 webhook.
8. Bỏ hoặc đổi ý nghĩa tham số omni_webhook_lock_wait_seconds trong docs/09 và hướng dẫn 01.

Hoàn thành khi: test xanh; tải lại không có 503 do khóa; ADR ghi; docs cập nhật.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4, thêm mục "Nguyên nhân phải chờ khóa trước đây".
```

## Người duyệt kiểm tra

1. Đọc ADR hàng dự phòng: cách dọn, chỗ lưu không nằm trong thư mục public.
2. Số 503 trong lần tải lại.
