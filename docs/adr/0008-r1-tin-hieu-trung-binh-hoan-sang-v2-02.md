# 0008 · R1 và tín hiệu trung bình (hoãn ở V2-01, thi hành ở V2-02)

- Ngày: 2026-11-02
- Trạng thái: Đã chấp nhận (2026-11-02, anh chốt ĐQ-4; thi hành ở V2-02)
- Prompt: V2-01 (gap-report 04.06, 04.07, 04.08)

## Bối cảnh
Quyết định của anh ở cổng 1 (V2-00): siết R1 (gộp sai do tín hiệu trung bình: tên rỗng/một từ/email trong nội dung) **chỉ làm ở V2-02/V2-03, không làm ở V2-01**. Vì vậy V2-01 không đổi bộ khớp khách (`identity/resolver.py`, `worker`).

Ba dòng liên quan và hiện trạng:
- **04.06 (R1)**: giữ nguyên hành vi đúng chữ docs/04; `test_resolver::test_medium_signal_with_placeholder_sender_name_attaches_per_spec` ghi nhận hành vi và rủi ro.
- **04.07** (SĐT nhắc trong nội dung không được lưu nên không nối được Lead tạo sau): cùng gốc với R1 — nếu lưu tín hiệu trung bình thì phải chốt luật khớp (một Organization/Contact duy nhất, docs/14).
- **04.08** (`oa_echo` tới trước khi khách từng nhắn tạo Lead rỗng tên): resolver không có tín hiệu nào cho echo; cách sửa gọn là không tạo Lead khi cả lô chỉ có `oa_echo` và định danh chưa có Lead. Sửa này đổi luật tạo Lead nên đi chung với R1.

## Đề xuất cho V2-02/V2-03
1. Tín hiệu trung bình cũ: chỉ gắn khi tên hai bên đều không rỗng, không một từ và độ giống >= 0,5; ngoài ra tạo Lead mới + Omni Duplicate Candidate.
2. Tín hiệu trung bình mới (MST khách tự khai, tên miền email công ty): chỉ gắn khi khớp **đúng một** Organization; không thì tạo Candidate.
3. Lô chỉ có `oa_echo` và định danh chưa có Lead: ghi Identity, không tạo Lead (04.08).
4. Lưu tín hiệu trung bình trên Identity (trường mới, cần duyệt DocType) hoặc chấp nhận không nối được (04.07).

## Quyết định (đã chấp nhận)
Anh chốt ĐQ-4: siết tín hiệu trung bình; khớp mã số thuế / tên miền email vào Organization CHỈ khi đúng một Organization khớp, mơ hồ thì không tự liên kết mà đưa vào danh sách xử lý tay. Thi hành ở V2-02 trong `identity/resolver.py`:
1. **Tín hiệu trung bình cũ** (SĐT/email trong nội dung): chỉ gắn khi tên hai bên đều có >= 2 từ riêng và độ giống >= 0,5 (`_names_compatible`). Ngoài ra tạo Lead mới + Omni Duplicate Candidate (điểm 60 nếu tên mâu thuẫn, 55 nếu tên thiếu/một từ/chưa đủ giống, 50 nếu nhiều Lead). Lý do chấp nhận: lỗi gộp sai (khách ẩn danh nhắc số của người khác bị gắn vào Lead của người đó) nguy hiểm hơn lỗi tách nhầm, vì tách nhầm sửa được bằng một lần bấm Gộp, còn gộp sai làm lộ hội thoại của người này cho người kia.
2. **Tín hiệu mới** (MST khách tự khai, Contact đã biết, tên miền email công ty): chỉ gắn khi khớp đúng MỘT Organization. Mơ hồ -> Lead mới + nghi trùng với Lead của từng Organization ứng viên + Comment liệt kê ứng viên (đó là "danh sách xử lý tay": màn Nghi trùng của Hộp thư). MST chỉ lấy từ hồ sơ có cấu trúc của kênh (`sender_profile.tax_id`), KHÔNG trích bằng regex từ văn bản vì 10 số dễ lẫn với SĐT; SĐT/email trong nội dung không dùng để khớp Contact.
3. **04.08**: lô chỉ có `oa_echo` và định danh chưa có Lead/chuỗi thư -> ghi Identity, không tạo Lead (`Resolution.lead = None`; worker, dòng thời gian và `first_response` chịu được `lead = None`, có test). Echo đó không gắn vào Lead về sau (chấp nhận).
4. **04.07**: chấp nhận không nối được. Không thêm trường vào Omni Identity (đổi DocType ngoài danh sách, phải có ADR riêng). Hệ quả: SĐT khách nhắc trong nội dung mà Lead được tạo sau đó sẽ không tự nối; người xử lý bằng nghi trùng/gộp tay.

**Bổ sung sau reviewer (V2-02):** độ giống tập từ >= 0,5 vẫn gắn nhầm "Nguyễn Văn Nam" ~ "Nguyễn Văn Hùng" và "Nguyễn Hùng" ~ "Nguyễn Nam" (chung họ). Nay còn bắt buộc tên riêng (từ cuối) của mỗi bên có mặt trong tên bên kia (cho phép đổi thứ tự họ/tên và khác dấu). Khớp tên miền chỉ dùng email của kênh Email (email tự khai ở form Zalo/cuộc gọi không dùng); tên miền thư chung (kể cả fpt.vn, vnpt.vn, viettel.com.vn) và website mạng xã hội của Organization bị loại.

Định danh đã gắn một Lead khác với Lead của Organization khớp: KHÔNG gắn lại (đổi hành vi với khách), chỉ tạo nghi trùng (điểm 70).

Test cũ đã đổi vì mâu thuẫn trực tiếp: `test_resolver::TestResolverBasics::test_medium_signal_with_placeholder_sender_name_attaches_per_spec` (ghim hành vi cũ "tên rỗng vẫn gắn") thành `..._does_not_attach_r1`.

## Hệ quả
- 04.06–04.08 đóng ở V2-02 (gap-report). Rủi ro R1 giảm nhưng chưa đo trên dữ liệu thật: cần đo tỉ lệ tách nhầm (Lead tạo mới có nghi trùng) trên staging trước khi bật cho khách thật.
