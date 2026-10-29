# 12 · Quyết định mở, giả định cần xác nhận, phát hiện

Tệp sống: agent cập nhật mỗi khi xác nhận một giả định hoặc phát hiện điều khác đặc tả. Người duyệt đọc tệp này trước mỗi cổng phát hành.

## A. Quyết định của chủ dự án

| # | Quyết định | Ảnh hưởng tới | Trạng thái |
| --- | --- | --- | --- |
| Q1 | Bot tự gửi hay chỉ soạn nháp — mặc định: nháp, mở tự động theo nhóm khi đạt chuẩn | D3-05 | Đề xuất |
| Q2 | Nhà cung cấp tổng đài — mặc định: adapter chung, bản đầu Stringee | D2-01, D2-02 | Đã chốt: adapter chung |
| Q3 | Mô hình AI và dịch vụ chuyển giọng nói | D2-04, D3-03 | Mô hình AI: Claude; STT: chờ D3-03 |
| Q4 | Quy tắc gán Lead cho sale (theo sản phẩm/khu vực/vòng tròn) | D1-06 (Assignment Rule của CRM) | Chưa chốt |
| Q5 | Bảng điểm Lead | D2-06 (`mau/bang-diem-lead-mau.csv`) | Chưa chốt |
| Q6 | Giờ bot hoạt động, câu trả lời ngoài giờ | D3-02 | Chưa chốt |
| Q7 | Thời hạn lưu dữ liệu, ghi âm; ai được xem | D1-02, D1-10, D3-04 | Chưa chốt — mặc định 180 ngày payload gốc, 365 ngày ghi âm |
| Q8 | Kênh báo cho sale khi chuyển người | D3-02 | Chưa chốt — mặc định thông báo hệ thống + email |
| Q9 | Có tạo Lead khi khách chỉ bấm quan tâm OA? | D1-08 | Mặc định: không |
| Q10 | Lead đã chuyển thành Deal: ghi tin mới vào Deal | D1-05, D1-06 | Mặc định: có |
| Q11 | Chính sách gửi dữ liệu hội thoại khách sang dịch vụ AI (Claude API) và dịch vụ chuyển giọng nói: được phép không, phạm vi, thông báo cho khách | D2-04, D2-05, D2-07, D2-08, D3-03 | **Chưa chốt — chặn mọi lần chạy AI trên dữ liệu khách thật** |

## B. Giả định cần xác nhận với hệ thống thật

Agent của prompt ghi trong cột "Ai xác nhận" phải kiểm tra rồi chuyển dòng sang mục C.

| # | Giả định | Nguồn cần đọc | Ai xác nhận |
| --- | --- | --- | --- |
| G1 | Tên và kiểu trường của `CRM Lead`, `CRM Call Log`, `CRM Task`, `CRM Note`, danh sách `CRM Lead Status` | `apps/crm/crm/fcrm/doctype/*/*.json` | D1-02 |
| G2 | Cách CRM hiển thị hoạt động trên Lead (email, bình luận, cuộc gọi…) và có hiện Communication không phải email không | `apps/crm/crm/api/activities.py` | D1-06 |
| G3 | Cách CRM tạo Lead từ email và cách tắt cho hộp thư bán hàng | Email Account, hooks của `crm` | D1-07 |
| G4 | `frappe.enqueue(job_id=..., deduplicate=True)` hoạt động trên bản v15 đang cài | `frappe/utils/background_jobs.py` | D1-03 |
| G5 | Cú pháp khóa Redis qua `frappe.cache` trên v15 | `frappe/utils/redis_wrapper.py` | D1-04 |
| G6 | Cấu hình hàng đợi riêng `workers` trong `common_site_config.json` | Tài liệu bench v15, `frappe/utils/background_jobs.py` | D1-04 |
| G7 | Công thức chữ ký webhook Zalo, tên sự kiện, cấu trúc payload | developers.zalo.me | D1-08 |
| G8 | Endpoint và quy tắc làm mới token Zalo (refresh token dùng một lần) | developers.zalo.me | D1-08 |
| G9 | Khung thời gian và phí tin tư vấn Zalo (48 giờ / 7 ngày), ZBS Template từ 2026 | developers.zalo.me, hợp đồng OA | D1-08 |
| G10 | Định dạng phản hồi tra cứu khách, payload sự kiện, chữ ký (nếu có) của Stringee | developer.stringee.com | D2-02 |
| G11 | Assignment Rule và SLA của CRM áp dụng được cho Lead tạo bằng code | Tài liệu và mã `crm` | D1-06 |
| G12 | Cách gọi Messages API, tool use, prompt caching, tên mô hình hiện hành | docs.claude.com | D2-04 |
| G13 | Thư viện `phonenumbers` có sẵn trong môi trường bench | `pip show phonenumbers` | D1-05 |

## C. Đã xác nhận

| # | Nội dung đã xác nhận | Bằng chứng (tệp/dòng, link tài liệu) | Ngày | Người/agent |
| --- | --- | --- | --- | --- |
| G1 — CRM Lead: tên trường thật | `first_name`, `middle_name`, `last_name`, `lead_name` (Data, tự ghép từ salutation+first+middle+last khi lưu; chỉ ghép nếu có `first_name`), `email` (Data, Options Email), `mobile_no` (Data, Phone), `phone` (Data, Phone), `organization` (Data, tên tổ chức dạng chuỗi, không phải Link), `job_title`, `source` (Link CRM Lead Source), `lead_owner` (Link User), `status` (Link CRM Lead Status), `converted` (Check — cờ đã chuyển Deal), `territory`, `industry`, `sla`, `communication_status`. Naming series `CRM-LEAD-.YYYY.-`, `title_field` là `lead_name`. Không có trường nào của CRM trùng ý nghĩa với 6 custom field `omni_*` Đợt 1 nên tạo cả 6. `validate` của Lead gọi `validate_email` -> `has_gravatar` (gọi mạng) khi có email: test dùng Lead không có email. | `apps/crm/crm/fcrm/doctype/crm_lead/crm_lead.json`, `crm_lead.py` (validate, set_full_name, validate_email) | 2026-10-29 | D1-02 (agent) |
| G1 — Trạng thái Lead mặc định | `CRM Lead Status` autoname `field:lead_status`, trường `lead_status`, `type` (Open/Ongoing/On Hold/Won/Lost), `position`, `color`. Mặc định cài bởi `install.py`: New (Open), Contacted (Ongoing), Nurture (Ongoing), Qualified (Won), Converted (Won), Unqualified (Lost), Junk (Lost). Lead mới tự gán `New`. Trạng thái loại Lost bắt buộc `lost_reason` khi lưu. `convert_to_deal` đặt `status`=Qualified (nếu tồn tại) và `converted`=1, không dùng Converted. | `apps/crm/crm/fcrm/doctype/crm_lead_status/crm_lead_status.json`, `apps/crm/crm/install.py::add_default_lead_statuses`, `crm_lead.py::validate_status/validate_lost_reason/convert_to_deal` | 2026-10-29 | D1-02 (agent) |
| G1 — CRM Call Log | Tên bản ghi theo trường `id` (autoname `field:id`). Trường: `id`, `from`, `to`, `type` (Incoming/Outgoing), `status` (Initiated/Ringing/In Progress/Completed/Failed/Busy/No Answer/Queued/Canceled), `start_time`, `end_time`, `duration` (Duration), `recording_url`, `caller` (Link User), `receiver` (Link User), `note` (Link FCRM Note), `telephony_medium` (Manual/Twilio/Exotel), `medium`. Tham chiếu tới Lead/Deal: `reference_doctype` (Link DocType) + `reference_docname` (Dynamic Link) và bảng con `links` (Dynamic Link). Chưa có chỗ cho Stringee: D2 phải kiểm tra `telephony_medium` (Select cố định) trước khi ghi. | `apps/crm/crm/fcrm/doctype/crm_call_log/crm_call_log.json` | 2026-10-29 | D1-02 (agent) |
| G1 — CRM Task | Trường: `title`, `description`, `status` (Backlog/Todo/In Progress/Done/Canceled), `priority` (Low/Medium/High), `assigned_to` (Link User), `start_date` (Date), `due_date` (Datetime). Tham chiếu: `reference_doctype` + `reference_docname` (Dynamic Link). Naming autoincrement. | `apps/crm/crm/fcrm/doctype/crm_task/crm_task.json` | 2026-10-29 | D1-02 (agent) |
| G1 — Ghi chú (đặc tả gọi `CRM Note`) | DocType thật tên **`FCRM Note`** (không có `CRM Note`): `title`, `content` (Text Editor), `reference_doctype` + `reference_docname` (Dynamic Link). Các prompt sau dùng tên `FCRM Note`. | `apps/crm/crm/fcrm/doctype/fcrm_note/fcrm_note.json` (không tồn tại `crm_note/`) | 2026-10-29 | D1-02 (agent) |
| G1 — CRM Deal | Có `lead` (Link CRM Lead) trỏ về Lead gốc; `deal_owner`, `status` (Link CRM Deal Status), `email`, `mobile_no`, `phone`, `organization` (Link CRM Organization), `source`. Hành vi Q10 (ghi tin vào Deal đã chuyển) dùng `lead` để tra ngược. | `apps/crm/crm/fcrm/doctype/crm_deal/crm_deal.json` | 2026-10-29 | D1-02 (agent) |
| G1 — Hệ quả cho D1-02 | Vị trí Custom Field: chuỗi `insert_after` bắt đầu từ `lead_owner` (nằm trong khối `details`, trước `organization_tab`): `omni_first_channel` -> `omni_last_inbound_at` -> `omni_merged_into` -> `omni_manual_fields` -> `omni_phone_e164` -> `omni_email_norm`. Ba trường kỹ thuật hidden=1, read_only=1. | `omni_crm/fixtures/custom_field.json` | 2026-10-29 | D1-02 (agent) |
| G13 — `phonenumbers` | Đã có trong môi trường bench: phonenumbers 8.13.55 (kiểm tra ngẫu nhiên khi chạy D1-02; D1-05 vẫn cần xác nhận lại khi dùng). | `bench/env/bin/pip list` | 2026-10-29 | D1-02 (agent) |
| Kỹ thuật Frappe v15.107.2 (D1-02) | `frappe.db.add_unique/add_index` trong `on_doctype_update` (đặt ở module controller) chạy khi migrate và tồn tại sau lần migrate thứ hai (đã kiểm tra bằng information_schema). Password field lưu `*****` trong bản ghi, `as_dict()` không lộ bí mật, đọc bằng `get_password()`. Unique vi phạm qua `doc.insert()` ném `frappe.UniqueValidationError`; SQL thô ném `pymysql.err.IntegrityError` (1062). | `omni_crm/tests/test_models.py` | 2026-10-29 | D1-02 (agent) |
| G4 — `frappe.enqueue` job_id/deduplicate | Đã xác nhận trên Frappe v15.107.2: `enqueue(..., enqueue_after_commit, job_id, deduplicate)` đều có. `deduplicate=True` bắt buộc `job_id`; nếu job cùng id đang `QUEUED` hoặc `STARTED` thì **bỏ qua, trả None** (chỉ ghi `frappe.logger().error`); nếu job cũ đã xong/lỗi thì xóa rồi tạo mới. `job_id` được gắn tiền tố `"{site}::"`. Hệ quả: (1) tin đến lúc worker đang `STARTED` sẽ không có job mới, phụ thuộc vòng lặp tối đa 5 vòng của worker (D1-04 phải kiểm tra lại tin New trước khi thoát) và tác vụ quét tin New > 2 phút; (2) kiểm tra trùng không nguyên tử (hai request đồng thời có thể cùng đẩy), an toàn nhờ khóa theo định danh ở worker; (3) với `enqueue_after_commit=True`, kiểm tra trùng chạy ngay lúc gọi (trước commit) còn việc đẩy job chạy trong `frappe.db.commit()` (`after_commit.run()` sau khi COMMIT) và **lỗi Redis sẽ ném ra từ chính `commit()`** dù bản ghi đã lưu. Vì vậy `enqueue_identity` tự đăng ký callback `after_commit` bọc `frappe.enqueue` (không dùng cờ `enqueue_after_commit`) và bắt mọi lỗi. Không cần phương án Redis SET NX, không cần ADR. | `apps/frappe/frappe/utils/background_jobs.py` (enqueue, dòng 59-172; create_job_id), `apps/frappe/frappe/database/database.py::commit` (dòng 1173-1184), `omni_crm/ingest/gateway.py`, `omni_crm/tests/test_gateway.py` | 2026-10-29 | D1-03 (agent) |
| Kỹ thuật Frappe (D1-03) | `@frappe.whitelist(methods=["POST"])` trả 403 khi sai method, không phải 405 nên webhook tự kiểm tra method trong `_handle_webhook`. Trường kiểu JSON phải truyền chuỗi (`frappe.as_json`), danh sách/dict bị từ chối. `frappe.cache().get_value(key)` mặc định cache cả giá trị None trong `frappe.local.cache` cho cả request: dùng `expires=True` cho khóa có TTL. Mã HTTP trả qua `frappe.local.response["http_status_code"]`. | `apps/frappe/frappe/__init__.py::whitelist`, `redis_wrapper.py::get_value`, `omni_crm/api/webhooks.py` | 2026-10-29 | D1-03 (agent) |

## D. Phát hiện ngoài phạm vi

| Ngày | Phát hiện | Mức độ | Đề xuất | Prompt phát hiện |
| --- | --- | --- | --- | --- |
| 2026-10-29 | Nhánh git hiện tại của repo là `develop`, không phải `main` như prompt nói; nhánh d1-02 tách từ `develop` (cùng commit D1-01). | Thấp | Điều phối viên merge vào nhánh đúng. | D1-02 |
| 2026-10-29 | Theo docs/02 mục 16 Sales User có quyền Đọc ở mức DocType trên Omni Inbound/Outbound Message và Omni Identity: mọi Sales User đọc được toàn bộ tin (kể cả `raw_payload`) qua Desk/API chuẩn, không giới hạn theo Lead. | Trung bình | Thêm `permission_query_conditions`/`has_permission` theo Lead xem được (prompt API/D1-06), hoặc bỏ `raw_payload` khỏi tầm nhìn bằng permlevel. Cần quyết định trước khi cho sale dùng. | D1-02 |
| 2026-10-29 | `CRM Lead.validate` gọi `has_gravatar` (request mạng) khi Lead có email; luồng tạo Lead trong worker nên đặt `flags.ignore_email_validation`/chấp nhận độ trễ và không giữ khóa DB khi đó. | Thấp | Xem lại ở D1-05/D1-06. | D1-02 |
| 2026-10-29 | `CRM Call Log.telephony_medium` là Select cố định (Manual/Twilio/Exotel); Stringee chưa có. | Thấp | D2-02 quyết định để trống/`Manual` hay Property Setter. | D1-02 |
| 2026-10-29 | Ghi Error Log khi xác thực sai (theo docs/03 mục 1 bước 4) cho phép bên ngoài làm tràn bảng Error Log. D1-03 giới hạn 1 dòng/phút/kênh/loại lỗi qua cache; webhook sai xác thực phải `commit` riêng để dòng log không bị rollback. | Thấp | Cân nhắc rate limit ở tầng nginx cho `/api/method/omni_crm.api.webhooks.*`. | D1-03 |
| 2026-10-29 | Đo p95 webhook (200 request, adapter giả, `frappe.enqueue` mock, `commit` mock) chỉ phản ánh phần Python + insert DB, chưa gồm mạng/Redis thật/adapter Zalo thật (chữ ký). | Thấp | Đo lại ở D1-10 với Redis thật. | D1-03 |
