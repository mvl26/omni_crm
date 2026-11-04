# 02 · Cấu hình Omni Settings

Dành cho: quản trị hệ thống và trưởng sale (một số mục cần trưởng sale quyết). Mở: **Desk > `/app/omni-settings`** (đường dẫn theo quy ước Frappe, chưa kiểm trên giao diện). Chỉ vai trò **Omni Admin** (và Administrator) mở và sửa được. Mọi thay đổi lưu lịch sử phiên bản (`track_changes`), xem ở mục Activity/Version của form.

Mục lục: [1. Nhóm Chung](#1-chung) · [2. Xử lý và thử lại](#2-xử-lý-và-thử-lại) · [3. Thời hạn lưu](#3-thời-hạn-lưu) · [4. Giờ làm việc và ngày lễ](#4-ánh-xạ-nguồn-lead-giờ-làm-việc-ngày-lễ) · [5. Cảnh báo và phân việc](#5-cảnh-báo-và-gán-lead) · [6. Tổng đài](#6-tổng-đài) · [7. Bot AI và ngân sách](#7-bot-ai-và-ngân-sách-token) · [8. Chấm điểm Lead](#8-chấm-điểm-lead) · [9. Chuyển người và tự gửi](#9-chuyển-người-nháp-và-tự-gửi) · [9A. Quyết định phát hành](#9a-quyết-định-phát-hành-q1-q11-và-cổng-bot) · [9B. Nhà thầu](#9b-nhà-thầu-điểm-phù-hợp-và-nhập-danh-sách) · [9C. Chăm sóc và Tổng quan](#9c-chăm-sóc-khách-và-tổng-quan) · [9D. Lead quảng cáo](#9d-lead-quảng-cáo-và-web-form) · [9E. Meta, hàng dự phòng](#9e-meta-kéo-lại-lead-và-hàng-dự-phòng-webhook) · [10. Ghi âm/STT](#10-ghi-âm-và-chuyển-giọng-nói) · [11. Bảng công tắc](#11-bảng-công-tắc--hiệu-ứng) · [11A. Cờ site_config và kênh](#11a-cờ-site_config-và-trường-của-kênh) · [11B. Lệnh kiểm trước phát hành](#11b-lệnh-kiểm-trước-phát-hành-các-mục-nó-kiểm) · [12. Sự cố thường gặp](#12-sự-cố-thường-gặp) · [13. Đã xong khi](#13-đã-xong-khi)

Cột "Khuyên dùng" là **đề xuất của người viết tài liệu**, không phải quyết định của chủ dự án; cột "Mặc định" lấy từ định nghĩa DocType trong mã. Chỗ nào chưa chốt thì ghi rõ.

## 1. Chung

| Trường (nhãn trên form) | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Chế độ thử (sandbox) | `sandbox_mode` | Bật | Dev/staging: bật. Production: **tắt có chủ đích** | Bật = chiều ra chỉ ghi log, không gọi API thật (xem [01](01-cai-dat-va-ha-tang.md) mục 9). Quên tắt ở production = khách không nhận trả lời. |
| Giây chờ gộp tin liên tiếp | `debounce_seconds` | 5 | 5 | Khách nhắn nhiều tin liền nhau trong khoảng này được gộp thành một lượt xử lý (và một lần bot trả lời). Tăng = gộp tốt hơn nhưng Lead xuất hiện chậm hơn và mỗi lượt giữ worker lâu hơn (công suất ≈ số worker / (debounce + 1) khách/giây). Không được âm. Cuộc gọi dùng chung giá trị này. |

## 2. Xử lý và thử lại

| Trường | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Số lần thử tối đa | `max_attempts` | 3 | 3 | Một tin xử lý lỗi quá số lần này thì chuyển trạng thái `Manual` (người phải bấm "Xử lý lại" ở màn hình giám sát). Phải ≥ 1. |
| Chờ giữa các lần thử (phút) | `retry_backoff_minutes` | `1,5` | `1,5` | Danh sách số nguyên dương cách nhau dấu phẩy; phần tử thứ n là thời gian chờ sau lần lỗi thứ n. Nên có (số lần thử tối đa − 1) phần tử. Sai định dạng thì không lưu được. |
| Tin kẹt ở Processing quá (phút) | `stuck_after_minutes` | 10 | 10 | Tin ở `Processing` quá số phút này (worker chết giữa chừng) được tác vụ quét chuyển về `Retry`. Phải ≥ 1. (Đặc tả `docs/11` nhắc 15 phút; mã đọc giá trị trường này, mặc định 10.) |

## 3. Thời hạn lưu

| Trường | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Giữ payload gốc (ngày) | `raw_payload_retention_days` | 180 | Theo Q7 (**chưa chốt**; 180 là mặc định tạm) | Tác vụ đêm 02:00 xóa `raw_payload` (dữ liệu thô nhận từ kênh) của tin đã xử lý xong, quá số ngày này. **Không đặt 0**: đặc tả nói 0 là tắt dọn, nhưng form hiện **từ chối lưu** giá trị nhỏ hơn 1 ("phải lớn hơn 0"). Muốn gần như không xóa thì đặt số lớn (ví dụ 3650). |
| Giữ ghi âm (ngày) | `recording_retention_days` | 365 | Theo Q7 (chưa chốt) | Chưa có tác dụng: tính năng ghi âm chưa được xây (mục 10). Không được âm. |

## 4. Ánh xạ nguồn Lead, giờ làm việc, ngày lễ

| Trường | Tên kỹ thuật | Mặc định | Ý nghĩa và hậu quả |
| --- | --- | --- | --- |
| Ánh xạ kênh sang nguồn Lead | `default_lead_source_map` | `{"Email":"Email","Zalo OA":"Zalo OA","Telephony":"Điện thoại","Meta Lead Ads":"Quảng cáo Meta","Google Lead Form":"Quảng cáo Google","Web Form":"Website"}` | Khi tạo Lead, nguồn (CRM Lead Source) lấy theo loại kênh (từ v2 có thêm ba kênh quảng cáo). Nguồn chưa có thì được tạo. JSON sai thì không lưu được. Đổi tên nguồn nếu CRM của bạn đã có tên nguồn khác. |
| Giờ làm việc | `business_hours` | `{"mon-fri":"08:00-17:30","sat":"08:00-12:00"}` | Dùng để tính hạn gọi lại khách gọi nhỡ, cảnh báo "kênh không nhận tin" (chỉ trong giờ làm việc), câu giữ chân trong/ngoài giờ và (nếu tự gửi) giờ bot hoạt động. |
| Ngày nghỉ lễ | `holidays` | `[]` | Danh sách ngày nghỉ. **Mặc định rỗng nên ngày lễ vẫn tính là ngày làm việc**: người phụ trách phải điền mỗi năm. |

**Định dạng Giờ làm việc** (đọc từ `omni_crm/utils/business_hours.py`):

- Là một đối tượng JSON, khóa là ngày hoặc khoảng ngày, giá trị là **một** khoảng giờ `"HH:MM-HH:MM"`.
- Khóa hợp lệ: `mon, tue, wed, thu, fri, sat, sun` (một ngày) hoặc `ngày-ngày` như `mon-fri` (khoảng, theo thứ tự trong tuần).
- Mỗi ngày chỉ một khoảng giờ (không hỗ trợ nghỉ trưa chia hai ca). Ngày không có khóa = không làm việc (Chủ nhật nếu bạn không ghi `sun`).
- Giờ đóng phải sau giờ mở; `24:00` được chấp nhận làm giờ đóng. Dòng sai bị bỏ qua; nếu **không** còn dòng nào hợp lệ, hệ thống lặng lẽ dùng giá trị mặc định ở trên (cẩn thận gõ nhầm).

Ví dụ làm việc cả thứ Bảy, nghỉ Chủ nhật: `{"mon-fri":"08:00-17:30","sat":"08:00-12:00"}`.

**Định dạng Ngày nghỉ lễ**: `["2027-01-01", "2027-02-05", "2027-02-06"]`, mỗi ngày dạng `YYYY-MM-DD`. Phần tử sai định dạng bị bỏ qua. Lưu ý: ô này **không được kiểm tra khi lưu** (khác hai ô JSON còn lại), nên JSON hỏng được lưu và hệ thống coi như không có ngày lễ nào. Sau khi điền hãy mở lại xem có lưu đúng không.

## 5. Cảnh báo và gán Lead

| Trường | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Email nhận cảnh báo | `alert_recipients` | trống | **Điền** ít nhất 2 địa chỉ (quản trị + dự phòng) | Mỗi dòng một địa chỉ. Trống = không có thư cảnh báo (Notification Log cho Omni Admin vẫn tạo). Chi tiết các loại cảnh báo: [09](09-van-hanh-hang-ngay.md). |
| Người nhận Lead dự phòng | `assignment_fallback_users` | trống | Điền 1-2 người trực | Mỗi dòng một user. **Theo mã hiện tại chỉ dùng để chọn người nhận thông báo** (Lead nóng, bot chuyển người) khi Lead chưa có chủ và chưa có `callback_fallback_user`. Nó **không** tự gán Lead cho ai (nhãn và mô tả trên form dễ gây hiểu nhầm). |

**Gán Lead cho sale** là việc của **Assignment Rule của Frappe CRM**, không phải của `omni_crm` (docs/12 mục C, G11: rule áp dụng được cho Lead do worker tạo). Quy tắc gán (theo sản phẩm, khu vực, vòng tròn) là quyết định Q4, **chưa chốt**. Việc cần làm: người quản trị tạo Assignment Rule cho CRM Lead ở Desk (`/app/assignment-rule`; chưa kiểm trên giao diện). Chưa có rule thì Lead mới không có người phụ trách, và các việc/thông báo cần "người phụ trách" sẽ rơi sang người dự phòng.

## 6. Tổng đài

| Trường | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Hạn gọi lại khách gọi nhỡ (phút) | `missed_call_callback_minutes` | 30 | 30 (hoặc theo cam kết dịch vụ) | Hạn của việc gọi lại, tính **trong giờ làm việc** (có xét ngày lễ). Gọi nhỡ lúc 17:20 thì hạn rơi vào sáng hôm sau. |
| Người nhận việc gọi lại dự phòng | `callback_fallback_user` | trống | **Điền** (trưởng sale hoặc người trực) | Dùng khi Lead chưa có chủ. Không điền và Lead chưa có chủ thì việc gọi lại **không có người nhận** (vẫn đếm quá hạn và cảnh báo Omni Admin). |

Xem thêm [05](05-kenh-tong-dai-stringee.md).

## 7. Bot AI và ngân sách token

| Trường | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Bật bot phân tích | `bot_analyze_enabled` | Tắt | Tắt cho tới khi qua cổng `phan_tich` ([07](07-bot-ai.md)) | Bật = sau mỗi lượt có tin khách, AI trích thông tin (tên, nhu cầu, SĐT, ý định...) vào Lead. Tắt = không gọi AI phân tích. |
| Chế độ bot trả lời | `bot_reply_mode` | Off | Off → Shadow → Draft theo thứ tự ở [07](07-bot-ai.md) | **Off**: không soạn trả lời. **Shadow**: soạn ngầm để đo, sale không thấy. **Draft**: nháp hiện trong Hộp thư cho sale Gửi/Sửa/Bỏ; bot không tự gửi. Chỉ có ba giá trị này: **không có giá trị "Auto" ở đây** (tự gửi bật theo từng nhóm câu hỏi, mục 9). |
| Khóa Claude API | `ai_api_key` | trống | Nhập một lần trên máy chủ | Kiểu Password (không hiện lại). Vẫn cần cổng Q11 mới gọi AI với dữ liệu khách thật. |
| Hạn mức token mỗi ngày | `daily_token_budget` | 2.000.000 (mặc định của DocType) | Đặt theo ngân sách; thấp khi thử | **Hạn mức mềm** cho cả ngày. Hết thì bot dừng đến hôm sau, ghi lần gọi `Skipped Budget` và gửi cảnh báo. Đặt **0 = dừng hẳn mọi AI**. Do nhiều worker chạy song song, thực tế có thể vượt nhẹ (cỡ số worker × token tối đa một lần gọi). Đặc tả `docs/09` nói site đã có Omni Settings từ trước sẽ migrate ra 0; site mới lấy 2.000.000. **Hãy mở xem giá trị thực tế sau khi migrate.** |
| Mô hình phân tích | `analyze_model` | `claude-haiku-5-5` | Giữ mặc định | Tên mô hình, không viết cứng trong mã. Đổi mô hình = phải chạy lại bộ đề và qua cổng `doi_cau_lenh`. |
| Mô hình soạn trả lời | `reply_model` | `claude-sonnet-5-5` | Giữ mặc định | Như trên. |
| Ghim câu lệnh phân tích, câu lệnh trả lời | `analyze_prompt_version`, `reply_prompt_version` | trống (= bản mặc định trong mã, hiện là `v1`) | Giữ trống hoặc `v1` | **Sửa được.** Bot thật chỉ chạy đúng phiên bản được ghim; tệp câu lệnh `v2` đã có trong mã nhưng **không có hiệu lực cho tới khi bạn ghim**. Ghim `v2` chỉ sau khi cổng `doi_cau_lenh` đo bằng AI thật ([07](07-bot-ai.md) mục 5). Quay lui = đặt về `v1`. |
| Phiên bản câu lệnh tóm tắt, phiên bản kho tri thức | `summarize_prompt_version`, `kb_version` | (tự điền) | — | **Chỉ đọc**, hệ thống ghi mỗi lần migrate hoặc khi kho tri thức đổi. Dùng để biết một nháp sinh bằng phiên bản nào. |

Quy tắc ngân sách: ngày tính theo giờ site; bộ đếm token nằm ở Redis và tự dựng lại từ tổng `Omni Bot Run` nếu mất. Cảnh báo khi dùng trên 80% hạn mức, khi lỗi AI quá 5% (từ 20 lần gọi), khi hết hạn mức và khi ngắt mạch mở (xem [09](09-van-hanh-hang-ngay.md)).

## 8. Chấm điểm Lead

| Trường | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Bật chấm điểm Lead | `lead_scoring_enabled` | Tắt | Bật sau khi trưởng sale duyệt bảng điểm (Q5, **chưa chốt**) | Bật = tính điểm bằng **luật cố định** (không do AI quyết) từ trường Lead và kết quả phân tích; vượt ngưỡng thì báo người phụ trách một lần. Tắt = không tính điểm mới, điểm cũ giữ nguyên. Bảng điểm rỗng cũng coi như không chấm. |
| Ngưỡng Lead nóng | `hot_lead_threshold` | 60 | 60 hoặc theo quyết định của trưởng sale | Từ ngưỡng này là Lead nóng, báo một lần ("Lead nóng: <tên>, điểm <n>"). Phải ≥ 1 (form từ chối 0 hay số âm, dù đặc tả nói "≤ 0 dùng 60"). |
| Bảng điểm | `score_rules` | trống | Nhập từ CSV đã duyệt | Bảng con, mỗi dòng: **Tín hiệu** (chọn trong danh sách cố định), **Điểm** (số nguyên, có thể âm), **Ghi chú**. Mỗi tín hiệu chỉ được một dòng. |

Danh sách tín hiệu hợp lệ (chọn trong danh sách, không gõ tự do):

- **Dùng cho khách lẻ (bảng cũ, bản v1):** `has_phone` (có SĐT), `has_email`, `has_org` (nêu tên đơn vị), `intent_price` (hỏi giá/báo giá), `intent_purchase` (muốn mua), `quantity_given` (nêu số lượng), `timeframe_within_30d` (dự kiến mua trong 30 ngày), `urgency_high` (mức gấp cao), `repeat_contact` (liên hệ từ 2 lần trong 30 ngày), `complaint` (khiếu nại; thường trừ điểm).
- **Dùng cho nhà thầu (bảng B2B, từ v2):** `known_org` (đã gắn được nhà thầu), `replied` (nhà thầu phản hồi lần đầu), `needs_discussed` (đã trao đổi nhu cầu), `intent_service_info` (hỏi cách vận hành hoặc gói dịch vụ), `intent_fee` (hỏi phí), `intent_proposal` (yêu cầu đề xuất), `decision_maker_engaged` (người quyết định tham gia), `pain_point_stated` (nêu vướng mắc), cộng với `has_phone`, `has_email`, `repeat_contact`, `complaint` ở trên.

Bản v1 của bot còn trả các nhãn ý định cũ; hệ thống tự đổi sang ý định mới trước khi lấy tín hiệu ([07](07-bot-ai.md) mục 3A). Bảng điểm B2B mẫu là `mau/bang-diem-lead-b2b-mau.csv` (nhập có `merge`, xem dưới); con số trong mẫu và ngưỡng 60 **chưa được trưởng sale duyệt**.

Nhập bảng điểm từ CSV (mẫu `mau/bang-diem-lead-mau.csv`, cột `signal,points,note`). Chạy **trong `<bench-dir>`**, dùng **đường dẫn tuyệt đối** (lệnh này đọc đường dẫn đúng như bạn gõ, không tính từ thư mục app):

```bash
bench --site <site> execute omni_crm.bot.scoring.import_rules --kwargs "{'path': '/duong/dan/tuyet/doi/bang-diem-da-duyet.csv'}"
```

- Sai tín hiệu/điểm hoặc trùng dòng: lệnh báo mọi lỗi và **không đổi gì**.
- Lệnh **thay toàn bộ** bảng điểm cũ.
- Nếu tên tệp có chữ "mau" hoặc nội dung trùng bản mẫu của repo, lệnh vẫn nhập nhưng in cảnh báo "bảng điểm MẪU, chưa được Trưởng sale duyệt". Con số trong bản mẫu chỉ là đề xuất, không phải quyết định.
- Điểm cũng nhập tay được ngay trên form Omni Settings (bảng `score_rules`).
- **Bảng B2B không thay bảng cũ mà ghép vào**: thêm `'merge': True` để cập nhật dòng cùng tín hiệu, thêm dòng mới và **tắt** (cột "Đang dùng" bỏ tích, không xóa) dòng không còn trong tệp; chạy lại không đổi thêm gì:

```bash
bench --site <site> execute omni_crm.bot.scoring.import_rules --kwargs "{'path': '/duong/dan/tuyet/doi/bang-diem-b2b-da-duyet.csv', 'merge': True}"
```
- Lead đang mở được chấm lại bằng một lệnh riêng, mặc định chỉ chạy thử (dry run) và không báo "Lead nóng": `bench --site <site> execute omni_crm.patches.v2.rescore_open_leads.execute`; sau khi xem kết quả mới thêm `--kwargs "{'dry_run': False}"`.

## 9. Chuyển người, nháp và tự gửi

| Trường | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Bot im sau khi chuyển người (giờ) | `handoff_pause_hours` | 24 | 24 | Sau khi bot chuyển người, nó không soạn nháp cho khách đó trong khoảng này; sale bấm "Cho bot tiếp tục" để mở sớm. Đặt 0 = vẫn chuyển người nhưng bot không im. Không được âm. |
| Kênh báo sale khi chuyển người | `handoff_notify_channels` | `system` | `system` (**Q8 chưa chốt**); thêm `email` nếu sale hay không mở hệ thống | Giá trị hợp lệ: `system`, `email`, hoặc `system,email`. `system` = Notification Log + realtime. `email` = thêm thư nhắc nếu sau 10 phút thông báo hệ thống chưa được mở (chỉ khi `sandbox_mode` tắt). Giá trị khác không lưu được. |
| Giờ bot hoạt động (JSON) | `bot_active_hours` | trống | Để trống (**Q6 chưa chốt**) | Cùng định dạng Giờ làm việc. Trống = theo Giờ làm việc. **Chỉ chế độ tự gửi dùng**; chế độ Draft không bị chặn bởi ô này. |
| Câu giữ chân trong giờ làm việc | `handoff_message_in_hours` | "Cảm ơn anh/chị đã liên hệ Miyano. Nhân viên sẽ phản hồi anh/chị sớm nhất có thể." | Soát lại câu chữ | **Chỉ gửi cho khách khi một nhóm được bật tự gửi**; chế độ Nháp không gửi câu này. |
| Câu giữ chân ngoài giờ làm việc | `handoff_message_after_hours` | "Cảm ơn anh/chị đã liên hệ Miyano. Nhân viên sẽ phản hồi anh/chị vào {gio_lam_viec_ke_tiep}." | Giữ mã `{gio_lam_viec_ke_tiep}` | Mã đó được thay bằng thời điểm mở cửa kế tiếp. Đừng hứa giờ hay giá khác trong câu này. |

**Tự gửi (Auto) không có ô trong Omni Settings.** Hai lớp riêng, đều tắt mặc định:

1. Khóa `omni_auto_send_allowed` trong site_config ([01](01-cai-dat-va-ha-tang.md) mục 10).
2. Nút **Bật tự động gửi** trên từng form **Nhóm câu hỏi** ([07](07-bot-ai.md)).

## 9A. Quyết định phát hành (Q1, Q11) và cổng bot

*Chưa kiểm trên giao diện cho các mục 9A tới 9E, 11A, 11B:* tên trường và nhãn lấy từ định nghĩa DocType trong mã, chưa mở form bằng trình duyệt.

Nhóm **Quyết định phát hành** (V21-08). Bốn trường này **chỉ ghi nhận**: bật hay tắt chúng không đổi hành vi nào của hệ thống. Chúng để lệnh kiểm trước phát hành ([10](10-quy-trinh-len-san-xuat.md) mục 4, mục 11B bên dưới) đọc được quyết định của chủ dự án thay vì đoán từ tài liệu. Chỉ Omni Admin ghi; mọi thay đổi có lịch sử phiên bản.

| Trường | Tên kỹ thuật | Mặc định | Ý nghĩa |
| --- | --- | --- | --- |
| Q1: cho phép bot tự gửi | `q1_allowed` | Tắt | Tích **chỉ khi chủ dự án đã chốt Q1** bằng văn bản. Lệnh kiểm chấp nhận có `omni_auto_send_allowed` trong site_config **chỉ khi** ô này được tích. Không tích mà đặt khóa kia = lệnh kiểm báo KHÔNG ĐẠT. |
| Q11: cho phép gửi hội thoại khách sang AI | `q11_allowed` | Tắt | Phải **khớp** khóa `omni_ai_allow_customer_data` trong site_config (cả hai cùng bật hoặc cùng tắt). |
| Cổng phân tích đã đạt lúc | `gate_phan_tich_passed_at` | trống | Điền thời điểm sau khi cổng `phan_tich` **được đo bằng AI thật** và đạt ([07](07-bot-ai.md) mục 5). Trống = chưa đạt; ở production mà `bot_analyze_enabled = 1` thì lệnh kiểm báo KHÔNG ĐẠT. |
| Cổng nháp đã đạt lúc | `gate_nhap_passed_at` | trống | Như trên cho cổng `nhap`; ở production mà `bot_reply_mode` khác Off thì cần ô này. |

Lưu ý trung thực: hệ thống **không kiểm chứng** rằng bạn khai đúng. Điền `gate_*` khi chưa đo thật là tự lừa mình. Hiện chưa cổng bot nào được đo bằng AI thật, nên cả hai ô phải **để trống** và `bot_analyze_enabled = 0`, `bot_reply_mode = Off` ở bản phát hành.

## 9B. Nhà thầu: điểm phù hợp và nhập danh sách

Dùng cho mảng khách là nhà thầu ([11](11-nha-thau-va-nhap-danh-sach.md)). Điểm phù hợp tính bằng công thức cố định, không do AI.

| Trường | Tên kỹ thuật | Mặc định | Ý nghĩa |
| --- | --- | --- | --- |
| Bật nhập danh sách nhà thầu | `prospect_import_enabled` | Bật | Tắt = không chạy được **Chạy thử** lẫn **Nhập thật**; việc đã xếp hàng thành `Refused`. Không xóa dữ liệu đã nhập. |
| Bật tính điểm phù hợp | `fit_scoring_enabled` | Bật | Tắt = giữ nguyên điểm cũ, không tính lại (kể cả lúc 02:20 hằng đêm). |
| Khoảng thời gian xét gói thầu (tháng) | `fit_period_months` | 24 | Chỉ tính gói thầu có ngày phê duyệt trong khoảng này. |
| Số bệnh viện tối thiểu / số mã hàng tối thiểu | `fit_min_hospitals`, `fit_min_items` | 5 / 200 | **Tiêu chí bắt buộc**: nhà thầu phải đạt cả hai mới được hạng A hoặc B. |
| Số bệnh viện / số mã hàng / giá trị đạt điểm tối đa | `fit_max_hospitals`, `fit_max_items`, `fit_max_value_bn` | 10 / 500 / 20 (tỷ đồng) | Đạt hay vượt mốc này thì thành phần đó được điểm tối đa. |
| Trọng số | `fit_w_hospitals`, `fit_w_items`, `fit_w_value`, `fit_w_active` | 40 / 30 / 20 / 10 | Bốn thành phần: số bệnh viện, số mã hàng, tổng giá trị, còn hợp đồng đang thực hiện. Tổng 100. |
| Ngưỡng hạng A | `fit_tier_a` | 70 | Đạt tiêu chí và điểm từ ngưỡng này = A; đạt tiêu chí nhưng thấp hơn = B; chưa đạt tiêu chí = C. |
| Loại hàng được tính điểm | `fit_categories` | VTYT tiêu hao, Hóa chất – sinh phẩm (mỗi dòng một loại) | Gói thầu loại khác không được tính. |

Đổi các tham số `fit_*` không tự tính lại ngay: điểm cập nhật khi dữ liệu nhà thầu đổi và trong lần tính lại hằng đêm. Trường chưa từng lưu dùng giá trị mặc định ở bảng; lần lưu Omni Settings đầu tiên điền các mặc định này.

## 9C. Chăm sóc khách và Tổng quan

Xem hướng dẫn dùng ở [13](13-dashboard-va-cham-soc.md).

| Trường | Tên kỹ thuật | Mặc định | Ý nghĩa |
| --- | --- | --- | --- |
| Bật Dashboard CSKH | `dashboard_enabled` | Bật | Tắt = trang **Tổng quan CSKH** báo "Dashboard CSKH đang tắt" và không nhận ghi nhật ký từ trang đó; Hộp thư và dữ liệu không đổi. |
| Tự ghi nhật ký chăm sóc | `care_log_enabled` | Bật | Tắt = hệ thống không tự tạo hoặc kéo dài phiên chăm sóc, không có thông báo 17:45; sale vẫn ghi tay được nếu `dashboard_enabled` còn bật. |
| Ngưỡng quá hạn phản hồi (phút làm việc) | `response_sla_minutes` | 15 | Lead quảng cáo chưa có phản hồi đầu tiên sau chừng này **phút làm việc** thì bị nhắc người phụ trách; quá 2 lần ngưỡng thì báo trưởng nhóm. Cũng là ngưỡng nhãn "Quá hạn phản hồi" ở Tổng quan. Nhắc quá hạn chỉ chạy khi `dashboard_enabled` bật. |
| Khoảng im lặng kết thúc một phiên (giờ) | `care_session_gap_hours` | 4 | Khách im lặng quá khoảng này thì phiên chăm sóc kết thúc; tin kế tiếp mở phiên mới. |
| Vai trò trưởng nhóm | `team_lead_role` | Sales Manager | Người có vai trò này thấy toàn đội ở Tổng quan và nhận báo quá hạn. |

Giá trị chưa lưu hoặc nhỏ hơn hay bằng 0 (với số) thì hệ thống dùng mặc định ở bảng.

## 9D. Lead quảng cáo và Web Form

Xem [12](12-lead-quang-cao.md) và [gan-form-website.md](gan-form-website.md).

| Trường | Tên kỹ thuật | Mặc định | Ý nghĩa |
| --- | --- | --- | --- |
| Cảnh báo im lặng: số giờ | `ads_silence_hours` | 6 | Có chiến dịch `Đang chạy` và kênh quảng cáo bật mà **không có lead thật nào** quá chừng này **giờ làm việc** thì có cảnh báo "Không có lead quảng cáo". Muốn tắt cảnh báo: chuyển hết chiến dịch khỏi `Đang chạy` hoặc tắt kênh. |
| Web Form: số lần gửi tối đa mỗi IP; cửa sổ (phút) | `webform_rate_limit`, `webform_rate_window_minutes` | 5 lần / 10 phút | Quá thì form trả lỗi 429 cho IP đó. Muốn giới hạn đúng, nginx phải ghi đè `X-Forwarded-For` ([01](01-cai-dat-va-ha-tang.md) mục 6). |
| Web Form: trần toàn kênh mỗi giờ | `webform_global_rate_per_hour` | 300 | Vượt thì 429 cho **mọi** IP và có cảnh báo "Web Form chạm trần toàn kênh". |
| Web Form: nhận biểu mẫu chưa cấu hình | `webform_accept_unknown_form` | Tắt | Tắt = form có mã chưa khai báo `Omni Lead Form` bị bỏ qua (trang vẫn nhận "thành công" giả). Bật = nhận, ghi chú "chưa cấu hình", **không** tự giao sale. |

Thông số trên Omni Channel loại Web Form: `webform_captcha_provider` (để trống = không kiểm chống bot; chọn Turnstile hoặc reCAPTCHA thì token sai hoặc nhà cung cấp lỗi sẽ bị từ chối) và `webform_captcha_secret` (kiểu mật khẩu).

## 9E. Meta, kéo lại lead và hàng dự phòng webhook

Sáu ô dưới đây gồm số và ô tích "Tắt..."; chưa từng lưu thì dùng mặc định. Chi tiết dùng ở [12](12-lead-quang-cao.md) mục 6 và [09](09-van-hanh-hang-ngay.md).

| Trường | Tên kỹ thuật | Mặc định | Ý nghĩa |
| --- | --- | --- | --- |
| Token Meta: cảnh báo trước (ngày) | `meta_token_warn_days` | 7 | Cảnh báo khi token Page hoặc quyền dữ liệu còn không quá chừng này ngày. Giá trị trống hoặc ≤ 0 = 7. |
| Tắt kiểm token Meta hằng ngày | `meta_token_check_disabled` | Không tích (= **đang kiểm**) | Tích = ngừng gọi Meta kiểm token; kết quả cũ trên kênh giữ nguyên. |
| Kéo lại lead Meta: trong N giờ | `meta_requeue_hours` | 72 | Chỉ kéo lại tin Meta lỗi tạm thời tạo trong chừng này giờ qua. |
| Kéo lại lead Meta: tối đa mỗi lần | `meta_requeue_batch` | 200 | Số tin tối đa mỗi lượt đối soát (mỗi kênh). Mỗi tin kéo lại tối đa 3 lần. |
| Tắt tự kéo lại lead Meta | `meta_requeue_disabled` | Không tích (= **đang kéo lại**) | Tích = đối soát hằng giờ vẫn bù lead sót, nhưng không kéo lại tin lỗi và không gửi cảnh báo gộp. |
| Tắt hàng dự phòng webhook | `webhook_spool_disabled` | Không tích (= **đang dùng hàng dự phòng**) | Tích = khi DB không ghi được, webhook trả lỗi 503 như trước V21-04 (nhà cung cấp phải tự gửi lại). Tác vụ quét vẫn chèn lại tin đã nằm trong hàng. |
| Tắt tab Zalo trong trang Lead/Deal | `zalo_tab_disabled` | Không tích (= **tab Zalo đang hiện**, nếu patch giao diện đã được áp trên bench) | Tích = ẩn tab Zalo trong giao diện CRM; Hộp thư và dòng thời gian không đổi. Có hiệu lực sau khi tải lại trang CRM. |

Cẩn thận chiều ngược: các ô `..._disabled` **bật** là **tắt** tính năng. Bốn ô `..._disabled` mặc định không tích, tức tính năng đang chạy.

## 10. Ghi âm và chuyển giọng nói

**Chưa có công tắc nào trên form.** Mã đọc một công tắc tên `recording_enabled` (`omni_crm/flags.py`) nhưng trường này **không tồn tại** trong Omni Settings; cũng chưa có trường chọn nhà cung cấp chuyển giọng nói. Nguyên nhân: nhiệm vụ D3-04 (ghi âm và tóm tắt cuộc gọi) đã bị bỏ qua vì cổng thử nghiệm chuyển giọng nói chưa đo (xem `docs/nghiem-thu/dot-3.md` và `docs/nghiem-thu/stt-thu-nghiem.md`). Không có adapter chuyển giọng nói thật nào; chỉ có nhà cung cấp `Fake` dùng cho thử nghiệm trên dev.

Hệ quả: **không có ghi âm tự tải, không có tóm tắt cuộc gọi**. Trường `recording_retention_days` (mục 3) chưa có tác dụng. Đừng hứa tính năng này với người dùng. Chủ dự án cần chọn nhà cung cấp (Q13) và ngưỡng (Q14) trước khi làm tiếp.

## 11. Bảng công tắc → hiệu ứng

Tắt một phần = phần đó ngừng hành động; tin vào vẫn được lưu để xử lý bù khi bật lại. Gom từ `docs/09` và đối chiếu với mã.

| Công tắc (ở đâu) | Giá trị | Hiệu ứng |
| --- | --- | --- |
| `sandbox_mode` (Omni Settings) | Bật | Không gọi API ngoài khi gửi: không gửi Zalo, không làm mới token, không OAuth, không gọi Stringee, không xếp email nhắc chuyển người, bỏ qua đối soát đêm. Không chặn lời gọi AI. |
| `enabled` (Omni Channel) | Tắt | Webhook vẫn trả 200 và **lưu** tin, không xử lý. Tổng đài: tra cứu trả "khách mới", đối soát và việc gọi lại dừng (bật lại thì tác vụ quét tạo bù). |
| `outbound_enabled` (Omni Channel) | Tắt | Tin ra thành `Blocked` ("Kênh đã tắt chiều ra"). Bật lại **không** tự gửi lại tin đã `Blocked`: sale gửi lại tay. |
| `bot_analyze_enabled` | Tắt | Không gọi AI phân tích. Bot soạn trả lời vẫn chạy được nhưng không có phân tích (nháp ghi cờ `info:khong_co_phan_tich`). |
| `lead_scoring_enabled` | Tắt | Không tính điểm mới, không báo Lead nóng. Điểm cũ giữ nguyên. |
| `bot_reply_mode` | Off | Không soạn trả lời; phân tích vẫn chạy nếu bật. |
| `bot_reply_mode` | Shadow | Ghi nháp ngầm để đo; sale không thấy; không im bot, không báo ai. |
| `bot_reply_mode` | Draft | Sale thấy nháp trong Hộp thư và duyệt; có chuyển người, thông báo, tạm dừng bot. Bot không tự gửi. Chuyển Draft → Shadow/Off: nháp đang chờ hết hạn sau 24 giờ hoặc khi khách nhắn tiếp. |
| `daily_token_budget` | 0 | Mọi lời gọi AI dừng (ghi `Skipped Budget`). |
| `omni_auto_send_allowed` (site_config) | vắng/0 | Không nhóm nào tự gửi được; tin Bot đang chờ bị `Blocked`. |
| `auto_send_enabled` (Nhóm câu hỏi) | Tắt | Nhóm đó rơi về Draft. Tự tắt khi ngắt mạch. |
| `handoff_pause_hours` | 0 | Bot không im sau khi chuyển người. |
| `alert_recipients` | trống | Không có thư cảnh báo; Notification Log vẫn tạo. |
| `raw_payload_retention_days` | (tối thiểu 1) | Dọn payload thô sau số ngày này. Không thể đặt 0 trên form. |
| Ngắt mạch AI (tự động, bộ nhớ đệm `omni:ai:breaker_open`) | 5 lần lỗi liên tiếp | 5 phút mọi lời gọi AI ghi `Skipped Switch`, kênh/Lead/chấm điểm không bị ảnh hưởng. |

Công tắc cho từng dòng dữ liệu (không phải trường của Omni Settings):

| Công tắc | Ở đâu | Hiệu ứng |
| --- | --- | --- |
| `active`, `shadow_only` | **Nhóm câu hỏi** (`Omni Question Group`) | `active = 0`: nhóm ngừng, bot không thấy nhóm này. `shadow_only = 1`: nháp trả lời của nhóm chỉ ghi ngầm (Shadow), sale không thấy. Bỏ cả hai về mặc định (1 / 0) thì như v1. |
| `active` | Dòng **bảng điểm** (`score_rules` trong Omni Settings) | `0` = quy tắc tắt, không xóa. Tắt cả bảng bằng `lead_scoring_enabled = 0`. |
| `bot_paused_until` | Khách (`Omni Identity`), sale bấm **Tạm dừng bot với khách này** ([08](08-huong-dan-nhan-vien-sale.md) mục 7) | Bot ngầm không soạn nháp cho khách đó đến thời điểm này. |
| `enabled` | **Biểu mẫu** (`Omni Lead Form`) | Tắt = tin vẫn được lưu nhưng **không tạo Lead**; bật lại **không** tự tạo Lead cho tin đã nhận lúc tắt ([12](12-lead-quang-cao.md) mục 3). |

## 11A. Cờ site_config và trường của kênh

Cờ **site_config** nằm ở `sites/<site>/site_config.json` (không phải `common_site_config.json`), sửa bằng `bench --site <site> set-config <khóa> <giá trị>` chạy trong `<bench-dir>` rồi `bench restart`. Chi tiết và cảnh báo từng khóa ở [01](01-cai-dat-va-ha-tang.md) mục 10.

| Khóa | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `omni_auto_send_allowed` | vắng = chặn | Khóa hạ tầng cho bot **tự gửi**. Chỉ người vận hành đặt, sau khi chủ dự án chốt Q1 (và `q1_allowed` đã tích). Không đặt trên dev/staging. |
| `omni_ai_allow_customer_data` | vắng = chặn | Cho gửi hội thoại khách thật sang AI. Chỉ sau khi chốt Q11 (và `q11_allowed` khớp). |
| `omni_ai_timeout_seconds` | 60 | Thời gian chờ tối đa mỗi lần gọi AI. |
| `omni_eval_judge_model` | `claude-sonnet-5-5` | Mô hình "giám khảo" chấm bộ đề; chỉ dùng khi chạy bộ đề tay. |
| `omni_webhook_lock_wait_seconds` | 3 | Giây webhook chờ khóa DB **trước khi** chuyển tin sang hàng dự phòng (từ V21-04; trước đây là chờ rồi trả 503). Giá trị cũ vẫn dùng được. |
| `omni_spool_max_bytes`, `omni_spool_max_lines`, `omni_spool_expire_days` | 50 MB, 5.000 dòng, 14 ngày | Giới hạn thư mục hàng dự phòng `private/omni_spool` và số ngày giữ tin chưa chèn lại được (tin chứa dữ liệu cá nhân nên không giữ vô hạn). Đầy thì cảnh báo và webhook trả 503. |
| `omni_zalo_signature_debug_until` | vắng = tắt | Mốc thời gian (ví dụ `2026-11-03T15:30:00`) bật **công cụ chẩn đoán chữ ký Zalo**, tối đa 2 giờ kể từ lần đầu hệ thống thấy giá trị. Chỉ dùng trên staging, xem [04](04-kenh-zalo-oa.md) mục 3A. Gỡ: `bench --site <site> set-config omni_zalo_signature_debug_until ""`. |
| `developer_mode` | tắt | **Không bao giờ bật ở production**: mở đường tắt bỏ qua chữ ký. |

Trường của **kênh** (`Omni Channel`, `/app/omni-channel`) cần biết thêm:

| Trường | Kênh | Ý nghĩa |
| --- | --- | --- |
| `zalo_skip_signature` | Zalo OA | "Bỏ qua kiểm tra chữ ký (chỉ site dev)". Để tắt. Chỉ có tác dụng khi `developer_mode` bật; staging và production từ chối và cảnh báo. |
| `zalo_oa_package`, `zalo_cs_paid_quota_month` | Zalo OA | Gói OA (mặc định `Chưa xác nhận`) và hạn mức tin tư vấn ngoài 48 giờ mỗi tháng (trống hoặc 0 = chưa biết). **Chỉ để báo** ở Hộp thư và `/app/omni-monitor`, không chặn gửi, không chặn kênh. Xem [04](04-kenh-zalo-oa.md) mục 4A. |
| `meta_page_id`, `meta_app_id`, `meta_app_secret`, `meta_verify_token`, `meta_page_access_token` | Meta Lead Ads | Thông số kênh Meta; ba ô cuối là mật khẩu. Cần `meta_app_id` + `meta_app_secret` + token Page thì hệ thống mới tự kiểm token ([12](12-lead-quang-cao.md) mục 6). |
| `meta_token_checked_at`, `meta_token_valid`, `meta_token_never_expires`, `meta_token_expires_at`, `meta_token_data_expires_at`, `meta_token_note` | Meta Lead Ads | **Chỉ đọc**: kết quả lần kiểm token gần nhất. Ghi chú "đủ quyền" nghĩa là ổn; ghi chú khác nghĩa là có vấn đề cần xử lý. |
| `webform_captcha_provider`, `webform_captcha_secret` | Web Form | Xem 9D. |
| `last_error` | Mọi kênh (chỉ đọc); cũng là cột "Lỗi gần nhất" của tin vào | Lỗi gần nhất của kênh hoặc của một tin; đã che bí mật. Xem [09](09-van-hanh-hang-ngay.md) mục 3 |

Trường ở DocType khác mà `docs/09` nhắc: `omni_score` (điểm Lead, chỉ đọc, trên Lead của CRM; còn nguyên khi tắt `lead_scoring_enabled`).

## 11B. Lệnh kiểm trước phát hành: các mục nó kiểm

Một lệnh chỉ đọc, chạy trong `<bench-dir>` ([10](10-quy-trinh-len-san-xuat.md) mục 4 và [truoc-ngay-chay-chien-dich.md](truoc-ngay-chay-chien-dich.md)):

```bash
bench --site <site> execute omni_crm.ops.preflight.run --kwargs "{'target': 'production'}"
```

`target` là `staging` hoặc `production`. Lệnh in bảng mục / kết quả / chi tiết / cách sửa. Kết quả là `DAT` (đạt), `KHONG_DAT` (phải sửa; có một mục như vậy thì lệnh thoát mã 1) hoặc `CAN_NGUOI_KIEM` (máy không quyết được, bạn đọc và tự xác nhận). Lệnh **không** sửa gì và **không** gọi Meta, Google, Zalo hay nginx thật. Cột đầu là tên mục trong bảng kết quả, không phải trường để điền.

| Tên mục trong bảng | Nó kiểm | Sửa ở đâu |
| --- | --- | --- |
| `developer_mode` | tắt | site_config (11A) |
| `zalo_skip_signature` | không kênh nào bật ô này | Omni Channel |
| `omni_auto_send_allowed` | vắng; chỉ có khi `q1_allowed` tích | site_config và 9A |
| `omni_ai_allow_customer_data` | khớp `q11_allowed` | site_config và 9A |
| `bot_analyze_enabled` | `0`; hoặc `gate_phan_tich_passed_at` đã có | 7, 9A |
| `bot_reply_mode` | Off; hoặc `gate_nhap_passed_at` đã có | 7, 9A |
| `sandbox_mode` | production: bật = `CAN_NGUOI_KIEM` ("tắt có chủ đích"); staging: phải bật | 1 |
| `scheduler` | bật (chỉ đọc cấu hình bật/tắt, không đo scheduler có thật sự chạy) | [01](01-cai-dat-va-ha-tang.md) mục 4 |
| `worker_omni`, `worker_omni_leads` | hàng đợi khai báo trong `common_site_config.json` và có ít nhất một worker đang nghe | [01](01-cai-dat-va-ha-tang.md) mục 3 |
| `alert_recipients` | có email hợp lệ | 5 |
| `business_hours`, `holidays` | hợp lệ; `holidays` trống hoặc đã qua hết = `CAN_NGUOI_KIEM` | 4 |
| `lead_form` | mỗi nền tảng có kênh bật thì có biểu mẫu bật, mỗi biểu mẫu có `default_owner` hoặc `assignment_rule` | [12](12-lead-quang-cao.md) |
| `token_meta`, `token_zalo` | token còn hạn (Meta: hợp lệ, hạn và quyền dữ liệu > 7 ngày, ghi chú "đủ quyền") | 11A, [04](04-kenh-zalo-oa.md) |
| `zalo_oa_package` | `Chưa xác nhận` = `KHONG_DAT`; Cơ bản hoặc Tiêu chuẩn = `CAN_NGUOI_KIEM` | 11A |
| `hang_du_phong_webhook` | không còn tin trong hàng dự phòng | [09](09-van-hanh-hang-ngay.md) |
| `sao_luu_24h` | có tệp sao lưu DB mới hơn 24 giờ (production) | [01](01-cai-dat-va-ha-tang.md) mục 8 |
| `prompt_ghim` | chỉ báo nếu ghim khác bản mặc định; không bao giờ `KHONG_DAT` | 7 |

## 12. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| "... phải lớn hơn 0" khi lưu | Đặt `raw_payload_retention_days`, `max_attempts`, `stuck_after_minutes` hay `hot_lead_threshold` bằng 0 | Đặt ≥ 1 |
| "... không phải JSON hợp lệ" | Dấu nháy hoặc dấu phẩy sai ở Giờ làm việc / Ánh xạ nguồn / Giờ bot hoạt động | Dùng nháy kép, kiểm tra bằng trình soát JSON. `holidays` không báo lỗi nhưng sai thì bị bỏ qua |
| "Chờ giữa các lần thử phải là danh sách số nguyên dương" | Gõ `1;5` hoặc `0,5` | Dùng `1,5` |
| "Kênh báo không hợp lệ" | Gõ sai tên kênh báo | Chỉ `system`, `email` |
| Bật bot nhưng không thấy gì xảy ra | Chưa có khóa AI, hoặc `daily_token_budget` = 0, hoặc Q11 chưa mở (`omni_ai_allow_customer_data`), hoặc ngắt mạch đang mở | Xem Bot Run (`/app/omni-bot-run`) cột Trạng thái: `Skipped Budget`/`Skipped Switch`/`Error`; xem [07](07-bot-ai.md) mục sự cố |
| Việc gọi lại bị trễ so với dự kiến | `business_hours` / `holidays` sai | Soát lại hai ô mục 4 |
| Không sửa được Omni Settings | Không có vai trò Omni Admin | Gán vai trò ([01](01-cai-dat-va-ha-tang.md) mục 2) |
| Lệnh kiểm trước phát hành báo `KHONG_DAT` ở `bot_analyze_enabled` hoặc `bot_reply_mode` | Site đang mang cấu hình dev (bot bật) | Đặt `bot_analyze_enabled = 0`, `bot_reply_mode = Off`. **Đừng** điền `gate_*` cho lệnh qua (mục 9A) |
| Tắt tính năng mà vẫn chạy (kéo lại Meta, kiểm token, hàng dự phòng) | Nhầm chiều ô `..._disabled`: ô tích là TẮT | Mục 9E |
| Sửa `fit_*` mà điểm nhà thầu không đổi | Điểm tính lại khi dữ liệu nhà thầu đổi hoặc lúc 02:20 đêm | Chờ lần tính lại; mục 9B |

## 13. Đã xong khi

- [ ] `sandbox_mode` đúng ý định của môi trường đang cấu hình.
- [ ] `business_hours` đúng thực tế; `holidays` đã điền các ngày nghỉ của năm; mở lại thấy lưu đúng.
- [ ] `alert_recipients`, `callback_fallback_user` đã điền; `assignment_fallback_users` điền nếu muốn có người nhận thông báo dự phòng.
- [ ] Assignment Rule của CRM cho Lead đã tạo (hoặc đã ghi nhận là Q4 chưa chốt và chấp nhận gán tay).
- [ ] `daily_token_budget` đã xem lại, khớp ngân sách.
- [ ] `bot_analyze_enabled = 0`, `bot_reply_mode = Off`, `lead_scoring_enabled = 0` ở môi trường chưa qua các cổng của [07](07-bot-ai.md) và [10](10-quy-trinh-len-san-xuat.md).
- [ ] Bảng điểm (nếu dùng) đã nhập từ CSV **do trưởng sale duyệt**, không phải bản mẫu.
- [ ] Mục 9A: `q1_allowed`, `q11_allowed` đúng với quyết định đã chốt bằng văn bản; `gate_*` để trống nếu chưa đo bằng AI thật.
- [ ] Mục 9C và 9D: `response_sla_minutes`, `ads_silence_hours`, giới hạn Web Form đúng ý; `alert_recipients` đã điền để cảnh báo token Meta, hàng dự phòng, hàng đợi `omni_leads` đến được người.
- [ ] Đã chạy lệnh kiểm trước phát hành (mục 11B) và đọc từng mục `CAN_NGUOI_KIEM`.
