# Marketing Hub trong CRM — khảo sát và kế hoạch (Prompt 0)

- Ngày khảo sát: 2026-11-13. Chỉ đọc, không sửa code.
- Mã khảo sát: `omni_crm` commit `ca2c0ca` (đang chạy trên máy chủ). Thêm vào đó là phần chưa commit trên máy chủ của người khác: Messenger, intake, email.
- Frappe CRM `v1.73.0` (`3942bf6`), Frappe `v15.107.2`.

## 0. Ba điều phải chốt trước khi code

1. **Không có "site thử yhct".** Bench 103.1.237.204 chỉ có một site là `miyano`, và `host_name = https://yhct.miyano.com.vn`. Đây chính là site chính có dữ liệu thật, có sale làm việc hằng ngày.
   - Bộ prompt viết "Chỉ làm trên site thử yhct. Không chạy lệnh nào lên site chính". Câu này tự mâu thuẫn: làm theo chữ thì phải chạy migrate, tác vụ mỗi phút và thử đăng bài trên site thật.
   - Đề xuất:
     - phát triển và test trên `omni.test` ở máy dev;
     - nếu cần chỗ bấm thử giống thật, tạo thêm một site staging trên cùng bench (ví dụ `mkt-staging.miyano.com.vn`, chép từ backup của `miyano`, bật `sandbox_mode`);
     - chỉ đưa lên `miyano` khi anh gõ lệnh "đưa lên", theo đúng quy trình đang dùng.
2. **Trùng với thứ đã có.** Nhiều DocType "OC" trong bộ prompt đã có bản `Omni` đang chạy, có dữ liệu và có code phụ thuộc. Tạo song song sẽ gây hai nguồn sự thật. Nguy hiểm nhất là **token Zalo**:
   - Zalo cấp refresh token dùng một lần.
   - Nếu `OC Channel Account` giữ một bản token riêng rồi tự làm mới, token của kênh chat Zalo đang chạy (`Omni Channel`, job `token.refresh_if_needed` mỗi 30 phút) sẽ chết, và ngược lại.
   - Chi tiết ở mục 4.
3. **Tiền tố "OC " khác quy ước repo.** CLAUDE.md của repo quy định DocType mang tiền tố `Omni`, và không thêm DocType ngoài `docs/02` khi chưa có ADR. Đề xuất giữ tiền tố `Omni` (ví dụ `Omni Content Item`) và viết ADR 0028 "Marketing Hub" liệt kê DocType mới. Nếu anh muốn giữ "OC " thì ADR ghi rõ ngoại lệ.

## 1. Hiện trạng

### 1.1 Phiên bản và cấu trúc

- Bench có các app: frappe, erpnext, crm, assetcore, lms, tracecore, chat, hrms, omni_crm, payments.
- Các module của `omni_crm`:
  - `api/`: inbox, zalo_tab, webhooks, dashboard, admin, …
  - `channels/`:
    - email, zalo, telephony;
    - `meta_leads` (Lead Ads);
    - `google_leads`, `web_form`;
    - `meta_messenger`: chưa commit, đang làm trên máy chủ.
  - `bot/`: `ai_client.py` là cổng AI, có ngân sách token/ngày và ngắt mạch.
  - `crm_bridge/`, `pipeline/`, `identity/`, `monitoring/`, `intake_files/`.
  - `crm_frontend_patch/`: patch giao diện CRM.
- Có 24 DocType `Omni …`. Những DocType liên quan Marketing Hub:
  - `Omni Campaign`;
  - `Omni Lead Form`, cùng bảng con `Omni Lead Form Field`;
  - `Omni Channel`;
  - `Omni Knowledge Item`;
  - `Omni Settings` (Single).
- Vai trò có sẵn: `Omni Admin`, `Omni Knowledge Editor`.

### 1.2 Cách tab Zalo và khối Bot AI gắn vào CRM

- **Tab Zalo, Trợ lý, Messenger** dùng một **patch git lên frontend của app crm** (ADR 0016):
  - Tệp patch: `omni_crm/crm_frontend_patch/0001-zalo-tab.patch`, áp bằng `apply.sh` (chỉ áp lên đúng crm v1.73.0, có `--check` và `--revert`). Sau đó `build.sh` chạy `vite build` với Node 22 và tự sao lưu/phục hồi bản cũ.
  - Patch gồm 12 tệp mới và 6 tệp có sẵn bị sửa vài dòng: `pages/Lead.vue`, `pages/Deal.vue`, `Activities.vue`, `ActivityHeader.vue`, `CallArea.vue`, `SidePanelLayout.vue` (chỉ CSS).
  - Dữ liệu đi qua API whitelisted `omni_crm.api.zalo_tab`.
  - Công tắc `zalo_tab_disabled` ẩn tab mà không cần build lại.
- **Khối "Bot AI" ở cột phải** không cần patch. Nó gồm Custom Field trên CRM Lead (`omni_bot_summary`, `omni_intent`, `omni_score`, …) đặt vào bố cục **CRM Fields Layout (Side Panel)**. Thẻ tóm tắt bot ở đầu tab Zalo thì nằm trong patch.
- Rủi ro: máy chủ đang có thay đổi chưa commit trong `crm_frontend_patch/0001-zalo-tab.patch` (tab Messenger của người khác). Patch là **một tệp dùng chung**, nên Marketing Hub mà cũng sửa patch này thì phải gộp với phần Messenger.

### 1.3 Điểm mở rộng chính thức của Frappe CRM v1.73.0 (đã đọc mã)

| Nhu cầu | Không cần vá crm? | Cơ chế / vị trí |
| --- | --- | --- |
| Nhóm menu trái "Marketing" | **Không**, phải vá | `AppSidebar.vue:221-262`: mảng `links` cố định. Chỉ thêm được "Public/Pinned Views" của các danh sách CRM có sẵn. Route trong `router.js` cố định; đường lạ chuyển sang trang Invalid. |
| Mục mới trong cửa sổ Thiết lập | **Không**, phải vá | `Settings/Settings.vue:93-235`: các nhóm tab và import cố định. `TelephonyPage.vue` viết cứng Twilio/Exotel. |
| Khối "Marketing" cột phải Lead | **Có** | CRM Fields Layout loại "Side Panel": thêm section và Custom Field, sửa bằng giao diện hoặc fixture. Trường HTML được làm sạch bằng DOMPurify. Trường Button gọi được hàm của Form Script. Không gắn được component Vue riêng. |
| Nút/hành động trên Lead, danh sách | **Có** | CRM Form Script (fixture). Class `CRMLead` có `actions`, `onLoad`, `onChange`, `call`, `router`, `createDialog`, `setFieldHtml`. List script có `actions` và `bulk_actions`. |
| Cột và bộ lọc "Chiến dịch nguồn" ở danh sách Lead | **Có** | Cột lưu trong CRM View Settings. Bộ lọc nhanh qua CRM Global Settings "Quick Filters" hoặc `in_standard_filter=1` trên Custom Field. Mọi trường Link tự có trong bộ lọc. |
| Lối vào trang riêng | **Có**, một phần | Hook `standard_dropdown_items` thêm mục vào menu người dùng (góc trên trái) và **mở cửa sổ mới**. `add_to_apps_screen` hiện ở menu Apps. |

## 2. Ba cách gắn giao diện Marketing Hub

**a) Thêm route và trang Vue vào frontend crm (mở rộng patch hiện có)**
- Ưu:
  - Đúng y prototype: menu "Marketing" ở thanh trái, mục mới trong cửa sổ Thiết lập.
  - Cùng frappe-ui và cùng phiên đăng nhập.
  - Thống nhất với cách tab Zalo đang làm.
- Nhược và rủi ro:
  - Patch phình to: thêm khoảng 15–25 tệp mới, sửa thêm `router.js`, `AppSidebar.vue`, `Settings.vue`.
  - Mỗi lần nâng crm phải dựng lại patch, và ba tệp này hay đổi giữa các bản crm.
  - Mỗi thay đổi giao diện phải `vite build` trên máy chủ thật. Nếu build hỏng giữa chừng, CRM của mọi người dùng hỏng theo.
  - Phải gộp với phần Messenger chưa commit.

**b) SPA riêng trong omni_crm (frappe-ui, cùng kiểu), mở từ CRM**
- Ưu:
  - Không đụng app crm (trừ 1 dòng menu nếu muốn).
  - Build độc lập, hỏng thì chỉ hỏng Marketing.
  - Nâng crm không ảnh hưởng.
- Nhược:
  - Không nằm trong khung CRM: không có menu trái chung, không có cửa sổ Thiết lập chung, mở bằng tab mới.
  - Phải tự dựng khung (thanh bên, breadcrumb) cho giống CRM.
  - Thêm một bộ build Node riêng.

**c) Lai: điểm mở rộng chính thức cho phần nằm trong Lead, trang riêng cho phần còn lại, và chỉ 2 chỗ vá nhỏ trong crm**
- Phần trong Lead, không vá crm:
  - Khối Marketing ở cột phải: CRM Fields Layout và Custom Field.
  - Cột và bộ lọc "Chiến dịch nguồn": View Settings và Quick Filters.
  - Nút "Mở chiến dịch": Form Script.
- Bốn trang Chiến dịch / Lịch đăng / Báo cáo / Kho tri thức và ba trang Thiết lập là component **mới** trong patch, gắn bằng:
  - 1 nhóm link trong `AppSidebar.vue`;
  - 4 route trong `router.js`;
  - 1 nhóm trong `Settings.vue`.
- Logic nằm trong tệp mới, tệp crm có sẵn chỉ sửa vài dòng, như cách làm với tab Zalo.

**Đề xuất: (c).**
- Đúng luồng thao tác của prototype: menu trái và Thiết lập nằm trong CRM.
- Thống nhất với ADR 0016: patch tối thiểu, logic ở tệp mới, API ở `omni_crm`.
- Phần nằm trong Lead không cần vá.
- Tách thành **patch thứ hai** `0002-marketing-hub.patch`, áp sau `0001`, có công tắc `marketing_hub_enabled`. Tắt công tắc thì menu, route và Thiết lập ẩn mà không cần build lại.
- Rủi ro còn lại là ba tệp crm phải sửa (`router.js`, `AppSidebar.vue`, `Settings.vue`). Cần ADR mới, vì ADR 0016 chỉ cho phép phạm vi tab Zalo.
- Nếu anh không muốn vá thêm crm thì chọn (b): mở từ menu người dùng bằng `standard_dropdown_items`.

## 3. Đăng bài theo lịch: hạ tầng có sẵn

- Scheduler bật trên `miyano`. Có 15 worker: `long` 1, `short` 1, `omni` 2, `omni_leads` 2, `schedule` 1. `hooks.py` đã có cron `* * * * *` (spool, xin số).
- Thêm `dispatch_due` mỗi phút là được, queue `long` có 1 worker.
- Nếu muốn đăng song song nhiều bài cùng phút thì cân nhắc tách worker riêng (ví dụ `omni_publish`).

## 4. DocType: dùng lại, mở rộng, tạo mới

| Bộ prompt | Đã có | Đề xuất |
| --- | --- | --- |
| OC Settings / OC Marketing Settings | `Omni Settings` (Single, đã có `ai_api_key`, `analyze_model`, `daily_token_budget`, `sandbox_mode`) | **Tạo** Single `Omni Marketing Settings` cho các trường riêng của marketing (`quality_threshold`, `require_approval`, `late_window_minutes`, `default_landing_page`, `landing_base_url`, `dry_run`, `graph_api_version`, `insights_sync_hours`, `brand_voice`, `monthly_ai_budget`, `ai_model`, `ai_fallback_model`). API key AI **dùng chung** `Omni Settings.ai_api_key`, không nhân bản. |
| OC Channel Account | `Omni Channel`: Zalo OA đã có `zalo_access_token`/`refresh_token`/`expires_at` và job tự làm mới. Meta có `meta_page_id`, `meta_page_access_token` (Password), `meta_token_*` và job kiểm token 6 giờ. Hiện có 2 kênh Meta (Lead Ads, Messenger). | **Không tạo bảng token mới.** Có hai lựa chọn: (1) thêm vào `Omni Channel` các trường marketing (`publish_enabled`, `granted_permissions`, `last_check_result`, `last_checked_at`, trạng thái kết nối); hoặc (2) `Omni Channel Account` chỉ là bản ghi mỏng Link tới `Omni Channel` (tên hiển thị, định dạng cho phép), token vẫn đọc từ `Omni Channel`. Đề xuất (1). TikTok cần `channel_type` mới, ghi trong ADR. |
| OC Marketing Campaign | `Omni Campaign` (đang có 0 bản ghi trên miyano), đã có `campaign_name`, `platform`, `status` (Nháp/Đang chạy/Tạm dừng/Kết thúc), `utm_campaign`, `start_date`, `end_date`, `budget`, `spend`. Lead Ads, web form và intake đã khớp chiến dịch theo `utm_campaign` (`crm_bridge/form_lead.resolve_campaign`). | **Mở rộng `Omni Campaign`**, chỉ thêm trường: `objective`, `audience`, `key_message`, `landing_page`, sinh slug `utm_campaign`. Trạng thái: thêm "Đã hủy" và "Có lỗi" vào Select (đổi enum thì ADR ghi rõ). `leadads_form_ids` **đã có dạng tốt hơn** là `Omni Lead Form.campaign` (mỗi biểu mẫu Link một chiến dịch), nên không thêm ô văn bản. |
| OC Content Pillar | không có | Tạo `Omni Content Pillar`. |
| OC Content Item, OC Content Media | không có | Tạo `Omni Content Item` và bảng con `Omni Content Media`. |
| OC Content Target | không có | Tạo `Omni Content Target` (DocType riêng, có index `(status, scheduled_at)` và `external_post_id`, `idempotency_key` unique). |
| OC Publish Log | không có | Tạo `Omni Publish Log`. |
| OC KB Document, Category, Chunk | `Omni Knowledge Item` (hỏi/đáp cho bot, có `question_group`, duyệt bởi Knowledge Editor) | Tạo mới ở Prompt 4 vì khác bản chất (tài liệu, đoạn, vector). Bot chat **không** đọc kho mới cho tới khi có quyết định riêng. Kho tri thức marketing dùng `Omni Knowledge Item` làm nguồn bổ sung, chỉ đọc. |
| Custom Field CRM Lead `oc_campaign` | `omni_campaign` ("Chiến dịch đầu tiên", first touch, đã giữ nguồn đầu tiên) và `omni_last_campaign` | **Dùng lại.** Đổi nhãn hiển thị thành "Chiến dịch nguồn" bằng Property Setter nếu anh muốn. |
| `utm_source/medium/campaign/content` | `omni_utm_source`, `omni_utm_medium`, `omni_utm_campaign`, `omni_utm_content`, `omni_utm_term` | **Dùng lại.** |
| `oc_platform` | `omni_ad_platform` (Meta/Google/Website/…) | Dùng lại `omni_ad_platform`. Nền tảng đăng bài lấy từ bài đăng. |
| `oc_content_item`, `oc_funnel_stage` | không có | Thêm `omni_content_item` (Link) và `omni_funnel_stage`. |
| Vai trò Marketing Manager / User | `Omni Admin` có sẵn (dùng thay "OC Admin") | Tạo `Marketing Manager` và `Marketing User` (fixture Role). |

## 5. Form landing page /spd và /tong-thau

- Trang tĩnh nằm ở `/srv/miyano-landing/current` (đóng gói theo bản phát hành), nginx phục vụ qua `/etc/nginx/miyano-landing.conf` (include vào server block của yhct).
- `shared/track.js` đã lưu `utm_source/medium/campaign/content/term`, `gclid`, `gbraid`, `wbraid`, `fbclid`. `shared/lead-form.js` gửi `POST /landing-api/lead` (`shared/config.js`).
- Luồng đi:
  1. `/landing-api/lead` qua nginx tới dịch vụ **miyano-intake** (`/hooks/web-form`).
  2. miyano-intake ghi file JSON, nhật ký đồng ý và Meta CAPI.
  3. `omni_crm/intake_files/` (watcher và mapper, **chưa commit, của người khác**) đọc file.
  4. Mapper tạo Lead qua `channels/web_form`, `crm_bridge/form_lead`. `mapper.py:101` đã chép `utm_*`, `gclid`, `fbclid`; khớp chiến dịch theo `utm_campaign`.
- Endpoint công khai `omni_crm.api.webhooks.web_form` có, nhưng landing **không gọi trực tiếp**.
- Kết luận: phần lớn Prompt 5 mục 1 **đã có**. Chỉ còn thiếu:
  - khớp `utm_content` với bài đăng;
  - điền `omni_content_item` và `omni_funnel_stage`;
  - dòng hoạt động "Lead tạo từ form … – Chiến dịch … – bài …".
- Không cần sửa landing. Nếu cần sửa thì phải sửa trong repo landing và mapper intake, tức là phối hợp với người làm intake.

## 6. Facebook và Zalo: điều kiện API cần kiểm trước

- **Facebook:**
  - Page Miyano Việt Nam `571684352685074`, ứng dụng `1012569668539635`.
  - Token Page hiện cấp cho Lead Ads và Messenger. Cần kiểm token có `pages_manage_posts`, `pages_read_engagement`, `read_insights` không. Nếu thiếu thì phải cấp lại token, và có thể phải qua App Review.
  - Bình luận (Prompt 5 mục 3) cần đăng ký webhook trường `feed` cho Page. Webhook Meta hiện trỏ về miyano-intake, nên phải thêm loại sự kiện ở intake. Intake là dịch vụ ngoài omni_crm.
- **Zalo:**
  - Đăng "Bài viết OA" dùng chung access token của OA đang dùng cho chat. App Zalo phải được cấp quyền quản lý bài viết.
  - Gói OA hiện là "Tăng trưởng" (V3-07). Hạn mức và quyền của API bài viết theo gói **chưa kiểm** (# VERIFY).
  - Không tự làm mới token ở chỗ thứ hai (mục 0.2).
- **TikTok:** chưa có app, đúng như prompt: để interface, báo "chưa được duyệt".
- Chế độ thử: dùng `Omni Marketing Settings.dry_run` (mặc định bật) **và** tôn trọng `Omni Settings.sandbox_mode`. Một trong hai bật thì không gọi mạng.

## 7. Rủi ro

1. Site chính đang làm site thử (mục 0.1): migrate, cron mỗi phút và lỗi giao diện ảnh hưởng ngay tới sale.
2. Máy chủ có **62 tệp thay đổi chưa commit** trong `apps/omni_crm`, gồm Messenger, intake, email, hooks.py, doctype JSON, frontend patch. Đây là việc của một người khác, làm thẳng trên site chính.
   - Quy trình triển khai bằng bundle và `merge --ff-only` của mình sẽ đụng các tệp này: `hooks.py`, `omni_settings.json`, `0001-zalo-tab.patch`.
   - Cần người đó commit và đẩy nhánh, rồi mới làm Marketing Hub trên nền đó.
3. Vá thêm `router.js`, `AppSidebar.vue`, `Settings.vue` làm việc nâng crm khó hơn. Mỗi lần đổi giao diện phải build lại trên máy chủ thật.
4. Hai nơi giữ token (mục 0.2): mất kết nối Zalo chat.
5. Đăng trùng khi timeout. Prompt 2 đã có khóa nguyên tử và `reconcile`. Facebook không có idempotency key phía server, nên `reconcile` theo `utm_content` là bắt buộc.
6. Chi phí AI: cổng hiện tại tính **token/ngày** (`daily_token_budget`), còn prompt muốn **tiền/tháng**. Cần quy đổi giá theo mô hình, hoặc đổi sang hạn mức token/tháng.
7. Vector: MariaDB trên máy chủ chưa kiểm bản có kiểu VECTOR (11.7+) hay chưa. Quyết ở Prompt 4.

## 8. Câu hỏi cần anh trả lời

1. Làm và thử ở đâu: `omni.test` trên máy dev, cộng thêm một site staging mới trên bench (đề xuất), hay chấp nhận thử trên `miyano`?
2. Tiền tố DocType: `Omni` (theo repo, đề xuất) hay "OC "?
3. Đồng ý dùng lại `Omni Campaign`, `Omni Channel` (token), các trường `omni_utm_*` và `omni_campaign` thay vì tạo bản "OC" mới?
4. Cách gắn giao diện: (c) vá thêm 3 tệp crm qua patch `0002` (đề xuất), hay (b) trang riêng không vá crm?
5. Người làm Messenger/intake trên máy chủ: ai, khi nào commit và đẩy nhánh để gộp nền chung?
6. Ngân sách AI tính theo tiền/tháng hay token/tháng? Mô hình chính và dự phòng là gì?
7. Facebook: anh có quyền admin ứng dụng `1012569668539635` để cấp lại token Page với `pages_manage_posts` không? Zalo: app đã được cấp quyền bài viết OA chưa?
8. Bình luận Facebook ra Lead (Prompt 5 mục 3) cần sửa miyano-intake (đăng ký `feed`). Có làm ở đợt này không?
