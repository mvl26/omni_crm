# 02 · Cấu hình Omni Settings

Dành cho: quản trị hệ thống và trưởng sale (một số mục cần trưởng sale quyết). Mở: **Desk > `/app/omni-settings`** (đường dẫn theo quy ước Frappe, chưa kiểm trên giao diện). Chỉ vai trò **Omni Admin** (và Administrator) mở và sửa được. Mọi thay đổi lưu lịch sử phiên bản (`track_changes`), xem ở mục Activity/Version của form.

Mục lục: [1. Nhóm Chung](#1-chung) · [2. Xử lý và thử lại](#2-xử-lý-và-thử-lại) · [3. Thời hạn lưu](#3-thời-hạn-lưu) · [4. Giờ làm việc và ngày lễ](#4-ánh-xạ-nguồn-lead-giờ-làm-việc-ngày-lễ) · [5. Cảnh báo và phân việc](#5-cảnh-báo-và-gán-lead) · [6. Tổng đài](#6-tổng-đài) · [7. Bot AI và ngân sách](#7-bot-ai-và-ngân-sách-token) · [8. Chấm điểm Lead](#8-chấm-điểm-lead) · [9. Chuyển người và tự gửi](#9-chuyển-người-nháp-và-tự-gửi) · [10. Ghi âm/STT](#10-ghi-âm-và-chuyển-giọng-nói) · [11. Bảng công tắc](#11-bảng-công-tắc--hiệu-ứng) · [12. Sự cố thường gặp](#12-sự-cố-thường-gặp) · [13. Đã xong khi](#13-đã-xong-khi)

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
| Ánh xạ kênh sang nguồn Lead | `default_lead_source_map` | `{"Email":"Email","Zalo OA":"Zalo OA","Telephony":"Điện thoại"}` | Khi tạo Lead, nguồn (CRM Lead Source) lấy theo loại kênh. Nguồn chưa có thì được tạo. JSON sai thì không lưu được. Đổi tên nguồn nếu CRM của bạn đã có tên nguồn khác. |
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
| Phiên bản câu lệnh phân tích/trả lời/tóm tắt, Phiên bản kho tri thức | `analyze_prompt_version`, `reply_prompt_version`, `summarize_prompt_version`, `kb_version` | (tự điền) | — | **Chỉ đọc**, hệ thống ghi mỗi lần migrate hoặc khi kho tri thức đổi. Dùng để biết một nháp sinh bằng phiên bản nào. |

Quy tắc ngân sách: ngày tính theo giờ site; bộ đếm token nằm ở Redis và tự dựng lại từ tổng `Omni Bot Run` nếu mất. Cảnh báo khi dùng trên 80% hạn mức, khi lỗi AI quá 5% (từ 20 lần gọi), khi hết hạn mức và khi ngắt mạch mở (xem [09](09-van-hanh-hang-ngay.md)).

## 8. Chấm điểm Lead

| Trường | Tên kỹ thuật | Mặc định | Khuyên dùng | Ý nghĩa và hậu quả |
| --- | --- | --- | --- | --- |
| Bật chấm điểm Lead | `lead_scoring_enabled` | Tắt | Bật sau khi trưởng sale duyệt bảng điểm (Q5, **chưa chốt**) | Bật = tính điểm bằng **luật cố định** (không do AI quyết) từ trường Lead và kết quả phân tích; vượt ngưỡng thì báo người phụ trách một lần. Tắt = không tính điểm mới, điểm cũ giữ nguyên. Bảng điểm rỗng cũng coi như không chấm. |
| Ngưỡng Lead nóng | `hot_lead_threshold` | 60 | 60 hoặc theo quyết định của trưởng sale | Từ ngưỡng này là Lead nóng, báo một lần ("Lead nóng: <tên>, điểm <n>"). Phải ≥ 1 (form từ chối 0 hay số âm, dù đặc tả nói "≤ 0 dùng 60"). |
| Bảng điểm | `score_rules` | trống | Nhập từ CSV đã duyệt | Bảng con, mỗi dòng: **Tín hiệu** (chọn trong danh sách cố định), **Điểm** (số nguyên, có thể âm), **Ghi chú**. Mỗi tín hiệu chỉ được một dòng. |

Danh sách tín hiệu hợp lệ: `has_phone` (có SĐT), `has_email`, `has_org` (nêu tên đơn vị), `intent_price` (hỏi giá/báo giá), `intent_purchase` (muốn mua), `quantity_given` (nêu số lượng), `timeframe_within_30d` (dự kiến mua trong 30 ngày), `urgency_high` (mức gấp cao), `repeat_contact` (liên hệ từ 2 lần trong 30 ngày), `complaint` (khiếu nại; thường trừ điểm).

Nhập bảng điểm từ CSV (mẫu `mau/bang-diem-lead-mau.csv`, cột `signal,points,note`). Chạy **trong `<bench-dir>`**, dùng **đường dẫn tuyệt đối** (lệnh này đọc đường dẫn đúng như bạn gõ, không tính từ thư mục app):

```bash
bench --site <site> execute omni_crm.bot.scoring.import_rules --kwargs "{'path': '/duong/dan/tuyet/doi/bang-diem-da-duyet.csv'}"
```

- Sai tín hiệu/điểm hoặc trùng dòng: lệnh báo mọi lỗi và **không đổi gì**.
- Lệnh **thay toàn bộ** bảng điểm cũ.
- Nếu tên tệp có chữ "mau" hoặc nội dung trùng bản mẫu của repo, lệnh vẫn nhập nhưng in cảnh báo "bảng điểm MẪU, chưa được Trưởng sale duyệt". Con số trong bản mẫu chỉ là đề xuất, không phải quyết định.
- Điểm cũng nhập tay được ngay trên form Omni Settings (bảng `score_rules`).

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

## 13. Đã xong khi

- [ ] `sandbox_mode` đúng ý định của môi trường đang cấu hình.
- [ ] `business_hours` đúng thực tế; `holidays` đã điền các ngày nghỉ của năm; mở lại thấy lưu đúng.
- [ ] `alert_recipients`, `callback_fallback_user` đã điền; `assignment_fallback_users` điền nếu muốn có người nhận thông báo dự phòng.
- [ ] Assignment Rule của CRM cho Lead đã tạo (hoặc đã ghi nhận là Q4 chưa chốt và chấp nhận gán tay).
- [ ] `daily_token_budget` đã xem lại, khớp ngân sách.
- [ ] `bot_analyze_enabled = 0`, `bot_reply_mode = Off`, `lead_scoring_enabled = 0` ở môi trường chưa qua các cổng của [07](07-bot-ai.md) và [10](10-quy-trinh-len-san-xuat.md).
- [ ] Bảng điểm (nếu dùng) đã nhập từ CSV **do trưởng sale duyệt**, không phải bản mẫu.
