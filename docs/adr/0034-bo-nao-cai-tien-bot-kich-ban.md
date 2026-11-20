# ADR 0034 — Bộ não cải tiến của bot kịch bản: tự rà hội thoại, đề xuất chờ duyệt

Ngày: 2026-11-20 · Trạng thái: Chấp nhận (người phụ trách yêu cầu 20/11; chọn "phải duyệt", không tự áp dụng) ·
Liên quan: ADR 0029, 0030, 0032, docs/08 mục 7

## Bối cảnh

- Bot kịch bản v3 trả lời bằng câu trong `Omni Script Node` (không tốn token). AI chỉ được gọi để hiểu ý câu khách gõ
  (`script/classify.py`) và soạn câu từ Kho tri thức (`bot/reply.py`).
- Việc cải tiến hiện làm tay: đọc hội thoại thật, tìm chỗ bot trả lời gượng, sửa mã hoặc thêm ô. Người phụ trách muốn
  bot tự làm việc này liên tục, lưu điều đã học vào kho kịch bản / Kho tri thức để các lần sau không phải hỏi AI lại,
  và có toàn bộ kịch bản dưới dạng một tệp trong Kho tri thức.

## Quyết định

1. **Không thêm DocType.** Dùng lại `Omni Script Node`, `Omni Knowledge Item`, `Omni KB Document`, `Omni Bot Run`.
2. **Kịch bản thành tài liệu Kho tri thức.** `script/brain.sync_document` xuất mọi ô thành một `Omni KB Document`
   ("Kịch bản bot Zalo (tự cập nhật)", danh mục "Kịch bản bot"), cập nhật khi ô kịch bản đổi và mỗi đêm. Tài liệu để
   `is_active = 0`: người dùng đọc được trong Kho tri thức, lượt rà dùng làm ngữ cảnh, nhưng KHÔNG lọt vào truy hồi của
   bot trả lời khách (ADR 0030) hay AI marketing (ADR 0029) — nếu lọt, bot có thể đọc mã ô và ghi chú nội bộ cho khách.
3. **Lượt rà ban đêm** (`brain.review`, cron 01:40):
   - Lọc bằng dấu hiệu, không gọi AI: bot gửi `REASK_CHUARO` / `REASK_LUACHON` / `KB_CHUYENVIEN`, hoặc nhân viên trả
     lời sau khi bot đã nói.
   - Chỉ đoạn hội thoại có dấu hiệu (tối đa 20/đêm, 14 lượt/đoạn) mới gửi AI qua `ai_client.call_tool` loại `Analyze`
     (cổng dữ liệu khách, hạn mức token/ngày, che SĐT/email, thoát thẻ; nội dung khách nằm trong `<hoi_thoai>`).
4. **Đề xuất chỉ vào hàng chờ duyệt**, không có đường nào tự tới khách:
   - Hỏi – đáp mới → `Omni Knowledge Item` `active = 0`, nhóm "Bot đề xuất (chờ duyệt)" (nhóm tắt). Chỉ nhận khi đoạn
     hội thoại có lời nhân viên.
   - Sửa câu một ô → ô mới mã `DX_<mã ô>_<băm>` trạng thái "Cần duyệt"; không đổi được tập biến `{...}` của câu.
   - Cách khách nói → mã ý định → dòng `cách nói => MÃ` trong ô `TUVUNG_DEXUAT`; cách nói phải có nguyên văn trong tin khách.
   - Mọi câu do AI soạn bị loại nếu có số tiền, phần trăm, đường link hoặc quá dài.
5. **Duyệt**: `brain.approve(name)` chép câu vào ô thật; `brain.approve_phrases()` chuyển dòng sang ô `TUVUNG_KHACH`;
   mục hỏi – đáp duyệt bằng cách chuyển nhóm và bật `active` như mọi mục khác. Chỉ Omni Admin / System Manager.
6. **Giảm token về sau**: bộ phân loại tra `TUVUNG_KHACH` trước khi gọi AI (`brain.learned_code`, so sau khi bỏ dấu, tối
   đa 8 từ, trùng cả câu). Ô `TUVUNG_*` và `DX_*` không bao giờ gửi được cho khách (`store.render` chặn).
7. **Công tắc**: site_config `omni_script_brain` (mặc định bật), đặt 0 để tắt cả xuất tài liệu, lượt rà và tra từ vựng.

## Hệ quả

- Chi phí: mỗi đêm tối đa 20 lượt gọi AI, mỗi lượt kèm toàn bộ kịch bản (~15–20 nghìn token đầu vào). Ngày không có
  dấu hiệu thì không gọi lần nào.
- Bot không tự đổi cách nói với khách; chất lượng phụ thuộc người duyệt.
- Chưa làm: tự thêm kịch bản mô phỏng từ hội thoại thật; đề xuất ô / nút mới; học từ khách bỏ đi giữa chừng.
