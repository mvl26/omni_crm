# 09 · Công tắc và cấu hình

Mỗi phần có thể tắt **tức thì, không cần deploy, không mất dữ liệu**. Tắt một phần = phần đó ngừng hành động, nhưng tin vào vẫn được lưu để xử lý bù khi bật lại.

## 1. Bảng công tắc

| Công tắc | Ở đâu | Tắt thì | Đợt |
| --- | --- | --- | --- |
| `sandbox_mode` | Omni Settings | (Bật) không gửi gì thật ra ngoài | 1 |
| `active`, `shadow_only` | Omni Question Group | V2-06: `active = 0` ngừng nhóm (bot không thấy); `shadow_only = 1` giữ nháp trả lời của nhóm ở Shadow. Bỏ cả hai về mặc định (1/0) thì như v1 | 2 |
| `active` (dòng bảng điểm) | Omni Settings.score_rules | V2-06: `0` = quy tắc tắt, không xóa; `lead_scoring_enabled = 0` vẫn tắt mọi chấm điểm như v1 | 2 |
| `analyze_prompt_version`/`reply_prompt_version` | Omni Settings | V2-06: v2 chỉ chạy khi ghim; mặc định v1. Quay lui = đặt về `v1` | 2 |
| `enabled` | Omni Channel | Webhook vẫn trả 200 và **lưu** tin, không xử lý | 1 |
| `enabled` (kênh Telephony) | Omni Channel | Sự kiện cuộc gọi vẫn được lưu (200) nhưng không xử lý; `telephony_lookup` trả phản hồi "khách mới" rỗng, không tra DB | 2 |
| `outbound_enabled` | Omni Channel | Tin ra bị `Blocked` | 1 |
| `enabled` (kênh Telephony) — đối soát và việc gọi lại | Omni Channel | Đối soát đêm bỏ qua kênh; không tạo việc gọi lại (job dừng, bật lại thì tác vụ quét tạo bù) | 2 |
| `sandbox_mode` — tổng đài | Omni Settings | (Bật) đối soát đêm bỏ qua, không xác minh cuộc gọi nhỡ qua API nhà cung cấp | 2 |
| `missed_call_callback_minutes`, `callback_fallback_user`, `business_hours`, `holidays` | Omni Settings | Hạn gọi lại (phút làm việc), người nhận dự phòng, giờ làm việc, ngày nghỉ lễ (JSON `["2027-01-01", …]`, mặc định rỗng — việc cần người phụ trách điền) | 2 |
| `enabled` | Omni Lead Form | Biểu mẫu quảng cáo/website đó chỉ lưu, không tạo Lead | 1 (D1-15) |
| `enabled` (kênh Meta Lead Ads / Google Lead Form / Web Form) | Omni Channel | V2-05 (sửa theo review): kênh Meta/Google TẮT thì webhook trả 200, ghi log `kenh_tat` và KHÔNG lưu gì (khác kênh Email/Zalo/tổng đài vẫn lưu); Web Form tắt trả 404; đối soát Meta hằng giờ bỏ qua kênh tắt | 1 (D1-15) |
| `ads_silence_hours` | Omni Settings | V2-05: ngưỡng (giờ làm việc, mặc định 6) cảnh báo `khong_co_lead_quang_cao` khi có chiến dịch `Đang chạy` + kênh quảng cáo bật mà không có lead thật nào. Chưa từng lưu = 6. Tắt cảnh báo: chuyển hết chiến dịch khỏi `Đang chạy` hoặc tắt kênh | 1 (D1-15) |
| `webform_global_rate_per_hour` | Omni Settings | V2-05: trần toàn kênh Web Form mỗi giờ (mặc định 300, chưa lưu = 300); vượt thì 429 cho mọi IP và cảnh báo `web_form_vuot_tran` |
| `webform_accept_unknown_form` | Omni Settings | V2-05: 0 (mặc định) = form có mã chưa cấu hình Omni Lead Form bị bỏ qua, trả thành công giả; 1 = nhận, ghi chú "chưa cấu hình", KHÔNG tự giao sale |
| `webform_rate_limit`, `webform_rate_window_minutes` | Omni Settings | V2-05: giới hạn Web Form theo IP (mặc định 5 lần/10 phút; quá thì 429). Chưa từng lưu = mặc định | 1 (D1-15) |
| `webform_captcha_provider` | Omni Channel (Web Form) | V2-05: để trống = không kiểm tra chống bot (mặc định); Turnstile/reCAPTCHA = từ chối khi token sai hoặc nhà cung cấp lỗi | 1 (D1-15) |
| Nhắc quá hạn phản hồi lead quảng cáo | `dashboard_enabled` | V2-05: tác vụ 5 phút `response_sla.run` thoát ngay khi `dashboard_enabled` tắt; ngưỡng là `response_sla_minutes` | 1 (D1-15) |
| `prospect_import_enabled` | Omni Settings | Không chạy được nhập danh sách nhà thầu: `importer.import_workbook` và nút **Nhập**/**Chạy thử** từ chối, việc nền đã xếp hàng thành `Refused`. V2-02: trường + `flags.prospect_import_enabled()` (chưa từng lưu = bật); V2-03: importer. Tắt công tắc không xóa dữ liệu đã nhập | 1 (D1-14) |
| `fit_scoring_enabled` | Omni Settings | Không tính lại điểm phù hợp (giữ giá trị cũ): `fit.apply_fit` và tác vụ đêm `recompute_all_fit` thoát ngay. Chưa từng lưu = bật. Tham số `fit_*` chưa từng lưu rơi về mặc định sheet ThamSo; lần lưu Omni Settings đầu tiên điền mặc định | 1 (D1-13) |
| `dashboard_enabled` | Omni Settings | V2-04: `api.dashboard.snapshot` trả `{"enabled": false}` không số liệu, `save_care_log` từ chối; Hộp thư và dữ liệu không đổi. Chưa từng lưu = bật | 1 (D1-12) |
| `care_log_enabled` | Omni Settings | V2-04: worker/gửi tin/cuộc gọi không tạo hay kéo dài phiên chăm sóc, tác vụ 17:45 không báo; sale vẫn ghi tay được ở trang khác nếu `dashboard_enabled`. Chưa từng lưu = bật | 1 (D1-12) |
| `response_sla_minutes`, `care_session_gap_hours`, `team_lead_role` | Omni Settings | V2-04: ngưỡng quá hạn phản hồi (15), khoảng im lặng chia phiên (4 giờ), vai trò trưởng nhóm (Sales Manager). Chưa lưu hoặc <= 0 (số) dùng mặc định (`flags.care_setting`) | 1 (D1-12) |
| Khối Đợt 2-3 của Tổng quan | theo công tắc có sẵn | V2-04: Gọi nhỡ/dòng Điện thoại: có Omni Channel Telephony `enabled`; Lead nóng và lý do "Lead nóng": `lead_scoring_enabled` + bảng điểm không rỗng; Khiếu nại: `bot_analyze_enabled`; Bot hôm nay: `bot_reply_mode` != Off; Nháp chờ duyệt: = Draft; Phù hợp A/B + hạng x khu vực: `fit_scoring_enabled`. Tắt thì khối vắng mặt (không số 0) | 2-3 |
| `meta_token_check_disabled` | Omni Settings | V21-02: đặt 1 (tắt) thì tác vụ `token_check.run` (cron mỗi 6 giờ, mỗi kênh ~1 lần/ngày) thoát ngay, không gọi Graph `debug_token`; kết quả cũ trên kênh giữ nguyên. Chưa từng lưu hoặc 0 = kiểm (cực tính ngược vì Single không áp default JSON; đọc `flags.meta_token_check_disabled`). Kênh không có `meta_app_id`/App Secret/Page token chỉ ghi "Chưa kiểm", không gọi Graph | 1 (D1-15) |
| `meta_token_warn_days` | Omni Settings | V21-02: cảnh báo khi token Page Meta (hoặc quyền dữ liệu) còn <= N ngày (mặc định 7). Chưa từng lưu hoặc <= 0 = 7 (`flags.ads_setting`, đọc tabSingles). Cảnh báo `token_meta` cũng bắn khi token không hợp lệ hoặc thiếu `leads_retrieval`; `expires_at = 0` không cảnh báo hết hạn. Lỗi Graph tạm thời không cảnh báo, thử lại sau 6 giờ | 1 (D1-15) |
| `bot_analyze_enabled` | Omni Settings | Không gọi AI phân tích | 2 |
| `analyze_prompt_version`, `reply_prompt_version` | Omni Settings | **Ghim phiên bản câu lệnh** (V2-01, ĐQ-6): đường chạy thật chỉ nạp phiên bản ghim; để trống = bản mặc định trong mã (`loader.PINNED_DEFAULTS`, hiện `v1`). Tệp `*_v2.md` mới không có hiệu lực cho tới khi ghim. Giám khảo (`judge`) dùng hằng. Bộ đề chạy bản chỉ định qua `eval.run.run(prompt_versions=…)` để so sánh | 2 |
| `lead_scoring_enabled` | Omni Settings | Không tính điểm mới, không báo Lead nóng; `omni_score` và breakdown cũ giữ nguyên. Bảng điểm `score_rules` rỗng cũng coi như không chấm (không có điểm mặc định bịa). `hot_lead_threshold` (mặc định 60; <= 0 dùng 60) | 2 |
| `bot_reply_mode` = Off | Omni Settings | Không soạn trả lời (không enqueue `reply_batch`, `call_tool` ghi `Skipped Switch`). Shadow chỉ ghi `Omni Bot Draft` ngầm (sale không thấy). **Draft (D3-01)**: nháp hiện trong Hộp thư để sale Gửi/Sửa rồi gửi/Bỏ; bot vẫn không tự gửi, tin đi là `source=Sale`. Chuyển Draft -> Shadow/Off: nháp Pending cũ hết hạn sau 24 giờ hoặc khi khách nhắn tiếp, sale không còn thấy nút Gửi mới. Mặc định Off; **bật Draft ở môi trường thật chỉ sau khi cổng `nhap` được đo bằng AI thật trên bộ đề đã duyệt** (docs/08 mục 10) | 2 |
| `bot_paused_until` (kiểm tra) | Omni Identity | Bot ngầm không soạn nháp cho khách này đến thời điểm đó | 2 |
| `omni_auto_send_allowed` | **site_config.json** (ngoài DB) | Khóa hạ tầng: vắng/0 = KHÔNG ai bật được tự gửi và mọi đường gửi Bot bị chặn (kể cả nhóm đã bật, kể cả tin Bot đang chờ). Chỉ người vận hành đặt sau khi chủ dự án chốt Q1; không đặt trên dev/test | 3 |
| `auto_send_enabled` | Omni Question Group | Nhóm đó rơi về Draft (tin Bot đang chờ bị `Blocked`). Chỉ Omni Admin bật được, chỉ khi đạt điều kiện (docs/08 mục 5.5.2); tự tắt khi ngắt mạch | 3 |
| `recording_enabled` | Omni Settings | **CHƯA LÀM** (D3-04 bỏ qua, chờ cổng STT): trường chưa có trong DocType, `flags.recording_enabled()` luôn False. Khi làm: không tải ghi âm, không chuyển văn bản | 3 |
| `daily_token_budget` = 0 | Omni Settings | Bot dừng hẳn (tương đương tắt mọi AI); lần gọi ghi `Skipped Budget`. Site đã migrate bắt đầu ở 0 cho đến khi đặt hạn mức | 2 |
| `daily_token_budget` — tính chất | Omni Settings | **Hạn mức mềm**: `call_tool` giữ chỗ token ước lượng (~ký tự/3 + max_tokens) trước khi gọi rồi hiệu chỉnh theo số thật, nhưng các worker chạy song song vẫn có thể vượt khoảng (số worker × max_tokens) | 2 |
| `sandbox_mode` — với AI | Omni Settings | `call_tool` KHÔNG đọc sandbox_mode: sandbox chỉ áp chiều gửi ra khách. Lời gọi AI thật bị chặn bằng cổng Q11 (`synthetic` / `omni_ai_allow_customer_data`) | 2 |
| Ngắt mạch AI (tự động) | cache `omni:ai:breaker_open` | 5 lần lỗi liên tiếp → 5 phút mọi lần gọi ghi `Skipped Switch`; xóa khóa để mở lại ngay | 2 |
| site_config `omni_ai_allow_customer_data` | site_config | Không bật (mặc định) thì lời gọi API thật chỉ chạy với hội thoại tổng hợp (`synthetic=True`); chỉ bật sau khi Q11 được chốt | 2 |
| site_config `omni_ai_timeout_seconds` | site_config | Timeout mỗi lần gọi AI, mặc định 60 | 2 |
| site_config `omni_eval_judge_model` | site_config | Mô hình giám khảo của bộ đề (mặc định `claude-sonnet-5-5`); chạy bộ đề tay, không cần công tắc bot (`call_tool(eval_mode=True)`), vẫn chịu hạn mức token, ngắt mạch và Q11 | 2 |
| `bot_paused_until` | Omni Identity | Bot im với một khách | 3 |
| `alert_recipients` (trống) | Omni Settings | Không gửi thư cảnh báo (Notification Log cho Omni Admin vẫn tạo; không có công tắc riêng, xem docs/12 mục D) | 1 |
| `raw_payload_retention_days` = 0 | Omni Settings | Tác vụ đêm không xóa `raw_payload`. Tác vụ đêm xóa theo `creation` (không theo `modified`) và chỉ với tin đã xử lý xong (bỏ qua New/Processing/Retry) | 1 |

### 1.1 Phần không có công tắc riêng và cách tắt thay thế (V2-01, ADR 0009)

| Phần | Cách tắt thay thế |
| --- | --- |
| Cảnh báo (`monitoring/alerts.py`) | Để trống `alert_recipients` (không gửi thư; Notification Log cho Omni Admin vẫn tạo) |
| Theo dõi sửa tay Lead | Không tắt được riêng; hệ thống ghi qua `flags.omni_bot()` thì không bị tính là sửa tay |
| Trang Hộp thư (`omni-inbox`) | Gỡ vai trò khỏi trang; tin vẫn được lưu và xử lý |
| Bộ định danh | Tắt `enabled` của kênh: tin vẫn lưu, không xử lý |
| Đồng bộ lead Facebook có sẵn của app `crm` (`Lead Sync Source`) | **Không dùng** (V2-01, ĐQ-11): giữ mọi nguồn ở trạng thái tắt; nếu bật, màn giám sát nổ cảnh báo `dong_bo_lead_crm_dang_bat`. Lead từ Meta chỉ đi qua kênh `Meta Lead Ads` của Omni (V2-05) |

## 2. Đọc công tắc — `flags.py`

```python
def channel_enabled(channel: str) -> bool: ...
def outbound_enabled(channel: str) -> bool: ...
def sandbox() -> bool: ...
def bot_analyze_enabled() -> bool: ...
def bot_reply_mode() -> str: ...            # "Off" | "Shadow" | "Draft"
def group_auto_send(group: str) -> bool: ...   # khóa hạ tầng mở VÀ cờ nhóm VÀ không always_handoff; đọc DB không cache
def auto_send_infra_allowed() -> bool: ...  # site_config omni_auto_send_allowed, mặc định chặn
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
| `handoff_pause_hours` | Int | 24 (giờ bot im sau khi chuyển người; 0 = không im) |
| `handoff_message_in_hours` | Small Text | "Cảm ơn anh/chị đã liên hệ Miyano. Nhân viên sẽ phản hồi anh/chị sớm nhất có thể." (khác đặc tả gốc "trong ít phút", xem `docs/12` Q6) |
| `handoff_message_after_hours` | Small Text | "Cảm ơn anh/chị đã liên hệ Miyano. Nhân viên sẽ phản hồi anh/chị vào {gio_lam_viec_ke_tiep}." |
| `handoff_notify_channels` | Small Text | "system" (mặc định tạm, Q8). Giá trị hợp lệ: `system`, `email`, hoặc `system,email` (email chỉ khi chưa mở thông báo sau 10 phút) |
| `bot_active_hours` | Small Text (JSON như Giờ làm việc) | trống = theo Giờ làm việc (mặc định tạm, Q6); chỉ chế độ tự gửi D3-05 dùng |

Tắt tính năng: đặt `bot_reply_mode` về Off hoặc Shadow (không chuyển người, không im bot); đặt `handoff_pause_hours = 0` để chuyển người nhưng không im bot.

## 5. Bảng cấu hình theo môi trường

**`sandbox_mode` chặn MỌI HTTP ra Zalo** (không chỉ gửi tin): gửi tin tư vấn, tin yêu cầu thông tin, lấy hồ sơ khách, làm mới token (`refresh_if_needed` bỏ qua) và đổi mã cấp quyền OAuth (nút "Kết nối Zalo OA" vẫn chuyển hướng được nhưng callback không đổi code lấy token). Lý do: refresh token của Zalo dùng một lần, site dev/staging chép từ DB production mà làm mới sẽ làm hỏng token production. Muốn thử thật phải tắt sandbox có chủ đích và có người phụ trách chỉ định.

| Cấu hình | Dev | Staging | Production |
| --- | --- | --- | --- |
| `sandbox_mode` | Bật | Bật (tắt khi chạy thử với người nhận chỉ định) | **Tắt** |
| `zalo_skip_signature` | Có thể bật | Tắt | Tắt (bật = cổng từ chối) |
| `daily_token_budget` | Thấp | Thấp | Theo quyết định chi phí |
| Worker `omni` | 1 | 2 | ≥ 2; công suất ≈ số worker / 6 khách mỗi giây (docs/11 mục D1-11), tính theo tin/giây giờ cao điểm |
