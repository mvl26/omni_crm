# D1-13 ★ · Mô hình nhà thầu (B2B) và điểm phù hợp

**Phụ thuộc:** D1-06. **Thời lượng dự kiến:** 2 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/14-nha-thau-b2b.md (toàn bộ — đặc tả chính), docs/04-dinh-danh-va-chong-trung.md, docs/02 mục 15–16, mẫu Excel mau/Mau_DanhSach_NhaThau_TiemNang.xlsx (sheet HuongDan, ThamSo, NhaThau — đọc công thức để tính điểm giống hệt).

Nhiệm vụ: đưa nhà thầu (công ty) vào mô hình dữ liệu, gộp theo mã số thuế, tính điểm phù hợp, và tạo các giai đoạn bán hàng B2B. CHƯA làm nhập file (D1-14) và giao diện (D1-12).

Bước 0 — G14 (docs/12): đọc mã app `crm`: DocType CRM Organization, Contact (và cách CRM liên kết Contact với Lead/Deal/Organization), CRM Lead Status, CRM Deal Status, và hàm chuyển Lead → Deal. Ghi tên trường, cách liên kết, cách chuyển đổi vào docs/12 mục C. Nếu CRM Organization đã có trường tương đương custom field dự kiến (website, địa chỉ, doanh thu…), dùng trường có sẵn, không tạo trùng; ghi lại.

Việc cần làm:
1. Custom field (fixtures, module Omni CRM) trên CRM Organization (docs/14 mục 1.1) và Contact (mục 1.3). `omni_tax_id`: chuẩn hóa trong `validate` (docs/14 mục 2.1), kiểm tra không trùng (ràng buộc DB nếu được; nếu không, kiểm tra trong validate kèm khóa Redis khi tạo để tránh hai bản ghi đồng thời).
2. DocType `Omni Bid Award` (docs/14 mục 1.2) và `Omni Hospital Alias`; `hospital_key`, `end_date`, `import_key` tính trong `validate`. Quyền: Omni Admin ghi; Sales User đọc theo quyền xem Organization/Lead.
3. Trường Settings `fit_*` (docs/14 mục 3.1) với mặc định giống sheet ThamSo của mẫu Excel.
4. `prospect/fit.py::compute_fit(organization) -> dict` đúng công thức docs/14 mục 3.1 (làm tròn `Decimal` ROUND_HALF_UP, cộng tháng bằng `relativedelta` — giống Excel; `active_awards` không lọc theo kỳ); bỏ qua khi `fit_scoring_enabled` tắt; ghi các trường chỉ đọc trên Organization với `frappe.flags.in_omni_bot = True`. Gọi khi Omni Bid Award thêm/sửa/xóa và khi trường Organization liên quan đổi (doc_events), và tác vụ đêm `recompute_all_fit` theo lô.
5. **Kiểm tra khớp với Excel**: test dùng đúng bộ dữ liệu giả lập trong `tests/fixtures/prospect/fit_cases.json` (bạn tạo, gồm tối thiểu: nhà thầu 6 bệnh viện có một gói trùng bệnh viện, một gói ngoài kỳ, một gói loại "Thuốc"; nhà thầu 5 bệnh viện có số mã hàng ước tính 300 và hợp đồng đã hết; nhà thầu 2 bệnh viện). Giá trị kỳ vọng (ngày tính 30/10/2026, tham số mặc định): lần lượt số BV 6/5/2, mã hàng 250/300/80, giá trị 13,00/15,00/2,00 tỷ (khi gói trùng BV có giá trị 1 tỷ), còn hiệu lực 7/0/2, điểm 62/53/25, hạng B/B/C — tính lại nếu bộ dữ liệu bạn tạo khác, nhưng phải giống kết quả của mẫu Excel trên cùng dữ liệu (ghi cách bạn đối chiếu vào báo cáo).
6. Định danh (mở rộng `identity/resolver.py`) theo thứ tự docs/14 mục 2: mã số thuế → SĐT/email của Contact đã biết → tên miền email công ty (loại danh sách tên miền miễn phí, để thành hằng số) → quy tắc cũ. Khi một Identity khớp Contact của một Organization: gắn Lead đang mở của Organization (tạo Lead Tiềm năng nếu chưa có). Mở rộng `merge_leads` để giữ liên kết Organization; thêm gộp Organization trùng mã số thuế (hiếm, do nhập tay) chuyển Contact, Bid Award, Lead.
7. Giai đoạn bán hàng (docs/14 mục 4): tạo CRM Lead Status và CRM Deal Status bằng patch có thể chạy lại (không xóa trạng thái có sẵn của CRM; chỉ thêm và sắp thứ tự). Ghi lý do "Không phù hợp" vào trường lý do mất của CRM nếu có (theo G14), nếu không thì custom field `omni_lost_reason`.
8. Mở rộng `erase_customer_data` (D1-10) sang Contact theo docs/14 mục 6, có test.
9. Trường Settings `prospect_import_enabled`, `fit_scoring_enabled` (docs/09).
10. Quyền theo Lead: người xem được Lead của Organization mới xem được Contact và Bid Award của Organization đó qua API của app.

Test: chuẩn hóa mã số thuế (10 số, 13 số có gạch, có khoảng trắng, sai độ dài); không tạo được hai Organization cùng mã số thuế (kể cả tạo đồng thời); compute_fit khớp bộ dữ liệu ở bước 5; hospital_key gộp "BV Đa khoa X" và "Bệnh viện đa khoa X"; resolver: email `@congty.vn` khớp đúng một Organization → gắn; khớp hai → nghi trùng, không gắn; email gmail → không dùng tên miền; patch trạng thái chạy hai lần không lỗi.

Hoàn thành khi: test xanh; docs/12 có G14; fixtures đã xuất.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Tạo tay 1 Organization có mã số thuế, thêm 6 gói thầu ở 6 bệnh viện, mã hàng 250: hạng B, điểm khớp với mẫu Excel khi nhập cùng số liệu.
2. Tạo Organization thứ hai cùng mã số thuế: bị chặn với thông báo dễ hiểu.
