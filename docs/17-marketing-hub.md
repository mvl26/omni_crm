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
