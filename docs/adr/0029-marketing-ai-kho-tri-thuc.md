# ADR 0029 — AI cho Marketing Hub và Kho tri thức tài liệu

Ngày: 2026-11-13 · Trạng thái: Chấp nhận · Liên quan: ADR 0028, docs/17, Prompt 4 của bộ Marketing Hub

## Bối cảnh

Prompt 4 yêu cầu: một cổng AI duy nhất có ghi chi phí, chặn theo ngân sách tháng, mô hình dự phòng; Kho tri thức gồm
danh mục, tài liệu (PDF/DOCX/text), đoạn 500–800 token "kèm embedding"; khung chat lập kế hoạch chiến dịch; chấm điểm
từng bài; chống prompt injection.

Hiện trạng site `miyano`:

- Đã có cổng `omni_crm/bot/ai_client.py` (Claude API, khóa ở `Omni Settings.ai_api_key`, nhật ký `Omni Bot Run`,
  ngắt mạch, kiểm schema). Prompt yêu cầu "dùng lại nếu đã có".
- MariaDB 10.11: không có kiểu `VECTOR` (có từ 11.7). Không có Redis Stack/vector DB.
- Anthropic không cung cấp API embedding. Dùng embedding phải thêm nhà cung cấp thứ hai (khóa mới, chính sách dữ
  liệu mới) — ngoài phạm vi được duyệt.
- Kho hỏi/đáp `Omni Knowledge Item` của bot đã có, khác bản chất (cặp hỏi/đáp có duyệt), không thay được kho tài liệu.

## Quyết định

1. **Không tạo `omni_crm/ai/gateway.py` mới.** Cổng là `bot/ai_client.call_tool_result` (mở rộng, giữ nguyên hành
   vi cũ). Thêm loại `Marketing` cho `Omni Bot Run`, trường `cost_usd` (bảng giá `bot/pricing.py`).
   Lớp mỏng `marketing/ai.py` lo: chọn mô hình, ngân sách tháng, chuyển mô hình dự phòng.
2. **Ngân sách tính bằng USD.** `monthly_ai_budget` đổi từ Currency (mặc định VND, dễ hiểu nhầm) sang Float có nhãn
   "(USD)", vì giá API tính bằng USD. 0 = tắt AI marketing. Lần gọi Marketing không tính vào hạn mức token/ngày của bot
   chat (hai ngân sách tách nhau). Chặn khi `tổng cost_usd tháng này + ước lượng trần của lần gọi` vượt ngân sách.
3. **Mô hình:** `ai_model` mặc định `claude-opus-5-5`, `ai_fallback_model` mặc định `claude-sonnet-5-5`. Chuyển dự
   phòng khi mô hình chính trả Error (hết lượt thử lại, lỗi cứng, từ chối) hoặc Invalid Output sau khi đã sửa. Mỗi lần
   gọi một dòng Bot Run với đúng tên mô hình nên chi phí quy được về từng mô hình.
4. **Cổng Q11 (dữ liệu khách):** brief chiến dịch và tài liệu nội bộ không phải dữ liệu khách nên các lời gọi
   Marketing truyền `customer_data=False`. Không dùng cờ `synthetic` (cờ đó dành cho hội thoại tổng hợp).
5. **Lưu vector: không dùng embedding ở bản này.** Truy hồi bằng BM25 viết bằng Python trên văn bản đã chuẩn hóa
   (chữ thường, bỏ dấu tiếng Việt bằng `unidecode`, giữ cả dạng có dấu). Lý do: không có kiểu VECTOR, không có API
   embedding của Anthropic, kho dự kiến vài trăm đoạn nên BM25 trong bộ nhớ (cache theo phiên bản kho) đủ nhanh.
   `Omni KB Chunk.embedding` (Long Text, để trống) giữ chỗ cho bước sau: khi có quyết định nhà cung cấp embedding hoặc
   nâng MariaDB ≥ 11.7, chỉ cần điền cột và đổi hàm truy hồi, không đổi DocType.
6. **DocType mới (3):** `Omni KB Category`, `Omni KB Document` (tệp + trạng thái Đang xử lý / Đã lập chỉ mục / Lỗi),
   `Omni KB Chunk` (DocType thường, không phải bảng con, để truy vấn/xóa theo tài liệu). Lập chỉ mục chạy bằng
   `frappe.enqueue` (hàng đợi `long`), không thêm lịch vào hooks.py. Đọc DOCX bằng `zipfile` + `word/document.xml`
   (không thêm thư viện `python-docx`), giới hạn kích thước giải nén; PDF bằng `pypdf` nếu có trong môi trường,
   không có thì tài liệu chuyển "Lỗi" kèm lý do.
7. **Phản hồi "Hỏi thử"** (Đúng / Cần sửa / Sai) lưu vào chính dòng `Omni Bot Run` của câu trả lời (trường
   `feedback`, `feedback_note`, `feedback_by`), không tạo DocType mới.
8. **Chống prompt injection:** nội dung tài liệu, tệp đính kèm, đoạn kho tri thức luôn được thoát HTML và bọc trong thẻ
   (`<tai_lieu>`, `<doan>`, `<dinh_kem>`); lời nhắc hệ thống nói rõ nội dung trong thẻ là dữ liệu, không phải lệnh.
   Đầu ra AI chỉ đi qua tool có schema; không có tool nào để AI tự đăng bài hay đổi trạng thái.
9. **Chặn duyệt sai điểm:** `quality_score` chỉ đổi qua bộ chấm điểm (cờ `score_via_ai`); sửa tiêu đề/caption/ảnh của
   bài đã chấm thì điểm về 0 (phải chấm lại). Bài còn `[cần bổ sung]` không duyệt được và bộ đăng bài từ chối.
10. **Chạy nền:** lập kế hoạch và chấm điểm có thể mất hàng chục giây nên chạy trong job nền, kết quả để ở cache
    (24 giờ) và đẩy realtime cho người yêu cầu; trình duyệt đọc lại bằng API khi nhận sự kiện hoặc khi tải lại trang.

## Rủi ro

- BM25 không hiểu đồng nghĩa: câu hỏi dùng từ khác tài liệu có thể trả ít đoạn. Giảm thiểu: gộp thêm `Omni Knowledge
  Item`, cho xem nguồn trích, thu phản hồi Đúng/Sai để quyết định có cần embedding không.
- Bảng giá cứng trong code có thể lệch giá thật; mô hình không có trong bảng ghi chi phí 0. Đổi mô hình phải cập nhật
  `bot/pricing.py`.
- Gọi AI thật trên site chính tốn tiền: chỉ chạy khi người phụ trách đồng ý; test dùng `FakeAIClient`.
