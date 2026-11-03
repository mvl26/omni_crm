# UAT v2: nhận lead quảng cáo, chăm sóc và xem Chi tiết nhà thầu (V2-08)

Cập nhật V21-10 (2026-11-04): vẫn **CHƯA CHẠY**; sau v2.1 cần thêm khi thử: thẻ token Meta, "Kéo lại lead Meta", hàng dự phòng webhook và hạn mức Zalo ở `/app/omni-monitor`, ý định gần nhất ở Chi tiết khách (chưa thẻ nào mở bằng trình duyệt). Form "Ghi nhật ký chăm sóc" chỉ có ở Chi tiết khách, không có trong Hộp thư (bước 7 đã sửa).

Trạng thái: **CHƯA CHẠY.** Tệp này là kịch bản và mẫu bảng cho 2 đến 3 sale chạy thử; không có số liệu nào ở đây được đo. Mọi bước phải làm trên **staging** (hiện CHƯA có) với `sandbox_mode` BẬT. Phần giao diện (`omni-dashboard`, `omni-customer`, Hộp thư mới) **chưa từng được mở bằng trình duyệt thật** trong toàn bộ v2 (gap-report 13.01, 13.03, 13.04, 13.07, 14.13) nên UAT này cũng là lần kiểm giao diện đầu tiên: ghi mọi lỗi hiển thị.

## 1. Điều kiện trước ngày thử (người phụ trách hệ thống)

- [ ] Đã đi hết `docs/huong-dan/truoc-ngay-chay-chien-dich.md` mục 1 đến 9 trên staging.
- [ ] Staging: `sandbox_mode` BẬT, `developer_mode` TẮT, scheduler bật, ít nhất 4 worker `omni` (xem `docs/nghiem-thu/v2.md` mục 3 về tải đồng thời).
- [ ] 2 đến 3 sale có tài khoản vai trò Sales User; mỗi người là `default_owner` của ít nhất một biểu mẫu thử; một trưởng nhóm (`Sales Manager`).
- [ ] Có ít nhất 5 nhà thầu mẫu đã nhập (dữ liệu tổng hợp hoặc danh sách thật đã duyệt) và một Lead thử đã gắn Organization để xem Chi tiết nhà thầu.
- [ ] Người phụ trách chuẩn bị 6 lead thử: 2 từ Meta Lead Ads Testing Tool, 2 từ Google "Send test data", 2 từ form website (một có link UTM). Ghi giờ gửi thật.

## 2. Việc của sale (làm lần lượt, ghi vào bảng mục 3)

### A. Nhận lead quảng cáo
1. Khi lead vào: chuông/thông báo "Lead mới từ quảng cáo: <tên>" đến **trong 1 phút**; ghi giờ gửi, giờ thấy thông báo.
2. Mở `/app/omni-inbox`: Lead hiện trong danh sách, chip "Chưa trả lời" bật, nhãn ưu tiên; dòng thời gian có câu trả lời của biểu mẫu (không có gì lạ như mã script).
3. Mở Lead trong CRM: có chiến dịch (`omni_campaign`), nguồn (Meta/Google/Website), UTM với lead từ website.
4. Lead thử (test) phải **không** được giao cho sale và **không** tính vào báo cáo; ghi lại nếu thấy ngược.

### B. Gọi hoặc nhắn
5. Gọi điện cho khách (bằng điện thoại cá nhân hoặc tổng đài nếu có) hoặc trả lời Zalo từ Hộp thư (sandbox: kiểm tin xuất hiện `is_sandbox`, không tới khách).
6. Với Contact `Từ chối`: nút gọi/nhắn phải ẩn, SĐT/email không hiện; ghi lại nếu thấy lối nào khác để liên hệ.

### C. Ghi nhật ký chăm sóc
7. Mở `/app/omni-customer/<lead>` (từ Hộp thư bấm vào tên khách, hoặc từ Tổng quan bấm "Ghi nhật ký" ở dòng nhà thầu), bấm "Ghi nhật ký chăm sóc" ở đầu trang hoặc tab Nhật ký chăm sóc (Hộp thư KHÔNG có form này, chỉ nhắc "Chưa ghi kết quả"): chọn kết quả (Đã liên hệ / Gọi lại / Không liên lạc được / …). "Gọi lại" bắt buộc có việc tiếp theo và hạn; kiểm có đúng một việc (ToDo) được tạo cho chủ Lead.
8. Kiểm phản hồi lần đầu của Lead được ghi (số phút làm việc) và Lead chuyển "Đã liên hệ" (trừ "Không liên lạc được").

### D. Chuyển giai đoạn
9. Trên Chi tiết nhà thầu bấm giai đoạn kế tiếp trên thanh giai đoạn; bấm "Chuyển thành Deal" với một Lead đã có người liên hệ (hàm của CRM). Ghi mọi lỗi.

### E. Xem Chi tiết nhà thầu
10. Mở `/app/omni-customer/<lead>`: tiêu đề là tên công ty kèm MST; khối Mức phù hợp (điểm, hạng, công thức), tab Gói thầu (theo 20 dòng/trang), người liên hệ (nhãn từ chối/đã xóa), việc cần làm, nghi trùng.
11. Sửa trực tiếp một trường (ví dụ ghi chú nhu cầu): lưu được và đánh dấu "sửa tay".
12. Đăng nhập bằng sale khác: mở Lead của người kia → phải bị từ chối.

### F. Tổng quan (`/app/omni-dashboard`)
13. Sale thường chỉ thấy Lead của mình; trưởng nhóm thấy cả đội. Khối "Theo chiến dịch" hiện số Lead, % phản hồi trong hạn, trung vị phản hồi lần đầu. Ghi tốc độ mở trang.

## 3. Bảng ghi kết quả (để trống, sale điền sau khi chạy trên staging)

| # | Lead thử (nguồn) | Giờ gửi | Giờ thấy thông báo | Đúng sale? | Chiến dịch/UTM đúng? | Hành động (gọi/nhắn/ghi nhật ký) | Giai đoạn sau | Lỗi/ghi chú |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Meta thử | | | | | | | |
| 2 | Meta thử | | | | | | | |
| 3 | Google thử | | | | | | | |
| 4 | Google thử | | | | | | | |
| 5 | Website (có UTM) | | | | | | | |
| 6 | Website | | | | | | | |

| Câu hỏi | Trả lời của sale |
| --- | --- |
| Thông báo lead mới đến đủ nhanh và đủ rõ không? | |
| Đủ thông tin để gọi ngay không (hay phải mở nhiều chỗ)? | |
| Màn Chi tiết nhà thầu có giúp gọi cuộc đầu tiên tốt hơn không? Thiếu gì? | |
| Có cảm giác bị làm phiền bởi thông báo nhắc quá hạn không? | |
| Giao diện nào lỗi hoặc khó hiểu? | |

## 4. Tiêu chí đạt (đề xuất, anh chỉnh)

- 6/6 lead thử vào đúng chiến dịch và đúng sale; 6/6 thông báo đến trong 1 phút.
- Không lead thử nào bị giao sai hoặc tính vào báo cáo; Contact `Từ chối` không còn lối liên hệ trong giao diện Omni.
- Không lỗi chặn ở màn Tổng quan và Chi tiết nhà thầu khi mở bằng Sales User.
- Sale ghi được nhật ký chăm sóc cho cả 6 lead.

Kết quả chạy: ______ (ngày, người chạy, đạt/không đạt, mã lỗi gửi sang `docs/12`).
