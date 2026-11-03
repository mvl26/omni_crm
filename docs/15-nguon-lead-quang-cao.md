# 15 · Nguồn Lead từ quảng cáo và chiến dịch

Phục vụ chiến dịch marketing đầu tiên: Lead từ **Meta Lead Ads**, **Google Ads (biểu mẫu khách hàng tiềm năng)**, **form trên website/landing page** và **dữ liệu từ hệ thống mua sắm công** đi vào cùng cổng tiếp nhận, được quy về đúng chiến dịch, phân loại, giao sale và đếm thời gian phản hồi.

> **Cảnh báo cho agent:** chi tiết API bên dưới lấy từ tài liệu Meta và Google tại thời điểm viết. Đối chiếu tài liệu hiện hành trước khi code (G15), ghi khác biệt vào `docs/12`.

## 1. Kênh mới

| `channel_type` | Cách nhận | Khóa chống trùng (`external_message_id`) |
| --- | --- | --- |
| `Meta Lead Ads` | Webhook Page, trường `leadgen` → worker gọi Graph API lấy dữ liệu | `leadgen_id` |
| `Google Lead Form` | Webhook do Google gọi (POST JSON) | `lead_id` |
| `Web Form` | Form website/landing page POST tới API của app | Mã gửi do trang tạo (UUID) |
Ba kênh trên là adapter theo `ChannelAdapter` (không có chiều ra), dùng chung cổng tiếp nhận, hàng đợi, định danh (`docs/03`, `docs/04`, `docs/14` mục 2). Sự kiện có `event_type = "form_lead"`.

Danh sách nhà thầu từ dữ liệu đấu thầu **không** đi qua cổng tiếp nhận: importer D1-14 ghi trực tiếp Organization, Bid Award, Contact, Lead (mục 8).

## 2. Meta Lead Ads

- Cấu hình: ứng dụng Meta có sản phẩm Webhooks, đăng ký đối tượng **Page**, trường **leadgen**; cài ứng dụng lên Page bằng `POST /{page-id}/subscribed_apps?subscribed_fields=leadgen` với Page access token.
- Quyền cần (theo tài liệu Meta): `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `ads_management` (và `pages_manage_ads` khi đọc lead); token của người có quyền quảng cáo trên Page; cài đặt Leads Access Manager của Page có thể chặn truy cập — kiểm tra khi cấu hình.
- **Token Page — khuyến nghị token System User (V21-02):** dùng token của **System User** trong Meta Business (Business Settings → Users → System users, gán Page và ứng dụng, cấp `leads_retrieval`, `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`, `ads_management`) vì không tự hết hạn (`expires_at = 0`), khỏi phải gia hạn định kỳ. Token người dùng thường hết hạn (khoảng 60 ngày) và chết khi người đó đổi mật khẩu hoặc rời Page. Điền `Meta: App ID` (cùng App Secret) trên Omni Channel để hệ thống tự kiểm token hằng ngày bằng `GET /debug_token` (app access token `app_id|app_secret`): cảnh báo khi token không hợp lệ, còn <= `meta_token_warn_days` (mặc định 7) ngày tới `expires_at` hoặc `data_access_expires_at`, hoặc thiếu `leads_retrieval`. Kết quả hiện ở nhóm "Meta: kết quả kiểm token Page" của kênh và ở `/app/omni-monitor` cạnh hạn token Zalo. Chưa thử với token thật.
- Xác minh webhook (theo hướng dẫn Webhooks chung của Meta — agent xác nhận): `GET` với `hub.mode=subscribe`, `hub.verify_token`, `hub.challenge` → so verify token cấu hình, trả lại `hub.challenge`; mỗi `POST` kiểm chữ ký `X-Hub-Signature-256` (HMAC-SHA256 của raw body với app secret).
- Payload: `object = "page"`, `entry[].changes[]` có `field = "leadgen"`, `value` gồm `leadgen_id`, `page_id`, `form_id`, `adgroup_id`, `ad_id`, `created_time`. Cổng **chỉ lưu** sự kiện; worker gọi `GET /{leadgen_id}` lấy `field_data` (mỗi phần tử `name` + `values[]`), `ad_id`, `form_id`, `created_time`; câu trả lời ô đồng ý tùy chỉnh lấy qua trường `custom_disclaimer_responses`.
- Lấy tên chiến dịch/nhóm quảng cáo/quảng cáo: gọi Graph API theo `ad_id` (cache theo ngày) để có `campaign_id`, `campaign name`, `adset`, `ad name`.
- **Đối soát mỗi giờ**: với mỗi form đã cấu hình, `GET /{form_id}/leads` lọc `time_created` > mốc cuối → tạo `InboundEvent` cho lead thiếu (chống trùng theo `leadgen_id`). Bù được webhook sót.

## 3. Google Ads — biểu mẫu khách hàng tiềm năng

- Cấu hình trong Google Ads cho từng biểu mẫu: URL webhook của app + **khóa** (`google_key`) riêng cho biểu mẫu đó.
- Google gửi `POST` JSON gồm: `lead_id` (duy nhất, dùng chống trùng), `user_column_data[]` (`column_id` như FULL_NAME, EMAIL, PHONE_NUMBER…, `string_value`; `column_name` có thể thiếu và đã ngừng dùng — dựa vào `column_id`), `form_id`, `campaign_id`, `gcl_id`, `adgroup_id`, `creative_id` (chỉ với quảng cáo video/Discovery), `google_key`, `is_test`, `lead_submit_time`.
- Xác thực: so `google_key` với khóa đã lưu cho đúng `form_id` (`compare_digest`); sai → 4xx.
- Phản hồi: `200` với `{}` khi đã lưu; lỗi tạm thời trả `5xx` để Google gửi lại; Google không đảm bảo gửi đúng một lần → chống trùng theo `lead_id`.
- `is_test = true` → lưu với cờ thử nghiệm, tạo Lead trong nhóm "Thử nghiệm" (không giao sale, không tính báo cáo).
- Câu hỏi tùy chỉnh trong biểu mẫu (tên công ty, mã số thuế…) đến dưới dạng `column_id` riêng — ánh xạ ở mục 5.
- Tải lại khi webhook sót: xác nhận khả năng lấy dữ liệu biểu mẫu qua Google Ads API hoặc tải CSV trong giao diện Google Ads (G15); tối thiểu phải có lệnh nhập CSV tải từ Google Ads, chống trùng theo `lead_id`.

## 4. Form website / landing page

- Endpoint `POST /api/method/omni_crm.api.webhooks.web_form` (allow_guest): nhận JSON hoặc form-data.
- Chống spam: trường honeypot, giới hạn tần suất theo IP (ví dụ 5 lần/10 phút), kiểm tra token chống bot nếu website dùng (reCAPTCHA/Turnstile — cấu hình được, tắt mặc định), độ dài trường.
- Trang gửi kèm: các tham số `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`, `utm_term`, `gclid`, `fbclid`, trang đích, trang giới thiệu (referrer) — script nhỏ lưu UTM từ lần truy cập đầu vào sessionStorage để không mất khi khách chuyển trang.
- Form `LeadForm` của bộ nhận diện Miyano hiện gửi vào ERPNext Lead: chuyển sang gửi tới endpoint này (giữ nguyên các ô đồng ý không đánh dấu sẵn theo Luật 91/2025/QH15 và Nghị định 356/2025/NĐ-CP như README bộ nhận diện).
- Trả về mã gửi để trang hiển thị lời cảm ơn; không trả thông tin nội bộ.

## 5. Ánh xạ biểu mẫu — DocType `Omni Lead Form`

Mỗi biểu mẫu quảng cáo/website là một bản ghi:

| Trường | Kiểu | Ghi chú |
| --- | --- | --- |
| `platform` | Select | Meta / Google / Website |
| `external_form_id` | Data, duy nhất theo platform | |
| `form_name` | Data | |
| `secret` | Password | `google_key` với Google; verify token với Meta (dùng chung cấp ứng dụng nếu cần) |
| `campaign` | Link Omni Campaign | Chiến dịch mặc định nếu nền tảng không gửi |
| `field_map` | Table (`Omni Lead Form Field`: `source_key` → `target`) | `target` ∈ full_name, phone, email, company_name, tax_id, job_title, province, hospital_count_text, message, consent_contact, consent_marketing, custom:<tên> |
| `default_owner`, `assignment_rule` | Link User, Data | Ghi đè quy tắc phân công nếu cần |
| `enabled` | Check | Công tắc theo biểu mẫu |

Ô không ánh xạ được vẫn lưu nguyên văn vào tin vào và hiện trên dòng thời gian.

## 6. Chiến dịch và quy kết nguồn

DocType **`Omni Campaign`**: `campaign_name`, `platform` (Meta / Google / Website / Liên hệ chủ động / Đa kênh), `external_campaign_id` (duy nhất theo platform), `start_date`, `end_date`, `budget` (Currency, tùy chọn — ngân sách dự kiến), `spend` (Currency — chi phí thực tế, nhập tay), `utm_campaign` (để khớp lead từ website), `owner_team`, `status`. Agent kiểm tra trước: app `crm`/Frappe v15 có sẵn DocType chiến dịch hoặc UTM (UTM Source/Medium/Campaign) không — có thì dùng/liên kết, không tạo trùng (G16).

Custom field trên CRM Lead (fixtures):

| fieldname | Ghi chú |
| --- | --- |
| `omni_campaign` | Link Omni Campaign — chiến dịch **đầu tiên** đưa Lead tới (first touch) |
| `omni_last_campaign` | Chiến dịch gần nhất (last touch) |
| `omni_ad_platform` | Meta / Google / Website / Danh sách mua sắm công / Khác |
| `omni_ad_id`, `omni_adset_id`, `omni_form_id` | Mã quảng cáo, nhóm quảng cáo, biểu mẫu |
| `omni_utm_source`, `omni_utm_medium`, `omni_utm_campaign`, `omni_utm_content`, `omni_utm_term` | |
| `omni_click_id` | `gclid` / `fbclid` |
| `omni_landing_page` | |
| `omni_first_response_at` | Thời điểm sale/bot phản hồi đầu tiên (đo tốc độ chăm sóc) |
| `omni_is_test` | Lead thử nghiệm |

`source` của CRM Lead đặt theo `omni_ad_platform` (tạo CRM Lead Source tương ứng). Lead đã có mà quay lại qua chiến dịch khác: giữ `omni_campaign`, cập nhật `omni_last_campaign`, ghi một dòng hoạt động "Quay lại qua chiến dịch …".

## 7. Phân loại và chăm sóc ngay khi Lead vào

1. **Định danh** theo `docs/14` mục 2: mã số thuế khách tự khai là tín hiệu **trung bình** (có thể gõ sai) — khớp đúng một Organization thì gắn; không khớp thì tạo Organization với `omni_verified = 0` ("chưa xác minh") để sale kiểm tra. SĐT/email khách tự điền là tín hiệu mạnh.
2. **Phân loại tự động** (code, không AI):
   - `form_lead` có tên công ty/mã số thuế → Lead nhà thầu; nhà thầu đã có điểm phù hợp → hiện hạng ngay.
   - Thiếu SĐT và email hợp lệ → "Thiếu thông tin liên hệ".
   - Trùng một Lead đang mở → gộp vào Lead đó, không giao lại người khác.
   - `is_test` → nhóm Thử nghiệm.
3. **Giao sale ngay** theo Assignment Rule của CRM hoặc `Omni Lead Form.default_owner`; thông báo realtime + email cho người được giao.
4. **Đồng hồ phản hồi**: tính từ lúc Lead vào tới `omni_first_response_at` (tin ra đầu tiên, cuộc gọi đi được nghe máy, hoặc nhật ký chăm sóc đầu tiên). Đây là định nghĩa chuẩn cho chỉ số "Phản hồi lần đầu" trên Tổng quan (`docs/13` mục 1.2). Quá `response_sla_minutes` trong giờ làm việc → nhãn "Quá hạn phản hồi" trên Tổng quan, nhắc người được giao; quá 2 lần ngưỡng → báo trưởng nhóm.
5. **Xác nhận tự động cho khách** (tùy chọn theo biểu mẫu, mặc định tắt): email cảm ơn kèm thời gian sale sẽ liên hệ, chỉ khi khách đã điền email và tích ô đồng ý liên hệ. Không gửi Zalo (OA chưa được nhắn khi khách chưa tương tác).

## 8. Dữ liệu từ hệ thống mua sắm công

- Bản này nhận dữ liệu qua **mẫu Excel** (D1-14) do IT tổng hợp.
- Thu thập tự động (cào) trang muasamcong và nguồn `ScraperFileSource`/API **chờ V2-Q13** (điều khoản sử dụng của trang, hoặc mua gói API như DauThau.info) — không xây trong bản này. Khi V2-Q13 được chốt, thêm nguồn tương ứng theo interface `BidDataSource`, xuất đúng mã trường của mẫu Excel.
- Lead từ danh sách có `omni_ad_platform = "Danh sách mua sắm công"` và chiến dịch "Liên hệ chủ động – <tháng>", để báo cáo tách được Lead chủ động với Lead từ quảng cáo.

## 9. Báo cáo chiến dịch (bổ sung Tổng quan, `docs/13`)

Khối **Theo chiến dịch** (lọc theo kỳ): mỗi chiến dịch/nguồn — số Lead, số Lead hợp lệ (có liên hệ, không phải thử, không trùng), % được phản hồi trong hạn, thời gian phản hồi lần đầu trung vị, số nhà thầu hạng A/B, số đã chuyển "Đã trao đổi nhu cầu" trở lên, số Deal. Chi phí quảng cáo nhập tay theo chiến dịch (trường `budget`/`spend`) để tính chi phí mỗi Lead hợp lệ; nối API chi phí để sau.

## 10. Kiểm thử bắt buộc

- Meta: handshake đúng/sai verify token; chữ ký đúng/sai; webhook gửi trùng; Graph API lỗi tạm thời → thử lại; đối soát giờ bù lead thiếu.
- Google: `google_key` đúng/sai/khác form; gửi trùng `lead_id`; `is_test`; câu hỏi tùy chỉnh ánh xạ đúng.
- Web form: honeypot, quá tần suất, thiếu đồng ý, UTM lưu đúng.
- Cùng một người điền form Meta rồi nhắn Zalo (cùng SĐT) → một Lead, có cả hai hoạt động, `omni_campaign` giữ chiến dịch Meta.
- SLA (giờ làm 08:00–17:30 thứ Hai–Sáu, 08:00–12:00 thứ Bảy, ngưỡng 15 phút): Lead vào 16:50 thứ Sáu → hạn 17:05 thứ Sáu; Lead vào 17:20 thứ Sáu → hạn 08:05 thứ Bảy; Lead vào 11:55 thứ Bảy → hạn 08:10 thứ Hai.

## 11. Ghi chú thi hành V2-05 (2026-11-02)

- Đã đối chiếu tài liệu Meta/Google (docs/12 G15, G16); **chưa thử với nền tảng thật**.
- Lệch có chủ đích so với văn bản trên: (1) KHÔNG có email xác nhận tự động (mục 7 bước 5, ADR 0013); (2) Graph API đọc lead lấy chiến dịch qua `GET /<ad_id>` (mục 2) vì tài liệu không liệt kê `campaign_id` trên nút lead; (3) đồng ý chỉ ghi lên Contact đã có; (4) đối soát Meta lùi mốc 5 phút và lần đầu quét 24 giờ; (5) "Thiếu thông tin liên hệ" là Comment trên Lead + cách tính "Lead hợp lệ", không phải trạng thái.
- Lead thử (`omni_is_test`) bị loại khỏi mọi khối Tổng quan, không giao sale, không nhắc quá hạn.
