# 17 · Marketing Hub

Quyết định kiến trúc: ADR 0028. Khảo sát: `docs/marketing_hub_plan.md`.

## DocType

| DocType | Trường chính |
| --- | --- |
| `Omni Marketing Settings` (Single) | `hub_disabled`, `dry_run` (mặc định 1), `late_window_minutes` (30), `graph_api_version`, `insights_sync_hours` (6), `landing_base_url` (https://yhct.miyano.com.vn), `default_landing_page` (/spd/), `require_approval` (1), `quality_threshold` (80), `default_approver`, `ai_model`, `ai_fallback_model`, `monthly_ai_budget`, `brand_voice`, `channel_checks` (JSON, chỉ đọc) |
| `Omni Content Pillar` | `pillar_name` (khóa), `description`, `is_active` |
| `Omni Content Item` | `title`, `campaign`, `status` (Nháp / Chờ duyệt / Đã lên lịch / Đang xử lý / Đã đăng / Đăng một phần / Thất bại / Đã hủy), `pillar`, `funnel_stage`, `quality_score`, `quality_note`, `caption`, `media`, `media_items` (Omni Content Media), `landing_page`, `utm_content` (bai-01…), `approved_by`, `approved_at` |
| `Omni Content Target` | `content_item`, `campaign`, `platform` (Facebook / Zalo OA / TikTok), `channel_account` (Link Omni Channel), `post_format`, `scheduled_at`, `status` (Chờ duyệt / Đã lên lịch / Đang xử lý / Đăng thành công / Thất bại / Đã hủy), `attempts`, `next_retry_at`, `idempotency_key` (unique), `tracking_url`, `external_post_id`, `permalink`, `published_at`, `error_type`, `last_error`, `reach`, `impressions`, `likes`, `comments`, `shares`, `clicks`, `insights_synced_at` |
| `Omni Publish Log` | `target`, `attempt_no`, `outcome` (success / error / dry_run), `http_status`, `started_at`, `finished_at`, `error_type`, `message` |
| `Omni Campaign` (thêm) | `objective`, `audience`, `key_message`, `landing_page`; trạng thái thêm `Đã hủy`, `Có lỗi`; `utm_campaign` unique, tự sinh `slug-mmyy` khi tạo bài đầu tiên |
| CRM Lead (thêm) | `omni_content_item` (Bài đăng nguồn), `omni_funnel_stage` |

## Vai trò

| Vai trò | Được làm |
| --- | --- |
| Omni Admin / System Manager | Sửa Thiết lập Marketing, Kiểm tra quyền kênh, mọi việc khác |
| Marketing Manager | Duyệt và lên lịch bài; tạo trụ cột, chiến dịch |
| Marketing User | Soạn bài, chọn nền tảng, gửi duyệt, hủy lịch. Duyệt được chỉ khi tắt "Bắt buộc duyệt" |

## API (`omni_crm.marketing.api`)

- `hub_state`
- `get_social_channels`
- `check_channel(name)` (POST, Omni Admin)
- `list_approvers`
- `submit_item(name)`, `approve_item(name)`, `cancel_item(name)` (POST)

## Link theo dõi

`landing_base_url + landing_page + ?utm_source={facebook|zalo|tiktok}&utm_medium={social|oa|social}&utm_campaign=<chiến dịch>&utm_content=<bai-NN>`

Trang đích luôn chuẩn hóa thành thư mục có `/` cuối, vì nginx bỏ query khi chuyển `/spd` sang `/spd/`.

## Công tắc

- `hub_disabled`: ẩn Marketing Hub.
- `dry_run`: không gọi API đăng bài. Chưa lưu cấu hình thì coi là bật. `Omni Settings.sandbox_mode` cũng tính.
- `monthly_ai_budget` (USD/tháng): 0 = tắt mọi lời gọi AI marketing.

## AI (Prompt 4, ADR 0029)

- Cổng: `bot/ai_client.call_tool_result`, loại Bot Run `Marketing`, chi phí `cost_usd` theo `bot/pricing.py`.
  Lớp `marketing/ai.py` chặn theo ngân sách USD/tháng và chuyển sang `ai_fallback_model` khi mô hình chính lỗi
  hoặc trả sai schema sau 2 lần sửa. Token Marketing không tính vào hạn mức token/ngày của bot chat.
- Q11: brief chiến dịch và tài liệu nội bộ không phải dữ liệu khách, gọi với `customer_data=False`.
  (Ghi ở đây và ADR 0029; sẽ chép sang docs/12 "Đã xác nhận" khi docs/12 trên máy chủ hết thay đổi dở.)
- Khung chat: `planner_send(text, files, session_id)` -> job nền -> hỏi lại tối đa 3 câu cả phiên (nút gợi ý) hoặc
  kế hoạch JSON -> chấm điểm từng bài -> `planner_create(session_id)` tạo chiến dịch + bài + nền tảng rồi đi qua
  `approval.submit` / `approve` (Manager: lên lịch; User: Chờ duyệt; dưới ngưỡng: dừng ở Chờ duyệt kèm lý do).
  Realtime `oc_plan_update`. Phiên lưu cache 24 giờ, chỉ chủ phiên đọc.
- AI chỉ lên bài Facebook / Zalo OA (TikTok chưa có quyền đăng). Giờ đăng phải sau hiện tại ≥ 30 phút và trong
  khoảng ngày chiến dịch. Kênh đăng mặc định: kênh bật tạo sớm nhất của nền tảng (`channels.default_channel`).
- Chấm điểm: 4 tiêu chí (thông tin 40, kêu gọi 20, giọng văn 20, độ dài 20), luật cứng chỉ hạ điểm (số tiền /
  phần trăm không có nguồn, tiêu đề Zalo > 150). `quality_score` chỉ bộ chấm ghi được; sửa tiêu đề/caption/ảnh
  thì điểm về 0. Duyệt bị chặn khi còn `[cần bổ sung]` hoặc thiếu ảnh cho fb_photo / fb_multi_photo / zalo_article.
- Kho tri thức: `Omni KB Category`, `Omni KB Document`, `Omni KB Chunk`. Lưu tài liệu -> job `long` lập chỉ mục
  (PDF qua pypdf, DOCX đọc XML, TXT/MD). Truy hồi BM25 không dấu, gộp `Omni Knowledge Item`. `kb_ask` trả lời kèm
  nguồn; `kb_feedback(run, Đúng|Cần sửa|Sai)` lưu vào Bot Run.
- API mới: `ai_budget`, `planner_send`, `planner_state`, `planner_create`, `score_item`, `kb_overview`, `kb_reindex`,
  `kb_ask`, `kb_feedback`.

## Lead và chiến dịch (Prompt 5)

- Landing page: `omni_utm.js` (đã có từ V2-05) lưu UTM lần truy cập đầu vào sessionStorage và gửi kèm form.
  `form_lead.resolve_campaign` tìm chiến dịch theo `utm_campaign`; `marketing/attribution.py` tìm bài theo
  (chiến dịch, `utm_content`) và điền `omni_content_item`, `omni_funnel_stage`. Mã không khớp: Lead vẫn tạo, giữ UTM gốc.
- Nguồn đầu tiên giữ nguyên (`omni_campaign` = "Chiến dịch nguồn"); lần sau cập nhật `omni_last_campaign` và ghi
  dòng nhật ký "Khách điền thêm form … - Chiến dịch … - bài …". Lead mới: "Lead tạo từ form landing page /spd/ - …".
- Lead Ads: chiến dịch lấy theo mã chiến dịch Meta hoặc `Omni Lead Form.campaign` (tương đương `leadads_form_ids`
  trong đề bài, không thêm trường mới).
- Bình luận / tin nhắn từ bài đăng: CHƯA làm. Cần webhook `feed` (bình luận) của Meta và quyền
  `pages_manage_engagement` / `pages_read_user_content` mà token Trang chưa có, đồng thời phải sửa luồng intake đang
  có thay đổi dở trên máy chủ. Sẽ làm khi có quyền Meta và intake ổn định.
- Giao diện: patch `v3.marketing_lead_ui` thêm khối "Marketing" (chiến dịch, bài đăng, UTM nguồn/kênh, giai đoạn phễu)
  ngay sau khối "Bot AI" ở cột phải trang Lead, và thêm "Chiến dịch nguồn" vào bộ lọc nhanh. Cột danh sách Lead do
  `CRMLead.default_list_data()` của app crm quyết định (không sửa app crm): người dùng thêm cột "Chiến dịch nguồn" bằng
  nút Cột của danh sách.
