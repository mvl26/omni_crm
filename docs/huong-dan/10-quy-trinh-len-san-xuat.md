# 10 · Quy trình lên sản xuất

Dành cho: chủ dự án, trưởng sale, quản trị hệ thống. Tổng hợp từ `docs/nghiem-thu/dot-1.md`, `dot-2.md`, `dot-3.md`, `uat-dot-1..3.md` và `docs/12-quyet-dinh-mo.md`.

> **Kết luận nghiệm thu hiện tại (dot-3, 2026-10-30): "Chưa phát hành".** Phần đo được trên máy phát triển đều tốt (1.043 test tự động qua, tải giả không mất tin). Phần quyết định có cho **bot nói chuyện với khách thật** hay không thì **chưa đo được gì**: không có staging, không có AI thật, Zalo thật, Stringee thật, trình duyệt hay sale thật khi nghiệm thu. Mọi con số trong tài liệu nghiệm thu là trên dữ liệu giả, máy dev. Không có dòng nào là kết luận "đạt" cho nghiệm thu.

Mục lục: [1. Ba môi trường](#1-ba-môi-trường) · [2. Thứ tự bật](#2-thứ-tự-bật-tính-năng-và-điều-kiện-từng-cổng) · [3. Checklist dev → staging](#3-checklist-dev--staging) · [4. Checklist staging → production](#4-checklist-staging--production) · [5. Cổng chưa đo](#5-các-cổng-và-chỉ-số-chưa-đo) · [6. Quyết định còn mở](#6-quyết-định-của-chủ-dự-án-còn-mở) · [7. Dọn dẹp](#7-việc-dọn-dẹp) · [8. Sự cố thường gặp](#8-sự-cố-thường-gặp-khi-lên-sản-xuất) · [9. Đã xong khi](#9-đã-xong-khi)

## 1. Ba môi trường

| Môi trường | Mục đích | `sandbox_mode` | `developer_mode` | Bot | Dữ liệu khách |
| --- | --- | --- | --- | --- | --- |
| Dev | Phát triển, test tự động | Bật | Được bật | Client giả | Không |
| Staging | Thử với kênh thử (OA thử, tổng đài thử), UAT với sale | Bật; tắt có chủ đích chỉ khi gửi cho người nhận chỉ định | **Tắt** | Chỉ sau Q11; hội thoại tổng hợp trước | Chỉ dữ liệu thử |
| Production | Khách thật | **Tắt** (có chủ đích) | **Tắt** | Theo cổng, mục 2 | Thật |

Quy tắc: không chạy test và không chạy tải trên site có dữ liệu thật (`CLAUDE.md`). Không chép dữ liệu production xuống dev/staging mà không bật `sandbox_mode` ngay và đổi bí mật ([01](01-cai-dat-va-ha-tang.md) mục 8).

## 2. Thứ tự bật tính năng và điều kiện từng cổng

Bật theo thứ tự này; **không nhảy bước**. Mỗi dòng: tính năng · công tắc · điều kiện vào · trạng thái đo.

| # | Tính năng | Công tắc | Điều kiện vào (phải đúng hết) | Trạng thái |
| --- | --- | --- | --- | --- |
| 1 | Hạ tầng | — | [01](01-cai-dat-va-ha-tang.md) xong: worker ≥ 2, scheduler, Redis khỏe, nginx, sao lưu | Chưa kiểm trên staging |
| 2 | Kênh Email | `enabled` Omni Channel | [03](03-kenh-email.md) xong; cờ tạo Lead của CRM tắt | Kiểm bằng test tự động; **chưa kiểm trên email thật** |
| 3 | Kênh Zalo OA, nhận tin | `enabled` | [04](04-kenh-zalo-oa.md); **chữ ký Zalo (Z1) đã được xác nhận trên OA thử** | **Chưa đo** (Z1-Z9, G7-G9 chưa đối chiếu với Zalo thật). Chặn Zalo thật |
| 4 | Zalo OA, trả lời từ Hộp thư | `outbound_enabled`, `sandbox_mode` tắt | Mục 3 ổn; token cấp quyền; mốc 48 giờ/7 ngày đã xác nhận với Zalo | **Chưa đo** |
| 5 | Chống trùng, gộp thủ công | — | [06](06-chong-trung-va-lead.md); quyết định rủi ro **R1** (gộp sai do tín hiệu trung bình) | Test tự động qua; **R1 chưa chốt**, Lead trùng trên khách thật **chưa đo** |
| 6 | Kênh Tổng đài | `enabled` | [05](05-kenh-tong-dai-stringee.md); **S1 (khóa ký) xác nhận trên staging có tổng đài thử** | **Chưa đo** (chưa thử Stringee thật). Chặn tổng đài |
| 7 | Việc gọi lại khách nhỡ | — | Mục 6 có kênh; `holidays`, `callback_fallback_user` điền | Tạo đúng 141/141 trên dữ liệu giả; "đúng hạn" **chưa đo** |
| 8 | Bot phân tích + chấm điểm | `bot_analyze_enabled`, `lead_scoring_enabled` | Q11 chốt; khóa AI; kho + nhóm thật; bảng điểm do trưởng sale duyệt (Q5); **cổng `phan_tich` ≥ 90% bằng AI thật trên bộ đề đã duyệt** | **Chưa đo** (cổng `phan_tich` chưa chạy với AI thật) |
| 9 | Shadow | `bot_reply_mode = Shadow` | Mục 8 chạy ổn; Q11 | **Chưa đo** |
| 10 | Draft (nháp cho sale) | `bot_reply_mode = Draft` | **Cổng `nhap` đạt bằng AI thật** (hành động đúng ≥ 90%, trả lời đạt ≥ 85%, lỗi nghiêm trọng = 0, đã chấm lại 20%); Q6, Q8; staging OA thử; sale tham gia | **CHƯA ĐO** (220/220 ca "chưa đo", `EVR-2026-00415`) |
| 11 | Tuần chạy thử có sale | — | Mục 10 ở staging; UAT Đợt 3 mục 2-5 và 8; `measure_dot3` | **Chưa chạy** |
| 12 | Auto theo nhóm | `omni_auto_send_allowed` + nút từng nhóm | **Q1 chốt**; Draft có dữ liệu thật: nhóm ≥ 30 nháp quyết định / 14 ngày, ≥ 95% nguyên văn hoặc sửa nhẹ, 0 nghiêm trọng | **Không có dữ liệu**; không thể nêu nhóm nào nên mở |
| — | Ghi âm, tóm tắt cuộc gọi | (chưa có công tắc) | D3-04 bị bỏ qua; cần Q13 (chọn nhà cung cấp), Q14 (ngưỡng) và đo chuyển giọng nói | **Không có tính năng** |

## 3. Checklist dev → staging

- [ ] Mã ở nhánh/tag phát hành đã qua toàn bộ test (`bench --site <site-test> run-tests --app omni_crm`; ghi số test, **chạy với Redis queue khỏe**, xem mục 7) và `ruff` sạch. (Nghiệm thu Đợt 3 ghi 1.043 test OK trên máy dev với Redis tạm; không chạy lại ở đây.)
- [ ] `bench --site <staging> migrate` sạch, chạy hai lần.
- [ ] Staging có: domain HTTPS, nginx giới hạn thân + rate limit + không ghi query ([01](01-cai-dat-va-ha-tang.md) mục 6), worker `omni` ≥ 2, scheduler bật, Redis khỏe, Email Account gửi đi.
- [ ] `sandbox_mode` **bật**; `developer_mode` **tắt**; không có `omni_auto_send_allowed`; không có `omni_ai_allow_customer_data` (cho tới Q11).
- [ ] Omni Settings theo [02](02-cau-hinh-omni-settings.md); `alert_recipients`, `callback_fallback_user`, `business_hours`, `holidays` điền.
- [ ] Assignment Rule của CRM cho Lead đã tạo (Q4) hoặc ghi nhận gán tay.
- [ ] Kênh thử: Email, **Zalo OA thử** (Z1 đối chiếu), tổng đài thử (S1 đối chiếu). Ghi kết quả từng mục Z1-Z9, S1-S7 vào `docs/12` mục C.
- [ ] UAT Đợt 1 (`docs/nghiem-thu/uat-dot-1.md`): sale thật dùng Hộp thư ≥ 1 tuần; số đo bằng `measure_dot1`.
- [ ] Lặp lại thử tải trên staging (`tests/load/send_burst.py`, `send_calls.py`) vì số đo hiện có là máy dev.
- [ ] Thử sự cố trên staging: tắt worker 10 phút (nhận cảnh báo), Redis chết, token Zalo hết hạn, kill worker.
- [ ] Quay lui mức 1 và 2 thử trên staging ([09](09-van-hanh-hang-ngay.md) mục 5).
- [ ] Đã ghi lại cho từng cổng ở mục 2 kết quả thật (đạt / không đạt / chưa đo).

## 4. Checklist staging → production

Trước khi bật:

- [ ] Các quyết định ở mục 6 đánh dấu "chặn" đã được chủ dự án chốt hoặc ghi chấp nhận rủi ro bằng văn bản.
- [ ] Production: `developer_mode` tắt (`frappe.conf.developer_mode`), `zalo_skip_signature` tắt, `omni_auto_send_allowed` vắng, `sandbox_mode` **bật** lúc đầu.
- [ ] Dọn dẹp ở mục 7 xong.
- [ ] **Lệnh kiểm trước phát hành (V21-08) không còn `KHONG_DAT`**: `bench --site <site> execute omni_crm.ops.preflight.run --kwargs "{'target': 'production'}"` (thoát mã 0). Chạy với `'staging'` ở bước 3. Đọc từng mục `CAN_NGUOI_KIEM`; chi tiết ở `docs/09` mục 6.
- [ ] Bí mật production (App secret, OA secret key, token, API key, `webhook_token`) tạo mới, không dùng lại giá trị của dev/staging; không nằm trong chat/ghi chú/commit.
- [ ] Sao lưu trước khi bật; đã thử khôi phục.
- [ ] Kế hoạch quay lui và người trực được giao tên.
- [ ] Sale đã được hướng dẫn bằng [08](08-huong-dan-nhan-vien-sale.md) và đã làm đủ bài kiểm "Đã xong khi".

Bật theo đợt, từng bước một, có người theo dõi `/app/omni-monitor`:

1. Giữ `sandbox_mode` bật 1 ngày: tin vào được nhận, sale xem Hộp thư, chưa gửi gì ra ngoài.
2. Tắt sandbox **có chủ đích**, gửi thử một tin tới tài khoản thử được chỉ định; xác nhận đã nhận.
3. Mở cho sale thật; theo dõi cảnh báo và Manual hằng ngày.
4. Chỉ sau khi tuần đầu ổn mới tính đến bot (mục 2 dòng 8 trở đi), mỗi bước một thay đổi và quan sát trước khi sang bước sau.

Sau khi bật: không đổi nhiều thứ cùng lúc; mỗi thay đổi công tắc có người ghi lại (lịch sử phiên bản của Omni Settings/Omni Channel/Omni Question Group lưu).

## 5. Các cổng và chỉ số chưa đo

Bảng này lấy từ `docs/nghiem-thu/dot-3.md` mục 4 và 8. "Chưa đo" nghĩa là chưa có số thật.

| Cổng / chỉ số | Ngưỡng | Hiện trạng |
| --- | --- | --- |
| Cổng `phan_tich` | Trường đúng ≥ 90% | **Chưa đo** (cần AI thật + bộ đề duyệt) |
| Cổng `nhap` | Trường ≥ 90%, hành động ≥ 90%, trả lời đạt ≥ 85%, nghiêm trọng = 0 | **Chưa đo** |
| Cổng `doi_cau_lenh` | Không giảm > 2 điểm so với lần chấp nhận | **Chưa đo** (chưa có lần chấp nhận nào) |
| Câu trả lời của bot đúng | ≥ 85% (3 tháng: ≥ 92%) | **Chưa đo** |
| Trường trích xuất đúng | ≥ 90% (≥ 95%) | **Chưa đo** |
| Phản hồi lần đầu (giờ hành chính) | < 15 phút (< 5) | **Chưa đo** |
| Lead trùng | < 3% (< 1%) | **Chưa đo** (R1 mở) |
| Gọi nhỡ gọi lại đúng hạn | ≥ 80% (≥ 95%) | Việc tạo đúng 141/141 trên dữ liệu giả; "đúng hạn" **chưa đo** |
| Tin vào đến Lead | < 1 phút (< 30 giây) | **Không đạt ở tải dồn, chưa đo ở tải thường**: nhận → Done p50 66-99 giây, p95 128-191 giây ở 20-30 tin/giây với 8 worker (do mỗi lượt giữ worker ~6 giây). p95 webhook ~17-19 ms là thời gian *nhận*, không phải chỉ số này |
| Tin bị mất | 0 | 0 trên mọi pha tải giả; chưa đo trên khách thật |
| Tin xử lý tay | < 1% | 0 tin Manual trên tải giả; chưa đo khi có lỗi thật |
| Cổng tiếp nhận hoạt động | ≥ 99,5% | Chưa đo (cần giám sát ngoài) |
| Sự cố hạ tầng (Redis chết, worker kill, webhook chờ khóa DB) | — | Đo ở Đợt 1-2; **không chạy lại ở Đợt 3**; E2, E3, E10 còn mở |

Công cụ đo (chạy trong `<bench-dir>`, giờ theo múi giờ của site; chỉ đọc):

```bash
bench --site <site> execute omni_crm.tests.acceptance.measure_dot1.run --kwargs '{"start": "2026-11-23 00:00:00", "end": "2026-11-30 00:00:00"}'
bench --site <site> execute omni_crm.tests.acceptance.measure_dot2.run --kwargs '{"start": "...", "end": "..."}'
bench --site <site> execute omni_crm.tests.acceptance.measure_dot3.run --kwargs '{"start": "...", "end": "..."}'
```

Thiếu mẫu thì lệnh in "chưa đủ dữ liệu", không ngầm in "ĐẠT". Mặc định bỏ Lead dữ liệu giả (tiền tố LOAD, PLT, TD, T05, D209, D306, DEMO, RV, MD).

## 6. Quyết định của chủ dự án còn mở

Lấy từ `docs/12` mục A, B và `docs/nghiem-thu/dot-3.md` mục 9.1. "Chặn" = chưa chốt thì không bật phần tương ứng.

| # | Quyết định | Chặn gì | Ghi chú |
| --- | --- | --- | --- |
| **Q11** | Chính sách gửi dữ liệu hội thoại/cuộc gọi sang Claude API và dịch vụ chuyển giọng nói; kéo theo `omni_ai_allow_customer_data`, `omni_ai_unmask_pii` | **Toàn bộ bot** với dữ liệu thật | Chưa chốt |
| **Q1** | Bot tự gửi hay chỉ soạn nháp | Mọi nhóm Auto | Đề xuất: nháp, mở Auto theo nhóm khi đạt chuẩn; chưa chốt |
| Q12 | Khi nào bật Draft | — | **Đã chốt**: sau khi cổng `nhap` đo bằng AI thật và đạt |
| Q2 | Nhà cung cấp tổng đài | — | Đã chốt: adapter chung, bản đầu Stringee |
| Q3 | Mô hình AI và chuyển giọng nói | Ghi âm/tóm tắt | AI: Claude. STT: chưa chọn |
| **Q4** | Quy tắc gán Lead cho sale | Lead có người phụ trách | Chưa chốt; gán tay hoặc tạo Assignment Rule |
| **Q5** | Bảng điểm Lead | Chấm điểm | Mẫu `mau/bang-diem-lead-mau.csv` chỉ là đề xuất |
| **Q6** | Giờ bot hoạt động, câu ngoài giờ | Mặc định tạm | `bot_active_hours` trống = theo giờ làm việc; câu giữ chân mặc định ("sớm nhất có thể") |
| **Q7** | Thời hạn lưu dữ liệu, ghi âm; ai được xem | Mặc định tạm | 180 ngày payload, 365 ngày ghi âm; kèm điều khoản dữ liệu nhà cung cấp STT |
| **Q8** | Kênh báo sale khi chuyển người | Mặc định tạm | `system`; thêm `email` nếu muốn |
| Q9 | Tạo Lead khi chỉ bấm quan tâm OA | Mặc định: không | Xác nhận |
| Q10 | Lead đã thành Deal: ghi tin vào Deal | Mặc định: có | Xác nhận |
| Q13, Q14 | Nhà cung cấp chuyển giọng nói muốn thử, 20 ghi âm thật, ngưỡng cổng | Ghi âm/tóm tắt | Chưa có |
| **Z1-Z9, G7-G9** | Chữ ký, payload, OAuth, mã lỗi, khung 48 giờ/7 ngày, echo của Zalo | **Zalo thật** | Cần người có OA đối chiếu |
| **S1-S7** | Khóa ký, JSON tra cứu, Call log id, ghi âm của Stringee | **Tổng đài thật** (S1 chặn) | Cần tài khoản Stringee thật |
| **R1** | Gộp Lead sai vì tín hiệu trung bình | Cho khách thật | Cần người phụ trách chốt hoặc chấp nhận rủi ro bằng văn bản (`docs/12` mục D, "Review D1-05, R1") |
| Xóa dữ liệu | `erase_customer_data` chưa phủ `Omni Call`, `CRM Call Log`, Communication, thông tin trong Identity/Lead | Yêu cầu xóa pháp lý, nhất là cuộc gọi | Mở rộng hàm xóa; quyết phạm vi |
| E6 | Job nền chạy bằng Guest (chủ bản ghi/người sửa = Guest) | — | Đã thử: các đường bot làm việc bằng Guest. Còn lại là quyết định ADR |
| Cooldown Auto | Có bắt buộc chờ sau khi nhóm tự tắt | Chưa có cooldown | `docs/12` mục D |
| E9 | Debounce 0 cho cuộc gọi | Công suất cuộc gọi | Mở |
| E2, E3, E10 | Webhook chờ khóa DB không giới hạn; phục hồi ~15 phút sau kill worker; phụ thuộc cron 5 phút | — | Mở từ Đợt 1/2 |
| Kho tri thức | 0 mục thật; trần 150 mục (vượt thì bot không soạn) | Mọi nháp là chuyển người | Phụ trách kho nhập kho thật đã duyệt |
| Bộ đề | 220 ca tổng hợp chưa duyệt, 0 ca thật | Cổng `phan_tich`, `nhap` | Người duyệt bộ đề; `build_from_real` cần Q11 |
| Cấu hình còn lại | `holidays`, `callback_fallback_user`, `alert_recipients`, Fields Layout CRM (điểm/tóm tắt bot), nginx `client_max_body_size` và rate limit, lối tắt/menu cho Hộp thư | — | Việc của quản trị |

## 7. Việc dọn dẹp

Làm **trước** khi mở production. Mục có ghi "không có chi tiết trong repo" là việc do người điều phối nêu; người phụ trách môi trường cần xác nhận phạm vi.

| Việc | Chi tiết |
| --- | --- |
| **Xóa user DB `omni_admin`** | Tài khoản cơ sở dữ liệu dùng khi phát triển/thử. Không có chi tiết trong repo (chỉ các test nhắc tên `omni_admin`). Xóa/vô hiệu ở máy chủ DB của môi trường thật và kiểm không còn quyền truy cập thừa. Người phụ trách DB xác nhận trước khi xóa |
| **Đổi mật khẩu đã lộ** | Mật khẩu/bí mật từng bị lộ trong quá trình phát triển (không có chi tiết trong repo). Đổi mọi mật khẩu đó (tài khoản quản trị, DB, Redis, Email Account, khóa API) và coi mọi bí mật đã xuất hiện trong chat/log là đã lộ. Quét Error Log, Comment, repo để chắc chắn không còn |
| **Sửa Redis 11000 (dev)** | Redis queue của bench dev bị hỏng RDB (`Stream listpack integrity check failed`), `bench start` khởi động lại liên tục. Chủ bench dev: sao lưu rồi xóa `config/pids/redis_queue.rdb` và các `temp-*.rdb`, khởi động lại Redis queue, xác nhận `bench doctor`. Không đưa RDB hỏng sang môi trường khác. Trước khi sửa, test tích hợp chạy với `FRAPPE_REDIS_QUEUE` trỏ Redis tạm cổng riêng |
| **Đặt lại cấu hình bot ở môi trường thật** | `bot_reply_mode = Off` và `bot_analyze_enabled = 0` (site test `omni.test` đang để `bot_analyze_enabled = 1`, `bot_reply_mode = Draft` do phiên trước để lại, **không phải mặc định phát hành**). Cũng đặt `lead_scoring_enabled = 0` cho tới khi có bảng điểm duyệt; mọi nhóm `auto_send_enabled = 0` |
| Kiểm site_config | Không có `developer_mode`, `omni_auto_send_allowed`, `omni_ai_allow_customer_data` trên production cho tới khi được phép; không đặt các cờ này ở `common_site_config.json` |
| Dữ liệu thử | Xóa Lead/Omni Identity/Omni Call/kênh/nhóm/mục kho có tiền tố dữ liệu giả (LOAD, PLT, TD, T05, D209, D306, DEMO, RV, MD, KB-93) nếu lỡ nằm trong site thật; xóa kênh thử |
| Omni Settings | `daily_token_budget` đặt theo ngân sách; `kb_version` là bộ đếm tự tăng (đừng sửa) |
| Hộp thư | Đặt lối tắt (Workspace) cho trang `/app/omni-inbox` để sale dễ mở (chưa kiểm cách làm trên giao diện) |
| Tài khoản và vai trò | Rà ai có Omni Admin, Omni Knowledge Editor; gỡ tài khoản thử |
| Quy ước Error Log | Nếu phải xóa dòng Error Log dữ liệu thử: dùng chức năng xóa Error Log của Frappe cho site thử, **không** xóa hàng loạt theo SQL trên site có dữ liệu thật |

## 8. Sự cố thường gặp khi lên sản xuất

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Khách nhắn Zalo mà không có tin nào vào | Chữ ký (Z1), URL webhook, tên miền chưa xác thực | [04](04-kenh-zalo-oa.md) mục 8; **không** bật `zalo_skip_signature` |
| Sale trả lời mà khách không nhận, Hộp thư ghi "thử nghiệm" | `sandbox_mode` còn bật | Tắt có chủ đích sau khi kiểm |
| Mọi cuộc gọi bị 401 | Khóa ký Stringee khác (S1) | [05](05-kenh-tong-dai-stringee.md) mục 7 |
| Bật bot mà không thấy gì | Q11 chưa mở, thiếu khóa, ngân sách 0, kho > 150 mục | [07](07-bot-ai.md) mục 13 |
| Tin vào chậm khi tải dồn | Số worker thấp | Thêm worker theo công thức ([01](01-cai-dat-va-ha-tang.md) mục 3) |
| Bot gửi gì đó sai | Auto bật sớm | Tắt khẩn ([07](07-bot-ai.md) mục 12) |
| Lead trùng nhiều | R1, nghi trùng không ai xử lý | [06](06-chong-trung-va-lead.md) |

## 9. Đã xong khi

- [ ] Mỗi dòng ở mục 2 có ghi kết quả thật (đạt/không đạt/chưa đo) cùng người và ngày xác nhận.
- [ ] Mọi quyết định "chặn" ở mục 6 liên quan phần định bật đã có câu trả lời bằng văn bản.
- [ ] Dọn dẹp mục 7 xong, đã đổi các bí mật cần đổi; DB user `omni_admin` đã xử lý.
- [ ] Production: `developer_mode` tắt, `zalo_skip_signature` tắt, `sandbox_mode` đã tắt có chủ đích sau khi thử một tin tới tài khoản thử.
- [ ] Bot: `bot_reply_mode = Off`, `bot_analyze_enabled = 0`, mọi `auto_send_enabled = 0`, không có `omni_auto_send_allowed` (cho tới khi đủ cổng).
- [ ] Sao lưu trước khi bật và đã thử khôi phục; kế hoạch quay lui có tên người trực.
- [ ] Sale đã được hướng dẫn và biết nơi báo lỗi; trưởng sale biết cách đọc báo cáo và nghi trùng.
