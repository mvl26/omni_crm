# 0011 · Trường kỹ thuật trên Contact, xóa dữ liệu Contact, và quy ước chuyển đổi Lead

- Ngày: 2026-11-02
- Trạng thái: Đề xuất (agent đã làm vì không đổi DocType ngoài danh sách; anh có thể đảo)
- Prompt: V2-02

## Bối cảnh
docs/14 mục 1.3 liệt kê 6 Custom Field cho Contact. Ba nhu cầu của mục 2 và mục 6 không thực hiện được chỉ với 6 trường đó:
1. Resolver phải tìm Contact theo SĐT/email. Contact lưu SĐT/email THÔ trong bảng con (`0903 105 801`), không có dạng chuẩn hóa.
2. Mục 6: "giữ bản ghi ẩn danh để lần nhập sau không tạo lại người này (khớp theo băm SĐT/email)" cần chỗ lưu băm.
3. Frappe Contact đặt tên bản ghi theo họ tên (`Contact.autoname`), nên xóa dữ liệu cá nhân phải đổi tên bản ghi, xóa Comment, cột `_comments` và Version.

## Quyết định
- Thêm 3 Custom Field ẩn, chỉ đọc trên Contact: `omni_phones_e164` và `omni_emails_norm` (dạng `|giá trị|giá trị|`, tính trong hook `Contact.validate`, điền cho Contact có sẵn bằng patch `backfill_contact_match_keys`), `omni_erased_keys` (HMAC-SHA256 với `encryption_key` của site, 32 ký tự hex, thay vì băm trần vì số điện thoại chỉ có ~10^9 khả năng nên băm trần dò ngược được). Đây là Custom Field (fixtures), không phải DocType mới.
- `omni_info_source` và `omni_tax_id` KHÔNG đặt `reqd` ở DocType: CRM tự tạo Organization/Contact (chuyển Lead -> Deal) mà không có chúng; đặt reqd làm hỏng chuyển đổi. "Bắt buộc khi nhập" do importer (V2-03) thi hành.
- Xóa dữ liệu Contact: `erase_customer_data` chỉ xóa Contact là CHÍNH người của Lead (khớp email/SĐT của Lead, hoặc nằm trong Deal của Lead), không xóa đồng nghiệp cùng nhà thầu; thêm `erase_contact_data(contact, reason)` cho một người cụ thể.
- Patch chuyển Lead sang Organization (`patches/v2/link_existing_leads_to_organizations.py`) KHÔNG đăng ký trong `patches.txt`: `bench migrate` sẽ chạy thật ngay trên staging/production, qua mặt cổng duyệt. Mặc định dry run; chạy thật bằng `bench execute ... --kwargs "{'dry_run': False}"` sau khi anh duyệt. Patch giai đoạn B2B (chỉ thêm) và patch điền khóa Contact (chỉ ghi trường kỹ thuật) thì đăng ký.
- Trường `fit_*` mới của Omni Settings: site migrate từ bản cũ không có dòng trong `tabSingles` nên đọc ra 0. Mã đọc thẳng `tabSingles` và rơi về mặc định ThamSo; lần lưu Omni Settings đầu tiên điền mặc định cho trường chưa từng lưu để không lưu 0.
- Liên kết Lead - Organization là ghi tên Organization vào `CRM Lead.organization` (Data, G14), không thêm Link mới.

## Hệ quả
- Khi V2-03 viết importer: ép `omni_info_source`; kiểm `is_erased_person()` trước khi tạo Contact; đặt `frappe.flags.omni_defer_fit` khi nhập hàng loạt rồi gọi `fit.apply_fit` một lần cho mỗi nhà thầu.
- Nếu anh muốn bỏ các trường kỹ thuật: Resolver bước Contact và "nhớ người đã xóa" mất; tìm Contact phải quét bảng con và chuẩn hóa bằng Python (chậm hơn).
