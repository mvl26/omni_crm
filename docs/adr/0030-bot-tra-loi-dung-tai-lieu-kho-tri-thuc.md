# ADR 0030 — Bot trả lời tin nhắn dùng thêm tài liệu của Kho tri thức

Ngày: 2026-11-13 · Trạng thái: Chấp nhận (người dùng yêu cầu "làm AI trả lời tin nhắn Zalo") · Liên quan: ADR 0029,
docs/08 mục 5.2, docs/comment_reply_plan.md

## Bối cảnh

- Bộ prompt "AI trả lời bình luận" (C0–C3) không làm được lúc này: Facebook thiếu `pages_manage_engagement` và chưa qua
  App Review; Zalo OA không có API/webhook bình luận bài viết (docs/comment_reply_plan.md). Người dùng chọn làm AI trả
  lời **tin nhắn** Zalo OA, trả lời dựa trên Kho tri thức.
- Bot trả lời tin nhắn đã có (docs/08 mục 5, `bot/reply.py`): chế độ Shadow (= Thử, chỉ soạn để đo), Draft (= Chờ duyệt,
  sale bấm Gửi), Auto theo nhóm câu hỏi (`bot/auto_send.py`); chốt chặn bằng code (`bot/guards.py`). Nhưng bot chỉ đọc
  mục hỏi/đáp `Omni Knowledge Item`, không đọc tài liệu (PDF/DOCX/văn bản) của Kho tri thức Marketing Hub.
- docs/08 mục 5.2: khi kho lớn phải chuyển sang truy hồi và cần ADR. Đây là ADR đó.

## Quyết định

1. **Không làm module trả lời mới.** Dùng lại bot trả lời, chế độ, chốt chặn, nháp, chuyển người sẵn có.
2. Mỗi lượt, `bot/kb_docs.py` lấy tối đa 5 đoạn tài liệu liên quan tới tin khách bằng `marketing.kb.search` (BM25,
   cùng hàm "Hỏi thử Kho tri thức"), đưa vào tin gửi AI trong thẻ `<tai_lieu><doan id nguon>` (đã thoát HTML, không
   cache vì đổi theo lượt). Mục hỏi/đáp vẫn ở khối `<kho_tri_thuc>` có cache như cũ.
3. Mã đoạn được coi như mã mục kho tri thức: AI phải ghi vào `kb_item_ids`; `guard_kb_items` chặn mã lạ, chốt chặn số
   tiền/thời hạn/cam kết/liên hệ/y khoa kiểm trên chính nội dung đoạn đã trích. Không nới chốt chặn nào; vẫn bắt buộc
   nhóm câu hỏi đang hoạt động (nhóm `always_handoff` là nơi chuyển người cho khiếu nại, y khoa, hỏi phí).
4. Câu lệnh mới `reply_v3` (v1, v2 giữ nguyên). Kho trống (không mục hỏi/đáp, không đoạn tài liệu) → Handoff
   `kb_trong` không gọi AI; chỉ có tài liệu mà không đoạn nào liên quan → Handoff `kb_khong_co_doan_lien_quan`
   không gọi AI.
5. Gọi AI qua `ai_client.call_tool` của bot (dữ liệu khách: cổng Q11, che SĐT/email, hạn mức token/ngày của bot),
   **không** qua `marketing/ai.py` (gọi với `customer_data=False`, ngân sách USD marketing).
6. **Công tắc**: tài liệu chỉ được dùng khi `reply_prompt_version` ghim từ v3 VÀ site_config
   `omni_reply_kb_documents` khác 0 (mặc định bật). Tắt: ghim lại v1/v2 hoặc đặt site_config = 0. Bot trả lời vẫn tắt
   hẳn bằng `bot_reply_mode = Off`. Không thêm trường DocType (Omni Settings đang có thay đổi chưa commit trên máy chủ).

## Hệ quả

- `bot_reply_mode` là toàn cục: bật Shadow thì cả tin Messenger/email cũng có nháp (chỉ để đo, không gửi).
- Trên site `miyano` hiện chưa có nhóm câu hỏi nào: mọi câu trả lời sẽ thành Handoff (`nhom_cau_hoi_khong_ton_tai`) cho đến khi tạo nhóm.
- Chưa làm: trang cấu hình riêng trong menu Marketing, đánh giá Đúng/Cần sửa/Sai trên nháp (hiện đo bằng so sánh với
  câu sale và quyết định Gửi/Sửa/Bỏ của nháp).
