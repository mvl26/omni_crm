# 16 · Nạp file JSON của miyano-intake

Dịch vụ `miyano-intake` ghi mỗi sự kiện (Gmail, Messenger, tổng đài, form landing) thành một file JSON schema `"1"` trong `<spool>/inbox/<năm>/<tháng>/<ngày>/<kênh>/`. Gói `omni_crm/intake_files/` đọc các file đó và đưa vào **cổng tiếp nhận hiện có** (`gateway.save_inbound`). Không có DocType mới.

## Chỗ lưu JSON trong CRM

`Omni Inbound Message.raw_payload` (JSON). Email và cuộc gọi lưu cả envelope gốc (khóa `intake` / `call_event.raw.intake`). Cũng nên đọc `docs/03` mục V21-04 về hàng dự phòng webhook `private/omni_spool`: đó là đường **webhook**, tách khỏi đường file này.

## Ánh xạ

| `kind` (intake) | Loại Omni Channel | Sự kiện | `external_message_id` (khóa chống trùng) |
| --- | --- | --- | --- |
| `email` (gmail) | Email | `message`, hoặc `ignored` nếu intake đánh dấu thư tự động | Message-ID (giống hook Communication) |
| `call` (stringee, asterisk) | Telephony | `call_ended` (chỉ cuộc gọi đã kết thúc) | `call_id` |
| `lead_form`, `booking` (web_form) | Web Form | `form_lead`, qua `WebFormAdapter.parse` | sha1(`event_key`), đúng `submission_id` mà relay của intake gửi |
| `lead_form` (meta) | — | bỏ qua | đã có webhook `meta_leads` (CRM tự lấy lead) |
| `message` (messenger) | — | **chưa hỗ trợ** | xem dưới |

Vì khóa trùng với đường trực tiếp, một tin đi cả hai đường (relay form, hook Communication) vẫn chỉ vào một lần.

## Cấu hình (site_config, không thêm trường DocType)

- `omni_intake_dir`: thư mục spool của intake (chứa `inbox/`). Vắng = tắt hoàn toàn.
- `omni_intake_channels` (tùy chọn): `{"Email": "<Omni Channel>", "Telephony": "...", "Web Form": "..."}`. Vắng thì dùng kênh **duy nhất đang bật** của loại đó; không có hoặc nhiều hơn một thì file nằm lại trong `inbox/`, không đoán.
- Chạy mỗi phút (cron `omni_crm.intake_files.runner.run`), tối đa 200 file/lần, cũ trước.
- Thủ công: `bench --site <site> execute omni_crm.intake_files.runner.run`.

## Số phận của file

`archive/` (đã nạp hoặc trùng hoặc bỏ qua có chủ đích), `chua_nap/` (loại chưa hỗ trợ, giữ để nạp sau), `dead_crm/` + `.loi.txt` (hỏng, quá 2 MB, lỗi DB), nằm lại `inbox/` (thiếu kênh). Cùng cấu trúc ngày để tra cứu.

## Việc cần quyết (xem docs/12 mục Phát hiện)

1. **Messenger**: Omni Channel chưa có loại "Meta Messenger"; thêm cần ADR (quy ước CLAUDE.md mục 3). Hiện file Messenger được cất ở `chua_nap/`, không mất.
2. **Email qua Gmail API**: kênh Email bắt buộc có `email_account` khi bật (validate). Nếu hộp thư chỉ đọc qua intake, cần một Email Account "vỏ" hoặc nới validate; chưa làm.
3. **Đính kèm**: file đính kèm của intake nằm trong `<spool>/attachments/`, mới được ghi là tham chiếu đường dẫn, chưa chép vào File của Frappe.
