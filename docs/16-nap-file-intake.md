# 16 · Nạp file JSON của miyano-intake

Dịch vụ `miyano-intake` ghi mỗi sự kiện (Gmail, Messenger, tổng đài, form landing) thành một file JSON schema `"1"` trong `<spool>/inbox/<năm>/<tháng>/<ngày>/<kênh>/`. Gói `omni_crm/intake_files/` đọc các file đó và đưa vào **cổng tiếp nhận hiện có** (`gateway.save_inbound`). Không có DocType mới.

## Chỗ lưu JSON trong CRM

`Omni Inbound Message.raw_payload` (JSON). Email và cuộc gọi lưu cả envelope gốc (khóa `intake` / `call_event.raw.intake`). Cũng nên đọc `docs/03` mục V21-04 về hàng dự phòng webhook `private/omni_spool`: đó là đường **webhook**, tách khỏi đường file này.

## Ánh xạ

| `kind` (intake) | Loại Omni Channel | Sự kiện | `external_message_id` (khóa chống trùng) |
| --- | --- | --- | --- |
| `email` (gmail) | Email, **qua Communication + Email Account có sẵn** | tạo Communication "Received"; hook `on_communication_insert` có sẵn lo phần còn lại | Message-ID |
| `call` (stringee, asterisk) | Telephony | `call_ended` (chỉ cuộc gọi đã kết thúc) | `call_id` |
| `lead_form`, `booking` (web_form) | Web Form | `form_lead`, qua `WebFormAdapter.parse` | sha1(`event_key`), đúng `submission_id` mà relay của intake gửi |
| `lead_form` (meta) | — | bỏ qua | đã có webhook `meta_leads` (CRM tự lấy lead) |
| `message` (messenger) | Meta Messenger (ADR 0022) | `message` (tin khách, postback); `oa_echo` (Page gửi, `direction = "out"`); referral đứng riêng -> `ignored` | phần sau `messenger:` của `event_key` (mid) |

Thư Gmail xếp vào Spam: intake (từ nhánh `feat/gmail-doc-spam`) vẫn ghi file với `review_reasons: ["gmail_spam"]`; khi nạp, tiêu đề Communication có tiền tố `[Gmail xếp vào Spam - cần xem lại]` để sale thấy ngay ở tab email của Lead. Spam là bản tin/thư tự động thì intake vẫn đánh dấu `ignored` và CRM bỏ qua.

Email không có đường thứ hai: file Gmail thành Communication của Email Account có `email_id` trùng `source.account`. Bỏ qua khi (a) intake đánh dấu thư tự động/hàng loạt, hoặc (b) Email Account đó đang `enable_incoming` (CRM tự kéo hộp thư rồi, nạp nữa là trùng). Giữ nguyên file nếu chưa có Email Account khớp hoặc chưa có Omni Channel Email đang bật cho nó.

Vì khóa trùng với đường trực tiếp, một tin đi cả hai đường (relay form, hook Communication) vẫn chỉ vào một lần.

## Cấu hình (site_config, không thêm trường DocType)

- `omni_intake_dir`: thư mục spool của intake (chứa `inbox/`). Vắng = tắt hoàn toàn.
- `omni_intake_kinds`: thêm `"messenger"` để nạp Messenger (mặc định chỉ `email`).
- `omni_intake_channels` (tùy chọn): `{"Telephony": "<Omni Channel>", "Web Form": "...", "Meta Messenger": "..."}` (email không cần khai). Vắng thì dùng kênh **duy nhất đang bật** của loại đó; không có hoặc nhiều hơn một thì file nằm lại trong `inbox/`, không đoán.
- Chạy ngay khi có file nhờ tiến trình `watcher` (mục "Độ trễ thấp"); cron `omni_crm.intake_files.runner.run` mỗi phút là lưới an toàn. Mỗi lúc một lượt (khóa Redis), tối đa 200 file/vòng, cũ trước.
- Thủ công: `bench --site <site> execute omni_crm.intake_files.runner.run`.

## Độ trễ thấp (nạp ngay, gửi ngay)

Đường thư khách -> CRM và CRM -> khách, mỗi chặng chờ bao lâu (ADR 0018):

| Chặng | Trước | Nay | Cơ chế |
|---|---|---|---|
| Gmail -> file | 60 s | ≤ 5 s | `.env` của intake: `INTAKE_GMAIL_POLL_SECONDS=5` |
| File -> Communication | ≤ 60 s (cron) | ~1 s | tiến trình `omni_crm.intake_files.watcher` thấy file mới thì `runner.kick()` |
| Gộp tin trước khi xử lý | 5 s | 0 với Email | `worker.NO_DEBOUNCE_CHANNELS` (chat vẫn gộp theo Omni Settings) |
| Danh sách `/crm/leads` | F5 | tự tải lại | `crm_bridge.list_refresh` (Lead mới) |
| Trang Lead/Deal đang mở | F5 | tự tải lại | `timeline.refresh_activity`: `refetch_resource` khóa `["activity", tên]` vào phòng của tài liệu |
| Sale gửi thư từ CRM | ≤ 4 phút (flush) | vài giây | `crm_bridge.fast_email`: Email Queue của CRM Lead/Deal đẩy job gửi ngay sau commit |

**Tiến trình watcher** chạy dưới supervisor, tệp riêng (không bị `bench setup supervisor` ghi đè):

```ini
[program:omni-intake-watch]
command=<bench>/env/bin/python -m frappe.utils.bench_helper frappe --site <site> execute omni_crm.intake_files.watcher.run
directory=<bench>/sites
user=<người chạy bench>
autostart=true
autorestart=true
stopwaitsecs=5
redirect_stderr=true
stdout_logfile=<bench>/logs/omni-intake-watch.log
```

Cập nhật mã omni_crm thì khởi động lại cả nó: `sudo supervisorctl restart omni-intake-watch`. Tắt watcher thì mọi thứ vẫn chạy, chỉ quay về chờ cron (≤ 60 s). Watcher chỉ `stat` thư mục của hai ngày mới nhất; thư có ngày gửi cũ hơn vẫn do cron nạp.

## Số phận của file

`archive/` (đã nạp hoặc trùng hoặc bỏ qua có chủ đích), `chua_nap/` (loại chưa hỗ trợ, ví dụ Instagram, giữ để nạp sau), `dead_crm/` + `.loi.txt` (hỏng, quá 2 MB, lỗi DB), nằm lại `inbox/` (thiếu kênh). Cùng cấu trúc ngày để tra cứu.

## Dựng kênh email cho hộp thư chỉ đọc qua intake

Không sửa code CRM, không có đường email thứ hai. Lệnh sau mặc định chỉ IN kế hoạch; thêm `"apply": 1` mới ghi:

```bash
bench --site <site> execute omni_crm.intake_files.setup.ensure_email_channel --kwargs '{"mailbox": "info@miyano.com.vn"}'
```

Nó tạo một Email Account "vỏ" (không nhận, không gửi, không tạo Lead) và Omni Channel Email `email-<tên>` trỏ vào đó. Nếu Email Account của hộp thư đã `enable_incoming` thì CRM tự kéo thư, lệnh không làm gì. Lưu ý: vì vỏ không có SMTP, sale chưa trả lời khách từ CRM qua hộp thư này được; muốn trả lời thì Email Account phải có cấu hình gửi.

Hiện trạng site yhct (ảnh chụp 10/11/2026): có 3 Omni Channel là `zalo-mvl` (Zalo OA), `web-landing` (Web Form), `meta-mvl` (Meta Lead Ads); **chưa có kênh Email và Telephony**.

## Kiểm tra site trước khi bật (chỉ đọc)

`bench --site <site> execute omni_crm.intake_files.inspect_site.report` liệt kê kênh, Email Account (không mật khẩu), biểu mẫu Website và khóa `omni_intake_*`.

## Việc cần quyết (xem docs/12 mục Phát hiện)

1. **Messenger**: đã quyết, ADR 0022 (mục "Messenger" bên dưới).
2. **Email qua Gmail API**: hộp thư chỉ đọc qua intake cần một Email Account "vỏ" (`enable_incoming` tắt, `email_id` = địa chỉ hộp thư) và một Omni Channel Email trỏ vào nó. Nếu CRM đã tự kéo hộp thư đó thì file Gmail bị bỏ qua, không cần làm gì.
3. **Đính kèm**: Messenger chép vào File riêng tư của Frappe (ADR 0022). Email và cuộc gọi vẫn chỉ ghi tham chiếu đường dẫn.

## Messenger (ADR 0022, tab và gửi tệp: ADR 0023)

- Một đường nhận duy nhất: file của miyano-intake. Không có webhook Messenger vào omni_crm (`MetaMessengerAdapter.verify_request` luôn từ chối).
- Kênh: Omni Channel loại `Meta Messenger` (trên yhct: `messenger-mvl`), điền `meta_page_id` (bắt buộc khi bật), `meta_app_id`, `meta_page_access_token` (gửi tin, lấy tên); `outbound_enabled` để trả lời từ Hộp thư.
- Bỏ: tin của Page khác `meta_page_id`; echo của chính tin CRM gửi (`raw.event.message.app_id` = `meta_app_id`, và trong worker: đã có Omni Outbound Message cùng mã tin).
- Đính kèm: `content.attachments[].path` (trong `<spool>/attachments/`, chặn đường dẫn ra ngoài) được chép vào File riêng tư rồi gắn vào Omni Inbound Message (quyền mở tệp theo quyền đọc tin). Tệp thiếu, quá `max_file_size` hoặc không lưu được (ảnh hỏng) chỉ ghi tên và lý do; tin vẫn được nạp. Hộp thư và tab Hoạt động hiện ảnh/sticker, trình phát video/âm thanh, liên kết tải cho tệp khác.
- Lead: tin đầu của PSID mới tạo Lead nguồn `Facebook Messenger`; tên lấy qua Graph (lỗi thì tên giữ chỗ "Khách Meta Messenger …"). Từ ADR 0023: tin hiện ở **tab Messenger** của trang Lead/Deal và Hộp thư, ẩn khỏi tab Hoạt động (`Communication` loại `Chat`; công tắc `messenger_in_activity_enabled`; tin cũ đổi bằng `omni_crm.ops.hide_messenger_activity.run`).
- Trả lời: Hộp thư hoặc tab Messenger, trong 24 giờ kể từ tin cuối của khách (`messaging_type = RESPONSE`); ngoài 24 giờ bị chặn kèm lý do. Gửi ảnh/tệp chỉ ở tab Messenger, khi bật `messenger_media_send_enabled` (ADR 0023): tệp phải đã đính kèm vào Lead hoặc Deal của Lead, tải lên Meta kiểu multipart; có chữ thì chữ đi trước thành tin riêng.
- Mẫu tin nhắn (ADR 0026): nút **Mẫu tin** trong tab Messenger điền mẫu `Omni Chat Template` (đã thay tên khách, tên/số điện thoại sale) vào ô soạn, sale sửa rồi tự gửi. Quản lý thêm/sửa mẫu ở Desk `/app/omni-chat-template`; bộ mẫu khởi đầu: `bench --site <site> execute omni_crm.crm_bridge.chat_templates.install` (chỉ tạo mẫu chưa có).
- Tương tác (ADR 0027, đợt 1): emoji, sticker của Meta và 👍 (`send_sticker`; kho sticker cần `meta_app_id` + `meta_app_secret` của kênh), thả/bỏ cảm xúc (`react`), trả lời một tin cụ thể (`reply_to.mid`), ẩn tin trong CRM (`hide_message`, thu hồi 1 phía; Meta không có API thu hồi 2 phía). Cảm xúc của khách: miyano-intake ghi file `kind = "reaction"` (cần trường webhook `message_reactions`), runner ghi `reaction_customer` lên tin cùng `mid`, không tạo tin mới. Sticker khách gửi: đính kèm kiểu `sticker` (có `sticker_id`, có thể không có ảnh).
- Đợt 2 (ADR 0027): **ghi âm** (`send_voice`: bản ghi `ghi-am-…` của trình duyệt được đổi sang MP3 bằng `ffmpeg` rồi gửi kiểu `audio`; cần công tắc gửi tệp và `ffmpeg` trên máy chủ, đường dẫn tùy chọn ở site_config `omni_ffmpeg_path`), **chuyển tiếp** (`forward`: chỉ tin Page đã gửi, tới một Lead Messenger khác người dùng được sửa, qua mọi guard của Lead nhận; tệp được chép sang Lead nhận).
- Kết nối Page (ADR 0028): nút **Connect Facebook Page** trên form Omni Channel loại Meta Messenger (chỉ Omni Admin). Đăng nhập bằng Facebook Login for Business (Configuration ID ở ô `meta_login_config_id`), chọn Page, CRM đăng ký webhook cho Page bằng danh sách trường đang có gộp với `PAGE_FIELDS` của miyano-intake (không làm rơi `leadgen`), rồi ghi Page, người và giờ kết nối, các trường đã đăng ký. Không lưu token Page của luồng này: gửi/nhận tin vẫn dùng token System User ở `meta_page_access_token`. Callback phải khai trong Valid OAuth Redirect URIs: `https://<site>/api/method/omni_crm.api.meta_connect.callback`.
- **Nạp bù** tin nhận trước khi bật kênh (đã bị cất `chua_nap/` hoặc ghi dấu Redis ở spool chỉ đọc):

```bash
bench --site <site> execute omni_crm.intake_files.runner.backfill --kwargs '{"kinds": ["messenger"], "since": "2026-11-01"}'             # chỉ đếm
bench --site <site> execute omni_crm.intake_files.runner.backfill --kwargs '{"kinds": ["messenger"], "since": "2026-11-01", "apply": 1}' # nạp
```

## Triển khai lên server (email thử nghiệm)

Xem lệnh từng bước trong lời hướng dẫn kèm commit; tóm tắt: lấy code (bản vá `git apply`, không phụ thuộc commit nền của server), `migrate` + khởi động lại, `ensure_email_channel`, cấp quyền ĐỌC spool cho người chạy bench bằng ACL, `set-config omni_intake_dir`, chạy tay một lần.

### Quyền tệp
Người dùng chạy bench (`mvl`) không cần ghi vào spool của intake (thư mục 0750, file 0640, chủ `miyano-intake`). Khi không ghi được, runner không di chuyển file mà ghi dấu "đã xử lý" vào Redis 14 ngày và chỉ quét `omni_intake_days` (mặc định 3) ngày gần nhất; chạy lại vô hại nhờ chống trùng. Chỉ cần quyền đọc: `setfacl -m u:mvl:rX` (ACL đọc không bị mặt nạ `chmod` của intake làm mất hiệu lực, khác với quyền ghi).
