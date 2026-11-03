# Đặc tả v2.1 — sửa trước chiến dịch

Bản v2.1 chạy trên `develop` sau tag `v2-08`. Nó không thêm tính năng lớn. Nó sửa bảy điểm kỹ thuật mà nghiệm thu v2 tìm ra, cập nhật bộ hướng dẫn cho đúng v2, và thêm một lệnh kiểm cấu hình trước khi phát hành.

Nguồn: `docs/nghiem-thu/v2.md`, `docs/huong-dan/tong-hop-he-thong.md`, `docs/huong-dan/truoc-ngay-chay-chien-dich.md` (bản ngày 2026-11-03).

Quy tắc chung giữ nguyên `CLAUDE.md` và `README-v2.md` mục 4. Thêm:

- Mỗi mục dưới đây có **tiêu chí đo**. Mục nào không đo được trên site test thì ghi "chưa đo" và nêu cần môi trường gì.
- Không đổi hành vi với khách. Nếu một sửa đổi buộc phải đổi hành vi với khách, ghi ADR "Đề xuất" và dừng.
- Mọi tham số mới nằm ở Omni Settings hoặc `site_config.json`, có giá trị mặc định an toàn, ghi vào `docs/09`.

## F1. Hàng đợi riêng cho lead quảng cáo

**Vấn đề:** lead quảng cáo và tin chat dùng chung hàng đợi `omni`. Khi chat đông, thời gian từ lúc nhận tới lúc có người phụ trách lên tới khoảng 113 giây (p95). Khi không có chat, con số này chỉ khoảng 5,5 giây.

**Sửa:**

1. Thêm hàng đợi `omni_leads`. Các việc của kênh Meta Lead Ads, Google Lead Form và Web Form đi vào hàng đợi này: xử lý tin, gọi Graph API, giao sale, thông báo. Việc của email, Zalo, tổng đài và bot giữ ở `omni`.
2. Việc định tuyến hàng đợi đặt ở một chỗ, ví dụ `queue.py::queue_for(channel_type)`. Không ghi cứng tên hàng đợi rải rác trong code.
3. Khóa theo từng khách và cơ chế gộp tin vẫn áp dụng như cũ. Một khách vừa điền form vừa nhắn Zalo vẫn không bị hai worker xử lý chồng.
4. Tác vụ quét tin kẹt và lệnh `reprocess` dùng cùng hàm định tuyến.
5. Thiếu khai báo `omni_leads` trong `common_site_config.json` thì rơi về `omni` và ghi cảnh báo, không lỗi.
6. Cập nhật `docs/01` (sơ đồ), `docs/03` (hàng đợi), `docs/11` (số worker): `omni_leads` từ 2 worker, `omni` theo lưu lượng chat. Cập nhật hướng dẫn supervisor ở `docs/huong-dan/01-…`.

**Tiêu chí đo:** chạy lại đúng kịch bản tải của V2-08 (200 lead trong 10 phút, chat cao điểm chạy cùng) với 2 worker `omni_leads` và số worker `omni` như cũ. p95 từ lúc nhận tới lúc có người phụ trách ≤ 30 giây. Không mất tin, không trùng.

## F2. Cảnh báo token Meta sắp hết hạn

**Vấn đề:** hệ thống chỉ cảnh báo token Zalo hết hạn (lỗi mở O-05). Token Page của Meta hết hạn thì lead ngừng vào, mà không ai biết cho tới khi luật "không có lead N giờ" kích hoạt.

**Sửa:**

1. Tác vụ hằng ngày gọi `GET /debug_token` của Graph API với token Page của từng kênh Meta, xác thực bằng app access token. Đọc `is_valid`, `expires_at`, `data_access_expires_at` và danh sách quyền (`scopes`).
2. Cảnh báo, theo cùng kênh cảnh báo hiện có, khi gặp một trong các trường hợp:
   - Token không hợp lệ.
   - Còn ≤ 7 ngày tới `expires_at` hoặc `data_access_expires_at`. Ngưỡng 7 ngày đặt được.
   - Thiếu quyền `leads_retrieval`.
3. `expires_at = 0` nghĩa là token không hết hạn (token System User). Khi đó không cảnh báo hết hạn, nhưng vẫn kiểm `is_valid` và quyền.
4. Màn `/app/omni-monitor` hiện hạn token Meta cạnh hạn token Zalo.
5. Hướng dẫn khuyến nghị dùng token System User của Meta Business để không phải gia hạn định kỳ.
6. Không ghi token vào log hay Error Log.

**Tiêu chí đo:** có test với phản hồi `debug_token` giả cho từng trường hợp: hợp lệ, sắp hết hạn, đã hết hạn, `expires_at = 0`, thiếu quyền. Dùng token thật trên staging thì ghi "chưa đo".

## F3. Tự kéo lại lead Meta sau sự cố Graph API

**Vấn đề:** khi Graph API lỗi quá khoảng 17 phút, tin hết lượt thử lại và chuyển sang Manual. Đối soát hằng giờ chỉ tìm lead chưa có tin, nên bỏ qua tin Manual. Phải chạy `reprocess` bằng tay.

**Sửa:**

1. Đối soát Meta hằng giờ thêm một bước: tìm tin Meta ở trạng thái Manual trong vòng 72 giờ (đặt được), có lỗi thuộc loại tạm thời (mạng, timeout, mã 5xx, giới hạn tần suất). Đưa các tin này về hàng đợi, mỗi lần đối soát tối đa N tin (mặc định 200).
2. Lỗi vĩnh viễn (token sai, thiếu quyền, lead đã bị xóa) không kéo lại. Các lỗi này sinh cảnh báo.
3. Ghi số tin đã kéo lại vào kết quả đối soát và lên màn giám sát.
4. Quy tắc phân loại lỗi tạm thời và vĩnh viễn đặt ở một hàm, có test.

**Tiêu chí đo:** chạy lại kịch bản sự cố "Graph chết 30 phút" của V2-08. Sau khi Graph sống lại, trong lần đối soát kế tiếp toàn bộ lead được tạo, không phải chạy `reprocess` tay.

## F4. Webhook không được làm mất tin khi chờ khóa

**Vấn đề:** webhook trả 503 khi chờ khóa DB quá `omni_webhook_lock_wait_seconds` (mặc định 5 giây). Nếu nhà cung cấp không gửi lại, tin mất. Hiện chưa xác nhận Google, Zalo hay Stringee có gửi lại khi gặp 5xx. Meta thì có đối soát bù.

**Sửa:**

1. Tìm nguyên nhân webhook phải chờ khóa. Cổng chỉ nên chèn một bản ghi `Omni Inbound Message`. Chống trùng phải dựa vào ràng buộc duy nhất `dedupe_key`: bắt lỗi trùng khóa thì coi là đã nhận và trả 200. Không được giữ khóa bảng hay khóa dòng của bản ghi khác trong lúc xử lý webhook. Ghi nguyên nhân tìm được vào báo cáo.
2. Nếu sau khi sửa vẫn còn trường hợp không ghi được DB (DB tạm mất kết nối, hết thời gian chờ), ghi payload thô kèm header cần cho chữ ký vào **hàng dự phòng**. Hàng này là danh sách Redis có bật lưu xuống đĩa, hoặc tệp nối thêm trong thư mục riêng tư của site. Agent chọn một cách và ghi ADR. Sau đó trả 200.
3. Tác vụ quét đọc hàng dự phòng mỗi phút và chèn lại vào `Omni Inbound Message`. Chống trùng vẫn theo `dedupe_key`, và chữ ký vẫn được kiểm.
4. Chỉ trả 5xx khi cả DB lẫn hàng dự phòng đều không ghi được. Khi đó ghi log mức nghiêm trọng và cảnh báo ngay.
5. Màn giám sát hiện số tin đang nằm trong hàng dự phòng. Cảnh báo khi có tin nằm quá 10 phút.

**Tiêu chí đo:** test giả lập DB khóa hoặc mất kết nối: webhook trả 200, tin vào hàng dự phòng, rồi được chèn lại đúng một lần. Thử tải lại kịch bản V2-08: không còn phản hồi 503 do chờ khóa.

## F5. Zalo OA theo bảng gói 2026 và kiểm chữ ký trên OA thật

**Bối cảnh:** từ 1/6/2026 Zalo áp dụng bốn gói OA: Cơ bản, Tiêu chuẩn, Tăng trưởng, Toàn diện. Theo trang giá của Zalo, chỉ gói Tăng trưởng và Toàn diện mới liên kết được ứng dụng với OA qua API. Mỗi gói có hạn mức tin tư vấn gửi ngoài khung 48 giờ, quá hạn mức thì tính phí theo tin. Công ty đã có OA nhưng chưa xác nhận đang ở gói nào. Code hiện tại ghi cứng hai mốc 48 giờ / 7 ngày, chưa đối chiếu với Zalo.

**Sửa:**

1. Hai mốc thời gian đã là tham số của `Omni Channel` (`zalo_cs_free_hours`, `zalo_cs_max_days`). Thêm `zalo_oa_package` (Select: Chưa xác nhận / Cơ bản / Tiêu chuẩn / Tăng trưởng / Toàn diện) và `zalo_cs_paid_quota_month` (Int, hạn mức tin ngoài 48 giờ mỗi tháng, để trống = chưa biết).
2. Đếm số tin sale đã gửi ngoài khung 48 giờ trong tháng. Hộp thư hiện "Đã dùng X / hạn mức Y tin ngoài 48 giờ tháng này", hoặc "chưa biết hạn mức" khi trống. Không chặn gửi theo hạn mức, chỉ báo.
3. Gói "Chưa xác nhận", Cơ bản hoặc Tiêu chuẩn: màn giám sát cảnh báo "Gói OA có thể chưa cho kết nối API". Không chặn kênh, vì kết quả thật quyết định bằng phép thử trên OA.
4. **Công cụ chẩn đoán chữ ký** (chỉ dùng khi xác minh trên staging): khi bật cờ `omni_zalo_signature_debug_until` (thời điểm hết hạn, tối đa 2 giờ) trong `site_config.json`, mỗi webhook Zalo bị từ chối chữ ký được ghi vào một bản ghi riêng tư, chỉ Omni Admin đọc được. Bản ghi gồm: header chữ ký nhận được, chữ ký hệ thống tính ra, độ dài thân và 200 ký tự đầu của thân. Không ghi `oa_secret_key`. Hết hạn thì tự tắt và xóa các bản ghi này sau 24 giờ. Có hướng dẫn cách dùng công cụ để xác nhận Z1 trong 15 phút.
5. Cập nhật `docs/06` và `docs/huong-dan/04-…`:
   - Mục "Gói OA và điều kiện API".
   - Bước "Kiểm gói trong trang quản trị OA, mục Quản lý gói".
   - Bỏ câu "7 ngày có phí" chưa xác nhận, thay bằng "theo gói và hạn mức, xác nhận với Zalo".

**Tiêu chí đo:** test cho phần đếm hạn mức, phần cảnh báo gói và công cụ chẩn đoán (tự tắt khi hết hạn, không chứa khóa bí mật). Phép thử chữ ký thật trên OA: ghi "chưa đo, cần OA của công ty".

## F6. Ý định và nhóm câu hỏi theo bối cảnh nhà thầu

**Vấn đề:** danh sách ý định của bot vẫn theo khách lẻ (Tư vấn / Hỏi giá / Đặt hàng / Khiếu nại / Khác), trong khi bảng điểm B2B dùng các tín hiệu `intent_service_info`, `intent_fee`, `intent_proposal`, `complaint`. Hướng dẫn `07-bot-ai.md` vẫn mô tả nhóm câu hỏi kiểu bán lẻ (Thông tin sản phẩm, Thủ tục mua hàng, Hỏi giá, Đặt hàng).

**Sửa:**

1. Danh sách ý định mới cho `record_lead_signals` và `omni_intent`:

   | Ý định | Tín hiệu điểm | Chuyển người |
   | --- | --- | --- |
   | Hỏi cách vận hành | `intent_service_info` | Không |
   | Hỏi gói dịch vụ | `intent_service_info` | Không |
   | Hỏi phí | `intent_fee` | Luôn |
   | Yêu cầu đề xuất | `intent_proposal` | Luôn |
   | Khiếu nại | `complaint` | Luôn |
   | Khác | — | Theo nhóm câu hỏi |

2. Migration chỉ **thêm** giá trị mới vào Select `omni_intent`, giữ giá trị cũ để không lỗi dữ liệu. Patch chạy được nhiều lần, có dry run, ánh xạ Lead đang mở: Hỏi giá → Hỏi phí, Đặt hàng → Yêu cầu đề xuất, Tư vấn → Hỏi cách vận hành.
3. Câu lệnh `analyze_v2`: dùng danh sách ý định mới; bỏ các trường kiểu bán lẻ `products`, `quantity`, `timeframe_within_30d`. Giữ các cột cũ trên Lead, không xóa. Tăng phiên bản schema.
4. Chốt chặn bằng code coi "Hỏi phí", "Yêu cầu đề xuất", "Khiếu nại" là luôn chuyển người, độc lập với nhóm câu hỏi.
5. Bộ đề: cập nhật `expected_intent` của các ca B2B theo danh sách mới. Ca v1 giữ nguyên, có ánh xạ khi chấm.
6. Hướng dẫn `07-bot-ai.md` dùng nhóm câu hỏi B2B (`mau/nhom-cau-hoi-mau.csv`).
7. Câu lệnh đang dùng vẫn ghim ở bản v1 cho tới khi đo được cổng. Sửa đổi này chỉ chuẩn bị `analyze_v2` và dữ liệu.

**Tiêu chí đo:** test schema; ánh xạ ý định sang tín hiệu điểm; chốt chặn với từng ý định luôn chuyển người; patch dry run trên dữ liệu tổng hợp.

## F7. Chốt chặn chủ đề không bị né bằng cách viết

**Vấn đề:** chốt chặn chủ đề phí và đàm phán còn lọt khi khách viết cách chữ ("đ à m p h á n") hoặc viết tắt kiểu "ph1 dich vu". Nó lại chặn nhầm "100 phần trăm" và "thỏa thuận lịch hẹn". Chặn nhầm thì chỉ chuyển người, nên chấp nhận được.

**Sửa:**

1. Chuẩn hóa văn bản trước khi so khớp. So khớp trên cả bản gốc lẫn bản đã chuẩn hóa. Các bước chuẩn hóa:
   - Chữ thường; bỏ dấu, kể cả đổi `đ` thành `d`.
   - Đổi ký tự số và ký hiệu hay dùng thay chữ: `1→i`, `0→o`, `3→e`, `4→a`, `@→a`, `$→s`.
   - Gộp các chuỗi chữ đơn cách nhau bởi khoảng trắng hoặc dấu câu: `d a m p h a n` → `damphan`.
   - Bỏ ký tự lặp quá hai lần.
2. Từ khóa cũng được chuẩn hóa theo cùng cách. So khớp thêm dạng viết liền: "dam phan" và "damphan" đều khớp.
3. Bộ ca kiểm thử chốt chặn gồm ít nhất 60 ca: 40 ca né (cách chữ, số thay chữ, không dấu, viết liền, xen emoji) phải bị chặn, và 20 ca câu thường phải không bị chặn. Báo số ca chặn nhầm và số ca lọt.
4. Chốt chặn chỉ quyết định "chuyển người". Nó không bao giờ là lý do để gửi tin cho khách.

**Tiêu chí đo:** 40/40 ca né bị chặn. Số ca chặn nhầm trên 20 câu thường được ghi lại, không yêu cầu bằng 0.

## F8. Bộ hướng dẫn cập nhật cho v2

**Vấn đề:** các tệp `docs/huong-dan/01` tới `10` viết trước v2. Mục lục còn ghi "Chưa phát hành" theo nghiệm thu Đợt 3. Hướng dẫn bot vẫn theo khách lẻ.

**Sửa:**

1. Cập nhật `00-muc-luc.md` và `tong-hop-he-thong.md` theo trạng thái sau v2.1.
2. Thêm ba tệp:
   - `11-nha-thau-va-nhap-danh-sach.md`: nhập Excel, chạy thử rồi nhập thật, xử lý danh sách "cần xác nhận", điểm phù hợp.
   - `12-lead-quang-cao.md`: Meta, Google, form website, `Omni Lead Form`, `Omni Campaign`, SLA, báo cáo chiến dịch, xử lý sự cố.
   - `13-dashboard-va-cham-soc.md`: dành cho trưởng sale và sale; Tổng quan, Chi tiết nhà thầu, Nhật ký chăm sóc.
3. Sửa `02` (tham số mới của v2 và v2.1), `04` (theo F5), `07` (theo F6), `08` (Hộp thư v2), `09` (cảnh báo mới), `10` (thêm bước của chiến dịch).
4. Mọi chỗ mô tả giao diện chưa mở bằng trình duyệt ghi "Chưa kiểm trên giao diện".

**Tiêu chí đo:** không còn tệp hướng dẫn nào nói về nhóm "Đặt hàng" hay "Thông tin sản phẩm". Mọi tham số trong `docs/09` đều có trong `02`. Người duyệt đọc được `11`, `12`, `13` mà không cần mở đặc tả.

## F9. Lệnh kiểm cấu hình trước phát hành

**Vấn đề:** site test đang để bot ở Draft và bật phân tích. Rất dễ sao chép nhầm cấu hình sang production. Danh sách kiểm trước ngày chạy chiến dịch lại có nhiều mục kiểm được bằng máy.

**Sửa:**

Lệnh chỉ đọc `bench --site <site> execute omni_crm.ops.preflight.run --kwargs "{'target': 'production'}"`. `target` nhận `staging` hoặc `production`. Lệnh in bảng gồm mục, kết quả (ĐẠT / KHÔNG ĐẠT / CẦN NGƯỜI KIỂM) và cách sửa. Các mục kiểm:

| Mục | production | staging |
| --- | --- | --- |
| `developer_mode` | tắt | tắt |
| `zalo_skip_signature` (mọi kênh) | tắt | tắt |
| `omni_auto_send_allowed` | vắng (trừ khi Q1 đã chốt) | vắng |
| `omni_ai_allow_customer_data` | khớp quyết định Q11 trong `docs/12` | vắng |
| `bot_reply_mode` / `bot_analyze_enabled` | Off / 0, trừ khi `docs/12` ghi cổng đã đạt | tùy |
| `sandbox_mode` | báo trạng thái, nhắc "tắt có chủ đích" | bật |
| Scheduler | bật | bật |
| Hàng đợi `omni`, `omni_leads` có worker đang chạy | có | có |
| `alert_recipients`, `business_hours`, `holidays` | đã điền (`holidays` trống thì nhắc) | đã điền |
| Kênh quảng cáo đang bật có ít nhất một `Omni Lead Form` bật, mỗi form có `default_owner` hoặc `assignment_rule` | có | có |
| Token Meta (F2), token Zalo | còn hạn > 7 ngày | còn hạn |
| Gói Zalo OA (F5) | đã xác nhận | — |
| Hàng dự phòng webhook (F4) | rỗng | rỗng |
| Có bản sao lưu trong 24 giờ | có | — |

Lệnh không bao giờ tự sửa cấu hình. Thoát với mã khác 0 khi có mục KHÔNG ĐẠT, để dùng được trong quy trình triển khai.

**Tiêu chí đo:** test cho từng mục với cấu hình đạt và không đạt. Chạy trên `omni.test` với target production phải ra KHÔNG ĐẠT ở mục bot (đúng với hiện trạng). Dán kết quả.

## Ngoài phạm vi v2.1

Tổng đài (chờ chọn nhà cung cấp), ghi âm và tóm tắt cuộc gọi, đo cổng bot bằng AI thật (chờ Q11), gửi email xác nhận tự động (chờ Q12), hoàn tác gộp Lead.
