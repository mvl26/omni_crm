# Thay đổi đặc tả từ kit v1 sang kit v2

Kit v1 có thể đã được tải ở một trong nhiều thời điểm, nên V2-00 **so code thực tế với đặc tả v2**, không dựa vào tệp này để kết luận. Tệp này chỉ là danh mục để không sót.

## A. Khách hàng nhà thầu (B2B) — mới

| Mục | Đặc tả | Ảnh hưởng tới code v1 |
| --- | --- | --- |
| CRM Organization là khách hàng; khóa mã số thuế; custom field `omni_*` | docs/14 mục 1.1 | Thêm field; Lead đang có cần liên kết Organization (V2-02) |
| Omni Bid Award, Omni Hospital Alias | docs/14 mục 1.2, 2.2 | DocType mới |
| Contact có vai trò, nguồn thông tin, đồng ý nhận liên hệ | docs/14 mục 1.3 | Thêm field; "Xóa dữ liệu khách" mở rộng sang Contact |
| Thứ tự định danh mới: mã số thuế → Contact → tên miền email công ty → quy tắc cũ | docs/14 mục 2 | Sửa `identity/resolver.py` (giữ nguyên hành vi cũ cho khách chưa biết công ty) |
| Điểm phù hợp (fit), hạng A/B/C, khớp công thức mẫu Excel | docs/14 mục 3.1 | Mới; tham số `fit_*` |
| Điểm quan tâm dùng bảng tín hiệu B2B; Lead nóng = quan tâm cao **và** hạng A/B | docs/14 mục 3.2–3.3, docs/02 mục 11, mau/bang-diem-lead-mau.csv | Sửa `bot/scoring.py`, bảng điểm, danh sách tín hiệu |
| Giai đoạn bán hàng B2B, `omni_lost_reason` | docs/14 mục 4 | Patch CRM Lead/Deal Status |
| Nhập danh sách nhà thầu từ mẫu Excel; ánh xạ mã trường | docs/14 mục 5, mau/Mau_DanhSach_NhaThau_TiemNang.xlsx | Mới |
| Không gửi hàng loạt; tôn trọng "Từ chối liên hệ" | docs/14 mục 6, V2-Q12 | Kiểm tra không có chức năng gửi hàng loạt; ẩn nút liên hệ |
| Bot: câu lệnh theo bối cảnh nhà thầu; nhóm kho tri thức mới (Phí dịch vụ, Đàm phán hợp đồng luôn chuyển người); trường B2B | docs/08 mục 8, docs/14 mục 7, mau/nhom-cau-hoi-mau.csv, mau/kho-tri-thuc-mau.csv, eval/mau-ca-kiem-thu.jsonl | Phiên bản câu lệnh mới (`analyze_v2`, `reply_v2`), nhập lại nhóm và kho tri thức, cập nhật bộ đề, chạy lại cổng |

## B. Lead từ quảng cáo và chiến dịch — mới

| Mục | Đặc tả |
| --- | --- |
| Kênh Meta Lead Ads, Google Lead Form, Web Form | docs/15 mục 1–4 |
| Omni Lead Form (ánh xạ trường), Omni Campaign (có `spend`) | docs/15 mục 5–6 |
| Trường quy kết trên CRM Lead (first/last touch, UTM, click id), `omni_first_response_at` | docs/15 mục 6 |
| Phân loại, giao sale ngay, đồng hồ phản hồi theo giờ làm việc | docs/15 mục 7 |
| Khối "Theo chiến dịch" trên Tổng quan | docs/15 mục 9 |

## C. Dashboard CSKH và nhật ký chăm sóc — mới hoặc mở rộng

(Chỉ có trong v1 nếu anh tải kit sau khi có D1-12.)

| Mục | Đặc tả |
| --- | --- |
| Tổng quan CSKH: chỉ số, Cần xử lý ngay, theo kênh, bot, nhân viên | docs/13 mục 1 |
| Hộp thư: bộ lọc chip, nhãn ưu tiên, hồ sơ rút gọn, nút ghi nhật ký | docs/13 mục 2 |
| Omni Care Log (phiên chăm sóc, kết quả, bước tiếp theo, hạn → CRM Task) | docs/13 mục 3 |
| Chi tiết Lead / Chi tiết nhà thầu: đầu trang, ba cột, các tab | docs/13 mục 3b, docs/14 mục 8 |
| "Phản hồi lần đầu" đo theo `omni_first_response_at` | docs/13 mục 1.2, docs/15 mục 7 |
| `utils/business_hours.py` và `holidays` làm từ D1-12 (trước kia ở D2-03) | prompts/dot-1/12 bước 0 |

## D. Sửa đặc tả và các cổng mở

| Mục | Ghi chú |
| --- | --- |
| Ví dụ hạn phản hồi đúng: 16:50 thứ Sáu → 17:05; 17:20 thứ Sáu → 08:05 thứ Bảy | docs/15 mục 10 |
| Công tắc mới: `prospect_import_enabled`, `fit_scoring_enabled`, `Omni Lead Form.enabled` | docs/09 |
| Quyền cho DocType mới | docs/02 mục 16 |
| Quyết định mới V2-Q12–V2-Q15, giả định G14–G16 | docs/12 |
| Cổng chất lượng bot (`phan_tich`, `nhap`) chưa đo bằng AI thật trong v1 | V2-07 |
