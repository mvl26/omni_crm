# 02 · Mô hình dữ liệu

Module Frappe: **Omni CRM**. Tất cả DocType có tiền tố `Omni`. Cột "Đợt" cho biết prompt nào tạo; DocType của Đợt 2–3 **không tạo sớm**.

Quy ước: `Link` ghi đích trong cột Options; "Duy nhất" = `unique: 1` ở mức DB; trường kiểu `Password` không bao giờ in ra log.

## 1. Danh sách

| DocType | Loại | Đợt | Vai trò |
| --- | --- | --- | --- |
| Omni Settings | Single | 1 (bổ sung ở 2) | Công tắc, tham số chung, cấu hình AI |
| Omni Channel | Thường | 1 | Cấu hình từng kênh, bí mật, token |
| Omni Identity | Thường | 1 | Một định danh của khách trên một kênh |
| Omni Inbound Message | Thường | 1 | Bảng tin gốc — mọi sự kiện vào |
| Omni Outbound Message | Thường | 1 | Mọi tin gửi ra |
| Omni Duplicate Candidate | Thường | 1 | Cặp Lead nghi trùng chờ người quyết |
| Omni Bid Award | Thường | 1 (D1-13) | Gói thầu nhà thầu đã trúng — `docs/14` mục 1.2 |
| Omni Hospital Alias | Thường | 1 (D1-13) | Gộp tên bệnh viện viết khác nhau — `docs/14` mục 2.2 |
| Omni Prospect Import | Thường | 1 (D1-14) | Nhật ký mỗi lần nhập danh sách nhà thầu |
| Omni Campaign | Thường | 1 (D1-15) | Chiến dịch marketing — `docs/15` mục 6 |
| Omni Lead Form | Thường (+ bảng con Omni Lead Form Field) | 1 (D1-15) | Biểu mẫu Meta/Google/website và ánh xạ trường — `docs/15` mục 5 |
| Omni Care Log | Thường | 1 (D1-12) | Nhật ký phiên chăm sóc: kết quả, bước tiếp theo, hạn — đặc tả đầy đủ ở `docs/13` mục 3.2 |
| Omni Call | Thường | 2 | Cuộc gọi từ tổng đài |
| Omni Agent Map | Bảng con của Omni Channel | 2 | Ánh xạ nhân viên tổng đài → User (xem `docs/07` mục 5) |
| Omni Question Group | Thường | 2 | Nhóm câu hỏi, công tắc tự động theo nhóm |
| Omni Knowledge Item | Thường | 2 | Kho tri thức cho bot |
| Omni Score Rule | Bảng con của Omni Settings | 2 | Bảng điểm Lead |
| Omni Bot Run | Thường | 2 | Nhật ký mỗi lần gọi AI |
| Omni Bot Draft | Thường | 2 | Câu trả lời bot soạn (ngầm, nháp, tự động) |
| Omni Assistant Session | Thường | V3-05 | Phiên hội thoại với trợ lý AI nội bộ (ADR 0018) |
| Omni Assistant Action | Thường | V3-05 | Mỗi lần trợ lý gọi tool: kiểm toán + hàng đợi duyệt (ADR 0018) |
| Omni Chat Template | Thường | ADR 0026 | Mẫu tin nhắn chat cho sale (tên, đang dùng, kênh, thứ tự, dùng khi, nội dung Jinja chữ thuần); chọn ở tab Messenger |
| Omni Eval Case | Thường | 2 | Một ca trong bộ đề |
| Omni Eval Run | Thường | 2 | Một lần chạy bộ đề và kết quả |

## 2. Omni Settings (Single)

| Trường | Kiểu | Options / mặc định | Ghi chú |
| --- | --- | --- | --- |
| `sandbox_mode` | Check | 1 | Bật: chiều ra không gọi API thật. **Production phải tắt có chủ đích** |
| `debounce_seconds` | Int | 5 | Chờ gộp tin liên tiếp của một khách |
| `max_attempts` | Int | 3 | Số lần thử tối đa trước khi chuyển Manual |
| `retry_backoff_minutes` | Data | "1,5" | Phần tử thứ n = thời gian chờ sau lần lỗi thứ n. Có `max_attempts − 1` phần tử; lỗi ở lần thứ `max_attempts` → Manual |
| `stuck_after_minutes` | Int | 10 | Tin ở Processing quá lâu thì đưa lại hàng đợi |
| `raw_payload_retention_days` | Int | 180 | Sau hạn này xóa `raw_payload`, giữ nội dung đã chuẩn hóa |
| `recording_retention_days` | Int | 365 | Thời hạn giữ ghi âm (Đợt 3) |
| `default_lead_source_map` | JSON | `{"Email":"Email","Zalo OA":"Zalo OA","Telephony":"Điện thoại","Meta Lead Ads":"Quảng cáo Meta","Google Lead Form":"Quảng cáo Google","Web Form":"Website"}` | Ánh xạ kênh → CRM Lead Source |
| `alert_recipients` | Small Text | | Email nhận cảnh báo, mỗi dòng một địa chỉ |
| `business_hours` | JSON | `{"mon-fri":"08:00-17:30","sat":"08:00-12:00"}` | Giờ làm việc (hạn gọi lại, tin ngoài giờ) |
| `missed_call_callback_minutes` | Int | 30 | Đợt 2: hạn gọi lại khách gọi nhỡ, tính trong giờ làm việc |
| `callback_fallback_user` | Link User | | Đợt 2: người nhận việc gọi lại khi Lead chưa có người phụ trách |
| `holidays` | JSON | `[]` | D1-12: danh sách ngày nghỉ lễ `["2027-01-01", …]` cho tính giờ làm việc |
| `response_sla_minutes`, `care_session_gap_hours`, `team_lead_role` | Int, Int, Link Role | 15, 4, Sales Manager | D1-12: xem `docs/13` mục 5 |
| `dashboard_enabled`, `care_log_enabled` | Check, Check | 1, 1 | V2-04: công tắc Dashboard CSKH và tự ghi nhật ký chăm sóc (docs/09) |
| `fit_*` (12 trường) | Int/Float/Small Text | xem `docs/14` mục 3.1 | D1-13: tham số điểm phù hợp, mặc định giống sheet ThamSo của mẫu Excel |
| `prospect_import_enabled`, `fit_scoring_enabled` | Check | 1, 1 | D1-13/D1-14: công tắc nhập danh sách và tính điểm phù hợp |
| `assignment_fallback_users` | Small Text | | Đợt 1: chỉ dùng nếu Assignment Rule của CRM không áp dụng được (G11) — mỗi dòng một user, chia vòng tròn |
| `bot_active_hours` | JSON | để trống = 24/7 | Đợt 3: giờ bot được tự gửi (Q6); ngoài giờ chỉ soạn nháp |
| `recording_enabled` | Check | 0 | Đợt 3: công tắc tải ghi âm và chuyển văn bản. **CHƯA LÀM** (D3-04 bỏ qua có chủ đích, chờ cổng STT; V2-01): trường không có trong DocType; `flags.recording_enabled()` luôn trả tắt |
| `stt_provider` | Select | (theo D3-03) | Đợt 3: nhà cung cấp chuyển giọng nói. **CHƯA LÀM** (như `recording_enabled`) |
| `stt_api_key` | Password | | Đợt 3. **CHƯA LÀM** |
| `stt_min_confidence` | Float | theo báo cáo D3-03 | Đợt 3: dưới ngưỡng thì không phân tích bản ghi. **CHƯA LÀM** |
| **Bot (Đợt 2)** | | | |
| `bot_analyze_enabled` | Check | 0 | Công tắc bot phân tích |
| `bot_reply_mode` | Select | Off / Shadow / Draft | Chế độ trả lời toàn cục. Tự động bật **theo nhóm** ở Omni Question Group |
| `ai_api_key` | Password | | Khóa Claude API |
| `analyze_model` | Data | `claude-haiku-5-5` | Mô hình cho phân tích |
| `reply_model` | Data | `claude-sonnet-5-5` | Mô hình cho soạn trả lời |
| `daily_token_budget` | Int | 2000000 | Vượt thì bot tạm dừng đến hôm sau, gửi cảnh báo. Lưu ý: site đã có Omni Settings trước khi thêm trường migrate ra 0 (bot dừng) — Omni Admin phải đặt giá trị; không có patch tự đặt |
| `analyze_prompt_version`, `reply_prompt_version`, `summarize_prompt_version` | Data | (chỉ đọc) | Phiên bản câu lệnh đang dùng; `bot/prompts/loader.py` ghi khi nạp tệp mới nhất. `Omni Bot Run.prompt_version` lưu bản thực sự đã dùng |
| `kb_version` | Int | (chỉ đọc) | Tăng mỗi khi kho tri thức thay đổi |
| `score_rules` | Table | Omni Score Rule | Bảng điểm |
| `hot_lead_threshold` | Int | 60 | Từ ngưỡng này là Lead nóng, báo sale ngay |
| `lead_scoring_enabled` | Check | 0 | Công tắc chấm điểm Lead (D2-06); tắt thì giữ điểm cũ |
| `handoff_pause_hours` | Int | 24 | Sau khi chuyển người, bot im với khách đó trong bao lâu |
| `handoff_message_in_hours`, `handoff_message_after_hours`, `handoff_notify_channels` | Small Text | xem `docs/09` mục 4 | Đợt 3 |
| `q1_allowed`, `q11_allowed` | Check | 0 | V21-08: GHI NHẬN quyết định Q1 (bot tự gửi) và Q11 (gửi hội thoại khách sang AI) của chủ dự án để lệnh kiểm trước phát hành đối chiếu; KHÔNG mở khóa gì (khóa thật là site_config `omni_auto_send_allowed`, `omni_ai_allow_customer_data`). Người chốt quyết định cập nhật; chỉ Omni Admin ghi. Chưa từng lưu = chưa cho phép |
| `gate_phan_tich_passed_at`, `gate_nhap_passed_at` | Datetime | (trống) | V21-08: thời điểm cổng `phan_tich` / `nhap` (docs/08 mục 10) đạt bằng AI thật trên bộ đề đã duyệt. Trống = chưa đạt. Cập nhật tay sau khi `accept_run`/báo cáo cổng; chỉ Omni Admin ghi |

## 3. Omni Channel

Mỗi bản ghi là một kênh cụ thể (một hộp thư, một OA, một tổng đài).

| Trường | Kiểu | Options | Ghi chú |
| --- | --- | --- | --- |
| `channel_name` | Data | Bắt buộc, duy nhất | Đặt tên, làm `name` |
| `channel_type` | Select | Email / Zalo OA / Telephony / Meta Lead Ads / Google Lead Form / Web Form / Meta Messenger | Ba loại sau: D1-15; `Meta Messenger`: ADR 0022 (cả ở `Omni Identity.channel_type`) |
| `enabled` | Check | | Công tắc kênh: tắt thì webhook trả 200 nhưng **chỉ lưu**, không enqueue |
| `outbound_enabled` | Check | | Công tắc chiều ra |
| `webhook_token` | Password | | Token bí mật trong URL webhook (tổng đài, lớp bảo vệ thêm) |
| `email_account` | Link | Email Account | Với Email |
| `zalo_app_id` | Data | | Với Zalo |
| `zalo_oa_id` | Data | | |
| `zalo_app_secret` | Password | | Dùng làm `secret_key` khi lấy token |
| `zalo_oa_secret_key` | Password | | Dùng xác thực chữ ký webhook |
| `zalo_access_token` | Password | | |
| `zalo_refresh_token` | Password | | Mỗi lần làm mới nhận refresh token mới, phải lưu ngay |
| `zalo_token_expires_at` | Datetime | | |
| `zalo_cs_free_hours` | Int | 48 | Khung gửi tin tư vấn miễn phí sau tương tác cuối (xác nhận quy định hiện hành) |
| `zalo_cs_max_days` | Int | 7 | Quá hạn này không gửi được tin tư vấn |
| `zalo_skip_signature` | Check | 0 | **Chỉ cho site dev.** Bật trên production thì cổng từ chối xử lý và cảnh báo |
| `zalo_create_lead_on_follow` | Check | 0 | Tạo Lead khi khách chỉ bấm quan tâm OA |
| `zalo_oauth_state` | Data (ẩn) | | Chống giả mạo trong luồng cấp quyền |
| `telephony_provider` | Select | Stringee / Fake (thêm sau) | `Fake` chỉ chọn được khi `developer_mode` bật hoặc đang test (D2-01) |
| `telephony_api_key_sid` | Data | | |
| `telephony_api_key_secret` | Password | | |
| `telephony_hotline` | Data | | Đầu số |
| `agent_map` | Table | Omni Agent Map | Đợt 2: nhân viên tổng đài → User |
| `last_success_at` | Datetime | chỉ đọc | Lần nhận/gửi thành công gần nhất — để giám sát |
| `last_error` | Small Text | chỉ đọc | Lỗi gần nhất (đã lọc bí mật) |

## 4. Omni Identity

| Trường | Kiểu | Options | Ghi chú |
| --- | --- | --- | --- |
| `identity_key` | Data | Bắt buộc, **duy nhất** | `"{channel_type}:{external_id}"`, ví dụ `Zalo OA:2973...`, `Email:an@abc.vn`, `Telephony:+84912345678` |
| `channel_type` | Select | như trên | |
| `external_id` | Data | | Zalo user id / email đã chuẩn hóa / SĐT E.164 |
| `display_name` | Data | | Tên hiển thị kênh cung cấp |
| `phone_e164` | Data | chỉ mục | SĐT khách cung cấp (kể cả khi định danh là Zalo/email) |
| `email_norm` | Data | chỉ mục | Email chữ thường |
| `lead` | Link | CRM Lead | Lead đang gắn |
| `last_inbound_at` | Datetime | | Tương tác cuối của khách — dùng cho khung gửi Zalo |
| `is_follower` | Check | | Zalo: đang quan tâm OA |
| `phone_requested_at` | Datetime | | Lần gần nhất gửi yêu cầu SĐT qua Zalo (không gửi lại trong 7 ngày) |
| `bot_paused_until` | Datetime | | Tạo ở D2-07 (bot trả lời kiểm tra), dùng đầy đủ ở D3-02: bot im với khách này đến thời điểm này |

## 5. Omni Inbound Message

| Trường | Kiểu | Options | Ghi chú |
| --- | --- | --- | --- |
| `dedupe_key` | Data | Bắt buộc, **duy nhất** | `"{channel}:{external_message_id}"` |
| `channel` | Link | Omni Channel | |
| `channel_type` | Data | | Sao chép để lọc nhanh |
| `event_type` | Data | | message, follow, unfollow, user_info, call_ended, oa_echo… |
| `external_message_id` | Data | | |
| `external_sender_id` | Data | chỉ mục | |
| `identity_key` | Data | Bắt buộc, chỉ mục | `"{channel_type}:{external_id chuẩn hóa}"` — điền ngay khi lưu ở `save_inbound`; worker tìm tin theo trường này |
| `identity` | Link | Omni Identity | Điền khi worker xử lý |
| `communication` | Link | Communication | Với kênh email: thư gốc (không mất khi xóa `raw_payload`) |
| `lead` | Link | CRM Lead | Điền khi worker xử lý |
| `received_at` | Datetime | chỉ mục | |
| `text` | Long Text | | Nội dung đã làm sạch |
| `attachments` | JSON | | |
| `sender_profile` | JSON | | |
| `raw_payload` | JSON | | Payload gốc; xóa sau `raw_payload_retention_days` |
| `status` | Select | New / Processing / Done / Retry / Manual / Ignored | chỉ mục |
| `attempts` | Int | 0 | |
| `next_attempt_at` | Datetime | | Với Retry |
| `last_error` | Small Text | | Đã lọc bí mật |
| `processing_started_at` | Datetime | | Để phát hiện kẹt |
| `batch_id` | Data | chỉ mục | Mã lượt xử lý (các tin gộp chung một lượt có cùng mã) |

Chỉ mục ghép: (`identity_key`, `status`), (`status`, `next_attempt_at`).

D1-06 bổ sung: chỉ mục ghép (`lead`, `received_at`) cho Hộp thư; `raw_payload` ở **permlevel 1** (chỉ Omni Admin đọc/ghi; Sales User và vai trò khác không thấy trường này qua Desk/API chuẩn).

## 6. Omni Outbound Message

| Trường | Kiểu | Options | Ghi chú |
| --- | --- | --- | --- |
| `channel` | Link | Omni Channel | |
| `identity` | Link | Omni Identity | Người nhận |
| `lead` | Link | CRM Lead | |
| `text` | Long Text | | |
| `source` | Select | Sale / Bot / System | |
| `sent_by` | Link | User | Với Sale |
| `bot_draft` | Link | Omni Bot Draft | Với tin từ bot |
| `status` | Select | Queued / Sent / Failed / Blocked | Blocked = quá khung gửi hoặc kênh tắt |
| `attempts` | Int | | |
| `next_attempt_at` | Datetime | | Thử lại khi lỗi tạm thời; tác vụ quét đẩy lại |
| `external_message_id` | Data | | |
| `sent_at` | Datetime | | |
| `last_error` | Small Text | | |
| `is_sandbox` | Check | | Tạo khi `sandbox_mode` bật |
| `message_kind` | Select | CS 48h / CS ngoai 48h / ZBS Template | V3-07: do `routing.pick_message_kind` chọn |
| `purpose` | Select | reply / followup / transaction / request_info | V3-07 |
| `zbs_template_id` | Data | | V3-07: chỉ khi là ZBS |
| `quota_consumed` | Check | | V3-07: tin ăn vào quỹ ngoài 48 giờ (Zalo trả `quota_type` thì theo Zalo) |
| `approved_by` | Link | User | V3-07: người đồng ý tiêu quỹ |
| `attachment` | Attach | | V3-09: ảnh/tệp sale gửi kèm (File của Lead trong CRM) |
| `attachment_kind` | Select | image / gif / file | V3-09: loại tin đính kèm Zalo |
| `attachment_name` | Data | | V3-09: tên tệp hiển thị |

V3-07 (ADR 0019): `source = Bot` không có `bot_draft` là tin OA gửi ngoài hệ thống không do admin gõ (kịch bản chatbot Zalo); không tính là sale phản hồi.

D1-06 bổ sung: chỉ mục ghép (`lead`, `creation`) cho Hộp thư.

ADR 0027 (tương tác Messenger), có ở **cả Omni Inbound Message và Omni Outbound Message**, chỉ đọc trên form: `reply_to_mid` (mã tin Meta mà tin này trả lời), `reaction_customer` + `reaction_customer_at` (emoji khách thả, thời điểm sự kiện; rỗng = đã bỏ), `reaction_page` + `reaction_page_by` (emoji sale thả qua CRM, người thả), `hidden_at` + `hidden_by` (thu hồi 1 phía: ẩn trong CRM, khách vẫn thấy; bản ghi gốc giữ nguyên). Riêng Omni Outbound Message: `sticker_id`, `sticker_url` (tin sticker của Meta; ảnh chỉ nhận https từ CDN của Meta); đợt 2: `forwarded_from` (`"<DocType>:<tên>"` của tin gốc khi là bản chuyển tiếp), `attachment_kind` thêm `video`, `audio` (Messenger; Zalo vẫn chỉ `image/gif/file`).

## 7. Omni Duplicate Candidate

| Trường | Kiểu | Options | Ghi chú |
| --- | --- | --- | --- |
| `lead_a` | Link | CRM Lead | |
| `lead_b` | Link | CRM Lead | `lead_a < lead_b` theo tên để không có cặp đảo |
| `reason` | Small Text | | Ví dụ: "Cùng tên, khác SĐT" |
| `score` | Int | | Độ giống 0–100 |
| `status` | Select | Open / Merged / Dismissed | |
| `decided_by` | Link | User | |

Ràng buộc duy nhất trên (`lead_a`, `lead_b`).

## 8. Omni Call (Đợt 2)

| Trường | Kiểu | Options | Ghi chú |
| --- | --- | --- | --- |
| `provider` | Data | | |
| `call_id` | Data | Bắt buộc, **duy nhất** | Mã cuộc gọi của nhà cung cấp |
| `direction` | Select | Inbound / Outbound | |
| `from_number`, `to_number` | Data | | E.164 |
| `status` | Select | Answered / Missed / Busy / Failed / Voicemail | |
| `started_at`, `answered_at`, `ended_at` | Datetime | | |
| `duration_seconds` | Int | | |
| `agent` | Link | User | Sale nghe máy |
| `lead` | Link | CRM Lead | |
| `crm_call_log` | Link | CRM Call Log | Bản ghi tương ứng trong CRM |
| `callback_task` | Link | CRM Task | Với gọi nhỡ |
| `recording_ref` | Data | | Đường dẫn/ID ghi âm phía nhà cung cấp |
| `recording_file` | Attach | private | Đợt 3. **CHƯA LÀM** (D3-04) |
| `transcript` | Long Text | | Đợt 3. **CHƯA LÀM** (D3-04) |
| `summary` | Small Text | | Đợt 3. **CHƯA LÀM** (D3-04) |
| `stt_status` | Select | Not Started / Pending / Done / Failed / Skipped | Đợt 3. **CHƯA LÀM** (D3-04) |
| `raw_events` | JSON | | Các sự kiện gốc của cuộc gọi (kể cả trung gian) |
| `unverified` | Check | | Không xác minh được `call_id` qua API nhà cung cấp |

## 9. Omni Question Group (Đợt 2)

| Trường | Kiểu | Ghi chú |
| --- | --- | --- |
| `group_name` | Data, duy nhất | Ví dụ: Cách vận hành dịch vụ, Gói dịch vụ, Thanh toán back-to-back, Phí dịch vụ, Khiếu nại (danh sách mẫu: `mau/nhom-cau-hoi-mau.csv`) |
| `always_handoff` | Check | Nhóm luôn chuyển người (Phí dịch vụ, Đàm phán hợp đồng, Khiếu nại) |
| `auto_send_enabled` | Check, **chỉ đọc**, mặc định 0 | Đợt 3 (D3-05): bot tự gửi cho nhóm này. KHÔNG sửa tay được: `validate` từ chối 0->1 trừ khi do `bot.auto_send.enable_group` (Omni Admin, đạt điều kiện, khóa hạ tầng mở); tắt (1->0) thì ai có quyền ghi cũng tắt được. Nhóm `always_handoff` không bao giờ có cờ này (đặt `always_handoff` cho nhóm đang bật thì cờ tự tắt) |
| `auto_send_enabled_at`, `auto_send_enabled_by` | Datetime, Link User (chỉ đọc) | Dấu vết ai bật, khi nào (xóa khi tắt; lịch sử bật/tắt nằm ở Version vì DocType bật `track_changes`, và ở Comment dấu `omni:auto_send:enable|disable|breaker:<mã>` trên nhóm) |
| `active` | Check, mặc định 1 | V2-06: 0 = nhóm ngừng hoạt động (không xóa, giữ lịch sử) |
| `shadow_only` | Check, mặc định 0 | V2-06: nháp trả lời của nhóm luôn Shadow |
| `description` | Small Text | Mô tả để bot phân loại |

## 10. Omni Knowledge Item (Đợt 2)

| Trường | Kiểu | Ghi chú |
| --- | --- | --- |
| `item_id` | Data, duy nhất, làm `name` | Dạng `KB-00001`; giữ nguyên khi nhập lại CSV (nhập = cập nhật theo `item_id`) để nháp và lần chấm cũ vẫn trỏ đúng |
| `question_group` | Link Omni Question Group | |
| `question` | Small Text | Câu hỏi mẫu (có thể nhiều cách hỏi, mỗi dòng một cách) |
| `answer` | Text Editor | Câu trả lời chuẩn |
| `active` | Check | |
| `source` | Data | Tài liệu gốc |
| `owner_user` | Link User | Người chịu trách nhiệm nội dung |
| `reviewed_at` | Date | Lần duyệt gần nhất |

Bật `track_changes`. Mỗi lần lưu, tăng `Omni Settings.kb_version` (Int, chỉ đọc) để gắn vào `Omni Bot Run`.

## 11. Omni Score Rule (bảng con, Đợt 2)

| Trường | Kiểu | Ghi chú |
| --- | --- | --- |
| `signal` | Select | has_phone / has_email / known_org (đã gắn nhà thầu) / replied / needs_discussed / intent_service_info / intent_fee / intent_proposal / decision_maker_engaged / pain_point_stated / repeat_contact / complaint (bảng tín hiệu điểm quan tâm B2B — `docs/14` mục 3.2) |
| `points` | Int | Có thể âm |
| `active` | Check, mặc định 1 | V2-06: 0 = quy tắc tắt (không xóa) |
| `note` | Data | |

## 12. Omni Bot Run (Đợt 2)

| Trường | Kiểu | Ghi chú |
| --- | --- | --- |
| `kind` | Select | Analyze / Reply / Summarize / Judge |
| `lead`, `identity` | Link | |
| `batch_id` | Data | Lượt xử lý tương ứng |
| `model` | Data | |
| `prompt_version`, `kb_version` | Data, Int | |
| `input_tokens`, `output_tokens`, `cache_read_tokens` | Int | |
| `latency_ms` | Int | |
| `status` | Select | Success / Invalid Output / Error / Skipped Budget / Skipped Switch |
| `request_digest` | Small Text | Tóm tắt đầu vào (không lưu toàn văn nếu không cần) |
| `output_json` | JSON | Kết quả có cấu trúc |
| `error` | Small Text | |

## 13. Omni Bot Draft (Đợt 2, hiện cho sale từ Đợt 3)

| Trường | Kiểu | Ghi chú |
| --- | --- | --- |
| `lead`, `identity`, `channel` | Link | |
| `batch_id` | Data | |
| `bot_run` | Link Omni Bot Run | |
| `mode` | Select | Shadow / Draft / Auto | Chế độ lúc soạn |
| `action` | Select | Reply / Handoff | Bot đề xuất trả lời hay chuyển người |
| `question_group` | Link Omni Question Group | |
| `draft_text` | Long Text | |
| `kb_items` | Small Text | Danh sách Omni Knowledge Item đã dùng |
| `confidence` | Float | 0–1, bot tự đánh giá (chỉ tham khảo) |
| `guard_flags` | Small Text | Lý do bị chặn bởi `guards.py` nếu có |
| `handoff_reason` | Small Text | |
| `decision` | Select | Pending / Sent As Is / Sent Edited / Discarded / Expired / Shadow Compared |
| `final_text` | Long Text | Câu thực sự gửi |
| `edit_distance_ratio` | Float | Mức sửa so với nháp, 0 = giữ nguyên |
| `sale_reply_text` | Long Text | Chế độ ngầm: câu sale thật sự gửi trong lượt đó, để so |
| `sale_handoff` | Check | Chế độ ngầm: câu sale chứa thông tin ngoài kho tri thức (thực tế là ca phải chuyển người) |
| `decided_by`, `decided_at` | Link User, Datetime | |
| `rating` | Select | Đúng / Sai / Thiếu | Sale chấm |
| `rating_note` | Small Text | Ghi chú khi chấm; đánh dấu "nghiêm trọng" nếu sai về giá, cam kết, y khoa |
| `critical` | Check | Sale hoặc người duyệt đánh dấu lỗi nghiêm trọng |
| `handoff_notified_at` | Datetime | D3-02: lúc gửi thông báo chuyển người (hệ thống ghi) |
| `handoff_email_done` | Check | D3-02: đã gửi email nhắc vì sale chưa mở thông báo sau 10 phút |
| `handoff_logs` | Small Text | D3-02: nhật ký các kênh thông báo đã thử (không chứa nội dung khách) |

D3-01: ở `mode = Draft`, `decision` đi `Pending -> Sent As Is | Sent Edited | Discarded | Expired`; `final_text`, `edit_distance_ratio`, `decided_by`, `decided_at` ghi khi sale quyết định (`Expired`: chỉ `decided_at`); `rating`, `rating_note`, `critical` do sale chấm sau khi gửi/bỏ. Tin ra từ nháp có `Omni Outbound Message.bot_draft`, `source = Sale`. Không thêm trường nào so với bảng trên. Quyền theo Lead: xem `permissions.py::bot_draft_conditions` và mục 16.

## 14. Omni Eval Case và Omni Eval Run (Đợt 2)

**Omni Eval Case**: `case_id` (duy nhất), `source` (Real / Synthetic), `conversation` (JSON: danh sách lượt `{role: customer|sale, text}`), `expected_fields` (JSON), `expected_action` (Reply/Handoff), `expected_group` (Link), `reference_answer` (Long Text), `critical` (Check — ca bắt lỗi nghiêm trọng), `active` (Check).

**Omni Eval Run**: `started_at`, `prompt_version`, `kb_version`, `model`, `total_cases`, `field_accuracy` (Float), `action_accuracy`, `answer_pass_rate`, `critical_failures` (Int), `result_json` (JSON chi tiết từng ca), `passed_gate` (Check).

## 15. Custom Field trên `CRM Lead` (fixtures, module Omni CRM)

Đợt 1:

| fieldname | Kiểu | Nhãn |
| --- | --- | --- |
| `omni_first_channel` | Data | Kênh liên hệ đầu tiên |
| `omni_last_inbound_at` | Datetime | Khách liên hệ gần nhất |
| `omni_first_response_at` | Datetime (chỉ đọc) | V2-01: phản hồi đầu tiên — tin ra đầu tiên không sandbox của Sale/Bot, thư gửi đi từ CRM, cuộc gọi đi được nghe máy (nhật ký chăm sóc: V2-04). Luôn giữ giá trị sớm nhất; `crm_bridge/first_response.py`, patch điền ngược `backfill_lead_first_response` |
| `omni_manual_fields` | JSON (ẩn) | Các trường sale đã sửa tay — bot không ghi đè |
| `omni_phone_e164` | Data (ẩn, search_index) | SĐT chuẩn hóa từ `mobile_no`/`phone` (xem `docs/04` mục 4) |
| `omni_email_norm` | Data (ẩn, search_index) | Email chuẩn hóa |
| `omni_merged_into` | Link CRM Lead | Lead bị gộp trỏ về Lead chính |

Đợt 2:

| fieldname | Kiểu | Nhãn |
| --- | --- | --- |
| `omni_need` | Small Text | Nhu cầu |
| `omni_products` | Small Text | Sản phẩm quan tâm |
| `omni_quantity` | Data | Số lượng |
| `omni_timeframe` | Data | Thời điểm mua dự kiến |
| `omni_urgency` | Select (Thấp/Trung bình/Cao) | Mức gấp |
| `omni_intent` | Select (giữ giá trị cũ Tư vấn/Hỏi giá/Đặt hàng/Khiếu nại/Khác; V21-06 thêm Hỏi cách vận hành/Hỏi gói dịch vụ/Hỏi phí/Yêu cầu đề xuất) | Ý định gần nhất; bot theo câu lệnh v2 chỉ ghi giá trị mới (xem `docs/08` mục 3) |
| `omni_score` | Int | Điểm Lead |
| `omni_score_breakdown` | JSON (ẩn) | Các tín hiệu đã cộng điểm và lúc đã báo Lead nóng |
| `omni_bot_summary` | Small Text | Tóm tắt của bot |

Custom Field trên `CRM Organization` và `Contact` (D1-13, V2-02): xem `docs/14` mục 1.1 và 1.3. V2-02 thêm 3 trường kỹ thuật ẩn trên Contact (`omni_phones_e164`, `omni_emails_norm`, `omni_erased_keys`, ADR 0011). Omni Settings thêm 12 trường `fit_*` + `fit_scoring_enabled` + `prospect_import_enabled`. Lý do "Không phù hợp": dùng `CRM Lost Reason` có sẵn (ĐQ-8), không thêm `omni_lost_reason`. Trên CRM Lead: trường chiến dịch và quy kết `omni_campaign` … `omni_is_test` (D1-15, `docs/15` mục 6); trường bot B2B `omni_tax_id_stated`, `omni_hospitals_mentioned`, `omni_current_pain`, `omni_decision_role` (D2-05, `docs/14` mục 7).

Custom Field trên `Communication` và `Comment` (Đợt 1):

| fieldname | Kiểu | Ghi chú |
| --- | --- | --- |
| `omni_inbound_message` | Link Omni Inbound Message (ẩn, search_index) | Khóa chống ghi trùng dòng thời gian cho tin vào |
| `omni_outbound_message` | Link Omni Outbound Message (ẩn, search_index) | Như trên cho tin ra |

Chỉ tạo trên DocType mà ADR "Cách hiển thị hội thoại" (D1-06) thực sự dùng.

Trước khi tạo, đọc `crm_lead.json` của app `crm` để: (1) không trùng trường có sẵn (ví dụ `mobile_no`, `email`, `organization`, `source`, `lead_owner`, `status`); (2) chọn `insert_after` hợp lý. Ghi kết quả đọc vào `docs/12-quyet-dinh-mo.md`.

### 15.1 Omni Prospect Import (V2-03)

Nhật ký mỗi lần nhập danh sách nhà thầu. Đặt tên `NHAP-.YYYY.-.#####`. Chỉ Omni Admin và System Manager đọc/ghi. Việc chạy do `omni_crm.prospect.importer` (việc nền, hàng đợi `omni`); form chỉ có nút **Chạy thử**, **Nhập thật**, **Tải mẫu trống**.

| Trường | Kiểu | Ghi chú |
| --- | --- | --- |
| `file` | Attach | Tệp `.xlsx` đúng mẫu; **bắt buộc riêng tư** (`/private/files/…`), để ngoài repo |
| `source_name`, `file_hash` | Data | Tên tệp, SHA-256 (nhận ra tệp đã nhập) |
| `dry_run` | Check | Bật: chỉ lập kế hoạch, không ghi gì vào CRM |
| `status` | Select | Draft / Queued / Running / Done / Failed / Refused |
| `run_by` | Link User | Người bấm nhập; quyền kiểm theo người này cả trong việc nền |
| `started_at`, `finished_at`, `duration_seconds` | | |
| `nt_*`, `gt_*`, `lh_*` | Int | Tạo mới / cập nhật / bỏ qua / lỗi của NhaThau, GoiThau, NguoiLienHe |
| `confirm_count` | Int | Số mục "cần xác nhận" |
| `error_file` | Attach (riêng tư) | `.xlsx` cùng định dạng mẫu: dòng 1-3 như mẫu, các dòng lỗi từ dòng 4 kèm cột `error_reason`, sheet `CanXacNhan`; sửa rồi nhập lại được |
| `summary_json`, `confirm_json`, `reconcile_json`, `warnings` | Code / Long Text | Báo cáo số dòng, danh sách cần xác nhận, đối chiếu với TongHop/`calc_*`/ThamSo, cảnh báo |

Trường ẩn `omni_import_snapshot` (Long Text) trên CRM Organization và Contact: giá trị importer đã ghi lần cuối (ADR 0012).

## 16. Quyền

| DocType | Omni Admin | Sales User | Omni Knowledge Editor |
| --- | --- | --- | --- |
| Omni Settings, Omni Channel | Đọc/Ghi | — | — |
| Omni Inbound/Outbound Message | Đọc/Ghi | Đọc (theo Lead xem được); tạo Outbound **chỉ qua API** `send_reply` | — |
| Omni Identity | Đọc/Ghi | Đọc | — |
| Omni Duplicate Candidate | Đọc/Ghi | Đọc; quyết định (gộp/bỏ) **chỉ qua API** `merge`/`dismiss`, có kiểm quyền ghi hai Lead | — |
| Omni Call | Đọc/Ghi | Đọc theo Lead | — |
| Omni Knowledge Item, Omni Question Group | Đọc/Ghi | Đọc | Đọc/Ghi |
| Omni Bot Draft | Đọc/Ghi | Đọc/Ghi theo Lead | Đọc |
| Omni Bot Run, Omni Eval * | Đọc/Ghi | — | Đọc |
| Omni Bid Award, Omni Hospital Alias | Đọc/Ghi | Đọc theo quyền xem Lead của nhà thầu (V2-02: Bid Award có `permission_query_conditions` + `has_permission` theo Lead có `organization` = nhà thầu, và API `api/organization.py`; Hospital Alias chỉ Omni Admin) | — |
| Omni Prospect Import | Đọc/Ghi (chạy nhập); System Manager cũng được. Không có vai trò "CRM Manager" trên site; Sales Manager KHÔNG được nhập | — | — |
| Omni Campaign | Đọc/Ghi (Sales Manager đọc, tạo, sửa từ ADR 0029) | Đọc | — |
| Omni Lead Form | Đọc/Ghi (khóa bí mật chỉ Omni Admin) | — | — |
| Omni Care Log | Đọc/Ghi | Đọc/ghi kết quả qua API theo quyền Lead | — |
| Omni Chat Template | Đọc/Ghi (Sales Manager cũng Đọc/Ghi) | Đọc; mẫu đã điền chỉ qua API `messenger_tab.get_templates` (quyền đọc Lead) | — |
| Omni Content Post, Omni Content Pillar | Đọc/Ghi (Sales Manager cũng Đọc/Ghi, lên lịch bài; ADR 0029) | Đọc | — |

"Theo Lead xem được": kiểm tra quyền bằng `frappe.has_permission("CRM Lead", doc=lead)` trong API, không chỉ dựa vào quyền DocType.

D3-01: `Omni Bot Draft` cũng có `permission_query_conditions` và `has_permission` theo Lead (cùng hàm với tin nhắn); Sales User không có quyền DocType nên chỉ thấy nháp qua `api/drafts.py` / `get_thread` (kiểm quyền Lead). Omni Knowledge Editor đọc được nháp chỉ khi CRM cho phép xem Lead đó; báo cáo "Bot nháp" cho Omni Admin và Omni Knowledge Editor (chỉ số đếm).

D1-06 thi hành: hook `permission_query_conditions` và `has_permission` (`omni_crm/permissions.py`) cho Omni Inbound Message, Omni Outbound Message và Omni Identity, gọi lại điều kiện của chính CRM Lead nên Sales User chỉ thấy bản ghi có `lead` mà CRM cho phép xem; Omni Admin, System Manager, Administrator không bị giới hạn. Bản ghi chưa có `lead` chỉ Omni Admin/System Manager thấy.

## V2-05 · Thi hành lead quảng cáo (ghi chú)

- `Omni Campaign` (autoname theo `campaign_name`; `campaign_key` ẩn duy nhất = "nền tảng:mã", NULL khi chưa có mã; `status` Nháp/Đang chạy/Tạm dừng/Kết thúc) và `Omni Lead Form` (autoname hash; `form_key` ẩn duy nhất; `secret` Password `permlevel 1` chỉ Omni Admin; thêm `last_reconciled_at`) cùng bảng con `Omni Lead Form Field` (`source_key`, `target` kiểm hợp lệ ở controller: các đích của docs/15 mục 5 hoặc `custom:<tên>`).
- `Omni Channel`: thêm `meta_page_id, meta_app_secret, meta_verify_token, meta_page_access_token` (Password) và `webform_captcha_provider, webform_captcha_secret`. `Omni Identity.channel_type` thêm 3 loại.
- `Omni Settings`: `ads_silence_hours`, `webform_rate_limit`, `webform_rate_window_minutes`; `default_lead_source_map` mặc định có 6 khóa (site cũ giữ giá trị đã lưu, mã lấy mặc định cho khóa còn thiếu nên KHÔNG cần patch).
- CRM Lead: 14 Custom Field của docs/15 mục 6 trừ `omni_first_response_at` đã có (`omni_ad_platform, omni_campaign, omni_last_campaign, omni_ad_id, omni_adset_id, omni_form_id, omni_utm_*, omni_click_id, omni_landing_page, omni_is_test`), đều chỉ đọc với người dùng.

## ADR 0029 · Marketing Hub: bài đăng

- `Omni Content Post` (autoname theo `post_code`, mã bài chữ thường/số/gạch ngang, tối đa 40 ký tự, dùng làm `utm_content`): `campaign` (Link Omni Campaign), `title`, `status` (Nháp, Đã lên lịch, Đang đăng, Đã đăng, Lỗi, Cần kiểm, Bỏ lỡ, Đã huỷ; chỉ đổi qua `api/content_posts.py`), `platform` (Facebook, Zalo OA, Khác), `publish_mode` (Đăng tay, Tự động), `publish_at`, `content`, `image` (Attach Image, tệp gắn vào chính bài), `pillar`, `funnel_stage`, `landing_url`, `utm_link` (tự sinh), kết quả đăng (`post_url`, `published_at`, `external_post_id`, `last_attempt_at`, `last_error`), chỉ số (`reach`, `reactions`, `comments`, `shares`, `link_clicks`, `metrics_synced_at`, `metrics_json`) và `lead_count`, `deal_count` (tính lại mỗi đêm).
- `Omni Content Pillar`: `pillar_name` (duy nhất), `enabled`, `description`. Patch `v3.seed_content_pillars` nạp 4 trụ cột khi bảng trống.
- `Omni Campaign` thêm `objective`, `target_audience`, `landing_url`, `lead_target`, `brief`; `status` thêm Đã huỷ.
- `CRM Lead.omni_content_post` (Link, chỉ đọc, search_index): bài đăng của lần chạm đầu, gán trong `crm_bridge/form_lead.py::_attribution_values` khi `utm_content` khớp mã bài, và gán bù khi tạo bài sau (`marketing/attribution.py::relink_post`).

## ADR 0030 · Ảnh bài đăng trên kho S3

- `File.omni_s3_key` (Custom Field, chỉ đọc): khóa object trên kho S3 của ảnh bài đăng đã chuyển khỏi đĩa. File đó có `file_url` = `/api/method/omni_crm.api.media.post_image?file=<File>` và `content_hash` rỗng (để Frappe không gộp tệp mới trùng nội dung vào tệp không còn trên đĩa).
- `Omni Content Post.image` thêm `no_copy`.
- `Omni Settings`: mục "Kho ảnh S3 (MinIO, ADR 0030)", xem docs/09.
