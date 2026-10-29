# 04 · Định danh và chống trùng

Nguyên tắc: **gộp khi chắc, hỏi người khi không chắc.** Một Lead trùng gây phiền (hai sale gọi một khách); một lần gộp sai gây hại (lộ hội thoại của người này cho người khác). Vì vậy hệ thống ưu tiên tránh gộp sai.

## 1. Chuẩn hóa

### 1.1 Số điện thoại → E.164 (`+84…`)

`identity/normalize.py::normalize_phone(raw: str) -> str | None`

1. Bỏ khoảng trắng, dấu chấm, gạch, ngoặc.
2. `00` đầu → `+`. `84…` (không có +) dài 11–12 số → `+84…`. `0…` → `+84` + phần sau số 0.
3. Đổi đầu số 11 số cũ sang 10 số (chuyển đổi năm 2018):

| Cũ | Mới | Cũ | Mới |
| --- | --- | --- | --- |
| 0120 | 070 | 0123 | 083 |
| 0121 | 079 | 0124 | 084 |
| 0122 | 077 | 0125 | 085 |
| 0126 | 076 | 0127 | 081 |
| 0128 | 078 | 0129 | 082 |
| 0162–0169 | 032–039 | 0186 | 056 |
| 0188 | 058 | 0199 | 059 |

4. Kiểm tra hợp lệ bằng thư viện `phonenumbers` (vùng `VN`); không hợp lệ → `None` (không dùng để khớp, vẫn lưu dạng thô trong `sender_profile`).
5. Số cố định (024, 028…) giữ nguyên, vẫn chuẩn hóa E.164.

Test bắt buộc: ít nhất 25 ca, gồm mọi dạng viết ở trên, số quốc tế, số sai, chuỗi rỗng, số lẫn chữ.

### 1.2 Email

Chữ thường, bỏ khoảng trắng. Không bỏ dấu chấm hay phần `+tag` (tránh gộp sai). Bỏ qua địa chỉ hệ thống: `no-reply`, `noreply`, `mailer-daemon`, `postmaster`, và danh sách cấu hình.

### 1.3 Trích SĐT, email từ nội dung

`extract_contacts(text) -> {"phones": [...], "emails": [...]}` bằng biểu thức chính quy cho số Việt Nam (có/không khoảng cách, dấu chấm). Kết quả là **tín hiệu trung bình** (khách có thể gửi số của người khác: "liên hệ anh Nam 09…").

## 2. Độ mạnh tín hiệu

| Tín hiệu | Độ mạnh |
| --- | --- |
| Định danh kênh đã gắn Lead (`Omni Identity.lead`) | Chắc chắn |
| SĐT gọi đến (caller id) | Mạnh |
| Email người gửi (kênh email) | Mạnh |
| SĐT/email khách gửi qua form yêu cầu thông tin Zalo | Mạnh |
| SĐT/email trích từ nội dung tin, chữ ký email | Trung bình |
| Tên hiển thị giống nhau | Yếu — chỉ dùng tạo nghi trùng, không bao giờ để khớp |

## 3. Thuật toán `resolver.resolve(msgs) -> (identity, lead)`

```text
1. identity = get_or_create Omni Identity theo identity_key; cập nhật display_name, phone_e164/email_norm nếu có tín hiệu mạnh
2. Nếu identity.lead tồn tại, chưa bị gộp:            → dùng lead đó. Kết thúc (vẫn thực hiện bước 6).
3. Thu tín hiệu mạnh S và trung bình M từ msgs + sender_profile
4. Tìm Lead ứng viên:
     L_S = Lead có omni_phone_e164 ∈ S.phones hoặc omni_email_norm ∈ S.emails
           + Lead gắn với Omni Identity khác có phone/email ∈ S
     L_M = tương tự với M
5. Quyết định:
     |L_S| == 1                       → gắn lead đó
     |L_S| > 1                        → gắn lead hoạt động gần nhất; tạo Duplicate Candidate cho từng lead còn lại
     |L_S| == 0 và |L_M| == 1         → gắn nếu tên không mâu thuẫn (xem 3.1); mâu thuẫn → tạo lead mới + Duplicate Candidate
     |L_S| == 0 và |L_M| > 1          → tạo lead mới + Duplicate Candidate với từng lead
     không có gì                      → tạo lead mới
6. Lead mới hoặc đã có: tìm Lead khác có tên hiển thị giống (≥ 0.9 sau bỏ dấu, chữ thường) trong 90 ngày
     → tạo Duplicate Candidate (score theo độ giống), không gộp
7. Gắn identity.lead = lead; commit
```

### 3.1 "Tên mâu thuẫn"

Cả hai tên có ít nhất 2 từ và độ giống (sau bỏ dấu, chữ thường, so khớp từ) < 0.5. Tên một từ hoặc tên chung chung ("Anh", "Chị", "Khách", "Zalo user") coi như không có thông tin.

### 3.2 Lead đã chuyển thành Deal

Đọc cách app `crm` đánh dấu Lead đã chuyển (trường `converted` và liên kết Deal — xác nhận trong mã). Nếu đã chuyển: vẫn gắn Lead đó, nhưng ghi dòng thời gian vào **Deal** tương ứng. Ghi quyết định thật vào `docs/12-quyet-dinh-mo.md`.

## 4. Trường chuẩn hóa trên CRM Lead

App `crm` lưu SĐT, email dạng thô, nên thêm hai custom field ẩn, có chỉ mục (Đợt 1, bổ sung vào danh sách ở `docs/02` mục 15):

| fieldname | Kiểu | Ghi chú |
| --- | --- | --- |
| `omni_phone_e164` | Data, search_index | Tính lại mỗi khi Lead lưu, từ `mobile_no` và `phone` |
| `omni_email_norm` | Data, search_index | Từ `email` |
| `omni_merged_into` | Link CRM Lead | Lead bị gộp trỏ về Lead chính |

Hook `CRM Lead.validate` tính hai trường đầu. Có patch chạy một lần để tính cho Lead cũ.

## 5. Gộp Lead

`identity/merge.py::merge_leads(primary, secondary, user)` — chỉ chạy khi sale/quản trị bấm "Gộp" trên `Omni Duplicate Candidate`:

1. Kiểm tra quyền ghi cả hai Lead.
2. Chuyển sang `primary`: mọi `Omni Identity`, `Omni Inbound Message`, `Omni Outbound Message`, `Omni Call`, `Omni Bot Draft` đang trỏ `secondary`; `Communication`, `Comment`, `CRM Call Log`, `CRM Task`, `CRM Note` có tham chiếu tới `secondary` (đổi `reference_name` — xác nhận tên trường tham chiếu trong từng DocType của `crm`).
3. Trường trống ở `primary` lấy giá trị từ `secondary`; trường đã có giữ nguyên; ghi danh sách khác biệt vào một Comment trên `primary`.
4. `secondary`: đặt trạng thái loại (ví dụ `Junk` — xác nhận danh sách CRM Lead Status), `omni_merged_into = primary`, thêm Comment "Đã gộp vào …".
5. Không xóa `secondary`. Thao tác chạy trong một transaction; lỗi giữa chừng thì rollback hết.
6. Đánh dấu Candidate `Merged`; Candidate khác có liên quan `secondary` được trỏ lại sang `primary` hoặc đóng nếu thành cặp trùng chính nó.

Có thể đánh giá `frappe.rename_doc(..., merge=True)` như một cách thay thế; chỉ dùng nếu đã chứng minh bằng test rằng nó chuyển đủ các liên kết ở bước 2 và không đụng tới dữ liệu khác. Ghi lựa chọn vào ADR.

## 6. Test bắt buộc

- Khách nhắn Zalo (không SĐT) → 1 Lead. Sau đó gửi form thông tin có SĐT → Lead đó được cập nhật SĐT, không tạo Lead mới.
- Cùng khách gửi email với chữ ký có SĐT đó → gắn đúng Lead (tín hiệu trung bình, tên không mâu thuẫn).
- Hai Lead cùng SĐT có sẵn → tin mới gắn Lead gần nhất + 1 Duplicate Candidate.
- Khách gửi "liên hệ anh Nam 0912…" trong khi Lead của anh Nam đã có SĐT đó và tên khác hẳn → tạo Lead mới + Candidate, **không gộp**.
- Gửi lại cùng tin hai lần → 1 Lead, 1 dòng thời gian.
- Gộp rồi kiểm tra mọi bản ghi liên quan đã chuyển; chạy gộp lần hai không lỗi.
