# 09 · Vận hành hằng ngày

Dành cho: quản trị hệ thống (Omni Admin), trưởng sale. Nguồn: `docs/11-van-hanh.md` (rút gọn và đối chiếu mã). Giám sát chỉ dành cho vai trò **Omni Admin**.

Mục lục: [1. Màn hình giám sát](#1-màn-hình-giám-sát) · [2. Cảnh báo](#2-cảnh-báo) · [3. Đọc lỗi](#3-đọc-lỗi-error-log-và-tin-vào-lỗi) · [4. Sổ tay sự cố](#4-sổ-tay-sự-cố-rút-gọn) · [5. Quay lui](#5-quay-lui) · [6. Sao lưu và dữ liệu](#6-sao-lưu-và-dữ-liệu) · [7. Lịch kiểm tra](#7-lịch-kiểm-tra) · [8. Đã xong khi](#8-đã-xong-khi)

## 1. Màn hình giám sát

Mở **`/app/omni-monitor`** ("Giám sát Omni"; chỉ Omni Admin). Tự làm mới mỗi 30 giây; có nút **Quét ngay** (nút chính) và **Làm mới**. Các khối:

| Khối | Cho biết | Dấu hiệu xấu (đỏ) |
| --- | --- | --- |
| Công tắc | `sandbox_mode`, `developer_mode`, đã đặt `alert_recipients` chưa, số ngày giữ `raw_payload`; dòng "Chẩn đoán chữ ký Zalo ĐANG BẬT tới ..." nếu đang bật; từng kênh bật nhận/gửi và có "bỏ qua chữ ký" không | `developer_mode` bật; `alert_recipients` chưa đặt; kênh có "bỏ qua chữ ký"; chẩn đoán chữ ký còn bật (chỉ dùng tạm trên staging) |
| Kênh | Mỗi kênh: lần nhận/gửi thành công gần nhất, lỗi gần nhất. Kênh Zalo OA: hạn token Zalo, dòng **Gói OA**, nút **Làm mới token Zalo**. Kênh Meta Lead Ads: dòng **Hạn token Meta** (hạn token, hạn quyền dữ liệu, ghi chú kết quả kiểm, hoặc "chưa kiểm") | Token Zalo còn < 1 giờ; gói OA là Chưa xác nhận/Cơ bản/Tiêu chuẩn ("Gói OA có thể chưa cho kết nối API"); token Meta có vấn đề; có lỗi gần nhất |
| Hàng dự phòng webhook (**chỉ hiện khi có tin hoặc từng hỏng**) | Số tin đang chờ chèn lại, tin cũ nhất, dung lượng, lần cuối DB và hàng dự phòng cùng hỏng | Có tin chờ lâu; có "lần cuối cùng hỏng" |
| Kéo lại lead Meta (**chỉ hiện khi đã có số liệu**) | Lượt đối soát gần nhất (tạo mới, kéo lại), tổng số lần đã kéo lại, tin Manual lỗi tạm thời đang đợi kéo lại, tin Manual lỗi vĩnh viễn, tin dừng kéo lại sau 3 lần | Lỗi vĩnh viễn > 0; dừng kéo lại > 0 |
| Hàng đợi | Số tin New, Processing, Retry, **Manual**; tuổi tin New lâu nhất; lần quét gần nhất | Manual > 0; tin New cũ nhất > 5 phút; lần quét cũ (scheduler dừng) |
| Thời gian xử lý (24 giờ) | Số tin Done, p50, p95 từ nhận tin đến Done | p95 > 2 phút |
| Chiều ra (hôm nay) | Queued, Sent, Failed, Blocked; Failed trong 1 giờ | Failed trong 1 giờ > 5 |
| Tổng đài (hôm nay) | Cuộc gọi, nghe máy, gọi nhỡ, việc gọi lại quá hạn, đối soát đêm, cuộc gọi nhỡ chưa xác minh | Quá hạn > 0; đối soát lỗi hoặc bù > 1%; chưa xác minh > 0 |
| Bot AI | Token hôm nay / hạn mức, số lần gọi AI 24 giờ, lần lỗi, lần bỏ qua do hạn mức | Dùng > 80% hạn mức; lỗi > 5% (từ 20 lần gọi); bỏ qua do hạn mức > 0 |
| Nghi trùng | Số cặp Open | > 20 |
| Tin Manual | Danh sách tin cần xử lý tay, ô chọn + nút **Xử lý lại** | Có dòng |
| Xóa dữ liệu khách | Xem [06](06-chong-trung-va-lead.md) mục 5 | — |

Nút:

- **Xử lý lại**: chọn các tin `Manual` rồi bấm, đưa về `New` để xử lý lại. Chỉ bấm sau khi đã sửa nguyên nhân (xem `last_error`).
- **Quét ngay**: chạy ngay tác vụ quét (đẩy lại tin kẹt, tin chờ thử lại, tin New mồ côi, tin ra); hiện kết quả "kẹt N, thử lại N, tin New mồ côi N, tin ra N".
- **Làm mới token Zalo**: làm mới ngay token của kênh (có thể báo "chưa làm mới" nếu token còn mới hoặc tiến trình khác đang làm).

## 2. Cảnh báo

Cứ mỗi 5 phút hệ thống kiểm các luật (riêng cảnh báo token Meta và lead Meta không kéo lại được gửi ngay lúc phát hiện, theo tác vụ kiểm token và đối soát) và gửi cảnh báo qua **Notification Log cho Omni Admin** và **email tới `alert_recipients`** (để trống `alert_recipients` thì không có thư, Notification Log vẫn tạo). Mỗi loại tối đa 1 lần / 30 phút (mỗi kênh với luật theo kênh). Cảnh báo không chứa bí mật hay nội dung tin khách.

| Tiêu đề cảnh báo | Nghĩa là | Việc đầu tiên |
| --- | --- | --- |
| Tin chờ xử lý quá lâu | Tin `New` cũ hơn 5 phút | Worker `omni`, Redis, scheduler ([01](01-cai-dat-va-ha-tang.md)); bấm **Quét ngay** |
| Có tin cần xử lý tay | Có tin `Manual` | Mục 3 |
| Xử lý tin chậm | p95 > 2 phút | Thêm worker; kiểm độ trễ DB |
| Gửi tin ra lỗi nhiều | > 5 tin ra Failed trong 1 giờ | Token Zalo, kênh, mạng ra ngoài |
| Nhiều cặp nghi trùng chưa xử lý | > 20 cặp Open | Xử lý nghi trùng ([06](06-chong-trung-va-lead.md)) |
| Kênh không nhận tin | Kênh bật nhưng không nhận tin nào > 4 giờ làm việc (chỉ trong giờ làm việc) | Webhook phía Zalo/tổng đài, chứng chỉ HTTPS, chữ ký |
| Token Zalo sắp hết hạn | Token còn < 1 giờ (tác vụ làm mới chạy khi còn < 2 giờ, nên làm mới đã thất bại) | **Làm mới token Zalo**; không được thì "Kết nối Zalo OA" lại. Không áp khi sandbox bật |
| Làm mới token Zalo lỗi | Lần làm mới gần nhất lỗi | Như trên |
| Có việc gọi lại quá hạn | Việc gọi lại quá hạn (gửi cả cho người được giao) | Trưởng sale nhắc người phụ trách; kiểm người nhận ([05](05-kenh-tong-dai-stringee.md)) |
| Đối soát đêm bù nhiều cuộc gọi / Đối soát đêm tổng đài không hoàn thành | Đối soát đêm bù > 1% hoặc lỗi | Webhook tổng đài rơi sự kiện / API Stringee lỗi |
| Bot AI sắp hết hạn mức token | Dùng > 80% | Xem chi phí; tăng hạn mức nếu hợp lý |
| Bot AI hết hạn mức token hôm nay | Dùng hết hạn mức (một lần/ngày) | Bot dừng đến hôm sau |
| Bot AI tạm ngừng vì lỗi liên tiếp | Ngắt mạch mở (5 lần lỗi liên tiếp, 5 phút) | Kiểm khóa/mạng/nhà cung cấp AI |
| Bot AI lỗi nhiều | > 5% lần gọi lỗi/sai định dạng (từ 20 lần) | Xem `Omni Bot Run` |
| Bot tự gửi nhóm X đã tự tắt: <lý do> | Ngắt mạch Auto ([07](07-bot-ai.md) mục 9) | Xem nguyên nhân, sửa kho trước khi bật lại |
| Hàng đợi omni_leads có việc nhưng không có worker (V21-01) | Hàng đợi riêng của lead quảng cáo đang có việc chờ mà không worker nào lắng nghe: lead nằm im | Thêm chương trình `bench worker --queue omni_leads` vào supervisor ([01](01-cai-dat-va-ha-tang.md) mục 3) hoặc bỏ khai báo `omni_leads` khỏi `workers` |
| Tin nằm lâu trong hàng dự phòng webhook (V21-04) | Có tin trong hàng dự phòng quá 10 phút: DB không ghi được hoặc tác vụ quét mỗi phút không chèn lại được | Bật scheduler; kiểm DB; chạy tay `bench --site <site> execute omni_crm.ingest.spool.drain` ([01](01-cai-dat-va-ha-tang.md) mục 12) |
| Webhook từng không lưu được tin: DB và hàng dự phòng đều hỏng (V21-04) | Có lúc webhook phải trả lỗi 5xx vì cả hai nơi đều không ghi được; tin đó có thể mất nếu nhà cung cấp không gửi lại | Kiểm DB và thư mục `private/omni_spool` (quyền, dung lượng); với Meta, đối soát hằng giờ sẽ bù lead sót |
| Token Meta cần xử lý (V21-02) | Kiểm hằng ngày thấy token Page không hợp lệ, còn ≤ 7 ngày (hoặc quyền dữ liệu sắp hết), hoặc thiếu quyền `leads_retrieval` | Cấp lại token (khuyến nghị token System User không hết hạn, [12](12-lead-quang-cao.md) mục 6) rồi nhập vào kênh |
| Có lead Meta không tự kéo lại được (V21-03) | Có tin Meta ở Manual do lỗi vĩnh viễn (token sai, thiếu quyền, lead đã xóa) hoặc đã kéo lại 3 lần vẫn lỗi | Sửa token/quyền rồi bấm **Xử lý lại** ở khối Tin Manual; [12](12-lead-quang-cao.md) mục 6 |
| Không có lead quảng cáo (V2-05) | Có chiến dịch **Đang chạy** và kênh quảng cáo bật mà không có lead thật nào trong `ads_silence_hours` giờ làm việc | Webhook Meta/Google, token, biểu mẫu bật; [12](12-lead-quang-cao.md) mục 8 |
| Web Form chạm trần toàn kênh (V2-05) | Form website vượt `webform_global_rate_per_hour` mỗi giờ: có thể bị tấn công; người thật cũng bị từ chối tới hết giờ | Xem nginx và nguồn gửi; chỉ nâng trần khi chắc là lưu lượng thật |
| Đồng bộ lead Facebook có sẵn của CRM đang bật | Tính năng Lead Sync Source của CRM đang bật, tạo Lead ngoài hệ thống chống trùng của Omni | Tắt nguồn đồng bộ đó; lead Meta chỉ nên vào qua kênh Meta Lead Ads của Omni |
| Quá hạn phản hồi / Quá 2 lần hạn phản hồi (V2-05) | Thông báo (chuông) cho sale (và trưởng nhóm) khi lead quảng cáo chưa được phản hồi trong `response_sla_minutes` phút làm việc | Sale liên hệ khách; trưởng nhóm đôn đốc ([13](13-dashboard-va-cham-soc.md)) |

Muốn gửi lại ngay một cảnh báo đang bị chống lặp: xóa khóa Redis `omni:alert:<loại>:<kênh>` hoặc `bench --site <site> clear-cache` (chạy trong `<bench-dir>`; xóa toàn bộ cache, chỉ khi cần). Nếu Redis cache lỗi, cảnh báo vẫn gửi (có thể lặp).

Kiểm thử cảnh báo: tắt worker `omni` khoảng 10 phút → sau ~5-10 phút nhận đúng một thư "Tin chờ xử lý quá lâu", rồi thêm một thư mỗi 30 phút nếu còn kẹt. Làm trên staging.

## 3. Đọc lỗi: Error Log và tin vào lỗi

**Tin vào (Omni Inbound Message)** - `/app/omni-inbound-message`. Các trạng thái:

| Trạng thái | Nghĩa | Làm gì |
| --- | --- | --- |
| New | Mới lưu, chờ xử lý | Nếu cũ hơn vài phút: worker/Redis |
| Processing | Đang xử lý | Nếu kẹt quá `stuck_after_minutes` (mặc định 10): tự về Retry |
| Retry | Lỗi tạm thời, sẽ thử lại theo `retry_backoff_minutes` | Chờ; nếu hàng loạt xem `last_error` |
| Done | Xong | — |
| Manual | Lỗi quá `max_attempts` lần | **Xem cột "Lỗi gần nhất" (`last_error`)**, sửa nguyên nhân, rồi **Xử lý lại** |
| Ignored | Cố ý bỏ qua (thư tự động, sự kiện không quan tâm) | Thường không cần làm gì |

Cách đọc: lọc `status = Manual`, nhóm theo `last_error` (cùng lỗi lặp lại thường là một nguyên nhân gốc: token, quyền, khóa bận, dữ liệu sai). `last_error` đã được che bí mật. `raw_payload` là dữ liệu thô nhận từ kênh (xóa sau 180 ngày mặc định); đừng chép nó ra ngoài vì chứa thông tin khách.

Lỗi hay gặp: "Khóa tín hiệu định danh đang bận" nhiều → DB chậm hoặc việc tạo Lead kéo dài (kiểm độ trễ DB); lỗi quyền (`PermissionError`) → job nền chạy bằng người dùng Guest, ghi Error Log "Omni: lỗi tạo việc gọi lại" kèm loại lỗi.

**Error Log của Frappe** - `/app/error-log`. Tiêu đề do app ghi bắt đầu bằng **"Omni"**. Thường gặp:

| Tiêu đề | Nghĩa |
| --- | --- |
| `Omni webhook: xac_thuc_that_bai` | Webhook bị từ chối vì xác thực sai (sai khóa/token/chữ ký). Tối đa 1 dòng/phút/kênh. Xem [04](04-kenh-zalo-oa.md), [05](05-kenh-tong-dai-stringee.md) |
| `Omni webhook: parse_that_bai` | Không đọc được nội dung; tin vẫn được lưu ở trạng thái Manual |
| `Omni webhook: khong_co_adapter` | Kênh chưa có bộ xử lý |
| `Omni: lỗi tạo việc gọi lại` | Việc gọi lại lỗi; tác vụ quét tạo bù sau 5-10 phút |
| `Omni: lỗi chấm điểm Lead` / `Omni: lỗi báo Lead nóng` | Lỗi chấm điểm/báo |
| `Omni: không báo được nháp bot` | Không tạo được thông báo |
| `Omni: kho tri thức vượt ngưỡng truy hồi` | Kho > 150 mục, bot không soạn ([07](07-bot-ai.md)) |
| `Omni: bot trả lời bỏ qua vì khóa lượt` | Hai job cùng một lượt; hiếm |
| "nhóm tự tắt tự gửi nhưng không có người nhận cảnh báo" | Không có Omni Admin/Sales Manager hoạt động để báo |

Error Log được thiết kế để không chứa bí mật hay nội dung khách (chỉ mã lượt, loại lỗi, độ dài). Nếu bạn thấy bí mật trong log, đó là lỗi nghiêm trọng: đổi bí mật đó và báo nhà phát triển.

**Lần gọi AI (Omni Bot Run)** - `/app/omni-bot-run`: lọc Trạng thái ≠ Success. **Nháp bot (Omni Bot Draft)** - `/app/omni-bot-draft`: cột `guard_flags` cho biết vì sao nháp bị chặn/chuyển người.

## 4. Sổ tay sự cố rút gọn

| Triệu chứng | Kiểm tra | Xử lý |
| --- | --- | --- |
| Tin New tăng mãi | `bench doctor` (trong `<bench-dir>`); worker `omni` có chạy không (`supervisorctl status`); Redis | Khởi động worker; **Quét ngay**; tăng `background_workers` nếu do tải ([01](01-cai-dat-va-ha-tang.md) mục 3) |
| Nhiều tin Manual | Xem `last_error`, nhóm theo lỗi | Sửa nguyên nhân, chọn tin, **Xử lý lại** |
| Zalo không gửi được | Hạn token, lỗi làm mới, quyền OA | **Làm mới token Zalo**; không được → "Kết nối Zalo OA" lại |
| Không nhận được tin Zalo | Cấu hình webhook phía Zalo, xác thực tên miền, HTTPS, 401 trong log | Sửa cấu hình; lỗi chữ ký hàng loạt → soát `zalo_oa_secret_key`; nếu đúng khóa mà vẫn lỗi thì công thức chữ ký (Z1), báo nhà phát triển |
| Bot trả lời sai với khách (nhóm Auto) | Tìm `Omni Bot Draft` (chế độ Auto), Bot Run, nhóm | **Tắt `auto_send_enabled` của nhóm ngay** (nút trên form nhóm; hoặc sale chấm "Sai" thì nhóm tự tắt). Tắt tất cả: bỏ `omni_auto_send_allowed` khỏi site_config. Sale gửi đính chính; sửa kho; chạy lại bộ đề |
| Sale báo nháp bot sai/bất thường (Draft) | Báo cáo "Bot nháp" (tỉ lệ bỏ, chấm Sai, nghiêm trọng theo nhóm/tuần) | `bot_reply_mode = Shadow` hoặc `Off`; sửa kho; chạy lại bộ đề. Nháp đã gửi là tin của sale |
| Chi phí AI tăng vọt | `Omni Bot Run` theo giờ/loại | Giảm `daily_token_budget` (0 = dừng); kiểm vòng lặp gọi lại |
| Lộ `webhook_token` tổng đài | — | [cau-hinh-stringee.md](cau-hinh-stringee.md) mục 6 |
| Không có việc gọi lại dù có cuộc gọi nhỡ | Khách ẩn số; kênh tắt; worker không chạy | [05](05-kenh-tong-dai-stringee.md) mục 5 |
| Thư nhắc chuyển người không tới | `sandbox_mode` bật (không xếp thư), Email Queue, người nhận chưa có email | `/app/email-queue`; User có email |
| Redis queue chết | Webhook vẫn trả 200, tin ở New | Khởi động lại Redis; tác vụ quét đẩy lại sau 2 phút; bấm **Quét ngay**; worker do supervisor khởi động lại |
| Worker bị kill / máy mất điện | Không mất tin, phục hồi chậm (~15 phút đo được) | Chờ hoặc **Quét ngay** sau khi kho đăng ký RQ dọn |
| Nghi trùng tăng bất thường | Độ trễ DB (khóa tín hiệu bận) | Xem [06](06-chong-trung-va-lead.md) mục 6 |
| Lead quảng cáo vào chậm (nhiều phút) khi chat đông | `omni_leads` chưa khai báo hoặc chưa có worker: lead đi chung hàng đợi `omni` với chat | [01](01-cai-dat-va-ha-tang.md) mục 3: khai báo `omni_leads`, chạy từ 2 worker (đề xuất 3 đến 4 ở production) |
| Webhook trả 5xx / có tin trong hàng dự phòng | DB khóa hoặc mất kết nối | Xem cảnh báo "Tin nằm lâu trong hàng dự phòng webhook" ở mục 2; chạy tay `omni_crm.ingest.spool.drain` |
| Meta không còn gửi lead, Graph báo lỗi quyền | Token Page hết hạn hoặc bị thu quyền | Cảnh báo "Token Meta cần xử lý"; cấp token mới ([12](12-lead-quang-cao.md) mục 6) |
| Graph Meta chết một lúc rồi sống lại, có tin Manual | Tin Manual lỗi tạm thời | Không cần làm gì: lượt đối soát hằng giờ kéo lại (tối đa 3 lần/tin, trong 72 giờ). Tin lỗi vĩnh viễn thì xử lý theo cảnh báo |
| Phát hành / nghi cấu hình sao chép nhầm từ site thử | Bot bật, sandbox sai, khóa tự gửi còn | Chạy lệnh kiểm trước phát hành (mục 7), sửa mọi mục `KHONG_DAT` |

## 5. Quay lui

Hai mức đầu là việc của người vận hành tại chỗ, không cần deploy:

| Mức | Làm gì | Ảnh hưởng |
| --- | --- | --- |
| 1 | Tắt công tắc của phần lỗi: bot (`bot_reply_mode`, `bot_analyze_enabled`), tự gửi của một nhóm, `outbound_enabled`, `enabled` của kênh | Chỉ phần đó dừng; dữ liệu vẫn được lưu. Tắt `outbound_enabled`: tin Queued thành Blocked, bật lại **không** tự gửi lại. Tắt `enabled` kênh: tin vào vẫn lưu, xử lý bù khi bật |
| 2 | `bot_reply_mode = Off` **và** `bot_analyze_enabled = 0` | Hệ thống chỉ còn là "kênh vào CRM" như Đợt 1: tin vào, Lead, dòng thời gian vẫn đủ; 0 nháp, 0 tin bot |

Mức 3 (deploy lại phiên bản app trước bằng git tag rồi `bench migrate`) và mức 4 (tắt `enabled` của kênh) tham khảo `docs/11-van-hanh.md` mục 4; mức 3 cần người có quyền deploy, migration chỉ thêm trường/DocType trong một đợt nên quay lại bản trước an toàn về dữ liệu. Chạy trong `<bench-dir>` (chỉ làm khi đã có kế hoạch được duyệt):

```bash
git -C apps/omni_crm checkout <tag-bản-trước>
bench --site <site> migrate
bench restart
```

## 6. Sao lưu và dữ liệu

- Sao lưu DB hằng ngày (`bench --site <site> backup --with-files`, [01](01-cai-dat-va-ha-tang.md) mục 8); kiểm khôi phục mỗi quý.
- `raw_payload` xóa sau `raw_payload_retention_days` (180) lúc 02:00 hằng đêm, theo lô, chỉ tin đã xử lý xong.
- Yêu cầu xóa dữ liệu khách: [06](06-chong-trung-va-lead.md) mục 5, **kèm các giới hạn hiện có**.

## 7. Lịch kiểm tra

| Tần suất | Việc | Người |
| --- | --- | --- |
| Hằng ngày | Mở `/app/omni-monitor`: tin Manual (xử lý), nghi trùng, việc gọi lại quá hạn, hạn token Zalo, hạn mức token; đọc thư cảnh báo | Quản trị hệ thống |
| Hằng tuần | Xem nháp bị sửa/bỏ ở báo cáo "Bot nháp"; cập nhật kho tri thức; **chạy lại bộ đề**; xem `Omni Bot Run` lỗi | Phụ trách kho tri thức |
| Hằng tháng | Báo cáo kênh, chi phí AI, rà quyền vai trò (Omni Admin, Omni Knowledge Editor), cập nhật `holidays` khi cần | Chủ dự án / quản trị |
| Hằng quý | Kiểm khôi phục sao lưu; xem lại bảng điểm Lead theo tỉ lệ chốt thực tế | Quản trị / trưởng sale |
| Mỗi lần phát hành | Chạy **lệnh kiểm trước phát hành** (xem dưới bảng này) và xử lý mọi mục `KHONG_DAT`; đọc từng mục `CAN_NGUOI_KIEM` | Quản trị |
| Hằng ngày khi có chiến dịch chạy | Thẻ Kéo lại lead Meta và hạn token Meta ở `/app/omni-monitor`; số lead mới theo chiến dịch ở Tổng quan ([13](13-dashboard-va-cham-soc.md)) | Quản trị / trưởng sale |

**Lệnh kiểm trước phát hành** (chỉ đọc, V21-08), chạy trong `<bench-dir>`:

```bash
bench --site <site> execute omni_crm.ops.preflight.run --kwargs "{'target': 'production'}"
```

Bảng kết quả `DAT` / `KHONG_DAT` / `CAN_NGUOI_KIEM`; thoát mã 1 khi còn `KHONG_DAT`. Danh sách mục nó kiểm ở [02](02-cau-hinh-omni-settings.md) mục 11B. Lệnh không gọi Meta, Google, Zalo hay nginx thật nên `DAT` không thay cho việc thử trên hệ thống thật; nó cũng không đo scheduler có thật sự chạy.

Số liệu mục tiêu theo dõi (từ `docs/00-tong-quan.md`): không mất tin; Lead trùng < 3%; tin vào đến Lead < 1 phút; phản hồi lần đầu giờ hành chính < 15 phút; nháp đúng ≥ 85%; trường trích xuất đúng ≥ 90%; tin xử lý tay < 1%; gọi nhỡ gọi lại đúng hạn ≥ 80%. Các script đo (`measure_dot1/2/3.py`, chạy bằng `bench --site <site> execute omni_crm.tests.acceptance.measure_dot1.run --kwargs '{"start": "...", "end": "..."}'`; giờ theo múi giờ của site) đã có; **chưa đo trên khách thật** (xem [10](10-quy-trinh-len-san-xuat.md)).

## 8. Đã xong khi

- [ ] Quản trị mở được `/app/omni-monitor` và hiểu từng khối ở mục 1.
- [ ] `alert_recipients` đã điền và đã thử cảnh báo (tắt worker 10 phút trên staging) nhận được thư.
- [ ] Có người phụ trách xử lý tin Manual hằng ngày và có người dự phòng.
- [ ] Người vận hành biết quay lui mức 1 và 2 (tắt bot, tắt nhóm, tắt kênh) và đã thử một lần trên staging.
- [ ] Sao lưu hằng ngày chạy; đã khôi phục thử một lần.
- [ ] Lịch kiểm hằng ngày/tuần/tháng/quý đã giao người.
- [ ] Người nhận cảnh báo biết các cảnh báo mới của mục 2 (hàng đợi `omni_leads`, hàng dự phòng webhook, token Meta, lead Meta không kéo lại được, không có lead quảng cáo) và việc đầu tiên cần làm với từng cái.
- [ ] Đã chạy lệnh kiểm trước phát hành ở mục 7 và lưu kết quả cùng ngày giờ.
