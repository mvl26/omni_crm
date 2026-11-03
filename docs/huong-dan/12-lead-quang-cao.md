# 12 · Lead quảng cáo: Meta, Google, form website

Dành cho: quản trị hệ thống (Omni Admin) và người chạy quảng cáo cùng làm; trưởng sale đọc mục 1, 7, 8. Nguồn kỹ thuật: `docs/15-nguon-lead-quang-cao.md`, `docs/11-van-hanh.md`, `docs/nghiem-thu/v2.md`, ADR 0013 và 0014. Bạn không cần mở các tệp đó để làm theo hướng dẫn này. Gắn form vào website có hướng dẫn riêng cho đội web: [gan-form-website.md](gan-form-website.md).

> **Hiện trạng trung thực.** **Chưa có lead thật nào từ Meta, Google hay website thật đi qua hệ thống.** Phía Omni CRM đã kiểm bằng test tự động và bằng máy chủ Graph giả trên site thử; tên trường, chữ ký và thông số của Meta và Google viết theo tài liệu của họ nhưng **chưa thử với nền tảng thật**. Mọi số đo (ví dụ thời gian nhận lead đến có người phụ trách) là trên **site thử, không phải staging**. Giao diện các form Desk bên dưới lấy từ mã nguồn: **Chưa kiểm trên giao diện**. Danh sách việc phải tự thử trên hệ thống thật nằm ở [truoc-ngay-chay-chien-dich.md](truoc-ngay-chay-chien-dich.md).

Mục lục: [1. Hệ thống làm gì](#1-hệ-thống-làm-gì-khi-có-lead-quảng-cáo) · [2. Việc phải có trước](#2-việc-phải-có-trước) · [3. Meta Lead Ads](#3-meta-lead-ads) · [4. Google Lead Form](#4-google-lead-form) · [5. Form website](#5-form-website) · [6. Token Meta và tự phục hồi](#6-token-meta-kéo-lại-lead-và-hàng-dự-phòng) · [7. Biểu mẫu, chiến dịch, SLA, báo cáo](#7-biểu-mẫu-chiến-dịch-hạn-phản-hồi-và-báo-cáo) · [8. Thử và giám sát](#8-thử-lead-và-giám-sát) · [9. Sự cố thường gặp](#9-sự-cố-thường-gặp) · [10. Đã xong khi](#10-đã-xong-khi)

## 1. Hệ thống làm gì khi có lead quảng cáo

Lead quảng cáo đi vào cùng cổng tiếp nhận như tin nhắn nhưng chạy ở **hàng đợi riêng `omni_leads`** để không phải chờ sau tin chat. Một lead đi qua các bước:

1. **Nhận và lưu ngay.** Meta, Google hoặc website gọi vào địa chỉ của CRM (webhook). Hệ thống kiểm chữ ký hoặc khóa, lưu bản ghi, trả "đã nhận". Riêng Meta, webhook chỉ mang mã lead; nội dung lead được lấy từ Graph API của Meta **sau khi đã lưu**.
2. **Nhận ra khách.** Khớp theo SĐT, email, mã số thuế; chỉ tự gắn khi khớp đúng một bản ghi, mơ hồ thì tạo Lead mới kèm "nghi trùng" ([06](06-chong-trung-va-lead.md)). Mã số thuế khách tự điền trên form là tín hiệu **yếu**: không tự gắn vào nhà thầu đã có (chỉ ghi chú cho sale kiểm).
3. **Tạo Lead hoặc gắn vào Lead đang mở**, giữ người phụ trách cũ; ghi chiến dịch, nguồn, UTM, mã click, trang đích.
4. **Giao sale ngay** theo `default_owner` của biểu mẫu hoặc Assignment Rule của biểu mẫu; gửi thông báo "Lead mới từ quảng cáo: <tên>".
5. **Đồng hồ phản hồi** (giờ làm việc), nhắc khi quá hạn (mục 7).
6. **Lên Tổng quan** và báo cáo "Theo chiến dịch" ([13](13-dashboard-va-cham-soc.md)).

Điều hệ thống **không** làm: không gửi gì cho người điền form (không email, không Zalo xác nhận tự động); không đưa lead quảng cáo qua bot; không có chức năng gửi hàng loạt. Lead thử (Meta "Testing Tool", Google "Send test data") được đánh dấu **thử nghiệm**: không giao sale, không tính vào báo cáo.

Số đo (site thử, Graph giả, kịch bản 200 lead trong 10 phút cùng lúc với chat cao điểm): thời gian từ lúc nhận đến lúc có người phụ trách ở mức p95 khoảng 21 giây khi có hàng đợi `omni_leads`, so với khoảng 113 giây khi lead và chat chung hàng đợi. **Chưa đo trên staging.**

## 2. Việc phải có trước

Chỉ làm khi đã xong [01](01-cai-dat-va-ha-tang.md) và [02](02-cau-hinh-omni-settings.md):

- [ ] Worker `omni_leads` đã khai báo và đang chạy, **từ 2 worker** (đề xuất 3 đến 4 ở production). Thiếu thì lead chạy chung hàng `omni` và chậm khi chat đông ([01](01-cai-dat-va-ha-tang.md) mục 3).
- [ ] **Scheduler bật**: đối soát Meta hằng giờ, kiểm token, nhắc quá hạn, quét hàng dự phòng đều là tác vụ định kỳ.
- [ ] Domain HTTPS công khai; nginx ghi đè `X-Forwarded-For` để giới hạn form theo IP đúng ([01](01-cai-dat-va-ha-tang.md) mục 6).
- [ ] `business_hours`, `holidays` đúng lịch chiến dịch ([02](02-cau-hinh-omni-settings.md) mục 4).
- [ ] `alert_recipients` đã điền để cảnh báo đến người ([02](02-cau-hinh-omni-settings.md) mục 5).
- [ ] Quy tắc phân công sale đã chốt (quyết định Q4): hoặc `default_owner` cho từng biểu mẫu, hoặc một Assignment Rule. Chưa chốt thì lead vẫn vào nhưng chỉ giao được cho người mặc định.
- [ ] Ngày chạy và danh sách câu hỏi trên form đã chốt (V2-Q15), có ô **đồng ý để Miyano liên hệ** không đánh dấu sẵn.
- [ ] `sandbox_mode` đúng ý. Lead quảng cáo **không bị sandbox chặn** vì không có chiều ra; sandbox chỉ chặn việc gửi ra ngoài.
- [ ] Nhớ: Lead Sync Source (đồng bộ lead Facebook có sẵn của app CRM) phải **tắt**; lead Meta chỉ nên vào qua kênh Meta Lead Ads của Omni, nếu không bị tạo trùng và mất quy kết chiến dịch (có cảnh báo riêng khi nó đang bật).

## 3. Meta Lead Ads

Phía Meta (theo tài liệu Meta, **chưa thử với tài khoản thật**; người có quyền quản trị Meta Business làm):

1. Meta App ở chế độ **Live**, có sản phẩm **Webhooks**, đăng ký đối tượng **Page**, trường **`leadgen`** cho đúng Page.
2. Quyền cần: `leads_retrieval`, `pages_manage_metadata`, `pages_show_list`, `pages_read_engagement`, `ads_management` (và `pages_manage_ads` khi đọc lead). Leads Access Manager của Page có thể chặn truy cập: kiểm khi cấu hình.
3. **Token Page: khuyến nghị token của System User** (Business Settings > Users > System users, gán Page và ứng dụng, cấp các quyền trên). Token này **không tự hết hạn**, khỏi phải gia hạn. Token người dùng thường hết hạn (khoảng 60 ngày) và chết khi người đó đổi mật khẩu hoặc rời Page.

Phía CRM (Omni Admin):

4. Mở **Desk > `/app/omni-channel` > Thêm mới**, `Loại kênh` = **Meta Lead Ads**, đặt `Tên kênh` ngắn, không dấu cách (dùng làm tham số `ch`). Điền:

| Trường | Điền |
| --- | --- |
| `meta_page_id` | ID Page |
| `meta_app_id` | ID ứng dụng Meta (cần để kiểm token tự động, mục 6) |
| `meta_app_secret` | App Secret (mật khẩu): dùng kiểm chữ ký webhook và kiểm token |
| `meta_verify_token` | Chuỗi bí mật bạn tự đặt, dán đúng vào ô Verify Token bên Meta |
| `meta_page_access_token` | Token Page (mật khẩu) |
| `enabled` | Tích **sau cùng** |

5. Khai báo webhook bên Meta: URL

```text
https://<crm>/api/method/omni_crm.api.webhooks.meta_leads?ch=<tên kênh Meta>
```

   Meta gửi yêu cầu xác minh (GET); hệ thống trả lại `hub.challenge` nếu verify token đúng, trả 403 nếu sai. Mỗi sự kiện sau đó (POST) phải có chữ ký đúng, sai thì bị từ chối (401).
6. Tạo `Omni Lead Form` cho từng biểu mẫu (mục 7). Biểu mẫu Meta chưa khai báo vẫn chạy bằng ánh xạ mặc định, nhưng có thể sót ô tùy chỉnh.
7. Phiên bản Graph API mặc định `v26.0` (**chưa xác nhận**): đổi bằng khóa site `omni_meta_graph_version` nếu cần.
8. Gửi lead thử bằng **Lead Ads Testing Tool** của Meta; kiểm Lead xuất hiện, đúng chiến dịch, đúng người, và có đánh dấu thử.

**Kênh Meta tắt (`enabled` bỏ tích)**: webhook trả 200, ghi log và **không lưu gì**; đối soát hằng giờ bỏ qua kênh. Khác với Email, Zalo, tổng đài vốn vẫn lưu khi tắt. Muốn tạm dừng mà không mất lead, tắt **biểu mẫu** (`Omni Lead Form.enabled`) thay vì tắt kênh: tin vẫn được lưu, chỉ không tạo Lead.

## 4. Google Lead Form

1. Trong Google Ads, với **từng** biểu mẫu khách hàng tiềm năng: Asset > Lead form > Lead delivery > Webhook:
   - URL: `https://<crm>/api/method/omni_crm.api.webhooks.google_leads?ch=<tên kênh Google>`
   - Khóa (key): chuỗi bí mật riêng cho biểu mẫu đó.
2. Trong CRM: tạo Omni Channel loại **Google Lead Form** (bật `enabled` sau cùng). Mỗi biểu mẫu Google là một `Omni Lead Form` với `Nền tảng` = Google, `Mã biểu mẫu trên nền tảng` = mã biểu mẫu Google, và `Khóa bí mật (google_key)` = đúng khóa đã nhập ở Google. Khóa sai hoặc thuộc biểu mẫu khác bị từ chối (401).
3. Câu hỏi tùy chỉnh (tên công ty, mã số thuế, số bệnh viện...) đến dưới mã cột riêng của Google: khai báo ở bảng ánh xạ của `Omni Lead Form` (mục 7).
4. Bấm **"Send test data"** của Google: lead thử phải xuất hiện, có đánh dấu thử.
5. Google không đảm bảo gửi đúng một lần: hệ thống chống trùng theo mã lead. Chưa xác nhận Google có gửi lại khi CRM trả lỗi 5xx; vì vậy hàng dự phòng (mục 6) quan trọng.
6. **Nhập bù từ CSV** (khi webhook sót): tải CSV lead từ giao diện Google Ads, rồi chạy trong `<bench-dir>` (chạy thử trước bằng `'dry_run': True`):

```bash
bench --site <site> execute omni_crm.channels.google_leads.csv_import.run --kwargs "{'channel': '<tên kênh Google>', 'file': '/duong/dan/leads.csv', 'form_id': '<mã biểu mẫu>', 'dry_run': True}"
```

   Lệnh in số dòng, tạo mới, trùng, lỗi. Bỏ `'dry_run': True` để nhập thật.

## 5. Form website

Chi tiết kỹ thuật cho đội web ở [gan-form-website.md](gan-form-website.md). Phía CRM (Omni Admin):

1. Tạo Omni Channel loại **Web Form** (bật `enabled`). Kênh Web Form tắt trả 404.
2. Tạo `Omni Lead Form` với `Nền tảng` = Website, `Mã biểu mẫu trên nền tảng` = đúng giá trị ô `form` mà trang gửi. **Form có mã chưa khai báo bị bỏ qua** (trang vẫn thấy "thành công" giả) trừ khi bật `webform_accept_unknown_form`.
3. Địa chỉ nhận: `POST https://<crm>/api/method/omni_crm.api.webhooks.web_form?ch=<tên kênh Web Form>`.
4. Chống spam có sẵn: ô bẫy bot, giới hạn 5 lần/10 phút mỗi IP và 300 lần/giờ cả kênh (chỉnh ở Omni Settings, [02](02-cau-hinh-omni-settings.md) mục 9D), quá thì 429; chống bot Turnstile hoặc reCAPTCHA là tùy chọn (tắt mặc định).
5. Ô **đồng ý liên hệ không đánh dấu sẵn**; hệ thống chỉ ghi đồng ý lên dòng thời gian của Lead, không nâng đồng ý của Contact đã có.
6. Nếu website khác tên miền với CRM: cấu hình CORS của site (`allow_cors`) cho đúng tên miền website, không dùng `*`.
7. Script `omni_utm.js` lưu UTM, `gclid`, `fbclid`, trang đích, trang giới thiệu để không mất khi khách chuyển trang.

## 6. Token Meta, kéo lại lead và hàng dự phòng

Ba cơ chế tự bảo vệ để lead không bị mất vì lỗi tạm thời. Bạn cần biết chúng làm gì và khi nào phải ra tay.

### 6.1 Cảnh báo token Meta (V21-02)

- Mỗi 6 giờ có một tác vụ chạy; mỗi kênh Meta được kiểm khoảng một lần mỗi ngày bằng cách hỏi Meta (`debug_token`) về token Page. Điều kiện: kênh có `meta_app_id`, `meta_app_secret` và token Page.
- **Cảnh báo "Token Meta cần xử lý"** (thư tới `alert_recipients` và thông báo cho Omni Admin) khi: token không hợp lệ; token hoặc quyền dữ liệu còn không quá `meta_token_warn_days` ngày (mặc định 7); hoặc thiếu quyền `leads_retrieval`. Token System User không hết hạn thì không cảnh báo hết hạn, nhưng vẫn kiểm hợp lệ và quyền.
- Kết quả hiện ở nhóm "Meta: kết quả kiểm token Page" của kênh (chỉ đọc) và ở dòng **Hạn token Meta** trong `/app/omni-monitor` cạnh hạn token Zalo.
- **Việc cần làm khi có cảnh báo:** cấp token mới (nên dùng System User), dán vào `meta_page_access_token`, lưu. Lần kiểm kế tiếp sẽ xóa cảnh báo.
- Tắt kiểm: tích `meta_token_check_disabled`. Lỗi mạng tạm thời của Meta khi kiểm không sinh cảnh báo giả.
- **Chưa đo với token Page thật.** Nếu cảnh báo này không hiện dù token sắp hết hạn, báo nhà phát triển.

### 6.2 Tự kéo lại lead Meta (V21-03)

- Khi Graph API lỗi lâu hơn số lần thử lại (cỡ vài chục phút), tin lead chuyển sang trạng thái **Manual**. Trước đây phải bấm xử lý lại bằng tay. Nay **mỗi giờ**, lượt đối soát Meta tự bù lead sót, rồi đưa các tin Manual có lỗi **tạm thời** (mạng, hết thời gian chờ, lỗi 5xx của Meta, giới hạn tần suất) trong **72 giờ** qua về hàng đợi, tối đa 200 tin mỗi lượt mỗi kênh, mỗi tin tối đa **3 lần**.
- Lỗi **vĩnh viễn** (token sai hoặc hết hạn, thiếu quyền `leads_retrieval`, lead đã bị xóa, dữ liệu lỗi) **không** được kéo lại. Thay vào đó có **một cảnh báo gộp mỗi lượt**: "Có lead Meta không tự kéo lại được". Việc cần làm: sửa token hoặc quyền, rồi vào `/app/omni-monitor` chọn các tin trong khối **Tin Manual** và bấm **Xử lý lại**.
- Thẻ **Kéo lại lead Meta** trong `/app/omni-monitor` cho biết lượt đối soát gần nhất tạo mới và kéo lại bao nhiêu, tổng số lần đã kéo lại, bao nhiêu tin Manual lỗi tạm thời (đang đợi), bao nhiêu lỗi vĩnh viễn (cần người), bao nhiêu tin đã dừng sau 3 lần.
- Nếu chính lượt đối soát cũng gặp lỗi tạm thời khi hỏi Meta (Graph vẫn chưa sống lại) thì **bỏ qua** bước kéo lại để không đốt lượt.
- Tắt: tích `meta_requeue_disabled` (đối soát vẫn bù lead sót). Chỉnh: `meta_requeue_hours`, `meta_requeue_batch`.
- Có thể chạy tay một lượt đối soát: `bench --site <site> execute omni_crm.channels.meta_leads.reconcile.run`.
- Đo (site thử, Graph giả, kịch bản nén thời gian, chưa staging): 20 tin Manual do Graph chết được kéo lại hết ở lượt đối soát kế tiếp, tổng 25 lead trên 25 lead gửi, không phải chạy xử lý lại bằng tay.

### 6.3 Hàng dự phòng webhook (V21-04)

- Webhook chỉ cần chèn một bản ghi. Nguyên nhân từng làm webhook chờ lâu là bộ đếm đặt tên bản ghi; đã đổi cách đặt tên. Nếu DB vẫn bị khóa, hết thời gian chờ hoặc mất kết nối, tin **đã xác thực** được ghi vào **tệp dự phòng** `private/omni_spool` trong thư mục site và webhook vẫn trả "đã nhận". Tác vụ chạy **mỗi phút** kiểm lại chữ ký và chèn tin vào DB đúng một lần (chống trùng như cũ).
- Chỉ khi **cả DB lẫn tệp dự phòng** đều không ghi được webhook mới trả lỗi 5xx và có cảnh báo "Webhook từng không lưu được tin". Tắt hàng dự phòng bằng `webhook_spool_disabled` (quay về trả 503 như trước).
- Trong `/app/omni-monitor` thẻ **Hàng dự phòng webhook** chỉ hiện khi có tin hoặc từng hỏng. Cảnh báo "Tin nằm lâu trong hàng dự phòng webhook" khi có tin chờ quá 10 phút: kiểm scheduler, DB; chạy tay `bench --site <site> execute omni_crm.ingest.spool.drain`.
- Tệp dự phòng chứa dữ liệu cá nhân chưa vào DB: thư mục chỉ chủ sở hữu đọc được, tin chưa chèn được bị xóa sau 14 ngày. Yêu cầu "xóa dữ liệu khách" **chưa phủ** hàng dự phòng (giới hạn đã biết).
- Đo (site thử, Graph giả): không còn phản hồi 503 do chờ khóa, p95 phản hồi webhook khoảng 21 đến 30 mili giây. Giới hạn: nếu DB mất hẳn **trước** khi xác thực xong thì vẫn trả 5xx.

## 7. Biểu mẫu, chiến dịch, hạn phản hồi và báo cáo

### 7.1 Omni Lead Form (chỉ Omni Admin tạo và sửa)

Mở `/app/omni-lead-form`. Mỗi biểu mẫu là một dòng:

| Trường (nhãn) | Tên kỹ thuật | Ý nghĩa |
| --- | --- | --- |
| Nền tảng | `platform` | Meta, Google hoặc Website |
| Mã biểu mẫu trên nền tảng | `external_form_id` | Mã form bên Meta/Google, hoặc giá trị ô `form` của trang web. Duy nhất theo nền tảng |
| Tên biểu mẫu | `form_name` | Tên gọi cho dễ nhận |
| Bật | `enabled` | Mặc định bật. Tắt = biểu mẫu chỉ lưu tin, không tạo Lead |
| Chiến dịch mặc định | `campaign` | Dùng khi nền tảng không báo chiến dịch |
| Người phụ trách mặc định | `default_owner` | Nếu đặt, **ghi đè** quy tắc phân công: lead giao thẳng người này |
| Assignment Rule áp dụng | `assignment_rule` | Tên một Assignment Rule của CRM, dùng khi không có người phụ trách mặc định |
| Khóa bí mật (google_key) | `secret` | Chỉ Google: khóa khớp với khóa nhập bên Google Ads (mật khẩu) |
| Ánh xạ trường | `field_map` | Bảng "Khóa nguồn" (`source_key`) → "Đích" (`target`) |

Đích hợp lệ của ánh xạ: `full_name`, `phone`, `email`, `company_name`, `tax_id`, `job_title`, `province`, `hospital_count_text`, `message`, `consent_contact`, `consent_marketing`, hoặc `custom:<tên>`. Ô không ánh xạ được **vẫn được lưu nguyên văn** và hiện trên dòng thời gian của Lead. Mỗi biểu mẫu thật nên gửi một lead thử rồi đọc dòng thời gian để thấy ô nào chưa ánh xạ.

**Mỗi biểu mẫu nên có `default_owner` hoặc `assignment_rule`**; lệnh kiểm trước phát hành ([02](02-cau-hinh-omni-settings.md) mục 11B, mục `lead_form`) báo không đạt nếu một kênh quảng cáo bật mà không có biểu mẫu bật có người phụ trách.

### 7.2 Omni Campaign

Mở `/app/omni-campaign` (Omni Admin tạo và sửa; Sales User chỉ đọc). Chiến dịch **tự được tạo** khi lead Meta hoặc Google mang mã chiến dịch, trạng thái **Đang chạy**; lead website khớp chiến dịch theo `utm_campaign`. Bạn bổ sung:

| Trường | Ghi chú |
| --- | --- |
| `campaign_name`, `platform` | Tên và nền tảng (Meta, Google, Website, Liên hệ chủ động, Đa kênh) |
| `status` | Nháp, Đang chạy, Tạm dừng, Kết thúc. **Cảnh báo "Không có lead quảng cáo" chỉ chạy khi có chiến dịch Đang chạy**: đặt Kết thúc khi chiến dịch xong |
| `utm_campaign` | Để lead từ website tự khớp chiến dịch |
| `budget`, `spend` | Ngân sách dự kiến và chi phí thực tế (**nhập tay**); dùng tính chi phí mỗi lead hợp lệ. Chưa nối API chi phí của nền tảng |

Mỗi Lead giữ **chiến dịch đầu tiên** đưa khách tới và **chiến dịch gần nhất**. Khách quay lại qua chiến dịch khác: không đổi chiến dịch đầu, cập nhật chiến dịch gần nhất, ghi một dòng "Quay lại qua chiến dịch ...".

### 7.3 Hạn phản hồi (SLA)

- Đồng hồ chạy từ lúc lead vào, **chỉ đếm giờ làm việc** và ngày làm việc của Omni Settings. Ngưỡng `response_sla_minutes` (mặc định 15 phút làm việc).
- "Phản hồi lần đầu" được ghi khi có: tin ra đầu tiên, cuộc gọi đi được nghe máy, hoặc nhật ký chăm sóc đầu tiên. Tin của sandbox không tính.
- Quá hạn: **nhắc người được giao một lần**; quá **2 lần** ngưỡng: **báo trưởng nhóm một lần**. Chỉ nhắc Lead mới (trong 3 ngày) và không phải Lead thử. Nhắc hoạt động khi `dashboard_enabled` bật. Ví dụ với giờ làm 08:00 đến 17:30 thứ Hai đến thứ Sáu, 08:00 đến 12:00 thứ Bảy, ngưỡng 15 phút: lead vào 16:50 thứ Sáu hạn 17:05 thứ Sáu; vào 17:20 thứ Sáu hạn 08:05 thứ Bảy; vào 11:55 thứ Bảy hạn 08:10 thứ Hai.

### 7.4 Báo cáo chiến dịch

Khối **Theo chiến dịch** của trang **Tổng quan CSKH** ([13](13-dashboard-va-cham-soc.md)): với mỗi chiến dịch hoặc nguồn có số Lead, số Lead hợp lệ (có SĐT hoặc email, không thử, không trùng), phần trăm phản hồi trong hạn, thời gian phản hồi lần đầu trung vị (phút), số nhà thầu hạng A/B, số đã tới "Đã trao đổi nhu cầu" trở lên, số Deal, chi phí và chi phí mỗi Lead hợp lệ. Chi phí tính trên cả chiến dịch chứ không theo kỳ chọn; Lead thử không được tính. Sau tuần chạy thử, đo bằng lệnh chỉ đọc (chạy trong `<bench-dir>`; tham số `platform_reported` là số lead mà Meta/Google tự báo, để đối chiếu):

```bash
bench --site <site> execute omni_crm.tests.acceptance.measure_campaign.run --kwargs '{"start": "<đầu tuần>", "end": "<cuối tuần>", "platform_reported": {"Meta": <số Meta báo>, "Google": <số Google báo>}}'
```

## 8. Thử lead và giám sát

**Thử lead (làm trên staging hoặc production trước ngày chạy, với người phụ trách đồng ý):** gửi 2 lead thử Meta (Testing Tool), 2 lead thử Google ("Send test data"), 2 lead từ form website (một có UTM). Kiểm với mỗi lead: Lead xuất hiện, đúng chiến dịch, đúng sale, sale nhận thông báo trong khoảng 1 phút, lead thử có đánh dấu và không tính báo cáo. Thời gian tới thông báo trên môi trường thật **chưa đo**.

**Giám sát hằng ngày khi có chiến dịch:** `/app/omni-monitor` (hạn token Meta, thẻ Kéo lại lead Meta, hàng dự phòng); Tổng quan CSKH (lead mới theo chiến dịch). Các cảnh báo liên quan và việc đầu tiên cần làm: [09](09-van-hanh-hang-ngay.md) mục 2 ("Không có lead quảng cáo", "Token Meta cần xử lý", "Có lead Meta không tự kéo lại được", "Tin nằm lâu trong hàng dự phòng webhook", "Hàng đợi omni_leads có việc nhưng không có worker", "Web Form chạm trần toàn kênh").

## 9. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Meta báo "xác minh thất bại" khi lưu webhook | Sai `ch`, sai `meta_verify_token`, kênh không phải loại Meta Lead Ads | Soát tên kênh trong URL và verify token |
| Webhook Meta 401 | Sai `meta_app_secret` | Nhập lại App Secret đúng ứng dụng |
| Có chiến dịch chạy nhưng không có lead; cảnh báo "Không có lead quảng cáo" | Webhook chưa đăng ký đúng Page, ứng dụng chưa Live, token chết, biểu mẫu bị tắt, kênh bị tắt | Kiểm theo thứ tự: kênh `enabled`, biểu mẫu `enabled`, token (mục 6.1), cấu hình bên Meta/Google |
| Lead vào nhưng không có người phụ trách | Biểu mẫu không có `default_owner` hoặc `assignment_rule` | Điền một trong hai; lead đã tạo giao tay |
| Lead nằm `New` mãi | Không có worker `omni_leads` (hoặc `omni`) | [01](01-cai-dat-va-ha-tang.md) mục 3; cảnh báo "Hàng đợi omni_leads có việc nhưng không có worker" |
| Lead website không thấy, trang vẫn báo thành công | Mã `form` chưa khai báo `Omni Lead Form`, hoặc ô bẫy bị điền | Mục 5 bước 2; hoặc bật `webform_accept_unknown_form` tạm |
| Form website báo 429 | Vượt giới hạn theo IP, hoặc nginx không ghi đè `X-Forwarded-For` nên mọi người cùng một IP | Kiểm nginx ([01](01-cai-dat-va-ha-tang.md) mục 6) |
| Lead Meta tạo thiếu thông tin | Graph lỗi tạm thời, tin Manual | Mục 6.2: chờ lượt đối soát kế tiếp; xem thẻ Kéo lại lead Meta |
| Lead Meta lỗi vĩnh viễn | Token hoặc quyền sai | Mục 6.1, rồi bấm **Xử lý lại** |
| Có hai Lead cho một người | Khớp mơ hồ | [06](06-chong-trung-va-lead.md) |
| Lead bị tạo trùng bởi CRM | Lead Sync Source của CRM đang bật | Tắt (có cảnh báo riêng) |
| Lead thử hiện lẫn trong báo cáo | Lead không mang cờ thử (ví dụ nhập tay) | Báo nhà phát triển; Lead thử tự đánh dấu chỉ khi nền tảng báo thử |
| Cảnh báo "Web Form chạm trần toàn kênh" | Lượng gửi vượt `webform_global_rate_per_hour`: có thể bị tấn công | Xem nguồn gửi; chỉ nâng trần khi chắc là lưu lượng thật |

## 10. Đã xong khi

- [ ] Worker `omni_leads` ≥ 2 chạy; scheduler bật; `alert_recipients` đã điền.
- [ ] Kênh Meta, Google, Web Form (cái nào dùng) đã tạo, webhook đã khai báo bên nền tảng, `enabled` bật sau cùng.
- [ ] Mỗi biểu mẫu thật có `Omni Lead Form` bật, ánh xạ đủ, có `default_owner` hoặc `assignment_rule`, gắn đúng chiến dịch.
- [ ] Token Meta là token System User (hoặc đã ghi ngày hết hạn và đặt nhắc); `meta_app_id` đã điền; thẻ "Hạn token Meta" ở `/app/omni-monitor` hiện đã kiểm.
- [ ] Gửi lead thử THẬT từ Meta, Google và form website: Lead đúng chiến dịch, đúng sale, thông báo đến trong khoảng 1 phút, lead thử không vào báo cáo. (Bước này chưa ai làm; ghi ngày và tên người làm.)
- [ ] Đã thử tình huống lỗi trên staging: ví dụ tắt worker 10 phút xem cảnh báo, hoặc chặn Graph xem tin Manual được kéo lại.
- [ ] Đội sale đã đọc [13](13-dashboard-va-cham-soc.md) và biết hệ thống không tự gửi gì cho người điền form.
- [ ] Lệnh kiểm trước phát hành với `production` không còn `KHONG_DAT` ([02](02-cau-hinh-omni-settings.md) mục 11B).
