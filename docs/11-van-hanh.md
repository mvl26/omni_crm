# 11 · Vận hành

## 1. Màn hình giám sát (`/app/omni-monitor`, role Omni Admin)

| Khối | Nội dung | Ngưỡng cảnh báo |
| --- | --- | --- |
| Công tắc | Trạng thái mọi công tắc ở `docs/09` | — |
| Kênh | Mỗi kênh: lần nhận/gửi thành công gần nhất, lỗi gần nhất, hạn token Zalo | Không nhận tin nào > 4 giờ trong giờ làm việc; token còn < 1 giờ (tác vụ làm mới chạy khi còn < 2 giờ, nên còn < 1 giờ nghĩa là làm mới đã thất bại); làm mới token lỗi |
| Hàng đợi | Số tin New, Processing, Retry, Manual; tuổi tin New lâu nhất | New lâu nhất > 5 phút; Manual > 0 |
| Thời gian | p50/p95 từ nhận tin đến Done (24 giờ) | p95 > 2 phút |
| Chiều ra | Tin Queued/Failed/Blocked hôm nay | Failed > 5 trong 1 giờ |
| Nghi trùng | Số Candidate Open | > 20 |
| Bot (Đợt 2+) | Token dùng hôm nay / hạn mức, số Bot Run lỗi, tỉ lệ chuyển người | Dùng > 80% hạn mức; lỗi > 5% |
| Tổng đài (Đợt 2+) | Cuộc gọi hôm nay, gọi nhỡ, việc gọi lại quá hạn, số bù từ đối soát | Bù > 1%; việc quá hạn > 0 |

Nút thao tác: "Xử lý lại" tin Manual (đưa về New), "Quét ngay", "Làm mới token Zalo".

## 2. Cảnh báo

- Gửi email tới `alert_recipients` và tạo Notification Log cho Omni Admin.
- Mỗi loại cảnh báo tối đa 1 lần / 30 phút (khóa Redis), kèm số liệu và liên kết màn hình giám sát.
- Không đưa bí mật hay nội dung tin khách vào cảnh báo.

## 3. Sổ tay sự cố

| Triệu chứng | Kiểm tra | Xử lý |
| --- | --- | --- |
| Tin New tăng mãi | `bench doctor`, worker `omni` có chạy? Redis? | Khởi động worker; bấm "Quét ngay"; tăng `background_workers` nếu do tải |
| Nhiều tin Manual | Xem `last_error` nhóm theo lỗi | Sửa nguyên nhân, chọn tin, "Xử lý lại" |
| Zalo không gửi được | Hạn token, lỗi làm mới, quyền OA | "Làm mới token"; không được → cấp quyền lại qua luồng OAuth |
| Không nhận được tin Zalo | Cấu hình webhook phía Zalo, xác thực tên miền, chứng chỉ HTTPS, log 401 | Sửa cấu hình; nếu lỗi chữ ký hàng loạt → kiểm tra secret |
| Bot trả lời sai với khách | Tìm Bot Draft (`mode=Auto`), Bot Run, nhóm câu hỏi | **Tắt `auto_send_enabled` của nhóm** ngay (nút "Tắt tự động nhóm này" trong Hộp thư, hoặc chấm "Sai" tin bot: nhóm tự tắt); tắt tất cả: bỏ `omni_auto_send_allowed` khỏi site_config (tin Bot đang chờ sẽ `Blocked` và nháp quay về Draft; câu giữ chân `System` liên kết nháp Handoff cũng bị `Blocked` lúc gửi khi khóa hạ tầng hoặc nhóm đã đóng); sale gửi đính chính; sửa kho tri thức; chạy lại bộ đề |
| Sale báo nháp bot sai/bất thường (chế độ Draft) | Báo cáo "Bot nháp" (tỉ lệ bỏ, chấm Sai, nghiêm trọng theo nhóm/tuần); Bot Draft có `rating`/`critical` | Đặt `bot_reply_mode = Shadow` hoặc Off (sale không còn nhận nháp mới); sửa kho tri thức; chạy lại bộ đề. Nháp đã gửi là tin của sale (`source=Sale`, `bot_draft` truy ngược được) |
| Chi phí AI tăng vọt | Bot Run theo giờ, kind | Giảm hạn mức; kiểm tra vòng lặp gọi lại |
| Lộ `webhook_token` tổng đài | — | Đổi token trên Omni Channel và phía nhà cung cấp (Stringee: `docs/huong-dan/cau-hinh-stringee.md` mục 6) |

## 4. Quay lui

| Mức | Làm gì | Ảnh hưởng |
| --- | --- | --- |
| 1 | Tắt công tắc phần lỗi (bot, nhóm câu hỏi, chiều ra, kênh) | Chỉ phần đó dừng; dữ liệu vẫn được lưu |
| 2 | Đặt `bot_reply_mode = Off` và `bot_analyze_enabled = 0` | Hệ thống thành "kênh vào CRM" như Đợt 1 |
| 3 | Deploy lại phiên bản app trước (git tag) + `bench migrate` | Cần quy trình deploy; migration phải tương thích ngược trong mỗi đợt |
| 4 | Tắt `enabled` của kênh | Tin vẫn được lưu, không xử lý; xử lý bù sau |

Quy tắc deploy: mỗi đợt gắn tag `dot-N-rcX`; migration chỉ **thêm** trường/DocType trong một đợt, không xóa hay đổi kiểu trường đang dùng.

## 5. Nhịp vận hành

| Tần suất | Việc | Người |
| --- | --- | --- |
| Hằng ngày | Xử lý tin Manual, nghi trùng, việc gọi lại quá hạn | Quản trị hệ thống |
| Hằng tuần | Xem nháp bị sửa/bỏ, cập nhật kho tri thức, chạy lại bộ đề | Phụ trách kho tri thức |
| Hằng tháng | Báo cáo kênh, chi phí AI, rà quyền | Chủ dự án |
| Hằng quý | Xem lại bảng điểm Lead theo tỉ lệ chốt thực tế | Trưởng sale |

## 6. Dữ liệu và lưu trữ

- `raw_payload` xóa sau `raw_payload_retention_days`; ghi âm xóa sau `recording_retention_days` (tác vụ đêm).
- Sao lưu DB hằng ngày (bench backup) và kiểm tra khôi phục mỗi quý.
- Yêu cầu xóa dữ liệu của khách: Omni Admin dùng thao tác "Xóa dữ liệu khách" trên Lead → xóa nội dung tin vào/ra, ghi âm, bản ghi bot của Lead, cuộc gọi, và (V2-02) dữ liệu cá nhân của Contact là chính người của Lead (họ tên, SĐT, email, ghi chú; đặt `omni_consent = Từ chối`; giữ bản ghi ẩn danh + khóa HMAC để lần nhập sau không tạo lại), giữ bản ghi kỹ thuật ẩn danh. Một người liên hệ cụ thể: `omni_crm.api.admin.erase_contact_data(contact, reason)`.

## Lưu ý D1-05: khóa tín hiệu `sig:`

Khóa Redis `omni:lock:sig:{SĐT/email}` (ADR 0003) có timeout 60 giây và KHÔNG được gia hạn; chờ tối đa 20 giây rồi ném `TransientError` (tin thử lại theo backoff). Nếu bước tìm + tạo Lead một lô kéo dài hơn 60 giây (ví dụ gọi mạng chậm, DB nghẽn) khóa hết hạn và lượt khác có thể chen vào, mất bảo đảm chống trùng. Triệu chứng: nhiều tin `Retry` với lỗi "Khóa tín hiệu định danh đang bận", hoặc Candidate cùng SĐT tăng bất thường. Khi gặp, kiểm tra độ trễ DB trước.

## Lưu ý D1-08: developer_mode

`zalo_skip_signature` (bỏ qua xác thực chữ ký webhook Zalo) chỉ có tác dụng khi `developer_mode` của site bật (đọc bằng `cint`, chuỗi "0" là tắt). **Cấm bật `developer_mode` ở production**: nó mở luôn đường bỏ qua chữ ký. Kiểm tra `bench --site <site> console` -> `frappe.conf.developer_mode` trước mỗi lần phát hành.

## Lưu ý D1-09: tắt chiều ra của kênh

Tắt `outbound_enabled` của một kênh thì mọi tin ra đang `Queued` của kênh đó chuyển `Blocked` với lý do "Kênh đã tắt chiều ra" khi worker/tác vụ quét chạm tới. Bật lại KHÔNG tự gửi lại những tin đã `Blocked`: sale phải gửi lại thủ công từ Hộp thư.

## Lưu ý D1-10: giám sát, cảnh báo, xóa dữ liệu khách

- **Màn hình** `/app/omni-monitor` (chỉ Omni Admin): công tắc, kênh (nhận/gửi gần nhất, lỗi, hạn token Zalo), hàng đợi, p50/p95 24 giờ, chiều ra hôm nay, nghi trùng, danh sách tin Manual để "Xử lý lại", "Quét ngay", "Làm mới token Zalo". Tự làm mới 30 giây. Số liệu p50/p95 = `modified - creation` của tin Done (worker đặt `modified` lúc Done); "Failed trong 1 giờ" tính theo `creation` của tin ra. Khối Bot (D2-04): token hôm nay / hạn mức, số lần gọi AI và lần lỗi trong 24 giờ, số lần bỏ qua do hạn mức. Cảnh báo: dùng > 80% hạn mức (`ai_gan_han_muc`), lỗi > 5% khi có ≥ 20 lần gọi trong 24 giờ (`ai_loi_nhieu`), hết hạn mức (`ai_het_han_muc`, 1 lần/ngày), ngắt mạch đang mở (`ai_ngat_mach`, D2-09: AI lỗi liên tiếp nên bot tạm không gọi AI; không phụ thuộc tỉ lệ lỗi 24 giờ vì các lượt bị ngắt mạch không tính là lỗi). Bộ đếm token là khóa Redis `omni:ai:tokens:<ngày>`, tự dựng lại từ tổng `Omni Bot Run` khi mất. Khối Tổng đài thuộc Đợt 2.
- **Cảnh báo** `monitoring.alerts.run_checks` chạy mỗi 5 phút cùng tác vụ quét. Luật: tin New cũ nhất > 5 phút; Manual > 0; p95 > 2 phút; tin ra Failed > 5 trong 1 giờ; Candidate Open > 20; kênh bật không nhận tin > 4 giờ làm việc (chỉ trong giờ làm việc); token Zalo còn < 1 giờ hoặc lần làm mới gần nhất lỗi (không áp khi sandbox bật). Chống lặp 30 phút mỗi loại (mỗi kênh với luật theo kênh) bằng khóa Redis `omni:alert:<loại>:<kênh>`; xóa khóa này (`bench --site <site> clear-cache` hoặc xóa key) để buộc gửi lại. Nếu Redis cache lỗi, cảnh báo vẫn gửi (có thể lặp). Muốn tắt thư: để trống `alert_recipients`.
- **Tắt worker 10 phút** để thử: sau ~5-10 phút nhận đúng một thư "Tin chờ xử lý quá lâu" (và thêm một thư sau mỗi 30 phút nếu vẫn kẹt).
- **Sau khi worker bị `kill -9`**: tin của các khách đang xử lý có thể đứng yên tới ~15 phút (job zombie trong RQ chặn đẩy lại; không mất tin) và cảnh báo "tin chờ lâu" sẽ nổ. Chấp nhận, xem ADR 0007 (đề xuất).
- **Webhook và khóa DB**: webhook chờ khóa DB tối đa `omni_webhook_lock_wait_seconds` (site_config, mặc định 5 giây); khi lưu tin hết hạn chờ thì trả 503 và không lưu dở (nhà cung cấp có gửi lại hay không: chưa xác nhận, docs/12 Z10/S8); khi chỉ ghi dấu giám sát `last_success_at` hết hạn chờ thì tin đã lưu và vẫn trả 200.
- **Dọn raw_payload** 02:00 hằng đêm, theo lô 500, tối đa ~240 giây mỗi lần, chỉ tin đã xử lý xong; đặt `raw_payload_retention_days = 0` để tắt.
- **Xóa dữ liệu khách**: trang `omni-monitor` > "Xóa dữ liệu khách" (Lead + lý do + xác nhận) hoặc API `omni_crm.api.admin.erase_customer_data`. Xóa nội dung tin vào/ra theo Lead và các Identity của Lead, ghi Comment (lý do, người làm); không xóa Lead. LƯU Ý: chưa xóa Communication do timeline tạo, tên/SĐT/email trong Identity, `identity_key`, Comment liên kết (xem docs/12 mục D) nên chưa đủ cho yêu cầu pháp lý đầy đủ.
- **Email Account của Frappe** (kênh email, docs/05): trên Email Account dùng cho kênh phải để cờ tạo Lead của CRM = 0 và `append_to` trống, nếu không CRM tự tạo Lead song song với Omni (trùng Lead).
- **Supervisor/worker**: production phải có tiến trình `bench worker --queue omni`, tối thiểu 2 worker (debounce 5 giây giữ 1 worker mỗi khách; 1 worker xử lý ~20 khách khác nhau mất ~100 giây). Thiếu worker thì tin New tăng và cảnh báo "tin chờ lâu" nổ.
- **Redis queue** phải chạy: không có thì không enqueue được job nền, không tạo được Comment trên Lead (hook CRM enqueue).
- Giữ nguyên các lưu ý D1-05 (`sig:`), D1-08 (cấm `developer_mode` ở production) và D1-09 (tắt `outbound_enabled` => tin Blocked, không tự gửi lại) ở trên. Bảo vệ 1 MB cho webhook cần đặt thêm `client_max_body_size 1m;` ở nginx cho `/api/method/omni_crm.api.webhooks.*` (code đã chặn ở tầng ứng dụng nhưng nginx chặn sớm hơn).

## Lưu ý D1-11: kiểm thử tải, sự cố, nghiệm thu Đợt 1

Số liệu và kết luận đầy đủ ở `docs/nghiem-thu/dot-1.md`. Điều vận hành rút ra:

- **Số worker `omni`.** Mỗi lượt xử lý giữ một worker khoảng `debounce_seconds` (mặc định 5 giây) cộng thời gian xử lý (~1 giây), kể cả khi lượt đó chỉ có một tin. Với nhiều khách khác nhau nhắn rải đều, công suất xấp xỉ `số_worker / 6` khách mỗi giây. Đo thật (máy dev 16 nhân): 4 worker xử lý ~0,75 tin/giây khi mỗi khách chỉ nhắn một tin mỗi lượt, backlog tăng khi tin đến 1,67 tin/giây; 12 worker giữ backlog dưới ~200 tin và cạn trong vài phút. Quy tắc: `số_worker >= tin_mỗi_giây_ở_giờ_cao_điểm x 6 x 1,5`. Khi khách nhắn dồn (nhiều tin cùng khách) các tin gộp thành một lượt nên công suất cao hơn nhiều (4 worker xử lý 6.000 tin dồn của 300 khách trong ~7 phút). Cảnh báo "Tin chờ xử lý quá lâu" (> 5 phút) là tín hiệu để thêm worker.
- **URL webhook.** Đặt `?ch=<tên kênh>` trên URL (đặt tên kênh không có khoảng trắng cho dễ cấu hình). Từ D1-11 mã đọc `ch` từ query string của request; trước đó Frappe bỏ query khi thân JSON nên webhook thật trả 404.
- **Khóa DB kéo dài.** Webhook không tự giới hạn thời gian chờ khóa của DB: khi bảng tin vào bị khóa, webhook chờ tới `innodb_lock_wait_timeout` (50 giây mặc định) hoặc `gunicorn -t` (120 giây) thay vì trả lời sau 5 giây; Zalo gửi lại thì khử trùng lặp xử lý (xem docs/12 mục D). Kiểm tra độ trễ DB khi thấy p95 webhook tăng.
- **Redis queue chết.** Webhook vẫn lưu tin và trả 200 (lỗi enqueue bị bắt); tin ở trạng thái `New`. Khi Redis lên lại, job đã mất, tác vụ quét (`requeue_stuck`, mỗi 5 phút, tin New > 2 phút) đẩy lại. Chắc chắn scheduler của site chạy (`bench --site <site> scheduler status`), nếu không thì bấm "Quét ngay" ở `/app/omni-monitor`.
- **Worker bị kill (`kill -9`, mất điện).** Không mất tin, nhưng phục hồi chậm: đo thật ~15 phút. Tin của các khách đang xử lý vẫn `New`; job bị bỏ lại trong StartedJobRegistry của RQ làm lần đẩy lại của tác vụ quét (dùng `deduplicate`) không có tác dụng cho tới khi RQ dọn kho đăng ký (timeout job 300 giây + chu kỳ bảo trì), sau đó lần quét kế tiếp đẩy được. Triệu chứng: cảnh báo "Tin chờ xử lý quá lâu" ngay sau khi khởi động lại worker. Cứ để tự phục hồi, hoặc bấm "Quét ngay" sau khi kho đã dọn. Tin kẹt `Processing` quá 15 phút được chuyển `Retry` như docs/03 mục 6. Khóa theo khách hết hạn sau tối đa 60 giây. Xem docs/12 mục D.
- **Đo chỉ số nghiệm thu.** `bench --site <site> execute omni_crm.tests.acceptance.measure_dot1.run --kwargs '{"start": "...", "end": "..."}'`. Giờ `start`/`end` theo múi giờ của site (kiểm tra `System Settings.time_zone`; trên omni.test lệch so với giờ máy).

## Lưu ý D2-03: gọi nhỡ, việc gọi lại, đối soát đêm

- **Màn hình** khối Tổng đài thêm: số việc gọi lại quá hạn, kết quả đối soát đêm của từng kênh (ngày, tổng, đã có, bù thêm hoặc LỖI) và **số cuộc gọi nhỡ chưa xác minh** (bộ đếm cache `omni:telephony:unverified:<kênh>`, giữ 7 ngày, tăng mỗi lần `verify_missed_call` trả False/None). Số này tăng đều nghĩa là sự kiện nhỡ tới webhook mà Call log của nhà cung cấp không có (hoặc API lỗi): kiểm tra kênh có bị giả mạo sự kiện (lộ `webhook_token`) hay API Stringee lỗi. Chưa có cảnh báo riêng cho bộ đếm này.
- **Cảnh báo mới**: việc gọi lại quá hạn > 0 (`goi_lai_qua_han`: Notification Log cho Omni Admin và từng người được giao, thư tới `alert_recipients`; chống lặp 30 phút); đối soát bù > 1% (`doi_soat_bu_nhieu`); đối soát lỗi hoặc `list_calls` chạm giới hạn trang (`doi_soat_loi`).
- **Cron**: `reconcile_yesterday` 01:30 (bật), `callbacks.sweep_pending` mỗi 5 phút. Tắt đối soát: bỏ chọn `enabled` của kênh hoặc để `sandbox_mode` bật. Cần scheduler của site chạy.
- **Sổ tay**: không có việc gọi lại dù có cuộc gọi nhỡ -> cuộc gọi chưa có Lead (ẩn số: cố ý không tạo), kênh tắt, hoặc worker `omni` không chạy (tác vụ quét tạo bù sau tối đa ~10 phút). Việc gọi lại không có người nhận -> chưa đặt `callback_fallback_user` và Lead chưa có chủ.
- **Việc cần người phụ trách điền**: `Omni Settings.holidays` (danh sách ngày nghỉ lễ hằng năm); mặc định rỗng nên hạn gọi lại vẫn tính ngày lễ là ngày làm việc.

## Lưu ý D2-09: tải cuộc gọi, sự cố AI, nghiệm thu Đợt 2

Số liệu và kết luận đầy đủ ở `docs/nghiem-thu/dot-2.md`. Điều vận hành rút ra:

- **Job nền chạy bằng Guest.** Job do worker xử lý tin đẩy ra (việc gọi lại, phân tích, chấm điểm, bot trả lời) thừa hưởng người dùng Guest của webhook. Việc nào cần quyền (giao việc `CRM Task`) phải tự đặt người dùng hệ thống (`process_missed_call` đã làm); test chạy bằng Administrator nên không thấy lỗi quyền. Triệu chứng nếu quên: Error Log "Omni: lỗi tạo việc gọi lại" kèm `loai_loi=PermissionError`, việc gọi lại chỉ xuất hiện sau tác vụ quét 5-10 phút. Từ D2-09 log lỗi này kèm loại lỗi và traceback đã che.
- **Đồng thời.** Tạo `Omni Call` và việc gọi lại phải tránh khóa khoảng (không `SELECT ... FOR UPDATE` trên khóa chưa tồn tại) và phải khóa trước mọi lần đọc thường (ảnh chụp REPEATABLE READ). Đã sửa và có test hai kết nối thật (`test_dot2_integration`). Triệu chứng nếu tái phát: tin cuộc gọi `Retry` (deadlock 1213), Lead có hơn 1 việc gọi lại mở.
- **Công suất cuộc gọi.** Cuộc gọi dùng chung `debounce_seconds` với tin nhắn nên mỗi cuộc gọi giữ worker ~6 giây: công suất ~`số_worker / 6` khách/giây (đo: 8 worker, 10 cuộc/giây -> nhận -> Done p95 89 giây). Xem xét debounce 0 cho Telephony (docs/12 mục D).
- **Redis queue chết với cuộc gọi.** Webhook vẫn lưu và trả 200 nhưng chậm hơn (đo ~210 ms thay vì ~14 ms vì mỗi request chờ nối Redis). Sau khi Redis lên: tin `New` được tác vụ quét đẩy lại (tin phải quá 2 phút; cron 5 phút); job việc gọi lại đã mất được `callbacks.sweep_pending` tạo bù (tối đa 100 cuộc/lượt, cũ nhất trước). Worker phải do supervisor khởi động lại khi mất kết nối Redis.
- **AI sập.** Sau 5 lỗi liên tiếp ngắt mạch mở 5 phút: Bot Run `Skipped Switch`, cảnh báo `ai_ngat_mach`; kênh, Lead, chấm điểm không bị ảnh hưởng; nháp Shadow vẫn ghi `Handoff` khi kho tri thức rỗng. Hết hạn mức: Bot Run `Skipped Budget`, cảnh báo `ai_gan_han_muc` (> 80%) và `ai_het_han_muc` (1 lần/ngày).
- **Tra cứu khi chuông reo.** Khi DB khóa đọc 25 giây, `telephony_lookup` vẫn trả HTTP 200 "khách mới" trong ~0,3-0,5 giây.
- **Công cụ đo.** `tests/load/send_calls.py`, `send_lookups.py`, `hold_read_lock.py`, `rq_worker.py` (worker RQ trên Redis của site, `--fake-ai`), `verify_calls.py` (chuẩn bị/đối chiếu/dọn); kênh tải dùng nhà cung cấp Stringee với khóa giả và `sandbox_mode` bật (không cần `developer_mode`). Đo chỉ số tuần chạy thử: `bench --site <site> execute omni_crm.tests.acceptance.measure_dot2.run --kwargs '{"start": "...", "end": "..."}'`.
- **Quy ước thứ tự khóa (D2-09).** Khi một đơn vị việc cần nhiều hàng: khóa **Omni Call trước, Lead sau**, và khóa **trước mọi lần đọc thường** trong giao dịch (đọc khóa/`for_update` không mở ảnh chụp REPEATABLE READ; một lần đọc thường mở ảnh chụp và sau khi chờ khóa sẽ không thấy dữ liệu bên kia vừa commit). Sau khi khóa hàng đã có, đọc lại dữ liệu cần ghi bằng đọc khóa. Không `SELECT ... FOR UPDATE` trên khóa duy nhất chưa có (khóa khoảng gây deadlock): tạo hàng bằng INSERT, đụng khóa duy nhất mới quay lại khóa hàng đã có. Tuân theo thứ tự này ở mọi mã mới để tránh deadlock chéo.

## Lưu ý D3-02: chuyển người, tạm dừng bot, thông báo

- Khi bot chuyển người (chế độ Draft), người phụ trách nhận một Notification Log + realtime. Nếu `handoff_notify_channels` có `email`, job `omni_crm.bot.handoff.email_unread_handoffs` (cron mỗi 5 phút) xếp email nhắc cho ai chưa mở thông báo sau 10 phút. Nếu không thấy email: kiểm `sandbox_mode` (sandbox không xếp thư), `Email Queue`, và người nhận có `email` trong User.
- "Đã mở" nghĩa là `Notification Log.read = 1` (sale bấm vào thông báo). Sale chỉ mở Hộp thư mà không bấm thông báo vẫn có thể nhận email nhắc; nháp đã Gửi/Bỏ thì không nhắc.
- Khách không nhận được nháp mới: xem `Omni Identity.bot_paused_until` (còn ở tương lai thì bot đang im; `reply_batch` trả `paused`). Sale bấm "Cho bot tiếp tục" trong Hộp thư, hoặc đợi hết `handoff_pause_hours`. Ai bấm, lúc nào nằm ở dòng thời gian Lead.
- Quay lui: đặt `bot_reply_mode` về Shadow/Off. Các Omni Identity đã bị im vẫn giữ `bot_paused_until` đến hết hạn.

## Lưu ý D3-05: bot tự gửi theo nhóm

- **Mặc định TẮT.** Chỉ người vận hành đặt `omni_auto_send_allowed: 1` trong `sites/<site>/site_config.json`, và chỉ sau khi chủ dự án chốt Q1 (cho phép tự gửi), cổng `nhap` đạt trên AI thật, Q11 chốt. Không đặt cờ này trên dev/test/staging chưa được phép. Kể cả khi đã đặt, không nhóm nào tự gửi cho tới khi Omni Admin bật từng nhóm (biểu mẫu Nhóm câu hỏi -> "Bật tự động gửi", có hộp xác nhận nêu số liệu 14 ngày).
- Bật thử đầu tiên nên là nhóm an toàn (ví dụ "Giờ làm việc") và `sandbox_mode` bật: tin bot sinh ra `is_sandbox = 1`, không gửi gì. Chỉ tắt sandbox để gửi thật tới đúng tài khoản thử do người phụ trách chỉ định.
- Nhóm tự tắt: Notification Log "Bot tự gửi nhóm X đã tự tắt: <lý do>" tới Omni Admin và Sales Manager; Comment `omni:auto_send:breaker:<rated_wrong|complaint|handoff_doubled>` trên nhóm. Xem tất cả bằng báo cáo "Bot tự gửi" (cột "Số lần tự tắt"). Muốn bật lại: sửa nguyên nhân (kho tri thức) rồi bật bằng nút. **Hiện không có thời gian chờ (cooldown) sau khi tự tắt**: nếu số liệu 14 ngày vẫn đạt (ví dụ ngắt mạch do khiếu nại hay Handoff, không có nháp nghiêm trọng) Omni Admin có thể bật lại ngay; chỉ tin chấm Sai kèm ghi chú hoặc đánh dấu nghiêm trọng mới làm nhóm chưa đủ điều kiện trong 14 ngày. Có cần cooldown hay không là quyết định mở (docs/12 mục D).
- **Không đặt `omni_auto_send_allowed` trong `common_site_config.json`** (áp cho MỌI site của bench, kể cả site dev/staging). Chỉ đặt trong `sites/<site>/site_config.json` của đúng site production được phép.
- Tin Bot gửi lỗi hoặc hết lượt thử (`Failed`): sale phụ trách Lead nhận Notification Log "Tin bot tự gửi bị lỗi, hãy kiểm tra trên Zalo"; nháp KHÔNG tự về Draft vì khách có thể đã nhận.
- Nếu không thấy bot tự gửi dù nhóm bật: xem `guard_flags` của nháp (`info:auto_khong_gui:<mã>`: `khung_gui`, `ngoai_gio_bot`, `bot_tam_dung`, `sale_da_tra_loi`, `phan_tich`, `khong_phan_tich`, `gioi_han_tan_suat`, `dinh_danh_khong_khop`, `kenh`, `che_do_bot`, `chot_chan`...), và `omni_auto_send_allowed`.
- Cảnh báo không có người nhận (không Omni Admin/Sales Manager nào hoạt động): Error Log "nhóm tự tắt tự gửi nhưng không có người nhận cảnh báo".

## Lưu ý D3-06: chạy lặp job bot, công cụ tải Đợt 3

- `reply.reply_batch` giữ khóa Redis `omni:lock:reply:<batch_id>` (timeout bằng `JOB_TIMEOUT`, 600 giây) trong lúc soạn, để hai job cùng một lượt không ghi hai nháp (và ở nhóm Auto không gửi hai tin Bot, E12). Khóa tự hết sau 150 giây nếu tiến trình chết; job cùng lượt chờ tối đa 100 giây rồi ghi Error Log "Omni: bot trả lời bỏ qua vì khóa lượt" và bỏ lượt đó. Nhóm Auto còn chốt thứ hai trong `try_auto_send` (không gửi nếu lượt đã có tin Bot). Redis lỗi thì job chạy không khóa.
- Đo tải Đợt 3 trên máy dev: Redis tạm cho queue (`FRAPPE_REDIS_QUEUE=redis://127.0.0.1:<cổng riêng>`) và cache (`FRAPPE_REDIS_CACHE`), gunicorn nhiều worker, `tests/load/rq_worker.py` (biến `OMNI_LOAD_D3=1` cho đáp án kho tri thức tải; `OMNI_LOAD_AUTO_INFRA=1` mở khóa hạ tầng tự gửi chỉ trong tiến trình worker, vẫn phải `sandbox_mode` bật), báo cáo `verify_calls.d3_report`, dọn `cleanup_bot` + `verify_burst.cleanup` + `cleanup_d3`. Không bao giờ đặt `omni_auto_send_allowed` trong site_config để đo.
