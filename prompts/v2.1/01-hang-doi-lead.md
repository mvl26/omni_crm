# V21-01 ★ · Hàng đợi riêng cho lead quảng cáo

**Phụ thuộc:** tag `v2-08` trên `{{BASE_BRANCH}}`. **Thời lượng dự kiến:** nửa ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, README-v2.1.md, docs/v2.1/dac-ta-v2.1.md mục F1, docs/03-cong-tiep-nhan-va-hang-doi.md, docs/nghiem-thu/v2.md mục 3 (kết quả tải).

Bước 0: nếu repo chưa có docs/v2.1/ và prompts/v2.1/, chép từ {{KIT_V21}} cùng README-v2.1.md, commit "docs: thêm đặc tả kit v2.1". Tạo nhánh v21/01-hang-doi-lead từ {{BASE_BRANCH}}.

Nhiệm vụ: làm đúng F1.
1. Tìm mọi chỗ enqueue việc với queue="omni" (grep). Thay bằng một hàm định tuyến duy nhất, ví dụ queue.py::queue_for(channel_type) -> str. Meta Lead Ads, Google Lead Form, Web Form → "omni_leads". Còn lại → "omni".
2. Giữ nguyên job_id, deduplicate, khóa theo từng khách, debounce. Viết test: một khách vừa có lead form vừa có tin Zalo đến gần nhau → không bị xử lý chồng, kết quả một Lead.
3. Tác vụ quét tin kẹt, lệnh reprocess, đối soát Meta dùng queue_for().
4. Thiếu khai báo omni_leads trong common_site_config.json (khóa workers) → rơi về "omni", ghi cảnh báo một lần mỗi giờ, không lỗi. Có test.
5. Cập nhật docs/01, docs/03, docs/11 (số worker: omni_leads từ 2), docs/huong-dan/01-cai-dat-va-ha-tang.md (khai báo workers, supervisor).
6. Đo: chạy lại kịch bản tải V2-08 (tests/load, 200 lead trong 10 phút + chat cao điểm) trên {{SITE_TEST}} với 2 worker omni_leads và số worker omni như lần đo cũ. Ghi p50/p95 từ lúc nhận đến lúc có người phụ trách, số tin mất, số Lead trùng; so với số cũ (p95 ~113 giây).

Hoàn thành khi: test xanh; p95 ≤ 30 giây với kịch bản trên (nếu không đạt: báo số đo, nguyên nhân, đề xuất; không nới tiêu chí); docs cập nhật.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Bảng so sánh p95 trước và sau.
2. `common_site_config.json` mẫu trong hướng dẫn có `omni_leads`.
