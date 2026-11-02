# 0012 · Nhập danh sách nhà thầu: ảnh chụp để không ghi đè sửa tay, chạy thử không ghi, chuyển Deal

- Ngày: 2026-11-02
- Trạng thái: Đề xuất (agent đã làm vì không thêm DocType ngoài danh sách; anh có thể đảo)
- Prompt: V2-03 (D1-14 bước 1: "chọn cách đơn giản, ghi ADR")

## Bối cảnh
D1-14 yêu cầu: "Không ghi đè trường người dùng đã sửa trong CRM sau lần nhập trước (lưu ngày nhập cuối cùng mỗi trường hoặc so `modified_by` — chọn cách đơn giản, ghi ADR)". Thêm ba yêu cầu từ V2-03: chạy thử không được ghi gì; chuyển Deal bằng hàm của CRM; không nhân đôi Lead/Contact của v1. Đã đọc `crm_lead.py` (installed): `first_name` bắt buộc; `convert_to_deal` tự gọi `create_contact` (tạo Contact nếu không truyền `existing_contact`, không có `omni_info_source`) và `create_organization`.

## Các lựa chọn
Cho "đã sửa tay":
1. So `modified_by`/`modified` — sai khi người nhập cũng là người sửa, và `modified` đổi mỗi lần tính điểm.
2. Lưu ngày nhập cuối cùng cho từng trường — nhiều cột phụ.
3. **Ảnh chụp**: Custom Field ẩn `omni_import_snapshot` (JSON) trên CRM Organization và Contact, ghi giá trị importer đã ghi lần cuối. Trường chỉ bị ghi đè khi đang trống HOẶC bằng đúng giá trị importer ghi lần trước; khác ảnh chụp (người dùng đã sửa hoặc đã xóa trống) thì giữ nguyên và báo xung đột. Cùng ý với ADR 0011 (trường kỹ thuật ẩn, là Custom Field chứ không phải DocType).

Cho chạy thử: (a) ghi rồi `rollback` — không an toàn vì hook của CRM/omni sinh việc nền, ToDo, DocShare, thông báo thoát khỏi rollback; (b) **kế hoạch chỉ đọc**: mọi lệnh ghi nằm sau `if not dry_run`, trạng thái trong bộ nhớ (nhà thầu mới, gói thầu, người liên hệ trong tệp) thay cho DB để các dòng phụ thuộc vẫn được dự báo, điểm phù hợp dự báo bằng `fit_from_awards`.

## Quyết định
- Ảnh chụp (lựa chọn 3). Cũng lưu trong ảnh chụp của Organization: tên Lead do importer tạo (`lead`) — để nhập lại không tạo Lead mới ngay cả khi Lead đó đã "Không phù hợp" — và băm nội dung ghi chú (`notes_hash`) — để Note/Comment chỉ tạo khi nội dung đổi.
- Chạy thử = kế hoạch chỉ đọc (b). Hạn chế đã biết: lỗi chỉ lộ khi LƯU thật (ví dụ validate của Frappe) không thấy ở chạy thử; chúng nằm trong tệp lỗi của lần nhập thật.
- "Lead đang mở" = Lead cùng `organization`, chưa gộp (`omni_merged_into` trống), chưa `converted`, trạng thái không thuộc loại Won/Lost; hoặc Nhà thầu có Deal chưa Lost (Lead đã chuyển Deal). Có thì không tạo Lead mới, giữ người phụ trách và trạng thái, chỉ tính lại điểm.
- Giai đoạn thuộc Deal: tạo Lead ở "Đã trao đổi nhu cầu", sau khi nhập người liên hệ gọi `crm.fcrm.doctype.crm_lead.crm_lead.convert_to_deal(lead, doc, existing_contact=, existing_organization=)` rồi đặt trạng thái Deal. Không có người liên hệ thì KHÔNG chuyển (CRM sẽ đẻ Contact rác) và đưa vào "cần xác nhận".
- Trùng tên Organization chưa có MST hoặc khác MST, hoặc cùng MST khác tên trong tệp: giữ lại cả dòng nhà thầu lẫn gói thầu/người liên hệ của nó, đưa vào "cần xác nhận" kèm hai bản ghi. Không tự gán MST, không tạo bản sao.
- Tên hiển thị: Frappe ghép `full_name` = first + middle + last nên đặt từ cuối vào `last_name`, phần đầu vào `first_name` (docs/14 mục 5 nói "tên = từ cuối" nhưng ghép lại sẽ ra "An Nguyễn Văn"). Khác chữ của tài liệu, giữ đúng kết quả hiển thị.
- Đồng ý nhận liên hệ: tệp không bao giờ nới "Từ chối" thành "Có"/"Chưa hỏi" (vào "cần xác nhận").

## Hệ quả
- Hai Custom Field mới (`omni_import_snapshot`) trong fixtures; không DocType mới.
- Nhập tệp lỗi đã sửa vẫn chạy lại được (ô được trung hòa công thức bằng dấu `'`, importer gỡ lại).
- Nếu muốn nhập đè trường đã sửa tay: xóa ảnh chụp của nhà thầu đó (hoặc sửa trong CRM rồi nhập).
- Ảnh chụp không có với dữ liệu nhập trước V2-03; trường đang có giá trị khác tệp bị coi là sửa tay và được báo, không ghi đè.
