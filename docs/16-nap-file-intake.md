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
| `message` (messenger) | — | **chưa hỗ trợ** | xem dưới |

Email không có đường thứ hai: file Gmail thành Communication của Email Account có `email_id` trùng `source.account`. Bỏ qua khi (a) intake đánh dấu thư tự động/hàng loạt, hoặc (b) Email Account đó đang `enable_incoming` (CRM tự kéo hộp thư rồi, nạp nữa là trùng). Giữ nguyên file nếu chưa có Email Account khớp hoặc chưa có Omni Channel Email đang bật cho nó.

Vì khóa trùng với đường trực tiếp, một tin đi cả hai đường (relay form, hook Communication) vẫn chỉ vào một lần.

## Cấu hình (site_config, không thêm trường DocType)

- `omni_intake_dir`: thư mục spool của intake (chứa `inbox/`). Vắng = tắt hoàn toàn.
- `omni_intake_channels` (tùy chọn): `{"Telephony": "<Omni Channel>", "Web Form": "..."}` (email không cần khai). Vắng thì dùng kênh **duy nhất đang bật** của loại đó; không có hoặc nhiều hơn một thì file nằm lại trong `inbox/`, không đoán.
- Chạy mỗi phút (cron `omni_crm.intake_files.runner.run`), tối đa 200 file/lần, cũ trước.
- Thủ công: `bench --site <site> execute omni_crm.intake_files.runner.run`.

## Số phận của file

`archive/` (đã nạp hoặc trùng hoặc bỏ qua có chủ đích), `chua_nap/` (loại chưa hỗ trợ, giữ để nạp sau), `dead_crm/` + `.loi.txt` (hỏng, quá 2 MB, lỗi DB), nằm lại `inbox/` (thiếu kênh). Cùng cấu trúc ngày để tra cứu.

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

1. **Messenger**: Omni Channel chưa có loại "Meta Messenger"; thêm cần ADR (quy ước CLAUDE.md mục 3). Hiện file Messenger được cất ở `chua_nap/`, không mất.
2. **Email qua Gmail API**: hộp thư chỉ đọc qua intake cần một Email Account "vỏ" (`enable_incoming` tắt, `email_id` = địa chỉ hộp thư) và một Omni Channel Email trỏ vào nó. Nếu CRM đã tự kéo hộp thư đó thì file Gmail bị bỏ qua, không cần làm gì.
3. **Đính kèm**: file đính kèm của intake nằm trong `<spool>/attachments/`, mới được ghi là tham chiếu đường dẫn, chưa chép vào File của Frappe.

## Triển khai lên server (email thử nghiệm, từng bước)

Thay `<BENCH>` (thư mục bench có site CRM), `<SITE>` (tên site), `<MAILBOX>` (hộp thư thử nghiệm). Mặc định chỉ nạp **email** (`omni_intake_kinds`), form/cuộc gọi/Messenger không bị đọc.

**A. Trên máy dev (một lần):** `cd ~/omni_crm && git push -u origin feat/nap-file-intake`.

**B. Tìm bench và đọc hiện trạng (chỉ đọc):**
```bash
ssh mvl@103.1.237.204
ls ~/*/sites -d; ls ~/*/apps -d          # tìm bench có apps/omni_crm
cd <BENCH>
bench --site <SITE> execute frappe.get_all --kwargs '{"doctype":"CRM Lead Status","pluck":"name"}'   # phải có "Tiềm năng"
ls /opt/miyano-intake/var/spool/inbox/*/*/*/ | sort | uniq -c | head          # thư mục kênh có trong spool
ls /opt/miyano-intake/var/spool/inbox/*/*/*/gmail 2>/dev/null | head -3
```
Mở một file trong `gmail/` và xem `source.account`: phải đúng `<MAILBOX>`. Nếu không, intake chưa đọc hộp thư thử nghiệm: dừng ở đây (xem README của miyano-intake, mục Gmail).

**C. Lấy code và áp dụng (cần chủ site đồng ý, đây là site đang chạy):**
```bash
cd <BENCH>/apps/omni_crm && git fetch origin && git checkout feat/nap-file-intake && cd ../..
bench --site <SITE> migrate
sudo supervisorctl restart all            # hoặc cách khởi động lại bench đang dùng: hooks.py có thêm một cron
```

**D. Dựng kênh email (xem kế hoạch trước, rồi ghi):**
```bash
bench --site <SITE> execute omni_crm.intake_files.setup.ensure_email_channel --kwargs '{"mailbox": "<MAILBOX>"}'
bench --site <SITE> execute omni_crm.intake_files.setup.ensure_email_channel --kwargs '{"mailbox": "<MAILBOX>", "apply": 1}'
```

**E. Quyền tệp (bước dễ vấp nhất):** người dùng chạy bench phải đọc và **di chuyển** file trong spool của intake (chạy bằng người dùng `miyano-intake`, thư mục 0750, file 0640). Chưa có cách nào đã kiểm; xem mục "Quyền tệp" dưới.

**F. Bật:**
```bash
bench --site <SITE> set-config omni_intake_dir /opt/miyano-intake/var/spool
bench --site <SITE> execute omni_crm.intake_files.runner.run      # chạy tay một lần, xem số "moi"/"bo_qua"/"loi"
```
Từ đó cron mỗi phút tự chạy (cần scheduler bật).

**G. Kiểm:** `/app/omni-inbound-message` có tin kênh `email-...`; Lead mới có thư ở tab email; `ls /opt/miyano-intake/var/spool/archive/...` có file đã nạp.

**H. Tắt/quay lui:** `bench --site <SITE> remove-config omni_intake_dir` (dừng nạp); tắt Omni Channel email (bỏ tích Bật kênh). File đã nạp nằm ở `archive/`, thư lỗi ở `dead_crm/`.

### Quyền tệp
`os.replace` cần quyền ghi trên thư mục `inbox/.../gmail`. Intake tạo thư mục 0750 / file 0640 nên người dùng bench không ghi được, và ACL không giúp vì `chmod` của intake đặt lại mặt nạ. Hai hướng: (1) sửa `DIR_MODE`/`FILE_MODE` của intake thành 0770/0660, thêm người dùng bench vào nhóm `miyano-intake`, đặt setgid cho `spool/`; (2) chạy bench bằng chính người dùng `miyano-intake`. Hướng (1) sửa code intake, cần anh đồng ý.
