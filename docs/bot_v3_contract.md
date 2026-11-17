# Giao ước chung K2–K7 (bot kịch bản v3) – 17/11/2026

Tài liệu này là hợp đồng giữa các phần việc chạy song song. **Không đổi tên/kiểu ở đây** mà không báo người điều phối.
Nguồn câu chữ: `docs/kich_ban_bot_miyano_v3.md`. Kế hoạch và khảo sát: `docs/bot_v3_plan.md`. Quy tắc: `CLAUDE.md`.

Người dùng đã chốt (17/11):
- Duyệt 3 DocType mới (Script Node/Button, Scheduled Message, Service Recipient). Ghi ADR `docs/adr/0032-bot-kich-ban-v3.md`.
- **Cập nhật cả CRM Lead Status gốc** theo bảng quy đổi ở `docs/bot_v3_plan.md` mục 6, ngoài trường `omni_journey_status`.
- Email (tài liệu, báo giá, nhắc, hỏi thăm 3 tháng): **tạo CRM Task cho nhân viên gửi tay**; phần tự gửi email để sau công tắc `script_email_enabled` (mặc định 0, chưa nối).
- Bot kịch bản **chỉ chạy cho danh sách thử**; `script_bot_enabled` mặc định 0.
- Không có người nhận mặc định cố định; bảng người nhận để trống, anh tự điền. Thiếu người nhận → người phụ trách Lead → không có thì Task không gán + cảnh báo (dùng `monitoring` có sẵn).
- Tư vấn y khoa: câu cố định C.3 rồi menu, không chuyển người.
- Chỉ Zalo OA. Messenger để sau (code Messenger của dev khác chưa vào repo – không động vào `channels/meta_messenger`).
- K7: chỉ khung + công tắc tắt; không có số giá nào, không nối ERP.

## 0. Ràng buộc gộp code (rất quan trọng)

Trên máy chủ, dev khác đang sửa dở 24 tệp; bản vá của họ: `~/frappe-bench/omni_shared/theirs_server.patch`.
Sau mỗi commit, trong worktree của mình chạy: `git apply --check ~/frappe-bench/omni_shared/theirs_server.patch` → **phải qua**.
Vì vậy:
- **KHÔNG sửa JSON** của `omni_settings`, `omni_identity`, `omni_outbound_message`, `omni_inbound_message`, `omni_channel`. Trường mới trên các DocType này và trên `CRM Lead` thêm bằng **Custom Field** trong `omni_crm/fixtures/custom_field.json` (module = "Omni CRM").
- Hạn chế tối đa sửa `pipeline/outbound.py`, `pipeline/worker.py`, `ingest/events.py`, `hooks.py`, `flags.py`, `crm_bridge/lead.py`, `api/inbox.py`. Nếu buộc phải sửa: hunk nhỏ, kiểm `git apply --check` như trên.
- Không sửa app `crm`/`frappe`. Không đụng `crm_frontend_patch` (giao diện Lead dùng Custom Field + panel sẵn có).

## 1. Test

Chỉ một site test `omni.test`, **không chạy song song**. Luôn chạy qua khóa:
```bash
cd ~/frappe-bench && export PYTHONPATH=<worktree_của_bạn>
flock /home/hoangvietyeuem/frappe-bench/omni_shared/test.lock bench --site omni.test run-tests --module omni_crm.tests.<module>
```
Migrate omni.test (khi thêm DocType/Custom Field) cũng qua khóa: `flock ... bench --site omni.test migrate`.
Không bao giờ chạy gì trên site `miyano`. Không gọi AI thật (dùng `FakeAIClient`). `sandbox` luôn bật trong test.

## 2. DocType mới (module Omni CRM, `omni_crm/omni_crm/doctype/...`)

### Omni Script Node (autoname `field:code`)
| field | type | ghi chú |
| --- | --- | --- |
| code | Data, unique, reqd | `GREETING`, `MENU_VATTU`, `TIMHIEU_SPD`, … (khớp mã nút trong kịch bản, không có `#`) |
| title | Data | nhãn cho Truyền thông |
| step | Select `1\n2\n3\n4\n5\nC` | bước kịch bản |
| service | Select `\nVATTU\nSUPPLYCORE\nSPD` | |
| channel | Select `Tất cả\nZalo OA\nMessenger` | mặc định Tất cả |
| status | Select `Đang dùng\nCần duyệt\nTắt` | mặc định Đang dùng; "Cần duyệt" chỉ gửi cho danh sách thử |
| text | Long Text | có biến `{ten}`, `{dich_vu}`, `{ngay_lam_viec_tiep_theo}`… ; trống = ô kịch bản chưa có → không gửi |
| buttons | Table → Omni Script Button | tối đa 5 (Zalo) |
| action | Data | hành động sau khi gửi (tùy chọn): `kb_answer`, `handoff`, `collect:<field>`, `demo`, … |
| source_ref | Data | số mục trong kịch bản, vd `1.1`, `2.3` |
| notes | Small Text | |

### Omni Script Button (istable)
| field | type | ghi chú |
| --- | --- | --- |
| label | Data, reqd | ≤ 20 ký tự (validate) |
| target | Data, reqd | mã node đích (không `#`); payload gửi Zalo = `#` + target |

### Omni Scheduled Message (autoname hash)
| field | type |
| --- | --- |
| kind | Select `followup_docs\nfollowup_quote\ndemo_remind_1d\ndemo_remind_1h\ndemo_noshow\nsilence_3d\npostdemo_1\nlost_3m` |
| identity | Link Omni Identity |
| lead | Link CRM Lead |
| due_at | Datetime, reqd, index |
| status | Select `Pending\nSent\nCancelled\nSkipped\nFailed`, index |
| dedupe_key | Data, unique | `kind:lead:mốc` |
| node | Data | mã node sẽ gửi |
| payload | JSON |
| sent_at | Datetime |
| reason | Small Text | lý do Skipped/Cancelled/Failed |

### Omni Service Recipient (istable, dùng trong Omni Settings qua Custom Field Table `script_recipients`)
| field | type |
| --- | --- |
| service | Select `VATTU\nSUPPLYCORE\nSPD\nKHAC\nDEMO_SUPPLYCORE\nDEMO_SPD\nTUAN_THU\nQUAN_LY` |
| primary_user | Link User |
| backup_user | Link User |

## 3. Custom Field (fixtures/custom_field.json)

**Omni Settings** (section "Bot kịch bản v3"): `script_bot_enabled` Check 0 · `script_test_identities` Small Text (mỗi dòng 1 tên Omni Identity) · `script_auto_send_kb_for_test` Check 0 · `script_fast_timers_for_test` Check 0 · `script_recipients` Table Omni Service Recipient · `script_email_enabled` Check 0 · `script_zns_enabled` Check 0 · `script_zns_template_id` Data · `script_quote_enabled_vattu` / `_supplycore` / `_spd` Check 0 · `script_quote_review_required` Check 1 · `script_privacy_url` Data (mặc định trống) · `script_docs_list` Small Text.

**Omni Identity**: `script_node` Data · `script_step` Data · `script_reask_count` Int · `script_greeted_at` Datetime · `script_last_bot_at` Datetime · `script_state` JSON (trạng thái thu thông tin: nhánh 3a/3b, câu đang hỏi, số lần sai…).

**Omni Outbound Message**: `script_node` Data · `buttons` JSON (danh sách `{label, target}` đã gửi).

**CRM Lead** (section "Hành trình bot"): `omni_services` Small Text (mã dịch vụ, phẩy) · `omni_journey_status` Select (Mới, Đang tư vấn, Đã gửi tài liệu, Đã gửi báo giá, Đã hẹn demo, Đã demo, Thương lượng, Đồng ý mua – chờ ký hợp đồng, Chăm sóc lại, Thua, Không liên hệ) · `omni_journey_need` Small Text · `omni_docs_sent_at` / `omni_quote_sent_at` Datetime · `omni_demo_at` Datetime · `omni_demo_mode` Data · `omni_demo_attendees` Int · `omni_demo_result` Select (\nĐã demo\nKhách vắng\nHoãn) · `omni_demo_note` Small Text · `omni_no_bot_followup` Check · `omni_do_not_contact` Check · `omni_data_consent_at` Datetime · `omni_unanswered_questions` Long Text · `omni_lost_reason_text` Small Text.

## 4. Module `omni_crm/script/` (gói mới)

| tệp | người làm | API công khai |
| --- | --- | --- |
| `switch.py` | Nền | `enabled_for(identity: str) -> bool` (bật toàn bộ, hoặc identity nằm trong `script_test_identities`); `is_test(identity) -> bool` |
| `store.py` | Nền | `get_node(code) -> dict|None` ({code,text,buttons,status,action,service,step}); `render(node, ctx) -> (text, buttons)`; node `Tắt`/text trống → None; `Cần duyệt` → None nếu không phải danh sách thử |
| `send.py` | Nền | `send_node(identity, code, ctx=None, *, purpose="reply") -> str|None` – qua `outbound.queue_outbound` (đường gửi chung, Zalo qua `pick_message_kind`), `source="Bot"`, ghi `script_node`/`buttons`; chặn nếu `not switch.enabled_for`. `send_text(identity, text, buttons=None, code=None)` cho câu của Kho tri thức |
| `state.py` | Nền | `get(identity) -> dict`, `set_node(identity, code, step=None)`, `bump_reask`, `reset_reask`, `add_service(lead, svc)`, `set_journey(lead, status)` (cập nhật cả `CRM Lead.status` theo bảng quy đổi) |
| `router.py` | Nền (khung) + K2 (luồng) | `handle_batch(batch_id) -> bool` (True = bot kịch bản đã xử lý, lượt Reply cũ không chạy); `register(code_or_prefix, handler)`; handler ký hiệu `handler(ctx: RouteContext) -> None` |
| `classify.py` | K2 | AI phân loại tin gõ → 1 mã trong danh sách đóng + confidence (qua `ai_client.call_tool`, nội dung khách là dữ liệu) |
| `timeutil.py` | K3 | `in_hours(at)`, `next_working_day(at)`, `next_working_day_text(at)`, `cutoff_1630(at)`, `in_proactive_window(at)` (09:00–16:30), đọc `business_hours`, `holidays` |
| `tasks.py` | K3 | `recipient(service, role="primary") -> str|None`; `create_task(lead, service, title, description, due=None, priority="Medium") -> str` (CRM Task, assigned theo bảng người nhận → người phụ trách Lead) |
| `flows/handoff.py` | K3 | Gặp nhân viên, tự chuyển, tạm dừng/trả lại, dừng hẳn |
| `flows/collect.py` | K4 | thu thông tin 3a/3b, kiểm email/SĐT (3.6), giao việc 3.5 |
| `scheduler.py` | K5 | `run_due()` mỗi 15 phút; `schedule(kind, identity, lead, due_at, node, payload)`; `cancel_for(lead, kinds=None)`; `pick_channel(...)` (bảng 4.2, hàm thuần) |
| `flows/demo.py` | K5 | đặt lịch demo 4.4, nhắc, đổi/hủy/vắng |
| `flows/postdemo.py` | K6 | 5.1–5.6 |
| `flows/quote.py` | K7 | khung, công tắc, chưa có số |

`RouteContext` (dataclass trong `router.py`): `batch_id, identity, lead, channel, text (tin khách cuối, đã gộp), payload_code (mã nút nếu khách bấm: text bắt đầu `#` và nằm trong danh sách mã), attachments, is_first_contact, state (dict)`. Handler gọi `send.send_node(...)` / `state.*` / `tasks.*`.

Mã nút đặc biệt: `#GAP_NHANVIEN` → K3 handoff; `#MENU` hoặc khách gõ "menu" → `MENU_CHINH`.

## 5. Gắn vào luồng tin

- Điểm gắn: đầu `bot/reply.py::_reply_batch` (tệp của mình, không dirty): nếu `script.router.handle_batch(batch_id)` trả True thì dừng (status `"script"`). Không sửa `pipeline/worker.py`.
- Sự kiện quan tâm OA (`follow`) → router gửi `GREETING` (kiểm cách adapter Zalo đang ghi sự kiện follow).
- `channels/zalo/phone_request.on_batch_done`: identity đang chạy bot kịch bản thì bỏ qua (K2).
- Lịch 15 phút (K5): thêm 1 dòng `"*/15 * * * *": ["omni_crm.script.scheduler.run_due"]` vào `hooks.py` (hunk nhỏ, kiểm apply).

## 6. Gửi tin kèm nút (Zalo)

Đã kiểm thật 17/11 (xem `docs/bot_v3_plan.md` mục 3): `POST /v3.0/oa/message/cs`, `message={text, attachment:{type:"template", payload:{buttons:[{title, type:"oa.query.show", payload:"#CODE"}]}}}`; 5 nút được nhận; nút chỉ hiện trên app điện thoại; khách bấm → `user_send_text` với text = payload. Nối trong `channels/zalo/adapter.py` (không dirty): nếu Outbound có `buttons` thì gửi kiểu template. `# VERIFY:` cho giới hạn ký tự tiêu đề nút.
