# 13 · Dashboard chăm sóc khách hàng và hồ sơ chăm sóc

Lưu trữ nằm trong cơ sở dữ liệu (CRM Lead, các DocType `Omni*`). Dashboard là **ba màn hình làm việc** đọc từ đó. Thiết kế tham chiếu: canvas "Dashboard CSKH Omni" (bộ nhận diện Miyano) — người duyệt mở để so; agent làm theo mô tả trong tệp này.

| Màn hình | Người dùng | Mục đích | Trang |
| --- | --- | --- | --- |
| 1. Tổng quan CSKH | Trưởng nhóm sale, quản lý | Hôm nay có gì cần xử lý, kênh nào đang đông, ai đang quá tải | `/app/omni-dashboard` |
| 2. Hộp thư | Sale | Đọc và trả lời mọi kênh ở một chỗ, duyệt nháp bot | `/app/omni-inbox` (D1-06, D1-09, D3-01) |
| 3. Hồ sơ chăm sóc | Sale | Format chuẩn: thông tin khách có nguồn + nhật ký mỗi phiên chăm sóc + form ghi nhật ký | Tab "Nhật ký chăm sóc" của màn 4 |
| 4. Chi tiết Lead | Sale, trưởng nhóm | Trang đầy đủ một khách: giai đoạn, thao tác nhanh, dòng hoạt động mọi kênh, việc cần làm, điểm, nghi trùng | `/app/omni-customer/<lead>` |

Màn 3 và màn 4 là **cùng một trang** `omni-customer/<lead>`: màn 4 là trang với tab "Hoạt động" đang mở, màn 3 là nội dung của tab "Nhật ký chăm sóc". Trang này không thay trang Lead có sẵn của Frappe CRM (không sửa app `crm`); hai trang liên kết qua lại.

> Khách hàng là nhà thầu (B2B): các thay đổi giao diện ở `docs/14` mục 8 (Chi tiết nhà thầu, tab Gói thầu đã trúng, khối Mức phù hợp, phễu giai đoạn, danh sách "Phù hợp A/B, chưa liên hệ") áp dụng cho các màn hình dưới đây.

Giao diện dùng bộ nhận diện Miyano: chữ Noto Sans, số mã/SĐT bằng JetBrains Mono, navy `#003366` cho tiêu đề và nút chính, nền `#f3f7fa`, viền mảnh `#d5e0e8`, nhãn trạng thái luôn có chữ (không chỉ màu). Một nút chính mỗi màn hình.

## 1. Màn hình Tổng quan

### 1.1 Bố cục (từ trên xuống)

1. Tiêu đề, giờ cập nhật, bộ chọn kỳ (Hôm nay / 7 ngày / 30 ngày), nút "Mở hộp thư".
2. Sáu ô chỉ số (bảng 1.2).
3. Bảng **Cần xử lý ngay**: Khách hàng (tên + đơn vị), Kênh, Tin gần nhất (cắt 80 ký tự), Đã chờ, Lý do ưu tiên, Phụ trách, nút "Mở" (đến Hộp thư đúng hội thoại). Tối đa 20 dòng.
4. Hai khối cạnh nhau: **Theo kênh** (số liên hệ, Lead mới theo kênh, thanh tỉ lệ) và **Bot hôm nay** (nháp đã soạn, gửi nguyên văn, sửa/bỏ, chuyển người và lý do).
5. Bảng **Theo nhân viên**: Đang phụ trách, Chờ trả lời, Phản hồi lần đầu TB, Gọi lại quá hạn, Nháp đã duyệt.

Khối thuộc tính năng chưa bật thì ẩn (không hiện số 0 gây hiểu nhầm): khối Bot ẩn khi `bot_reply_mode = Off`; ô "Gọi nhỡ" và dòng Điện thoại ẩn khi chưa có kênh tổng đài.

### 1.2 Định nghĩa chỉ số

| Chỉ số | Cách tính | Đợt |
| --- | --- | --- |
| Hội thoại chờ trả lời | Số Lead có lượt cuối (theo `conversation_turns`) là tin khách loại message/user_info, chưa có tin ra sau đó | 1 |
| Phản hồi lần đầu (TB) | Trung bình, với Lead tạo trong kỳ, thời gian từ lúc Lead vào đến `omni_first_response_at` (định nghĩa ở `docs/15` mục 7 bước 4: tin ra đầu tiên, cuộc gọi đi nghe máy, hoặc nhật ký chăm sóc đầu tiên; không tính tin sandbox), chỉ đếm phút trong giờ làm việc. Cột “Phản hồi lần đầu” theo nhân viên dùng cùng định nghĩa | 1 |
| Lead mới | CRM Lead tạo trong kỳ có `omni_first_channel` | 1 |
| Gọi nhỡ chưa gọi lại | Việc gọi lại (CRM Task do `create_callback_task` tạo) còn mở; delta = số quá hạn | 2 |
| Nháp bot chờ duyệt | `Omni Bot Draft` mode Draft, decision Pending | 3 |
| Lead nóng | Lead đang mở có `omni_score ≥ hot_lead_threshold` | 2 |

Delta (biến động) chỉ hiện khi so sánh có nghĩa, ghi rõ mốc so ("so với hôm qua"). Hướng tốt/xấu theo nghĩa nghiệp vụ, không theo lên/xuống (thời gian phản hồi giảm là tốt).

### 1.3 Thứ tự "Cần xử lý ngay"

Sắp theo thứ tự ưu tiên rồi theo thời gian chờ giảm dần:

1. Khiếu nại (`omni_intent = Khiếu nại` ở lượt gần nhất, chưa có tin ra sau đó) — Đợt 2
2. Gọi nhỡ chưa gọi lại (việc gọi lại đang mở) — Đợt 2
3. Quá hạn phản hồi (chờ > 15 phút trong giờ làm việc; ngưỡng cấu hình `Omni Settings.response_sla_minutes`)
4. Lead nóng có tin chờ — Đợt 2
5. Có nháp bot chờ duyệt — Đợt 3
6. Còn lại đang chờ

Sales User chỉ thấy dòng của Lead mình được xem; trưởng nhóm (role cấu hình) thấy toàn đội.

## 2. Màn hình Hộp thư

Ba cột (thu về một cột trên điện thoại): danh sách hội thoại | dòng thời gian | hồ sơ rút gọn.

- **Danh sách**: ô tìm (tên, SĐT, email, đơn vị); bộ lọc dạng chip: Chưa trả lời, Của tôi, Zalo, Email, Cuộc gọi, Có nháp bot. Mỗi dòng: tên, đơn vị, giờ, trích tin cuối, nhãn kênh, một nhãn ưu tiên (cùng quy tắc mục 1.3).
- **Dòng thời gian**: tin vào bên trái, tin ra bên phải, cuộc gọi là dòng riêng có tóm tắt (Đợt 3), mỗi mục ghi người · kênh · giờ. Nháp bot là khối viền xanh "Nháp của bot — chưa gửi" có nhóm câu hỏi, nguồn kho tri thức, nút Bỏ / Sửa rồi gửi / Gửi (Đợt 3). Thanh trên cùng: trạng thái bot với khách này và nút Tạm dừng/Tiếp tục (Đợt 3).
- **Khung soạn**: chọn kênh trả lời, dòng trạng thái khung gửi Zalo, nút "Ghi nhật ký chăm sóc" (mở form mục 3.3 ngay trong Hộp thư) và nút Gửi.
- **Hồ sơ rút gọn**: tên, đơn vị, nhãn Lead nóng, điểm và lý do; SĐT, email, kênh đầu, phụ trách, trạng thái; khối "Bot tóm tắt" (nhu cầu, sản phẩm, số lượng, thời điểm, mức gấp); khối "Bước tiếp theo" (bước và hạn gần nhất); nút "Mở hồ sơ chăm sóc", "Mở Lead trong CRM".

## 3. Màn hình Hồ sơ chăm sóc — format chuẩn

### 3.1 Thông tin khách hàng

Ba nhóm, mỗi trường ghi **nguồn** bằng nhãn: Kênh (khách cung cấp qua kênh), Bot (trích từ hội thoại), Sale (sửa tay — bot không ghi đè, theo `omni_manual_fields`).

| Nhóm | Trường | Lấy từ |
| --- | --- | --- |
| Liên hệ | Họ tên, Chức vụ/phòng, Đơn vị, Điện thoại, Email, Zalo (đang quan tâm OA?) | CRM Lead, Omni Identity |
| Nhu cầu | Nhu cầu, Sản phẩm quan tâm, Số lượng, Thời điểm mua, Mức gấp | `omni_*` trên CRM Lead (Đợt 2) |
| Quản lý | Nguồn/kênh đầu, Người phụ trách, Trạng thái, Liên hệ gần nhất, Điểm và lý do | CRM Lead |

Sửa trường ngay trên hồ sơ (ghi về CRM Lead, đánh dấu sửa tay).

### 3.2 Nhật ký chăm sóc — DocType `Omni Care Log`

Mỗi dòng là một **phiên chăm sóc**: chuỗi trao đổi liên tục với khách trên một kênh, kết thúc khi im lặng quá `care_session_gap_hours` (mặc định 4 giờ). Mỗi cuộc gọi là một phiên riêng.

| Trường | Kiểu | Ghi chú |
| --- | --- | --- |
| `lead` | Link CRM Lead | Bắt buộc |
| `started_at`, `last_activity_at` | Datetime | |
| `channel` | Select | Điện thoại / Zalo OA / Email / Gặp trực tiếp / Khác |
| `handled_by` | Link User | Sale xử lý chính (người gửi tin ra đầu tiên, người nghe máy) |
| `handled_kind` | Select | Sale / Bot / Khách / Hệ thống |
| `summary` | Small Text | Tự điền: bot tóm tắt (Đợt 2+) hoặc trích tin khách đầu phiên; sale sửa được |
| `outcome` | Select | (trống) / Đã tư vấn / Đã gửi tài liệu / Đã gửi báo giá / Hẹn gọi lại / Chờ khách phản hồi / Đã chốt đơn / Không có nhu cầu / Không liên lạc được / Đã xử lý khiếu nại |
| `next_action` | Data | Bắt buộc khi `outcome` không phải "Đã chốt đơn", "Không có nhu cầu" |
| `next_due` | Datetime | Bắt buộc cùng `next_action` |
| `crm_task` | Link CRM Task | Việc tạo từ bước tiếp theo |
| `auto_created` | Check | Hệ thống tự tạo |
| `call` | Link Omni Call | Với phiên là cuộc gọi |
| `outcome_by`, `outcome_at` | Link User, Datetime | Ai ghi kết quả, khi nào |

Quy tắc:

1. Worker tự tạo hoặc cập nhật phiên sau mỗi lượt xử lý (tin vào, tin ra, cuộc gọi). Không tạo phiên cho follow/unfollow, thư tự động.
2. Phiên có tin ra của sale mà sau 2 giờ chưa có `outcome` → nhãn "Chưa ghi kết quả" trong Hộp thư của người xử lý; cuối ngày gửi một thông báo tổng hợp (không cảnh báo từng phiên).
3. Có `next_action` + `next_due` và ô "Tạo việc trong CRM" được tích → tạo CRM Task giao người phụ trách Lead, tham chiếu Lead; sửa hạn trên nhật ký thì cập nhật việc.
4. `outcome = Đã chốt đơn` → gợi ý (không tự động) chuyển Lead thành Deal.
5. Nhật ký là dữ liệu của Lead: theo quyền xem Lead; gộp Lead (`docs/04` mục 5) phải chuyển cả `Omni Care Log`; "Xóa dữ liệu khách" (`docs/11` mục 6) xóa nội dung `summary`.

### 3.3 Form "Ghi nhật ký chăm sóc"

Mở từ Hộp thư hoặc Hồ sơ; điền sẵn phiên đang mở. Trường: Kênh, Thời gian, Tóm tắt, Kết quả, Bước tiếp theo, Hạn, ô "Tạo việc trong CRM" (mặc định tích). Bên cạnh là khối **Quy ước kết quả** giải thích từng giá trị (nội dung như màn hình thiết kế).

## 3b. Màn hình Chi tiết Lead

### Đầu trang

- Đường dẫn: Hộp thư / Lead / mã Lead (chữ mono).
- Tên, chức vụ · đơn vị, nhãn: Lead nóng + điểm, các kênh đã liên kết, trạng thái bot với khách.
- Thao tác nhanh: Gọi (Đợt 2+, khi tổng đài hỗ trợ gọi ra; chưa có thì hiện SĐT để bấm gọi trên điện thoại), Nhắn Zalo (mở Hộp thư), Gửi email (trình soạn của CRM), Ghi nhật ký chăm sóc, **Chuyển thành Deal** (nút chính duy nhất — gọi đúng hành động chuyển đổi có sẵn của CRM).
- Thanh giai đoạn: các bước lấy từ **danh sách CRM Lead Status** đã xác nhận ở G1 (không viết cứng), bước hiện tại tô đậm.

### Ba cột

| Cột trái — thông tin | Cột giữa — tab | Cột phải — theo dõi |
| --- | --- | --- |
| Liên hệ (sửa tại chỗ) | **Hoạt động**: mọi sự kiện theo ngày, mới nhất trước — tin vào/ra mọi kênh, cuộc gọi kèm tóm tắt, nhật ký chăm sóc, nháp bot, thay đổi điểm, gộp kênh/Lead, tạo Lead, đổi người phụ trách. Lọc: Tất cả / Tin nhắn / Cuộc gọi / Nhật ký / Hệ thống | Việc cần làm (CRM Task của Lead: tích hoàn thành, thêm việc) |
| Kênh đã liên kết (mỗi Omni Identity: kênh, mã/SĐT/email, lần liên hệ gần nhất, Zalo có đang quan tâm OA) | **Nhật ký chăm sóc**: format màn 3 | Điểm Lead: điểm, ngưỡng, từng tín hiệu đã cộng (từ `omni_score_breakdown`) |
| Nhu cầu (bot điền, nhãn nguồn) | **Cuộc gọi**: danh sách Omni Call, nghe ghi âm (theo quyền), bản ghi | Nghi trùng: các `Omni Duplicate Candidate` Open liên quan, nút Gộp vào Lead này / Không phải trùng |
| | **Việc cần làm**, **Ghi chú và tệp** (CRM Note, tệp đính kèm), **Báo giá · Deal** (Deal đã chuyển, liên kết) | Quản lý: phụ trách, nguồn, tạo lúc, liên hệ gần nhất, số lần liên hệ 30 ngày |

Dòng "Hệ thống" trong tab Hoạt động lấy từ: tạo Lead, Version của CRM Lead (đổi phụ trách, trạng thái), lần đầu vượt ngưỡng Lead nóng, gộp kênh/Lead (Comment do `merge_leads` ghi). Mỗi sự kiện có giờ, tiêu đề, nhãn nguồn, một dòng chi tiết; bấm vào mở đúng chỗ (hội thoại trong Hộp thư, cuộc gọi, nhật ký).

## 4. Dữ liệu và hiệu năng

- Số liệu Tổng quan tính bằng truy vấn gộp trong `api/dashboard.py::snapshot(period, scope)`, cache 60 giây theo (kỳ, phạm vi người dùng); p95 < 800 ms với 100.000 tin vào.
- Hộp thư và Hồ sơ cập nhật realtime theo cơ chế D1-06.
- Mọi API kiểm quyền theo Lead như các API khác của app.

## 5. Cấu hình bổ sung (Omni Settings)

| Trường | Kiểu | Mặc định |
| --- | --- | --- |
| `response_sla_minutes` | Int | 15 |
| `care_session_gap_hours` | Int | 4 |
| `team_lead_role` | Link Role | Sales Manager |

## 6. Ghi chú thi hành V2-04 (khác biệt so với văn bản trên)

- Trường hợp 1: v1 chưa có D1-12 nên V2-04 làm trọn. Mã: `crm_bridge/care_log.py`, `dashboard/{access,priority,blocks}.py`, `api/dashboard.py`, `api/customer.py`, `prospect/fit_view.py`, trang `omni-dashboard`, `omni-customer`, `public/css/omni.css` (mọi luật dưới `.omni-ui`).
- Công tắc: `dashboard_enabled`, `care_log_enabled` (docs/09). Khối Đợt 2-3 đăng ký bằng `@block(key, phase, enabled=…)`; khối tắt vắng mặt trong kết quả.
- Phạm vi: truy vấn gộp với điều kiện quyền CRM Lead làm truy vấn con; `scope` ("mine"/"team") chỉ thu hẹp, không mở rộng. Cache 60 giây theo (người dùng, kỳ, phạm vi).
- Phản hồi lần đầu / Lead mới chỉ tính Lead có `omni_first_channel`; delta so với kỳ liền trước cùng độ dài, chỉ hiện khi mốc so sánh khác 0.
- "Chưa ghi kết quả": phiên do Sale xử lý, chưa có kết quả, im lặng >= 2 giờ.
- Hạng "Phù hợp A/B, chưa liên hệ": nhà thầu hạng A/B có Lead (xem được, chưa chuyển Deal) mà không Lead nào có `omni_first_response_at` hoặc đã rời giai đoạn "Tiềm năng"/"New".
- Hiệu năng (máy dev, dữ liệu tổng hợp, KHÔNG phải staging): xem `docs/11-van-hanh.md` mục "Lưu ý V2-04".
- Chưa làm: ảnh chụp 5 màn hình trên staging; form ghi nhật ký ngay trong Hộp thư; giao diện chưa thử trên trình duyệt.

