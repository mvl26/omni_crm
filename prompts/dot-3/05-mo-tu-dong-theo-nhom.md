# D3-05 ★ · Mở bot tự gửi theo nhóm câu hỏi

**Phụ thuộc:** D3-02 và ít nhất 2 tuần dữ liệu nháp. **Cần anh chốt:** Q1 ở docs/12 (cho phép tự gửi). **Thời lượng dự kiến:** 1,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/08-bot-ai.md mục 5.5 (dòng Auto), 6, 10 (ngưỡng mở tự động); docs/09; docs/11 mục 3 (dòng "Bot trả lời sai với khách"); docs/12 mục A (Q1).

Nhiệm vụ: cho phép bot tự gửi câu trả lời cho các nhóm câu hỏi đã chứng minh đủ tốt, với chốt chặn và cơ chế tự tắt.

Việc cần làm:
1. Trường Đợt 3 trên Omni Question Group: `auto_send_enabled`, `auto_send_enabled_at`, `auto_send_enabled_by` (track_changes).
2. `bot/auto_send.py::eligibility(group, as_of) -> dict`: tính đúng định nghĩa ở docs/08 mục 10 (đoạn cuối) trên 14 ngày gần nhất — mẫu số là MỌI nháp Draft đã quyết định của nhóm (gửi nguyên văn, gửi có sửa, bỏ), tối thiểu 30; tử số là nháp được gửi có `edit_distance_ratio ≤ 0.1`; ngưỡng ≥ 95%; không có nháp `critical = 1` hoặc chấm "Sai" có `rating_note` nghiêm trọng. Trả `eligible`, các con số và lý do.
3. Bật/tắt: API `enable_auto(group)` chỉ Omni Admin, CHỈ khi `eligibility` đạt (trừ khi truyền `force=True` kèm lý do, ghi Comment); có hộp xác nhận nêu rõ số liệu. Nhóm `always_handoff` không bao giờ bật được.
4. `reply_batch` ở chế độ Draft: nháp đủ mọi điều kiện docs/08 mục 5.5 dòng Auto (action reply, qua mọi guard, nhóm bật, `can_send_now` trong khung miễn phí, trong `Omni Settings.bot_active_hours` theo Q6) → `queue_outbound(source="Bot", bot_draft=…)`, `decision` = "Sent As Is", `mode=Auto`. Còn lại → nháp Draft như cũ. Handoff ở chế độ này gửi câu giữ chân (`holding_message`).
5. Tự tắt (ngắt mạch): một nhóm đang Auto tự tắt `auto_send_enabled` và cảnh báo Omni Admin + Trưởng sale khi: sale chấm "Sai" cho một tin bot đã gửi; hoặc trong 24 giờ sau tin bot, khách có tin được phân tích là Khiếu nại; hoặc tỉ lệ Handoff của nhóm tăng gấp đôi so với trung bình 14 ngày. Ghi lý do tắt.
6. Hộp thư: tin do bot gửi có nhãn "Bot đã gửi"; sale có thể chấm Đúng/Sai/Thiếu; nút "Tắt tự động nhóm này" (Omni Admin).
7. Báo cáo: theo nhóm — trạng thái Auto, eligibility hiện tại, số tin bot gửi, số lần tự tắt.

Test: nhóm chưa đủ điều kiện không bật được; always_handoff không bật được kể cả force; nháp qua đủ điều kiện → Outbound source=Bot; ngoài khung miễn phí Zalo → rơi về Draft; giá trong câu trả lời → guard chặn → Draft Handoff; ngắt mạch kích hoạt với từng điều kiện; `sandbox_mode` bật → Outbound is_sandbox, không HTTP.

Hoàn thành khi: test xanh; trên staging ở `sandbox_mode`, bật Auto cho đúng một nhóm an toàn (ví dụ "Giờ làm việc") nếu đủ điều kiện và chứng minh tin bot được tạo (is_sandbox); chỉ khi anh cho phép rõ ràng và chỉ định tài khoản Zalo thử, mới tắt sandbox trên staging để gửi một tin thật tới đúng tài khoản đó; thử ngắt mạch bằng cách chấm "Sai". Dán bằng chứng.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Chỉ các nhóm an toàn, đủ số liệu mới bật được.
2. Chấm "Sai" một tin bot gửi: nhóm đó tự tắt, có cảnh báo.
