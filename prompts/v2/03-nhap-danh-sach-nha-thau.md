# V2-03 ★ · Nhập danh sách nhà thầu

**Phụ thuộc:** V2-02. **Thời lượng dự kiến:** 1,5 ngày.
**Cần anh cung cấp:** file danh sách đã đổ vào mẫu (đặt ngoài repo): `{{FILE_DANH_SACH}}`.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/14 mục 1, 2, 5, 6, mẫu mau/Mau_DanhSach_NhaThau_TiemNang.xlsx, và prompt gốc prompts/dot-1/14-nhap-danh-sach-nha-thau.md.

Nhiệm vụ: thực hiện TOÀN BỘ prompts/dot-1/14-nhap-danh-sach-nha-thau.md (mục 1–5 và toàn bộ test), với điều chỉnh:

1. Nhập phải khớp với Organization/Contact đã tạo từ dữ liệu v1 (V2-02): Organization chưa có mã số thuế mà trùng tên với dòng trong file → KHÔNG tự gán mã số thuế; ghi vào báo cáo "cần xác nhận" kèm hai bản ghi để người duyệt chọn gộp.
2. Nhà thầu trong file đã có Lead đang mở từ v1 (khách tự nhắn trước đó) → không tạo Lead mới, chỉ liên kết và cập nhật điểm; giữ người phụ trách hiện tại (cột `owner_email` chỉ áp cho Lead mới).
3. Contact trùng người đã có (cùng SĐT chuẩn hóa hoặc email) → cập nhật, không tạo mới; Contact đã bị "Xóa dữ liệu khách" (bản ghi ẩn danh) → không tạo lại.
4. Trường chiến dịch (`omni_ad_platform`, `omni_campaign`) chưa có ở bước này nếu V2-05 chưa chạy — importer kiểm tra trường tồn tại trước khi điền; V2-05 sẽ điền ngược.

Thử thật trên {{SITE_STAGING}} với {{FILE_DANH_SACH}}: chạy thử (dry run) rồi nhập; dán báo cáo số dòng, báo cáo đối chiếu với sheet TongHop, danh sách "cần xác nhận".

Hoàn thành khi: mọi tiêu chí "Hoàn thành khi" của prompts/dot-1/14 đạt; nhập lại cùng file không tạo trùng; không có Lead/Contact của v1 bị nhân đôi.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra (cổng 2)

1. So 10 nhà thầu điểm cao nhất trong CRM với sheet TongHop của file.
2. Xử lý danh sách "cần xác nhận" (gộp hay giữ riêng).
