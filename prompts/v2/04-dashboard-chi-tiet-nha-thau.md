# V2-04 ★ · Dashboard CSKH, Chi tiết nhà thầu, nhật ký chăm sóc

**Phụ thuộc:** V2-03. **Thời lượng dự kiến:** 2,5–3 ngày nếu v1 chưa có D1-12; 1–1,5 ngày nếu đã có.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/13-dashboard-cskh.md (toàn bộ), docs/14 mục 3.3 và 8, docs/v2/gap-report.md (các dòng mảng C), prompt gốc prompts/dot-1/12-dashboard-ho-so-cham-soc.md, và prompts/dot-3/01-nhap-cho-sale.md mục 6 (các khối dashboard của nháp bot).

Bước 0 — Xác định nhánh làm việc theo gap-report:
- Trường hợp 1 (v1 CHƯA có D1-12: không có Omni Care Log, không có trang omni-dashboard / omni-customer): thực hiện TOÀN BỘ prompts/dot-1/12 (bước 0 và mục 1–7, kèm test), trừ bước 0 nếu V2-01 đã làm `utils/business_hours.py`.
- Trường hợp 2 (v1 ĐÃ có D1-12): chỉ làm phần gap-report ghi "Có-lệch" hoặc "Thiếu" của docs/13; giữ nguyên phần đúng.
Ghi rõ trường hợp nào trong báo cáo.

Sau đó, trong cả hai trường hợp, bổ sung phần B2B theo docs/14 mục 8:
1. Chi tiết Lead → Chi tiết nhà thầu (cùng trang `omni-customer/<lead>`): tiêu đề tên công ty + mã số thuế (chưa có Organization thì giữ tiêu đề cũ theo tên người); cột trái thêm danh sách người liên hệ (vai trò, đồng ý nhận liên hệ; Contact "Từ chối" có nhãn đỏ và ẩn nút gọi/nhắn); tab "Gói thầu đã trúng"; cột phải khối "Mức phù hợp" (từng tiêu chí đạt/chưa, điểm, hạng, dòng "cách tính" lấy tham số thật từ Omni Settings) đặt cạnh "Điểm quan tâm"; khối "Gói đề xuất" (từ `omni_interested_package`).
2. Tổng quan: phễu theo giai đoạn B2B (docs/14 mục 4), danh sách "Phù hợp A/B, chưa liên hệ" (sắp theo điểm phù hợp, chỉ hiện Organization có Lead mà sale xem được), số nhà thầu theo hạng × khu vực.
3. Hộp thư: tên công ty và nhãn hạng phù hợp cạnh tên người nhắn.
4. Các khối của Đợt 2–3 đã có trong code v1 (Cuộc gọi, Điểm, Nháp bot chờ duyệt, Bot hôm nay…): BẬT theo công tắc thật thay vì ẩn — kiểm tra từng khối hiện đúng khi tính năng bật, ẩn khi tắt, không hiện số 0 giả.
5. Chỉ số "Phản hồi lần đầu" dùng `omni_first_response_at` (đã có từ V2-01).
6. Hiệu năng (docs/13 mục 4): `snapshot()` < 1 giây với dữ liệu staging sau V2-03 (đo và ghi); thêm chỉ mục nếu cần (migration chỉ thêm).

Test bắt buộc ngoài test của prompts/dot-1/12: tiêu đề nhà thầu khi có/không có Organization; Contact "Từ chối" ẩn nút; bảng gói thầu chỉ hiện gói của đúng Organization; khối Mức phù hợp khớp số từ `prospect/fit.py`; phễu và danh sách A/B tôn trọng quyền xem Lead; mỗi khối Đợt 2–3 bật/tắt theo công tắc.

Hoàn thành khi: test xanh; ảnh chụp 5 màn hình trên staging với dữ liệu nhà thầu đã nhập ở V2-03 (Tổng quan, Hộp thư, Chi tiết nhà thầu tab Hoạt động, tab Nhật ký chăm sóc, tab Gói thầu đã trúng), kèm so sánh ngắn với canvas thiết kế.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Mở một nhà thầu hạng A: tiêu đề, người liên hệ, gói thầu, Mức phù hợp khớp file Excel.
2. Đăng nhập một sale: Tổng quan chỉ có Lead của mình; danh sách A/B không lộ nhà thầu của sale khác.
3. Ghi một phiên "Hẹn gọi lại" có hạn → có việc trong CRM; Lead chuyển "Đã liên hệ".
