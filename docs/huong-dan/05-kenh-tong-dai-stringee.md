# 05 · Kênh tổng đài (Stringee)

Dành cho: quản trị hệ thống (cấu hình) và trưởng sale (nhân viên tổng đài, việc gọi lại). Đặc tả: `docs/07-kenh-tong-dai.md`.

**Tài liệu này không lặp lại** hướng dẫn cấu hình Stringee từng bước đã có ở [cau-hinh-stringee.md](cau-hinh-stringee.md) (URL sự kiện, URL tra cứu, chữ ký, đổi token, thử cuộc gọi). Hãy đọc tệp đó cùng với tệp này. Tệp này bổ sung: tổng quan, Omni Agent Map, gọi nhỡ → việc gọi lại, giám sát, và ghi chú S1.

> **ĐIỀU KIỆN CHẶN (S1).** Không bật Stringee production cho đến khi staging xác nhận **khóa ký webhook**. Hiện CRM kiểm chữ ký bằng *API key secret* của kênh; theo tài liệu Stringee chữ ký dùng "project's signing secret key" và nhiều khả năng là khóa khác. Nếu sai: mọi sự kiện thật bị 401 (Error Log "Omni webhook: xac_thuc_that_bai"). Chưa thử với tài khoản Stringee thật. Xem [cau-hinh-stringee.md](cau-hinh-stringee.md) mục 3 và `docs/12` mục B3 (S1-S7).

Mục lục: [1. Tổng quan](#1-tổng-quan) · [2. Tạo kênh](#2-tạo-omni-channel-loại-telephony) · [3. Omni Agent Map](#3-omni-agent-map-nhân-viên-tổng-đài) · [4. Cuộc gọi vào CRM](#4-cuộc-gọi-vào-crm-như-thế-nào) · [5. Gọi nhỡ → việc gọi lại](#5-gọi-nhỡ--việc-gọi-lại) · [6. Đối soát đêm và giám sát](#6-đối-soát-đêm-và-giám-sát) · [7. Ghi chú S1](#7-ghi-chú-s1-và-các-điều-chưa-xác-nhận) · [8. Sự cố thường gặp](#8-sự-cố-thường-gặp) · [9. Đã xong khi](#9-đã-xong-khi)

## 1. Tổng quan

Luồng: Stringee gọi **hai URL của CRM** (tra cứu khách khi chuông reo; sự kiện khi cuộc gọi kết thúc) → CRM lưu `Omni Call` + `CRM Call Log` gắn Lead, và nếu gọi nhỡ thì tạo **việc gọi lại**. Kênh tổng đài **chỉ nhận**, không gửi tin; trong Hộp thư hội thoại điện thoại chỉ để xem (không có khung trả lời).

Chỉ nhà cung cấp **Stringee** dùng được ở production. Lựa chọn `Fake` trong danh sách chỉ lưu được khi `developer_mode` bật (dùng để thử trên dev).

## 2. Tạo Omni Channel loại Telephony

Các bước đầy đủ ở [cau-hinh-stringee.md](cau-hinh-stringee.md) mục 1. Tóm tắt các trường (**Desk > `/app/omni-channel`**):

| Trường | Điền |
| --- | --- |
| Tên kênh | Không dấu cách, ví dụ `tong-dai`. Tên này nằm trong URL (`?ch=`). |
| Loại kênh | `Telephony` |
| Nhà cung cấp tổng đài | `Stringee`. Kênh đang bật mà để trống nhà cung cấp thì không lưu được. |
| API key SID / API key secret | Lấy ở Stringee Dashboard (mục API Keys). Secret dạng mật khẩu. Để trống secret = bỏ kiểm chữ ký nhưng **mất** gọi Call log/xác minh cuộc gọi (chỉ là cách tạm). |
| Đầu số hotline | Số hotline của Stringee. |
| Token webhook | Chuỗi ngẫu nhiên **từ 32 ký tự** (ví dụ tạo bằng `openssl rand -hex 24`). Ngắn hơn 32 ký tự thì không lưu được; chưa đặt thì mọi webhook bị từ chối. |
| Ánh xạ nhân viên tổng đài | Bảng Omni Agent Map, xem mục 3. |
| Bật kênh | Tích **sau cùng**. Tắt: sự kiện vẫn được nhận (200) và lưu nhưng không xử lý; tra cứu trả "khách mới". |

## 3. Omni Agent Map (nhân viên tổng đài)

Bảng con trên form Omni Channel (phần "Tổng đài"), để biết **ai nghe máy** khi sự kiện chỉ ghi mã phía tổng đài. Mỗi dòng:

| Cột | Ý nghĩa |
| --- | --- |
| Mã nhân viên phía tổng đài (`agent_ref`) | Số máy nhánh hoặc ID user phía Stringee (khớp trường `number` của bên internal trong sự kiện; việc khớp này **chưa xác nhận**, S6). |
| User CRM (`user`) | Nhân viên tương ứng trong CRM. |
| Máy nhánh (`extension`) | Số máy nhánh (thông tin thêm). |

Không có dòng nào khớp thì cuộc gọi vẫn được lưu nhưng không biết sale nào nghe (`agent` trống). Cập nhật bảng khi có nhân viên vào/ra.

## 4. Cuộc gọi vào CRM như thế nào

- Khi chuông reo: Stringee hỏi CRM (URL tra cứu). CRM trả tên khách, mã Lead, sale phụ trách, đường dẫn Lead; hoặc "khách mới". Việc này **chỉ đọc**, trả lời trong khoảng 300 ms, và mọi lỗi (sai token, kênh tắt, DB chậm) vẫn trả HTTP 200 để **không làm hỏng cuộc gọi**.
- Khi cuộc gọi kết thúc: CRM nhận sự kiện, chuẩn hóa SĐT, tìm/tạo Lead (nguồn "Điện thoại"), tạo `Omni Call` và `CRM Call Log`, và xuất hiện một mục trong Hộp thư. Gửi lại cùng sự kiện không tạo bản ghi thứ hai.
- Khách **ẩn số** hoặc số không chuẩn hóa được: chỉ ghi `Omni Call` và `CRM Call Log`, **không** tạo Lead và không có việc gọi lại (không biết gọi ai).
- Cuộc gọi dùng chung `debounce_seconds` (5 giây) nên mỗi cuộc gọi giữ một worker ~6 giây: tính số worker ở [01](01-cai-dat-va-ha-tang.md) mục 3.

## 5. Gọi nhỡ → việc gọi lại

Khi có **cuộc gọi đến không ai nghe** từ khách đã có Lead:

1. Hệ thống (qua một job riêng, sau khi lô tin commit) xác minh cuộc gọi nhỡ bằng Call log của Stringee (khi `sandbox_mode` tắt; sandbox bật thì bỏ qua xác minh). Không xác minh được thì cuộc gọi bị đánh dấu "Chưa xác minh với nhà cung cấp" nhưng **vẫn xử lý**.
2. Tạo **một CRM Task** "Gọi lại khách <tên>" (trạng thái Todo, ưu tiên cao), gắn Lead, giao cho:
   - chủ Lead / người phụ trách Lead; nếu không có thì
   - **Người nhận việc gọi lại dự phòng** (`callback_fallback_user` trong Omni Settings); nếu cũng trống thì task **không có người nhận** (vẫn đếm là quá hạn và cảnh báo).
3. **Hạn** = thời điểm gọi + `missed_call_callback_minutes` (mặc định 30 phút) **tính theo giờ làm việc** và ngày lễ ([02](02-cau-hinh-omni-settings.md) mục 4, 6). Gọi nhỡ ngoài giờ/lễ thì hạn rơi vào giờ làm việc kế tiếp. **Phải điền ngày nghỉ lễ** thì hạn mới đúng.
4. Cùng khách gọi nhỡ nhiều lần khi việc cũ còn mở: **không tạo thêm việc**, cập nhật mô tả "Khách đã gọi nhỡ N lần, lần cuối ...".
5. Khi có cuộc gọi được nghe máy với khách này sau lần nhỡ cuối (đến hoặc đi): việc gọi lại **tự đóng** (Done).
6. Việc bị quá hạn: cảnh báo `goi_lai_qua_han` tới Omni Admin và người được giao (Notification Log) và thư tới `alert_recipients`.

Sale xử lý việc này ở **CRM** (danh sách việc/Task của Lead), không phải ở Hộp thư Omni: xem [08](08-huong-dan-nhan-vien-sale.md) mục gọi lại.

Nếu không thấy việc gọi lại dù có cuộc gọi nhỡ: cuộc gọi chưa có Lead (ẩn số), hoặc kênh tắt, hoặc worker `omni` không chạy (tác vụ quét tạo bù sau tối đa ~10 phút).

## 6. Đối soát đêm và giám sát

- **01:30 mỗi đêm**: với mỗi kênh tổng đài đang bật, hệ thống hỏi Stringee danh sách cuộc gọi hôm qua và **bù** cuộc gọi nào CRM chưa có (không trùng). Bị bỏ qua khi kênh tắt hoặc `sandbox_mode` bật.
- Khối **"Tổng đài (hôm nay)"** ở `/app/omni-monitor`: số cuộc gọi, đã nghe máy, gọi nhỡ, việc gọi lại quá hạn, kết quả đối soát đêm (ngày, tổng, đã có, bù thêm hoặc LỖI) và số "cuộc gọi nhỡ chưa xác minh". Số chưa xác minh tăng đều nghĩa là sự kiện nhỡ tới webhook mà Call log của Stringee không có (hoặc API Stringee lỗi): kiểm tra có bị giả mạo sự kiện (lộ `webhook_token`) hay API lỗi. Chưa có cảnh báo riêng cho số này.
- Cảnh báo "đối soát bù nhiều" (`doi_soat_bu_nhieu`, bù trên 1%) nghĩa là webhook thật đang rơi sự kiện: kiểm tra URL, token, chữ ký.
- Đổi `webhook_token` khi nghi lộ: [cau-hinh-stringee.md](cau-hinh-stringee.md) mục 6. Làm ngoài giờ cao điểm.

## 7. Ghi chú S1 và các điều chưa xác nhận

Chưa thử với tài khoản Stringee thật. Phải xác nhận trên staging có tổng đài thử (`docs/12` mục B3):

| Mã | Điều cần xác nhận | Hậu quả nếu sai |
| --- | --- | --- |
| **S1** | Khóa ký webhook có đúng là API key secret của kênh không | **Chặn production**: mọi sự kiện bị 401 |
| S2 | Định dạng JSON tra cứu (`found`, `customer_name`, `lead`, `owner_name`, `lead_url`) có được softphone/app đọc từ `customDataFromYourServer` | Màn hình chuông reo không hiện thông tin khách; cuộc gọi không hỏng |
| S3 | `id` trong Call log có trùng `call_id` của sự kiện | Đối soát đêm coi mọi cuộc gọi là thiếu; xác minh luôn thất bại (chỉ đánh dấu chưa xác minh) |
| S4 | URL tra cứu có được ký không | Tra cứu chỉ dựa vào token `t` |
| S5 | Sự kiện ghi âm | Chỉ ảnh hưởng ghi âm, chưa có ở bản này |
| S6 | `agent_ref` khớp máy nhánh | Sai nhân viên nghe máy |
| S7 | Cuộc gọi nghe máy luôn có thời lượng > 0 | Cuộc gọi nghe máy có thể bị coi là nhỡ → việc gọi lại thừa |

**Ghi âm và tóm tắt cuộc gọi chưa có** (xem [02](02-cau-hinh-omni-settings.md) mục 10): chưa có công tắc, chưa có nhà cung cấp chuyển giọng nói.

## 8. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Không có cuộc gọi nào vào CRM, Error Log "xac_thuc_that_bai" | Sai `t` trong URL, token ngắn hơn 32 ký tự, hoặc khóa ký khác (S1) | [cau-hinh-stringee.md](cau-hinh-stringee.md) mục 3, 6 |
| Màn hình chuông reo không hiện tên khách | Chưa cấu hình URL tra cứu, timeout 300 ms bị vượt, hoặc S2 | Mục 2 của tệp Stringee; thử gọi |
| Cuộc gọi có nhưng không biết sale nào nghe | Omni Agent Map thiếu/sai `agent_ref` | Mục 3 |
| Gọi nhỡ không sinh việc gọi lại | Khách ẩn số/chưa có Lead; kênh tắt; worker không chạy | Mục 5, `/app/omni-monitor` |
| Việc gọi lại không có người nhận | Lead chưa có chủ và chưa đặt `callback_fallback_user` | Điền `callback_fallback_user` ([02](02-cau-hinh-omni-settings.md) mục 6); giao tay các việc đang trống |
| Hạn gọi lại sai (ngày lễ vẫn tính là làm việc) | Chưa điền `holidays` | [02](02-cau-hinh-omni-settings.md) mục 4 |
| "Cuộc gọi nhỡ chưa xác minh" tăng | Mục 6 | Kiểm token/API |
| Cuộc gọi nghe máy lại bị coi là nhỡ | S7 | Báo nhà phát triển kèm một ví dụ |

## 9. Đã xong khi

- [ ] Omni Channel Telephony đã tạo: nhà cung cấp Stringee, API key SID/secret, hotline, `webhook_token` ≥ 32 ký tự, Omni Agent Map đủ nhân viên.
- [ ] Hai URL (sự kiện, tra cứu) đã khai báo ở Stringee với `ch` và `t` đúng; HTTPS; nginx không ghi query vào log ([01](01-cai-dat-va-ha-tang.md) mục 6).
- [ ] Gọi thử vào đầu số: có `Omni Call` và `CRM Call Log` gắn Lead; hội thoại điện thoại hiện trong Hộp thư.
- [ ] Gọi nhỡ thử: có đúng một việc "Gọi lại khách ...", đúng người nhận, đúng hạn; gọi nhỡ thêm lần nữa không tạo việc thứ hai; sau khi sale gọi lại thành công việc tự đóng.
- [ ] S1 (khóa ký) đã được xác nhận trên staging; các mục S2-S7 đã ghi kết quả.
- [ ] `callback_fallback_user` và `holidays` đã điền.
- [ ] Đối soát đêm chạy (khối Tổng đài ở `/app/omni-monitor` có dòng ngày hôm qua).
