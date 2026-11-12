# 0022 · Kênh Meta Messenger: nhận qua file của miyano-intake, đính kèm vào File, trả lời từ Hộp thư

- Ngày: 2026-11-12
- Trạng thái: Đã chấp nhận (người phụ trách duyệt 12/11/2026: C1, D1, có ghi tin echo)
- Prompt: không có prompt kit; yêu cầu trực tiếp của người phụ trách ngày 12/11/2026 ("Messenger: quyết định đưa vào CRM. Thêm một loại kênh mới và viết ADR theo quy ước của repo"; "file đính kèm … cần chép vào File của Frappe … messenger có gì phải làm y cái đó")
- Liên quan: `docs/16-nap-file-intake.md` (mục "Việc cần quyết" 1 và 3), `docs/12` mục Phát hiện (dòng Messenger), ADR 0014 (hàng dự phòng webhook), ADR 0017 (ẩn tin Zalo khỏi Hoạt động)

## Bối cảnh

Đã kiểm (12/11/2026):

- Webhook Meta của Page Miyano Việt Nam (571684352685074, ứng dụng 1012569668539635) trỏ về miyano-intake: `https://yhct.miyano.com.vn/intake/hooks/meta`. miyano-intake ghi mỗi tin Messenger thành một file JSON schema `"1"` và chuyển tiếp nguyên văn body + chữ ký sang `omni_crm.api.webhooks.meta_leads?ch=meta-mvl`; adapter `Meta Lead Ads` chỉ đọc `leadgen` nên tin Messenger bị bỏ qua ở đó (`channels/meta_leads/adapter.py::parse`).
- Định dạng file Messenger (miyano-intake `intake/channels/meta.py`, nhánh main): `channel = "messenger"`, `kind = "message"`; `event_key = "messenger:<mid>"` (postback `messenger:postback:…`, referral `messenger:referral:…`); `sender = {type: "psid", value}`; `thread_ref = "messenger:<psid>"`; `source.account = <page_id>`; `content.text`; `content.attachments[] = {type, url, path}` với `path` tương đối gốc spool, dạng `attachments/<sha1(event_key)>/<tên tệp>`. File chỉ được ghi SAU khi đã tải xong đính kèm. Tin echo (Page tự gửi), read, delivery bị bỏ; tin ở `standby` vẫn ghi.
- `intake_files` hiện coi `messenger` là "chưa hỗ trợ" (`mapper.SKIP_NO_CHANNEL_TYPE`), runner cất sang `chua_nap/`. Trên server yhct spool là CHỈ ĐỌC với người chạy bench (`mvl`, ACL `rX`), nên file không bị di chuyển mà được ghi dấu "đã xử lý" trong Redis 14 ngày; runner chỉ quét `omni_intake_days` (mặc định 3) ngày. Tức là: tin Messenger cũ không mất (vẫn nằm trong spool của intake) nhưng **không tự nạp lại** sau khi có kênh; cần lệnh nạp bù.
- Omni Channel: `channel_type` là Select `Email / Zalo OA / Telephony / Meta Lead Ads / Google Lead Form / Web Form`. Các trường `meta_page_id`, `meta_app_id`, `meta_app_secret`, `meta_page_access_token` (Password) và nhóm kiểm token **không có `depends_on`**, dùng lại được cho kênh mới. Có sẵn `enabled`, `outbound_enabled`.
- Đường gửi ra: `api/inbox.py::send_reply` hiện chỉ nhận `Zalo OA`; xếp hàng qua `pipeline.outbound.queue_outbound` → `adapter.send`. `timeline.render_text` chỉ biến đính kèm `http(s)` thành liên kết; `inbox._safe_attachments` đã chấp nhận `/files/`, `/private/files/`.
- Nhiều chỗ liệt kê cứng loại kênh: `crm_bridge/lead.py::DEFAULT_SOURCE_MAP`, `crm_bridge/care_log.py`, `identity/normalize.py::_DEFAULT_NAME_RE`, `bot/reply.py::CHANNEL_MAX_CHARS`, `monitoring/alerts.py`, `api/customer.py`.
- Ứng dụng Meta đang ở chế độ development: chỉ người có vai trò trong ứng dụng nhắn được vào webhook và nhận được tin gửi ra. Chuyển Live cần App Review `pages_messaging` (ngoài phạm vi ADR, xem `spd/docs/12` phía IT).

## Các lựa chọn

### A. Đường nhận
1. **Chỉ đọc file của miyano-intake (`intake_files`)** — một đường duy nhất; đính kèm đã được intake tải về đĩa (không lo link CDN của Meta hết hạn); không mở thêm endpoint công khai; chữ ký đã được intake kiểm. Nhược: phụ thuộc intake và quyền đọc spool.
2. Webhook thẳng vào omni_crm (`channels/meta_messenger` verify/parse) — tự chủ, nhưng Page chỉ có MỘT callback URL: phải bỏ miyano-intake hoặc để intake chuyển tiếp; đính kèm phải tự tải từ CDN ngay (link hết hạn); nếu cả hai đường cùng chạy thì mỗi tin vào hai lần.

### B. Đính kèm
1. **Chép vào File riêng tư của Frappe (`is_private=1`), gắn vào Omni Inbound Message**, trường `attachments` ghi `/private/files/…` — sale mở được mọi loại tệp trong CRM theo quyền của Frappe; không phụ thuộc spool sau khi nạp.
2. Chỉ ghi đường dẫn spool (hiện trạng) — không mở được từ trình duyệt.

### C. Hiển thị tin Messenger trên Lead
1. Hiện ở tab Hoạt động của Lead (Communication thường) và ở Hộp thư — làm được ngay, không vá giao diện app `crm`.
2. Ẩn khỏi Hoạt động như Zalo (ADR 0017, `communication_type = "Chat"`) và làm tab Messenger riêng bằng bản vá giao diện như tab Zalo (ADR 0016) — gọn hơn nhưng phải vá giao diện CRM, khối lượng lớn.

### D. Chiều ra
1. **Trả lời chữ từ Hộp thư**, chỉ trong 24 giờ kể từ tin cuối của khách (`messaging_type = RESPONSE`); ngoài 24 giờ thì chặn và ghi lý do — đúng chính sách Meta, không cần quyền thêm.
2. Như 1, thêm gửi tệp từ CRM — làm sau, theo cách Zalo đã làm (ADR 0021).
3. Gửi ngoài 24 giờ bằng tag `HUMAN_AGENT` (tối đa 7 ngày) — cần quyền Human Agent qua App Review.

## Quyết định

A1, B1, C1, D1 (D2 đợt sau, D3 chỉ khi có quyền), và ghi tin echo (mục 9).

1. **Loại kênh mới `Meta Messenger`** (thêm option vào `Omni Channel.channel_type`). Không thêm DocType, không thêm trường: kênh dùng `meta_page_id` (lọc đúng Page), `meta_app_id`, `meta_page_access_token` (gửi tin, lấy tên), và `enabled` / `outbound_enabled`. Kênh trên site yhct đặt tên `messenger-mvl`.
2. **Adapter `omni_crm/channels/meta_messenger/adapter.py`** theo interface chung: `verify_request` luôn từ chối (không có webhook Messenger vào omni_crm, giữ một đường nhận); `parse` không dùng cho webhook; `send` gọi Graph Send API `POST /{page_id}/messages` (`recipient.id = PSID`, `messaging_type = RESPONSE`, chữ ≤ 2000 ký tự) qua client có timeout 10 giây, thử lại giới hạn, log không chứa token; `can_send_now` cho phép khi tin cuối của khách trong 24 giờ, ngược lại trả lý do; `sandbox_mode` chỉ ghi log.
3. **`intake_files`:** `mapper` ánh xạ `kind = "message"`, `channel = "messenger"` thành `InboundEvent(channel_type="Meta Messenger", event_type="message" | "postback" | "referral", external_message_id = phần sau "messenger:" của event_key, external_sender_id = PSID, text, attachments, sender_profile = {psid, page_id, tên nếu có}, raw = {"intake": env})`. Bỏ tin của Page khác `meta_page_id`. `runner` thêm nhánh Messenger: chép từng tệp trong `content.attachments[].path` (đọc từ spool, chỉ đọc) vào File riêng tư rồi mới `gateway.save_inbound`; tệp thiếu thì ghi tên + lý do vào đính kèm, không làm hỏng cả tin. Giới hạn kích thước theo `max_file_size` của site; quá thì giữ tham chiếu và ghi chú. Chống trùng File bằng `content_hash` của Frappe; chống trùng tin bằng `dedupe_key` của cổng.
4. **Hiển thị đính kèm "như Messenger":** ảnh và sticker hiện ảnh thu nhỏ bấm mở ảnh gốc; video, âm thanh có trình phát (`<video>`, `<audio>`); tệp khác (docx, xlsx, pdf, txt, zip…) là liên kết tải kèm tên; `fallback`/chia sẻ liên kết là liên kết. Sửa `timeline.render_text` (nhận `/private/files/`, thẻ media) và phần hiển thị luồng tin của Hộp thư.
5. **Định danh và Lead:** `identity_key = "Meta Messenger:<PSID>"` (PSID là mã theo Page, không gộp với Zalo/SĐT cho tới khi khách để lại SĐT/email — theo `docs/04`). Tin đầu tiên của PSID mới tạo Lead, nguồn `Facebook Messenger` (thêm vào `DEFAULT_SOURCE_MAP`, tạo `CRM Lead Source` nếu thiếu). Tên hiển thị lấy qua Graph `/{PSID}?fields=first_name,last_name` (lỗi thì tên giữ chỗ "Khách Meta Messenger …1234", thêm vào `_DEFAULT_NAME_RE`). Referral từ quảng cáo (click-to-Messenger) lưu `ad_id` trong `raw` để quy kết chiến dịch theo `docs/15` (gắn Omni Campaign làm sau).
6. **Trả lời từ Hộp thư:** `send_reply` nhận thêm `Meta Messenger` (cùng các chặn: quyền ghi Lead, người đã từ chối liên hệ, nội dung rỗng/dài). Bot không tự gửi trên Messenger (giữ `auto_send` chỉ Zalo).
7. **Nạp bù tin cũ:** lệnh `omni_crm.intake_files.runner.backfill(kinds=["messenger"], since="YYYY-MM-DD", apply=0)` bỏ qua dấu Redis và cửa sổ ngày, mặc định chỉ in số file; `apply=1` mới nạp. An toàn nhờ chống trùng.
8. **Công tắc:** tắt toàn bộ bằng `Omni Channel(messenger-mvl).enabled = 0` hoặc bỏ `messenger` khỏi `omni_intake_kinds` (file nằm lại, nạp bù sau); tắt chiều ra bằng `outbound_enabled = 0`.

9. **Tin echo (nhân viên trả lời ngay trên Meta Business Suite / ứng dụng Messenger của Page):** miyano-intake ghi thêm các tin `is_echo` (cùng định dạng, `direction = "out"`, `sender` là Page, người nhận là PSID; đăng ký trường webhook `message_echoes`). omni_crm nạp chúng như tin echo của Zalo: `event_type = "oa_echo"`, `external_sender_id` = PSID của khách. Các chỗ đã coi `oa_echo` là "sale trả lời ngoài hệ thống" (Hộp thư, dòng thời gian, phiên chăm sóc, dashboard, resolver không tạo Lead khi lô chỉ có echo, worker không tính là tin vào) dùng lại nguyên. `after_resolve` của adapter ghi echo thành Omni Outbound Message `Sent` (như `zalo/worker_hooks._create_echo_outbound`). **Bỏ echo của chính tin CRM đã gửi**: Send API trả `message_id` = `mid` của echo, nên đã có Omni Outbound Message cùng mã tin (`_is_own_send` như Zalo); thêm lớp phụ: echo có `app_id` = `meta_app_id` của kênh cũng bỏ. Nếu không, mỗi tin gửi từ CRM hiện hai lần.

## Người phụ trách đã duyệt (12/11/2026)

1. Mục C: **C1**, tin Messenger hiện ở tab Hoạt động của Lead và ở Hộp thư.
2. Mục D: **D1**, đợt đầu chỉ trả lời chữ trong 24 giờ; gửi tệp từ CRM để đợt sau.
3. **Có ghi tin echo** (mục 9).

## Hệ quả

- `bench migrate` trên site thật (thêm option Select) và khởi động lại `omni-intake-watch`; `site_config`: thêm `messenger` vào `omni_intake_kinds`; tạo Omni Channel `messenger-mvl` (dán token Page trong Desk). Bản vá cho server bỏ `hooks.py` (server khác repo); hook mới (nếu có) thêm bằng script có kiểm tra như `buoc3b`/`buoc4`.
- Mọi `frappe.enqueue(` mới đăng ký trong `tests/test_job_users.py` và có test chạy bằng Guest (ADR 0006).
- Test: mapper/adapter/đính kèm (đơn vị, không gọi mạng), runner tích hợp trên site test; không chạy song song với bộ test lớn của phiên khác; không `migrate` site test dùng chung khi chưa báo.
- Tài liệu: `docs/16` (bảng ánh xạ, số phận file, nạp bù), `docs/02` (option mới của `channel_type`), `docs/09` (công tắc), `docs/12` (đổi dòng Phát hiện Messenger thành Đã xác nhận + trỏ ADR này).
- miyano-intake (repo khác) phải sửa cho mục 9: ghi tin echo, thêm `message_echoes` vào lệnh `meta-subscribe` và hướng dẫn webhook.
- Chưa làm: gửi tệp ra (D2), tag ngoài 24 giờ (D3), tab riêng (C2), Instagram.
