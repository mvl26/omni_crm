# 00 · Mục lục và bản đồ hướng dẫn omni_crm

Bộ tài liệu hướng dẫn sử dụng và cấu hình app **`omni_crm`** (Miyano Việt Nam) cho người **không phải lập trình viên**: quản trị hệ thống, trưởng sale, nhân viên sale. `omni_crm` gom tin nhắn và cuộc gọi của khách từ **email, Zalo OA, tổng đài điện thoại** về một cổng tiếp nhận, chống trùng khách, cho bot AI phân tích và soạn nháp trả lời, rồi ghi thành **Lead trong Frappe CRM**.

> **Hiện trạng (nghiệm thu Đợt 3, 2026-10-30): "Chưa phát hành".** Phần đo được trên máy phát triển đều tốt; còn **chưa đo được gì** về chất lượng bot thật, Zalo thật, Stringee thật, sale thật. Đọc [10-quy-trinh-len-san-xuat.md](10-quy-trinh-len-san-xuat.md) trước khi bật bất cứ thứ gì cho khách thật.

Quy ước trong bộ tài liệu:

- Mỗi tệp có mục **"Sự cố thường gặp"** và checklist **"Đã xong khi"**.
- "**Chưa kiểm trên giao diện**" = mô tả lấy từ mã nguồn/đặc tả, chưa ai thao tác trên trình duyệt thật.
- Lệnh shell luôn ghi chạy ở đâu (`<bench-dir>` là thư mục bench, `<site>` là tên site); mọi giá trị là giá trị giả.
- Đường dẫn Desk (`/app/...`) theo quy ước Frappe.
- Mã Q/Z/S/G/R/E (ví dụ Q11, S1) là mã quyết định/giả định trong `docs/12-quyet-dinh-mo.md`.

## 1. Ai đọc gì

| Bạn là | Đọc | Mục đích |
| --- | --- | --- |
| **Quản trị hệ thống** (có quyền shell, vai trò Omni Admin) | [01](01-cai-dat-va-ha-tang.md) → [02](02-cau-hinh-omni-settings.md) → [03](03-kenh-email.md), [04](04-kenh-zalo-oa.md), [05](05-kenh-tong-dai-stringee.md) → [09](09-van-hanh-hang-ngay.md) → [10](10-quy-trinh-len-san-xuat.md) | Cài, cấu hình, nối kênh, vận hành, lên sản xuất |
| **Trưởng sale / quản lý** | [02](02-cau-hinh-omni-settings.md) (giờ làm việc, ngày lễ, bảng điểm, chuyển người), [06](06-chong-trung-va-lead.md), [07](07-bot-ai.md) (kho tri thức, báo cáo), [08](08-huong-dan-nhan-vien-sale.md), [09](09-van-hanh-hang-ngay.md) mục báo cáo | Quyết định bot, duyệt kho tri thức/bảng điểm, theo dõi |
| **Phụ trách kho tri thức** (vai trò Omni Knowledge Editor) | [07](07-bot-ai.md) mục 2, 3, 5, 10 | Viết/duyệt nội dung bot được phép nói, đọc báo cáo bot |
| **Nhân viên sale** | [08](08-huong-dan-nhan-vien-sale.md), [duyet-nhap-bot.md](duyet-nhap-bot.md) | Dùng Hộp thư, trả lời khách, duyệt nháp |
| **Chủ dự án** | Mục 3 và 4 dưới đây, [10](10-quy-trinh-len-san-xuat.md) mục 5-6 | Chốt các quyết định đang mở |
| **Nhà phát triển** | `CLAUDE.md`, `docs/00` đến `docs/12` (đặc tả), `docs/nghiem-thu/` | Bộ này không dành cho họ |

## 2. Thứ tự triển khai: cài đặt → kênh → bot → vận hành

1. **Cài đặt và hạ tầng**: [01](01-cai-dat-va-ha-tang.md) (cài app, migrate, worker `omni`, scheduler, Redis, nginx, email gửi đi, sao lưu, sandbox, cờ site_config).
2. **Cấu hình chung**: [02](02-cau-hinh-omni-settings.md) (Omni Settings: giờ làm việc, ngày lễ, cảnh báo, gán Lead, ngân sách).
3. **Kênh**, mỗi kênh thử riêng rồi mới bật: [03 Email](03-kenh-email.md) → [04 Zalo OA](04-kenh-zalo-oa.md) → [05 Tổng đài](05-kenh-tong-dai-stringee.md) (kèm [cau-hinh-stringee.md](cau-hinh-stringee.md)).
4. **Khách và Lead**: [06](06-chong-trung-va-lead.md) (nghi trùng, gộp, xóa dữ liệu).
5. **Sale bắt đầu dùng**: [08](08-huong-dan-nhan-vien-sale.md).
6. **Bot** (chỉ sau khi 1-5 ổn và đủ cổng): [07](07-bot-ai.md): khóa AI/Q11 → kho tri thức → nhóm câu hỏi → bảng điểm → bộ đề → phân tích → Shadow → Draft → (cuối cùng) Auto theo nhóm.
7. **Vận hành hằng ngày**: [09](09-van-hanh-hang-ngay.md).
8. **Lên sản xuất**: [10](10-quy-trinh-len-san-xuat.md) (checklist, cổng, quyết định còn mở, dọn dẹp).

Ba đợt phát hành trong đặc tả (`docs/00-tong-quan.md`): **Đợt 1** email + Zalo OA vào CRM, chống trùng, sale trả lời từ hệ thống. **Đợt 2** tổng đài (nhật ký, gọi nhỡ, việc gọi lại), bot phân tích, chấm điểm Lead, bot trả lời chạy ngầm. **Đợt 3** bot nháp cho sale duyệt, chuyển người, bot tự gửi theo nhóm; ghi âm và tóm tắt cuộc gọi **không được làm** (xem mục 4).

## 3. Sơ đồ ngắn luồng dữ liệu

```text
  Email (Email Account)     Zalo OA (webhook)        Tổng đài Stringee (webhook)
          \                       |                          /
           \                      v                         /
            +---------> CỔNG TIẾP NHẬN (lưu ngay, trả 200, chống trùng tin)
                                  |            kênh tắt vẫn LƯU để xử lý bù
                                  v
                       HÀNG ĐỢI "omni" (worker, gộp tin trong vài giây)
                                  |
                  nhận diện khách ---> chống trùng ---> Lead (Frappe CRM)
                                  |                         |
                                  |                         +--> dòng thời gian, việc gọi lại (cuộc gọi nhỡ)
                                  v
              Bot phân tích (điền thông tin, chấm điểm Lead)   [mặc định TẮT]
                                  v
              Bot soạn trả lời:  Shadow (ngầm) -> Draft (sale duyệt) -> Auto (theo nhóm)
                                  v
                    HỘP THƯ OMNI: sale đọc, trả lời, duyệt nháp
                                  v
              Chiều ra: Zalo (sandbox_mode bật = chỉ ghi log, không gửi thật)
```

Điểm cần nhớ: **tin không bao giờ mất khi kênh/bot/worker lỗi** (tin được lưu trước, xử lý bù sau); **mọi lời gọi ra ngoài** (Zalo, tổng đài, AI) đi qua một lớp có timeout, thử lại giới hạn và không ghi bí mật vào log; **nội dung khách là dữ liệu, không phải lệnh** (bot không làm theo câu như "bỏ qua hướng dẫn").

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
| Ghi âm, tóm tắt cuộc gọi | **Không có tính năng** | D3-04 bị bỏ qua: chưa chọn nhà cung cấp chuyển giọng nói, chưa đo | Cần quyết định Q13/Q14 và làm tiếp |
| Tạm dừng/duyệt nháp trong Hộp thư | Chỉ hiện khi bot Draft bật | — | — |

## 5. Danh sách tài liệu

| Tệp | Nội dung |
| --- | --- |
| [01-cai-dat-va-ha-tang.md](01-cai-dat-va-ha-tang.md) | Cài app, migrate, worker omni, supervisor, scheduler, Redis, nginx, sao lưu, sandbox, cờ site_config |
| [02-cau-hinh-omni-settings.md](02-cau-hinh-omni-settings.md) | Từng trường Omni Settings, bảng công tắc → hiệu ứng |
| [03-kenh-email.md](03-kenh-email.md) | Nối hộp thư bán hàng |
| [04-kenh-zalo-oa.md](04-kenh-zalo-oa.md) | Zalo OA: kênh, webhook, chữ ký, token, khung 48 giờ/7 ngày, thử bằng sandbox |
| [05-kenh-tong-dai-stringee.md](05-kenh-tong-dai-stringee.md) | Tổng đài: Omni Agent Map, gọi nhỡ, đối soát, ghi chú S1 |
| [cau-hinh-stringee.md](cau-hinh-stringee.md) | (Có sẵn) Cấu hình phía Stringee từng bước |
| [06-chong-trung-va-lead.md](06-chong-trung-va-lead.md) | Nhận diện khách, nghi trùng, gộp Lead, xóa dữ liệu khách |
| [07-bot-ai.md](07-bot-ai.md) | Bật bot từng bước, báo cáo, ngân sách, ngắt mạch, tắt khẩn |
| [duyet-nhap-bot.md](duyet-nhap-bot.md) | (Có sẵn) Duyệt nháp bot, ngắn gọn cho sale |
| [08-huong-dan-nhan-vien-sale.md](08-huong-dan-nhan-vien-sale.md) | Dùng Hộp thư từng bước |
| [09-van-hanh-hang-ngay.md](09-van-hanh-hang-ngay.md) | Giám sát, cảnh báo, sổ tay sự cố, quay lui, lịch kiểm tra |
| [10-quy-trinh-len-san-xuat.md](10-quy-trinh-len-san-xuat.md) | Checklist dev → staging → production, cổng, quyết định mở, dọn dẹp |

Đặc tả kỹ thuật (cho người muốn đào sâu): `docs/01-kien-truc.md` đến `docs/12-quyet-dinh-mo.md`; số liệu nghiệm thu: `docs/nghiem-thu/`.

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

## 7. Đã xong khi (cho cả bộ tài liệu)

- [ ] Mỗi người trong mục 1 biết tệp nào dành cho mình.
- [ ] Hạ tầng xong theo [01](01-cai-dat-va-ha-tang.md) mục 13 và cấu hình theo [02](02-cau-hinh-omni-settings.md) mục 13.
- [ ] Mỗi kênh dùng thật đã qua checklist "Đã xong khi" của tệp kênh tương ứng.
- [ ] Mọi cổng ở [10](10-quy-trinh-len-san-xuat.md) mục 2 có kết quả ghi lại, trung thực: "chưa đo" không được đọc là "đạt".
