# V2-02 ★ · Mô hình nhà thầu trên codebase đã có

**Phụ thuộc:** V2-01. **Thời lượng dự kiến:** 2–2,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/14-nha-thau-b2b.md (toàn bộ), docs/v2/gap-report.md (các dòng của mảng A), và prompt gốc prompts/dot-1/13-mo-hinh-nha-thau.md.

Nhiệm vụ: thực hiện TOÀN BỘ việc trong prompts/dot-1/13-mo-hinh-nha-thau.md (bước 0 và mục 1–10, kèm toàn bộ test), với các điều chỉnh sau vì code v1 đã tồn tại:

A. Mở rộng, không viết lại:
- `identity/resolver.py`: thêm các bước khớp mới (mã số thuế, Contact, tên miền email) TRƯỚC logic cũ; toàn bộ test định danh của v1 phải giữ xanh không sửa (trừ khi test cũ mâu thuẫn trực tiếp với docs/14 — khi đó ghi rõ trong báo cáo từng test đã đổi và lý do).
- `identity/merge.py`: mở rộng để giữ liên kết Organization, Contact, Omni Care Log (nếu có), Omni Bid Award.
- `bot/scoring.py` của v1 KHÔNG đổi ở bước này (V2-06 đổi bảng tín hiệu); chỉ thêm điểm phù hợp như trục riêng.

B. Chuyển đổi dữ liệu đang có — patch `patches/v2/link_existing_leads_to_organizations.py`:
- Với mỗi CRM Lead đang có: nếu Lead có tên tổ chức (trường organization của CRM) hoặc email tên miền công ty → tìm/tạo CRM Organization (không có mã số thuế thì để trống, `omni_verified = 0`), liên kết Lead; tạo Contact từ thông tin người của Lead nếu chưa có.
- Không gộp Lead, không đổi người phụ trách, không đổi trạng thái hiện tại.
- Có chế độ dry run in ra: số Lead sẽ liên kết, số Organization sẽ tạo, các trường hợp mơ hồ (nhiều Organization trùng tên) — trường hợp mơ hồ KHÔNG tự xử lý, ghi vào danh sách để người xử lý.
- Chạy được nhiều lần không tạo trùng. Chạy thử trên bản sao dữ liệu staging, dán kết quả dry run.

C. Trạng thái bán hàng: v1 có thể đã dùng các trạng thái Lead mặc định của CRM. Patch chỉ THÊM trạng thái B2B (docs/14 mục 4) và ánh xạ đề xuất từ trạng thái cũ sang mới — in bảng ánh xạ ở dry run, KHÔNG tự đổi trạng thái của Lead đang có (anh quyết định ở cổng sau).

D. Quyền: kiểm tra lại mọi API v1 trả dữ liệu khách vẫn kiểm quyền đúng khi thêm Organization/Contact.

Hoàn thành khi: mọi tiêu chí "Hoàn thành khi" của prompts/dot-1/13 đạt; test v1 vẫn xanh; dry run chuyển đổi trên bản sao staging có kết quả trong báo cáo; gap-report cập nhật.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4, thêm bảng ánh xạ trạng thái đề xuất để anh duyệt.
```

## Người duyệt kiểm tra

1. Đọc kết quả dry run: số Lead liên kết, danh sách mơ hồ, bảng ánh xạ trạng thái.
2. Duyệt bảng ánh xạ trạng thái trước khi cho chạy patch thật trên staging.
