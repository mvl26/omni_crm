# AI trả lời bình luận: khảo sát (Prompt C0)

Ngày 2026-11-13. Chỉ khảo sát: đọc code (nhánh v3/10-marketing-hub, 21e266f) và đọc CSDL site thật **`miyano`** (không phải
`crm.miyano.com.vn` như bộ prompt ghi; bench CRM riêng đã gỡ). Không gọi Graph API (lệnh `meta-check` của intake bị chặn khi
chạy), số liệu quyền lấy từ kết quả "Kiểm tra quyền" đã lưu lúc 2026-11-13 11:18.

## 1. Hiện trạng

### 1.1 Webhook Meta: chưa nhận bình luận

- Callback của Page Miyano Việt Nam (…5074) trỏ về **miyano-intake** (`/intake/hooks/meta`, dịch vụ riêng ở
  `/opt/miyano-intake`, không thuộc omni_crm). Intake chuyển tiếp **nguyên văn body + chữ ký** sang
  `omni_crm.api.webhooks.meta_leads?ch=meta-mvl` (`intake/relay.py`).
- Intake đăng ký Page với `PAGE_FIELDS = messages, message_echoes, message_reactions, messaging_postbacks,
  messaging_referrals, standby, leadgen`: **không có `feed`**. Muốn nhận bình luận phải sửa intake (repo khác) và chạy
  `meta-subscribe` lại. Đây là việc ngoài omni_crm, cần người phụ trách intake đồng ý.
- Phía omni_crm: `channels/meta_leads/adapter.py::parse` chỉ đọc `changes[].field == "leadgen"`, các sự kiện khác bị bỏ.
  Kênh `Meta Messenger` (ADR 0022) không nhận webhook, đọc file của intake.
- CSDL: 5 tin `form_lead`, 9 tin Messenger, **0** payload có `feed` / `comment_id`. Không có payload bình luận thật để
  mô tả. Theo tài liệu Meta (Webhooks Page, trường `feed`), `value` của bình luận mới có `item=comment`, `verb=add`,
  `comment_id`, `post_id`, `parent_id`, `from{id,name}`, `message`, `created_time`. # VERIFY: payload thật.

### 1.2 Quyền của app trên Trang (kết quả Kiểm tra quyền đã lưu)

| Việc | Quyền theo tài liệu Meta | Hiện có |
|---|---|---|
| Nhận bình luận qua webhook `feed` | `pages_manage_metadata` (đăng ký) + đọc nội dung Trang | Chưa kiểm (FB_REQUIRED không có quyền này) |
| Đọc bình luận | quyền xem bài + task MODERATE của Trang (`pages_read_engagement`) | `pages_read_engagement` có |
| Trả lời bình luận bằng danh nghĩa Trang (`POST /{comment-id}/comments`, `message`) | `pages_manage_engagement`, token Trang của người có task MODERATE | **Thiếu** |
| Tin nhắn riêng (`POST /{page-id}/messages`, `recipient.comment_id`; trong 7 ngày, 1 tin/người) | `pages_messaging` | Kênh Messenger đã gửi được, nhưng app đang ở chế độ development |

App Meta đang ở **development** (ADR 0022): chỉ người có vai trò trong app mới phát sinh sự kiện và nhận được phản hồi.
Phục vụ khách thật cần Advanced Access qua App Review cho `pages_manage_engagement`, `pages_read_engagement`,
`pages_manage_metadata`, `pages_messaging`. # VERIFY: danh sách quyền phải xét duyệt trên trang App Review của app.
**Người dùng xác nhận 2026-11-13: chưa xin được quyền Meta.**

### 1.3 Zalo OA (người dùng đề nghị làm qua Zalo)

- Bài viết OA có bình luận, nhưng chỉ quản lý trên OA Manager (`oa.zalo.me/manage/comment`). Không tìm thấy API hay sự
  kiện webhook bình luận trong tài liệu Zalo đã đối chiếu. # VERIFY với developers.zalo.me nếu Zalo có tài liệu riêng.
- Sự kiện Zalo thật trên site (toàn bộ tin đã nhận): `user_send_*`, `oa_send_*`, `user_seen_message`,
  `user_received_message`, `user_click_chatnow`, `user_submit_info`, `follow`. **Không có sự kiện bình luận**, 0 payload
  chứa "comment".
- Kết luận: **không làm được AI trả lời bình luận bài viết Zalo OA qua API**. Thứ làm được trên Zalo là trả lời **tin nhắn**
  chat, phần này đã có bot trả lời (`bot/reply.py`, nháp/duyệt/tự gửi), chỉ chưa dùng Kho tri thức tài liệu.

### 1.4 Kho tri thức

- "Hỏi thử": `marketing/api.py::kb_ask` → `kb.search(question, 6)` (BM25 trên `Omni KB Chunk` của tài liệu đang dùng +
  `Omni Knowledge Item`), trả `Passage{id, source, text, document, score}`; AI trả lời qua `ai.call("kb-ask")`, nguồn
  phải là id của đoạn đã đưa.
- Đánh giá: `kb_feedback` ghi `feedback/feedback_note/feedback_by` vào **`Omni Bot Run`** của câu trả lời. Dùng lại được
  cho đánh giá phản hồi (thêm trường "câu trả lời đúng" thì phải ở bản ghi nhật ký riêng).
- Site thật: **0 tài liệu, 0 đoạn, 0 mục hỏi/đáp**. Kho trống ⇒ mọi câu hỏi sẽ chuyển người.

### 1.5 Cổng AI và cấu hình

- Cổng chung `bot/ai_client.call_tool_result`; lớp `marketing/ai.py` (ngân sách USD/tháng, mô hình dự phòng). Cấu hình ở
  `Omni Marketing Settings`: `ai_model`, `ai_fallback_model` (trên site đang rỗng), `monthly_ai_budget` (5 USD),
  `brand_voice`; trụ cột ở `Omni Content Pillar`. Bot chat dùng `Omni Settings` (`reply_model`, `daily_token_budget`).
- `marketing/ai.py` gọi với `customer_data=False`. Bình luận là **dữ liệu khách** nên phải gọi `customer_data=True`;
  site đang bật `omni_ai_allow_customer_data=1` nên gọi được.

### 1.6 Marketing Hub trong CRM

- Trang gắn bằng bản vá giao diện `crm_frontend_patch/0002-marketing-hub.patch` (router.js, AppSidebar.vue nhóm
  Marketing, `pages/marketing/*.vue`), API ở `marketing/api.py`. Quyền: `marketing/roles.py` (Marketing User / Manager).
- DocType tiền tố `Omni`, nhãn tiếng Việt. Thêm DocType phải ghi ADR (CLAUDE.md mục 3).
- ID bài Facebook đã đăng: `Omni Content Target.external_post_id` (+ `permalink`, `campaign`). Trên site: 2 target
  Facebook, chưa bài nào đăng thật (dry_run bật).

### 1.7 Lead và danh tính

- Lead Facebook hiện có từ Lead Ads (`form_lead`, nguồn "Quảng cáo Meta") và Messenger (PSID, nguồn "Facebook
  Messenger"), qua `Omni Identity(channel_type, external_id)`.
- `from.id` của bình luận là ID theo Trang/app, **không chắc trùng PSID Messenger**. # VERIFY. Nếu không trùng thì bình
  luận chỉ gắn được Lead khi khách nhắn tin riêng (private reply trả `recipient_id`).

### 1.8 Hàng đợi, lịch chạy

- Marketing Hub: cron riêng `"*/1 * * * *"` → `marketing.publisher.dispatch.dispatch_due`, khóa từng bản ghi trước khi
  gửi. Độ trễ trả lời bình luận dùng cùng kiểu: job mỗi phút xử lý bản ghi đến hạn.

### 1.9 Phát hiện ngoài phạm vi

- `Omni Settings.sandbox_mode = 0` trên site `miyano` (ghi nhớ cũ ghi là đang bật). Không sửa; cần người phụ trách xác
  nhận đây là chủ ý (kênh Messenger/Zalo đang gửi thật).

## 2. Thiết kế dữ liệu đề xuất (khi có quyền Meta)

Như Prompt C1 mục A: `Omni Comment Reply Settings` (mỗi Trang một bản ghi, Link `Omni Channel`) và
`Omni Comment Reply Log` (`comment_id` unique). Cần ADR 0030 trước khi tạo. Đường vào: nhánh `feed` trong
`meta_leads.parse` (body do intake chuyển tiếp, đã có kiểm chữ ký), không mở endpoint mới.

## 3. Rủi ro

1. Không có quyền `pages_manage_engagement` và App Review ⇒ không thể gửi trả lời cho khách thật.
2. Sửa intake (thêm `feed`) là repo/dịch vụ khác.
3. Kho tri thức trống.
4. Trả lời công khai không thu hồi được: mặc định Thử, chỉ gửi trên bài thử.

## 4. Câu hỏi cần người trả lời

1. Hoãn bình luận Facebook đến khi Meta duyệt quyền? Trong lúc chờ có muốn làm phần chạy được ở chế độ Thử (nhận
   bình luận, soạn, ghi nhật ký, không gửi)? Phần này vẫn cần thêm `feed` vào intake.
2. Với Zalo: bình luận bài viết OA không có API. Có muốn thay bằng "AI trả lời **tin nhắn** Zalo OA từ Kho tri thức"
   (nối bot trả lời sẵn có với Kho tri thức tài liệu, chế độ Thử / Chờ duyệt / Tự động) không?
3. `sandbox_mode = 0` trên `miyano` có phải chủ ý không?
