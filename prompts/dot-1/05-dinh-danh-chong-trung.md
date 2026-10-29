# D1-05 ★ · Định danh và chống trùng

**Phụ thuộc:** D1-04. **Thời lượng dự kiến:** 2 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/04-dinh-danh-va-chong-trung.md (toàn bộ — đây là đặc tả chính), docs/02-mo-hinh-du-lieu.md mục 4, 7, 15, docs/12 mục A (Q10).

Nhiệm vụ: thay cài đặt tạm của `resolver.resolve` bằng thuật toán thật; chuẩn hóa SĐT/email; trích liên hệ từ văn bản; nghi trùng; gộp Lead.

Bước 0 — G13: kiểm tra `phonenumbers` trong môi trường bench (`bench pip show phonenumbers` hoặc import thử). Thiếu thì thêm vào dependencies của app (pyproject) và ghi lại.

Việc cần làm:
1. `identity/normalize.py`: `normalize_phone`, `normalize_email`, `is_system_email`, `normalize_sender(channel_type, raw)` (thay bản tạm ở D1-03), `name_similarity(a, b) -> float` (bỏ dấu tiếng Việt, chữ thường, so theo từ), `is_placeholder_name(name)`. Bảng đổi đầu số 11 số ở docs/04 mục 1.1 để thành hằng số có chú thích.
2. `identity/extract.py::extract_contacts(text) -> {"phones": [...], "emails": [...]}` (đã chuẩn hóa, bỏ trùng).
3. Hook `CRM Lead.validate` → `crm_bridge/lead.py::compute_normalized_fields` tính `omni_phone_e164` (từ trường SĐT của CRM đã xác nhận ở G1, ưu tiên di động), `omni_email_norm`. Patch một lần (`patches.txt`) tính cho Lead cũ theo lô 500, có thể chạy lại an toàn.
4. `crm_bridge/lead.py::create_lead_for_identity(identity, signals) -> str`: tạo CRM Lead tối thiểu: tên (tên hiển thị hoặc "Khách {kênh} {4 số cuối mã}"), SĐT/email nếu có tín hiệu mạnh, `omni_first_channel`. Chỉ tạo bằng API Frappe chuẩn (`frappe.get_doc(...).insert`) để hook của CRM chạy bình thường. D1-06 sẽ bổ sung nguồn, gán sale.
5. `identity/resolver.py::resolve(msgs)` đúng thuật toán docs/04 mục 3 (bước 1–7), gồm 3.1 tên mâu thuẫn, 3.2 Lead đã chuyển Deal (chỉ cần trả về thông tin Deal để D1-06 ghi dòng thời gian; xác nhận cách CRM đánh dấu đã chuyển và ghi vào docs/12). Tín hiệu lấy từ: `sender_profile` (mạnh nếu kênh cung cấp SĐT/email hoặc event_type = user_info; với kênh Email thì email người gửi là mạnh; với Telephony thì số gọi là mạnh), `extract_contacts(text)` (trung bình). Lead có `omni_merged_into` thì đi theo về Lead chính.
6. `identity/duplicates.py`: `create_candidate(lead_a, lead_b, reason, score)` bỏ qua nếu đã có cặp; API whitelisted `dismiss(candidate)`, `merge(candidate, primary)` — kiểm tra quyền ghi cả hai Lead bằng `frappe.has_permission`.
7. `identity/merge.py::merge_leads(primary, secondary, user)` đúng docs/04 mục 5, trong một transaction. Danh sách DocType và trường tham chiếu cần chuyển lấy từ kết quả G1 (ghi rõ trong code thành hằng số có chú thích nguồn). Đánh giá `frappe.rename_doc(merge=True)` CHỈ nếu bạn chứng minh được bằng test; mặc định làm tường minh. Ghi ADR lựa chọn.

Test (tối thiểu, `tests/test_normalize.py`, `tests/test_resolver.py`, `tests/test_merge.py`):
- normalize_phone: ≥ 25 ca (mọi dạng ở docs/04 mục 1.1, kể cả 0162… → +8432…, +84, 0084, khoảng trắng, dấu chấm, số cố định, rác, rỗng).
- extract_contacts: chữ ký email nhiều dòng, SĐT viết tách "0912 345 678", hai số trong một câu.
- Toàn bộ ca ở docs/04 mục 6.
- Lead đã gộp: tin mới của định danh cũ đi về Lead chính.
- Đồng thời: hai lượt xử lý của hai định danh khác nhau cùng SĐT mới chạy song song → kết quả cuối không có 2 Lead không được nối (hoặc có đúng 1 Candidate). Mô tả trong báo cáo cách đã đảm bảo (ví dụ khóa theo SĐT chuẩn hóa khi tạo Lead).

Hoàn thành khi: test xanh; patch chạy được hai lần liên tiếp không lỗi; docs/12 cập nhật G1 (phần Deal) và G13; ADR về cách gộp.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Tự tạo 2 Lead cùng SĐT (viết khác dạng: `0912.345.678` và `+84 912345678`) → mở Omni Duplicate Candidate sau khi gửi một tin giả: có 1 cặp.
2. Bấm gộp: mở Lead chính, thấy đủ hoạt động của Lead phụ; Lead phụ có ghi chú "Đã gộp vào…".
3. Đọc ca kiểm thử "liên hệ anh Nam 0912…" trong test: phải là KHÔNG gộp.
