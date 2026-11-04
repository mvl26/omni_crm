# 0017 · Ẩn tin Zalo khỏi tab Hoạt động/Email (supersede một phần ADR 0004)

- Ngày: 2026-11-04
- Trạng thái: Đã chấp nhận (yêu cầu của chủ dự án: "trông rất rối" vì một tin hiện ở 3 nơi)
- Prompt: V3-02
- Supersede một phần: ADR 0004 (chỉ phần "tin Zalo hiện trong Hoạt động/Email"); phần "mỗi tin là một Communication" còn nguyên.

## Bối cảnh
ADR 0004 ghi mỗi tin Zalo thành `Communication` (`communication_type = "Communication"`, `communication_medium = "Chat"`) để CRM hiện trong tab Hoạt động/Email. Từ V3-01 đã có tab Zalo (bong bóng chat), nên cùng một tin hiện ở tab Zalo, tab Hoạt động và tab Email.

## Cơ chế đã xác minh trong mã đang cài
- `frappe/desk/form/load.py::_get_communications` và `crm/api/activities.py` chỉ lấy Communication có `communication_type IN ('Communication', 'Feedback', 'Automated Message')` gắn Lead/Deal; không lọc theo `communication_medium`.
- `Communication.communication_type` có giá trị hợp lệ `Chat`. Bản ghi loại `Chat` không vào hai tab. Không sửa app `crm`/`frappe`.
- `crm.utils.on_communication_update` / `_get_communication_status` không lọc theo loại, nên `communication_status` (Open/Replied theo FCRM Settings) vẫn cập nhật khi vẫn `doc.insert()`.
- Hệ quả phụ của Frappe với loại `Chat` (`Communication.after_insert`): không gọi `notify_change("add")` (làm tươi tab Hoạt động; ta cần ẩn nên không sao) và phát `new_message` tới phòng realtime `user:<tên Lead>`, phòng không ai tham gia được. Có thể coi là vô hại (một lần publish Redis mỗi tin).

## Quyết định
1. `timeline._insert_communication` ghi `communication_type = "Chat"` cho tin của kênh **Zalo OA** (vào, `oa_echo`, tin ra thật và sandbox). Giữ `communication_medium = "Chat"`, `sent_or_received`, khóa `omni_inbound_message`/`omni_outbound_message`. Kênh khác (form quảng cáo, web form) và Email giữ `Communication` (form quảng cáo không có tab riêng nên vẫn cần hiện ở Hoạt động).
2. Công tắc `Omni Settings.zalo_in_activity_enabled` (Check, mặc định TẮT = ẩn; `flags.zalo_in_activity_enabled`, đọc thẳng `tabSingles` vì Single không áp default JSON: chưa lưu hoặc 0 = ẩn, chỉ 1 mới hiện; lỗi đọc = ẩn). Bật thì tin Zalo GHI MỚI quay lại `Communication`. Công tắc chỉ áp cho tin ghi sau đó; không sửa dữ liệu cũ.
3. Dữ liệu cũ: lệnh chạy tay `omni_crm.ops.hide_zalo_activity.run(dry_run=1, reverse=0)`. Đổi loại bằng `db.set_value(update_modified=False)` (không ORM save, không chạy lại hook CRM, không đổi `modified`), chỉ Communication Zalo OA có khóa của Omni; không đụng Email (kể cả thư có khóa `omni_inbound_message`) hay Communication không của Omni. Idempotent. Không đăng ký `patches.txt` nên không tự chạy khi `migrate`. Không cần xóa cache máy chủ (docinfo dựng lại mỗi lần tải); người dùng chỉ cần tải lại trang CRM.
4. Các chỉ số không đổi vì không chỉ số nào đọc Communication của Zalo: `first_response.py`, `dashboard/blocks.py` (liên lạc gần nhất), `bot/replay.py`, `timeline.conversation_turns` chỉ đọc Omni Inbound/Outbound Message và Communication có `communication_medium = 'Email'`. Chứng minh bằng test (so chỉ số trước/sau khi đổi loại).

## Hệ quả
- Tab Hoạt động/Email không còn dòng tin Zalo; xem Zalo ở tab Zalo hoặc Hộp thư. Comment hệ thống (liên kết ngược Lead -> Hộp thư, "Ý định...") vẫn ở Hoạt động.
- Nếu tắt tab Zalo (`zalo_tab_disabled`) mà không bật `zalo_in_activity_enabled`, trang Lead không hiện tin Zalo ở đâu ngoài Hộp thư; nên bật công tắc mới và chạy `reverse` khi tắt tab.
- Tài liệu cập nhật: docs/06 mục 8, docs/09, hướng dẫn 02, 08, 13, tong-hop-he-thong, nhật ký điều phối.

## Rủi ro
- Bản ghi cũ vẫn hiện ở Hoạt động cho đến khi chạy lệnh.
- Công cụ ngoài đếm Communication Zalo theo `communication_type = 'Communication'` sẽ không thấy tin mới (trong repo không có).
- Frappe cập nhật `Communication` có thể đổi hành vi với loại `Chat` ở bản sau (phát realtime, thông báo); kiểm khi nâng cấp frappe.

## Quay lui
- Tức thời cho tin mới: đặt `zalo_in_activity_enabled` = 1.
- Với tin cũ: `bench --site <site> execute omni_crm.ops.hide_zalo_activity.run --kwargs "{'dry_run': 0, 'reverse': 1}"`, rồi tải lại trang CRM.
