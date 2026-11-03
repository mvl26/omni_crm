# V21-10 ★ · Hồi quy và nghiệm thu v2.1

**Phụ thuộc:** V21-01 → V21-09. **Thời lượng dự kiến:** nửa ngày đến 1 ngày.

```
Bạn là agent kiểm thử cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md (tiêu chí đo F1–F9), docs/nghiem-thu/v2.md, docs/huong-dan/truoc-ngay-chay-chien-dich.md.

Tạo nhánh v21/10-nghiem-thu từ {{BASE_BRANCH}}.

Nhiệm vụ:
1. Toàn bộ test (Redis queue khỏe): số test so với 1.596 của V2-08; không test nào bị xóa/bỏ qua (liệt kê nếu có, kèm lý do).
2. Chạy lại trên {{SITE_TEST}}: kịch bản tải V2-08 (với 2 worker omni_leads), kịch bản sự cố "Graph chết 30 phút", "Redis khởi động lại giữa đợt", "DB khóa khi webhook đến". Ghi số đo.
3. Bảng tiêu chí F1–F9: Đạt / Không đạt / Chưa đo (kèm môi trường cần có), bằng chứng (tên test, số đo).
4. Chạy preflight target production và staging trên site test; dán kết quả.
5. Cập nhật docs/nghiem-thu/v2.md: thêm mục "v2.1" (không xóa nội dung cũ), cập nhật danh sách lỗi mở (O-…) — đóng lỗi nào đã sửa, kèm bằng chứng.
6. Cập nhật truoc-ngay-chay-chien-dich.md: các mục giờ máy tự kiểm được bằng preflight; mục còn cần người.
7. Kết luận: "Sẵn sàng dựng staging và thử tài khoản thật" / "Cần sửa … trước".

Hoàn thành khi: mọi test xanh; số đo thật từ site test; tài liệu nghiệm thu cập nhật.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Bảng F1–F9 trong `docs/nghiem-thu/v2.md` mục v2.1.
2. Kết quả preflight production: chỉ còn các mục cần hệ thống thật hoặc quyết định của anh.
