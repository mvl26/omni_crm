# 0002 · Gộp Lead bằng cách chuyển tham chiếu tường minh, không dùng `rename_doc(merge=True)`

- Ngày: 2026-10-29
- Trạng thái: Đã chấp nhận
- Prompt: D1-05

## Bối cảnh
docs/04 mục 5 yêu cầu gộp Lead phụ vào Lead chính mà **không xóa** Lead phụ (giữ dấu vết, cho phép tra ngược và cho tin cũ của định danh đã gộp đi về Lead chính), trong một transaction, và nói có thể đánh giá `frappe.rename_doc(..., merge=True)`.

Đã đọc `apps/frappe/frappe/model/rename_doc.py` (v15.107.2): với `merge=True`, sau khi cập nhật các trường Link/Dynamic Link trỏ tới bản ghi cũ, hàm gọi `frappe.delete_doc(doctype, old)` (dòng 217-218) và ghi comment "merged ... into ...". Nó còn yêu cầu `allow_rename` trên DocType và sắp xếp lại tên.

## Các lựa chọn
1. `rename_doc(merge=True)` — ngắn gọn, nhưng xóa Lead phụ (trái đặc tả), không chọn được trường nào lấy từ Lead phụ, không để lại `omni_merged_into`, và đi quét mọi DocType có Link tới CRM Lead (kể cả bản ghi Version/Activity) nên khó dự đoán phạm vi. CRM Lead cũng không bật `allow_rename`.
2. Chuyển tường minh từng loại bản ghi (chọn) — danh sách DocType và trường tham chiếu là hằng số có chú thích nguồn trong `identity/merge.py`; Lead phụ giữ lại, trạng thái `Junk`, `omni_merged_into` trỏ về Lead chính.

## Quyết định
Chọn 2. Không đánh giá thêm bằng test cho lựa chọn 1 vì nó vi phạm ràng buộc "không xóa Lead phụ" ngay từ mã nguồn.

## Hệ quả
- Thêm DocType mới có Link tới CRM Lead (Đợt 2: Omni Call, Omni Bot Draft đã có trong danh sách, bỏ qua khi chưa tồn tại) thì phải bổ sung vào hằng số trong `merge.py`; test `test_merge.py` quét `Link` tới CRM Lead của mọi DocType omni_crm để nhắc việc này.
- Đã chuyển cả `ToDo` (`reference_type`/`reference_name`, phân công sale). `CRM Deal.lead` KHÔNG bị sửa (an toàn nhất, Deal giữ nguồn gốc): `get_deal_for_lead(primary)` tra thêm Deal của các Lead đã gộp vào primary (`omni_merged_into`, `converted=1`), nên tin mới của khách vẫn đi về Deal cũ.
- Hai lượt gộp ngược chiều đồng thời: khóa hàng cả hai Lead (`for_update`, theo thứ tự tên) trong savepoint, kiểm tra `omni_merged_into` sau khi giữ khóa; lượt sau bị từ chối, không có vòng.
- Gộp chạy trong savepoint, không tự commit (request/job gọi sẽ commit); lỗi giữa chừng rollback về savepoint.
