# V21-03 · Tự kéo lại lead Meta sau sự cố Graph API

**Phụ thuộc:** V21-01. **Thời lượng dự kiến:** nửa ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md mục F3, docs/15 mục 2 (đối soát), docs/nghiem-thu/v2.md mục 4 (kịch bản Graph chết), mã channels/meta_leads/reconcile.py.

Tạo nhánh v21/03-keo-lai-meta từ {{BASE_BRANCH}}.

Nhiệm vụ: làm đúng F3.
1. Hàm phân loại lỗi classify_meta_error(exc_or_response) -> "tam_thoi" | "vinh_vien", có test cho: timeout, lỗi mạng, HTTP 5xx, mã giới hạn tần suất của Graph (đọc tài liệu Meta về error codes, ghi link vào docs/12 mục C), token sai/hết hạn, thiếu quyền, lead không tồn tại.
2. Lưu loại lỗi trên Omni Inbound Message khi chuyển Manual (trường chỉ thêm, hoặc dùng trường lỗi sẵn có nếu đủ).
3. Đối soát hằng giờ thêm bước: tin Meta ở Manual, lỗi "tam_thoi", nhận trong meta_requeue_hours (Omni Settings, mặc định 72) → đưa lại hàng đợi qua queue_for(), tối đa meta_requeue_batch (mặc định 200) mỗi lần. Đếm số lần kéo lại trên mỗi tin; quá 3 lần thì dừng kéo và cảnh báo.
4. Lỗi "vinh_vien" → không kéo lại, cảnh báo (gộp, không gửi một cảnh báo cho mỗi tin).
5. Kết quả đối soát ghi số tin kéo lại; màn giám sát hiện số này.
6. Chạy lại kịch bản "Graph chết 30 phút" trên {{SITE_TEST}}: sau khi Graph sống lại, lần đối soát kế tiếp tạo đủ lead, không cần reprocess tay. Dán số liệu.

Hoàn thành khi: test xanh; kịch bản sự cố đạt; docs/09, docs/15, docs/11 (sổ tay sự cố) cập nhật.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```
