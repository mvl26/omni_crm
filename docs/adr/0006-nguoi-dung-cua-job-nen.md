# 0006 · Người dùng của job nền

- Ngày: 2026-11-02
- Trạng thái: Đề xuất
- Prompt: V2-01 (gap-report mục 3, việc 6; docs/12 mục D "D2-09, E6")

## Bối cảnh
`frappe.enqueue` ghi `user` của phiên đang chạy vào job. Job do webhook đẩy ra (xử lý tin, phân tích, bot trả lời, việc gọi lại) chạy bằng **Guest**; job do sale bấm gửi chạy bằng chính sale đó; tác vụ theo lịch (`scheduler_events`) chạy bằng Administrator. Ở D2-09 `process_missed_call` vấp `PermissionError` vì `assign_to` kiểm quyền với Guest (lỗi E6) và đã được sửa riêng từng chỗ. v2 thêm nhiều job nền (nhập danh sách nhà thầu, lấy lead Meta, tính điểm phù hợp hằng đêm) nên cần một quy tắc chung thay vì sửa từng lần.

Hiện có 5 chỗ gọi `frappe.enqueue` (ingest/gateway.py, pipeline/outbound.py, bot/analyze.py, bot/reply.py, channels/telephony/callbacks.py). Cả 5 điểm vào nay có test chạy bằng Guest (`tests/test_job_users.py`; đường bot đầy đủ ở `test_dot3_integration`, việc gọi lại ở `test_dot2_integration`).

## Các lựa chọn
1. Mỗi job tự `frappe.set_user("Administrator")` ở đầu rồi trả lại — một chỗ duy nhất, nhưng mọi thao tác chạy với quyền tối cao, che mất lỗi quyền thật.
2. Giữ người dùng thừa hưởng, chỉ dùng `ignore_permissions=True` ở đúng lệnh ghi cần thiết (đang làm) — ít quyền nhất, nhưng dễ sót khi viết job mới (chỉ lộ khi chạy bằng Guest).
3. Tạo người dùng hệ thống riêng (ví dụ `omni-system@…`) cho job — rõ ràng trong nhật ký, nhưng thêm bản ghi User và cấu hình.

## Quyết định (đề xuất)
Chọn 2, kèm quy tắc kiểm chứng: mọi job mới phải (a) ghi lệnh ghi hệ thống bằng `ignore_permissions=True` có chủ đích hoặc dùng `flags.omni_bot()`; (b) có test gọi điểm vào bằng `frappe.set_user("Guest")`; (c) đăng ký trong `ENQUEUE_SITES` của `tests/test_job_users.py` (test dò mã nguồn, thêm `frappe.enqueue(` mà chưa đăng ký thì đỏ). Ngoại lệ hiện có: `process_missed_call` tự chuyển sang Administrator vì `assign_to` bắt buộc kiểm quyền.

## Hệ quả
- Job nhập danh sách nhà thầu, lấy lead Meta, tính điểm phù hợp (V2-02, V2-03, V2-05) phải có test Guest ngay từ đầu.
- Chờ người phụ trách duyệt; chưa duyệt thì quy tắc trên vẫn là cách làm của các prompt v2.
