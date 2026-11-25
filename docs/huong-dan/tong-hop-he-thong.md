# Tổng hợp hệ thống omni_crm

Tài liệu tóm tắt một trang cho người quản lý và quản trị: hệ thống đang có gì, dữ liệu chạy thế nào, bên trong hoạt động ra sao, và cần cấu hình gì để chạy thật. Chi tiết từng bước nằm trong các tệp `01`…`13` cùng thư mục (mục lục và "ai đọc gì" ở `00-muc-luc.md`).

> **Trạng thái trung thực (cập nhật 2026-11-03, sau v2.1):** v1, kit v2 (V2-00 … V2-08, bỏ V2-07) và kit v2.1 (V21-01 … V21-08) đã merge vào nhánh `develop`, các bước có tag git (`v2-00` … `v2-06`, `v2-08` — không có `v2-07` vì V2-07 bị bỏ qua — và `v21-01` … `v21-08`); số test cuối từng bước ghi ở `docs/v2/nhat-ky-dieu-phoi.md`. Kết luận nghiệm thu v2: **"Sẵn sàng sau khi sửa/làm"**, chưa phải "Sẵn sàng chạy chiến dịch". Chưa có lead thật từ Meta/Google/website nào đi qua hệ thống; chưa có kết quả staging nào ghi trong repo (đã có một máy chủ staging nhưng kết quả triển khai nằm ngoài các tài liệu đã commit); giao diện v2 chưa mở bằng trình duyệt; cổng chất lượng bot **chưa đo**; chữ ký Zalo (Z1) và khóa ký Stringee (S1) **chưa xác nhận** với nền tảng thật. Mọi số đo là trên **site thử, dữ liệu tổng hợp, Graph giả**. "Chưa đo" không có nghĩa là "đạt".

---

## 1. Hệ thống là gì

App Frappe **`omni_crm`** (Miyano Việt Nam) chạy trên **Frappe v15 + Frappe CRM 1.73**, không sửa code của `crm` hay `frappe`. App gom khách từ nhiều nguồn về một cổng, chống trùng, chấm điểm, cho bot AI gợi ý, rồi ghi thành **Lead/Organization/Contact trong Frappe CRM** để đội sale xử lý.

| Mảng | Có gì |
| --- | --- |
| **Kênh vào** | Email, Zalo OA, tổng đài Stringee (v1); Meta Lead Ads, Google Lead Form, Web Form website (v2) |
| **Cổng tiếp nhận** | Lưu tin ngay, trả 200 nhanh, chống trùng tin; DB không ghi được thì ghi tệp dự phòng và quét lại mỗi phút; xử lý nền qua hai hàng đợi: `omni` (chat, tổng đài, bot) và `omni_leads` (lead quảng cáo) |
| **Nhận diện khách** | Chuẩn hóa SĐT/email/mã số thuế, khớp khách cũ, nghi trùng, gộp Lead |
| **Mảng nhà thầu (B2B)** | Organization có mã số thuế, gói thầu (`Omni Bid Award`), bí danh bệnh viện, điểm phù hợp A/B/C, giai đoạn bán hàng B2B, nhập danh sách từ Excel |
| **Quảng cáo** | Chiến dịch (`Omni Campaign`), biểu mẫu (`Omni Lead Form`), quy kết nguồn (UTM, click id), SLA phản hồi lần đầu, đối soát Meta hằng giờ kèm tự kéo lại lead lỗi tạm thời, kiểm token Meta hằng ngày |
| **CSKH** | Hộp thư Omni, Chi tiết khách, Nhật ký chăm sóc (`Omni Care Log`), Dashboard theo vai trò |
| **Bot AI** | Phân tích tin, chấm điểm quan tâm, soạn trả lời theo 3 mức: Shadow → Draft → Auto, chuyển người, kho tri thức, bộ đề kiểm thử |
| **An toàn/vận hành** | Sandbox, khóa hạ tầng, giám sát và cảnh báo, xóa dữ liệu khách, sao lưu/quay lui, **lệnh kiểm cấu hình trước phát hành** (chỉ đọc) |

---

## 2. Luồng hoạt động

### 2.1 Luồng tin nhắn và cuộc gọi (email, Zalo, tổng đài)

```text
 Email          Zalo OA (webhook)        Stringee (webhook)
    \                |                        /
     +------> CỔNG TIẾP NHẬN: kiểm chữ ký, lưu Omni Inbound Message, trả 200
                       |        (kênh tắt vẫn LƯU để xử lý bù)
                       v
              HÀNG ĐỢI "omni" -> worker (gộp tin trong vài giây, khóa theo từng khách)
                       |
        nhận diện khách -> chống trùng -> Lead (Frappe CRM) + dòng thời gian
                       |                       \-> cuộc gọi nhỡ: việc gọi lại
                       v
        Bot phân tích (điền thông tin, chấm điểm)            [mặc định TẮT]
                       v
        Bot soạn trả lời:  Shadow (ngầm) -> Draft (sale duyệt) -> Auto (theo nhóm)
                       v
        HỘP THƯ OMNI: sale đọc, trả lời, duyệt nháp
                       v
        Chiều ra (Omni Outbound Message): Zalo/email...   [sandbox bật = chỉ ghi log]
```

### 2.2 Luồng lead quảng cáo (v2)

```text
 Meta Lead Ads --webhook--+
 Google Lead Form --webhook--+--> CỔNG TIẾP NHẬN --> hàng đợi omni_leads --> khớp khách/nhà thầu
 Form website --POST-------+                                        |
                                                                    v
        Lead mới (hoặc gắn vào Lead đã có, giữ người phụ trách) + quy kết chiến dịch
                          |
                          +--> giao sale (default_owner của biểu mẫu / Assignment Rule)
                          +--> đồng hồ SLA phản hồi lần đầu (giờ làm việc) --> nhắc khi quá hạn
                          +--> Dashboard, báo cáo "Theo chiến dịch"
 (Hệ thống KHÔNG tự gửi tin cho người điền form.)
```

Riêng **Meta**: webhook chỉ mang mã lead; worker gọi Graph API để lấy nội dung (sau khi đã lưu, không giữ khóa DB). Mỗi giờ có đối soát để bù lead webhook làm rơi và **tự kéo lại** các tin lỗi tạm thời (Graph chết rồi sống lại) trong 72 giờ, tối đa 3 lần mỗi tin; lỗi vĩnh viễn (token, quyền) thì báo, không kéo lại. Hằng ngày hệ thống kiểm token Page và cảnh báo khi sắp hết hạn.

### 2.3 Luồng nhà thầu (B2B)

```text
 File Excel mẫu --> Omni Prospect Import: CHẠY THỬ (chỉ đọc) -> báo cáo + "cần xác nhận" -> NHẬP THẬT
                         |
                         v
   CRM Organization (mã số thuế duy nhất) + Contact + Omni Bid Award (gói thầu)
                         |
                         v
   Điểm phù hợp (số bệnh viện, số mã hàng, giá trị...) -> hạng A/B/C
   "Lead nóng" = điểm quan tâm >= ngưỡng VÀ hạng phù hợp A/B
```

---

## 3. Cách thức hoạt động bên trong

1. **Lưu trước, xử lý sau.** Mọi tin vào được ghi vào `Omni Inbound Message` trước khi làm bất cứ việc gì khác; lỗi ở bot, kênh hay worker không làm mất tin. Chống trùng bằng `dedupe_key` (gửi lại cùng một sự kiện không tạo hai tin).
2. **Worker và khóa.** Hàng đợi `omni` chạy bằng `bench worker --queue omni`, hàng đợi `omni_leads` (lead quảng cáo) bằng `bench worker --queue omni_leads`. Mỗi khách có khóa riêng để tin của cùng một người không bị xử lý chồng; tin đến liên tiếp được gộp vài giây (debounce).
3. **Nhận diện khách (resolver).** Thứ tự khớp: mã số thuế, Contact, SĐT/email, tên miền email công ty (chỉ tin khi tin đến từ kênh Email), rồi mới đến tín hiệu trung bình theo tên. Quy tắc cốt lõi: **chỉ tự gắn khi khớp đúng một kết quả**; mơ hồ thì tạo Lead mới kèm "nghi trùng" cho người xử lý. Tên Việt phải trùng cả **tên riêng** (từ cuối). Mã số thuế khách tự khai trên kênh công khai (Web Form) chỉ là tín hiệu yếu.
4. **Bot ba mức.** Shadow: bot soạn ngầm để đo, sale không thấy. Draft: sale thấy nháp trong Hộp thư và tự duyệt. Auto: bot gửi thẳng, chỉ theo từng nhóm câu hỏi, cần **hai tầng** mở (khóa hạ tầng `omni_auto_send_allowed` + cờ của nhóm). Các chủ đề **luôn chuyển người** (phí dịch vụ, điều khoản/đàm phán hợp đồng) bị chặn **bằng mã**, không chỉ dựa vào câu lệnh cho AI.
5. **Nội dung khách là dữ liệu, không phải lệnh.** Bot không làm theo câu như "bỏ qua hướng dẫn"; mọi trường bot trích ra phải kèm trích dẫn nguyên văn từ tin, không thì bị bỏ.
6. **Gọi ra ngoài** (Zalo, Stringee, AI, Graph) luôn qua một lớp có timeout 10 giây, thử lại có giới hạn, log không chứa bí mật.
7. **Quyền riêng tư.** Contact đã "Từ chối" hoặc đã bị xóa dữ liệu bị ẩn SĐT/email/nút gọi nhắn ở tầng API. "Xóa dữ liệu khách" xóa nội dung tin, nháp, Bot Run, Care Log, thông tin Contact (băm HMAC để nhớ đã xóa).
8. **Giám sát.** Màn `/app/omni-monitor` + cảnh báo (hàng đợi kẹt, `omni_leads` có việc mà không có worker, tin nằm lâu trong hàng dự phòng webhook, token Meta sắp hết hạn, lead Meta không tự kéo lại được, không có lead quảng cáo trong N giờ làm việc, vượt trần Web Form, đồng bộ lead Facebook của CRM đang bật, gói Zalo OA chưa xác nhận...).
9. **Chốt chặn chủ đề bằng mã.** Câu của khách (và nháp của bot) có chủ đề phí dịch vụ, tỷ lệ phần trăm, báo giá, đàm phán, điều khoản, hợp đồng bị chuyển người; từ v2.1 chốt chặn nhận ra cả cách viết lách (cách từng chữ, không dấu, số thay chữ, ký hiệu xen giữa, tiếng Anh). Ba ý định **Hỏi phí, Yêu cầu đề xuất, Khiếu nại** luôn chuyển người. Bộ ca kiểm của repo chặn 46/46 ca lách đã biết và để 24/24 câu thường qua; đó không phải bằng chứng với tin thật.
10. **Ý định theo nhà thầu.** Bot gán một trong sáu ý định (Hỏi cách vận hành, Hỏi gói dịch vụ, Hỏi phí, Yêu cầu đề xuất, Khiếu nại, Khác). Câu lệnh đang chạy vẫn ghim v1 và còn trả nhãn cũ của khách lẻ; hệ thống tự đổi nhãn cũ sang mới. Câu lệnh v2 chưa ghim.

---

## 4. Cần cấu hình gì để chạy thật

Thứ tự gợi ý: hạ tầng → cấu hình chung → từng kênh → nhà thầu/quảng cáo → sale dùng thử → bot (cuối cùng).

### 4.1 Hạ tầng (quản trị hệ thống)

| # | Việc | Ghi chú |
| --- | --- | --- |
| 1 | Cài app lên site, `bench migrate` | Áp DocType, Custom Field, vai trò |
| 2 | **Worker `omni` và `omni_leads`** chạy thường trực (supervisor) | Khai báo cả hai trong `workers` của `common_site_config.json`, chạy lại `bench setup supervisor`; cấu hình sinh sẵn của bench dev **chưa có** hai worker này. Thiếu `omni_leads` thì lead quảng cáo rơi về `omni` (có cảnh báo) |
| 3 | **Số worker** | `omni_leads` **từ 2 worker** (đề xuất 3 đến 4 ở production); `omni` theo lưu lượng chat (công thức ở `01`). Đo trên site thử (Graph giả, chưa staging): có hàng đợi riêng, p95 từ lúc nhận lead đến lúc có người phụ trách ~21 giây so với ~113 giây khi dùng chung với chat cao điểm |
| 4 | **Scheduler bật** | Quét kẹt, đối soát Meta hằng giờ, cảnh báo, SLA 5 phút, tác vụ đêm |
| 5 | Redis khỏe | Redis cổng 11000 trên máy dev đang hỏng rdb; production dùng Redis sạch |
| 6 | Nginx | Khối `location` cho webhook; **bắt buộc ghi đè `X-Forwarded-For`** (không nối thêm) để giới hạn Web Form theo IP không bị né |
| 7 | HTTPS công khai | Zalo, Meta, Google, Stringee gọi vào bằng URL công khai |
| 8 | Email gửi đi mặc định, sao lưu, quy trình quay lui về `v1-final` | Thử quay lui trước ngày chạy |
| 9 | (Nên) cài `defusedxml` | Thêm lớp phòng thủ cho việc đọc tệp Excel lạ |
| 10 | Tắt `developer_mode`; không đặt `zalo_skip_signature` | Cấm ở production |

### 4.2 Cờ cấp site (`sites/<site>/site_config.json`, KHÔNG đặt ở `common_site_config.json`)

| Khóa | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `omni_ai_allow_customer_data` | vắng = chặn | Cho gửi hội thoại khách thật sang AI. **Chỉ bật khi chủ dự án chốt Q11** (hiện: không) |
| `omni_ai_unmask_pii` | vắng = che SĐT/email/MST | Bật mới gửi SĐT/email thật sang AI; mã số thuế bị che nên bot không trích được MST khi còn che |
| `omni_auto_send_allowed` | vắng = chặn | Khóa tổng cho bot **tự gửi**. Chỉ đặt sau khi Q1 chốt và có số liệu từng nhóm |
| `omni_meta_graph_version` | `v26.0` (chưa xác nhận) | Đổi nếu phiên bản Graph API của Meta khác |
| `omni_webhook_lock_wait_seconds` | 3 | Giây webhook chờ khóa DB **trước khi** chuyển tin sang tệp dự phòng (trước v2.1: chờ rồi trả 503) |
| `omni_spool_max_bytes`, `omni_spool_max_lines`, `omni_spool_expire_days` | 50 MB, 5.000 dòng, 14 ngày | Giới hạn hàng dự phòng `private/omni_spool` và thời gian giữ tin chưa chèn được |
| `omni_zalo_signature_debug_until` | vắng = tắt | Bật công cụ chẩn đoán chữ ký Zalo tối đa 2 giờ (chỉ staging) |
| `allow_cors` | — | Cần khi form website khác tên miền với CRM (không dùng `*`) |
| `omni_zalo`, `omni_ai_timeout_seconds` | — | Ghi đè hằng Zalo / thời gian chờ AI nếu cần |

### 4.3 Omni Settings (`/app/omni-settings`) — các nhóm chính

| Nhóm | Cấu hình |
| --- | --- |
| Chung | **`sandbox_mode`**: BẬT ở dev/staging; **tắt có chủ đích ở production** (ghi ai tắt, lúc nào). Thời hạn lưu payload thô, thử lại (`max_attempts`, `retry_backoff_minutes` — đặt dài hơn cho chiến dịch) |
| Giờ làm việc | Giờ làm việc, ngày lễ (dùng chung cho SLA, hạn xử lý, KPI) |
| Cảnh báo/gán | `alert_recipients`, người nhận dự phòng, `default_owner` theo biểu mẫu |
| CSKH | `response_sla_minutes` (mặc định theo docs/15: 15 phút giờ làm việc), `care_session_gap_hours`, công tắc `dashboard_enabled`, `care_log_enabled` |
| Quảng cáo | `ads_silence_hours` (cảnh báo không có lead), `webform_rate_limit` + `webform_rate_window_minutes` (mặc định 5 lần/10 phút/IP), `webform_global_rate_per_hour` (300), `webform_accept_unknown_form` (0) |
| Nhà thầu | `fit_*` (12 tham số điểm phù hợp), `fit_scoring_enabled`, `prospect_import_enabled` |
| Bot | `bot_analyze_enabled`, `bot_reply_mode` (Off/Shadow/Draft), `lead_scoring_enabled`, `daily_token_budget`, ghim `analyze_prompt_version`/`reply_prompt_version` (đang **v1**), khóa API (`ai_api_key`, kiểu Password), ngưỡng Lead nóng |
| Chuyển người | `handoff_pause_hours`, người nhận thông báo chuyển người |
| Meta và hàng dự phòng | `meta_token_warn_days` (7), `meta_token_check_disabled`, `meta_requeue_hours` (72), `meta_requeue_batch` (200), `meta_requeue_disabled`, `webhook_spool_disabled`, `zalo_tab_disabled` (tab Zalo trong trang Lead/Deal của CRM; V3-01, ADR 0016), `zalo_in_activity_enabled` (tích = tin Zalo mới hiện lại ở Hoạt động/Email; mặc định ẩn; V3-02, ADR 0017) (các ô "Tắt..." mặc định không tích, tức tính năng ĐANG chạy) |
| Quyết định phát hành | `q1_allowed`, `q11_allowed`, `gate_phan_tich_passed_at`, `gate_nhap_passed_at`: chỉ ghi nhận cho lệnh kiểm, **không** mở khóa gì; để trống/tắt khi chưa có quyết định hay kết quả đo thật |

### 4.4 Từng kênh (`Omni Channel`)

| Kênh | Cần có |
| --- | --- |
| **Email** | Email Account của hộp thư bán hàng; tạo Omni Channel loại Email, bật `enabled` |
| **Zalo OA** | OA ID, App ID/Secret (Password), OAuth để lấy token, URL webhook công khai, **khóa kiểm chữ ký** (công thức **chưa xác nhận** với Zalo; có công cụ chẩn đoán cho staging), **gói OA** (chỉ gói Tăng trưởng hoặc Toàn diện liên kết API theo trang giá Zalo; điền `zalo_oa_package` và hạn mức tin ngoài 48 giờ), nhớ khung trả lời 48 giờ/7 ngày (mốc 7 ngày chưa được tài liệu Zalo xác nhận). Lấy token lần đầu cần tắt sandbox có chủ đích hoặc nhập tay |
| **Tổng đài Stringee** | Tài khoản Stringee, URL webhook, `Omni Agent Map` (số nội bộ ↔ nhân viên), đối soát đêm. Cấu hình phía Stringee: `cau-hinh-stringee.md` |
| **Meta Lead Ads** | Meta App ở chế độ Live, webhook đăng ký trường `leadgen` cho đúng Page, App ID + App Secret + verify token + Page token (Password; **khuyến nghị token System User** không hết hạn; token thường hết hạn và hệ thống kiểm hằng ngày, cảnh báo khi sắp hết hạn hoặc thiếu quyền `leads_retrieval`; chưa thử với token thật), `Omni Lead Form` cho từng form |
| **Google Lead Form** | URL webhook và `google_key` nhập vào từng biểu mẫu trong Google Ads, gửi "Send test data" thử |
| **Web Form** | Omni Channel loại Web Form (bật), `Omni Lead Form` nền tảng Website với **mã `form`** khớp (form có mã chưa cấu hình bị bỏ qua), CORS, nhúng `omni_utm.js`. Hướng dẫn: `gan-form-website.md` |

Mỗi `Omni Lead Form` cần: `enabled = 1`, `field_map` đủ, gắn đúng `Omni Campaign`, `default_owner` (hoặc Assignment Rule). Mỗi kênh có hai công tắc: `enabled` (tắt = webhook vẫn lưu, không xử lý) và `outbound_enabled` (tắt = tin ra bị chặn). Riêng Meta/Google tắt kênh thì **không lưu gì**.

### 4.5 Dữ liệu và quy tắc cần người của Miyano cung cấp

| Việc | Ai |
| --- | --- |
| File danh sách nhà thầu theo mẫu `mau/Mau_DanhSach_NhaThau_TiemNang.xlsx` (đặt ngoài repo) | Phòng kinh doanh |
| Quy tắc phân công Lead (Q4), quy tắc liên hệ chủ động (Q12), ngày chạy và câu hỏi form (Q15) | Chủ dự án |
| Bảng ánh xạ trạng thái Lead cũ → giai đoạn B2B (đã gửi, chờ duyệt) | Trưởng sale |
| Kho tri thức B2B đã duyệt nội dung (mẫu hiện **không hoạt động**, không mục nào được nêu phí/cam kết khi chưa duyệt) | Phòng kinh doanh |
| Bảng điểm quan tâm B2B (mẫu, ngưỡng 60 là **chưa duyệt**, Q5) | Trưởng sale |
| Sáu ADR "Đề xuất": 0006, 0007, 0009, 0011, 0012, 0013 | Chủ dự án |
| Chính sách người "Từ chối liên hệ" ở gửi nháp bot/tự gửi (hiện mới chặn ở `send_reply`) | Chủ dự án |
| Q11 (cho/không cho gửi hội thoại thật sang AI), Q1 (bot tự gửi) | Chủ dự án |

---

## 5. Trạng thái bật tính năng hiện tại (mặc định phát hành)

| Tính năng | Mặc định | Bật khi |
| --- | --- | --- |
| `sandbox_mode` | **BẬT** | Tắt có chủ đích ở production |
| Kênh, chiều ra | Tắt | Sau khi cấu hình và thử từng kênh |
| Bot phân tích / chấm điểm | Tắt | Có khóa AI, Q11, bảng điểm duyệt |
| Bot trả lời | **Off** | Shadow → Draft theo `07-bot-ai.md` |
| Bot tự gửi | Tắt hai tầng | Cuối cùng, theo nhóm, sau Q1 |
| Gửi dữ liệu khách thật sang AI | Tắt | Q11 chốt |
| Ghi âm/tóm tắt cuộc gọi | **Không có** | Cần làm D3-04 (chọn nhà cung cấp chuyển giọng nói) |
| Email xác nhận tự động cho người điền form | **Không xây** | Sau khi Q12 chốt (ADR mới) |
| Kiểm token Meta, tự kéo lại lead Meta, hàng dự phòng webhook | **Bật** (chỉ bảo vệ dữ liệu) | Tắt bằng ô "Tắt..." tương ứng ở Omni Settings |
| Lệnh kiểm trước phát hành | Có sẵn, chỉ đọc | Chạy trước mỗi lần phát hành |

> Máy phát triển `omni.test` đang để bot ở `Draft` + `bot_analyze_enabled = 1` + `sandbox = 1` (di sản các phiên thử). **Bản phát hành phải là Off / 0**, và sandbox chỉ tắt có chủ đích ở production.

---

## 6. Những điều chưa kiểm chứng (đọc kỹ trước khi cam kết với khách)

- **Nền tảng thật:** chưa thử Meta, Google, website, Zalo, Stringee thật. Tên trường, chữ ký, Graph v26.0, việc Google/Zalo/Stringee có gửi lại webhook khi nhận 5xx — đều chưa xác nhận. Từ v2.1 khi DB khóa hoặc mất kết nối webhook ghi tệp dự phòng thay vì trả 503; **vẫn trả 5xx** nếu cả DB lẫn tệp dự phòng hỏng, hoặc nếu DB mất hẳn trước khi xác thực xong (G-V21-04-2). Công thức chữ ký Zalo (Z1): đã thử đối chiếu lại tài liệu Zalo ngày 2026-11-03 nhưng trang không đọc được bằng máy, vẫn **chưa xác nhận**; công cụ chẩn đoán trên OA thật chưa ai chạy. Mốc 7 ngày của khung tin Zalo không có trong trang giá Zalo. Gói OA của công ty chưa biết.
- **Giao diện:** Hộp thư, Chi tiết khách, Dashboard, form nhập Excel chưa ai mở bằng trình duyệt; `omni.css` chỉ nạp được sau `bench build`.
- **Bot:** cổng `phan_tich`, `nhap`, `doi_cau_lenh` **chưa đo** (chưa có khóa AI thật và Q11 chưa cho phép; bộ đề 237 ca B2B + 220 ca v1 đều tổng hợp, chưa được duyệt). Mọi con số 100% trong báo cáo `-fake.md` là hiện vật của client giả.
- **Tải:** đo trên site test với máy chủ Graph giả, không phải staging. 200 lead quảng cáo + 1.002 tin Zalo trong 10 phút: không mất, không trùng. Trước v2.1, nhận → có người phụ trách p95 113 giây khi chat cao điểm chạy cùng; sau khi tách hàng đợi `omni_leads` p95 ~21 giây (hai worker `omni_leads`). **Chưa đo trên staging với Graph/Redis/supervisor thật.**
- **Phục hồi:** `kill -9` worker (ADR 0007) chưa đo lại. Graph Meta chết quá ~17 phút thì tin thành `Manual`; từ v2.1 lượt đối soát hằng giờ tự kéo lại (đo trên site thử, Graph giả, thời gian nén: 25/25 lead, không phải chạy xử lý lại tay); với Graph thật thì chưa đo.
- **Chống né chốt chặn chủ đề:** v2.1 đã xử lý cách từng chữ, số thay chữ, không dấu và các cụm lành tính; còn lọt các kiểu chưa biết (đảo thứ tự chữ trong từ, chữ ngoài bảng chữ Latin; `docs/12` G-V21-07-1). Chặn nhầm chỉ dẫn tới chuyển người, chấp nhận được.
- **Dry run chuyển đổi dữ liệu:** chưa chạy trên staging/dữ liệu thật; mới chạy trên dữ liệu tổng hợp.
- **Gộp Lead không có hoàn tác:** khi không chắc hãy bấm "Không trùng".

---

## 7. Trước khi chạy chiến dịch — đường ngắn nhất

1. Dựng staging + bản sao dữ liệu; bật scheduler; khai báo `omni_leads` và chạy ≥ 2 worker `omni_leads` cùng số worker `omni` theo lưu lượng chat.
2. Chốt Q4, Q12, Q15, bảng ánh xạ trạng thái, sáu ADR, chính sách "Từ chối liên hệ".
3. Nhập danh sách nhà thầu: **chạy thử** → xem "cần xác nhận" → xử lý → **nhập thật** → so top 10 với sheet TongHop.
4. Gửi lead thử THẬT: Meta (Testing Tool), Google ("Send test data"), form website; kiểm đúng chiến dịch, đúng sale, sale nhận thông báo trong ~1 phút.
5. Cho 2–3 sale chạy kịch bản trong `docs/nghiem-thu/uat-v2.md` bằng trình duyệt thật.
6. Đi hết `truoc-ngay-chay-chien-dich.md`; mục nào cần hệ thống thật đều ghi "chưa kiểm" cho đến khi có bằng chứng. Chạy lệnh kiểm trước phát hành (`bench --site <site> execute omni_crm.ops.preflight.run --kwargs "{'target': 'staging'}"`, rồi `production`): lệnh chỉ đọc, báo mục `KHONG_DAT` phải sửa; nó không thay cho thử trên Meta/Google/Zalo thật.
7. Production: tắt `sandbox_mode` có chủ đích; `developer_mode` tắt; không đặt `omni_auto_send_allowed`; sao lưu và thử quay lui về `v1-final`.
8. Sau tuần chạy thử: `measure_campaign.py` (lưu ý đặt `exclude_prefixes` để Lead thật tên như "TDK ..." không bị loại nhầm) rồi điền cột "Đo được" trong `docs/nghiem-thu/v2.md`.

Dọn phía quản trị (từ v1): xóa user MariaDB `omni_admin`, đổi mật khẩu đã từng gửi trong chat, sửa Redis cổng 11000, dựng supervisor cho worker `omni`.

---

## 8. Bản đồ tài liệu

| Muốn biết | Đọc |
| --- | --- |
| Ai đọc gì, thứ tự triển khai | `00-muc-luc.md` |
| Cài đặt, worker, nginx, cờ site | `01-cai-dat-va-ha-tang.md` |
| Từng trường Omni Settings, bảng công tắc | `02-cau-hinh-omni-settings.md` |
| Kênh email / Zalo / Stringee | `03`, `04`, `05`, `cau-hinh-stringee.md` |
| Chống trùng, gộp, xóa dữ liệu khách | `06-chong-trung-va-lead.md` |
| Bot AI, duyệt nháp, tắt khẩn cấp | `07-bot-ai.md`, `duyet-nhap-bot.md` |
| Sale dùng Hộp thư | `08-huong-dan-nhan-vien-sale.md` |
| Vận hành hằng ngày, sự cố | `09-van-hanh-hang-ngay.md` |
| Lên sản xuất, cổng bật tính năng, bước chiến dịch | `10-quy-trinh-len-san-xuat.md` |
| Nhà thầu: nhập danh sách Excel, hạng A/B/C | `11-nha-thau-va-nhap-danh-sach.md` |
| Lead quảng cáo: Meta, Google, website, chiến dịch, token | `12-lead-quang-cao.md` |
| Tổng quan CSKH, Chi tiết khách, nhật ký chăm sóc | `13-dashboard-va-cham-soc.md` |
| Gắn form website | `gan-form-website.md` |
| Danh sách kiểm trước ngày chạy chiến dịch | `truoc-ngay-chay-chien-dich.md` |
| Kết quả nghiệm thu v2, lỗi còn mở, bảng quyết định ADR | `docs/nghiem-thu/v2.md`, `docs/nghiem-thu/uat-v2.md` |
| Khoảng cách v1 → v2, nhật ký từng bước | `docs/v2/gap-report.md`, `docs/v2/nhat-ky-dieu-phoi.md` |
| Đặc tả kỹ thuật | `docs/00` … `docs/15`, `docs/v2.1/dac-ta-v2.1.md`, `docs/adr/` |

> Lưu ý: bộ hướng dẫn `01`…`13` đã được đối chiếu với mã nguồn ngày 2026-11-03 (V21-09). Mô tả giao diện lấy từ mã, **chưa ai mở bằng trình duyệt**; mỗi tệp ghi "Chưa kiểm trên giao diện" ở chỗ liên quan.
