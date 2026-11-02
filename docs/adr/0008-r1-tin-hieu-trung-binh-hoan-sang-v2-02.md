# 0008 · R1 và tín hiệu trung bình: hoãn sang V2-02/V2-03

- Ngày: 2026-11-02
- Trạng thái: Đề xuất
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

## Hệ quả
- Ba dòng giữ trạng thái "Có-lệch, đã có ADR chờ quyết định", `V2-02`. Rủi ro R1 vẫn mở trước khi bật cho khách thật (docs/nghiem-thu/dot-1.md 9.1 mục 3).
