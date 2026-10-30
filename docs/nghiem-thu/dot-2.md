# Nghiệm thu Đợt 2 (D2-09)

Ngày lập: 2026-10-30. Người lập: agent kiểm thử D2-09. Nhánh `d2-09-kiem-thu-dot-2` (từ `develop`, nền 816 test OK skipped=4). App `omni_crm`, site test `omni.test`.

## 0. Kết luận và khuyến nghị

Tách làm hai phần vì chúng có điều kiện khác nhau.

**A. Kênh tổng đài (nhật ký cuộc gọi, Lead từ cuộc gọi, gọi nhỡ -> việc gọi lại, đối soát đêm): "Phát hành sau khi" xong S1 (chữ ký/khóa ký Stringee) và các việc ở 9.1 mục 1-8, thử trên staging có tổng đài thử.** Mã đã đạt các tiêu chí đo được trên máy test sau khi sửa 3 lỗi tìm thấy ở D2-09 (mục 6): 1.200 cuộc gọi trong 2 phút (10 cuộc/giây) cho 150 số: 0 mất, 0 trùng, 150 Lead = 150 số, 1.200 Call Log, mỗi khách chỉ 1 việc gọi lại mở, webhook p95 ~18 ms.

**B. Bot (phân tích, chấm điểm, bot trả lời chạy ngầm): "Chưa phát hành".** `bot_analyze_enabled` giữ **0**, `bot_reply_mode` giữ **Off** (Shadow cũng gửi hội thoại khách sang AI nên cũng bị chặn). Lý do:
1. **Q11 chưa chốt** nên mọi lần gọi AI trên dữ liệu khách thật bị chặn bởi mã (`call_tool`).
2. **Cổng `phan_tich` (trường đúng >= 90%) CHƯA ĐO.** Chưa có khóa API, chưa có bộ đề đã duyệt (220 ca đều Synthetic và chưa duyệt, 0 ca Real), chưa có lần chạy bằng AI thật. Số "100%" của lần chạy Fake là của client giả trả đúng đáp án, **không phải điểm của bot**.
3. Cổng có nguy cơ trượt vì che SĐT/email (docs/12 mục D, D2-08): 20 ca nhóm "trích thông tin" đều có SĐT.
4. Chưa có sale dùng thật: chưa có chỉ số "gọi nhỡ được gọi lại đúng hạn", "trường trích xuất đúng" hay báo cáo "Bot ngầm" của tuần chạy thử.

**Giới hạn của bản nghiệm thu này (đọc trước):**

1. **Không có staging, không có tổng đài, không có Claude thật.** Mọi số đo chạy trên `omni.test` (máy dev 16 nhân dùng chung với 3 bench khác, MariaDB cục bộ) với gunicorn 8 worker riêng ở cổng 8099, Redis tạm cổng 11098, 8 worker RQ chạy từ `tests/load/rq_worker.py` (không phải supervisor), không nginx, HTTPS, mạng thật. Số đo là cận dưới về độ trễ mạng; không chứng minh gì về staging/production.
2. **Stringee hoàn toàn là giả.** Kênh tải dùng nhà cung cấp Stringee thật của mã (không dùng FakeProvider nên không cần `developer_mode`), `sandbox_mode` bật (không HTTP ra ngoài), khóa/token ngẫu nhiên. Chữ ký test ký bằng đúng công thức trong code (HMAC-SHA1 base64) nên **không chứng minh gì về S1** (khóa ký thật có phải `telephony_api_key_secret` không), S2-S7.
3. **AI hoàn toàn là giả.** `FakeAIClient` (trả kết quả tối thiểu hợp lệ, hoặc lỗi/hết hạn mức theo kịch bản). Các số về bot đo **hạ tầng** (hàng đợi, khóa, ghi Bot Run, ngắt mạch, cảnh báo); độ trễ AI thật, độ chính xác và chi phí token **chưa đo**.
4. **Redis queue của bench (cổng 11000) hỏng** (đã biết, không đụng vào; còn bị `bench start` khởi động lại liên tục khi đo). 4 test tích hợp vẫn bị skip vì thế; tải/sự cố chạy trên Redis tạm. `sites/omni.test/site_config.json` được trỏ tạm sang Redis tạm trong lúc đo và **đã khôi phục y nguyên, so `cmp` giống hệt bản sao lưu**; `developer_mode` không đặt.
5. **Chưa có số liệu tuần chạy thử** (cần sale thật): "gọi nhỡ gọi lại đúng hạn", "trường trích xuất đúng" (số chính thức), báo cáo Bot ngầm: để trống ở mục 4.
6. **Không thử** Stringee lỗi HTTP thật (đối soát đêm với API thật, `verify_missed_call`, JWT thật), ghi âm/STT (Đợt 3), giao diện Hộp thư/Lead trên trình duyệt (không có trình duyệt trong phiên; chỉ kiểm API).

## 1. Kết quả test tự động

Lệnh: `cd /home/hoangvietyeuem/frappe-bench && bench --site omni.test run-tests --app omni_crm` (ruff 0.8.1: `check` và `format --check` sạch).

```
FULL_RESULT_PLACEHOLDER
```

Test mới của D2-09: `test_dot2_integration` 16 test (C-01, C-02, C-04, B-01...B-05 và 3 test đồng thời bên dưới), `test_measure_dot2` 7 test. Test đồng thời (`TestConcurrentCallCreate`, `TestConcurrentCallbackJobs`, `TestCallbackJobRunsAsGuest`) **đỏ trước khi sửa** (ghi ở mục 6). `bench --site omni.test migrate`: xem mục báo cáo cuối. Dữ liệu tải đã dọn sạch (0 Lead/Call/Task/Identity/kênh `LOAD*` còn lại), Omni Settings khôi phục đúng giá trị trước đo.

## 2. Bảng ánh xạ kịch bản Đợt 2

`docs/10` mục 3 chỉ liệt kê I-01...I-13 của Đợt 1; kịch bản Đợt 2 lấy từ prompt D2-09 (C-xx, B-xx) và từ bảng sự cố `docs/10` mục 5. Đối chiếu theo **nội dung assert**, không theo tên test. Test mới nằm ở `omni_crm/tests/test_dot2_integration.py`.

| # | Kỳ vọng | Test chính | Test bổ trợ | Ghi chú |
| --- | --- | --- | --- | --- |
| C-01 | Gọi đến nghe máy -> Lead + Call Log + hiện trong Hộp thư | `test_dot2_integration.TestC01C02Telephony.test_c01_answered_call_creates_lead_call_log_and_shows_in_inbox` (webhook -> worker -> Lead, Omni Call, CRM Call Log, tab Calls của CRM, `get_thread`, `list_conversations`) | `test_telephony.TestWorkerFlow.test_answered_inbound_creates_lead_call_and_call_log`, `TestInboxPermissionsMonitor.test_thread_shows_call_rows` | Mới (đi hết chuỗi). Tải: 1.200 cuộc, 1.200 Call Log, 150 Lead = 150 số |
| C-02 | Gọi nhỡ 3 lần -> 1 việc gọi lại, đúng hạn giờ làm việc | `...test_c02_three_missed_calls_one_task_due_inside_business_hours` (17:20/17:25/17:29 thứ sáu -> hạn 08:20 thứ bảy, `is_business_time`) | `test_callbacks.TestCallbackTask.test_three_missed_one_task`, `test_missed_call_creates_one_task_with_owner_and_due`, `TestBusinessHours.*` | Mới. **Tải bắt được 2 lỗi mà các test này không thấy** (mục 6, E6 và E7), nay có `TestConcurrentCallbackJobs`, `TestCallbackJobRunsAsGuest` |
| C-03 | Đối soát bù cuộc gọi thiếu | `test_callbacks.TestReconcile.test_fills_exactly_two_missing_and_rerun_adds_none` | `test_filled_calls_flow_through_worker`, `test_alert_when_filled_over_one_percent`, `test_provider_error_is_caught_alerted_and_recorded`, `test_max_pages_raises_in_real_stringee_provider`, `test_sandbox_and_disabled_channel_are_skipped` | Có sẵn, khớp. **Chưa kiểm với API Call log thật** (S3) |
| C-04 | Khách gọi trước, sau đó nhắn Zalo gửi SĐT -> cùng Lead | `...TestC04CallThenZalo.test_c04_call_then_zalo_form_with_same_phone_is_one_lead` (1 Lead, 0 nghi trùng, hai định danh cùng Lead) | `test_resolver.test_email_signature_phone_attaches_to_existing_lead` (kênh khác) | Mới. Không có lỗi |
| B-01 | Bot điền trường có căn cứ; sale đã sửa thì không ghi đè | `...TestB01B02AnalyzeAndScore.test_b01_b02_fields_filled_with_evidence_sale_edit_kept_hot_once` (webhook -> worker -> `analyze_batch`; sale sửa qua form Lead; tin sau không ghi đè, `skipped_manual`) | `test_b01_fabricated_value_without_evidence_not_written`; `test_bot_analyze.TestAnalyzeBatch.*`, `TestValueMatchesEvidence.*` | Mới. FakeAIClient |
| B-02 | Điểm đúng bảng điểm; báo Lead nóng một lần | cùng test trên: 25 + 15 + 15 + 10 = 65, 1 Notification Log, tin thứ hai không báo lại | `test_scoring.TestComputation.test_five_signal_sets_match_hand_calculation`, `TestHotLead.test_notified_once_with_reasons_and_no_pii`, `test_oscillation_around_threshold_does_not_renotify` | Bảng điểm là mẫu `RULES` của test, **không phải bảng đã duyệt (Q5)** |
| B-03 | Bot trả lời chỉ ở Shadow; không có Outbound source=Bot | `...TestB03Shadow.test_b03_shadow_draft_pending_and_nothing_sent_to_customer` (nháp `Shadow/Reply/Pending`, 0 Outbound, 0 HTTP Zalo) | `test_bot_reply.TestNoBotOutbound.test_no_outbound_in_any_mode`, `test_no_code_path_creates_bot_outbound`. Tải: 0 tin ra trên mọi Lead sau 3 pha có bot | Mới |
| B-04 | Hết hạn mức token -> bot dừng, kênh vẫn chạy | `...TestB04BudgetExhausted.test_b04_budget_exhausted_bot_stops_channel_keeps_running` (3 khách, Lead + dòng thời gian đủ, Bot Run `Skipped Budget`, 0 lần gọi AI, cảnh báo đúng 1 lần, bật lại hạn mức bot chạy tiếp) | `test_ai_client.TestCallTool.test_budget_exceeded_skips_and_alerts_once`, `test_reservation_*`. Tải: 441 request, 2.200 tin Done, 121 lượt `Skipped Budget` | Mới (end-to-end) |
| B-05 | AI lỗi hàng loạt -> lượt xử lý vẫn Done, cảnh báo bật | `...TestB05AIFailsEverywhere.test_b05_ai_fails_100_percent_pipeline_done_breaker_opens_alert_raised` + `TestBreakerAlertRule` | `test_ai_client.test_circuit_breaker_opens_and_resets`, `test_bot_reply.test_ai_failure_no_draft_no_exception`. Tải: mục 5 | Mới. **Bắt được lỗi E8**: cảnh báo `ai_loi_nhieu` bị pha loãng, thêm `ai_ngat_mach` |
| S-1 | Webhook tổng đài token sai | `test_telephony.TestWebhookSecurity.test_wrong_missing_or_short_token_rejected`, `test_stringee.TestSignature.test_token_still_required` | Tải: 28 + 29 request sai token -> 401, không tạo bản ghi | Đạt. Lưu ý: bỏ header chữ ký thì chỉ còn token (docs/12 mục D D2-02) |
| S-2 | Stringee gửi lại sự kiện | `test_stringee.TestSignature.test_webhook_bad_signature_401_good_200_and_flow`, `test_telephony.TestEvents.test_duplicate_final_is_one_record` | Tải: 112 + 114 request gửi lại -> 0 Omni Call trùng (1.200 = 1.200) | Đạt |
| S-3 | Tra cứu khi DB chậm | `test_telephony.TestLookup.test_database_enforces_statement_time_limit`, `test_slow_step_hits_budget_and_returns_in_time`, `test_db_error_returns_valid_response` | Mục 5: khóa đọc 25 giây | Đạt (p95 ~0,5 giây khi bị khóa) |
| S-4 | Chèn lệnh (injection) | `test_bot_analyze.test_prompt_injection_is_data`, `TestHardening.*`; `test_bot_reply.test_customer_instruction_stays_in_data_tags`, `test_injection_with_money_blocked`, `TestGuards.test_more_sensitive_and_injection_keywords` | Bộ đề: 10 ca "chèn lệnh" (chỉ chạy bằng Fake) | Đạt ở mức mã; **chưa kiểm với mô hình thật** |
| S-5 | AI lỗi/timeout (docs/10 mục 5, "Chưa kiểm được" của Đợt 1) | B-05 + mục 5 | | Đã kiểm với FakeAIClient |

Bảng sự cố hạ tầng (Redis tắt, DB chậm, kill worker) ở mục 5.

## 3. Kết quả tải

Công cụ mới: `omni_crm/tests/load/send_calls.py` (webhook cuộc gọi Stringee ký HMAC-SHA1, open-loop, gửi lại, sai token), `send_lookups.py` (tra cứu khi chuông reo), `hold_read_lock.py` (khóa ĐỌC), `rq_worker.py` (worker RQ trên Redis của site, tùy chọn `--fake-ai` với tệp điều khiển `ok|fail|slow:<giây>`), `verify_calls.py` (chuẩn bị, đối chiếu, báo cáo bot, dọn). Dùng chung `send_burst.py`/`verify_burst.py` của Đợt 1 cho Zalo. Môi trường: mục 0.

Giả định tải: **chưa có số cuộc gọi/ngày thật**. Dùng 10 cuộc/giây x 2 phút = 1.200 cuộc cho 150 số (gấp hàng trăm lần mức ngày cao điểm của một đội sale; 35% nhỡ, 10% gửi lại).

### 3.1 Cuộc gọi: trước và sau khi sửa lỗi (cùng kịch bản, 8 worker)

| Mục | Lần 1 (trước sửa) | Lần cuối (sau 3 bản sửa) |
| --- | --- | --- |
| Request / mã trả về | 1.340: 1.312 x 200, 28 x 401 (sai token) | 1.343: 1.314 x 200, 29 x 401 |
| Webhook (chỉ 200) | p50 13,9 ms, p95 19,3, p99 26,3, max 99,1 | p50 14,2 ms, p95 **17,8**, p99 23,7, max 217,3 |
| Tin cuộc gọi lưu / gửi | 1.200 / 1.200 | 1.200 / 1.200 (0 mất, 0 thừa) |
| Tin Done sau khi gửi xong | 1.105 xong ngay, 89 xong sau 1 lần thử lại, **6 vẫn Retry sau 15+ phút** (95 tin = 7,9% gặp deadlock 1213) | **1.200 Done, 0 thử lại**, cạn sau ~95 giây |
| Omni Call / Call Log | 1.194 / 1.194 | 1.200 / 1.200 |
| Lead / số điện thoại | 150 / 150, 0 số có 2 Lead | 150 / 150, 0 số có 2 Lead |
| Cuộc nhỡ / có việc gọi lại do **job** tạo | 407 / **12** (395 job lỗi, còn lại chờ tác vụ quét) | 411 / 411 sau khi job cuối chạy xong (8 cuộc còn đang chạy lúc đếm đầu, 0 sau đó) |
| Việc gọi lại / khách có nhỡ | 144 / 140 | **140** / 141 (đếm lúc 8 cuộc nhỡ còn đang chạy; 0 cuộc thiếu việc sau khi cạn) |
| Khách có hơn 1 việc gọi lại đang mở | 2 | **0** (giữa hai bản sửa từng có 53) |
| Nhận -> Done | p50 56 s, p95 145 s, max 529 s | p50 51 s, p95 89 s, max 93 s |

Con số "Nhận -> Done" ~50 giây là backlog do chỉ 8 worker, mỗi lượt giữ worker ~6 giây (debounce 5 giây dùng chung với Zalo, kể cả cuộc gọi, xem E9). Công suất xấp xỉ `số_worker / 6` khách/giây như `docs/11` (D1-11).

### 3.2 Zalo + bot (FakeAIClient, bot ngầm bật)

| Pha | Gửi | Kết quả |
| --- | --- | --- |
| Hồi quy Đợt 1, bot **tắt**: 30 tin/giây x 120 giây, 300 khách, 10% gửi lại | 3.600 duy nhất / 3.989 request | 3.989 x 200, webhook p50 13,1 / p95 **20,6** / p99 26,1 / max 49,0 ms. Tích lũy trong DB: 5.800 tin vào = 5.800 mong đợi (0 mất, 0 trùng `dedupe_key`), 500 Lead = 500 khách, 0 định danh có 2 Lead, 0 nghi trùng. Cạn hàng đợi sau 195 giây (8 worker); nhận -> Done p50 72 s, p95 190 s, max 202 s |
| Bot **bật** (phân tích + chấm điểm + Shadow, kho tri thức rỗng): 20 tin/giây x 60 giây, 200 khách | 1.200 / 1.326 | 1.326 x 200, p95 17,7 ms. 1.200 Done, 200 Lead = 200 khách. 275 lượt `Analyze: Success`, 275 nháp `Shadow:Handoff:Pending` (kho rỗng nên không gọi AI soạn), 72 Lead có điểm, **0 tin ra**, 0 Error Log |

Kết luận hồi quy: Đợt 1 không suy giảm (so D1-11: webhook ~16 ms, 0 mất, 0 trùng). So sánh thời gian cạn với D1-11 không có ý nghĩa vì khác số worker (8 so với 4/12) và khác tốc độ.

### 3.3 Tra cứu khi chuông reo (`telephony_lookup`)

| Điều kiện | Kết quả |
| --- | --- |
| 400 yêu cầu, 20 song song, 5 số (3 khách có Lead, 1 số lạ, 1 số hỏng) | 400 x 200; 240 "tìm thấy"; p50 26,8 ms, p95 35,3, p99 40,9, max 54,3 |
| `LOCK TABLES Omni Identity, CRM Lead WRITE` 25 giây (chặn cả đọc), 60 yêu cầu, 10 song song | **60 x 200 với phản hồi "khách mới" hợp lệ**, 0 tìm thấy (do khóa); p50 265 ms, p95 506,5, max 517,4; 4/60 vượt 500 ms. Không treo 25 giây (ngân sách 0,3 giây của mã + 200 ms chờ do gunicorn 8 worker phục vụ 10 song song) |

## 4. Chỉ số mục tiêu Đợt 2 (docs/00 mục 2) và cách đo

Script `omni_crm/tests/acceptance/measure_dot2.py` (chỉ đọc, có test `test_measure_dot2`):

```bash
bench --site <site> execute omni_crm.tests.acceptance.measure_dot2.run \
  --kwargs '{"start": "2026-11-09 00:00:00", "end": "2026-11-16 00:00:00"}'
```

`start`/`end` theo múi giờ của site (trên omni.test lệch 1,5 giờ so với đồng hồ máy). Mặc định bỏ dữ liệu giả (tiền tố LOAD, PLT, TD, T05, D209, DEMO, RV, MD, `call-vn-1-FAKE`).

| Chỉ số | Ngưỡng | Cách đo | Đo được |
| --- | --- | --- | --- |
| Gọi nhỡ được gọi lại đúng hạn | >= 80% | Mẫu = mỗi CRM Task gọi lại tạo trong kỳ. Đúng hạn = có cuộc gọi **nghe máy** (đến hoặc đi) SAU lần nhỡ cuối của việc và trước `due_date`; việc sale đóng tay không có cuộc gọi thì tính theo `modified <= due_date`; việc mở quá hạn là trễ; việc mở chưa đến hạn không tính. Cuộc gọi ẩn số không có việc và nằm ngoài mẫu số (script in riêng) | chưa đo (cần tuần chạy thử) |
| Trường trích xuất đúng | >= 90% | **Số chính thức: cổng `phan_tich` của bộ đề đã duyệt chạy bằng AI thật** (`bot/eval/run.py`); script chỉ in lần chạy gần nhất và đánh dấu `counts_as_measurement` = đúng khi (AI thật và bộ đề đã duyệt). Từ DB chỉ có **đại diện**: tỉ lệ giá trị bot điền mà sale không sửa sau đó. Không phải độ đúng (sale không sửa != đúng) | chưa đo |
| Câu trả lời đúng của bot (>= 85%) | | Thuộc cổng `nhap` (Đợt 3) | chưa đo, ngoài phạm vi Đợt 2 |
| Báo cáo "Bot ngầm" (tỉ lệ nháp gần giống câu sale theo nhóm) | | `Omni Bot Draft` + báo cáo ở `bot/reply.py` (D2-07) sau 1 tuần | chưa đo (cần Q11 + sale) |
| Các chỉ số Đợt 1 | | `measure_dot1.py` | xem `dot-1.md` mục 4 |

## 5. Kết quả sự cố

Cùng môi trường tải (mục 0); không `sudo`, không đụng Redis 11000/13000.

| Sự cố | Cách tạo | Kết quả | Đạt? |
| --- | --- | --- | --- |
| AI lỗi 100% (B-05), bot bật, Shadow | Tệp điều khiển `fail`: mọi lần gọi `FakeAIClient` ném lỗi HTTP 400 giả. 20 tin/giây x 30 giây, 100 khách | 668 x 200; **tích lũy 1.800 tin vẫn Done**; Bot Run: **5 `Error` rồi 131 `Skipped Switch`** (ngắt mạch mở sau 5 lỗi liên tiếp); 136 nháp `Shadow:Handoff:Pending` (kho rỗng, không gọi AI); 0 tin ra; điểm Lead vẫn tính (105 Lead có điểm). Snapshot: `breaker_open = true`, `errors_24h = 5` trên `runs_24h = 764` (0,65%): **`ai_loi_nhieu` không bật** (ngưỡng 5%, tỉ lệ bị pha loãng), cảnh báo mới `ai_ngat_mach` bật | Đạt sau khi sửa E8 |
| Hết hạn mức token (B-04) | `daily_token_budget = 1`; 20 tin/giây x 20 giây, ~100 khách | 441 x 200; tích lũy 2.200 tin Done; 121 lượt `Skipped Budget`, 0 lần gọi AI; 0 tin ra; snapshot `usage_pct` > 80% nên `ai_gan_han_muc` bật. Thư `ai_het_han_muc` (1 lần/ngày) chỉ kiểm bằng test có mock (`deliver_now`), **không** quan sát gửi thật | Đạt |
| Redis queue tắt 62 giây (cuộc gọi) | `redis-cli shutdown nosave` 07:45:25, bật lại 07:46:27 trong khi gửi 10 cuộc/giây x 90 giây (900 cuộc, 100 số) | **996 request x 200, 0 mất** nhưng p50 **210 ms**, p95 226 ms (so 14 ms): mỗi request chờ ~200 ms để thử nối Redis. Worker RQ của script thoát (nay có vòng nối lại; production dùng supervisor). Sau khi Redis lên: 28 tin Done, **872 tin `New`**; tác vụ quét (`requeue_stuck` chạy tay) cần 2 lượt (lượt đầu chỉ thấy 50 khách có tin quá 2 phút; cron 5 phút = tối đa ~10 phút); **cả 900 Done lúc 07:48:50** (+2 phút 23 giây). 7 job việc gọi lại đã mất cùng Redis; `sweep_pending` bù sau (mỗi lượt tối đa 100 cuộc, tồn đọng cũ chiếm chỗ nên 3 lượt) | Đạt về dữ liệu (0 mất, 0 trùng); phục hồi phụ thuộc scheduler |
| Stringee gửi lại | 10% gửi lại đúng payload và chữ ký | 112 + 114 request gửi lại -> 0 bản ghi trùng | Đạt |
| Webhook tổng đài token sai | 2% request sai token | 57 x 401, không bản ghi, Error Log giới hạn 1 dòng/phút/kênh | Đạt |
| Tra cứu khi DB chậm | Mục 3.3 | Không treo; p95 ~0,5 giây khi đọc bị khóa 25 giây | Đạt |
| Worker bị kill giữa lượt | **Không chạy lại ở D2-09** | Đợt 1 đo ~15 phút phục hồi (E3 của dot-1.md, vẫn mở) | Chưa kiểm lại |
| Webhook chờ khóa DB ghi | **Không chạy lại ở D2-09** | E2 của dot-1.md vẫn mở (cùng đường `save_inbound`) | Chưa kiểm lại |
| Stringee API lỗi thật / token JWT hết hạn | Không có tài khoản | Chỉ test mock (`test_stringee`, `test_callbacks.TestVerifyOutsideLock`, `TestReconcile`) | Chưa kiểm với hệ thống thật |
| Token Zalo hết hạn | | Như `dot-1.md` | Chưa kiểm với hệ thống thật |

## 6. Lỗi tìm thấy

Mức: Chặn / Cao / Trung bình / Thấp. Mọi lỗi dưới đây **tìm thấy bằng tải** (test đơn vị chạy bằng Administrator, một kết nối, `enqueue` bị mock nên không bắt được); mỗi lỗi nay có test đỏ trước, xanh sau.

| # | Mức | Lỗi | Trạng thái |
| --- | --- | --- | --- |
| E5 | Trung bình | **Deadlock 1213 khi tạo `Omni Call` song song.** `calls.get_or_create` làm `SELECT ... FOR UPDATE` trên `call_id` chưa tồn tại: khóa khoảng của InnoDB tương thích nhau, hai worker tạo hai cuộc gọi khác nhau cùng giữ khóa rồi chờ nhau khi INSERT. 7,9% tin cuộc gọi phải thử lại ở 10 cuộc/giây, một số tin vẫn Retry sau 15 phút (thử lại có lùi dần). Không mất dữ liệu | **Đã sửa** (`fix(telephony)`): đọc thường trước, chỉ khóa khi hàng đã có, chưa có thì INSERT thẳng. Test: `TestConcurrentCallCreate` (hai kết nối thật, hàng rào đồng bộ; đỏ với `QueryDeadlockError` trước khi sửa) |
| E6 | **Cao** | **Job việc gọi lại không tạo được việc khi chạy ở production.** Job do worker xử lý tin đẩy ra thừa hưởng người dùng **Guest** của webhook; `CRM Task` giao việc (`assign_to`) kiểm quyền nên `PermissionError`, `process_missed_call` bắt lỗi và bỏ qua. Gần như mọi cuộc nhỡ (395/407) không có việc từ job; chỉ tác vụ quét (Administrator, 5 phút/lần, tuổi cuộc nhỡ >= 5 phút) tạo bù nên việc gọi lại chậm 5-10 phút và Error Log đầy. Test cũ chạy bằng Administrator nên không thấy | **Đã sửa**: `process_missed_call` đặt người dùng Administrator ở đầu job. Test: `TestCallbackJobRunsAsGuest` (đỏ: job trả None). **Rủi ro còn lại:** mọi job khác của Đợt 1/2 vẫn chạy bằng Guest (xử lý tin, phân tích, chấm điểm, bot trả lời). Đã chạy thật qua worker ở mục 3.2: 275 lượt phân tích, 275 nháp, điểm Lead, 0 lỗi, nên các đường đó không bị chặn quyền; nhưng hành vi "chủ bản ghi/người sửa = Guest" khác với test (Administrator): xem 9.1 mục 14 |
| E7 | **Cao** | **Hai job việc gọi lại của cùng một khách chạy song song tạo việc trùng hoặc lỗi `TimestampMismatchError`.** Job thứ hai chờ khóa dòng Lead nhưng ảnh chụp REPEATABLE READ đã mở từ lần đọc thường trước đó, nên sau khi chờ không thấy việc job thứ nhất vừa commit: tạo việc thứ hai (53/141 khách có hơn 1 việc mở) hoặc gộp vào bản việc cũ rồi lỗi `TimestampMismatchError` (95 lỗi trong cửa sổ hai lượt tải đo sau khi sửa E6). Làm sai chỉ số "gọi lại đúng hạn" và kỳ vọng C-02 (3 nhỡ = 1 việc) | **Đã sửa**: khóa Call rồi Lead **trước mọi lần đọc thường** (đọc khóa không mở ảnh chụp). Test: `TestConcurrentCallbackJobs` (2 kết nối, đỏ: hai việc khác nhau). Sau sửa: 0 khách có hơn 1 việc, 0 lỗi |
| E8 | Trung bình | **Cảnh báo khi AI hỏng có thể không bao giờ bật.** `ai_loi_nhieu` đo tỉ lệ lỗi trong 24 giờ (> 5% khi >= 20 lượt); ngắt mạch bỏ qua các lượt sau 5 lỗi liên tiếp (`Skipped Switch` không tính là lỗi), nên khi AI sập hẳn chỉ có 5 lỗi: nếu trong 24 giờ đã có >= 100 lượt gọi tốt thì tỉ lệ < 5% và không có cảnh báo nào (đo thật: 5/764 = 0,65%). Hệ quả: bot im lặng ngừng làm việc, sale không biết | **Đã sửa (cộng thêm, không đổi hành vi cũ):** snapshot thêm `bot.breaker_open`, cảnh báo mới `ai_ngat_mach` khi ngắt mạch đang mở. Test: `TestBreakerAlertRule`, `TestB05...` (đỏ trước). Không đổi luật `ai_loi_nhieu` |
| E9 | Thấp (đề xuất) | Cuộc gọi dùng chung `debounce_seconds` (5 giây) với tin nhắn nên mỗi cuộc gọi giữ một worker ~6 giây dù không có ý nghĩa gom (một cuộc gọi = một sự kiện kết thúc). Đo: 8 worker, 10 cuộc/giây: nhận -> Done p95 89 giây (backlog) | **Mở**, chưa sửa (đổi cấu hình/thiết kế worker). Đề xuất: debounce 0 cho kênh Telephony (hoặc tối đa 1 giây), hoặc tính số worker theo `số_worker / 6` như `docs/11` |
| E10 | Thấp (đề xuất) | Phục hồi sau sự cố phụ thuộc số lượt cron 5 phút: `requeue_stuck` chỉ đẩy lại tin `New` đã quá 2 phút (đọc tối đa 1.000 dòng tin mỗi lượt) và `sweep_pending` xử lý tối đa 100 cuộc nhỡ mỗi lượt, cũ nhất trước. Đo sau sự cố Redis: 872 tin New cần 2 lượt quét; 7 job việc gọi lại mất cần 3 lượt `sweep_pending` vì tồn đọng cũ chiếm 100 chỗ/lượt | **Mở**. Đề xuất: tăng giới hạn hoặc lặp trong một lượt đến hết ngân sách thời gian; trong sự cố bấm "Quét ngay" nhiều lần |
| E11 | Thấp | Log "Omni: lỗi tạo việc gọi lại" chỉ ghi `call=<mã>` nên không chẩn đoán được E6/E7 trong lúc tải | **Đã sửa** cùng E6: ghi thêm loại lỗi và traceback đã che (`redact`) |
| (mở từ Đợt 1) | | E2 (webhook chờ khóa DB không giới hạn), E3 (phục hồi 15 phút sau kill worker), E4 (số worker) | Vẫn mở, xem `dot-1.md` mục 6 |

## 7. Bảo mật nhanh

- **Hàm whitelisted:** Đợt 2 không thêm hàm `@frappe.whitelist` mới ngoài `telephony_event`, `telephony_lookup` (cả hai `allow_guest=True` và có token) — `test_only_webhook_allows_guest` đã chấp nhận đúng ba tên (`zalo`, `telephony_event`, `telephony_lookup`). Ma trận Guest/Sales User của Đợt 1 (`dot-1.md` mục 7) không đổi.
- **Webhook tổng đài:** sai token -> 401, không bản ghi (test + 57 request tải). Có header `X-Stringee-Signature` sai -> 401; **không có header -> chỉ còn kiểm token** (tạm thời, docs/12 mục D D2-02): kẻ biết `webhook_token` giả được sự kiện. Cần công tắc "bắt buộc chữ ký" sau khi S1 xác nhận (thêm trường: cần ADR).
- **Tra cứu:** sai token/kênh tắt trả cùng phản hồi "khách mới" (không lộ khách nào có trong hệ thống); `t` nằm trong query nên xuất hiện trong log nginx: che query ở nginx.
- **Dữ liệu khách ra AI:** cổng Q11 trong `call_tool`: client thật chỉ chạy khi `omni_ai_allow_customer_data` hoặc `synthetic`; bộ đề `Synthetic` bị importer từ chối nếu chứa SĐT/email/URL thật; Bot Run không lưu hội thoại; `output_json`/`Bot Draft` có PII nằm trong `erase_customer_data`.
- **Chưa phủ:** `erase_customer_data` **chưa xóa** `Omni Call` (`from_number`, `to_number`, `raw_events`) và `CRM Call Log` (`from`, `to`) của Lead; chưa có ghi âm (Đợt 3). Nút xóa chưa dùng được cho yêu cầu pháp lý đối với dữ liệu cuộc gọi.
- **Trên omni.test:** `Omni Settings.bot_analyze_enabled = 1` (còn từ trước D2-09; mặc định phát hành là 0). Không ảnh hưởng vì không có khóa API và client thật bị chặn bởi Q11, nhưng **phải kiểm tra lại giá trị trên staging/production**.

## 8. Việc chưa kiểm được

| Việc | Vì sao chưa kiểm | Ai/việc gì để kiểm |
| --- | --- | --- |
| S1: khóa ký webhook Stringee là `telephony_api_key_secret` hay khóa khác (chặn) | Không có tài khoản Stringee | Gọi thử vào staging, xem Error Log `xac_thuc_that_bai` (docs/12 B3) |
| S2-S7: JSON tra cứu hiển thị ở app, `Call log.id` = `call_id`, ký `get_customer_info_url`, sự kiện ghi âm, `agentEnded`/`agent_ref`, `answerDuration` | Như trên | docs/12 mục B3 |
| Đối soát đêm và `verify_missed_call` với API Call log thật; JWT thật | Sandbox chặn HTTP, không có khóa | Staging, một ngày có cuộc gọi |
| Hạn gọi lại theo `holidays` thật | `holidays` chưa điền | Người phụ trách điền, kiểm 1 ngày lễ |
| Độ chính xác bot (cổng `phan_tich`), độ trễ và chi phí token thật, hành vi mô hình với chèn lệnh | Không có khóa API, Q11, bộ đề chưa duyệt | Mục 10 |
| Hiển thị điểm/tóm tắt/trường bot trên màn Lead của CRM (Fields Layout) | Chưa cấu hình, không sửa app `crm`; không có trình duyệt | Người duyệt cấu hình Fields Layout rồi mở thử |
| Giao diện Hộp thư hiển thị dòng cuộc gọi, tóm tắt bot, nhãn ý định | Chỉ kiểm API | Người duyệt mở `/app/omni-inbox` |
| Hiệu năng trên staging/production | Không có staging | Lặp lại `send_calls.py` và `send_burst.py` |
| Tuần chạy thử: gọi nhỡ gọi lại đúng hạn, trường đúng, báo cáo Bot ngầm | Cần sale thật | `uat-dot-2.md` rồi `measure_dot2.py` |
| Worker bị kill, webhook chờ khóa DB ghi (lặp lại Đợt 1 với tải cuộc gọi) | Không chạy lại ở D2-09 | Staging |
| Ghi âm, tóm tắt cuộc gọi, STT | Đợt 3 | D3-03 |

## 9. Tổng hợp cho chủ dự án

### 9.1 Phải quyết hoặc làm TRƯỚC khi bật cho khách thật

| # | Việc | Mức | Ghi chú |
| --- | --- | --- | --- |
| 1 | **Q11:** chính sách gửi dữ liệu hội thoại/cuộc gọi sang Claude API và dịch vụ chuyển giọng nói (phạm vi, thông báo cho khách) | Chặn bot | Chưa chốt thì mã chặn mọi lần gọi AI trên dữ liệu thật. Quyết định kéo theo `omni_ai_allow_customer_data`, `omni_ai_unmask_pii` |
| 2 | **Khóa Claude API** và mô hình (Q3): chưa có khóa trong hệ thống nào; đặt vào `Omni Settings` (trường Password), không bao giờ vào log | Chặn bot | |
| 3 | **Che SĐT/email** (`omni_ai_unmask_pii`): mặc định bot không thấy SĐT/email nên không trích được `phones` (1/5 trường nhóm "trích thông tin"): hoặc bật khi Q11 cho phép, hoặc bỏ `phones` khỏi trường chấm của cổng | Cao | docs/12 mục D (D2-04, D2-08) |
| 4 | **Bộ đề chưa duyệt:** 220 ca Synthetic, 0 ca Real, chưa người duyệt, chưa chấm lại 20%; cần ca thật đã che (`build_from_real`, cần Q11) và người duyệt chỉnh. Cổng `phan_tich` >= 90% phải **đo bằng AI thật trên bộ đã duyệt**; hiện "chưa đo" | Chặn bot | Mục 10 |
| 5 | **Kho tri thức rỗng** (0 mục kho tri thức trên omni.test; có 8 nhóm câu hỏi): bot trả lời chỉ tạo nháp `Handoff` cho mọi lượt. Cần nhập kho thật (`knowledge.import_*`), **ngưỡng 150 mục** (`KB_MAX_ITEMS`): vượt thì bot không soạn cho tới khi có ADR chuyển sang truy hồi | Cao (Đợt 3) | docs/12 D2-07 |
| 6 | **Q5 bảng điểm Lead:** `mau/bang-diem-lead-mau.csv` là mẫu (import ra cảnh báo `is_sample`); chấm điểm tắt mặc định; Omni Admin nhập bảng đã duyệt rồi bật. **Q8** kênh báo Lead nóng, **Q6** giờ bot | Trung bình | |
| 7 | **S1-S7 Stringee** (mục 8): S1 là điều kiện chặn Stringee production. Thêm công tắc "bắt buộc chữ ký" (mặc định bật) sau S1 (thêm trường: cần ADR) | Chặn tổng đài | |
| 8 | **Điền `Omni Settings.holidays`** (JSON ngày nghỉ lễ); **đặt `callback_fallback_user`** (việc gọi lại không có người nhận nếu Lead chưa có chủ); **Q4** quy tắc gán Lead; Assignment Rule của CRM áp cho `CRM Task` chưa xác nhận (docs/12 D2-03) | Cao | Không điền thì hạn gọi lại tính ngày lễ là ngày làm việc |
| 9 | **Fields Layout của CRM** (màn Lead): thêm điểm, tóm tắt bot, trường `omni_*` để sale thấy; không sửa app `crm` | Trung bình | Hộp thư Omni đã hiện tóm tắt và nhãn ý định |
| 10 | **`erase_customer_data` chưa phủ `Omni Call` và `CRM Call Log`** (SĐT, `raw_events`) và ghi âm Đợt 3 | Cao nếu dùng cho yêu cầu pháp lý | Mở rộng hàm xóa, cần quyết phạm vi |
| 11 | **Số worker `omni`** cho tải cuộc gọi: công suất ~`số_worker / 6` khách/giây (E9); xem xét debounce 0 cho Telephony; supervisor phải khởi động lại worker khi mất Redis | Trung bình | docs/11 |
| 12 | **E10** tác vụ quét `sweep_pending` giới hạn 100 cuộc/lượt, cron 5 phút: chấp nhận hoặc nâng giới hạn | Thấp | |
| 13 | **Ngắt mạch (5 lỗi/5 phút), timeout AI 60 giây** (CLAUDE.md nói 10 giây cho lời gọi ngoài; AI sinh văn bản dài): xác nhận ngưỡng | Thấp | docs/12 D2-04 |
| 14 | **Người dùng của job nền:** mọi job do webhook đẩy ra chạy bằng **Guest** (chủ bản ghi/người sửa = Guest, `frappe.session.user`); test chạy bằng Administrator nên chưa phủ. Quyết: đặt người dùng hệ thống ở `process_identity` (đổi hành vi toàn Đợt 1+2, cần ADR) hay chấp nhận và chỉ đặt ở các job cần quyền như `process_missed_call` (đã làm) | Trung bình | E6 |
| 15 | Các mục mở từ Đợt 1: Z1...Z9 (chữ ký/payload Zalo), R1, xóa dữ liệu, E2/E3, Q4, Q7, Q9, Q10 | Xem `dot-1.md` 9.1 | Chưa đổi trạng thái ở D2-09 |
| 16 | **Cuộc gọi ẩn số** không tạo Lead/việc (quyết định D2-03): xác nhận | Thấp | docs/12 D2-03 |
| 17 | Công tắc: `developer_mode` TẮT, `zalo_skip_signature` TẮT, `sandbox_mode` tắt có chủ đích; `bot_analyze_enabled = 0`, `bot_reply_mode = Off`, `lead_scoring_enabled = 0` cho tới khi 1-6 xong; scheduler của site bật (đối soát 01:30, quét 5 phút) | Chặn | |

### 9.2 Điều kiện trước khi cho sale dùng thật (tuần chạy thử)

Tổng đài (không cần bot):

1. Staging có tổng đài Stringee thử: xác nhận S1...S7, ghi vào docs/12 mục C, rồi lặp lại tải (`send_calls.py` cần khóa thử) để so mục 3.
2. Điền `holidays`, `callback_fallback_user`, ánh xạ nhân viên `Omni Agent Map` cho từng máy nhánh; kiểm tra Assignment Rule/Task.
3. Scheduler bật; supervisor có `bench worker --queue omni` (số worker theo `số_worker / 6`).
4. `alert_recipients` đã điền; người nhận biết ý nghĩa `goi_lai_qua_han`, `doi_soat_bu_nhieu`, `doi_soat_loi`, `ai_ngat_mach`.
5. Khởi đầu với `sandbox_mode` bật 1 ngày (không gọi Call log, không gửi), rồi tắt có chủ đích.
6. Chạy `uat-dot-2.md`, điều kiện dừng ở mục 5 của tệp đó.

Bot (sau khi xong 9.1 mục 1-6): bật `bot_analyze_enabled = 1` và giữ `bot_reply_mode = Shadow` ở môi trường có khóa thật, chạy bộ đề `phan_tich` bằng AI thật trên bộ đề đã duyệt (>= 90%), rồi mới mở tuần chạy thử có sale.

### 9.3 Khuyến nghị phát hành

| Phần | Khuyến nghị | Công tắc |
| --- | --- | --- |
| Tổng đài (cuộc gọi, Lead, gọi nhỡ, việc gọi lại, đối soát) | **Phát hành sau khi** xong 9.1 mục 7, 8, 11, 17 trên staging và UAT tổng đài | `Omni Channel.enabled` của kênh tổng đài, `sandbox_mode` |
| Bot phân tích + chấm điểm | **Chưa phát hành** (giữ `bot_analyze_enabled = 0`, `lead_scoring_enabled = 0`) cho tới khi Q11, khóa API, bộ đề đã duyệt và cổng `phan_tich` đạt | |
| Bot trả lời chạy ngầm | **Chưa phát hành** (`bot_reply_mode = Off`; Shadow chỉ khi Q11 cho phép) | |

## 10. Bộ đề và cổng `phan_tich`

Lệnh: `bench --site omni.test execute omni_crm.bot.eval.run.run_cli --kwargs "{'gate': 'phan_tich', ...}"` với `kb_file: 'eval/kb-tong-hop.csv'`.

| Lần chạy | Chế độ | Kết quả | Có phải số đo của bot? |
| --- | --- | --- | --- |
| `EVR-2026-00317` (`eval/reports/2026-10-30-analyze-v1-fake.md`) | Fake (client trả đúng đáp án) | 220/220 ca đo được, trường đúng 100%, lỗi nghiêm trọng 0; kết luận cổng: **KHÔNG ÁP DỤNG (giả lập)** | **Không.** Chỉ kiểm hạ tầng bộ chấm |
| `EVR-2026-00318` (`eval/reports/2026-10-30-analyze-v1.md`) | Không có client AI (không khóa) | 0/220 đo được, 220 "chưa đo"; kết luận: cổng **không qua** (chưa đo) | **Chưa đo** |

**Cổng `phan_tich` (>= 90% trường đúng): chưa đo, không thể kết luận qua hay trượt.** Thiếu: (1) khóa API và Q11 để chạy AI thật; (2) bộ đề đã duyệt: hiện 220 ca Synthetic chưa duyệt, 0 ca Real, mẫu chấm lại tay 20% (21 ca) chưa có kết luận người; (3) quyết định che SĐT/email (mục 9.1 mục 3) vì 20 ca "trích thông tin" có SĐT. Đề xuất khi chạy được: chạy bằng AI thật với `allow_real` và bộ đã duyệt; nếu trượt, xem bảng "ca trượt theo loại" trong báo cáo để sửa câu lệnh `analyze_v1` hoặc kho tri thức; đổi câu lệnh/mô hình sau này phải qua cổng `doi_cau_lenh` (không giảm quá 2 điểm).
