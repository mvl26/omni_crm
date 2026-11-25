# Kế hoạch bot theo "Kịch bản BOT Miyano v3" (K0 – khảo sát)

Ngày: 17/11/2026 · Nhánh: `v4/kich-ban-bot` (tách từ `3e3208f`, đang chạy trên yhct) · Phiên K0 chỉ khảo sát, không sửa code.

Nguồn: code `omni_crm` trong nhánh này; dữ liệu đọc (chỉ đọc) trên site `miyano` ngày 17/11; tài liệu kịch bản `docs/kich_ban_bot_miyano_v3.md`.

> **Lưu ý về site.** Trong bench chỉ có site `miyano` (phục vụ qua yhct.miyano.com.vn). Bộ prompt gốc ghi `crm.miyano.com.vn`; khối bối cảnh trong CLAUDE.md đã sửa lại cho đúng.

---

## 1. Đường đi của một tin khách hiện nay

```
Webhook (api/webhooks.py) ─► Omni Inbound Message (status New, dedupe_key)
   └► ingest/gateway.enqueue_identity ─► hàng đợi "omni"
pipeline/worker.process_identity (khóa theo identity, gom lô ≤ BATCH_LIMIT)
   └► _process_batch: adapter.prepare_batch (Zalo: follow/unfollow, echo)
        → identity/resolver.resolve_full (khớp khách, tạo Lead)
        → adapter.after_resolve (Zalo: ghi echo thành Omni Outbound Message)
        → timeline.append (Communication trên Lead/Deal) → Done, cập nhật Omni Identity.last_inbound_at
   └► post_batch_hooks(batch_id, lead)
        ├ channels.zalo.phone_request.on_batch_done  (hẹn thẻ xin SĐT sau 60 giây, job run_due)
        ├ telephony.callbacks.after_batch
        └ bot.analyze.enqueue_analyze  ──(xong)──► scoring + bot.reply.enqueue_reply
          (bot_analyze_enabled tắt → scoring + enqueue_reply ngay)
bot/reply.reply_batch (khóa Redis theo lô) → _reply_batch:
   bỏ qua nếu bot_reply_mode = Off · tin cuối không phải tin khách · đã có nháp của lô ·
   handoff.is_bot_paused(identity) (bot_paused_until) · sale đã trả lời sau tin · > MAX_DRAFTS_PER_HOUR
   → analysis_of(batch) lấy intent/wants_human từ lượt Analyze CÙNG LÔ
   → make_draft: Kho tri thức (knowledge.kb_items + kb_docs.passages_for, ADR 0030)
        → ai_client.call_tool("Reply", prompt reply v3, REPLY_MAX_TOKENS 2000)
        → evaluate(): bot/guards.run_guards (chốt chặn 1–7)
        → templates.pick (ADR 0031: mẫu Hỏi giá / Đặt lịch demo cho sale điền)
        → Omni Bot Draft (mode Shadow | Draft)
   → _after_insert (chỉ Draft): expire_stale_for_lead;
        Handoff → auto_send.try_holding + handoff.handoff (tạm dừng bot handoff_pause_hours=24, báo người phụ trách)
        Reply   → auto_send.try_auto_send (nhóm auto_send_enabled + site_config omni_auto_send_allowed + đủ 30 nháp ≥95%)
                  không tự gửi được → notify_pending_draft (chuông CRM + Notification Log)
Sale gửi/sửa/bỏ nháp: api/drafts.py (tab Zalo của Lead, V3-13) → pipeline/outbound.queue_outbound
```

Job định kỳ liên quan: `reply.expire_pending_drafts` (nháp quá hạn), `reply.compare_pending` (so nháp với tin sale gửi), `auto_send.run_breaker_checks` (tự tắt Auto khi tỉ lệ đạt tụt), `handoff.email_unread_handoffs`, `zalo.phone_request.run_due`, `crm_bridge.response_sla.run`.

Prompt: Analyze `v1` (phân tích **cả hội thoại**), Reply `v3` (`reply_prompt_version`). Omni Question Group: AI chọn nhóm; nhóm `always_handoff` → chốt chặn chuyển người; `shadow_only` → nháp ẩn; `auto_send_enabled` → tự gửi khi qua bộ kiểm (auto_send.py).

## 2. Nguồn các cờ chốt chặn

| Cờ | Sinh ở | Dựa vào |
| --- | --- | --- |
| `handoff_topic:y_dinh_hoi_gia` | `guards.guard_handoff_topic` → `intent_forces_handoff` | `intent` của **lượt Analyze cùng lô**, mà lượt này phân tích **cả hội thoại** (không phải `CRM Lead.omni_intent`, nhưng cùng nguồn). Từ v313 (16/11 17:41): Hỏi phí chỉ chặn khi lời khách ở lượt hiện tại có chữ giá/phí (`sensitive_topics` → `hoi_gia`). Không dựa vào nội dung câu trả lời. |
| `handoff_topic:nhom_luon_chuyen_nguoi` | cùng hàm | Nhóm **AI Reply chọn** là nhóm `always_handoff` (Hỏi giá, Đặt lịch demo…) |
| `tra_loi_rong` | `guards.guard_length` | Câu trả lời rỗng; xảy ra khi AI trả `action=handoff` kèm văn bản rỗng nhưng vẫn qua chốt độ dài |
| `kb_trong` / `kb_khong_co_doan_lien_quan` | `reply.make_draft` trước khi gọi AI | Kho trống / không đoạn tài liệu nào khớp |
| `info:mau_tin:price` / `:demo` | `reply.make_draft` sau `templates.pick` | Nháp chuyển người + nhóm/ý định/từ khóa giá hoặc demo |

## 3. Tin Zalo kèm nút

- Code hiện có (`channels/zalo/client.py`): `send_text`, `send_request_user_info` (thẻ xin thông tin, `template_type: request_user_info`), `send_media`, hồ sơ, quỹ tin. **Chưa có hàm gửi tin kèm nút.**
- Tài liệu đã tìm được (bên thứ ba, chưa phải tài liệu chính thức của Zalo — `# VERIFY:`):
  - Tin tư vấn gửi qua `POST /v3.0/oa/message/cs`, `message.attachment = {type: "template", payload: {buttons: [{title, type, payload}]}}`.
  - Loại nút: `oa.open.url`, `oa.query.show`, `oa.query.hide` (payload là chuỗi, ≤ 1.000 ký tự), `oa.open.sms`, `oa.open.phone`. Tiêu đề nút ≤ 100 ký tự; nội dung tin ≤ 2.000 ký tự.
  - Khách bấm `oa.query.*` → Zalo gửi về OA **một tin nhắn của khách có nội dung là payload** (`show`: hiện trong khung chat, `hide`: ẩn). Nghĩa là webhook là `user_send_text` bình thường, adapter hiện tại sẽ nhận payload như chữ khách gõ.
  - **Chưa xác nhận:** số nút tối đa mỗi tin, việc gói OA hiện tại có được gửi tin dạng nút không, và tin dạng nút có tính vào quỹ tin như tin thường không.
- **Kết quả gửi thử 17/11** (OOUT-2026-00000040, tới tài khoản thử Lead CRM-LEAD-2026-00003, người dùng cho phép): `POST /v3.0/oa/message/cs` với `message = {text, attachment: {type: "template", payload: {buttons: [5 nút oa.query.show/hide]}}}` → `error 0`, `quota: {quota_type: "reply", remain: 8, total: 8}`. **Trên app Zalo điện thoại hiện đủ 5 nút; trên Zalo PC/web chỉ thấy chữ, không có nút.** Echo webhook là `oa_send_text`, không mang nút. Hệ quả: kịch bản phải luôn có cách gõ thay nút (khách dùng PC), và ghi nút vào Omni Outbound Message vì echo không có. `quota reply 8/8`: nghi giới hạn số tin trả lời mỗi lượt khách – `# VERIFY:`.
- **Khách bấm nút (thử 17/11 10:07–10:08):** webhook là `user_send_text` bình thường, `message.text` = đúng chuỗi payload (`#MENU_SPD`, `#GAP_NHANVIEN`…), không có trường nào khác. Nút `oa.query.show` và `oa.query.hide` **không phân biệt được ở webhook**. Nút **không biến mất** sau khi bấm: khách bấm lại nhiều lần, mỗi lần là một tin. Hệ quả cho K2: bộ định tuyến nhận diện mã nút bằng tiền tố `#` + mã trong danh sách đóng (không qua AI); chống lặp khi khách bấm cùng nút liên tiếp; lô tin gồm nhiều lần bấm chỉ xử lý lần bấm cuối. Hiện tại bot coi `#MENU_SPD` là câu hỏi, và `#GAP_NHANVIEN` kích hoạt chuyển người (nháp BDR-2026-0000006).
- Đề xuất trước K2: gửi thử **một** tin dạng nút tới tài khoản Zalo thử (không phải khách) bằng một lệnh riêng, sau khi người dùng đồng ý; ghi lại phản hồi của API và sự kiện webhook khi bấm. Không làm được thì dừng K2 theo quy tắc của prompt.
- Nguồn: [eSMS – Tin Zalo tư vấn dạng button](https://developers.esms.vn/esms-api/ham-gui-tin/tin-zalo-tu-van-dang-button), [RedAI Zalo SDK – Consultation Service](https://unpkg.com/@warriorteam/redai-zalo-sdk@1.11.1/docs/CONSULTATION_SERVICE.md).

## 4. Hai mẫu tin trong webhook `oa_send_template` (Lead CRM-LEAD-2026-00026, 11/11)

| Thời điểm | Bản ghi | Kết luận |
| --- | --- | --- |
| 16:34:25 | OOUT `…29`: source `Bot`, text rỗng, owner `Guest`, `message_kind` "CS ngoai 48h", `quota_consumed` 1 | **Không phải code CRM gửi.** Bản ghi do webhook tạo qua `channels/zalo/worker_hooks._create_echo_outbound`: tin OA gửi ra mà **không có `admin_id`** được quy ước là "kịch bản chatbot Zalo / ứng dụng khác" → nguồn `Bot`. Thời điểm trùng đúng giây với tin khách → đây là tin tự động của **Zalo OA Manager** (mẫu "Bạn có một tin nhắn mới", templateId 16471 theo người dùng; raw payload không lưu template id nên không đối chiếu được). |
| 16:36:10 | OOUT `…30`: source `System`, purpose `request_info`, "CS 48h", quota 0, text "Miyano xin phép nhận thông tin liên hệ…" | Thẻ xin SĐT của **CRM** (`phone_request`, V3-07) — tương ứng templateId 16134. Echo của nó được bỏ qua đúng (`_is_own_send`). |

**Sai lệch phát hiện:** `_echo_route` tính loại tin bằng tin vào cuối cùng **đã gắn identity**; tin khách cùng lô (16:34:25) lúc đó chưa được gắn identity (chỉ gắn khi lô Done) nên bị bỏ sót → ghi "ngoài 48h" và trừ 1 quỹ, trong khi thực tế còn trong 48 giờ. Báo cáo quỹ tin của CRM vì thế lệch (tăng giả). Đề xuất sửa ở K1 (ngoài phạm vi nếu người dùng muốn tách).

**Hai bot song song:** chatbot/tin tự động trên Zalo OA Manager **đang chạy** cùng bot CRM. Cần tắt trước khi bật bot kịch bản (việc người dùng làm tay).

## 5. Messenger

- Kênh `messenger-mvl` (Meta Messenger) đang **bật** trên site.
- Code Messenger (`channels/meta_messenger/`, `api/messenger_tab.py`, `api/meta_connect.py`, ADR 0022/0023/0027/0028, tab Messenger trong CRM) **chỉ có trên máy chủ, chưa commit**, là việc của một dev khác. Nhánh này không có code đó, nên chưa trả lời được: đã gửi tin được chưa, có quick replies / postback / kiểm tra hạn 24 giờ chưa.
- Hệ quả: phần Messenger của K2 phải chờ code đó được commit vào repo; không được viết bộ thứ hai.

## 6. Lưu trạng thái hội thoại – đề xuất

**Trạng thái máy (theo cuộc trò chuyện, tức theo kênh)** — thêm trường vào `Omni Identity` (mỗi identity là một khách trên một kênh, đã có `bot_paused_until`, `last_inbound_at`):

| Trường | Kiểu | Ý nghĩa |
| --- | --- | --- |
| `script_node` | Data | Mã nút kịch bản đang đứng (MENU_SPD, TAILIEU_SPD, …) |
| `script_step` | Data | Câu đang hỏi dở trong bước (vd `3a.email`, `3b.spd.q2`) |
| `script_reask_count` | Int | Số lần hỏi lại liên tiếp (≥2 thì lần sau chuyển người) |
| `script_greeted_at` | Datetime | Đã gửi tin chào chưa (khách cũ không chào lại) |
| `script_last_bot_at` | Datetime | Tin cuối của bot (mốc nhắc 3 ngày C.1) |

**Trường trên CRM Lead (cho nhân viên đọc và báo cáo)** — Custom Field qua fixtures:

| Bảng kịch bản | Trường đề xuất | Ghi chú |
| --- | --- | --- |
| 1.6, 2.7 | `omni_services` (Small Text, nhiều giá trị: Vật tư/Phần mềm/SPD/Khác) | "Dịch vụ quan tâm", giữ cả khi khách hỏi nhiều dịch vụ. Hiện có `omni_products` (sản phẩm cụ thể) – giữ nguyên |
| 2.7 | `omni_journey_need` (Select: Tìm hiểu/Tài liệu/Báo giá/Demo/Gửi danh mục) | Khác `omni_need` (tóm tắt tự do của AI) |
| 1.6, 2.7, 3.7, 4.8, 5.5 | `omni_journey_status` (Select, 10 trạng thái kịch bản) | Trường riêng, không đụng CRM Lead Status |
| 1.6 | `omni_bot_enabled` (hiển thị, tính từ Identity) | Có thể chỉ hiện trên tab Zalo, không cần lưu |
| 2.7, C.3 | Câu hỏi bot chưa trả lời được | Đề xuất ghi vào `Omni Eval Case` (trạng thái "chưa duyệt") để vừa bổ sung Kho vừa làm ca kiểm thử – không thêm DocType |
| 3.7 | `omni_docs_requested`, `omni_quote_request` (Small Text), `omni_scale` (Small Text JSON), `omni_data_consent_at` (Datetime) | Email/SĐT/Tên/Đơn vị dùng trường gốc của CRM Lead |
| 3.5, 4.1 | `omni_docs_sent_at`, `omni_quote_sent_at` (Datetime) | Nút "Đã gửi tài liệu/báo giá" |
| 4.8 | `omni_followup_at`, `omni_followup_channel`, `omni_followup_response` | |
| 4.8 | Lịch demo: Frappe `Event` (đang có, 0 bản ghi) liên kết Lead; `omni_demo_event` (Link Event) | Không thêm DocType lịch |
| 5.5 | `omni_demo_result`, `omni_demo_note`, `omni_no_bot_followup`, `omni_demo_feedback`, `omni_concern`, `omni_lost_reason` | |
| C.4 | `omni_do_not_contact` (Check) | Chặn mọi tin chủ động |

**Bảng quy đổi trạng thái kịch bản → CRM Lead Status gốc** (chỉ đề xuất; cập nhật status gốc tự động hay không do người dùng chốt):

| Trạng thái kịch bản | CRM Lead Status |
| --- | --- |
| Mới | New |
| Đang tư vấn, Chờ gửi tài liệu, Chờ báo giá | Contacted |
| Đã hẹn demo, Đã demo, Thương lượng | Qualified |
| Đồng ý mua – chờ ký hợp đồng | Qualified (chuyển Deal khi ký) |
| Chăm sóc lại | Nurture |
| Thua | Unqualified |
| Không liên hệ | Unqualified (+ `omni_do_not_contact`) |

**DocType mới cần thêm** (ngoài danh sách `docs/02` → mỗi cái cần ADR và người dùng duyệt):

1. `Omni Script Node` + bảng con `Omni Script Button` – kho kịch bản (mã, nội dung có biến, nút ≤ 20 ký tự → mã đích, kênh, trạng thái Đang dùng/Cần duyệt/Tắt). K2.
2. `Omni Scheduled Message` – tin hẹn giờ (loại, identity, lead, due_at, trạng thái, khóa chống trùng). K5.
3. Bảng con `Omni Service Recipient` trong Omni Settings – người nhận chính/thay theo dịch vụ, người tuân thủ, quản lý. K3 (bảng con, không phải màn hình riêng).

## 7. Việc và thông báo

- Chuyển người hiện báo **người được gán Lead** (`_assign`), không có thì `assignment_fallback_users`. Trên site **`assignment_fallback_users` đang trống** và Lead CRM-LEAD-2026-00026 không có người phụ trách → BDR-2026-0000004 ghi `NO_RECIPIENT` (job `email_unread_handoffs` sẽ thử lại nhưng không ai nhận). Cơ chế dự phòng đã có, chỉ thiếu cấu hình và thiếu cảnh báo khi vẫn không có ai.
- `CRM Task`: có 6 bản ghi (đều Todo), omni_crm **chưa tạo CRM Task** ở đâu. Dùng được cho "việc" của K3–K6 (có assigned_to, due_date, priority, status; CRM tự hiện trên tab Tasks của Lead và gửi thông báo Assignment).
- Lịch demo: Frappe `Event` có sẵn (0 bản ghi), `Google Calendar` có DocType nhưng chưa cấu hình. Chưa có "lịch trống của người demo" – đề xuất K5 tính từ Event của người demo + giờ làm việc.

## 8. Email

- Email Account trên site: một tài khoản **Gmail cá nhân** (`mi***@gmail.com`) chỉ gửi đi (incoming tắt), OAuth; một tài khoản mẫu `jobs@example.com` tắt.
- **`info@miyano.com.vn` chưa được cấu hình** (không gửi, không nhận). Có Connected App "Microsoft 365 Email" nhưng chưa có Email Account dùng nó.
- Không có kết nối Microsoft 365/Teams để tạo cuộc họp.
- Hệ quả: phần email của K4–K6 (4.5, hỏi thăm 3 tháng, gửi tài liệu) **chưa làm được**; theo prompt K5 thì dừng phần email và báo. Link Teams: tạo việc cho người demo gửi lời mời (câu xác nhận sửa lại, "cần duyệt").

## 9. Trang Lead – cách gắn giao diện

- Tab "Zalo" (`ZaloTab.vue`), tab "Messenger" và composable `zalo.js`: do bản vá `omni_crm/crm_frontend_patch/0001-zalo-tab.patch` (dev khác đang sửa dở, chưa commit) áp vào `apps/crm`, build bằng `crm_frontend_patch/build.sh` (Node ≥ 20.19).
- Khung "Nháp của bot" (V3-13): `BotDrafts.vue` + 2 dòng trong `ZaloTab.vue`, bản vá áp tay `crm_frontend_patch/extra/bot-drafts-crm.patch`; dữ liệu từ `omni_crm.api.zalo_tab.get_conversation` → `drafts`.
- Khối "Bot AI" (tóm tắt, ý định…): trường Custom Field của Lead hiển thị ở panel bên (fixtures) + phần trong ZaloTab.
- Cách làm cho nút mới (Trả lại cho bot, Đã gửi tài liệu, Kết quả demo…): API whitelisted trong `omni_crm/api/`, component Vue mới gắn vào ZaloTab theo cùng kiểu bản vá áp tay; trường hiển thị thì dùng Custom Field + Property Setter. Rủi ro: mọi thay đổi giao diện chồng lên bản vá chưa commit của dev kia.

## 10. Đối chiếu kịch bản – hiện trạng

Ký hiệu: ✅ đã có · ◐ có một phần · ✗ chưa có.

| Mục | Nội dung | Trạng thái | Ghi chú / vướng |
| --- | --- | --- | --- |
| Tính cách | Giọng văn, xưng Miyano | ◐ | Tài liệu 08 trong Kho; prompt v3 chưa bắt buộc. Câu chữ kịch bản sẽ lấy từ kho kịch bản |
| 1.1 | Tin chào + 5 nút | ✗ | Chưa gửi được tin nút (mục 3). Zalo OA Manager đang tự gửi tin chào riêng |
| 1.2 | Phân loại tin gõ về mã nút, hỏi lại ≤2 | ◐ | Có phân loại nhóm câu hỏi (5 nhóm), chưa có danh sách mã đóng và đếm hỏi lại |
| 1.3 | Menu "Khác" | ✗ | Email nhận CV [Miyano bổ sung] |
| 1.4 | Gặp nhân viên trong/ngoài giờ, theo dịch vụ, 15 phút | ◐ | Có chuyển người + tạm dừng 24h + `handoff_message_in/after_hours` + `business_hours`. Thiếu: người nhận theo dịch vụ, nút, xin SĐT, tạo CRM Task, nhắc 15 phút. business_hours có thứ Bảy, holidays trống |
| 1.5 | Build từng kênh | ◐ | Zalo: thiếu nút, menu OA làm tay. Messenger: code chưa commit |
| 1.6 | Ghi CRM | ◐ | Có kênh, ID, nguồn/chiến dịch, thời điểm tương tác cuối. Thiếu dịch vụ quan tâm, trạng thái kịch bản |
| 2.1–2.4 | Menu dịch vụ, Tìm hiểu nhanh | ✗ | Nội dung có sẵn nguyên văn; nhóm vật tư [anh Xuân cung cấp] |
| 2.5 | Gõ tự do ở bước 2 | ◐ | Trả lời theo Kho có (nháp); chưa có "đổi dịch vụ", chưa gắn câu hỏi tiếp + nút |
| 2.6 | File danh mục | ◐ | Zalo nhận ảnh/file vào Lead (đính kèm Communication); chưa gắn ý nghĩa DANHMUC_VATTU |
| 2.7 | Ghi CRM | ✗ | |
| 3.1–3.2 | Thu thông tin 3a | ◐ | Có thẻ xin SĐT Zalo (`phone_request`, job tự gửi). Chưa có hỏi email, tên, tài liệu |
| 3.3 | Báo giá 3b | ◐ | Hiện chỉ mẫu tin cho sale điền (ADR 0031). Bot tự báo giá = K7, số liệu 3.3.5 còn trống |
| 3.4 | Không để lại thông tin, link bảo mật | ✗ | |
| 3.5 | Giao việc, hạn 16:30, nhắc | ✗ | `response_sla` có SLA phản hồi, không phải hạn việc |
| 3.6 | Kiểm định dạng email/SĐT | ◐ | Có chuẩn hóa SĐT/email trong identity |
| 3.7 | Ghi CRM | ✗ | |
| 4.1–4.3 | Nhắn lại sau tài liệu | ✗ | Không có tin hẹn giờ |
| 4.2 | Chọn kênh theo hạn chat | ◐ | Zalo có `pick_message_kind` 48h + quỹ; email chưa có info@; ZNS có trường `zbs_template_id` nhưng chưa có mẫu |
| 4.4 | Đặt lịch demo | ✗ | Không có lịch người demo, không có Teams |
| 4.5 | Email/ZNS | ✗ | info@ chưa cấu hình |
| 4.6 | Nhắn lại sau báo giá | ✗ | |
| 4.7–4.8 | Hẹn giờ, ghi CRM | ✗ | |
| 5.1–5.6 | Sau demo | ✗ | |
| C.1 | Nhắc sau 3 ngày | ✗ | |
| C.2 | Tự chuyển nhân viên, tóm tắt 5 dòng | ◐ | Chuyển khi khiếu nại/muốn gặp người/ngoài Kho; tóm tắt có `omni_bot_summary`, chưa đúng 5 dòng; chưa đếm "không hiểu 3 lần" |
| C.3 | Gõ tự do | ◐ | Y khoa: hiện **chuyển người** (nhóm always_handoff) – kịch bản muốn trả câu cố định + menu. Injection: có chốt `nghi_chen_lenh`. Spam/link lạ, ngôn ngữ khác, "bạn là bot à": chưa |
| C.4 | Dừng hẳn | ◐ | Có `bot_paused_until`; chưa có cờ Không liên hệ, chưa có "Trả lại cho bot" |
| C.5 | Giờ gửi chủ động | ◐ | Có `business_hours`, `bot_active_hours`; chưa có khung 09:00–16:30 |

## 11. Nguyên nhân các hiện tượng ngày 16/11 (đầu vào cho K1)

| Hiện tượng | Nguyên nhân gốc (đã đối chiếu dữ liệu) | Đã sửa? |
| --- | --- | --- |
| BOT-076 "Demo supply core có mất phí k" → chuyển người, nháp rỗng | Lượt Analyze (v1, cả hội thoại) ra `Hỏi giá` → `y_dinh_hoi_gia`; **và** câu lệnh reply v3 quy tắc 2 ("Khách hỏi phí … → handoff") khiến AI tự trả action handoff, text rỗng → `tra_loi_rong`. Bộ lọc chữ **không** bắt câu này (`sensitive_topics` = [] – đã kiểm trên omni.test 17/11) | ◐ v313 gỡ phần ý định; phần câu lệnh sửa ở reply v4 (K1) |
| BOT-080/082 "spd là gì" → nhóm Hỏi giá, mẫu báo giá | (a) Analyze ra `Hỏi giá`; (b) **chính AI Reply chọn nhóm "Hỏi giá"** (0,92/0,95) do lịch sử hội thoại có chuyện báo giá; nhóm `always_handoff` → `nhom_luon_chuyen_nguoi` | ◐ v313 gỡ (a), **chưa gỡ (b)**. K1: nhóm phải xét theo tin hiện tại |
| 19/30 lượt Analyze ra "Hỏi giá" | Prompt Analyze v1 xét cả hội thoại; trong hội thoại có tin của Miyano: thẻ xin SĐT "Để Miyano gửi báo giá…" (`phone_request.card_texts`), tin mẫu báo giá | ✗ K1 mục 3 |
| BOT-074 Invalid Output (vi phạm `required`) | `ai_client.call_tool` **đã thử lại** khi sai định dạng (`schema_left`, kèm ghi chú sửa) rồi vẫn sai → trả None → `make_draft` trả None, **không nháp, không báo ai** (chỉ có đường dự phòng khi khách đòi gặp người) | ◐ thử lại đã có; thiếu chuyển người + báo nhân viên (K1 mục 4) |
| 16:18 "Spd là gì" (Lead 00003) chỉ có Analyze, không Reply | Lúc 16:10 tài khoản Administrator gửi tin từ CRM (OOUT-2026-00000037, source Sale) → tạm dừng bot 24 giờ → `_reply_batch` trả `paused`. Đúng thiết kế hiện tại, nhưng không có dấu vết cho người xem | ◐ K1/K3: hiện rõ "bot đang tạm dừng đến …" |
| BDR-2026-0000004 NO_RECIPIENT | Lead không có người phụ trách + `assignment_fallback_users` trống | ✗ cấu hình + cảnh báo monitoring (K1 mục 6) |
| Tin 16:10 hiện "Nhân viên (ngoài hệ thống)" | `api/inbox.get_thread`: khi có cả echo và Outbound cùng `external_message_id` thì **giữ bản echo, bỏ bản Outbound** (quy tắc viết cho tin sale gõ trên OA Manager). Tin gửi từ CRM cũng có echo → bị gán nhãn sai. Không ghi hai lần (echo của tin CRM không tạo Outbound thứ hai nhờ `_is_own_send`) | ✗ K1 mục 8: ưu tiên bản Outbound khi Outbound do CRM gửi |
| Khung nháp hiện "Chốt chặn: handoff_topic:…", lặp tên nguồn | `handoff_reason` lấy mã cờ; `kb` liệt kê theo từng đoạn nên một tài liệu có 2 đoạn hiện 2 lần | ✗ K1 mục 7 |
| `message_kind` "ngoài 48h" sai cho tin echo cùng giây | `_echo_route` không thấy tin khách cùng lô (mục 4) | ✗ ghi vào Phát hiện; sửa trong K1 nếu người dùng đồng ý |

## 12. Thứ tự làm và rủi ro

Thứ tự: K1 (sửa bot hiện tại) → xác minh tin nút Zalo bằng 1 tin thử → K2 → K3 → K4 → K5 (phần email chờ info@) → K6. K7 chờ số liệu 3.3.5.

Rủi ro:

1. **Tin nút Zalo chưa kiểm chứng** (số nút, gói OA, quỹ). Chặn K2.
2. **Code Messenger và tab CRM chưa commit** (dev khác, 67 tệp sửa dở trên máy chủ). Mọi thay đổi chung (outbound, ZaloTab, inbox) phải cất/trả như các lần deploy trước; K2 phần Messenger chờ code vào repo.
3. **Chatbot Zalo OA Manager chạy song song** (mục 4): khách nhận hai lời chào.
4. **Email info@ và Teams chưa có**: K4 gửi tài liệu do nhân viên làm tay; K5 phần email dừng.
5. **Site có khách thật, `sandbox_mode = 0`**: bot kịch bản phải có công tắc + danh sách thử trước dòng gửi đầu tiên; test chỉ trên omni.test.
6. **Hai bộ phân loại**: Question Group (AI Reply) và mã kịch bản (router). K2 phải để router quyết, Question Group chỉ dùng bên trong nhánh "Thông tin dịch vụ".
7. Tư vấn y khoa: kịch bản muốn bot trả câu cố định thay vì chuyển người – đổi hành vi với khách, cần người dùng xác nhận.

## 13. Câu hỏi cần người dùng trả lời

1. Cho phép gửi **1 tin thử dạng nút** tới tài khoản Zalo thử của anh để xác minh API (mục 3)? Tài khoản thử nào (identity)?
2. Duyệt thêm 2 DocType + 1 bảng con (mục 6: kho kịch bản, tin hẹn giờ, người nhận theo dịch vụ) – em sẽ ghi ADR trước khi tạo.
3. Dev phụ trách Messenger/tab CRM có commit phần đang sửa dở vào repo trước K2 được không?
4. Người nhận mặc định (`assignment_fallback_users`) là ai? Anh điền trong Omni Settings, hoặc cho em tên để ghi vào báo cáo.
5. Email gửi tài liệu/nhắn lại: khi nào cấu hình `info@miyano.com.vn` trên CRM? Tài khoản Gmail đang dùng có giữ không?
6. Tư vấn y khoa: đổi từ "chuyển người" sang "trả câu cố định + menu" như C.3?
7. Cập nhật CRM Lead Status gốc theo bảng quy đổi ở mục 6, hay chỉ ghi trường trạng thái riêng?
8. Tắt chatbot/tin chào trên Zalo OA Manager trước khi bật bot kịch bản (anh làm tay)?

## 14. K1 – đã làm (17/11, nhánh `v4/kich-ban-bot`, chưa deploy)

Người dùng chốt 17/11: **không dùng người nhận mặc định** (Lead chuyển thẳng về CRM); **tư vấn y khoa trả câu cố định ở C.3 rồi đưa lại menu** (làm ở K2 vì cần menu nút).

| Mục K1 | Thay đổi | Công tắc |
| --- | --- | --- |
| 2 – quyết định theo tin hiện tại | Câu lệnh `reply_v4.md`: tin cần trả lời trong `<tin_moi>`, nhóm/hành động chỉ xét tin đó, dòng "miyano" không phải ý khách; v4 không gửi `<tom_tat_lead>` và không dùng ý định của lượt Analyze (`reply.current_turn_mode`) | `Omni Settings.reply_prompt_version`: v3 = như cũ, v4 = mới. **Người dùng tự đổi** |
| 2 – "Yêu cầu đề xuất" | Nhận từ lời khách ở lượt hiện tại (`guards` chủ đề `yeu_cau_de_xuat`), áp cho mọi phiên bản | – |
| 3 – Analyze không gán "Hỏi giá" sai | **Chưa sửa câu lệnh Analyze** (v1 đang chạy). Từ reply v4, ý định Analyze không còn ảnh hưởng quyết định trả lời; `omni_intent` chỉ để nhân viên đọc. Sửa Analyze (đổi sang bản mới) là thay đổi dữ liệu Lead, đề xuất làm riêng | – |
| 3 – "có mất phí không" | reply v4 quy tắc 3: câu có/không mà kho có câu trả lời thì trả lời (demo miễn phí, 30 phút) | reply v4 |
| 4 – AI sai định dạng | `check_reply_output` + `repair`: action reply mà text rỗng thì AI được yêu cầu làm lại. Vẫn không có kết quả → nháp **chuyển người** `ai_khong_tra_ket_qua` + báo nhân viên | chế độ Draft |
| 4 – không tin nào im lặng | Chế độ Draft: AI lỗi, lỗi giữa chừng (`loi_he_thong`), quá 6 nháp/giờ (`qua_nhieu_tin`), kho quá lớn → nháp chuyển người. Vẫn im lặng có chủ đích: bot tắt, bot đang tạm dừng, nhân viên đã trả lời, lượt không có tin khách, job trùng lượt đang chạy (`locked`) | `bot_reply_mode` |
| 6 – không người nhận | Không thêm người nhận mặc định (theo người dùng). Thêm cảnh báo giám sát `chuyen_nguoi_khong_nguoi_nhan` (gửi Omni Admin) khi có nháp chuyển người không báo được ai trong 24 giờ | luồng cảnh báo sẵn có |
| 7 – khung nháp | Lý do chuyển người hiện tiếng Việt (`handoff.explain_reason`, dịch lúc đọc nên nháp cũ cũng đúng); nguồn gộp theo tên tài liệu. Không đổi giao diện (không phải build lại) | – |
| 8 – tin gửi từ CRM | `api/inbox.get_thread`: Outbound có `sent_at` khác thời điểm echo (do CRM gửi) thì hiện bản Outbound kèm tên người gửi, bỏ echo; Outbound do echo tạo (nhân viên gõ trên OA, chatbot Zalo) giữ nhãn "Nhân viên (ngoài hệ thống)" | – |
| 9 – bộ kiểm thử | `eval/cases/k1-kich-ban-v3.jsonl` (45 ca Synthetic, từ bảng 1.2, 2.5, C.3 và tài liệu "Câu hỏi thường gặp"); ý phải có/không được có lưu trong `expected_fields._phai_co/_khong_duoc_co`; bộ chạy dùng tài liệu Kho tri thức + câu lệnh v4 như production, thêm `case_pass_rate` và `by_group` | `run_cli(allow_real=…)` |

Rủi ro cần biết: khi AI lỗi kéo dài, mỗi khách nhắn vào sẽ thành một nháp chuyển người và bot **tạm dừng 24 giờ** với khách đó (cơ chế chuyển người hiện có). Cảnh báo `ai_loi_nhieu`/`ai_ngat_mach` đã có sẵn.

Deploy: `api/inbox.py` và `pipeline/worker.py` đang bị dev khác sửa dở trên máy chủ; K1 sửa `api/inbox.py` nên script deploy phải cất/trả lại cả tệp này (ngoài `outbound.py`, `omni_inbox.js`).
