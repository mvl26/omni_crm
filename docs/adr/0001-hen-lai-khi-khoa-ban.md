# 0001 · Hẹn lại khi khóa định danh đang bận

- Ngày: 2026-10-29
- Trạng thái: Đã chấp nhận
- Prompt: D1-04

## Bối cảnh
docs/03 mục 4 yêu cầu: không lấy được khóa trong 30 giây thì enqueue lại chính job sau 30 giây. Đã đọc `frappe/utils/background_jobs.py::enqueue` (v15.107.2): không có tham số trì hoãn (`enqueue_in`); lịch trễ của RQ cần rq-scheduler mà bench không chạy. Thêm nữa, `deduplicate=True` bỏ qua enqueue khi job cùng `job_id` đang `STARTED` (docs/12 mục C, G4), nên job đang chạy không tự đẩy lại chính nó bằng cùng `job_id`.

## Các lựa chọn
1. Ngủ rồi enqueue lại — chiếm worker và vẫn bị deduplicate chặn.
2. Dùng rq-scheduler — thêm thành phần vận hành.
3. Không enqueue lại; để tin ở `New` và dựa vào hai chốt chặn: (a) lượt đang giữ khóa kiểm tra lại tin `New`/`Retry` đến hạn ở mỗi vòng (tối đa 5 vòng) và sau khi nhả khóa, nếu còn thì enqueue tiếp nối bằng `job_id` có hậu tố khác (`after_commit=False`); (b) tác vụ quét mỗi 5 phút enqueue lại tin `New` cũ hơn 2 phút.

## Quyết định
Chọn 3. Lượt bận trả `"busy"` và không đổi dữ liệu.

## Hệ quả
- Trường hợp xấu nhất (lượt giữ khóa chết giữa chừng) tin chậm tối đa khoảng 2 + 5 phút; khóa tự hết hạn sau 120 giây.
- `enqueue_identity` có tham số `job_suffix`; worker và tác vụ quét luôn gọi `after_commit=False`.
