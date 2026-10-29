# Nghiệm thu Đợt 1 (D1-11)

Ngày lập: 2026-10-29. Người lập: agent kiểm thử D1-11. Nhánh `d1-11-kiem-thu-dot-1`. App `omni_crm`, site test `omni.test`.

## 0. Kết luận và khuyến nghị

**Khuyến nghị: "Chưa phát hành"** cho khách thật. Chuyển thành **"Phát hành sau khi sửa/quyết …"** (tuần chạy thử với 1-2 sale, có kiểm soát) khi xong các mục 1, 2, 3, 6, 7 của 9.1 và điều kiện 9.2, trong đó quan trọng nhất là đối chiếu công thức chữ ký Zalo (Z1) và lặp lại tải/sự cố trên staging thật.

Lý do ngắn: mã Đợt 1 đạt các tiêu chí đo được trên máy test (0 tin mất, 0 Lead trùng trong 2 đợt tải 6.000 tin, webhook p95 ~16 ms, Redis tắt 2 phút không mất tin), nhưng (a) phát hiện và sửa 1 lỗi chặn (E1: webhook thật trả 404), cho thấy test cũ chưa đủ gần thực tế; (b) Zalo hoàn toàn là giả nên không biết webhook thật có qua chữ ký không; (c) không có staging; (d) có 3 điểm yếu vận hành đã đo được (số worker, phục hồi sau kill worker chậm ~15 phút, webhook chờ khóa DB) và vài quyết định chưa chốt (R1, xóa dữ liệu khách).

**Giới hạn của bản nghiệm thu này (đọc trước):**

1. **Không có staging.** Mọi số đo chạy trên `omni.test` (máy dev, 16 nhân dùng chung với các bench khác, MariaDB cục bộ), với gunicorn 8 worker riêng ở cổng 8099, Redis tạm ở cổng 11099 và các worker RQ chạy từ script, không phải supervisor. Không có nginx, HTTPS, mạng thật, Zalo thật. Số đo là cận dưới về độ trễ mạng và chưa chứng minh gì về staging/production.
2. **Zalo hoàn toàn là giả.** Chữ ký test ký bằng đúng công thức trong code (Z1), nên test qua không chứng minh webhook thật sẽ qua. Không gửi gì ra ngoài (`sandbox_mode=1`, kênh tải không có token).
3. **Redis queue của bench (cổng 11000) hỏng** (đã biết, không đụng vào). 4 test tích hợp bị skip vì thế; tải/sự cố chạy trên Redis tạm.
4. **Chưa có số liệu tuần chạy thử** (cần sale thật): các chỉ số "phản hồi lần đầu", "Lead trùng ngoài đời", "tin xử lý tay", "cổng hoạt động" để trống ở mục 4.
5. **Không mô phỏng được** token Zalo hết hạn bằng HTTP thật (sandbox chặn làm mới, và không được gọi Zalo), chỉ có test mock; không có kịch bản AI (Đợt 1 chưa có bot).

## 1. Kết quả test tự động

Lệnh: `cd /home/hoangvietyeuem/frappe-bench && bench --site omni.test run-tests --app omni_crm` (ruff 0.8.1: `check` và `format --check` sạch).

```
Ran 430 tests in 212.054s

OK (skipped=4)
```

430 test = 412 của D1-01..D1-10 + 18 mới của D1-11 (`test_dot1_integration` 8, `test_security_dot1` 5, `test_measure_dot1` 5). 4 test bị skip vì Redis queue của bench (cổng 11000) hỏng, như trước D1-11. `bench --site omni.test migrate` thoát mã 0 (có dòng cảnh báo Redis 11000 không kết nối được, đã biết). Dữ liệu tải đã dọn sạch; `sites/omni.test/site_config.json` khôi phục nguyên trạng (so `cmp` giống hệt bản sao lưu).

## 2. Bảng ánh xạ kịch bản I-01...I-13 (docs/10 mục 3)

Đối chiếu theo **nội dung assert** có khớp cột "Kỳ vọng" hay không, không theo tên test. Test mới viết ở D1-11 nằm trong `omni_crm/tests/test_dot1_integration.py` (tên `test_iNN_...`).

Lưu ý: các test I-xx dùng `SignedZaloCase` gán `form_dict` bằng tay nên không đi qua `make_form_dict` của Frappe. Phần đọc request thật được phủ bởi `TestRealRequestParsing` (E1) và số đo HTTP thật ở mục 3 và 5.

| # | Kỳ vọng | Test chính | Test bổ trợ | Ghi chú |
| --- | --- | --- | --- | --- |
| I-01 | 1 Lead, 1 lượt (cùng `batch_id`), 3 dòng thời gian | `test_dot1_integration.TestI01toI02.test_i01_three_messages_one_lead_one_batch_three_timeline_entries` (webhook ký -> worker -> `get_activities` có 3 dòng) | `test_worker.test_three_new_messages_one_batch_all_done` | Mới ở D1-11 (test cũ không kiểm 3 dòng thời gian và không đi qua webhook) |
| I-02 | Gửi lại 5 lần -> 1 bản ghi | `test_dot1_integration.TestI01toI02.test_i02_same_event_redelivered_five_times_is_one_record` | `test_gateway.test_duplicates_saved_once_enqueued_once`, `test_zalo_channel.TestWebhookEndToEnd.test_signed_event_saved_once_even_if_redelivered` (gửi lại 1 lần) | Mới (5 lần, qua webhook ký) |
| I-03 | Form SĐT Zalo rồi email chữ ký cùng SĐT -> 1 Lead | `test_dot1_integration.TestI03CrossChannel.test_i03_zalo_form_phone_then_email_signature_same_phone_is_one_lead` (đi hết worker cả hai kênh) | `test_resolver.test_email_signature_phone_attaches_to_existing_lead` | Mới. Điều kiện: tên người gửi email tương thích tên Zalo (xem R1) |
| I-04 | Lỗi 2 lần rồi thành công: Done, `attempts=2` | `test_worker.test_transient_error_twice_then_success` | `test_retry.test_schedule_follows_table` | Có sẵn, khớp đúng |
| I-05 | Lỗi 3 lần: Manual, có cảnh báo | `test_dot1_integration.TestI05toI07.test_i05_three_failures_manual_and_admin_alert` | `test_worker.test_three_failures_go_manual`, `test_monitoring.TestAlerts` (luật `tin_manual`, chống lặp 30 phút, thư không lộ bí mật) | Mới ở phần "có cảnh báo" đi từ tin Manual thật qua `evaluate(build_snapshot())` |
| I-06 | Processing kẹt 15 phút: quét -> Retry -> Done | `test_dot1_integration.TestI05toI07.test_i06_stuck_processing_15_minutes_goes_retry_then_done` | `test_sweeper.test_stuck_processing_goes_to_retry` | Mới (test cũ dừng ở Retry) |
| I-07 | Tắt kênh, 10 tin, bật lại: lưu đủ, xử lý đủ sau quét | `test_dot1_integration.TestI05toI07.test_i07_channel_off_ten_messages_saved_then_all_processed_after_enable` | `test_sweeper.test_disabled_channel_not_enqueued`, `test_worker.test_channel_disabled_midway_keeps_new` | Mới |
| I-08 | Sale trả lời Zalo ở sandbox: Sent, `is_sandbox=1`, không HTTP | `test_outbound.TestSendReply.test_owner_sale_queues_reply_i08` | `test_outbound.TestSendOutbound.test_sandbox_sent_without_any_http`, `test_zalo_channel.TestSandboxBlocksAllZaloHttp` | Có sẵn, khớp |
| I-09 | Quá 7 ngày: Blocked, lý do rõ | `test_outbound.TestSendReply.test_beyond_seven_days_blocked_i09` | `test_zalo_channel.TestWindow.test_three_windows_for_sale` | Có sẵn, khớp |
| I-10 | Sai chữ ký: 401, không bản ghi | `test_webhooks.TestWebhooks.test_bad_signature_401_no_message` | `test_zalo_channel.TestWebhookEndToEnd.test_bad_signature_401_unknown_channel_404`, `test_security_dot1.TestWebhookExposure.test_unsigned_body_creates_nothing`; HTTP thật mục 7 | Có sẵn, khớp |
| I-11 | Thư tự động: Ignored, không Lead | `test_email_channel.TestIgnored.test_all_ignored_fixtures_saved_ignored_without_lead` (fixture 10 auto-reply, 11 bounce, 12 no-reply) | `test_email_channel.TestIgnored.test_ignored_mail_does_not_enqueue` | Có sẵn, khớp. Giới hạn: không đọc header `Auto-Submitted` (docs/12 mục D), thư tự động có tiêu đề lạ vẫn thành Lead |
| I-12 | Hai Lead cùng SĐT, tin mới: Lead gần nhất + 1 nghi trùng | `test_resolver.TestResolverBasics.test_two_leads_same_phone_attach_latest_plus_one_candidate` | `test_resolver.test_identity_linked_lead_but_phone_held_by_other_lead_flags_candidate` | Có sẵn, khớp |
| I-13 | Gộp hai Lead: mọi bản ghi liên quan chuyển về Lead chính | `test_merge.TestMergeLeads.test_all_references_move_and_secondary_is_kept` | `test_merge.test_every_link_to_crm_lead_in_this_app_is_covered`, `test_merge.test_failure_midway_rolls_back_everything` | Có sẵn, khớp |

Test thêm ở D1-11: `test_dot1_integration.TestRealRequestParsing` (lỗi `?ch=` với thân JSON, mục 8), `test_security_dot1` (mục 7), `test_measure_dot1` (script đo).

## 3. Kết quả tải

Công cụ: `omni_crm/tests/load/send_burst.py` (httpx, lịch gửi cố định open-loop, ký payload bằng secret kênh test lấy từ biến môi trường, tin gửi lại dùng đúng payload và chữ ký gốc sau 1 giây) và `verify_burst.py` (đối chiếu DB). Kênh `LOAD kenh` (Zalo OA, không token, sandbox bật), 300 khách giả, 10% tin gửi lại.

Môi trường đo: gunicorn 8 worker (`127.0.0.1:8099`, Host `omni.test`), 1 máy với client, MariaDB cục bộ, Redis tạm 11099, worker RQ `omni,default,short` (số worker ghi từng pha). Ngày cao điểm dự kiến giả định 2.000 tin/ngày (mặc định của prompt; **chưa có giá trị thật**).

### 3.1 Pha cao điểm: 50 tin/giây trong 2 phút (6.000 tin duy nhất)

| Mục | Kết quả |
| --- | --- |
| Tin duy nhất gửi / tổng request | 6.000 / 6.641 (641 gửi lại, 10,7%) |
| Mã trả về | 6.641 x 200, 0 lỗi, 0 timeout |
| Thời gian phản hồi webhook | p50 12,4 ms, p95 **15,5 ms**, p99 18,4 ms, max 40,4 ms |
| Worker xử lý | 4 (`omni,default,short`) |
| Thời gian tới khi New+Processing = 0 sau khi ngừng gửi | **~6,7 phút** (ngưỡng 10 phút) |
| Bản ghi tin vào / tin duy nhất | 6.000 / 6.000 (0 mất, 0 dòng thừa, 0 `dedupe_key` trùng) |
| Lead / khách giả | 300 / 300 (mỗi định danh đúng 1 Lead; 0 Nghi trùng sinh ra) |
| Nhận -> Done | p50 210 giây, p95 403 giây, max 421 giây (backlog vì chỉ có 4 worker) |

### 3.2 Pha 6.000 tin dồn trong 1 giờ (1,667 tin/giây, 300 khách, 10% gửi lại)

| Mục | Kết quả |
| --- | --- |
| Tin duy nhất / tổng request | 6.000 / 6.645 (645 gửi lại) |
| Mã trả về | 6.645 x 200 |
| Thời gian phản hồi webhook | p50 13,5 ms, p95 **16,0 ms**, p99 18,1 ms, max 34,7 ms |
| Bản ghi tin vào | 6.000 / 6.000, 0 trùng; trạng thái cuối 6.000 Done |
| Lead / khách giả | 300 / 300; 0 định danh có hơn 1 Lead; 0 Nghi trùng |
| Nhận -> Done | p50 5 giây, p95 77 giây, max 157 giây |
| Nhận tin đầu của khách -> Lead tạo | p50 117 giây, p95 155 giây (xem dưới) |

Diễn biến worker (đây là kết quả đáng chú ý nhất của đợt tải): chạy với **4 worker** đầu pha, hàng đợi **không theo kịp**: ~0,75 tin xử lý/giây so với 1,67 tin/giây đến (mỗi tin của khách khác nhau là một lượt riêng giữ worker ~5 giây debounce + ~1 giây xử lý), New tăng từ 76 lên 219 trong 3 phút. Sau khi thêm lên **12 worker** (22:37), backlog về 9 New (trạng thái ổn định, do debounce) trong ~3 phút và giữ như thế đến hết 1 giờ. Vì 300 khách nhắn tin đầu tiên trong 3 phút đầu pha nên p50/p95 "tin -> Lead" bị kéo bởi giai đoạn 4 worker; ở trạng thái ổn định chờ ≈ debounce (5-6 giây). Bảng sizing: xem `docs/11` mục D1-11. `docs/09` mục 5 ("Worker omni >= 2") **không đủ** cho tải cao; con số cần tính theo tin/giây giờ cao điểm.

Đối chiếu tiêu chí docs/10 mục 4:

| Tiêu chí | Kết quả |
| --- | --- |
| p95 webhook < 1 giây | Đạt trên máy test (16 ms). **Không có nginx/HTTPS/mạng thật**, chữ ký dùng công thức tự tính |
| Xử lý xong trong 10 phút sau đợt cao điểm | Đạt (6,7 phút với 4 worker ở pha 50/giây; ~3 phút sau khi thêm worker ở pha 1 giờ). Chỉ đạt vì pha 50/giây là sự kiện ngắn; xem cảnh báo sizing |
| Số Lead = số khách giả | Đạt (300 = 300 ở cả hai pha) |
| Số tin vào = số gửi (trừ trùng) | Đạt (6.000 = 6.000 ở cả hai pha) |
| Không mất, không trùng | Đạt trong điều kiện thử |

Chưa làm: "3 lần ngày cao điểm" theo giá trị thật (chưa có số ngày cao điểm; dùng giả định 2.000 tin/ngày x 3 = 6.000 tin dồn trong 1 giờ, tương đương 24 lần tốc độ trung bình của ngày cao điểm nếu trải 24 giờ); không đo CPU/RAM/độ trễ DB trong khi tải.


## 4. Chỉ số mục tiêu Đợt 1 (docs/00 mục 2) và cách đo

Cột "Đo được" để trống: điền sau tuần chạy thử bằng `measure_dot1.py`.

```bash
bench --site <site> execute omni_crm.tests.acceptance.measure_dot1.run \
  --kwargs '{"start": "2026-11-02 00:00:00", "end": "2026-11-09 00:00:00", "webhook_2xx_count": 1234, "uptime_pct": 99.9}'
```

`start`/`end` theo múi giờ của site (trên omni.test lệch 1,5 giờ so với đồng hồ máy). Mặc định bỏ kênh có tiền tố LOAD, PLT, TD1, DEMO, RV (dữ liệu giả); đổi bằng `exclude_prefixes`.

| Chỉ số | Ngưỡng | Cách đo (script) | Đo được |
| --- | --- | --- | --- |
| Tin bị mất | 0 | Tin New/Processing/Retry quá 10 phút + tin Manual còn đó; nếu có `webhook_2xx_count` (số request webhook trả 2xx lấy từ nhật ký nginx/gunicorn) thì so thêm với số dòng DB | |
| Lead trùng (Lead phải gộp / Lead mới) | < 3% | `Lead đánh dấu omni_merged_into trong kỳ` / `Lead có omni_first_channel tạo trong kỳ` | |
| Thời gian tin vào -> Lead | < 1 phút | p50/p95 của `Lead.creation - min(received_at)` cho Lead do Omni tạo; thêm tỉ lệ dưới 60 giây | |
| Phản hồi lần đầu (giờ hành chính) | < 15 phút | Trung vị phút từ tin đầu (nếu rơi vào giờ làm việc trong `Omni Settings.business_hours`) đến tin ra `Sale` đầu tiên (Outbound Sent không sandbox, hoặc email Sent trên Lead). Lead chưa trả lời đếm riêng | |
| Tin xử lý tay | < 1% | Tin Manual còn lại / tổng tin (trừ Ignored). Chạy mỗi ngày và cộng dồn vì "Xử lý lại" đặt `attempts=0` | |
| Cổng hoạt động | >= 99,5% | **Không đo được từ DB.** Cần bộ giám sát ngoài (vd Uptime Kuma gọi GET tới URL webhook mong đợi 405 mỗi phút). Script chỉ in khoảng lặng dài nhất giữa hai tin trong giờ làm việc để gợi ý | |

Ngoài ra Đợt 1 không có chỉ số bot/tổng đài (Đợt 2, 3).

**Định nghĩa "tin bị mất"** (phần của sản phẩm bàn giao): một sự kiện mà kênh đã gửi thành công tới cổng (HTTP 2xx) nhưng (a) không có dòng `Omni Inbound Message` tương ứng, hoặc (b) có dòng nhưng nằm ở New/Processing/Retry quá 10 phút, hoặc ở Manual mà chưa ai xử lý. (a) chỉ phát hiện được bằng cách so với nhật ký truy cập; (b) và Manual script đo được. Sự kiện mà Zalo không gửi (cổng đang hỏng) nằm trong chỉ số "Cổng hoạt động", không nằm ở đây.

Truy vấn SQL tương đương (MariaDB, thay khoảng thời gian; bỏ kênh giả bằng `channel not like 'LOAD%'`):

```sql
-- Tin kẹt/Manual
select status, count(*) from `tabOmni Inbound Message`
 where creation between :s and :e and status in ('New','Processing','Retry','Manual')
   and (status = 'Manual' or creation < now() - interval 10 minute)
 group by status;

-- Lead trùng
select (select count(*) from `tabCRM Lead` where omni_merged_into is not null and modified between :s and :e)
     / (select count(*) from `tabCRM Lead` where omni_first_channel is not null and creation between :s and :e);

-- Tin -> Lead (giây)
select l.name, timestampdiff(second, min(m.received_at), l.creation)
  from `tabCRM Lead` l join `tabOmni Inbound Message` m on m.lead = l.name
 where l.creation between :s and :e and l.omni_first_channel is not null and m.event_type != 'oa_echo'
 group by l.name, l.creation;

-- Phản hồi lần đầu (phút), cho từng Lead; lọc giờ hành chính ở script
select l.name, min(m.received_at) first_in,
       (select min(sent_at) from `tabOmni Outbound Message` o
         where o.lead = l.name and o.status='Sent' and o.source='Sale' and o.is_sandbox=0 and o.sent_at >= min(m.received_at)) first_out
  from `tabCRM Lead` l join `tabOmni Inbound Message` m on m.lead = l.name
 where l.creation between :s and :e group by l.name;

-- Tin xử lý tay
select sum(status='Manual') / sum(status!='Ignored') from `tabOmni Inbound Message` where creation between :s and :e;
```

## 5. Kết quả sự cố (docs/10 mục 5)

Chạy trên tiến trình riêng của D1-11 (gunicorn 8099, worker từ script, Redis tạm 11099, không `sudo`, không `systemctl`, không đụng Redis 11000/13000). Số liệu thật, đồng hồ máy.

| Sự cố | Cách tạo | Kết quả | Đạt? |
| --- | --- | --- | --- |
| Redis queue tắt 2 phút | `redis-cli shutdown nosave` Redis 11099 lúc 23:35:18, bật lại 23:37:41 (2 phút 23 giây; Redis không lưu đĩa nên mọi job đang chờ bị mất) | Trong lúc tắt gửi 60 tin duy nhất + 9 gửi lại (69 request): **69 x 200, p95 25,8 ms**, 60 tin lưu `New`; Error Log "enqueue định danh thất bại" 60 dòng (không chứa nội dung tin). Worker tự thử kết nối lại (8/16/32 giây). Sau khi Redis lên, tác vụ quét (chạy tay `requeue_stuck`, `new_orphan: 10` khách) đẩy lại và **cả 60 tin Done lúc 23:38:34** (~53 giây sau khi Redis lên; chờ worker nối lại). 0 mất, 0 trùng | Đạt. Lưu ý: tin chỉ được đẩy lại khi tin `New` quá 2 phút VÀ tác vụ quét chạy (cron mỗi 5 phút, nên tối đa ~7 phút; scheduler phải bật) |
| Worker bị kill giữa lượt | Gửi 400 tin (20/giây, 40 khách), sau 12 giây `kill -9` cả 12 worker (23:39:45), để chết 75 giây (khóa theo khách hết hạn), bật lại 12 worker 23:41:01 | Ngay sau khi worker mới lên, 118 tin của 12 khách vẫn `New` và **sweeper chạy tay 3 lần**: 2 lần đầu (23:41:56, 23:43:25) enqueue 12 khách nhưng **không có tác dụng**: job cũ vẫn nằm trong `StartedJobRegistry` của RQ (12 mục `rq:wip` tên đúng `omni.test::omni-identity::<khóa>`), nên suy ra `deduplicate=True` của Frappe (G4) thấy job "đang STARTED" và bỏ qua enqueue (suy luận từ trạng thái Redis và thời điểm hết kẹt, chưa đọc từng bước mã RQ). Kho đăng ký RQ chỉ được dọn ~13-14 phút sau khi kill (timeout job 300 giây + chu kỳ bảo trì 10-15 phút). Lần quét thứ 3 lúc 23:54:50 mới đẩy được và cả 118 tin Done sau ~15 giây. **0 mất, 0 trùng**, nhưng **thời gian phục hồi ~15 phút** | Đạt về dữ liệu; **chậm hơn mô tả của docs** (xem docs/12 mục D). Không chỉnh `processing_started_at` (không cần tạo giả) |
| AI lỗi/timeout | — | **Không áp dụng**: Đợt 1 chưa có bot (`omni_crm/bot/*` mới là tệp khung). Kiểm thử ở Đợt 2 | Chưa kiểm được |
| Token Zalo hết hạn | — | **Không chạy được với HTTP thật**: sandbox chặn mọi HTTP Zalo và không được gọi Zalo. Chỉ có test mock `test_zalo_channel.TestTokenRefresh`, `TestClientAndSend` (làm mới khi token hết hạn, thất bại thì cảnh báo và giữ token cũ, tin vào vẫn lưu vì webhook không phụ thuộc token) | Chưa kiểm với hệ thống thật |
| Kênh gửi lại tin | Pha tải: 641 và 645 request gửi lại đúng payload+chữ ký; I-02: 5 lần | 0 bản ghi trùng (`dedupe_key` không trùng) | Đạt |
| DB chậm (khóa bảng) | `hold_db_lock.py` giữ `SELECT ... FOR UPDATE` trên toàn bảng tin vào (khóa cả khoảng chèn) | (A) khóa 25 giây, 10 request: **tất cả chờ ~20-22 giây rồi trả 200** (p50 19,9 s, max 21,9 s), tin lưu đủ và Done. (B) khóa 62 giây, 3 request: **2 request trả 500 sau ~50 giây** (`innodb_lock_wait_timeout`), 1 request trả 200 sau 58 giây | **Không đạt yêu cầu "không quá 5 giây"**: webhook không tự giới hạn thời gian chờ khóa DB. Tin vẫn không mất nếu Zalo gửi lại (500 -> gửi lại -> lưu, đã có khử trùng lặp); 2 tin trả 500 ở (B) là hai tin không lưu, cần Zalo gửi lại (đối chiếu: 6.473 gửi, 6.471 lưu = đúng 2 tin trả 500) |

Ghi chú về "tin bị mất" ở sự cố DB: 2 tin trả HTTP 500 không thuộc định nghĩa "đã nhận 2xx". Zalo có gửi lại hay không là điều chưa xác nhận (Z8).


## 6. Lỗi tìm thấy

| # | Mức | Lỗi | Trạng thái |
| --- | --- | --- | --- |
| E1 | **Chặn** | Webhook trả 404 với mọi request thật: Frappe bỏ tham số query khi thân là JSON, nên `?ch=` mất, trong khi Zalo gửi JSON. Test cũ gán `form_dict` bằng tay nên không bắt được. | **Đã sửa** ở D1-11 (`fix(webhooks)`, đọc `ch` từ `request.args`), có test dựng Request werkzeug thật (`TestRealRequestParsing`, đỏ trước khi sửa) |
| E2 | Trung bình | Webhook không giới hạn thời gian chờ khóa DB: khóa bảng 25 giây -> mọi request chờ ~20 giây rồi trả 200; khóa 62 giây -> 2/3 request trả 500 sau ~50 giây. Không đạt "không quá 5 giây" (docs/10 mục 5). Không mất tin nếu Zalo gửi lại (Z8 chưa xác nhận). | **Mở**, chưa sửa (đổi hành vi với kênh, cần quyết định). Đề xuất: giới hạn lock wait ngắn rồi trả 503 |
| E3 | Trung bình | Sau `kill -9` worker, tin của các khách đang xử lý đứng yên ~15 phút: job zombie trong StartedJobRegistry của RQ chặn enqueue `deduplicate` của tác vụ quét tới khi RQ dọn. Không mất tin. | **Mở**, chưa sửa (đổi thiết kế, cần ADR). Giảm thiểu: supervisor khởi động lại worker nhanh, theo dõi cảnh báo "tin chờ lâu" |
| E4 | Trung bình | Số worker `omni` ở docs/09 ("≥ 2") không đủ: 4 worker xử lý ~0,75 tin/giây khác khách, tin đến 1,67/giây thì backlog tăng mãi. | **Mở** (cấu hình/vận hành): chốt số worker (docs/11 D1-11 có công thức); docs/09 đã chỉnh chú thích |

Các phát hiện khác (không phải lỗi mã, hoặc cần quyết định) nằm ở mục 9 và `docs/12` mục D.

## 7. Bảo mật nhanh

**Kết quả HTTP thật** (gunicorn riêng, `Host: omni.test`; Guest = không đăng nhập, Sales = người dùng role Sales User, không có role Omni, không sở hữu Lead được thử; POST có CSRF token):

| Hàm | Guest | Sales User |
| --- | --- | --- |
| `api.admin.reprocess`, `sweep_now`, `refresh_zalo_token`, `erase_customer_data` (POST) | 403 | 403 |
| `api.admin.monitor_snapshot`, `list_manual` (GET) | 403 | 403 |
| `api.inbox.get_thread` (Lead không của mình), `send_reply`, `request_phone` | 403 | 403 |
| `api.inbox.list_conversations` | 403 | 200 (chỉ Lead được xem, rỗng với tài khoản thử) |
| `api.inbox.system_status` | 403 | 200 (thông điệp cố định, không lộ lỗi thô) |
| `identity.duplicates.dismiss`, `merge` | 403 | 404 (cặp không tồn tại; hàm kiểm quyền ghi cả hai Lead khi có cặp thật) |
| `api.webhooks.zalo_oauth_start`, `zalo_oauth_callback` | 403 | 403 |
| `api.webhooks.zalo` GET | 405 | 405 |
| `api.webhooks.zalo` POST không chữ ký | 401 | 401 |

Chỉ `api.webhooks.zalo` có `allow_guest=True` (test `test_only_webhook_allows_guest` giữ điều này). Test tầng hàm: `test_security_dot1` (Guest và Sales User bị `PermissionError` ở mọi hàm admin; Guest ở hộp thư; Sales User không đọc/gửi vào Lead của người khác).

- **Webhook không chữ ký:** 401, không bản ghi (HTTP thật và test).
- **`zalo_skip_signature` ở production:** chỉ có tác dụng khi `developer_mode` của site bật (test `test_skip_flag_only_on_dev_site`, `test_skip_signature_needs_real_developer_mode_value`). `frappe.conf.developer_mode` trên omni.test: không đặt (tắt). **Kiểm tra bắt buộc trước phát hành** vì `developer_mode` bật là mở đường bỏ qua chữ ký (mục 9).
- **Chưa kiểm:** quyền theo Lead khi có hệ thống phân cấp bán hàng phức tạp (Sales Manager), quyền đọc `raw_payload` qua `filters` (đã biết, docs/12 mục D Review D1-06 #2).

## 8. Việc chưa kiểm được

| Việc | Vì sao chưa kiểm | Ai/việc gì để kiểm |
| --- | --- | --- |
| Công thức chữ ký, tên sự kiện, payload, OAuth, mã lỗi, khung 48 giờ/7 ngày của Zalo (Z1...Z9, G7-G9) | developers.zalo.me là SPA, không có OA/khóa thật; mọi test dùng công thức tự tính | Người có OA đối chiếu tài liệu chính thức hoặc gửi webhook thật từ OA thử, rồi chuyển Z1...Z9 sang `docs/12` mục C |
| Hiệu năng trên staging/production (nginx, HTTPS, mạng, DB riêng, supervisor) | Không có staging; chạy trên máy dev | Lặp lại `send_burst.py` trên staging, so với mục 3 |
| Làm mới token Zalo thật, token hết hạn | Sandbox chặn mọi HTTP Zalo; không được gọi Zalo | Chỉ có test mock (`TestTokenRefresh`); thử ở staging với OA thử khi sandbox tắt có chủ đích |
| Gửi tin thật, echo `oa_send_*` của tin do API gửi (Z9) | Không gửi thật | Staging với OA thử |
| Email thật qua IMAP/SMTP của công ty | Chỉ thư giả qua `InboundMail` | Staging với Email Account thử (cờ tạo Lead của CRM = 0, `append_to` trống) |
| Giao diện `omni-inbox`, `omni-monitor` trên trình duyệt | Không có trình duyệt trong phiên; mới kiểm API và `node --check` | Người duyệt mở bằng tài khoản sale và Omni Admin |
| Chỉ số tuần chạy thử (phản hồi lần đầu, Lead trùng ngoài đời, tin xử lý tay, cổng hoạt động) | Cần sale thật dùng 1 tuần | `uat-dot-1.md` rồi `measure_dot1.py` |
| Redis cổng 11000 của bench dev | Hỏng RDB, không được sửa | Chủ bench; 4 test tích hợp skip vì lý do này |
| Kịch bản "AI lỗi" | Đợt 1 chưa có bot (`omni_crm/bot/*` mới là khung 1 dòng) | Đợt 2 |

## 9. Tổng hợp cho chủ dự án

### 9.1 Phải quyết hoặc làm TRƯỚC khi bật cho khách thật

| # | Việc | Mức | Ghi chú |
| --- | --- | --- | --- |
| 1 | **Z1/G7:** đối chiếu công thức chữ ký webhook Zalo (header `X-ZEvent-Signature` hay trường `mac`, cách nối chuỗi) | Chặn | Sai thì mọi webhook thật bị 401. Test hiện ký bằng chính công thức của code nên không chứng minh gì |
| 2 | **Z2...Z9, G8, G9:** payload, OAuth, mã lỗi, khung 48 giờ/7 ngày, echo | Chặn việc gửi thật | `docs/12` mục B2 |
| 3 | **R1:** gộp Lead sai do tín hiệu trung bình (tên rỗng/một từ/cùng họ đệm; email trong nội dung) | Cao | Chọn (a)/(b)/(c) hoặc chấp nhận. `docs/12` mục D "Review D1-05, R1". Gắn với "Lead trùng" và rủi ro gộp nhầm khách |
| 4 | **Xóa dữ liệu khách chưa đủ phạm vi** (Communication, `identity_key`, Identity, Comment, `last_error` còn dữ liệu cá nhân) | Cao nếu dùng cho yêu cầu pháp lý | Chọn phạm vi xóa mở rộng; hoặc chưa dùng nút xóa cho yêu cầu thật |
| 5 | **Redis queue của bench dev cổng 11000 hỏng** | Môi trường | Chủ bench đưa RDB hỏng đi. Production/staging cần Redis queue khỏe |
| 6 | **Số worker `omni`**: docs/09 nói "≥ 2"; đo thật cho thấy cần `tin_mỗi_giây_cao_điểm x 6 x 1,5` (mục 3) | Trung bình | Chốt số worker và supervisor; thêm tiến trình `bench worker --queue omni` |
| 6b | **E2/E3** (webhook chờ khóa DB không giới hạn; phục hồi ~15 phút sau kill worker): chấp nhận hoặc ra quyết định/ADR sửa | Trung bình | Mục 6 |
| 7 | **Công tắc:** `sandbox_mode` tắt có chủ đích khi mở; `developer_mode` TẮT; `zalo_skip_signature` TẮT | Chặn | Mục 7 |
| 8 | **Q4 (quy tắc gán Lead cho sale)** chưa chốt: Lead mới chưa có người phụ trách nếu không có Assignment Rule | Trung bình | Với 1-2 sale thử có thể gán tay |
| 9 | **Q7 (thời hạn lưu dữ liệu)**: mặc định 180 ngày `raw_payload` | Trung bình | Q7 chưa chốt |
| 10 | **Q9, Q10** mặc định (không tạo Lead khi chỉ follow; ghi tin vào Deal) | Thấp | Xác nhận |
| 11 | **Q11** (gửi dữ liệu hội thoại sang AI): chưa chốt, chặn mọi lần chạy AI trên dữ liệu khách thật | Đợt 2 | Không ảnh hưởng Đợt 1 |
| 12 | Thư tự động chỉ nhận diện qua người gửi/tiêu đề (Frappe không lưu header), thư lạ vẫn thành Lead; tên có dấu từ email bị Frappe làm mất một số ký tự | Trung bình | Chấp nhận hoặc đề xuất khác |
| 13 | Tín hiệu trung bình (SĐT/email nhắc trong nội dung) không được lưu; hai Lead không nối được (docs/12 mục D D1-05) | Trung bình | Chọn cách lưu |
| 14 | Giao diện chưa kiểm trên trình duyệt; chưa có lối tắt Workspace | Trung bình | Người duyệt mở thử |
| 15 | Không có công tắc riêng cho cảnh báo, theo dõi sửa tay, trang hộp thư (D1-06, D1-10) | Thấp | Chấp nhận hoặc thêm trường (đổi DocType, cần duyệt) |
| 16 | Đặt `client_max_body_size 1m;` ở nginx cho webhook; rate limit nginx cho webhook | Thấp | `docs/11` D1-10 |

### 9.2 Điều kiện trước khi cho 1-2 sale dùng thật (tuần chạy thử)

1. Làm xong các mục 1, 2, 7 ở 9.1 trên **staging có OA thử**, kèm link tài liệu Zalo chính thức, rồi lặp lại tải ở staging (số đo mục 3 chỉ là máy dev).
2. Quyết R1 (mục 3) hoặc ghi nhận chấp nhận rủi ro bằng văn bản.
3. Redis queue khỏe, supervisor có `bench worker --queue omni` với số worker đã tính, scheduler bật (`bench --site <site> scheduler status`; trên omni.test scheduler đang tắt).
4. `alert_recipients` đã điền; người nhận biết xử lý tin Manual.
5. Email Account bán hàng đặt đúng (cờ tạo Lead của CRM = 0, `append_to` trống).
6. Người duyệt mở thử `/app/omni-inbox`, `/app/omni-monitor` bằng tài khoản sale và Omni Admin.
7. Khởi đầu với `sandbox_mode` bật 1 ngày; tắt có chủ đích với người nhận thử chỉ định.
8. Chạy `uat-dot-1.md`; điều kiện dừng ở mục 5 của tệp đó.
