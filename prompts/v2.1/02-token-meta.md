# V21-02 · Cảnh báo token Meta sắp hết hạn

**Phụ thuộc:** tag `v2-08`. **Thời lượng dự kiến:** nửa ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md mục F2, docs/15 mục 2, docs/11 mục 2 (cảnh báo), mã cảnh báo token Zalo hiện có (monitoring/alerts.py) để làm theo cùng kiểu.

Tạo nhánh v21/02-token-meta từ {{BASE_BRANCH}}.

Nhiệm vụ: làm đúng F2.
1. Đọc tài liệu chính thức Meta về endpoint debug_token (WebFetch developers.facebook.com, Graph API "debug_token") để xác nhận tên trường is_valid, expires_at, data_access_expires_at, scopes. Ghi vào docs/12 mục C kèm link và ngày.
2. Tác vụ hằng ngày (scheduler) kiểm token Page của mọi Omni Channel loại Meta Lead Ads đang bật, xác thực bằng app access token (app_id|app_secret). Gọi qua client chung có timeout, thử lại, không log token.
3. Cảnh báo khi: is_valid sai; còn ≤ meta_token_warn_days (Omni Settings, mặc định 7) tới expires_at hoặc data_access_expires_at; thiếu leads_retrieval trong scopes. expires_at = 0 → không cảnh báo hết hạn.
4. Lưu kết quả kiểm gần nhất (thời điểm kiểm, hạn, hợp lệ) trên Omni Channel (trường chỉ đọc, chỉ thêm) và hiện ở /app/omni-monitor cạnh hạn token Zalo.
5. Test với phản hồi giả: hợp lệ, sắp hết hạn, đã hết hạn, expires_at = 0, thiếu quyền, Graph lỗi tạm thời (không cảnh báo sai, thử lại lần sau).
6. Cập nhật docs/09 (tham số mới), docs/15 mục 2 (khuyến nghị token System User).

Hoàn thành khi: test xanh; kiểm với token thật ghi "chưa đo — cần staging và token Page thật".

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```
