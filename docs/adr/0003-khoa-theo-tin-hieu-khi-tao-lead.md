# 0003 · Khóa theo SĐT/email chuẩn hóa khi khớp và tạo Lead

- Ngày: 2026-10-29
- Trạng thái: Đã chấp nhận
- Prompt: D1-05

## Bối cảnh
Worker chỉ khóa theo `identity_key` (docs/03). Hai định danh khác nhau (ví dụ Zalo U1 và cuộc gọi từ số của chính khách đó) có thể cùng xử lý lúc nhau, cùng không tìm thấy Lead theo SĐT rồi cùng tạo Lead mới: hai Lead cùng SĐT không được nối (đúng điều chống trùng muốn tránh).

## Các lựa chọn
1. Ràng buộc duy nhất trên `omni_phone_e164` — không dùng được: nhiều Lead cùng SĐT là trạng thái hợp lệ (nghi trùng), và Lead do sale tạo tay có thể trùng.
2. Khóa Redis theo từng SĐT/email chuẩn hóa liên quan tới lô tin (chọn), lấy theo thứ tự sắp xếp, giữ trong suốt bước tìm + tạo Lead + commit, nhờ `pipeline/locks.identity_lock` (khóa tên `sig:{giá trị}` nên không đụng khóa định danh).
3. Hàng đợi tuần tự một worker — hạn chế mở rộng.

## Quyết định
Chọn 2. Lượt vào sau lấy khóa xong thì `commit()` để có snapshot mới, thấy Lead lượt trước vừa commit, nên gắn vào Lead đó. Không lấy được khóa trong 20 giây thì ném `TransientError` (worker thử lại theo bảng backoff).

## Hệ quả
- Lô tin không có tín hiệu nào thì không khóa gì thêm.
- Lead do sale tạo tay đồng thời với resolver không được bảo vệ; trường hợp này dựa vào Candidate sinh ở lượt sau.
- Tín hiệu trung bình (SĐT trong nội dung tin) không được lưu lại trên Lead mới, nên chưa nối được với Lead tạo sau chỉ nhờ SĐT đó (xem docs/12, phát hiện D1-05).
