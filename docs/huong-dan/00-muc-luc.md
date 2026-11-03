# 00 · Mục lục và bản đồ hướng dẫn omni_crm

Bộ tài liệu hướng dẫn sử dụng và cấu hình app **`omni_crm`** (Miyano Việt Nam) cho người **không phải lập trình viên**: quản trị hệ thống, trưởng sale, nhân viên sale. `omni_crm` gom tin nhắn và cuộc gọi của khách từ **email, Zalo OA, tổng đài điện thoại**, cùng lead từ **quảng cáo Meta, Google và form website**, về một cổng tiếp nhận, chống trùng khách, cho bot AI phân tích và soạn nháp trả lời, rồi ghi thành **Lead trong Frappe CRM**. Khách mục tiêu là **nhà thầu** đã trúng gói cung cấp vật tư y tế cho bệnh viện.

> **Hiện trạng (cập nhật 2026-11-03, sau v2.1): chưa phát hành; "Sẵn sàng sau khi sửa/làm".** Mã nguồn có kênh email, Zalo OA, tổng đài, lead quảng cáo (Meta, Google, form website), mảng nhà thầu (nhập danh sách, điểm phù hợp), Tổng quan CSKH và nhật ký chăm sóc, bot AI, và các sửa v2.1 (hàng đợi riêng `omni_leads`, cảnh báo token Meta, tự kéo lại lead Meta, hàng dự phòng webhook, gói Zalo OA, chốt chặn chủ đề, lệnh kiểm trước phát hành). Phần đã đo là **trên site thử với dữ liệu tổng hợp và máy chủ Graph giả**. **Chưa đo, không được đọc là đạt:** chất lượng bot thật (chưa có khóa AI, Q11 chưa chốt), Meta/Google/website/Zalo/Stringee thật, chữ ký Zalo (Z1) và khóa ký Stringee (S1), giao diện v2 trên trình duyệt thật, sale thật dùng thử. Đọc [10-quy-trinh-len-san-xuat.md](10-quy-trinh-len-san-xuat.md) và chạy **lệnh kiểm trước phát hành** ([02](02-cau-hinh-omni-settings.md) mục 11B) trước khi bật bất cứ thứ gì cho khách thật.

Quy ước trong bộ tài liệu:

- Mỗi tệp có mục **"Sự cố thường gặp"** và checklist **"Đã xong khi"**. Số mục (ví dụ "mục 9A") được giữ ổn định; nội dung mới thêm dưới dạng mục có chữ cái.
- "**Chưa kiểm trên giao diện**" = mô tả lấy từ mã nguồn/đặc tả, chưa ai thao tác trên trình duyệt thật.
- Lệnh shell luôn ghi chạy ở đâu (`<bench-dir>` là thư mục bench, `<site>` là tên site); mọi giá trị là giá trị giả.
- Đường dẫn Desk (`/app/...`) theo quy ước Frappe.
- Mã Q/Z/S/G/R/E (ví dụ Q11, S1) là mã quyết định/giả định trong `docs/12-quyet-dinh-mo.md`.

## 1. Ai đọc gì

| Bạn là | Đọc | Mục đích |
| --- | --- | --- |
| **Quản trị hệ thống** (có quyền shell, vai trò Omni Admin) | [01](01-cai-dat-va-ha-tang.md) → [02](02-cau-hinh-omni-settings.md) → [03](03-kenh-email.md), [04](04-kenh-zalo-oa.md), [05](05-kenh-tong-dai-stringee.md) → [11](11-nha-thau-va-nhap-danh-sach.md), [12](12-lead-quang-cao.md) → [09](09-van-hanh-hang-ngay.md) → [10](10-quy-trinh-len-san-xuat.md) | Cài, cấu hình, nối kênh, nhập nhà thầu, nối lead quảng cáo, vận hành, lên sản xuất |
| **Trưởng sale / quản lý** | [13](13-dashboard-va-cham-soc.md) (Tổng quan, nhật ký chăm sóc), [11](11-nha-thau-va-nhap-danh-sach.md) mục 1, 6 (hạng nhà thầu), [12](12-lead-quang-cao.md) mục 1, 7 (chiến dịch, hạn phản hồi), [02](02-cau-hinh-omni-settings.md) (giờ làm việc, ngày lễ, bảng điểm, chuyển người), [06](06-chong-trung-va-lead.md), [07](07-bot-ai.md) (kho tri thức, báo cáo), [08](08-huong-dan-nhan-vien-sale.md), [09](09-van-hanh-hang-ngay.md) mục báo cáo | Theo dõi đội, quyết định bot, duyệt kho tri thức/bảng điểm |
| **Phụ trách kho tri thức** (vai trò Omni Knowledge Editor) | [07](07-bot-ai.md) mục 2, 3, 5, 10 | Viết/duyệt nội dung bot được phép nói, đọc báo cáo bot |
| **Nhân viên sale** | [08](08-huong-dan-nhan-vien-sale.md), [13](13-dashboard-va-cham-soc.md) mục 3, 4, [duyet-nhap-bot.md](duyet-nhap-bot.md) | Dùng Hộp thư, Chi tiết khách, ghi nhật ký chăm sóc, trả lời khách, duyệt nháp |
| **Đội web** (gắn form vào trang) | [gan-form-website.md](gan-form-website.md), [12](12-lead-quang-cao.md) mục 5 | Gửi form về Omni CRM, UTM, đồng ý liên hệ |
| **Người chạy quảng cáo** (Meta, Google) | [12](12-lead-quang-cao.md), [truoc-ngay-chay-chien-dich.md](truoc-ngay-chay-chien-dich.md) | Nối webhook, token, kiểm lead thử trước ngày chạy |
| **Chủ dự án** | Mục 3, 4 và 6 dưới đây, [10](10-quy-trinh-len-san-xuat.md) mục 5-6, [tong-hop-he-thong.md](tong-hop-he-thong.md) | Hiểu hệ thống đang có gì, chốt các quyết định đang mở |
| **Nhà phát triển** | `CLAUDE.md`, `docs/00` đến `docs/15` (đặc tả), `docs/v2.1/dac-ta-v2.1.md`, `docs/nghiem-thu/` | Bộ này không dành cho họ |

## 2. Thứ tự triển khai: cài đặt → kênh → bot → vận hành

1. **Cài đặt và hạ tầng**: [01](01-cai-dat-va-ha-tang.md) (cài app, migrate, worker `omni`, scheduler, Redis, nginx, email gửi đi, sao lưu, sandbox, cờ site_config).
2. **Cấu hình chung**: [02](02-cau-hinh-omni-settings.md) (Omni Settings: giờ làm việc, ngày lễ, cảnh báo, gán Lead, ngân sách).
3. **Kênh**, mỗi kênh thử riêng rồi mới bật: [03 Email](03-kenh-email.md) → [04 Zalo OA](04-kenh-zalo-oa.md) → [05 Tổng đài](05-kenh-tong-dai-stringee.md) (kèm [cau-hinh-stringee.md](cau-hinh-stringee.md)).
4. **Khách và Lead**: [06](06-chong-trung-va-lead.md) (nghi trùng, gộp, xóa dữ liệu).
4A. **Nhà thầu và lead quảng cáo** (nếu chạy chiến dịch): [11](11-nha-thau-va-nhap-danh-sach.md) (nhập danh sách), [12](12-lead-quang-cao.md) (Meta, Google, website), [gan-form-website.md](gan-form-website.md), rồi đi hết [truoc-ngay-chay-chien-dich.md](truoc-ngay-chay-chien-dich.md).
5. **Sale bắt đầu dùng**: [08](08-huong-dan-nhan-vien-sale.md) và [13](13-dashboard-va-cham-soc.md).
6. **Bot** (chỉ sau khi 1-5 ổn và đủ cổng): [07](07-bot-ai.md): khóa AI/Q11 → kho tri thức → nhóm câu hỏi → bảng điểm → bộ đề → phân tích → Shadow → Draft → (cuối cùng) Auto theo nhóm.
7. **Vận hành hằng ngày**: [09](09-van-hanh-hang-ngay.md).
8. **Lên sản xuất**: [10](10-quy-trinh-len-san-xuat.md) (checklist, cổng, quyết định còn mở, dọn dẹp).

Ba đợt phát hành trong đặc tả (`docs/00-tong-quan.md`): **Đợt 1** email + Zalo OA vào CRM, chống trùng, sale trả lời từ hệ thống. **Đợt 2** tổng đài (nhật ký, gọi nhỡ, việc gọi lại), bot phân tích, chấm điểm Lead, bot trả lời chạy ngầm. **Đợt 3** bot nháp cho sale duyệt, chuyển người, bot tự gửi theo nhóm; ghi âm và tóm tắt cuộc gọi **không được làm** (xem mục 4).

## 3. Sơ đồ ngắn luồng dữ liệu

```text
 Email        Zalo OA        Tổng đài        Meta Lead Ads    Google Lead Form    Form website
   |            |               |                  |                 |                |
   +------------+---------------+------------------+-----------------+----------------+
                                   |
                CỔNG TIẾP NHẬN: kiểm chữ ký/khóa, lưu ngay, trả 200, chống trùng tin
                (kênh email/Zalo/tổng đài tắt vẫn LƯU; Meta/Google tắt thì KHÔNG lưu;
                 DB không ghi được -> tệp dự phòng, quét lại mỗi phút)
                                   |
        +--------------------------+---------------------------+
        v                                                      v
 HÀNG ĐỢI "omni" (chat, tổng đài, bot)              HÀNG ĐỢI "omni_leads" (lead quảng cáo)
        |                                                      |
        +-------- nhận diện khách -> chống trùng -> Lead (Frappe CRM) / nhà thầu (Organization)
                                   |
        Bot phân tích + chấm điểm      [mặc định TẮT; lead quảng cáo KHÔNG qua bot]
                                   v
        Bot soạn trả lời:  Shadow (ngầm) -> Draft (sale duyệt) -> Auto (theo nhóm)
                                   v
   HỘP THƯ OMNI -- TỔNG QUAN CSKH -- CHI TIẾT KHÁCH / NHÀ THẦU -- NHẬT KÝ CHĂM SÓC
                                   v
        Chiều ra: Zalo (sandbox_mode bật = chỉ ghi log, không gửi thật)
        Lead quảng cáo: KHÔNG có chiều ra, hệ thống không gửi gì cho người điền form
```

Điểm cần nhớ: **tin không bao giờ mất khi kênh/bot/worker lỗi** (tin được lưu trước, xử lý bù sau; từ v2.1 webhook có thêm tệp dự phòng khi DB không ghi được, Meta có đối soát hằng giờ và tự kéo lại tin lỗi tạm thời); **mọi lời gọi ra ngoài** (Zalo, tổng đài, Meta Graph, AI) đi qua một lớp có timeout, thử lại giới hạn và không ghi bí mật vào log; **nội dung khách là dữ liệu, không phải lệnh** (bot không làm theo câu như "bỏ qua hướng dẫn", và từ v2.1 chốt chặn chủ đề phí/đàm phán không bị lách bằng cách viết lạ).

## 4. Tính năng đang TẮT mặc định, và vì sao

| Tính năng | Mặc định | Vì sao tắt | Bật khi nào |
| --- | --- | --- | --- |
| `sandbox_mode` (Omni Settings) | **BẬT** (tức "chế độ thử": chiều ra không gửi gì thật) | An toàn: tránh gửi nhầm cho khách thật, tránh làm hỏng token Zalo khi chép site | Tắt có chủ đích ở production, sau khi thử ([10](10-quy-trinh-len-san-xuat.md) mục 4) |
| Kênh (`enabled` Omni Channel) | Tắt | Chưa cấu hình/xác nhận | Sau khi làm xong tệp 03/04/05 |
| Chiều ra (`outbound_enabled`) | Tắt | Chưa muốn gửi gì cho khách | Khi sale trả lời từ Hộp thư |
| Bot phân tích (`bot_analyze_enabled`) | Tắt | Cần khóa AI, Q11 (gửi dữ liệu khách sang dịch vụ ngoài) và cổng `phan_tich` | [07](07-bot-ai.md) mục 6 |
| Chấm điểm Lead (`lead_scoring_enabled`) | Tắt | Bảng điểm do trưởng sale duyệt (Q5) chưa chốt | Sau khi có bảng điểm duyệt |
| Bot trả lời (`bot_reply_mode`) | **Off** | Rủi ro bot nói sai; cổng `nhap` **chưa đo** | Shadow rồi Draft theo [07](07-bot-ai.md) |
| Bot **tự gửi** (Auto) | Tắt hai tầng: khóa `omni_auto_send_allowed` trong site_config + cờ từng nhóm | Bot nói thẳng với khách không qua người; Q1 **chưa chốt** | Cuối cùng, theo từng nhóm, khi đủ số liệu |
| Gửi dữ liệu khách thật sang AI (`omni_ai_allow_customer_data`) | Tắt | Q11 **chưa chốt** | Khi chủ dự án chốt |
| Tạo Lead khi chỉ quan tâm OA (`zalo_create_lead_on_follow`) | Tắt | Q9 (tránh Lead rác) | Nếu chủ dự án muốn |
| Bỏ qua chữ ký Zalo (`zalo_skip_signature`) | Tắt | Chỉ cho dev; mở cửa cho tin giả | **Không bao giờ ở production** |
| `developer_mode` (site_config) | Tắt | Mở nhiều đường tắt nguy hiểm | **Không bao giờ ở production** |
| Kiểm token Meta (`meta_token_check_disabled`), tự kéo lại lead Meta (`meta_requeue_disabled`), hàng dự phòng webhook (`webhook_spool_disabled`) | **BẬT** (ô "Tắt..." không tích) | An toàn: chỉ bảo vệ dữ liệu, không gửi gì cho khách | Tắt chỉ khi có lý do ([02](02-cau-hinh-omni-settings.md) mục 9E) |
| Kênh quảng cáo Meta/Google/Web Form (`enabled`) và biểu mẫu `Omni Lead Form` | Tắt / biểu mẫu mới mặc định bật | Chưa nối nền tảng, chưa có người phụ trách | Sau khi làm [12](12-lead-quang-cao.md) |
| Nhập danh sách nhà thầu (`prospect_import_enabled`), điểm phù hợp (`fit_scoring_enabled`), Tổng quan (`dashboard_enabled`), nhật ký tự động (`care_log_enabled`) | Bật | Không gửi gì ra ngoài; chỉ ghi và hiển thị | Tắt được nếu cần ([02](02-cau-hinh-omni-settings.md) mục 9B, 9C) |
| Quyết định Q1/Q11, cổng bot (`q1_allowed`, `q11_allowed`, `gate_*`) | Tắt / trống | Chỉ ghi nhận quyết định cho lệnh kiểm; **không** mở khóa gì | Người chốt cập nhật sau khi có quyết định hoặc kết quả đo thật ([02](02-cau-hinh-omni-settings.md) mục 9A) |
| Chẩn đoán chữ ký Zalo (`omni_zalo_signature_debug_until`) | Tắt | Ghi webhook bị từ chối để đối chiếu Z1; chỉ staging | [04](04-kenh-zalo-oa.md) mục 3A |
| Email xác nhận tự động cho người điền form | **Không xây** | Chờ quyết định V2-Q12 | — |
| Ghi âm, tóm tắt cuộc gọi | **Không có tính năng** | D3-04 bị bỏ qua: chưa chọn nhà cung cấp chuyển giọng nói, chưa đo | Cần quyết định Q13/Q14 và làm tiếp |
| Tạm dừng/duyệt nháp trong Hộp thư | Chỉ hiện khi bot Draft bật | — | — |

## 5. Danh sách tài liệu

| Tệp | Nội dung |
| --- | --- |
| [01-cai-dat-va-ha-tang.md](01-cai-dat-va-ha-tang.md) | Cài app, migrate, hai hàng đợi worker (`omni`, `omni_leads`), supervisor, scheduler, Redis, nginx, sao lưu, sandbox, cờ site_config, hàng dự phòng |
| [02-cau-hinh-omni-settings.md](02-cau-hinh-omni-settings.md) | Từng trường Omni Settings và cờ site_config, bảng công tắc → hiệu ứng, lệnh kiểm trước phát hành |
| [03-kenh-email.md](03-kenh-email.md) | Nối hộp thư bán hàng |
| [04-kenh-zalo-oa.md](04-kenh-zalo-oa.md) | Zalo OA: kênh, webhook, chữ ký và công cụ chẩn đoán, token, gói OA, khung 48 giờ/7 ngày, thử bằng sandbox |
| [05-kenh-tong-dai-stringee.md](05-kenh-tong-dai-stringee.md) | Tổng đài: Omni Agent Map, gọi nhỡ, đối soát, ghi chú S1 |
| [cau-hinh-stringee.md](cau-hinh-stringee.md) | (Có sẵn) Cấu hình phía Stringee từng bước |
| [06-chong-trung-va-lead.md](06-chong-trung-va-lead.md) | Nhận diện khách (kể cả nhà thầu), nghi trùng, gộp Lead, xóa dữ liệu khách |
| [07-bot-ai.md](07-bot-ai.md) | Bật bot từng bước, nhóm câu hỏi và ý định theo nhà thầu, chốt chặn chủ đề, báo cáo, ngân sách, ngắt mạch, tắt khẩn |
| [duyet-nhap-bot.md](duyet-nhap-bot.md) | (Có sẵn) Duyệt nháp bot, ngắn gọn cho sale |
| [08-huong-dan-nhan-vien-sale.md](08-huong-dan-nhan-vien-sale.md) | Dùng Hộp thư từng bước, lead quảng cáo, hạng nhà thầu |
| [09-van-hanh-hang-ngay.md](09-van-hanh-hang-ngay.md) | Giám sát, cảnh báo (kể cả token Meta, hàng dự phòng, `omni_leads`), sổ tay sự cố, quay lui, lịch kiểm tra |
| [10-quy-trinh-len-san-xuat.md](10-quy-trinh-len-san-xuat.md) | Checklist dev → staging → production, cổng, bước chiến dịch quảng cáo, quyết định mở, dọn dẹp |
| [11-nha-thau-va-nhap-danh-sach.md](11-nha-thau-va-nhap-danh-sach.md) | Mảng nhà thầu: nhập Excel (chạy thử rồi nhập thật), "cần xác nhận", điểm phù hợp và hạng |
| [12-lead-quang-cao.md](12-lead-quang-cao.md) | Meta, Google, form website, `Omni Lead Form`, `Omni Campaign`, hạn phản hồi, báo cáo chiến dịch, token và tự phục hồi |
| [13-dashboard-va-cham-soc.md](13-dashboard-va-cham-soc.md) | Tổng quan CSKH, Chi tiết khách/nhà thầu, Nhật ký chăm sóc |
| [gan-form-website.md](gan-form-website.md) | (Có sẵn) Cho đội web: gắn form website vào Omni CRM |
| [truoc-ngay-chay-chien-dich.md](truoc-ngay-chay-chien-dich.md) | (Có sẵn) 16 mục kiểm trước ngày chạy chiến dịch, ai làm, đã tự kiểm gì |
| [tong-hop-he-thong.md](tong-hop-he-thong.md) | (Có sẵn) Tóm tắt một trang: hệ thống có gì, chạy thế nào, cần cấu hình gì |

Đặc tả kỹ thuật (cho người muốn đào sâu): `docs/01-kien-truc.md` đến `docs/15-nguon-lead-quang-cao.md`, `docs/v2.1/dac-ta-v2.1.md`, các quyết định kiến trúc `docs/adr/`; số liệu nghiệm thu: `docs/nghiem-thu/` (kết luận v2 ở `docs/nghiem-thu/v2.md`, kịch bản cho sale thử ở `docs/nghiem-thu/uat-v2.md`).

## 6. Sự cố thường gặp (tra nhanh)

| Hỏi | Xem |
| --- | --- |
| Tin vào nhưng không thành Lead | [01](01-cai-dat-va-ha-tang.md) mục 3 (worker omni) |
| Khách không nhận trả lời | `sandbox_mode` còn bật ([01](01-cai-dat-va-ha-tang.md) mục 9) |
| Zalo không có tin vào | [04](04-kenh-zalo-oa.md) mục 8 |
| Gọi nhỡ không có việc gọi lại | [05](05-kenh-tong-dai-stringee.md) mục 5 |
| Bật bot mà không thấy gì | [07](07-bot-ai.md) mục 13 |
| Hai Lead là một người | [06](06-chong-trung-va-lead.md) mục 3 |
| Cảnh báo là gì, làm gì | [09](09-van-hanh-hang-ngay.md) mục 2 |
| Tắt bot khẩn cấp | [07](07-bot-ai.md) mục 12 |
| Lead quảng cáo không về / chậm | [12](12-lead-quang-cao.md) mục 9; [01](01-cai-dat-va-ha-tang.md) mục 3 (`omni_leads`) |
| Cảnh báo token Meta, lead Meta không kéo lại được, hàng dự phòng | [12](12-lead-quang-cao.md) mục 6; [09](09-van-hanh-hang-ngay.md) mục 2 |
| Nhập danh sách nhà thầu báo "cần xác nhận" | [11](11-nha-thau-va-nhap-danh-sach.md) mục 4 |
| Cách ghi nhật ký chăm sóc, đọc Tổng quan | [13](13-dashboard-va-cham-soc.md) |
| Trước khi phát hành: máy kiểm được gì | [02](02-cau-hinh-omni-settings.md) mục 11B |

## 7. Đã xong khi (cho cả bộ tài liệu)

- [ ] Mỗi người trong mục 1 biết tệp nào dành cho mình.
- [ ] Hạ tầng xong theo [01](01-cai-dat-va-ha-tang.md) mục 13 và cấu hình theo [02](02-cau-hinh-omni-settings.md) mục 13.
- [ ] Mỗi kênh dùng thật đã qua checklist "Đã xong khi" của tệp kênh tương ứng (kể cả [12](12-lead-quang-cao.md) cho kênh quảng cáo).
- [ ] Lệnh kiểm trước phát hành không còn `KHONG_DAT` và mọi `CAN_NGUOI_KIEM` có người xác nhận.
- [ ] Mọi cổng ở [10](10-quy-trinh-len-san-xuat.md) mục 2 có kết quả ghi lại, trung thực: "chưa đo" không được đọc là "đạt".
