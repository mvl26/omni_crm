# 0028 · Marketing Hub trong CRM: dùng lại Omni Campaign / Omni Channel, đăng bài theo lịch do CRM giữ, patch giao diện 0002

- Ngày: 2026-11-13
- Trạng thái: Đã chấp nhận (người phụ trách chọn 13/11/2026: "đã có trong omni rồi không tạo trùng, tối ưu hóa doctype có sẵn, sẽ không có token riêng, chỉ có 1 tài khoản Zalo OA"; "giữ Omni và viết một ADR"; "làm xong đẩy và test trên site yhct")
- Prompt: bộ prompt "Marketing Hub trong Frappe CRM" (Prompt 0 khảo sát: `docs/marketing_hub_plan.md`; Prompt 1: dữ liệu, phân quyền, Thiết lập)
- Dựa trên: ADR 0016 (patch giao diện crm), docs/15 (chiến dịch, UTM, Lead Ads)

## Bối cảnh

Bộ prompt đề xuất DocType tiền tố "OC " (OC Channel Account, OC Marketing Campaign, OC Settings, Custom Field `oc_*`). Khảo sát (Prompt 0) thấy:
- `Omni Campaign` đã có `utm_campaign`, trạng thái, ngày, ngân sách; Lead Ads, web form và intake đã khớp chiến dịch theo `utm_campaign`.
- CRM Lead đã có `omni_campaign` (chiến dịch đầu tiên), `omni_last_campaign` và `omni_utm_*`.
- Token Facebook (`meta_page_access_token`) và Zalo (`zalo_access_token`/`refresh_token`) đã nằm trong `Omni Channel`.
- Refresh token Zalo chỉ dùng một lần: nơi thứ hai tự làm mới sẽ làm chết kênh chat.

## Quyết định

1. **Giữ tiền tố `Omni`.** Không tạo bản trùng.

   | Bộ prompt | Dùng |
   | --- | --- |
   | OC Marketing Campaign | `Omni Campaign` + 4 trường (`objective`, `audience`, `key_message`, `landing_page`), thêm trạng thái `Đã hủy`, `Có lỗi` (giữ `Nháp`, `Kết thúc`; giao diện hiện "Bản nháp", "Hoàn thành"), `utm_campaign` UNIQUE |
   | OC Channel Account | `Omni Channel` (kênh `Meta …` = Facebook, `Zalo OA`). **Không có bảng token riêng.** Kết quả "Kiểm tra quyền" (không chứa token, chỉ 4 ký tự cuối) lưu ở `Omni Marketing Settings.channel_checks` theo tên kênh |
   | OC Settings / OC Marketing Settings | Single mới `Omni Marketing Settings` (trường riêng của marketing); API key AI dùng chung `Omni Settings.ai_api_key` |
   | `leadads_form_ids` | Đã có dạng tốt hơn: `Omni Lead Form.campaign` |
   | `oc_campaign`, `utm_*`, `oc_platform` | `omni_campaign`, `omni_utm_*`, `omni_ad_platform` (có sẵn) |
   | `oc_content_item`, `oc_funnel_stage` | Custom Field mới `omni_content_item`, `omni_funnel_stage` |
   | OC Admin | `Omni Admin` (có sẵn); thêm `Marketing Manager`, `Marketing User` (patch `v3.marketing_hub_setup`) |

2. **DocType mới** (module Omni CRM):
   - `Omni Marketing Settings`;
   - `Omni Content Pillar`;
   - `Omni Content Item` và bảng con `Omni Content Media`;
   - `Omni Content Target` (DocType riêng, chỉ mục `(status, scheduled_at)` và `(status, next_retry_at)`);
   - `Omni Publish Log`.

   Danh sách trường: `docs/17-marketing-hub.md`.
3. **Chế độ thử an toàn khi chưa cấu hình.** Single chưa lưu thì Frappe trả None. `marketing.settings` có mặc định: `dry_run` = BẬT, duyệt = BẬT, ngưỡng = 80. Patch cài đặt ghi mặc định vào Single. `Omni Settings.sandbox_mode` bật cũng tính là thử.
4. **Đổi trạng thái chỉ qua `marketing.approval`** (cờ `status_via_rules`): Gửi duyệt, Duyệt và lên lịch, Hủy. Không sửa thẳng trường trạng thái. Duyệt cần đủ cả:
   - vai trò Marketing Manager (khi "Bắt buộc duyệt" bật);
   - điểm >= ngưỡng;
   - có >= 1 nền tảng;
   - giờ đăng chưa quá cửa sổ đăng muộn.

   Khi duyệt, mọi nền tảng chuyển "Đã lên lịch", có `idempotency_key = omni-mk-<tên>` và `tracking_url`. `utm_campaign` khóa khi chiến dịch đã có bài được duyệt.
5. **Link theo dõi luôn dùng thư mục có dấu `/`** (`/spd/`). Lý do: nginx của landing (`/etc/nginx/miyano-landing.conf`: `location = /spd { return 301 /spd/; }`) bỏ query string khi chuyển hướng, nên `/spd?utm_…` sẽ mất mã.
6. **Giao diện: patch thứ hai `crm_frontend_patch/0002-marketing-hub.patch`.**
   - Không có điểm mở rộng chính thức cho cửa sổ Thiết lập của CRM v1.73.0 (`Settings.vue` viết cứng).
   - Patch sửa 1 tệp có sẵn (`components/Settings/Settings.vue`) và thêm 4 tệp mới: `Settings/Marketing/SocialChannelsSettings.vue`, `MarketingAISettings.vue`, `ContentVoiceSettings.vue`, `composables/marketingHub.js`.
   - Không đụng tệp nào của 0001.
   - `apply.sh` áp mọi `NNNN-*.patch` theo thứ tự và gỡ theo thứ tự ngược.
   - Menu trái "Marketing" và các trang Chiến dịch / Lịch (Prompt 3) sẽ thêm vào 0002: `router.js`, `AppSidebar.vue`.
   - Phần trong trang Lead (khối Marketing, cột/bộ lọc "Chiến dịch nguồn", Prompt 5) dùng điểm mở rộng chính thức (CRM Fields Layout, View Settings, Form Script), không vá.
7. **Công tắc** `Omni Marketing Settings.hub_disabled` (cực tính ngược, mặc định hiện). Bật thì các mục Marketing trong Thiết lập ẩn mà không cần build lại, API trả lỗi quyền, bộ đăng bài (Prompt 2) không chạy.
8. **Kiểm tra quyền chỉ gọi đọc:**
   - Facebook: `debug_token`, rồi `GET /{page_id}?fields=id,name`. So với `pages_manage_posts`, `pages_read_engagement`, `pages_manage_engagement`, `read_insights`. Lead Ads và Messenger cùng Page thì hiện một dòng.
   - Zalo: `GET /v2.0/oa/getoa` (**VERIFY**, chưa đọc được tài liệu chính thức) và hạn token. Token lỗi thì `ZaloClient` làm mới bằng đúng cơ chế khóa của kênh chat, không có nơi làm mới thứ hai.
   - Quyền đăng bài viết OA chưa kiểm được qua API.

## Rủi ro

- Thêm một tệp crm bị sửa (`Settings.vue`), và sẽ thêm `router.js`, `AppSidebar.vue` ở Prompt 3: nâng crm phải dựng lại cả 0002.
- `hooks.py` trên máy chủ đang có thay đổi chưa commit của luồng Messenger/intake. Cron mỗi phút của Prompt 2 bắt buộc sửa `hooks.py`. Migrate của Frappe xóa Scheduled Job Type không còn trong hooks, nên phải gộp nền chung trước Prompt 2.
- Site thử chính là site chính `miyano` (theo yêu cầu người phụ trách). Mặc định Chế độ thử bật, chưa đăng bài thật nào.
