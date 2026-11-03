# V21-08 ★ · Lệnh kiểm cấu hình trước phát hành

**Phụ thuộc:** V21-01, V21-02, V21-04, V21-05. **Thời lượng dự kiến:** nửa ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md mục F9, docs/09 (bảng công tắc, cấu hình theo môi trường), docs/12 (Q1, Q11 và kết quả cổng), docs/huong-dan/truoc-ngay-chay-chien-dich.md, docs/huong-dan/10-quy-trinh-len-san-xuat.md.

Tạo nhánh v21/08-preflight từ {{BASE_BRANCH}}.

Nhiệm vụ: làm đúng F9.
1. Module omni_crm/ops/preflight.py, hàm run(target: str = "production") — CHỈ ĐỌC. Mỗi mục kiểm là một hàm nhỏ trả (ket_qua, chi_tiet, cach_sua) với ket_qua ∈ {"DAT", "KHONG_DAT", "CAN_NGUOI_KIEM"}; danh sách mục theo bảng F9.
2. Quyết định Q1/Q11 và trạng thái cổng bot: đọc từ một nguồn máy đọc được. Nếu docs/12 chỉ là văn bản tự do, thêm trường trong Omni Settings (q11_allowed, q1_allowed, gate_phan_tich_passed_at, gate_nhap_passed_at — chỉ thêm, chỉ Omni Admin ghi) và ghi trong docs/09 rằng người chốt quyết định cập nhật các trường này. Không đoán từ văn bản.
3. Kiểm worker theo hàng đợi: đọc từ RQ (Redis) số worker đang lắng nghe omni và omni_leads. Không truy cập được Redis → CAN_NGUOI_KIEM kèm lý do.
4. Sao lưu: tìm bản backup mới nhất của site trong thư mục backups; > 24 giờ → KHONG_DAT (production).
5. In bảng (mục · kết quả · chi tiết · cách sửa), trả về dict; khi gọi qua bench execute với có mục KHONG_DAT thì raise để lệnh thoát mã khác 0. Không in bí mật.
6. Test từng mục với cấu hình đạt/không đạt (dùng frappe.conf và Settings tạm, khôi phục sau test).
7. Chạy trên {{SITE_TEST}} với target production và staging, dán kết quả (mục bot phải KHONG_DAT ở production do đang Draft/analyze=1).
8. Thêm lệnh vào docs/huong-dan/10-…, truoc-ngay-chay-chien-dich.md (mục tự kiểm bằng máy).

Hoàn thành khi: test xanh; kết quả chạy trên site test dán đủ.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```
