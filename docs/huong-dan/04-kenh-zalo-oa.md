# 04 · Kênh Zalo OA

Dành cho: quản trị hệ thống, có tài khoản quản trị Zalo Official Account (OA) và Zalo for Developers của công ty. Đặc tả: `docs/06-kenh-zalo-oa.md`.

> **ĐỌC TRƯỚC.** Mã Zalo (công thức chữ ký, tên sự kiện, OAuth, API gửi tin, mã lỗi, khung 48 giờ/7 ngày) được viết theo đặc tả và hiểu biết sẵn có, **chưa được đối chiếu với tài liệu hay API Zalo thật** (`docs/12` mục B2, các mục Z1-Z9, G7-G9). Test tự động ký bằng đúng công thức của mã nên không chứng minh gì về công thức thật. **Điểm dễ sai nhất là chữ ký (Z1)**: nếu sai, mọi webhook thật bị trả 401 và không có tin nào vào. Vì vậy phải thử trên **staging với OA thử** trước khi bật cho khách (xem mục 7 và [10](10-quy-trinh-len-san-xuat.md)). Ngày 2026-11-03 đã thử đối chiếu lại bằng máy, nhưng trang tài liệu chữ ký của Zalo là trang động nên máy không đọc được nội dung: **Z1 vẫn CHƯA xác nhận**. Mục 3A là cách xác nhận trên OA thật (chưa ai làm).

Mục lục: [1. Chuẩn bị](#1-chuẩn-bị-phía-zalo) · [2. Tạo kênh](#2-tạo-omni-channel-loại-zalo-oa) · [3. Webhook và chữ ký](#3-webhook-và-chữ-ký) · [3A. Chẩn đoán chữ ký](#3a-xác-nhận-công-thức-chữ-ký-z1-trong-15-phút) · [4. Cấp quyền/token](#4-cấp-quyền-và-token) · [4A. Gói OA](#4a-gói-oa-và-điều-kiện-api) · [5. Gửi tin và khung thời gian](#5-gửi-tin-khung-48-giờ-và-7-ngày) · [6. Các tùy chọn khác](#6-tùy-chọn-khác-của-kênh) · [7. Kiểm thử bằng sandbox](#7-kiểm-thử-bằng-sandbox) · [8. Sự cố thường gặp](#8-sự-cố-thường-gặp) · [9. Đã xong khi](#9-đã-xong-khi)

## 1. Chuẩn bị phía Zalo

- OA đã xác thực; ứng dụng tạo trên Zalo for Developers và liên kết với OA. Ghi lại **App ID**, **App secret**, **OA ID**, **OA secret key** (khóa dùng để ký webhook). Cất ở nơi an toàn, không dán vào chat hay tài liệu.
- Tên miền webhook đã được xác thực với Zalo và dùng **HTTPS**. Theo khuyến nghị trong đặc tả, máy chủ đặt tại Việt Nam để nhận đủ thông tin người dùng.
- Webhook phải trả 200 trong **5 giây** (cổng của app nhắm dưới 1 giây).
- Bật các sự kiện: người dùng gửi tin (văn bản, ảnh, tệp, sticker, vị trí, tin thoại), quan tâm/bỏ quan tâm, người dùng gửi thông tin (form yêu cầu thông tin), và OA gửi tin (để ghi lại tin nhân viên trả lời trực tiếp trên ứng dụng Zalo OA).

## 2. Tạo Omni Channel loại Zalo OA

Mở **Desk > `/app/omni-channel` > Thêm mới**.

| Trường (nhãn) | Tên kỹ thuật | Điền |
| --- | --- | --- |
| Tên kênh | `channel_name` | Tên ngắn **không có dấu cách**, ví dụ `zalo-miyano` (dễ đưa vào URL). |
| Loại kênh | `channel_type` | `Zalo OA` |
| App ID | `zalo_app_id` | Bắt buộc, không có thì không lưu được. |
| OA ID | `zalo_oa_id` | ID của OA. |
| App secret | `zalo_app_secret` | Dạng mật khẩu, không hiện lại. Dùng khi đổi/làm mới token. |
| OA secret key | `zalo_oa_secret_key` | Dạng mật khẩu. Dùng **xác thực chữ ký webhook** (công thức ở [mục 3](#3-webhook-và-chữ-ký)). |
| Access token / Refresh token | `zalo_access_token`, `zalo_refresh_token` | Thường được **điền tự động** bởi nút "Kết nối Zalo OA" (mục 4); có thể nhập tay khi cần. Cả hai dạng mật khẩu. |
| Access token hết hạn lúc | `zalo_token_expires_at` | Chỉ đọc, hệ thống tự cập nhật. |
| Gói Zalo OA | `zalo_oa_package` | Mặc định `Chưa xác nhận`. Chọn gói thật sau khi xem trong trang quản trị OA (mục 4A). Chỉ để báo, không chặn gì. |
| Hạn mức tin tư vấn ngoài 48 giờ mỗi tháng | `zalo_cs_paid_quota_month` | Để trống nếu chưa biết. Hộp thư dùng số này để hiện "Đã dùng X / Y". |
| Khung gửi tin tư vấn miễn phí (giờ) | `zalo_cs_free_hours` | Mặc định 48. |
| Hạn tối đa gửi tin tư vấn (ngày) | `zalo_cs_max_days` | Mặc định 7. |
| Bỏ qua kiểm tra chữ ký (chỉ site dev) | `zalo_skip_signature` | **Để tắt.** Xem [01](01-cai-dat-va-ha-tang.md) mục 11. |
| Tạo Lead khi khách chỉ bấm quan tâm OA | `zalo_create_lead_on_follow` | Mặc định tắt (Q9, chưa chốt). Bật = mỗi lượt "quan tâm OA" tạo một Lead dù khách chưa nhắn gì. |
| Bật kênh | `enabled` | Tích **sau cùng**. Tắt: webhook vẫn trả 200 và lưu tin nhưng không xử lý. |
| Bật chiều ra | `outbound_enabled` | Tích khi muốn sale trả lời từ Hộp thư. Tắt: tin ra bị `Blocked`. |
| Token webhook | `webhook_token` | Không dùng cho Zalo (dùng cho tổng đài). Để trống. |

Lưu lại. Kênh bật mà chưa có token thì nhận tin vẫn được, nhưng gửi tin ra thì không.

## 3. Webhook và chữ ký

**URL webhook** (dán vào cấu hình Webhook của ứng dụng trên Zalo for Developers):

```text
https://<site>/api/method/omni_crm.api.webhooks.zalo?ch=<tên kênh>
```

Ví dụ: `https://crm.example.vn/api/method/omni_crm.api.webhooks.zalo?ch=zalo-miyano`. Tham số `ch` là **bắt buộc** (thiếu hoặc sai tên kênh thì trả 404). Nếu tên kênh có dấu cách phải thay bằng `%20`, nên đặt tên không dấu cách để khỏi nhầm.

**Chữ ký** theo đặc tả: header `X-ZEvent-Signature` dạng `mac=<hex>`, với `hex = sha256(app_id + thân_request + timestamp + oa_secret_key)`; timestamp lệch quá 10 phút so với giờ máy chủ thì bị từ chối (chống phát lại). Công thức này **chưa được xác nhận** (Z1). Dấu hiệu công thức sai: sau khi cấu hình, Zalo gửi sự kiện thật nhưng không có tin vào, màn hình giám sát không thấy "Nhận tin gần nhất" của kênh và Error Log (`/app/error-log`) xuất hiện tiêu đề **"Omni webhook: xac_thuc_that_bai"**. Khi đó đừng bật `zalo_skip_signature`: báo nhà phát triển kèm một ví dụ sự kiện (đã che nội dung) để đối chiếu công thức với tài liệu chính thức. Giá trị header, đường dẫn API và hạn timestamp có thể ghi đè bằng khóa `omni_zalo` trong site_config ([01](01-cai-dat-va-ha-tang.md) mục 10) mà không cần sửa mã.

Zalo gửi lại sự kiện nếu không nhận 200: hệ thống khử trùng lặp theo mã tin nên gửi lại không tạo tin thứ hai.

## 3A. Xác nhận công thức chữ ký (Z1) trong 15 phút

Dùng khi webhook thật bị 401 hoặc trước khi bật cho khách. **Chỉ làm trên staging với OA thử; không làm trên production.** Công cụ không đổi công thức, chỉ ghi lại webhook bị từ chối để đối chiếu. **Chưa đo trên OA thật** (cần OA của công ty trên staging).

1. (2 phút) Kênh Zalo OA đã có App ID và `oa_secret_key` đúng; URL webhook (mục 3) đã khai báo ở Zalo; `sandbox_mode` bật.
2. (1 phút) Bật công cụ, hạn tối đa 2 giờ. Trên máy chủ: `bench --site <site> set-config omni_zalo_signature_debug_until "2026-11-03T15:30:00"` (giờ của site, ít hơn 2 giờ kể từ bây giờ). Đặt hạn xa hơn cũng chỉ có hiệu lực 2 giờ từ lần đầu hệ thống thấy giá trị. Kiểm: `/app/omni-monitor`, khối Công tắc có dòng "Chẩn đoán chữ ký Zalo ĐANG BẬT tới ...". Chưa kiểm trên giao diện; nếu không thấy dòng này, chạy `bench restart` rồi xem lại.
3. (3 phút) Từ tài khoản Zalo thử, nhắn một tin cho OA (và bấm Quan tâm/Bỏ quan tâm nếu muốn thêm mẫu).
4. (3 phút) Đọc bản ghi, bằng Omni Admin: `bench --site <site> execute omni_crm.api.admin.zalo_signature_probes` hoặc mở `/api/method/omni_crm.api.admin.zalo_signature_probes` khi đang đăng nhập Omni Admin. Mỗi bản ghi có: `reason` (`chu_ky_sai`, `thieu_chu_ky`, `timestamp_lech`, `body_khong_doc_duoc`), `signature_received` (chữ ký Zalo gửi), `signature_computed` (chữ ký hệ thống tính theo công thức hiện hành), `matched_variants` (công thức ứng viên nào khớp chữ ký nhận được), `header_names` (tên các header nhận được, không có giá trị), `body_has_mac_field`, `body_length`, `body_preview` (200 ký tự đầu, có thể chứa chữ của khách nên chỉ Omni Admin xem).
5. (3 phút) Đọc kết quả:
   - `matched_variants` chứa `sha256(app_id+body+timestamp+secret)` và `reason = timestamp_lech`: công thức ĐÚNG, giờ máy chủ lệch (kiểm NTP).
   - `signature_received` rỗng, `header_names` không có `x-zevent-signature`: Zalo dùng tên header khác hoặc đặt chữ ký trong body (`body_has_mac_field = true`). Ghi lại, báo nhà phát triển.
   - `matched_variants` chứa công thức KHÁC: công thức thật khác mã. Không tự sửa mã: ghi vào `docs/12` (Z1) và tạo ADR "Đề xuất".
   - `matched_variants` rỗng, `signature_computed` khác `signature_received`: kiểm lại `zalo_app_id`, `zalo_oa_secret_key` (khóa OA, không phải App Secret).
6. (2 phút) Tắt: `bench --site <site> set-config omni_zalo_signature_debug_until ""` (hoặc `remove-config`). Bản ghi tự xóa sau 24 giờ; có thể xóa tay thư mục `<site>/private/omni_sigprobe`.

Webhook ĐƯỢC chấp nhận (chữ ký đúng) thì không có bản ghi. Khi webhook thật vào được và `Done`, ghi kết quả vào `docs/12` mục Z1 kèm ngày; nếu có bản ghi `reason = chu_ky_sai` lặp lại, đừng bật `zalo_skip_signature`.

## 4. Cấp quyền và token

**Cách 1 (khuyên dùng): nút "Kết nối Zalo OA".**

1. Trong ứng dụng Zalo for Developers, khai báo **URL callback OAuth** là `https://<site>/api/method/omni_crm.api.webhooks.zalo_oauth_callback`.
2. Trong Omni Channel đã lưu (kênh Zalo OA), bấm nút **Kết nối Zalo OA** (trên thanh nút của form) rồi xác nhận "Chuyển sang trang Zalo để cấp quyền cho OA?".
3. Đăng nhập tài khoản quản trị OA, đồng ý cấp quyền. Zalo chuyển về callback của app, app đổi mã lấy token và đưa bạn về form kênh; dòng tiêu đề form hiện "Token Zalo hết hạn lúc ...".
4. Chỉ **Omni Admin** đã đăng nhập mới bắt đầu được luồng này.

**Hạn chế quan trọng:** khi `sandbox_mode` **bật**, bước đổi mã lấy token bị chặn và hiện thông báo "Chế độ thử (sandbox) đang bật: không đổi mã cấp quyền lấy token Zalo". Lý do: refresh token của Zalo dùng một lần, site dev/staging chép từ production mà làm mới sẽ làm hỏng token production. Vì vậy để lấy token lần đầu: hoặc tắt sandbox **có chủ đích** trên site thử (không phải bản chép của production), hoặc nhập tay Access token/Refresh token vào form (cách nhập tay chưa kiểm).

**Làm mới token tự động:** tác vụ chạy mỗi 30 phút, làm mới khi token còn dưới 2 giờ. Mỗi lần làm mới Zalo cấp refresh token mới; hệ thống lưu cả hai và dùng khóa để hai tiến trình không làm mới cùng lúc. Lỗi làm mới → cảnh báo ngay (vì quá hạn là mất khả năng gửi tin). Xem hạn token ở khối "Kênh" của `/app/omni-monitor`, có nút **Làm mới token Zalo**. Nếu làm mới thất bại liên tục (refresh token đã hết hạn), bấm lại **Kết nối Zalo OA** để cấp quyền mới.

## 4A. Gói OA và điều kiện API

Từ 1/6/2026 Zalo có bốn gói OA: Cơ bản, Tiêu chuẩn, Tăng trưởng, Toàn diện. Theo trang giá của Zalo (https://zalo.solutions/oa/pricing, đọc 2026-11-03): "Để liên kết ứng dụng với OA thông qua API, bạn cần sử dụng Gói tăng trưởng hoặc toàn diện." Hai gói đầu có thể không cho kênh này kết nối.

**Kiểm gói trong trang quản trị OA, mục Quản lý gói** (chưa kiểm trên giao diện Zalo): đăng nhập quản trị OA bằng tài khoản quản trị viên, mở Quản lý gói, ghi lại tên gói đang dùng, ngày hết hạn và "số lượng tin tư vấn ngoài khung 48h có sẵn/tháng" của gói. Điền vào kênh: `Gói Zalo OA` và `Hạn mức tin tư vấn ngoài 48 giờ mỗi tháng`.

- Để **Chưa xác nhận**, Cơ bản hoặc Tiêu chuẩn: `/app/omni-monitor` hiện "Gói OA có thể chưa cho kết nối API". Đây chỉ là cảnh báo: kết quả thật quyết định bằng việc cấp quyền và nhận webhook thành công.
- Hạn mức tin ngoài 48 giờ: trang giá ghi hai giá trị 500 và 2.000 tin/tháng, phí 55 đ/tin sau hạn mức, nhưng bản đọc được không gắn giá trị với gói (bên thứ ba ghi 500 cho Tăng trưởng, 2.000 cho Toàn diện). Phải đối chiếu trong trang quản trị OA.
- Hộp thư hiện "Đã dùng X / Y tin ngoài 48 giờ tháng này" (hoặc "chưa biết hạn mức" khi để trống). Hệ thống không chặn gửi theo hạn mức.

## 5. Gửi tin, khung 48 giờ và 7 ngày

Mốc tính là **tin cuối cùng của khách gửi cho OA** (`last_inbound_at`). Các giá trị theo mặc định của kênh:

| Thời gian kể từ tin cuối của khách | Ai gửi được | Hành vi |
| --- | --- | --- |
| ≤ 48 giờ (`zalo_cs_free_hours`) | Sale, và bot khi được mở tự gửi | Gửi bình thường. |
| Từ 48 giờ đến 7 ngày (`zalo_cs_max_days`) | **Chỉ sale** | Hộp thư hiện cảnh báo "Tin này có thể bị tính phí (đã quá 48 giờ kể từ tin cuối của khách)." và hỏi xác nhận trước khi gửi. Bot không gửi (không bao giờ). |
| Quá 7 ngày | Không ai | Bị chặn: "Đã quá 7 ngày kể từ tin cuối của khách, Zalo không cho gửi tin tư vấn; hãy liên hệ qua kênh khác." |
| Khách chưa từng nhắn OA | Không ai | Bị chặn: "Khách chưa từng nhắn cho OA nên chưa gửi được tin tư vấn; hãy liên hệ qua kênh khác." |

Hai mốc này là tham số của kênh. Trang giá của Zalo chỉ nói đến hạn mức tin tư vấn ngoài khung 48h theo gói và phí sau hạn mức; **mốc 7 ngày chưa được tài liệu chính thức xác nhận**, và việc tin ngoài 48 giờ bị tính phí thế nào là "theo gói và hạn mức, xác nhận với Zalo" (Z7, G9, xem mục 4A). Cần xác nhận với Zalo trước khi sale dùng khung "có phí".

Khác:

- **Xin SĐT qua Zalo**: nút trong Hộp thư, gửi tin yêu cầu thông tin; chỉ khi Lead chưa có SĐT, và mỗi khách tối đa 1 lần trong 7 ngày. Khách bấm gửi form → hệ thống nhận SĐT (tín hiệu mạnh để gộp khách giữa các kênh).
- Tin dài hơn giới hạn (đang đặt 2.000 ký tự) bị tách thành nhiều tin, giữ thứ tự (giá trị giới hạn chưa xác nhận).
- Lỗi gửi được dịch thành thông báo tiếng Việt (token hết hạn, quá khung, khách đã chặn/bỏ quan tâm, vượt hạn mức). Token hết hạn thì thử làm mới rồi gửi lại một lần.
- Tin nhân viên trả lời trực tiếp trên ứng dụng Zalo OA (không qua CRM) được ghi vào dòng thời gian như tin ra của sale, và dùng làm "câu sale trả lời" khi bot chạy ngầm so sánh.

## 6. Tùy chọn khác của kênh

- **Kênh bị tắt `outbound_enabled`:** mọi tin ra đang chờ thành `Blocked`; bật lại **không** tự gửi lại. Sale gửi lại tay.
- **Tin vào khi kênh tắt `enabled`:** vẫn lưu, xử lý bù khi bật lại.
- **Hộp thư báo "Zalo đang mất kết nối, tin đến vẫn được lưu"** (khối "Tình trạng hệ thống" ở đầu Hộp thư) khi kênh lỗi hoặc token sắp hết hạn: xem mục 8.

## 7. Kiểm thử bằng sandbox

Mục đích: thử toàn bộ đường vào/ra mà **không gửi gì cho khách thật**.

1. Dùng một site staging; `sandbox_mode` **bật**; `developer_mode` **tắt**.
2. Bật kênh (`enabled` + `outbound_enabled`). Chưa có token cũng được cho phần nhận tin; chưa cần OAuth.
3. Dùng một OA thử (không phải OA đang chăm khách) hoặc một tài khoản Zalo của người thử, cấu hình webhook như mục 3, rồi nhắn OA từ một tài khoản Zalo chưa từng nhắn.
4. Kỳ vọng: trong vài chục giây có Lead mới, tên là tên hiển thị Zalo; Hộp thư (`/app/omni-inbox`) có hội thoại; tin vào ở `/app/omni-inbound-message` trạng thái `Done`. Nhắn 3 tin liền: vẫn một Lead.
5. Trả lời từ Hộp thư: tin ra hiện nhãn "thử nghiệm" và dòng "Chế độ thử: không gửi thật"; không có yêu cầu mạng ra Zalo. Khách **không** nhận tin.
6. Muốn thử gửi thật: chỉ khi người phụ trách đồng ý, tắt sandbox có chủ đích và chỉ gửi cho tài khoản thử được chỉ định. Bật lại sandbox ngay sau đó.

Nếu **không có OA thử**, chỉ thử được ở môi trường dev bằng cách gửi sự kiện mẫu: bật `developer_mode` **ở site dev** và tích `zalo_skip_signature` trên kênh thử, rồi POST một tệp mẫu trong `omni_crm/tests/fixtures/zalo/` (ví dụ `user_send_text.json`) tới URL webhook (lệnh `curl` chạy ở máy dev). Cách này **không** chứng minh chữ ký đúng. Không làm trên staging/production.

Kết quả các bước này trên Zalo thật **chưa được kiểm** (nghiệm thu chạy bằng test tự động, tải giả và sandbox).

## 8. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Không có tin vào, Error Log "Omni webhook: xac_thuc_that_bai" | Sai `zalo_oa_secret_key`, sai `zalo_app_id`, hoặc công thức chữ ký (Z1) khác thực tế | Soát lại khóa; nếu đúng khóa mà vẫn 401 hàng loạt, dùng công cụ chẩn đoán ở mục 3A rồi báo nhà phát triển (docs/12 Z1). Không bật `zalo_skip_signature` ở production |
| Không có tin vào, không có Error Log | URL webhook sai (`ch` sai/thiếu), tên miền chưa xác thực, HTTPS lỗi, kênh chưa bật | Mở URL bằng `curl -i` thử POST rỗng: 404 = tên kênh sai; 401 = tới được cổng |
| Tin vào `New` mãi | Worker `omni` không chạy | [01](01-cai-dat-va-ha-tang.md) mục 3 |
| Không gửi được tin ra | Token hết hạn / làm mới lỗi; `outbound_enabled` tắt; quá khung gửi (`zalo_cs_max_days`); gói OA chưa cho API (mục 4A); sandbox bật | `/app/omni-monitor` xem hạn token, bấm "Làm mới token Zalo"; không được thì "Kết nối Zalo OA" lại |
| Lỗi "Chế độ thử (sandbox) đang bật: không đổi mã cấp quyền lấy token Zalo" | Sandbox bật khi cấp quyền | Mục 4 |
| Sau khi chép site production xuống staging, token Zalo production hỏng | Làm mới token ở nơi khác | Cấp quyền lại ở production; ở staging bật sandbox ngay sau khi chép ([01](01-cai-dat-va-ha-tang.md) mục 8) |
| Tin có phí bị hỏi xác nhận mà sale không rõ | Đã quá 48 giờ | Chỉ xác nhận khi thật cần; ưu tiên kênh khác (điện thoại/email) |
| Một khách có hai hội thoại Zalo | Khách dùng hai tài khoản Zalo | Xem [06](06-chong-trung-va-lead.md) |

## 9. Đã xong khi

- [ ] Omni Channel Zalo OA đã tạo, đủ App ID, OA ID, App secret, OA secret key; tên kênh không có dấu cách.
- [ ] URL webhook (có `?ch=`) và URL callback OAuth đã khai báo ở Zalo for Developers; tên miền đã xác thực, HTTPS.
- [ ] Đã cấp quyền thành công: form kênh hiện hạn token; khối "Kênh" ở `/app/omni-monitor` hiện hạn token còn nhiều giờ.
- [ ] Nhắn thử OA từ một tài khoản thử: có Lead mới, có tin trong Hộp thư, tin vào `Done`.
- [ ] Chữ ký được chấp nhận với sự kiện thật (không có "xac_thuc_that_bai" lặp lại) - **bước này chặn việc bật cho khách thật, xem Z1**.
- [ ] Trả lời từ Hộp thư ở sandbox: tin ra có nhãn thử nghiệm; (staging, có người phụ trách đồng ý) một tin gửi thật tới tài khoản thử đã đến nơi.
- [ ] `zalo_skip_signature` tắt, `developer_mode` tắt, `sandbox_mode` tắt ở production, `outbound_enabled` bật.
- [ ] Gói OA thật đã xem trong trang quản trị OA, mục Quản lý gói; đã điền `Gói Zalo OA` và hạn mức tin ngoài 48 giờ (mục 4A); gói là Tăng trưởng hoặc Toàn diện.
- [ ] Mốc 48 giờ/7 ngày và chuyện tính phí đã được xác nhận với Zalo/hợp đồng OA.
- [ ] Lệnh kiểm trước phát hành ([02](02-cau-hinh-omni-settings.md) mục 11B) không báo `KHONG_DAT` ở `token_zalo`, `zalo_oa_package`, `zalo_skip_signature`. Lệnh chỉ đọc cấu hình đã lưu, không chứng minh chữ ký hay quyền với Zalo thật.
