# ADR 0031 — Mẫu tin cho sale điền khi khách hỏi giá hoặc đặt lịch demo

Ngày: 2026-11-16 · Trạng thái: Chấp nhận (người dùng yêu cầu) · Liên quan: ADR 0030, docs/08 mục 5.5, `bot/templates.py`

## Bối cảnh

Người dùng (2026-11-16) muốn trên Zalo OA:

- Câu hỏi kiến thức có trong Kho tri thức: bot trả lời thẳng. Phần này dùng cơ chế Auto theo nhóm câu hỏi đã có
  (`bot/auto_send.py`, nhóm "Thông tin dịch vụ"), không đổi code.
- Hỏi giá, đặt lịch demo: bot **soạn sẵn mẫu tin** để nhân viên điền thông tin rồi gửi.

Trước đây, nháp `Handoff` không có nội dung. `api/drafts.send_draft` và `pipeline/outbound._check_bot_draft` đều từ chối
gửi nháp Handoff, và Hộp thư chỉ cho bấm "Bỏ".

## Quyết định

1. Nháp vẫn là `Handoff`. Hành vi chuyển người giữ nguyên: tạm dừng bot với khách theo `handoff_pause_hours` (người dùng
   chọn giữ 24 giờ) và báo người phụ trách. Bot không bao giờ tự gửi mẫu: nhóm `always_handoff` không bật Auto được.
2. `bot/templates.pick(nhóm, ý định, lời khách)` chọn mẫu:
   - `demo`: nhóm "Đặt lịch demo", hoặc lời khách có "demo"/"dùng thử".
   - `price`: nhóm "Hỏi giá", chủ đề `hoi_gia` của chốt chặn, hoặc ý định Hỏi phí.
   - Không có mẫu khi ý định là Khiếu nại, nhóm là "Khiếu nại"/"Tư vấn y khoa", hoặc lời khách có chủ đề khiếu nại, y
     khoa, pháp lý hay điều khoản hợp đồng.

   Mẫu được ghi vào `draft_text` cùng cờ `info:mau_tin:<khóa>`.
3. Nhóm của nháp:
   - Giữ nhóm AI chọn nếu nhóm đó đã luôn chuyển người.
   - Nếu không (ví dụ "Thông tin dịch vụ" bị chốt chặn giá), đổi sang nhóm của mẫu; thiếu nhóm đó thì để trống. Nhờ vậy
     nháp sale tự điền không làm sai tỉ lệ mở Auto của nhóm kiến thức.
4. Gửi:
   - Sale gửi được nháp Handoff có mẫu, **chỉ khi tin đã điền hết chỗ trống `[...]`** (`knowledge.has_placeholder`).
   - Máy chủ kiểm ở cả `send_draft` lẫn `queue_outbound`; nguồn khác Sale bị từ chối.
   - Hộp thư hiện nút "Điền rồi gửi" (mở ô sửa), không có nút "Gửi" nguyên văn.
   - Chỉ kênh Zalo OA như nháp thường.
5. Nội dung mẫu mặc định theo tài liệu 05 (Demo, tài liệu và báo giá) và 08 (Giọng văn) của Kho tri thức.
6. Công tắc, không đổi DocType nên không cần migrate:
   - site_config `omni_handoff_templates_enabled` = 0 tắt hẳn.
   - `omni_handoff_templates` (JSON `{"price": "...", "demo": "..."}`) thay nội dung; `null` tắt một mẫu.

## Hệ quả

- Muốn sửa nội dung mẫu phải sửa site_config, chưa có màn hình cấu hình.
- Nháp mẫu luôn tính là "Sent Edited". Chúng thuộc nhóm luôn chuyển người nên không ảnh hưởng điều kiện mở Auto.
- Messenger/email: nháp mẫu hiện để sale chép, không gửi từ Hộp thư (như nháp thường).
