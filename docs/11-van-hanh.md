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
- **Token Meta (V21-02):** `channels/meta_leads/token_check.run` (cron `10 */6 * * *`, mỗi kênh Meta Lead Ads bật ~1 lần/20 giờ) gửi cảnh báo `token_meta` (theo kênh) khi token Page không hợp lệ, sắp hết hạn, thiếu `leads_retrieval`, hoặc Graph từ chối kiểm (sai App ID/Secret). Lỗi tạm thời không cảnh báo. Xử lý: cấp token System User mới, dán vào `Meta: Page Access Token`, chờ lần kiểm kế tiếp hoặc chạy `bench --site <site> execute omni_crm.channels.meta_leads.token_check.run` (xóa `Kiểm lần cuối` của kênh trước nếu vừa kiểm).

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

## Lưu ý V2-03: nhập danh sách nhà thầu

- Chỉ Omni Admin (và System Manager). Công tắc `prospect_import_enabled` (Omni Settings) tắt thì không nhập được.
- Cách làm: tạo `Omni Prospect Import` (Desk, tìm "Omni Prospect Import"), đính kèm tệp `.xlsx` (riêng tư), bấm **Chạy thử**, đọc báo cáo (số dòng, **Cần xác nhận**, đối chiếu với TongHop), tải `error_file`, sửa lỗi, rồi **Nhập thật**. Nhập lại cùng tệp không tạo trùng. Từ dòng lệnh: `bench --site <site> execute omni_crm.prospect.importer.import_workbook --kwargs "{'file_url_or_path': '/đường/dẫn/ngoài/repo/file.xlsx', 'dry_run': True, 'now': True}"` (đường dẫn tuyệt đối chỉ chạy được từ dòng lệnh, chạy ngay trong tiến trình; trên web chỉ nhận tệp riêng tư đã tải lên). Lệnh trả về tên nhật ký (ví dụ `NHAP-2026-00004`); đọc báo cáo: `bench --site <site> execute omni_crm.prospect.importer.print_log_report --kwargs "{'log': 'NHAP-2026-00004'}"`. Nhập thật: cùng lệnh với `'dry_run': False`.
- Mẫu mở rộng: 1.000 nhà thầu / 5.000 gói thầu / 3.000 người liên hệ; tệp lớn hơn bị từ chối (giới hạn 15 MB, 5.000 / 25.000 / 15.000 dòng, không macro, không bom nén). Việc chạy trên hàng đợi `omni` (cần worker `omni`), commit mỗi 100 dòng; lỗi một dòng không ảnh hưởng dòng khác.
- Quay lui: importer không xóa gì. Muốn bỏ một lần nhập: lọc `Omni Bid Award`/Organization theo `omni_data_source` "Nhập danh sách nhà thầu (NHAP-…)" và xử lý tay, hoặc khôi phục bản sao lưu trước khi nhập.
- Không bao giờ gửi tin tự động từ importer; Lead mới nằm ở "Tiềm năng", việc liên hệ là của sale từng nhà thầu (docs/14 mục 6).

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
- **Webhook và khóa DB (V21-04):** webhook chờ khóa DB tối đa `omni_webhook_lock_wait_seconds` (site_config, mặc định 3 giây); hết hạn chờ, bế tắc hoặc mất kết nối thì tin đã xác thực được ghi vào hàng dự phòng `private/omni_spool` và webhook trả thành công của kênh; tác vụ quét mỗi phút chèn lại (ADR 0014). Chỉ trả 503 khi DB lẫn hàng dự phòng đều hỏng (hoặc công tắc `webhook_spool_disabled` bật). Trước V21-04 hết hạn chờ là 503 (có thể mất tin nếu nhà cung cấp không gửi lại: docs/12 Z10, S8).
- **Dọn raw_payload** 02:00 hằng đêm, theo lô 500, tối đa ~240 giây mỗi lần, chỉ tin đã xử lý xong; đặt `raw_payload_retention_days = 0` để tắt.
- **Xóa dữ liệu khách**: trang `omni-monitor` > "Xóa dữ liệu khách" (Lead + lý do + xác nhận) hoặc API `omni_crm.api.admin.erase_customer_data`. Xóa nội dung tin vào/ra theo Lead và các Identity của Lead, ghi Comment (lý do, người làm); không xóa Lead. LƯU Ý: chưa xóa Communication do timeline tạo, tên/SĐT/email trong Identity, `identity_key`, Comment liên kết (xem docs/12 mục D) nên chưa đủ cho yêu cầu pháp lý đầy đủ.
- **Email Account của Frappe** (kênh email, docs/05): trên Email Account dùng cho kênh phải để cờ tạo Lead của CRM = 0 và `append_to` trống, nếu không CRM tự tạo Lead song song với Omni (trùng Lead).
- **Supervisor/worker**: production phải có tiến trình `bench worker --queue omni` VÀ `bench worker --queue omni_leads` (V21-01: lead quảng cáo có hàng đợi riêng, **từ 2 worker**; `omni` theo lưu lượng chat). `omni`: tối thiểu 2 worker (debounce 5 giây giữ 1 worker mỗi khách; 1 worker xử lý ~20 khách khác nhau mất ~100 giây). Thiếu worker thì tin New tăng và cảnh báo "tin chờ lâu" nổ.
- **Redis queue** phải chạy: không có thì không enqueue được job nền, không tạo được Comment trên Lead (hook CRM enqueue).
- Giữ nguyên các lưu ý D1-05 (`sig:`), D1-08 (cấm `developer_mode` ở production) và D1-09 (tắt `outbound_enabled` => tin Blocked, không tự gửi lại) ở trên. Bảo vệ 1 MB cho webhook cần đặt thêm `client_max_body_size 1m;` ở nginx cho `/api/method/omni_crm.api.webhooks.*` (code đã chặn ở tầng ứng dụng nhưng nginx chặn sớm hơn).

## Lưu ý D1-11: kiểm thử tải, sự cố, nghiệm thu Đợt 1

Số liệu và kết luận đầy đủ ở `docs/nghiem-thu/dot-1.md`. Điều vận hành rút ra:

- **Số worker `omni`.** Mỗi lượt xử lý giữ một worker khoảng `debounce_seconds` (mặc định 5 giây) cộng thời gian xử lý (~1 giây), kể cả khi lượt đó chỉ có một tin. Với nhiều khách khác nhau nhắn rải đều, công suất xấp xỉ `số_worker / 6` khách mỗi giây. Đo thật (máy dev 16 nhân): 4 worker xử lý ~0,75 tin/giây khi mỗi khách chỉ nhắn một tin mỗi lượt, backlog tăng khi tin đến 1,67 tin/giây; 12 worker giữ backlog dưới ~200 tin và cạn trong vài phút. Quy tắc: `số_worker >= tin_mỗi_giây_ở_giờ_cao_điểm x 6 x 1,5`. Khi khách nhắn dồn (nhiều tin cùng khách) các tin gộp thành một lượt nên công suất cao hơn nhiều (4 worker xử lý 6.000 tin dồn của 300 khách trong ~7 phút). Cảnh báo "Tin chờ xử lý quá lâu" (> 5 phút) là tín hiệu để thêm worker.
- **URL webhook.** Đặt `?ch=<tên kênh>` trên URL (đặt tên kênh không có khoảng trắng cho dễ cấu hình). Từ D1-11 mã đọc `ch` từ query string của request; trước đó Frappe bỏ query khi thân JSON nên webhook thật trả 404.
- **Khóa DB kéo dài (đã xử lý ở V21-04).** Webhook giới hạn chờ khóa vài giây rồi ghi hàng dự phòng; xem mục "V21-04" cuối tài liệu. Kiểm tra độ trễ DB khi thấy p95 webhook tăng hoặc hàng dự phòng không về 0.
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

## Lưu ý V2-04: Tổng quan CSKH, nhật ký chăm sóc

- **Bật/tắt**: `Omni Settings.dashboard_enabled`, `care_log_enabled` (docs/09). Tắt không mất dữ liệu; bật lại dùng tiếp.
- **Chỉ mục mới**: `Omni Inbound Message (lead, event_type, status, received_at)` — thêm bằng `on_doctype_update` và patch `omni_crm.patches.v2.add_dashboard_indexes` (chạy lại được; `bench migrate` trên site cũ cần patch này vì JSON DocType không đổi). Migration chỉ thêm.
- **Tác vụ 17:45** (`care_log.end_of_day_digest`): một Notification Log cho mỗi sale có phiên "Chưa ghi kết quả" trong 7 ngày; bỏ qua ngày nghỉ/lễ (`business_hours.is_working_day`).
- **Đo hiệu năng** (không phải test, không dùng dữ liệu thật; nạp dữ liệu tổng hợp tiền tố `TD404P`, đo lạnh, dọn sạch):
  `bench --site omni.test execute omni_crm.tests.acceptance.measure_v204_dashboard.run --kwargs '{"leads": 25000, "messages": 100000, "repeats": 5}'`
  Kết quả trên máy dev `omni.test` (25.000 Lead, 100.000 tin vào, tất cả Lead đều đang chờ trả lời = tình huống xấu, mọi tính năng Đợt 2-3 bật), p95 mili giây: trưởng nhóm hôm nay 507, 7 ngày 541, 30 ngày 632; Sales User (1/10 Lead) hôm nay 200, 30 ngày 215. Mục tiêu docs/13 mục 4 (p95 < 800 ms) đạt trên dữ liệu tổng hợp; **chưa đo trên staging**. Trước khi thêm chỉ mục, 30 ngày là 882 ms.
- **Đổi tên nhà thầu** (CRM Organization): `Lead.organization` và `Contact.company_name` tự theo; đổi tên kiểu "gộp" bị chặn, dùng `merge_organizations`.
- **Quay lui**: tắt hai công tắc; DocType Omni Care Log chỉ thêm, không ảnh hưởng luồng cũ.

## Lưu ý V2-05: lead quảng cáo (Meta, Google, Web Form)

**Chưa thử với nền tảng thật** (không có tài khoản/token). Mọi điều dưới đây kiểm bằng payload mẫu và Graph API giả.

- **Cảnh báo mới** `khong_co_lead_quang_cao`: không có lead thật nào trong `ads_silence_hours` giờ làm việc khi có chiến dịch `Đang chạy` và kênh quảng cáo bật. Kênh Meta/Google/Web Form không còn dùng cảnh báo `kenh_im_lang` chung (vì kênh quảng cáo im lặng là bình thường khi chiến dịch dừng). Cách xử lý: xem Giám sát > số tin New/Manual của kênh; Meta: kiểm token Page còn hạn, ứng dụng còn đăng ký `leadgen`, Leads Access Manager; Google: kiểm URL webhook và `google_key`.
- **Nhắc quá hạn**: tác vụ 5 phút `omni_crm.crm_bridge.response_sla.run` nhắc người được giao khi Lead quảng cáo quá `response_sla_minutes` phút làm việc kể từ `received_at`, báo trưởng nhóm khi quá gấp đôi (mỗi người một lần, dấu là Notification Log).
- **Đối soát Meta** hằng giờ (`channels.meta_leads.reconcile.run`): bù lead sót; lỗi một biểu mẫu chỉ ghi log loại lỗi. Token Page hết hạn -> lead mới vào `Manual` (tin_manual) vì không lấy được nội dung; cấp lại token rồi bấm Xử lý lại.
- **Web Form**: lỗi 429 nhiều từ một IP là bị giới hạn tần suất (cấu hình ở Omni Settings). Honeypot trả 200 giả nên không thấy trong bảng tin.
- **Nhập CSV Google**: `bench --site <site> execute omni_crm.channels.google_leads.csv_import.run --kwargs "{'channel': '<kênh>', 'file': '<csv>', 'form_id': '<mã>'}"`. Dry run: thêm `'dry_run': True`. Chống trùng với webhook theo `lead_id`.
- **Điền ngược chiến dịch cho Lead nhập ở V2-03** (KHÔNG đăng ký trong patches.txt): `bench --site <site> execute omni_crm.patches.v2.backfill_ad_fields.execute` (dry run mặc định); sau khi duyệt: thêm `--kwargs "{'dry_run': False}"`. Chưa chạy trên staging.
- **Trước khi bật Meta**: giữ MỌI `Lead Sync Source` của crm ở trạng thái tắt (cảnh báo `dong_bo_lead_crm_dang_bat`); lead Omni ghi `facebook_lead_id` nên nếu lỡ bật thì đồng ý bị báo trùng thay vì tạo Lead đôi.
- **Phiên bản Graph API**: mặc định `v26.0` (lấy từ tóm tắt trang tài liệu Meta ngày 2026-11-02, CHƯA kiểm với API thật). Nếu sai thì mọi lead Meta vào `Manual`. Đổi bằng `omni_meta_graph_version` trong `site_config.json` (dạng `vNN.N`, giá trị khác bị bỏ qua).
- **Địa chỉ IP của Web Form**: giới hạn tần suất dựa vào `frappe.local.request_ip`, mà Frappe lấy từ phần tử ĐẦU của `X-Forwarded-For`. Nginx PHẢI ghi đè header này (`proxy_set_header X-Forwarded-For $remote_addr;`) chứ không nối thêm, nếu không người gọi xoay header để né giới hạn. Chưa kiểm trên nginx thật.
- **Lead đã được liên hệ rồi điền biểu mẫu mới**: không còn `omni_first_response_at` làm sạch trạng thái chờ; chỉ nhật ký chăm sóc do sale ghi (hoặc cuộc gọi đi được nghe máy) SAU lúc điền, hoặc tin ra, mới làm hết "Quá hạn phản hồi". Việc NHẮC quá hạn bằng thông báo chỉ áp cho Lead chưa từng được phản hồi.


## Lưu ý V2-08: chạy chiến dịch quảng cáo, tải và sự cố (đo trên site test)

Số đo đầy đủ ở `docs/nghiem-thu/v2.md`; **chưa đo trên staging**.

- **Worker và hàng đợi (đã xử lý ở V21-01, xem mục "V21-01" cuối tài liệu):** lead quảng cáo và chat từng dùng chung hàng đợi `omni`. Với 8 worker và 1,67 tin chat/giây cùng lúc, lead quảng cáo chờ p95 ~113 giây mới có người phụ trách. Giờ cao điểm cần ≥ 12 worker `omni` (hoặc quyết hàng đợi riêng). **Scheduler phải bật**: lượt thử lại, quét tin kẹt, đối soát Meta hằng giờ và nhắc quá hạn đều chạy từ scheduler.
- **Graph Meta lỗi lâu (V21-03 đã tự phục hồi):** với `max_attempts` 3, backoff "1,5" tin form Meta vẫn thành `Manual` sau ~8-10 phút Graph chết, nhưng nếu lỗi là loại tạm thời (mạng, timeout, 5xx, giới hạn tần suất; `error_kind = tam_thoi`) thì lượt đối soát hằng giờ kế tiếp tự đưa tin về hàng đợi (tối đa 200 tin/lượt, nhận trong 72 giờ, mỗi tin tối đa 3 lần) mà không cần `reprocess`. Xem thẻ "Kéo lại lead Meta" ở `/app/omni-monitor`. Cảnh báo `meta_loi_vinh_vien` (một thư gộp mỗi kênh mỗi lượt) nghĩa là có tin lỗi VĨNH VIỄN (token sai/hết hạn, thiếu quyền, lead không tồn tại, lỗi dữ liệu) hoặc tin đã kéo lại quá 3 lần: KHÔNG tự kéo lại; kiểm token Meta (cảnh báo `token_meta`), sửa nguyên nhân rồi Xử lý lại trong trang Giám sát. Không muốn chờ giờ sau: chạy `omni_crm.channels.meta_leads.reconcile.run`. Tắt: `meta_requeue_disabled` = 1. Tin Manual có từ trước V21-03 (`error_kind` trống) vẫn xử lý tay.
- **Redis queue chết:** webhook vẫn lưu tin (200); tin `New`/`Retry` được tác vụ quét đẩy lại, tối đa ~7 phút sau khi Redis lên (cron 5 phút, tin phải quá 2 phút).
- **Token Meta:** không có cảnh báo hết hạn. Dùng token System User dài hạn; đặt nhắc lịch.
- **Tải thử lại:** `omni_crm/tests/load/` có `send_ads_burst.py`, `fake_graph.py`, `ads_setup.py` (`setup`, `verify`, `cleanup`, `reconcile_with_fake_graph`), `rq_worker.py --fake-graph`. Chỉ chạy trên site test, dữ liệu tiền tố `LOADADS`, và **luôn chạy `ads_setup.cleanup` trước khi chạy bộ test** (G-5). `ads_setup.setup` nâng giới hạn tần suất form website và `cleanup` khôi phục đúng giá trị cũ.
- **Đo chỉ số chiến dịch sau tuần chạy:** `omni_crm.tests.acceptance.measure_campaign.run` (chỉ đọc).

## V21-01: hàng đợi riêng `omni_leads` cho lead quảng cáo

- **Cấu hình:** `workers.omni_leads` trong `common_site_config.json` + tối thiểu 2 tiến trình `bench worker --queue omni_leads` (docs/huong-dan/01 mục 3). Số worker `omni` theo lưu lượng chat. Thiếu khai báo thì lead quảng cáo rơi về `omni` và log có cảnh báo "chưa khai báo hàng đợi omni_leads" (tối đa một lần mỗi giờ): thấy cảnh báo này là thiếu cấu hình, không phải lỗi dữ liệu.
- **Công suất `omni_leads`:** mỗi lead giữ một worker ~`debounce_seconds` + ~1 giây xử lý, nên công suất ~`số_worker / 6` lead/giây. 2 worker = ~0,33 lead/giây = 200 lead trong 10 phút, tức **đúng bằng** tải đo (không có dư địa). Chiến dịch dồn lead nhiều hơn thì thêm worker `omni_leads`; hàng đợi `omni_leads` tăng và thời gian "tin vào -> có người phụ trách" tăng là tín hiệu.
- **Số đo (site test `omni.test`, KHÔNG phải staging; Graph API là máy chủ GIẢ):** kịch bản V2-08 (200 lead trong 600 giây thật + 1.002 tin Zalo cùng lúc, gunicorn 8, 8 worker `omni`, nay thêm 2 worker `omni_leads`):

| Chỉ số | V2-08 (chung hàng đợi `omni`) | V21-01 (`omni_leads` 2 worker) |
| --- | --- | --- |
| Lead: tin vào -> có người phụ trách p50 / p95 / max | 97,9 / **113,1** / 114,1 giây | 5,4 / **21,3** / 25,9 giây |
| Lead: nhận -> Done p50 / p95 | 97 / 113 giây | 5 / 21 giây |
| Lead mất / dòng thừa / `dedupe_key` trùng / Lead trùng / Lead không chủ | 0 / 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 / 0 (200 Lead, 200 + 200 thông báo) |
| Zalo cùng lúc: nhận -> Done p50 / p95 / max | 85 / 112 / 114 giây | 47 / 68 / 71 giây |
| Zalo mất / trùng | 0 / 0 (1.002 tin, 300 Lead) | 0 / 0 (1.002 tin, 300 Lead) |

  Tiêu chí p95 <= 30 giây: **đạt** (21,3 giây) trên site test. Webhook trả 200 cho 223/223 request quảng cáo và 1.114/1.114 Zalo; p95 webhook 27,0 và 26,8 ms nhưng p99/max cao hơn lần V2-08 (quảng cáo 6,6 / 8,8 giây; Zalo 7,1 / 10,6 giây, so với 64,7 / 100,6 ms và 38,2 / 234 ms): nguyên nhân CHƯA xác minh (nghi gunicorn khởi động nguội, 8 worker chưa được làm nóng trước khi bắn tải; Zalo không đi qua mã mới nên ít khả năng do thay đổi). Cần đo lại có làm nóng.
- **Dọn sau khi đo tải:** chạy `ads_setup.cleanup` TRƯỚC `verify_burst.cleanup` (kênh Zalo). Làm ngược lại thì Lead đã bị xóa nên `Omni Bot Draft`/`Omni Bot Run` do bot tạo cho chúng thành mồ côi và làm đỏ `test_draft`, `test_handoff`, `test_web_form` (đã gặp ở V21-01: 331 nháp + 845 lượt chạy mồ côi; đã dọn đúng các dòng tạo sau thời điểm bắt đầu tải).
- **Cảnh báo thiếu worker `omni_leads`:** nếu `omni_leads` đã khai báo, đang có job chờ mà không có worker nào lắng nghe thì luật `omni_leads_khong_worker` (email + Notification Log cho Omni Admin) nổ; snapshot có khối `lead_queue` (`declared`, `length`, `workers`; lỗi Redis thì để trống, không báo). Cảnh báo "tin chờ lâu" (New > 5 phút) vẫn là chốt chặn thứ hai.
- **Đề xuất production:** 3-4 worker `omni_leads` (2 worker là mức tối thiểu và đúng bằng tải đo, không có dư địa; Graph thật chậm hơn Graph giả nên công suất mỗi worker còn thấp hơn). Đây là đề xuất, CHƯA kiểm chứng trên staging.
- **Chưa đo / cần hệ thống thật:** staging, độ trễ Graph API thật (lúc đó mỗi lead giữ worker lâu hơn, công suất 2 worker giảm), supervisor thật với hai chương trình worker, `maxmemory`/Redis thật, tải chat khác 1,67 tin/giây.

## V21-03: tự kéo lại lead Meta sau sự cố Graph API

- **Cơ chế:** `mark_retry` ghi `error_kind` (`tam_thoi`/`vinh_vien`, `channels/meta_leads/errors.classify_meta_error`) mỗi lần tin vào thất bại. Đối soát Meta hằng giờ (`reconcile.run`) sau bước bù lead sót đưa tin Manual `tam_thoi` về `New` qua `enqueue_identity` (cùng `queue_for`, `job_id`, `deduplicate`; không tạo bản ghi mới nên không thể trùng), đếm `requeue_count`; quá 3 lần dừng và báo. Bỏ qua bước này trong lượt mà chính đối soát gặp lỗi tạm thời (Graph vẫn chết). Cấu hình ở docs/09.
- **Kịch bản "Graph chết" (chạy lại V2-08 mục 4.1) — SITE TEST `omni.test`, Graph GIẢ (`fake_graph.py`, HTTP 500 mã 2), Redis tạm, CHƯA STAGING, THỜI GIAN NÉN 30 → 13 phút** (`tests/load/graph_down_requeue.py --down-minutes 13 --leads 20 --missed 5`; tác vụ quét mô phỏng mỗi 60 giây thay cho cron 5 phút; 2 worker `omni`; bot giả; `retry_backoff_minutes` "1,5", `max_attempts` 3 mặc định; `reprocess` KHÔNG được gọi):

| Mốc | Kết quả |
| --- | --- |
| Webhook Meta lúc Graph chết | 20/20 trả 200 |
| Phút 8,4 / 9,5 / 10,5 sau khi Graph chết | Manual 4 / 18 / 20 (còn lại Retry) |
| Lúc Graph sống lại (12:32:05) | 20 tin `Manual`, 0 Lead; Graph nhận 180 yêu cầu lỗi |
| Lượt đối soát kế tiếp | `forms 2, seen 25, created 5, requeued 20, permanent 0, exhausted 0` (5 lead chỉ có trên Graph được bù, 20 tin Manual được kéo lại) |
| Sau ~90 giây (worker) | 25/25 `Done`, 25 CRM Lead (25 `facebook_lead_id` khác nhau), 25 dòng Inbound / 25 `dedupe_key` khác nhau, 0 Lead không chủ, `max requeue_count` 1, 0 lần `reprocess` |

  Đạt tiêu chí F3 trên site test. **Lần chạy đầu (Graph chết 8 phút, cùng cấu hình) cho 24/25:** 4 tin Manual được kéo lại đúng; 1 tin vào `Manual` vĩnh viễn vì hai worker cùng tạo `CRM Lead Source` "Quảng cáo Meta" lần đầu (`IntegrityError 1062`), lỗi có sẵn từ trước V21-03, ghi ở docs/12 mục D; lần chạy chính thức tạo sẵn Lead Source (như production sau lead đầu tiên). Chưa đo: Graph thật (có chết bằng 5xx hay treo kết nối), staging, cron thật, UI thẻ "Kéo lại lead Meta" trên trình duyệt.
- **Dọn sau kịch bản:** `ads_setup.cleanup` (xóa kênh/Lead/Identity/Source LOADADS) rồi `verify_burst.cleanup` (Zalo) nếu có; tắt Graph giả, `bench serve`, `rq_worker`, Redis tạm.


## V21-04: hàng dự phòng webhook

- **Nguyên nhân phải chờ khóa trước đây:** bộ đếm `tabSeries` của tên `OIN-.YYYY.-.########` (khóa ghi tới commit) làm mọi webhook ghi tin xếp hàng sau bất kỳ giao dịch nào giữ dòng đó; thêm khóa duy nhất `dedupe_key` của sự kiện cùng mã đang chèn dở và khoảng khóa của `cleanup_raw_payloads`. Đã bỏ bộ đếm (tên theo thời gian) nên webhook chỉ còn chờ khi trùng chính sự kiện.
- **Màn giám sát:** thẻ "Hàng dự phòng webhook" (số tin, tin cũ nhất, byte; chưa thử trên trình duyệt). Cảnh báo `webhook_spool_cu` khi tin nằm quá 10 phút; `webhook_spool_hong` khi DB và hàng cùng hỏng; `webhook_spool_het_han` khi dòng bị xóa vì quá 14 ngày. Hàm `omni_crm.ingest.spool.spool_count()`/`oldest_age_seconds()`/`stats()` cho preflight V21-08.
- **Sổ tay:** (1) Thấy `spool_count > 0` kéo dài: kiểm tra DB (khóa, kết nối), rồi chạy tay `bench --site <site> execute omni_crm.ingest.spool.drain`. (2) Dòng bị loại ghi log `omni spool: loai dong id=... ly_do=seal_sai|chu_ky_sai|...` (không có payload). (3) Cảnh báo "DB và hàng dự phòng đều hỏng": kiểm đĩa (`df`), quyền `private/omni_spool` (thư mục 0700, tệp 0600, cùng người chạy web) và DB ngay. (4) Chuyển site/khôi phục bản sao lưu: chạy `drain` trước, thư mục này chứa dữ liệu cá nhân chưa vào DB nên không sao chép lung tung. (5) Dọn tay: xóa `private/omni_spool/*` chỉ khi chắc chắn mất các tin đó.
- **Đo tải lại kịch bản V2-08 (SITE TEST `omni.test`, Graph GIẢ, Redis tạm, CHƯA STAGING):** 200 lead quảng cáo trong 600 giây thật + 1.002 tin Zalo (1,67 tin/giây, 300 khách) cùng lúc; gunicorn 4 worker `--preload` ĐÃ làm nóng (750 request 401 trước khi đo), 8 worker RQ `omni,default,short` + 2 worker `omni_leads`, bot giả, sandbox bật, Lead Source quảng cáo KHÔNG tạo sẵn (kiểm luôn sửa G-V21-03-1: không có lỗi 1062 trong log worker).

| Chỉ số | Kết quả |
| --- | --- |
| Phản hồi webhook quảng cáo | 223/223 là 200, **0 phản hồi 503** |
| Phản hồi webhook Zalo | 1.115/1.115 là 200, **0 phản hồi 503** |
| p50 / p95 / p99 / max quảng cáo | 16,2 / 29,7 / 238,2 / 253,8 ms |
| p50 / p95 / p99 / max Zalo | 14,0 / 21,1 / 256,8 / 989,6 ms |
| Tin vào quảng cáo | 200/200 Done, 0 mất, 0 dòng thừa, 0 `dedupe_key` trùng, 200 Lead, 0 Lead trùng, 0 Lead không chủ |
| Lead: nhận -> có người phụ trách p50 / p95 / max | 5,4 / 13,3 / 15,2 giây |
| Zalo | 1.002/1.002 Done, 300 Lead = 300 khách, 0 mất, 0 trùng; nhận -> Done p50 46 / p95 59 / max 61 giây |
| Hàng dự phòng sau tải | rỗng (không có thư mục `omni_spool`): không có lần nào phải dùng hàng trong tải này |

  Lưu ý trung thực: trong lần tải này DB không bị khóa nên "0 phản hồi 503" chủ yếu chứng minh đường lưu mới (không còn chờ bộ đếm Series) không làm hỏng gì và không chậm hơn; khả năng chịu khóa/mất kết nối được chứng minh bằng test (20 luồng, `tabSeries` bị giữ, DB khóa, mất kết nối, hàng đầy/hỏng). Lần đo đầu cùng ngày (cấu hình `workers` sai làm tin Zalo không vào hàng đợi; đã dọn và đo lại) cũng cho 0 phản hồi 503, p95 18,4 ms (Zalo) và 19,3 ms (quảng cáo). Chưa đo: staging, HTTPS/nginx, tải khi DB thật bị khóa trong lúc tải, nhiều máy web.
