# 09 · Công tắc và cấu hình

Mỗi phần có thể tắt **tức thì, không cần deploy, không mất dữ liệu**. Tắt một phần = phần đó ngừng hành động, nhưng tin vào vẫn được lưu để xử lý bù khi bật lại.

## 1. Bảng công tắc

| Công tắc | Ở đâu | Tắt thì | Đợt |
| --- | --- | --- | --- |
| `sandbox_mode` | Omni Settings | (Bật) không gửi gì thật ra ngoài | 1 |
| `enabled` | Omni Channel | Webhook vẫn trả 200 và **lưu** tin, không xử lý | 1 |
| `enabled` (kênh Telephony) | Omni Channel | Sự kiện cuộc gọi vẫn được lưu (200) nhưng không xử lý; `telephony_lookup` trả phản hồi "khách mới" rỗng, không tra DB | 2 |
| `outbound_enabled` | Omni Channel | Tin ra bị `Blocked` | 1 |
| `enabled` (kênh Telephony) — đối soát và việc gọi lại | Omni Channel | Đối soát đêm bỏ qua kênh; không tạo việc gọi lại (job dừng, bật lại thì tác vụ quét tạo bù) | 2 |
| `sandbox_mode` — tổng đài | Omni Settings | (Bật) đối soát đêm bỏ qua, không xác minh cuộc gọi nhỡ qua API nhà cung cấp | 2 |
| `missed_call_callback_minutes`, `callback_fallback_user`, `business_hours`, `holidays` | Omni Settings | Hạn gọi lại (phút làm việc), người nhận dự phòng, giờ làm việc, ngày nghỉ lễ (JSON `["2027-01-01", …]`, mặc định rỗng — việc cần người phụ trách điền) | 2 |
| `bot_analyze_enabled` | Omni Settings | Không gọi AI phân tích | 2 |
| `lead_scoring_enabled` | Omni Settings | Không tính điểm mới, không báo Lead nóng; `omni_score` và breakdown cũ giữ nguyên. Bảng điểm `score_rules` rỗng cũng coi như không chấm (không có điểm mặc định bịa). `hot_lead_threshold` (mặc định 60; <= 0 dùng 60) | 2 |
| `bot_reply_mode` = Off | Omni Settings | Không soạn trả lời | 2 |
| `auto_send_enabled` | Omni Question Group | Nhóm đó rơi về Draft | 3 |
| `recording_enabled` | Omni Settings | Không tải ghi âm, không chuyển văn bản | 3 |
| `daily_token_budget` = 0 | Omni Settings | Bot dừng hẳn (tương đương tắt mọi AI); lần gọi ghi `Skipped Budget`. Site đã migrate bắt đầu ở 0 cho đến khi đặt hạn mức | 2 |
| `daily_token_budget` — tính chất | Omni Settings | **Hạn mức mềm**: `call_tool` giữ chỗ token ước lượng (~ký tự/3 + max_tokens) trước khi gọi rồi hiệu chỉnh theo số thật, nhưng các worker chạy song song vẫn có thể vượt khoảng (số worker × max_tokens) | 2 |
| `sandbox_mode` — với AI | Omni Settings | `call_tool` KHÔNG đọc sandbox_mode: sandbox chỉ áp chiều gửi ra khách. Lời gọi AI thật bị chặn bằng cổng Q11 (`synthetic` / `omni_ai_allow_customer_data`) | 2 |
| Ngắt mạch AI (tự động) | cache `omni:ai:breaker_open` | 5 lần lỗi liên tiếp → 5 phút mọi lần gọi ghi `Skipped Switch`; xóa khóa để mở lại ngay | 2 |
| site_config `omni_ai_allow_customer_data` | site_config | Không bật (mặc định) thì lời gọi API thật chỉ chạy với hội thoại tổng hợp (`synthetic=True`); chỉ bật sau khi Q11 được chốt | 2 |
| site_config `omni_ai_timeout_seconds` | site_config | Timeout mỗi lần gọi AI, mặc định 60 | 2 |
| `bot_paused_until` | Omni Identity | Bot im với một khách | 3 |
| `alert_recipients` (trống) | Omni Settings | Không gửi thư cảnh báo (Notification Log cho Omni Admin vẫn tạo; không có công tắc riêng, xem docs/12 mục D) | 1 |
| `raw_payload_retention_days` = 0 | Omni Settings | Tác vụ đêm không xóa `raw_payload` | 1 |

## 2. Đọc công tắc — `flags.py`

```python
def channel_enabled(channel: str) -> bool: ...
def outbound_enabled(channel: str) -> bool: ...
def sandbox() -> bool: ...
def bot_analyze_enabled() -> bool: ...
def bot_reply_mode() -> str: ...            # "Off" | "Shadow" | "Draft"
def group_auto_send(group: str) -> bool: ...
def recording_enabled() -> bool: ...
# Kind → công tắc của call_tool: Analyze → bot_analyze_enabled; Reply → bot_reply_mode != Off; Summarize → recording_enabled; Judge → không có (chỉ chịu hạn mức)
```

- Đọc qua cache của Frappe (`frappe.get_cached_doc` / `get_cached_value`); cache tự xóa khi lưu Settings. Không cache riêng lâu hơn 30 giây.
- Kiểm tra công tắc **ngay trước hành động có tác động ra ngoài** (gọi AI, gửi tin), không chỉ ở đầu job.

## 3. Ghi vết

Mọi lần bật/tắt công tắc (trừ `bot_paused_until`) ghi Version (bật `track_changes` cho Omni Settings, Omni Channel, Omni Question Group). Màn hình giám sát hiện trạng thái công tắc hiện tại ở đầu trang.

## 4. Cấu hình bổ sung cho chuyển người (Đợt 3)

| Trường (Omni Settings) | Kiểu | Mặc định |
| --- | --- | --- |
| `handoff_message_in_hours` | Small Text | "Cảm ơn anh/chị đã liên hệ Miyano. Nhân viên sẽ phản hồi anh/chị trong ít phút." |
| `handoff_message_after_hours` | Small Text | "Cảm ơn anh/chị đã liên hệ Miyano. Nhân viên sẽ phản hồi anh/chị vào {gio_lam_viec_ke_tiep}." |
| `handoff_notify_channels` | Small Text | "system,email" |

## 5. Bảng cấu hình theo môi trường

**`sandbox_mode` chặn MỌI HTTP ra Zalo** (không chỉ gửi tin): gửi tin tư vấn, tin yêu cầu thông tin, lấy hồ sơ khách, làm mới token (`refresh_if_needed` bỏ qua) và đổi mã cấp quyền OAuth (nút "Kết nối Zalo OA" vẫn chuyển hướng được nhưng callback không đổi code lấy token). Lý do: refresh token của Zalo dùng một lần, site dev/staging chép từ DB production mà làm mới sẽ làm hỏng token production. Muốn thử thật phải tắt sandbox có chủ đích và có người phụ trách chỉ định.

| Cấu hình | Dev | Staging | Production |
| --- | --- | --- | --- |
| `sandbox_mode` | Bật | Bật (tắt khi chạy thử với người nhận chỉ định) | **Tắt** |
| `zalo_skip_signature` | Có thể bật | Tắt | Tắt (bật = cổng từ chối) |
| `daily_token_budget` | Thấp | Thấp | Theo quyết định chi phí |
| Worker `omni` | 1 | 2 | ≥ 2; công suất ≈ số worker / 6 khách mỗi giây (docs/11 mục D1-11), tính theo tin/giây giờ cao điểm |
