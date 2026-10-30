# D1-14 ★ · Nhập danh sách nhà thầu từ mẫu Excel

**Phụ thuộc:** D1-13. **Thời lượng dự kiến:** 1,5 ngày.
**Cần anh cung cấp:** một file mẫu đã có dữ liệu thật hoặc mẫu (đặt ngoài repo, ví dụ `{{FILE_DANH_SACH}}`) để thử trên staging.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/14-nha-thau-b2b.md mục 1, 2, 5, 6; mẫu mau/Mau_DanhSach_NhaThau_TiemNang.xlsx (mọi sheet; tên sheet và mã trường ở dòng 2 là hợp đồng dữ liệu — không đổi).

Nhiệm vụ: nhập file danh sách nhà thầu vào CRM an toàn, chạy lại được, có báo cáo lỗi để người dùng sửa.

Việc cần làm:
1. `prospect/importer.py::import_workbook(file_url_or_path, dry_run=False, user) -> import_log_name`:
   - Đọc bằng openpyxl `data_only=True` (cột tự tính bỏ qua; CRM tự tính lại điểm). Xác định cột theo **mã trường ở dòng 2**, không theo vị trí cột. Thiếu sheet hoặc thiếu mã trường bắt buộc → dừng, báo rõ thiếu gì.
   - Bỏ dòng 1–3 (tiêu đề, mã trường, ví dụ) và dòng trống. Nếu dòng 3 không còn là dòng ví dụ (mã số thuế khác `0109999999`) → vẫn bỏ qua nhưng cảnh báo trong báo cáo “Dòng 3 có dữ liệu thật — chuyển xuống từ dòng 4”.
   - Trường bắt buộc và ánh xạ mã trường → CRM theo đúng bảng ở docs/14 mục 5 (kể cả cách xử lý cột `stage` thuộc giai đoạn Deal và “Không phù hợp”, `notes`, `full_name`).
   - Từ chối chạy khi `prospect_import_enabled` tắt.
   - NhaThau → upsert CRM Organization theo `omni_tax_id` chuẩn hóa. Chỉ ghi đè trường có giá trị trong file; không xóa dữ liệu CRM khi ô trống. Không ghi đè trường người dùng đã sửa trong CRM sau lần nhập trước (lưu ngày nhập cuối cùng mỗi trường hoặc so `modified_by` — chọn cách đơn giản, ghi ADR).
   - Tạo CRM Lead trạng thái "Tiềm năng" (hoặc theo cột `stage` nếu có) cho Organization chưa có Lead đang mở; người phụ trách = `owner_email` nếu là User hợp lệ, không thì theo quy tắc phân công.
   - GoiThau → upsert Omni Bid Award theo `import_key`; dòng có mã số thuế không có trong NhaThau và chưa có trong CRM → lỗi dòng.
   - NguoiLienHe → upsert Contact gắn Organization theo (Organization, SĐT chuẩn hóa) hoặc (Organization, email); thiếu `info_source` → lỗi dòng; `consent` ghi vào custom field.
   - Mỗi Organization bị chạm → tính lại điểm phù hợp một lần (không tính lại sau mỗi dòng gói thầu).
   - Chạy bằng job nền trên hàng đợi `omni` (file 1.000 nhà thầu / 5.000 gói phải xong dưới 5 phút), commit theo lô 100 dòng; lỗi một dòng không làm hỏng cả file.
2. DocType `Omni Prospect Import` (log): tệp, người nhập, dry_run, trạng thái, số dòng tạo/cập nhật/bỏ qua/lỗi theo từng sheet, `error_file` (xlsx: các dòng lỗi + cột "Lý do lỗi", cùng định dạng mẫu để sửa và nhập lại). Nút "Chạy thử" (dry_run: không ghi gì, chỉ báo cáo) và "Nhập".
3. Trang hoặc form đơn giản cho Omni Admin: tải file lên, chạy thử, xem báo cáo, nhập thật; nút tải mẫu trống (phục vụ nguyên file trong `mau/`, giữ dòng ví dụ — importer luôn bỏ dòng 3).
4. Tuân thủ docs/14 mục 6: không thêm bất kỳ chức năng gửi email/tin hàng loạt nào; Contact `consent = Từ chối` được đánh dấu để giao diện ẩn nút liên hệ (D1-12).
5. Interface `prospect/sources/base.py::BidDataSource` và `ExcelSource` (bọc importer phần GoiThau) để sau này thêm nguồn API; KHÔNG viết công cụ thu thập tự động trang web đấu thầu (V2-Q13 chưa chốt).

Test (fixtures: tạo file xlsx trong test bằng openpyxl theo đúng mẫu, không đọc file thật):
- Nhập file dựng từ đúng bộ `tests/fixtures/prospect/fit_cases.json` của D1-13 → đúng số bản ghi, điểm khớp D1-13.
- Nhập lại cùng file → 0 tạo mới, không trùng.
- Sửa một ô trong file rồi nhập lại → chỉ trường đó cập nhật.
- Mã số thuế sai định dạng, gói thầu trỏ mã số thuế không tồn tại, người liên hệ thiếu nguồn → vào error_file với lý do; các dòng khác vẫn nhập.
- Đổi thứ tự cột trong file → vẫn nhập đúng (đọc theo mã trường).
- dry_run không ghi gì vào DB.
- Sales User không chạy được importer.

Thử thật trên {{SITE_STAGING}} với {{FILE_DANH_SACH}}: chạy thử rồi nhập; dán báo cáo số dòng và 3 nhà thầu điểm cao nhất (so với sheet TongHop trong file).

Báo cáo đối chiếu: importer đọc thêm `calc_hospitals`, `calc_fit_score`, `calc_tier` (data_only) và liệt kê nhà thầu có số bệnh viện/điểm/hạng trong CRM khác file, kèm lý do dự kiến (thường do CRM chuẩn hóa tên bệnh viện).

Hoàn thành khi: test xanh; điểm trong CRM khớp sheet TongHop của file thử, trừ chênh lệch có trong báo cáo đối chiếu và giải thích được bằng chuẩn hóa tên bệnh viện.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Mở file thử, xem sheet TongHop; mở CRM: 3 nhà thầu điểm cao nhất trùng nhau, cùng điểm và hạng.
2. Nhập lại file lần hai: báo cáo ghi 0 tạo mới.
3. Mở error_file: lý do lỗi dễ hiểu, sửa xong nhập lại được.
