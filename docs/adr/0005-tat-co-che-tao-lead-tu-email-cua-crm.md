# 0005 · Tắt cơ chế tự tạo Lead từ email của CRM cho hộp thư bán hàng

- Ngày: 2026-10-29
- Trạng thái: Đã chấp nhận
- Prompt: D1-07 (G3)

## Bối cảnh
docs/05 mục 1 yêu cầu tắt việc tự tạo Lead từ thư đến của app `crm` cho hộp thư bán hàng, vì `omni_crm` đã chống trùng khách ở `resolver`. Nếu không tắt được thì dùng phương án "Lead ứng viên".

Đã đọc mã và thử trên `omni.test` (Email Account giả, `InboundMail(raw, account).process()`, rollback sau thử; chi tiết ở docs/12 mục C, G3):
- CRM chỉ có một cơ chế: hook `Communication.after_insert` (`crm/hooks.py`) -> `crm.utils.create_lead_from_incoming_email`, bật bằng Custom Field `Email Account.create_lead_from_incoming_email` (mặc định 0). Kể cả khi bật nó chỉ tạo Lead cho người gửi chưa có Lead cùng email, và không gắn các thư sau vào Lead.
- `Email Account.append_to = "CRM Lead"` chọn được (CRM Lead có `email_append_to = 1`) nhưng mọi thư đến lỗi `AttributeError` vì CRM Lead không có `subject_field`: không phải cơ chế dùng được.
- Cờ tắt và `append_to` rỗng: thư vào Communication không tham chiếu, không có Lead nào.

## Các lựa chọn
1. Tắt bằng cấu hình Email Account (cờ = 0, `append_to` rỗng): không động tới mã `crm`, đúng ràng buộc CLAUDE.md.
2. Giữ cơ chế CRM, coi Lead do CRM tạo là Lead ứng viên: thêm một Lead sinh ra ngoài định danh, dễ gộp sai/trùng khi hai thư đến cùng lúc (CRM không khóa).
3. Gỡ hook của CRM bằng mã: sửa/ghi đè hành vi app `crm` (cấm).

## Quyết định
**Chọn 1.** Hộp thư bán hàng phải có `create_lead_from_incoming_email = 0` và `append_to` trống. Thư thành Communication không tham chiếu; hook `omni_crm` đưa vào cổng tiếp nhận; khi worker gắn được Lead thì `timeline._link_email` trỏ Communication về Lead (không tạo bản sao) và gọi hook cập nhật của CRM (`communication_status`).

Phòng hờ cấu hình sai: `Omni Channel.validate` (kênh Email đang bật) hiện cảnh báo màu cam nếu gặp hai cấu hình trên; không chặn lưu. Nếu cờ vẫn bị bật, Lead do CRM tạo có `email` nên `resolver` khớp theo email mạnh và dùng lại, không sinh Lead thứ hai (có test).

## Hệ quả
- Người triển khai phải kiểm tra hai cấu hình trên khi tạo kênh Email (đưa vào sổ tay vận hành D1-10).
- Các Email Account khác của công ty (không thuộc Omni Channel đang bật) không bị ảnh hưởng.
- Frappe không lưu header gốc của thư nên không dùng được `Auto-Submitted`/`Precedence` trong hook (docs/12 mục D).
