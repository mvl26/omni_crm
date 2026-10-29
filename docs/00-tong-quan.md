# 00 · Tổng quan

## 1. Vấn đề cần giải

Khách của Miyano liên hệ qua email, Zalo OA và điện thoại. Hiện thông tin nằm rải rác ở hộp thư, điện thoại và Zalo cá nhân của từng sale: dễ sót khách, trùng người chăm sóc, không đo được kênh nào hiệu quả.

## 2. Mục tiêu

Mọi liên hệ của khách tự thành **Lead trong Frappe CRM**: không mất tin, không trùng khách; bot xử lý phần lớn câu hỏi thường gặp dưới sự giám sát của sale.

| Chỉ số | Nghiệm thu | Sau 3 tháng |
| --- | --- | --- |
| Tin bị mất | 0 | 0 |
| Lead trùng (Lead phải gộp / Lead mới) | < 3% | < 1% |
| Thời gian từ tin vào đến Lead | < 1 phút | < 30 giây |
| Phản hồi khách lần đầu (giờ hành chính) | < 15 phút | < 5 phút |
| Câu trả lời của bot đúng (sale gửi nguyên văn hoặc sửa nhẹ) | ≥ 85% | ≥ 92% |
| Trường trích xuất đúng | ≥ 90% | ≥ 95% |
| Tin phải xử lý tay | < 1% | < 0,5% |
| Gọi nhỡ được gọi lại đúng hạn | ≥ 80% | ≥ 95% |
| Cổng tiếp nhận hoạt động | ≥ 99,5% | ≥ 99,5% |

## 3. Phạm vi

**Trong phạm vi**

- 3 kênh: email, Zalo OA, tổng đài (cuộc gọi đến/đi, gọi nhỡ, ghi âm).
- Cổng tiếp nhận, hàng đợi, chuẩn hóa và chống trùng khách.
- Bot phân tích (trích thông tin, tín hiệu chấm điểm) và bot chăm sóc (soạn và gửi trả lời).
- Lead, lịch sử trao đổi, chấm điểm, gán sale trong Frappe CRM.
- Báo cáo vận hành, sale chấm bot.

**Ngoài phạm vi đợt này**

- Facebook Messenger, chat website (kiến trúc phải cho phép thêm sau bằng một adapter mới).
- Bot tự báo giá, chốt đơn, xác nhận thanh toán.
- Tin ZBS/ZNS hàng loạt có phí cho marketing.
- Nối ERPNext tạo báo giá, đơn hàng.

## 4. Ba đợt phát hành

| Đợt | Phát hành | Sale nhận được | Prompt |
| --- | --- | --- | --- |
| 1 | Cuối tuần 6 | Email và Zalo OA vào thẳng CRM, không trùng khách, trả lời từ hệ thống | D1-01 … D1-11 |
| 2 | Cuối tuần 9 | Nhật ký cuộc gọi, việc gọi lại khi gọi nhỡ; Lead tự điền thông tin và có điểm. Bot trả lời chạy ngầm (sale không thấy) | D2-01 … D2-09 |
| 3 | Cuối tuần 12 | Bot soạn nháp trả lời cho sale duyệt; tóm tắt cuộc gọi từ ghi âm (nếu qua cổng thử nghiệm); mở bot tự gửi theo nhóm câu hỏi | D3-01 … D3-06 |

Mỗi đợt chạy thử 1 tuần với 1–2 sale trước khi mở toàn đội.

## 5. Nguyên tắc thiết kế

1. **Lưu trước, xử lý sau.** Tin vào được ghi xuống DB trước mọi xử lý; mọi xử lý chạy lại được từ bản ghi gốc.
2. **Không bao giờ tự gộp khi không chắc.** Khớp không chắc thì gắn cờ cho người quyết.
3. **Bot không nói điều không có trong kho tri thức.** Không hứa giá, thời hạn, chất lượng ngoài tài liệu.
4. **Mọi phần có công tắc.** Mỗi kênh, bot phân tích, bot trả lời, từng nhóm câu hỏi đều tắt được tức thì.
5. **Không sửa app `crm`.** Mở rộng qua hook, custom field, API.
6. **Mở rộng kênh bằng adapter.** Thêm kênh mới không phải sửa pipeline.

## 6. Thuật ngữ

| Thuật ngữ | Nghĩa |
| --- | --- |
| Tin vào | Một sự kiện từ kênh: tin nhắn, email, cuộc gọi, khách gửi thông tin |
| Định danh | Một mã nhận diện khách trên một kênh: Zalo user id, email, SĐT |
| Lượt xử lý | Một lần worker xử lý các tin vào liên tiếp của một khách |
| Nghi trùng | Hai Lead có thể là một người, chờ người quyết |
| Chế độ ngầm | Bot chạy và lưu kết quả nhưng không hiện cho sale, không gửi cho khách |
| Chế độ nháp | Bot soạn, sale duyệt rồi mới gửi |
| Chế độ tự động | Bot tự gửi, chỉ áp dụng cho nhóm câu hỏi đã đạt chuẩn |
| Chuyển người | Bot dừng với khách đó và báo sale xử lý |
