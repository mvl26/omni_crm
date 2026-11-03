# V21-05 · Zalo OA theo bảng gói 2026, công cụ chẩn đoán chữ ký

**Phụ thuộc:** tag `v2-08`. **Thời lượng dự kiến:** 1 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md mục F5, docs/06-kenh-zalo-oa.md, docs/huong-dan/04-kenh-zalo-oa.md, docs/12 (Z1–Z9, G7–G9), mã channels/zalo/.

Tạo nhánh v21/05-zalo-goi-oa từ {{BASE_BRANCH}}.

Bước 0 — tài liệu chính thức: đọc (WebFetch) trang giá gói OA của Zalo (zalo.solutions/oa/pricing, tin "Quyền lợi và biểu phí các gói dịch vụ Zalo OA mới từ 1/6/2026" trên oa.zalo.me) và tài liệu Zalo for Developers về webhook (chữ ký X-ZEvent-Signature) và API gửi tin tư vấn. Ghi vào docs/12 mục C những gì xác nhận được, kèm link và ngày; điều gì trang không nói rõ thì ghi "chưa xác nhận". Không tự đổi công thức chữ ký nếu tài liệu không nói khác; nếu tài liệu nói khác code → ADR "Đề xuất" và dừng.

Nhiệm vụ: làm đúng F5.
1. Omni Channel thêm zalo_oa_package (Select: Chưa xác nhận / Cơ bản / Tiêu chuẩn / Tăng trưởng / Toàn diện, mặc định Chưa xác nhận) và zalo_cs_paid_quota_month (Int, trống = chưa biết). Chỉ thêm trường.
2. Đếm tin sale gửi ngoài khung zalo_cs_free_hours trong tháng (theo Omni Outbound Message), hiện trong Hộp thư cạnh cảnh báo khung gửi: "Đã dùng X / Y tin ngoài 48 giờ tháng này" hoặc "chưa biết hạn mức". Không chặn gửi theo hạn mức.
3. Gói Chưa xác nhận / Cơ bản / Tiêu chuẩn → cảnh báo trên /app/omni-monitor "Gói OA có thể chưa cho kết nối API". Không chặn kênh.
4. Công cụ chẩn đoán chữ ký theo F5 bước 4: cờ site_config omni_zalo_signature_debug_until (ISO datetime, quá 2 giờ kể từ hiện tại thì bị cắt về 2 giờ); bản ghi chẩn đoán riêng (DocType nhỏ "Omni Signature Probe" hoặc Error Log có tiêu đề cố định — chọn cách không cần DocType mới nếu được; DocType mới cần ADR); chỉ Omni Admin đọc; không chứa oa_secret_key; tự xóa sau 24 giờ. Viết hướng dẫn 15 phút xác nhận Z1 trong docs/huong-dan/04-….
5. Cập nhật docs/06 và docs/huong-dan/04-…: mục "Gói OA và điều kiện API", bước kiểm gói trong trang quản trị OA (mục Quản lý gói), bỏ khẳng định "7 ngày có phí", thay bằng "theo gói và hạn mức, xác nhận với Zalo".
6. Test: đếm hạn mức qua ranh giới tháng; cảnh báo gói; công cụ chẩn đoán tự tắt, không chứa khóa, tự xóa.

Hoàn thành khi: test xanh; docs/12 có ghi chép tài liệu Zalo; phép thử trên OA thật ghi "chưa đo — cần OA của công ty trên staging".

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```
