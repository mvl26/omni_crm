# Nghiệm thu Đợt 3 và tổng hợp toàn dự án (D3-06)

Ngày lập: 2026-10-30. Người lập: agent kiểm thử D3-06. Nhánh `d3-06-kiem-thu-dot-3` (từ `develop`, đã có D3-01, D3-02, D3-03, D3-05; **D3-04 (ghi âm và tóm tắt cuộc gọi) bị bỏ qua** vì cổng chuyển giọng nói chưa đo, xem `stt-thu-nghiem.md`). App `omni_crm`, site test `omni.test`. Nền 1006 test OK do điều phối nêu (không chạy lại trước khi thêm test).

## 0. Kết luận và khuyến nghị

**Đợt 3 (bot nháp cho sale duyệt, chuyển người, bot tự gửi theo nhóm): "Chưa phát hành".** Phần đo được trên máy phát triển đều tốt; phần quyết định có cho bot nói chuyện với khách thật hay không thì **chưa đo được gì**. Giữ `bot_reply_mode = Off`, `bot_analyze_enabled = 0`, mọi `auto_send_enabled` = 0, không đặt `omni_auto_send_allowed` trong site_config. "Phát hành sau khi" cả bốn điều kiện sau thành sự thật, theo đúng thứ tự:

1. **Cổng `nhap` đo được và đạt** bằng AI thật trên bộ đề đã duyệt (hành động đúng >= 90%, trả lời đạt >= 85%, lỗi nghiêm trọng = 0, không suy giảm quá 2 điểm so với lần đạt trước). Hiện **chưa đo**: không có khóa AI, 220 ca đều `Synthetic` và chưa duyệt, 0 ca `Real` (mục 10). Số 100% của client giả không phải điểm của bot.
2. **Chủ dự án chốt Q11** (gửi dữ liệu hội thoại sang Claude API), **Q12, Q6, Q8**; có khóa Claude API; staging có OA Zalo thử.
3. **Tuần chạy thử có sale thật** ở chế độ Draft (`uat-dot-3.md` mục 8), đo bằng `measure_dot3.py`: nháp đúng >= 85%, lỗi nghiêm trọng = 0, không tin bot sai tới khách thật.
4. **Q1 chốt** trước khi mở BẤT KỲ nhóm Auto nào; Auto chỉ khi nhóm đủ điều kiện số liệu (>= 30 nháp đã quyết định trong 14 ngày, >= 95% gửi nguyên văn hoặc sửa nhẹ, 0 nghiêm trọng).

Kênh (email, Zalo OA, tổng đài, Lead, chống trùng) không đổi so với kết luận của `dot-1.md` và `dot-2.md`: các điều kiện "Phát hành sau khi" ở đó vẫn còn nguyên (Z1/G7, S1, R1, Redis, số worker...). D3-06 không làm chúng nhẹ đi và cũng không làm chúng nặng thêm: **hồi quy Đợt 1 và Đợt 2 không thấy suy giảm** (mục 3).

**Điều đã làm được ở D3-06 (đo được trên máy dev):**

- 27 test tích hợp mới (`tests/test_dot3_integration.py`) đi từ webhook Zalo ký thật qua worker đến bot, **mọi job nền chạy bằng Guest** (như worker RQ), thao tác sale bằng Sales User thật, và có test nhiều kết nối. Các test này tìm ra **1 lỗi thật (E12, đã sửa)**: hai job `reply_batch` cùng một lượt ghi hai nháp, và ở nhóm Auto gửi cho khách **hai tin Bot giống nhau**. Đỏ trước (cả hai test), xanh sau khi khóa Redis theo lượt.
- Tải thật bằng gunicorn 8 worker, 8 worker RQ chạy bằng Guest trên Redis tạm: Zalo (bot tắt) 3.600 tin / 300 khách, cuộc gọi 1.200 cuộc / 150 số, bot Draft 1.200 tin / 200 khách, bot Auto trong tiến trình worker, hết hạn mức. Không mất, không trùng, không lỗi quyền; trong các cửa sổ tải chỉ có 2 dòng Error Log `Omni webhook: xac_thuc_that_bai` (do 2% request sai token cố ý của pha cuộc gọi; có giới hạn 1 dòng/phút/kênh), không dòng nào của bot.
- Quay lui mức 1 và mức 2 (docs/11 mục 4) có test: tắt từng công tắc thì dừng đúng phần đó (mục 5).

**Giới hạn của bản nghiệm thu này (đọc trước):**

1. **Không có staging, AI thật, Zalo thật, Stringee thật, trình duyệt, sale thật.** Mọi lời gọi AI là `FakeAIClient`; mọi tin ra là sandbox (không HTTP). Do đó **chưa có số nào về chất lượng câu trả lời của bot, về độ trễ AI hay chi phí token thật.**
2. **p95 webhook ~17-19 ms là thời gian NHẬN, không phải độ trễ xử lý.** Từ lúc nhận đến khi tin Done (Lead, dòng thời gian, bot) là p50 66-99 giây, p95 128-191 giây ở các pha tải dồn với 8 worker RQ (backlog do mỗi lượt giữ worker ~6 giây, E4/E9). Đừng đọc p95 nhận như "phản hồi khách < 15 phút" hay "tin -> Lead < 1 phút": hai chỉ số đó chưa đo bằng khách thật.
3. **Máy dev 16 nhân dùng chung với 3 bench khác**, MariaDB cục bộ, không nginx/HTTPS/mạng thật. Số đo là cận dưới về độ trễ mạng.
4. **Redis queue của bench (cổng 11000) hỏng và bị `bench start` khởi động lại liên tục (khi đo ở trạng thái "loading dataset")**. Không đụng vào. Toàn bộ test và tải ở D3-06 chạy với `FRAPPE_REDIS_QUEUE` trỏ Redis tạm cổng riêng (11097; cache tạm 11096 cho tải), đã tắt sau khi xong. Hệ quả cần biết: khi chạy thử một số module riêng lúc Redis 11000 ở trạng thái "đang nạp", 23 test (2 của `test_draft`, 21 của `test_handoff`) đỏ vì `frappe.enqueue` chưa mock ném `ConnectionError`; với Redis tạm khỏe chúng qua (mục 1). Các test này nằm trong nền 1006 OK đã nêu nên đây là phụ thuộc môi trường của test cũ, không phải lỗi mã. Nguyên nhân phía Redis 11000 là suy luận từ thông báo lỗi, chưa kiểm sâu hơn. **Lưu ý:** các lần chạy module riêng trước khi dựng Redis tạm (gồm `test_bot_reply` 65 OK và lần `test_dot3_integration` 27 OK đầu) chạy với Redis 11000 mặc định; chỉ lần chạy toàn bộ (mục 1) và toàn bộ tải dùng Redis tạm.
5. **Chưa có tuần chạy thử** nên không có số "nháp đúng", "lỗi nghiêm trọng trên khách thật", "bot tự xử lý theo nhóm", "phản hồi lần đầu": `measure_dot3.py` có, in "chưa đủ dữ liệu" cho tới khi có dữ liệu thật (mục 4).
6. **Lần chạy tải đầu có webhook p95 16,9 giây không giải thích được** (mục 3.6). Không tái hiện ở các lần chạy sau (p95 14,6-19,8 ms). Ghi như một rủi ro chưa hiểu, không hạ nhẹ.

**Rủi ro lớn nhất còn lại (theo thứ tự):**

1. Bot nói sai với khách thật (Auto) hoặc nháp sai bị sale gửi đi vì tin bot: chốt chặn bằng mã đã có test, nhưng **chưa thử với mô hình thật**; mô hình thật có thể lách chốt chặn theo cách client giả không mô phỏng được (D-03 chỉ chứng minh chốt chặn, không chứng minh mô hình).
2. Q11/Q1 chưa chốt: dữ liệu khách có thể sang dịch vụ AI; bot có thể tự gửi. Cả hai được chặn bằng mã/khóa hạ tầng, nhưng đó là rào chặn kỹ thuật, không phải quyết định kinh doanh.
3. Chữ ký webhook Zalo/Stringee (Z1/G7, S1) chưa đối chiếu với hệ thống thật: sai thì không nhận được tin nào từ kênh thật.
4. R1: gộp Lead sai vì tín hiệu trung bình; `erase_customer_data` chưa phủ `Omni Call` và `CRM Call Log` (SĐT, `raw_events`) nên chưa dùng được cho yêu cầu xóa dữ liệu pháp lý đối với cuộc gọi.
5. E6: job nền chạy bằng Guest. Đợt 3 đã kiểm bằng test và tải rằng các đường bot (phân tích, chấm điểm, soạn nháp, chuyển người, tự gửi, ngắt mạch) **làm được việc khi chạy bằng Guest** (không lỗi quyền); nhưng chủ bản ghi/người sửa vẫn là Guest và chưa có quyết định chính thức (mục 9.1).

## 1. Kết quả test tự động

Lệnh: `cd /home/hoangvietyeuem/frappe-bench && FRAPPE_REDIS_QUEUE=redis://127.0.0.1:11097 bench --site omni.test run-tests --app omni_crm` (Redis tạm khỏe, xem giới hạn 4; ruff 0.8.1: `check` và `format --check` sạch).

```
[[FULL_RUN]]
```

Test mới của D3-06: `test_dot3_integration` 27 test, `test_measure_dot3` 10 test. Hai test đồng thời `test_double_run_of_same_batch_*` **đỏ trước khi sửa** (2 nháp / 2 tin Bot cho một lượt) và xanh sau khi khóa Redis theo lượt ở `reply_batch`. Trước lần chạy toàn bộ, chạy riêng sau khi sửa (với Redis 11000 mặc định): `test_bot_reply` 65 OK, `test_dot3_integration` 27 OK.

`bench --site omni.test migrate`: [[MIGRATE]]

Dữ liệu tải đã dọn sạch (0 Lead/Cuộc gọi/Định danh/kênh/nhóm câu hỏi/mục kho `LOAD*`/`KB-93*`); `sites/omni.test/site_config.json` giống hệt bản sao lưu (`cmp`), không có `developer_mode` hay `omni_auto_send_allowed`; Omni Settings đã khôi phục đúng giá trị trước khi đo (xem mục 7). Đã xóa các dòng Error Log tiêu đề `Omni…` tạo sau 10:50 giờ site (do chính các lượt test/tải của phiên này, kể cả các lần chạy bị ngắt); Error Log cũ hơn giữ nguyên.

## 2. Bảng ánh xạ kịch bản Đợt 3

Đối chiếu theo **nội dung assert**, không theo tên. "Mới" = `tests/test_dot3_integration.py` (webhook ký -> `process_identity` -> `analyze_batch` -> `reply_batch`, job bằng Guest, sale bằng Sales User thật).

| # | Kỳ vọng | Test chính (mới) | Test bổ trợ có sẵn | Ghi chú |
| --- | --- | --- | --- | --- |
| D-01 | Nháp hiện cho sale, gửi nguyên văn / sửa / bỏ, chấm; khách nhắn tiếp thì nháp cũ hết hạn | `TestD01Draft.test_d01_draft_shown_sent_edited_discarded_rated` (thông báo đúng 1 lần cho chủ Lead, không lộ tin khách; sale khác bị `PermissionError` ở `get_thread` và `send_draft`; tin ra `source=Sale`, `sent_by`, `is_sandbox=1`; Sent As Is/Edited/Discarded, tỉ lệ sửa; chấm Đúng/Thiếu/Sai; nháp Pending không chấm được; không HTTP), `test_d01_new_customer_message_expires_pending_draft_and_blocks_stale_send` | `test_draft.TestDraftCreation.*`, `TestSendDraft.*`, `TestStaleAndConcurrency.*`, `TestRating.*`, `TestDraftPermissions.*` | Giao diện Hộp thư chưa mở trên trình duyệt |
| D-02 | Hỏi giá -> Handoff, thông báo, tạm dừng bot, tiếp tục bot | `TestD02PriceHandoff.test_d02_price_question_handoff_pause_resume` (nháp Handoff không có văn bản, đúng 1 thông báo, `bot_paused_until` ~24 giờ, ghi dòng thời gian, tin kế tiếp "paused" không gọi AI soạn, không thông báo thêm, nháp Handoff không gửi được, sale khác không bấm được "Cho bot tiếp tục", sale phụ trách bấm được, tin sau lại có nháp, tạm dừng tay), `test_d02_wants_human_goes_through_handoff_even_if_bot_proposes_reply` | `test_handoff.*` (33 test) | Email nhắc theo Q8 chỉ có test mock, chưa quan sát thật |
| D-03 | Khách chèn lệnh -> không làm theo | `TestD03Injection.*` (3 test): client giả LÀM THEO lệnh (trả "giá 0 đồng, giảm 50%", gắn link/SĐT lạ) -> nháp Handoff, cờ `nghi_chen_lenh` + `tien_ngoai_kho`, không có văn bản gửi được, 0 tin ra; lệnh chỉ nằm trong khối `<hoi_thoai>`, không lọt câu lệnh hệ thống; câu "bật Auto" không đổi cấu hình | `test_bot_reply.test_injection_with_money_blocked`, `TestGuards.test_more_sensitive_and_injection_keywords`, bộ đề 10 ca "chèn lệnh" (chỉ chạy bằng Fake) | **Chứng minh chốt chặn bằng mã, không chứng minh mô hình thật không bị lừa** |
| D-04 | Nhóm Auto gửi đúng; ngoài khung Zalo -> Draft; ngắt mạch | `TestD04Auto.*` (7 test; khóa hạ tầng đặt trong phạm vi test bằng `mock.patch.dict`): Auto gửi đúng 1 tin `source=Bot`, `is_sandbox=1`, không HTTP, không thông báo chờ duyệt, job bằng Guest; nhóm tắt / ngoài khung 72 giờ / ngoài giờ bot / khóa hạ tầng đóng -> Draft kèm cờ; hỏi giá trong nhóm Auto -> Handoff, không tin Bot, đúng 1 câu giữ chân `source=System`; **sale thật (Sales User) chấm Sai -> nhóm tự tắt, Omni Admin và Sales Manager nhận thông báo**, tin sau rơi về Draft; khiếu nại sau tin bot -> nhóm tự tắt trong job Guest; gỡ khóa hạ tầng -> tin Bot đang chờ `Blocked`; tắt bằng API (Omni Admin) | `test_auto_send.*` (TestAutoSend, TestBreakers, TestSendTimeRecheck, TestConcurrency...) | Mở Auto thật chưa làm (Q1) |
| D-05 | Hết hạn mức AI khi đang Auto -> không gửi, kênh vẫn chạy, cảnh báo | `TestD05BudgetDuringAuto.test_d05_budget_exhausted_while_auto_sends_nothing_channel_keeps_running` (3 tin liên tiếp: Bot Run `Skipped Budget`, 0 nháp, 0 tin Bot mới, tin vào Done, cảnh báo đúng 1 lần, nhóm KHÔNG tự tắt, bật lại hạn mức thì Auto chạy lại) + tải mục 3.5 | `test_dot2_integration.TestB04BudgetExhausted` (bot tắt), `test_ai_client.*` | Thư `ai_het_han_muc` chỉ kiểm bằng mock |
| D-06 | Ghi âm -> tóm tắt; quyền nghe ghi âm | **Bỏ qua: D3-04 không làm** (cổng STT chưa đo) | | Không có tính năng để kiểm |
| D-07 | Quay lui mức 1 (từng công tắc) và mức 2 | `TestD07Rollback.*` (6 test, mục 5) | `test_flags.*` | |
| Đồng thời / quyền | Nhiều kết nối, người dùng không phải Administrator, job Guest | `TestGuestAndRealUserPaths.*` (3), `TestConcurrentConnections.*` (3: song song nhiều khách; chạy lặp cùng lượt 2 test) | | Tìm ra E12 |

## 3. Kết quả tải

Công cụ có sẵn (`tests/load/`): `send_burst.py`, `send_calls.py`, `verify_burst.py`, `verify_calls.py`, `rq_worker.py`. Bổ sung ở D3-06 (không đổi hành vi cũ): `rq_worker.py` biến môi trường `OMNI_LOAD_D3=1` (bot trả đáp án thật theo kho tri thức tải, câu hỏi giá chuyển người) và `OMNI_LOAD_AUTO_INFRA=1` (mở khóa hạ tầng tự gửi **chỉ trong tiến trình worker**, vá `flags.auto_send_infra_allowed`, không đụng site_config); `verify_calls.py`: `setup_d3`, `auto_group`, `d3_report`, `cleanup_d3`.

Môi trường: `omni.test`, gunicorn 8 worker cổng 8099 (`--preload`), **8 worker RQ chạy từ `rq_worker.py` (không phải supervisor)**, Redis queue tạm 11097 và Redis cache tạm 11096 (cô lập khỏi 13000), MariaDB cục bộ, không nginx, `sandbox_mode` bật (0 HTTP ra ngoài), khóa/token ngẫu nhiên, `FakeAIClient` ở worker. Job do RQ chạy mang người dùng **Guest**.

### 3.1 Hồi quy Đợt 1: Zalo, bot TẮT (so với dot-2 mục 3.2)

30 tin/giây x 120 giây, 300 khách, 10% gửi lại, `bot_analyze_enabled = 0`, `bot_reply_mode = Off`:

| Mục | D3-06 | D2-09 (cùng kịch bản) |
| --- | --- | --- |
| Tin duy nhất / request | 3.600 / 3.989 | 3.600 / 3.989 |
| Mã trả về | 3.989 x 200 | 3.989 x 200 |
| Webhook (chỉ 200) | p50 12,7 / p95 **16,8** / p99 23,0 / max 245,0 ms | p50 13,1 / p95 20,6 / p99 26,1 / max 49,0 ms |
| Tin vào / duy nhất | 3.600 / 3.600 (0 mất, 0 `dedupe_key` trùng) | 5.800 / 5.800 (tích lũy) |
| Lead / khách; định danh có 2 Lead; nghi trùng | 300 / 300; 0; 0 | 500 / 500; 0; 0 |
| Cạn hàng đợi (8 worker) | 195 giây | 195 giây |
| Nhận -> Done | p50 99 s, p95 191 s, max 198 s | p50 72 s, p95 190 s, max 202 s |
| Bot Run / nháp / thông báo / tin ra | 0 / 0 / 0 / 0 | |

Không suy giảm. `max 245 ms` là một request đơn lẻ.

### 3.2 Hồi quy Đợt 2: cuộc gọi Stringee (so với dot-2 mục 3.1)

10 cuộc/giây x 120 giây, 150 số, 35% nhỡ, 10% gửi lại, 2% sai token:

| Mục | D3-06 | D2-09 (sau 3 bản sửa) |
| --- | --- | --- |
| Request / mã | 1.340: 1.312 x 200, 28 x 401 | 1.343: 1.314 x 200, 29 x 401 |
| Webhook (chỉ 200) | p50 14,0 / p95 **16,8** / p99 22,2 / max 68,3 ms | p50 14,2 / p95 17,8 / p99 23,7 / max 217,3 ms |
| Tin cuộc gọi lưu / gửi; Done | 1.200 / 1.200; 1.200, cạn sau 90 s | 1.200 / 1.200; 1.200, ~95 s |
| `Omni Call` / `CRM Call Log` | 1.200 / 1.200 (0 cuộc không có Call Log) | 1.200 / 1.200 |
| Lead / số; số có 2 Lead | 150 / 150; 0 | 150 / 150; 0 |
| Cuộc nhỡ / khách có nhỡ / việc gọi lại | 410 / 141 / 141 (mỗi khách 1 việc, 0 khách có hơn 1 việc mở, 0 cuộc nhỡ thiếu việc) | 411 / 141 / 140+ |
| Nhận -> Done | p50 52 s, p95 93 s, max 97 s | p50 51 s, p95 89 s, max 93 s |

Không suy giảm; các sửa lỗi E5, E6, E7 của D2-09 vẫn giữ.

### 3.3 Đợt 3: bot Draft và chuyển người trên worker RQ thật (Guest)

20 tin/giây x 60 giây, 200 khách, `bot_reply_mode = Draft`, bot phân tích bật, kho tri thức tải 2 mục / 2 nhóm (một nhóm `always_handoff`), mọi nhóm Auto tắt:

| Mục | Kết quả |
| --- | --- |
| Request / mã | 1.326 x 200; webhook p50 13,2 / p95 **19,0** / p99 23,6 / max 40,9 ms |
| Tin vào / Done; Lead / khách | 1.200 / 1.200; 200 / 200 (0 mất, 0 trùng, 0 định danh 2 Lead) |
| Nhận -> Done | p50 66 s, p95 128 s, max 133 s (8 worker; có bot) |
| Bot Run | Analyze 274 Success; Reply 263 Success |
| Nháp | 257: Handoff 241 (70 Expired, 171 Pending), Reply 16 (4 Expired, 12 Pending). Phần lớn là Handoff vì `send_burst` chọn ngẫu nhiên 5 câu mẫu (2/5 hỏi giá) sau 200 tin đầu nên mọi khách sớm hỏi giá, rồi bot tạm dừng với khách đó (171 định danh bị tạm dừng). Đây là hình dạng của dữ liệu tải, **không phải tỉ lệ thật** |
| Bất biến | Lượt có hơn 1 nháp: **0**; tin ra: **0**; thông báo: 183 cho 183 Lead, **0 Lead trùng thông báo chuyển người**; Error Log trong cửa sổ: **0** |

### 3.4 Đợt 3: bot Auto trong tiến trình worker (khóa hạ tầng mở CHỈ trong tiến trình, sandbox bật)

Cùng kịch bản 3.3, `OMNI_LOAD_AUTO_INFRA=1`, nhóm "giờ làm việc" bật `auto_send_enabled` bằng đường nội bộ của test (bỏ qua số liệu eligibility), kênh `outbound_enabled`:

| Mục | Kết quả |
| --- | --- |
| Request / webhook | 1.326 x 200; p50 13,6 / p95 **19,3** / p99 24,7 / max 32,0 ms |
| Nháp | `Auto:Reply:Sent As Is` 13; Handoff 257 (70 Expired, 187 Pending); `Draft:Reply:Expired` 4 |
| Tin ra | `Bot:Sent` với `is_sandbox = 1`: **13 (đúng 1 tin cho mỗi nháp Auto)**; tin Bot không từ nháp Auto: **0**; tin Bot trên nháp Handoff: **0**; tin ra không phải sandbox: **0** |
| Bất biến | Lượt có hơn 1 nháp: 0; thông báo trùng: 0; Error Log: **0** |

Chỉ có 13 nháp Auto vì 2/5 khách hỏi giá ngay tin đầu và tin sau đó bị tạm dừng. Con số 13 không nói gì về tỉ lệ bot tự xử lý thật. Không có HTTP ra ngoài (sandbox); **không gửi thật qua Zalo**.

### 3.5 D-05 trên tải: hết hạn mức AI khi nhóm đang Auto

`daily_token_budget = 1`, nhóm Auto vẫn bật, 20 tin/giây x 20 giây, 100 khách:

| Mục | Kết quả |
| --- | --- |
| Request / webhook | 441 x 200; p50 13,7 / p95 19,7 / p99 33,1 / max 202,7 ms |
| Tin vào Done; Lead | 400 / 400; 100 |
| Bot Run | `Analyze: Skipped Budget` 121; `Reply: Skipped Budget` 121; không lượt nào gọi AI |
| Nháp / tin ra / thông báo | 0 / 0 / 0 |
| Snapshot giám sát | `ai_gan_han_muc` bật; `skipped_budget_24h` tăng; `breaker_open = false` |

Thư `ai_het_han_muc` (một lần/ngày) chỉ kiểm bằng test có mock, không quan sát thư thật.

### 3.6 Bất thường ở lần chạy tải đầu (không tái hiện được)

Lần chạy Zalo đầu tiên (3.600 tin, 30/giây) cho **webhook p50 14,0 ms nhưng p95 16.869 ms, p99 21.715 ms, max 22.676 ms**, mọi request vẫn 200 và dữ liệu vẹn toàn (3.600 vào, 300 Lead, 0 trùng). Điều kiện lúc đó **không phải điều kiện Đợt 1 sạch**: do lỗi của tôi khi gọi `bench execute` (hàm không đối số trả `NameError` che lỗi thật, nên lệnh "tắt bot" không chạy), bot đang ở Draft, bot phân tích bật, kho tri thức rỗng; gunicorn, worker và Redis cache tạm đều mới khởi động (cache lạnh). Sau đó tôi chạy lại đúng kịch bản (bot tắt thật: mục 3.1) và 6 lần chạy khác, mọi lần webhook p95 14,6-19,8 ms. Giả thuyết (chưa chứng minh): cache lạnh của 16 tiến trình khởi động đồng thời cạnh tranh DB trong phút đầu. Không thể loại trừ nguyên nhân thật trong mã (ví dụ nghẽn khi bot Draft ghi dồn lúc chưa có cache). **Đề xuất:** khi tải trên staging, chạy khởi động nguội một lần và ghi webhook p95 từng phút.

## 4. Chỉ số mục tiêu toàn dự án (docs/00 mục 2) và số đo hiện tại

"Đo được" = có số do công cụ trong repo sinh ra, trên dữ liệu **giả/máy dev**. "Chưa đo" = cần khách thật, sale thật, AI thật hoặc bộ đề đã duyệt. **Không có dòng nào ở đây là kết luận "đạt" cho nghiệm thu.**

| Chỉ số | Nghiệm thu | Sau 3 tháng | Số đo hiện tại | Trạng thái |
| --- | --- | --- | --- | --- |
| Tin bị mất | 0 | 0 | 0 mất trên mọi pha tải (3.600/3.600; 1.200/1.200 tin Zalo; 1.200/1.200 cuộc gọi; 400/400) trên máy dev | Đo được trên dữ liệu giả; chưa đo trên khách thật (cần `measure_dot1` + nhật ký nginx) |
| Lead trùng (phải gộp / Lead mới) | < 3% | < 1% | 0 định danh có 2 Lead, 0 nghi trùng trên dữ liệu tải (SĐT/định danh duy nhất, không phản ánh khách thật) | Chưa đo; rủi ro R1 mở |
| Tin vào đến Lead | < 1 phút | < 30 giây | Webhook NHẬN p95 ~17-19 ms (không phải chỉ số này). Nhận -> Done p50 66-99 s, p95 128-191 s ở tải dồn 20-30 tin/giây với 8 worker; dot-1 đo "tin đầu -> Lead" p50 117 s khi 4 worker | **Không đạt ở tải dồn; chưa đo ở tải thường.** Phụ thuộc số worker (E4/E9) và debounce 5 s |
| Phản hồi khách lần đầu (giờ hành chính) | < 15 phút | < 5 phút | Chưa có sale trả lời nào | Chưa đo (`measure_dot1.first_response`, `measure_dot3.first_response_after_draft`) |
| Câu trả lời của bot đúng (nguyên văn hoặc sửa nhẹ) | >= 85% | >= 92% | Không có | **Chưa đo.** Cổng `nhap` chưa đo; `measure_dot3.draft_quality` in "chưa đủ dữ liệu" |
| Trường trích xuất đúng | >= 90% | >= 95% | Không có (cổng `phan_tich` chưa đo; số 100% của Fake không phải điểm của bot) | **Chưa đo** |
| Tin phải xử lý tay | < 1% | < 0,5% | 0 tin Manual trên mọi pha tải (Done 100%) | Đo được trên tải giả; chưa đo khi có lỗi thật từ kênh |
| Gọi nhỡ gọi lại đúng hạn | >= 80% | >= 95% | Việc tạo đúng: 141 việc / 141 khách nhỡ, 0 trùng, 0 thiếu. "Đúng hạn" cần sale gọi thật | Chưa đo (`measure_dot2.callbacks_on_time`) |
| Cổng tiếp nhận hoạt động | >= 99,5% | >= 99,5% | Không đo được từ DB | Chưa đo (cần giám sát ngoài) |

Script cho Đợt 3 (`measure_dot3.py`, chỉ đọc, có `test_measure_dot3`):

```bash
bench --site <site> execute omni_crm.tests.acceptance.measure_dot3.run \
  --kwargs '{"start": "2026-11-23 00:00:00", "end": "2026-11-30 00:00:00"}'
```

| Mục trong script | Định nghĩa |
| --- | --- |
| `draft_quality` | Đúng = Sent As Is + Sent Edited có `edit_distance_ratio` <= 0,1 (import hằng số của `auto_send`, cùng định nghĩa eligibility); mẫu số = mọi nháp Draft đã quyết định kể cả bị bỏ; theo nhóm; tỉ lệ chỉ in khi >= 30 mẫu |
| `serious_errors` | Nháp `critical`, Sai kèm ghi chú, tin Auto bị chấm Sai, số lần ngắt mạch |
| `bot_self_handled` | Theo nhóm: tin Auto đã gửi / mọi nháp Draft+Auto; 0 khi chưa mở Auto |
| `first_response_after_draft`, `first_response_dot1` | Từ tin khách cuối của lượt đến tin ra gắn nháp (theo chế độ) và số đo Đợt 1; **tin sandbox bị loại** |
| `handoff_gaps` | Nháp Handoff theo nhóm và mã chốt chặn; gom ca "ngoài kho tri thức" để biết kho cần bổ sung gì |
| `nhap_gate` | Lần chạy bộ đề cổng `nhap` gần nhất; **chỉ `counts_as_measurement` khi AI thật trên bộ đề đã duyệt** |

Mặc định bỏ Lead dữ liệu giả (tiền tố LOAD, PLT, TD, T05, D209, D306, DEMO, RV, MD). Thiếu mẫu thì in "chưa đủ dữ liệu", không bao giờ ngầm in "ĐẠT".

## 5. Quay lui và sự cố (docs/11 mục 4)

| Mức | Thao tác | Kết quả kiểm (test `TestD07Rollback` trừ khi ghi khác) |
| --- | --- | --- |
| 1 | `bot_reply_mode = Off` | Không nháp, không tin ra; **phân tích vẫn chạy** (Bot Run Analyze Success) |
| 1 | `auto_send_enabled` của một nhóm tắt (Omni Admin qua API) | Nhóm rơi về Draft (nháp vẫn hiện cho sale); tin Bot đã gửi trước đó giữ nguyên |
| 1 | `outbound_enabled` của kênh = 0 | Tin vào vẫn xử lý, nháp vẫn tạo; sale bấm Gửi thì tin không tới khách (`Blocked` kèm lý do hoặc bị từ chối), nháp còn Pending; 0 HTTP |
| 1 | `bot_analyze_enabled = 0` | Không Bot Run Analyze; nháp vẫn tạo (kèm `info:khong_co_phan_tich`) |
| 1 | `enabled` của kênh = 0 | Webhook vẫn lưu tin (200), không xử lý (không Lead); bật lại thì xử lý đủ |
| 2 | `bot_reply_mode = Off` + `bot_analyze_enabled = 0` | Hệ thống thành "kênh vào CRM": tin vào Done, Lead và dòng thời gian đủ, **0 Bot Run, 0 nháp, 0 tin Bot**, kể cả với khóa hạ tầng Auto mở; **nháp Pending tạo trước đó không gửi được** (từ chối, `try_auto_send` trả False) |
| 2 | Tải thật | Pha 3.1 (bot tắt) chính là trạng thái này: 0 Bot Run/nháp/thông báo/tin ra |
| Khẩn cấp | Gỡ `omni_auto_send_allowed` khỏi site_config | Tin Bot đang chờ `Blocked` (`test_d04_infra_key_removed_blocks_queued_bot_message`) |

Sự cố hạ tầng (Redis tắt, worker bị kill, webhook chờ khóa DB, token Zalo/Stringee hết hạn): **không chạy lại ở D3-06**. Kết quả của D1-11 và D2-09 vẫn là bản ghi mới nhất (dot-1 mục 5, dot-2 mục 5); E2 (webhook chờ khóa DB ghi, không giới hạn), E3 (phục hồi ~15 phút sau kill worker), E10 (phụ thuộc cron 5 phút) **vẫn mở**. Sự cố AI: lỗi 100% và hết hạn mức đã đo ở D2-09 (bot tắt chế độ trả lời) và hết hạn mức đã đo lại ở mục 3.5 với Draft/Auto.

## 6. Lỗi tìm thấy

Mức: Chặn / Cao / Trung bình / Thấp. Đỏ trước, xanh sau.

| # | Mức | Lỗi | Trạng thái |
| --- | --- | --- | --- |
| E12 | **Cao** (chỉ khi job chạy lặp) | **`reply_batch` chạy lặp cùng một lượt ghi hai nháp; ở nhóm Auto gửi cho khách hai tin Bot giống nhau.** Hàm kiểm "đã có nháp chưa" rồi mới soạn (check-then-act); hai job cùng một lượt (ví dụ một job bị thử lại hoặc chạy lại trong khi bản trước còn chạy) cùng qua bước kiểm trước khi một bên ghi. `try_auto_send` khóa theo từng nháp nên mỗi nháp riêng đều "hợp lệ" và cả hai được gửi. Test cũ chạy một kết nối nên không bắt được. RQ `deduplicate` chỉ chặn khi job cũ còn trong hàng đợi, không chặn job chạy lại sau khi lượt trước đã bắt đầu | **Đã sửa** (`fix(reply)`, commit trên nhánh `d3-06-kiem-thu-dot-3`, không tách nhánh `fix/…`): khóa Redis `omni:lock:reply:<batch>` quanh `reply_batch` (giữ được suốt lúc gọi AI, không phải khóa DB), `commit` ngay sau khi lấy được khóa để đọc từ ảnh chụp mới; Redis lỗi thì chạy không khóa (fail-open). **Hạn chế đã biết:** thời gian chờ khóa, TTL khóa và timeout job RQ đều 600 giây, nên một job chờ khóa bị kẹt có thể giữ worker tới 600 giây và bị RQ giết trước khi kịp trả "locked" (nhánh "locked" thực tế gần như không bao giờ chạy); nên hạ thời gian chờ khóa xuống <= 120 giây (đề xuất, chưa làm vì đổi mã sau khi đã chạy toàn bộ test) Test: `test_double_run_of_same_batch_makes_one_draft` và `..._in_auto_group_sends_at_most_one_bot_message` (hai kết nối thật, hàng rào trước `make_draft`; đỏ: 2 nháp / 2 tin Bot; xanh sau sửa). Chưa sửa cùng kiểu cho `analyze_batch` (chạy lặp chỉ tốn thêm lời gọi AI, không có kiểm "đã có", ngoài phạm vi) |
| (môi trường) | — | 23 test (`test_draft` 2, `test_handoff` 21) đỏ khi Redis queue 11000 ở trạng thái "đang nạp dữ liệu" (`ConnectionError` từ `frappe.enqueue` chưa mock); xanh với Redis tạm khỏe | Không sửa test (không nới lỏng). Ghi vào docs/12 mục D |
| (quan sát) | Thấp | Lần chạy tải đầu có webhook p95 16,9 giây (mục 3.6) | **Mở**, không tái hiện |
| (mở từ trước) | | E2, E3, E4 (số worker), E8 còn tồn dư, E9, E10 | Xem dot-1 mục 6, dot-2 mục 6 |

Đã kiểm và **không tìm thấy lỗi** ở các điểm sau, vì đây là chỗ các test cũ (Administrator, một kết nối) hay bỏ lọt: job bằng Guest cho `process_identity`, `analyze_batch`, `reply_batch`, `handoff.handoff`, `try_auto_send`, `try_holding`, `auto_send.trip` (gọi thẳng bằng Guest), ngắt mạch khiếu nại; `rate_draft "Sai"` bởi Sales User thật làm nhóm tự tắt và báo Omni Admin / Sales Manager; `send_draft`/`discard_draft`/`rate_draft`/`pause_bot`/`resume_bot` bởi Sales User thật (người không có quyền Lead bị `PermissionError`); `get_auto_status`/`enable_auto`/`disable_auto` chỉ Omni Admin; ba khách song song không tạo trùng nháp.

## 7. Bảo mật nhanh

- **Hàm whitelisted:** Đợt 3 không thêm hàm `allow_guest`; `test_only_webhook_allows_guest` vẫn chỉ cho `zalo`, `telephony_event`, `telephony_lookup`.
- **Quyền thật đã thử** (Sales User, không Administrator, không `ignore_permissions`): xem mục 6. Omni Admin **không** có quyền đọc `CRM Lead` theo mặc định nên không mở được `get_thread` (nút "Tắt tự động nhóm này" trong Hộp thư chỉ hiện với người vừa là Omni Admin vừa đọc được Lead; đường chắc chắn là biểu mẫu Nhóm câu hỏi), xem docs/12 D3-05.
- **Bí mật:** không có trong log/Error Log của các lượt tải (mọi khóa tải sinh ngẫu nhiên, không in; `redact()` giữ nguyên). Đã quét Error Log của phiên: không có token/khóa.
- **Cấu hình trên omni.test (để người duyệt biết, không tự đổi):** `bot_analyze_enabled = 1` và `bot_reply_mode = Draft` là **giá trị do phiên trước để lại**, không phải mặc định phát hành (mặc định phát hành là `0` / `Off`). Không có khóa AI và Q11 chưa chốt nên `call_tool` chặn mọi lời gọi AI thật. `sandbox_mode = 1`; `lead_scoring_enabled = 0`; `daily_token_budget = 10000000`; `bot_active_hours` và `handoff_*` rỗng. Omni Settings sau D3-06 giống hệt trước D3-06 (so từng trường) **trừ `kb_version`** (bộ đếm tăng 7546 -> giá trị lớn hơn do test thêm/xóa mục kho tri thức; không khôi phục được và không có ý nghĩa nghiệp vụ). **Phải đặt lại về `0` / `Off` trên staging/production.**
- **Chưa phủ:** `erase_customer_data` chưa xóa `Omni Call` / `CRM Call Log`.

## 8. Việc chưa kiểm được

| Việc | Vì sao chưa kiểm | Ai/việc gì để kiểm |
| --- | --- | --- |
| Chất lượng nháp / câu trả lời của AI thật; cổng `nhap` | Không khóa AI; Q11; bộ đề chưa duyệt | Mục 10 |
| Hành vi mô hình thật với chèn lệnh | Fake làm theo lệnh và bị chốt chặn bằng mã chặn; mô hình thật có thể lách theo cách khác | Cổng `nhap` có 10 ca chèn lệnh khi chạy AI thật |
| Giao diện Hộp thư (thẻ nháp, nút Gửi/Sửa/Bỏ, chấm, nút tạm dừng, nhãn "Bot đã gửi"), biểu mẫu Nhóm câu hỏi (nút bật/tắt Auto), báo cáo "Bot nháp", "Bot tự gửi" | Không trình duyệt | `uat-dot-3.md` mục 2-5, 7 |
| Notification Log / email / realtime hiển thị thật | Không UI, không mail server | UAT |
| Gửi thật qua Zalo (Bot, Sale, câu giữ chân), khung 48 giờ thật | Sandbox; không OA | Staging với OA thử, người nhận chỉ định |
| Auto thật (Q1) và mọi điều kiện eligibility trên khách thật | Chưa có 30 nháp quyết định / nhóm | Tuần chạy thử rồi `measure_dot3` |
| D-06 ghi âm, tóm tắt, quyền nghe | D3-04 không làm | Chỉ sau khi cổng STT đo và đạt |
| Hiệu năng staging, supervisor thật, sự cố Redis/worker | Không staging | Lặp `send_burst.py`, `send_calls.py` (và sự cố của docs/10 mục 5) |
| Chỉ số tuần chạy thử | Cần sale thật | `measure_dot1/2/3.py` |
| S1-S7 Stringee; Z1-Z9, G7-G9 Zalo | Không tài khoản | docs/12 mục B |

## 9. Tổng hợp cho chủ dự án

### 9.1 Quyết định và việc cần người có thẩm quyền (cả dự án)

| # | Việc | Ảnh hưởng nếu chưa làm | Ghi chú |
| --- | --- | --- | --- |
| Q11 | Chính sách gửi dữ liệu hội thoại/cuộc gọi sang Claude API và dịch vụ chuyển giọng nói | **Chặn toàn bộ bot** (`call_tool` chặn lời gọi AI thật) | Kéo theo `omni_ai_allow_customer_data`, `omni_ai_unmask_pii` (che SĐT/email ảnh hưởng cổng `phan_tich`: 20 ca có SĐT) |
| Q12 | Khi nào bật `bot_reply_mode = Draft` ở môi trường thật | Đã chốt: sau khi cổng `nhap` đo bằng AI thật trên bộ đề đã duyệt và đạt | Hiện chưa đo |
| Q1 | Bot tự gửi hay chỉ soạn nháp | **Chặn mọi nhóm Auto** | Đề xuất: nháp, mở Auto theo nhóm khi đạt chuẩn. Tính năng đã xây, mặc định TẮT hai tầng |
| `omni_auto_send_allowed` | Khóa hạ tầng trong site_config | Vắng = không ai bật được Auto | **Chỉ người vận hành đặt, sau Q1**; không đặt ở dev/staging khi chưa cho phép rõ; D3-06 chỉ đặt trong phạm vi test/tiến trình worker đo |
| Q3 | STT chưa chọn nhà cung cấp | D3-04 không làm được | Q13, Q14: danh sách nhà cung cấp, 20 ghi âm, ngưỡng |
| Q5 | Bảng điểm Lead (mẫu `bang-diem-lead-mau.csv` chỉ là mẫu) | Chấm điểm giữ tắt | |
| Q6 | Giờ bot hoạt động, câu ngoài giờ | Mặc định tạm: `bot_active_hours` trống = giờ làm việc; câu giữ chân mặc định ("sớm nhất có thể") | Cần chốt, không phải mặc định chính thức |
| Q7 | Thời hạn lưu dữ liệu, ghi âm, ai được xem | Mặc định tạm 180/365 ngày | Cộng điều khoản dữ liệu nhà cung cấp STT |
| Q8 | Kênh báo cho sale khi chuyển người | Mặc định tạm `system` (Notification Log + realtime); email chỉ khi cấu hình | |
| Q4 | Quy tắc gán Lead cho sale | Lead mới có thể chưa có người phụ trách; không có người nhận thông báo Handoff nếu chưa đặt người dự phòng | Gắn với `callback_fallback_user`, `assignment_fallback_users` |
| Q9, Q10 | Có tạo Lead khi chỉ follow OA; tin vào Deal | Mặc định (không / có) | Xác nhận |
| S1-S7 | Stringee: khóa ký webhook (S1 là điều kiện chặn tổng đài), JSON tra cứu, Call log id, ghi âm... | Tổng đài không dùng được thật | docs/12 mục B3 |
| Z1-Z9, G7-G9 (Đợt 1) | Công thức chữ ký, payload, OAuth, mã lỗi, khung 48 giờ/7 ngày, echo của Zalo | **Chặn Zalo thật** | docs/12 mục B2 |
| R1 | Gộp Lead sai vì tín hiệu trung bình | Rủi ro gộp nhầm khách | dot-1 mục 9.1 |
| Xóa dữ liệu | `erase_customer_data` chưa phủ `Omni Call` (SĐT, `raw_events`) và `CRM Call Log` | Không dùng được cho yêu cầu pháp lý với cuộc gọi | Mở rộng hàm xóa, quyết phạm vi |
| E6 | Job nền chạy bằng Guest (chủ bản ghi/người sửa = Guest) | Đợt 3 đã thử: các đường bot làm được việc bằng Guest. Còn lại là quyết định ADR: đặt người dùng hệ thống ở `process_identity` hay chấp nhận | |
| Redis 11000 | Redis queue của bench dev hỏng RDB, `bench start` khởi động lại liên tục | Test tích hợp skip/lỗi tùy trạng thái; staging/production cần Redis khỏe | Chủ bench xử lý; D3-06 dùng Redis tạm |
| Worker | Số worker `omni` theo `số_worker / 6` khách/giây; E9 debounce 0 cho Telephony; supervisor | Tin -> Lead chậm khi bão tin | dot-1 mục 6, dot-2 E9 |
| E2/E3/E10 | Webhook chờ khóa DB, phục hồi ~15 phút sau kill worker, phụ thuộc cron 5 phút | | Mở từ Đợt 1/2 |
| Kho tri thức | 0 mục thật trên omni.test; trần `KB_MAX_ITEMS = 150` (vượt thì bot không soạn cho tới khi có ADR truy hồi) | Mọi nháp là Handoff | Phụ trách kho nhập kho thật đã duyệt |
| Bộ đề | 220 ca Synthetic chưa duyệt, 0 ca Real | Cổng `nhap` và `phan_tich` không đo được | Người duyệt bộ đề, `build_from_real` (cần Q11) |
| Cấu hình còn lại | `holidays`, `callback_fallback_user`, `alert_recipients`, Fields Layout CRM (điểm/tóm tắt bot), nginx `client_max_body_size`, rate limit | | dot-1/dot-2 |
| Công tắc | `bot_analyze_enabled = 0`, `bot_reply_mode = Off` khi phát hành; `developer_mode` TẮT; `zalo_skip_signature` TẮT; scheduler bật (trên omni.test scheduler tắt nên các cron chạy tay) | | |

### 9.2 Điều kiện trước khi cho sale dùng thật (tuần chạy thử Đợt 3)

1. Xong 9.1 các dòng Q11, Q12, Q6, Q8, khóa API, kho tri thức thật đã duyệt, bộ đề đã duyệt; cổng `nhap` đạt bằng AI thật; ghi mã `Omni Eval Run`.
2. Staging có OA Zalo thử; Z1/G7... đã đối chiếu; lặp lại tải ở staging (số ở mục 3 chỉ là máy dev).
3. `bot_reply_mode = Draft`, mọi nhóm Auto tắt, **không** `omni_auto_send_allowed`; `sandbox_mode` bật 1 ngày rồi tắt có chủ đích với người nhận thử chỉ định.
4. Redis khỏe, supervisor có `bench worker --queue omni` theo `số_worker / 6`, scheduler bật; `alert_recipients` điền.
5. Chạy `uat-dot-3.md` mục 1-5 và 8; điều kiện dừng ngay ở mục 8 của tệp đó.

### 9.3 Khuyến nghị phát hành

| Phần | Khuyến nghị | Công tắc |
| --- | --- | --- |
| Kênh email, Zalo OA, Lead, chống trùng (Đợt 1) | **Phát hành sau khi** xong dot-1 mục 9.1 trên staging | `Omni Channel.enabled`, `sandbox_mode` |
| Tổng đài (Đợt 2) | **Phát hành sau khi** xong dot-2 mục 9.1 (S1, ...) trên staging có tổng đài thử | `Omni Channel.enabled` của kênh tổng đài |
| Bot phân tích + chấm điểm | **Chưa phát hành** cho tới khi Q11, khóa API, cổng `phan_tich` đạt | `bot_analyze_enabled = 0`, `lead_scoring_enabled = 0` |
| Bot nháp cho sale duyệt + chuyển người (Đợt 3) | **Chưa phát hành**; **phát hành sau khi** cổng `nhap` đạt trên AI thật + Q11/Q12/Q6/Q8 + tuần chạy thử có sale | `bot_reply_mode = Off` |
| Bot tự gửi theo nhóm (Auto) | **Chưa phát hành**; chỉ sau Q1 và dữ liệu thật của từng nhóm | `omni_auto_send_allowed` vắng; `auto_send_enabled = 0` |
| Ghi âm, tóm tắt cuộc gọi (D3-04) | **Không có** (bỏ qua) | `recording_enabled = 0` |

### 9.4 Khuyến nghị cho 3 tháng tiếp theo

**Chỉ dựa trên dữ liệu thật sau khi có; hiện chưa có dữ liệu nào, nên chưa thể nêu nhóm cụ thể nào "nên mở Auto".** Quy trình đề xuất, mỗi bước có tiêu chí lấy từ số đo, không từ phán đoán:

1. **Tháng 1 (chuẩn bị, không có khách thật):** đóng các quyết định chặn ở 9.1 (Q11, Q12, Q6, Q8, Z1/G7, S1), nhập kho tri thức thật, người duyệt bộ đề, chạy cổng `nhap` bằng AI thật. Nếu trượt, sửa câu lệnh `reply_v1` hoặc kho theo bảng "ca trượt theo loại"; sau đó không đổi câu lệnh/mô hình nếu không qua cổng `doi_cau_lenh` (không giảm quá 2 điểm).
2. **Tháng 2 (Draft, sale thật):** tuần chạy thử rồi mỗi tuần chạy `measure_dot3`. Đọc `draft_quality.by_group` và `serious_errors`. **Ứng viên mở Auto** là nhóm thỏa đồng thời: >= 30 nháp đã quyết định trong 14 ngày, >= 95% gửi nguyên văn/sửa nhẹ, 0 nghiêm trọng, và không phải nhóm `always_handoff` (Hỏi giá, Khiếu nại). Về bản chất, nhóm chỉ trả lời thông tin không kèm giá/thời hạn (ví dụ giờ làm việc, địa chỉ) ít rủi ro hơn vì chốt chặn 3 (tiền/thời hạn/cam kết ngoài kho) không bao giờ kích hoạt; đó là suy luận về rủi ro, **không phải dữ liệu**, và không thay cho tiêu chí số liệu.
3. **Tháng 3 (nếu và chỉ nếu Q1 chốt):** mở Auto cho MỘT nhóm đủ điều kiện, với `sandbox_mode` tắt chỉ cho người nhận thử đầu tiên; theo dõi báo cáo "Bot tự gửi" và `breaker_trips`; mỗi lần tự tắt phải có người xem nguyên nhân trước khi bật lại (hiện chưa có cooldown, docs/12 D3-05).
4. **Kho tri thức cần bổ sung gì:** lấy từ `measure_dot3.handoff_gaps` sau tuần chạy thử: mục `outside_knowledge_base_by_group` xếp theo nhóm cho biết nhóm nào có nhiều ca bot chuyển người vì ngoài kho (cờ `kb_rong`, `*_ngoai_kho`); các nhóm `always_handoff` được loại khỏi việc "bổ sung kho" (Handoff theo thiết kế). Cũng đọc nháp bị sale sửa nặng hoặc bỏ, để biết câu trả lời nào trong kho lỗi thời. **Hiện chưa có số ca nào.**
5. Song song: giải E9/E10, quyết E6, mở rộng `erase_customer_data`, và quyết định D3-04 sau khi có kết quả thử STT (`stt-thu-nghiem.md`).

## 10. Cổng `nhap`

Lệnh: `bench --site omni.test execute omni_crm.bot.eval.run.run_cli --kwargs "{'gate': 'nhap'}"` (không client AI), chạy ở D3-06 trên Redis tạm:

| Hạng mục | Kết quả |
| --- | --- |
| Lần chạy | [[NHAP_RUN]] |
| Bộ đề | 220 ca, tất cả `Synthetic`, chưa duyệt, 0 ca `Real` |
| Hành động đúng / Câu trả lời đạt / Lỗi nghiêm trọng | **chưa đo** / **chưa đo** / **chưa đo** |
| Cổng `doi_cau_lenh`, `phan_tich` | **chưa đo** (cùng lý do); không so sánh suy giảm bằng số của Fake |
| Qua cổng | **Không** (chưa đo = không qua) |

Điều kiện đo được, theo thứ tự: (1) khóa Claude API và Q11 chốt; (2) bộ đề đã duyệt: người duyệt chỉnh 220 ca Synthetic và chấm lại 20% (`regrade`), thêm ca `Real` đã che thông tin cá nhân (`build_from_real`, cần Q11); (3) chạy `run_cli(gate='nhap', allow_real=True, ...)` bằng AI thật với kho tri thức thật; (4) lần chạy đạt đầu tiên trở thành mốc cho cổng `doi_cau_lenh` (không giảm quá 2 điểm).
