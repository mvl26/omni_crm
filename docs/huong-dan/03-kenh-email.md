# 03 · Kênh email

Dành cho: quản trị hệ thống. Kênh email là kênh đơn giản nhất: Frappe lo việc nhận thư, CRM lo việc hiển thị và trả lời thư trên Lead. Việc của `omni_crm` là **chặn tạo Lead trùng** và đưa thư vào chung đường chống trùng khách (cùng một người nhắn Zalo rồi gửi email vẫn là một Lead). Đặc tả: `docs/05-kenh-email.md`, quyết định: `docs/adr/0005-tat-co-che-tao-lead-tu-email-cua-crm.md`.

Mục lục: [1. Chuẩn bị](#1-chuẩn-bị) · [2. Email Account](#2-cấu-hình-email-account-quan-trọng-nhất) · [3. Omni Channel](#3-tạo-omni-channel-loại-email) · [4. Hành vi](#4-hệ-thống-xử-lý-thư-thế-nào) · [5. Kiểm thử](#5-kiểm-thử) · [6. Sự cố thường gặp](#6-sự-cố-thường-gặp) · [7. Đã xong khi](#7-đã-xong-khi)

## 1. Chuẩn bị

- Hoàn thành [01](01-cai-dat-va-ha-tang.md) (worker `omni` chạy, scheduler bật) và [02](02-cau-hinh-omni-settings.md) mục 1.
- Một hộp thư bán hàng (ví dụ `sales@ten-mien-cua-ban.vn`) có IMAP/POP3 mà bạn có thông tin đăng nhập.
- Quyền Omni Admin (để tạo Omni Channel) và quyền quản trị Email Account.

## 2. Cấu hình Email Account (quan trọng nhất)

Mở **Desk > `/app/email-account`** (chưa kiểm trên giao diện), tạo hoặc mở tài khoản của hộp thư bán hàng:

1. Điền thông tin nhận thư (IMAP/POP3), bật **Enable Incoming**.
2. **Bỏ chọn** "Create Lead from Incoming Emails" (trường `create_lead_from_incoming_email`, do app `crm` thêm vào Email Account). Nếu để chọn, CRM tự tạo Lead song song với Omni và bạn có hai Lead cho một thư.
3. Để trống **Append To**, hoặc ít nhất **không** đặt là `CRM Lead`.
4. Lưu và dùng nút kiểm tra kết nối của Email Account nếu có.

Khi bạn lưu Omni Channel email đang bật (mục 3), form sẽ hiện cảnh báo màu cam "CRM sẽ tạo Lead trùng với Omni CRM" kèm việc cần làm nếu hai cấu hình trên còn sai. Cảnh báo chỉ nhắc, không chặn lưu: hãy đọc nó.

Nếu hộp thư này **cũng** dùng để gửi thư cho khách: trả lời bằng trình soạn thư của CRM trên Lead (mục 4). Với mục đích gửi cảnh báo nội bộ thì xem [01](01-cai-dat-va-ha-tang.md) mục 7 (tài khoản gửi đi mặc định).

## 3. Tạo Omni Channel loại Email

Mở **Desk > `/app/omni-channel` > Thêm mới** (hoặc nút New).

| Trường | Điền |
| --- | --- |
| Tên kênh (`channel_name`) | Tên ngắn, không dấu cách, ví dụ `email-ban-hang`. Đây cũng là mã của kênh: đặt một lần, tránh đổi về sau. |
| Loại kênh (`channel_type`) | `Email` |
| Tài khoản email (`email_account`) | Email Account vừa cấu hình |
| Bật kênh (`enabled`) | Tích **sau cùng**, khi mọi thứ đã sẵn sàng. Kênh email đang bật mà thiếu tài khoản email thì không lưu được. |
| Bật chiều ra (`outbound_enabled`) | Với email không cần: sale trả lời bằng trình soạn thư của CRM, không qua chiều ra của Omni. Bot tự gửi chỉ áp dụng cho Zalo OA (`bot/auto_send.py` kiểm kênh là Zalo OA), nên ô này không có tác dụng thực tế với email. |
| Token webhook (`webhook_token`) | Không dùng cho email. Để trống. |

Lưu lại.

## 4. Hệ thống xử lý thư thế nào

- Mỗi thư **đến** (Communication loại Email, "Received") của Email Account thuộc một Omni Channel đang bật được đưa vào đường ống xử lý (qua hook `Communication.after_insert`). Hộp thư không thuộc kênh nào, hoặc kênh đang tắt thì thư bị bỏ qua.
- **Thư tự động bị bỏ qua** (trạng thái `Ignored`, không tạo Lead): thư có header `Auto-Submitted` khác `no`, `Precedence` là `bulk`/`list`/`junk`, có `List-Unsubscribe`, người gửi dạng hệ thống (`no-reply`, `noreply`, `mailer-daemon`, `postmaster`), thư báo lỗi gửi.
- **Chống trùng**: mỗi thư được nhận dạng bằng `Message-ID`; Frappe kéo lại cùng thư không tạo bản ghi thứ hai. Thư trả lời trong cùng chuỗi (`In-Reply-To` trỏ tới thư đã gắn Lead) được gắn vào đúng Lead đó.
- **Làm sạch nội dung** trước khi bot đọc: bỏ phần trích dẫn thư cũ, bỏ chữ ký (nhưng lấy SĐT/email trong chữ ký làm tín hiệu nhận diện khách), cắt tối đa 8.000 ký tự. Bản đầy đủ vẫn nằm trong Communication.
- Thư hiện trong tab hoạt động/email của Lead trong CRM (không tạo bản sao).
- **Trả lời khách**: sale dùng trình soạn thư có sẵn của CRM trên Lead. Trong Hộp thư Omni, hội thoại email có nút **Trả lời email trong CRM** mở Lead; Hộp thư **không** có khung soạn thư cho email. Sandbox không ảnh hưởng thư gửi từ trình soạn của CRM (đó là chức năng của CRM, không phải của Omni).

## 5. Kiểm thử

Làm trên staging, `sandbox_mode` bật hay tắt đều được (chiều vào không bị sandbox ảnh hưởng). Cần một địa chỉ email cá nhân để gửi thử.

1. Gửi thư từ Gmail cá nhân tới hộp thư bán hàng. Chờ Frappe kéo thư (chu kỳ do Frappe quyết định, thường vài phút; chưa kiểm thực tế) rồi thêm tối đa 1-2 phút xử lý.
2. Kỳ vọng: **một** Lead mới, thư hiện trong tab email của Lead; mở `/app/omni-inbox` thấy hội thoại kênh Email. Kiểm bằng `/app/omni-inbound-message`: trạng thái `Done`.
3. Gửi thư thứ hai từ cùng địa chỉ với tiêu đề khác: vẫn **cùng Lead**, không có Lead thứ hai do CRM tạo.
4. Trả lời một thư trong chuỗi: vào cùng Lead.
5. Gửi thư có tiêu đề "Automatic reply" hoặc từ `no-reply@...`: không sinh Lead; bản ghi tin vào có trạng thái `Ignored`.
6. Gửi thư có chữ ký chứa SĐT: Lead được điền SĐT nếu Lead chưa có.

Kết quả các bước trên trên giao diện thật **chưa được kiểm** (nghiệm thu Đợt 1 chạy bằng test tự động và dữ liệu giả; xem `docs/nghiem-thu/uat-dot-1.md`, kịch bản U-4, U-6).

## 6. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Một thư tạo **hai** Lead | Email Account còn tích "Create Lead from Incoming Emails" hoặc `Append To = CRM Lead` | Làm lại mục 2; Lead thừa xử lý bằng gộp ([06](06-chong-trung-va-lead.md)) |
| Thư không vào hệ thống | Omni Channel tắt/không trỏ đúng Email Account; Email Account không bật nhận; scheduler tắt | Kiểm từng bước; `/app/omni-inbound-message` lọc theo kênh |
| Tin vào kẹt `New` | Worker `omni` không chạy | [01](01-cai-dat-va-ha-tang.md) mục 3 |
| Tin vào `Manual` | Xử lý lỗi quá số lần thử | `/app/omni-monitor` > "Xử lý lại" sau khi sửa nguyên nhân ([09](09-van-hanh-hang-ngay.md)) |
| Thư thật bị `Ignored` | Người gửi thuộc dạng hệ thống/thư hàng loạt | Đọc `last_error`/`event_type` của tin; nếu thật sự là khách, nhờ khách gửi từ địa chỉ khác; báo nhà phát triển nếu sai lặp lại |
| Hai khách cùng một công ty bị gộp một Lead | SĐT/email chung trong chữ ký | Xem [06](06-chong-trung-va-lead.md): hệ thống chỉ gộp khi tín hiệu chắc chắn, còn lại tạo "nghi trùng" |

## 7. Đã xong khi

- [ ] Email Account: nhận thư được, "Create Lead from Incoming Emails" bỏ chọn, `Append To` không phải CRM Lead.
- [ ] Omni Channel loại Email đã lưu, đã bật, không còn cảnh báo cam.
- [ ] Gửi thử một thư mới: đúng **một** Lead, thư hiện trong CRM.
- [ ] Gửi thư thứ hai khác tiêu đề từ cùng người: cùng Lead.
- [ ] Thư tự động/no-reply không tạo Lead.
- [ ] Sale biết cách trả lời email: mở Lead, dùng trình soạn thư của CRM ([08](08-huong-dan-nhan-vien-sale.md)).
