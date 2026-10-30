# 06 · Chống trùng khách và Lead

Dành cho: trưởng sale, quản trị hệ thống; nhân viên sale đọc mục 3 và 4. Đặc tả: `docs/04-dinh-danh-va-chong-trung.md`, quyết định gộp: `docs/adr/0002-gop-lead-tuong-minh.md`.

Nguyên tắc của hệ thống: **gộp khi chắc, hỏi người khi không chắc.** Một Lead trùng gây phiền (hai sale gọi một khách); một lần gộp sai gây hại (lộ hội thoại của người này cho người khác). Vì vậy hệ thống thà để trùng và báo "nghi trùng" còn hơn gộp nhầm.

Mục lục: [1. Cách nhận diện khách](#1-hệ-thống-nhận-diện-khách-thế-nào) · [2. Nghi trùng](#2-nghi-trùng-omni-duplicate-candidate) · [3. Gộp Lead thủ công](#3-gộp-lead-thủ-công) · [4. Lead đã chuyển thành Deal](#4-lead-đã-chuyển-thành-deal) · [5. Xóa dữ liệu khách](#5-xóa-dữ-liệu-khách-và-giới-hạn-hiện-có) · [6. Sự cố thường gặp](#6-sự-cố-thường-gặp) · [7. Đã xong khi](#7-đã-xong-khi)

## 1. Hệ thống nhận diện khách thế nào

Mỗi người liên hệ có một **định danh** (Omni Identity) theo kênh: Zalo (`Zalo OA:<id>`), email (`Email:<địa chỉ>`), điện thoại (`Telephony:+84...`). Định danh được gắn vào một Lead. Khi có tin mới:

1. Định danh đã gắn Lead → vào đúng Lead đó.
2. Chưa gắn: gom **tín hiệu** từ tin, rồi tìm Lead đã có cùng SĐT/email.

| Tín hiệu | Độ mạnh |
| --- | --- |
| Định danh đã gắn Lead | Chắc chắn |
| SĐT gọi đến (caller id) | Mạnh |
| Email người gửi (kênh email) | Mạnh |
| SĐT/email khách gửi qua form yêu cầu thông tin của Zalo | Mạnh |
| SĐT/email nằm trong nội dung tin, chữ ký email | Trung bình (khách có thể ghi số của người khác) |
| Tên hiển thị giống nhau | Yếu: chỉ để tạo "nghi trùng", không bao giờ để gộp |

Quyết định: đúng **một** Lead khớp tín hiệu mạnh → gắn vào Lead đó. Nhiều Lead khớp → gắn Lead hoạt động gần nhất và tạo "nghi trùng" cho các Lead còn lại. Chỉ khớp tín hiệu trung bình → gắn nếu tên không mâu thuẫn, ngược lại tạo Lead mới kèm nghi trùng. Không khớp gì → Lead mới.

SĐT được chuẩn hóa sang dạng quốc tế `+84...` (kể cả đầu số 11 số cũ); email chữ thường (không bỏ dấu chấm, không bỏ phần `+tag`). Việc chuẩn hóa chạy mỗi khi Lead được lưu (hai trường ẩn `omni_phone_e164`, `omni_email_norm`).

Hệ quả cho người dùng: một khách nhắn Zalo (chưa có SĐT), sau đó gửi form SĐT, sau đó gửi email chữ ký có SĐT đó → vẫn **một** Lead. Cách làm tăng khả năng này: dùng nút **Xin SĐT qua Zalo** ([08](08-huong-dan-nhan-vien-sale.md)).

## 2. Nghi trùng (Omni Duplicate Candidate)

Khi hệ thống không chắc, nó tạo một bản ghi **Omni Duplicate Candidate** gồm hai Lead (A, B), **độ giống 0-100**, lý do và trạng thái:

| Trạng thái | Nghĩa |
| --- | --- |
| Open | Chờ người quyết |
| Merged | Đã gộp |
| Dismissed | Đã bỏ qua ("Không trùng") |

Điểm: tín hiệu mạnh dùng chung = 100; tín hiệu trung bình tên mâu thuẫn = 60; trung bình mơ hồ = 50; trùng tên = độ giống × 100 (so sau bỏ dấu, chỉ khi cả hai tên có ít nhất 2 từ riêng).

**Xem danh sách:** Desk > `/app/omni-duplicate-candidate` (lọc Trạng thái = Open). Sale (Sales User) có quyền **đọc**; Omni Admin đọc/ghi/xóa. Màn hình giám sát cảnh báo khi số cặp Open vượt 20.

**Nhịp xử lý:** quản trị hoặc trưởng sale xem hằng ngày ([09](09-van-hanh-hang-ngay.md)). Cặp nào quá cũ không ai xử lý thì tích dần và gây gọi trùng khách.

## 3. Gộp Lead thủ công

Hệ thống **không bao giờ tự gộp** hai Lead đã tồn tại. Người gộp bấm nút trên form nghi trùng.

Điều kiện: người bấm phải có **quyền sửa cả hai Lead** (theo phân quyền CRM) và quyền đọc bản ghi nghi trùng. Sales User chỉ có quyền đọc bản ghi nghi trùng; việc gộp thành công hay không phụ thuộc quyền sửa hai Lead (chưa kiểm trên giao diện với tài khoản Sales User thật). Nếu sale không gộp được, nhờ trưởng sale/quản trị.

**Các bước** (chưa kiểm trên giao diện thật):

1. Mở bản ghi nghi trùng ở trạng thái **Open**.
2. Đọc cả hai Lead (mở từng Lead trong CRM): SĐT, email, tên, hội thoại, người phụ trách. Quyết định Lead nào là **Lead chính** (giữ lại): thường là Lead có nhiều lịch sử hơn hoặc đã chuyển thành Deal.
3. Bấm nhóm nút **Gộp** > **Gộp vào <mã Lead chính>** (có hai lựa chọn, mỗi Lead một nút), xác nhận "Gộp Lead còn lại vào ...? Lead còn lại sẽ được giữ nhưng đánh dấu đã gộp."
4. Nếu **không phải cùng một người**, bấm **Không trùng**: cặp chuyển sang Dismissed, không gộp, và hệ thống sẽ không báo lại cặp này.

**Điều gì xảy ra khi gộp:**

- Chuyển sang Lead chính: các định danh, tin vào/ra, cuộc gọi, nháp bot, lần gọi AI của Lead phụ; và bản ghi Frappe/CRM có tham chiếu Lead phụ: Communication, Comment, CRM Call Log, CRM Task, ghi chú (FCRM Note), ToDo (phân công sale), liên kết timeline.
- Trường trống ở Lead chính (email, SĐT, đơn vị, nguồn, ...) lấy từ Lead phụ; trường đã có thì **giữ nguyên**, các khác biệt được ghi vào một Comment trên Lead chính. Tên và người phụ trách (`lead_owner`) **không** bị đổi.
- Lead phụ **không bị xóa**: chuyển trạng thái `Junk` (lý do mất "Other", ghi chú "Đã gộp vào ..."), trường "Đã gộp vào Lead" (`omni_merged_into`) trỏ về Lead chính, có Comment "Đã gộp vào ...".
- Mọi thứ nằm trong một giao dịch: lỗi giữa chừng thì hoàn lại hết.
- Các nghi trùng khác liên quan Lead phụ được trỏ lại sang Lead chính hoặc đóng.

**Không có chức năng hoàn tác** gộp trong app (không thấy trong mã). Lead phụ còn nguyên nên về lý thuyết khôi phục được bằng tay, nhưng các tham chiếu đã chuyển thì không tự trả lại. Vì vậy: **không chắc thì bấm "Không trùng"**, rồi tạm để hai Lead.

## 4. Lead đã chuyển thành Deal

Nếu Lead đã chuyển thành Deal, tin mới của khách vẫn gắn vào Lead đó, nhưng dòng thời gian được ghi vào **Deal** tương ứng (Q10: ghi tin mới vào Deal; mặc định "có"). Hộp thư hiện nút "Mở Deal trong CRM". Khi gộp, `CRM Deal.lead` **không bị sửa**; hệ thống vẫn tìm đúng Deal của các Lead đã gộp.

## 5. Xóa dữ liệu khách và giới hạn hiện có

Khi khách yêu cầu xóa dữ liệu, **Omni Admin** vào **`/app/omni-monitor`** (không phải trên trang Lead như đặc tả `docs/11` mục 6 ghi) > khối **"Xóa dữ liệu khách"**:

1. Nhập mã Lead (dạng `CRM-LEAD-...`) và **lý do** (bắt buộc).
2. Bấm **Xóa dữ liệu khách**, xác nhận. **Không hoàn tác được.**

**Hệ thống xóa gì:** thay nội dung (văn bản) tin vào và tin ra của Lead và các định danh của Lead bằng dấu "đã xóa"; xóa tệp đính kèm, payload thô, hồ sơ người gửi của tin vào; xóa kết quả có cấu trúc và tóm tắt đầu vào của các lần gọi AI (`Omni Bot Run`); xóa nháp, câu cuối cùng gửi, câu sale trả lời và ghi chú chấm của nháp bot. Ghi một Comment lên Lead (người làm, lý do, số lượng). **Không xóa Lead.**

**Hệ thống KHÔNG xóa (giới hạn hiện có, chưa đủ cho yêu cầu pháp lý đầy đủ):**

- `Communication` do dòng thời gian tạo (nội dung email, tin Zalo hiển thị trong CRM).
- Tên, SĐT, email trong **Omni Identity** và `identity_key`; tên, SĐT, email trong chính **CRM Lead**.
- `Comment` liên quan.
- **`Omni Call`** (số gọi, `raw_events`) và **`CRM Call Log`**: chưa phủ, nên **chưa dùng được cho yêu cầu xóa dữ liệu pháp lý đối với cuộc gọi** (`docs/nghiem-thu/dot-3.md` mục 9.1).
- Tệp ghi âm (chưa có tính năng ghi âm).
- Bản sao lưu cũ.

Mô tả trên màn hình ("... và các định danh của nó") nghe rộng hơn thực tế: nó xóa *nội dung tin* gắn với định danh, không xóa thông tin liên lạc trong bản ghi định danh. Nếu cần xóa hẳn, phải xử lý thủ công những mục trên (cần phương án và quyết định của chủ dự án; Q7 về thời hạn lưu cũng chưa chốt).

## 6. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Hai Lead cùng một khách mà không có nghi trùng | Khách dùng SĐT/email khác nhau trên hai kênh, không có tín hiệu chung | Gộp thủ công ở mục 3 (cần mở bản ghi nghi trùng: nếu không có, nhờ quản trị tạo bản ghi nghi trùng tay hoặc ghi lại làm số liệu chống trùng) |
| Quá nhiều nghi trùng cùng SĐT | Nhiều người dùng chung một SĐT (công ty) | Xử lý "Không trùng" hoặc gộp có chủ đích; nếu bất thường tăng đột ngột, kiểm độ trễ DB (khóa tín hiệu bận, `docs/11` D1-05) |
| Bấm "Gộp" báo "Bạn không có quyền sửa Lead ..." | Người bấm không có quyền sửa một trong hai Lead | Nhờ trưởng sale/quản trị |
| Báo "Cặp này đã được bỏ qua, không thể gộp" | Cặp đã ở trạng thái Dismissed | Gộp bằng bản ghi nghi trùng khác nếu cần |
| Báo "Lead chính ... đã bị gộp vào ..." | Lead đã được gộp trước đó | Chọn Lead chính hiện hành |
| Khách bị gộp nhầm với người khác (R1) | Tín hiệu trung bình (SĐT trong nội dung) sai | Nghi trùng tạo ra là để tránh điều này; nếu vẫn xảy ra, ghi lại ví dụ cho nhà phát triển. Rủi ro R1 chưa đo trên khách thật |
| Yêu cầu xóa dữ liệu khách có cuộc gọi | Giới hạn mục 5 | Không hứa xóa hoàn toàn; xử lý thủ công và chờ quyết định |

## 7. Đã xong khi

- [ ] Trưởng sale/quản trị biết nơi xem nghi trùng (`/app/omni-duplicate-candidate`) và nhịp xử lý hằng ngày.
- [ ] Đã thử gộp một cặp thử trên staging: Lead chính nhận hội thoại/việc của Lead phụ, Lead phụ thành `Junk` và có "Đã gộp vào ...".
- [ ] Đã thử "Không trùng" trên một cặp thử.
- [ ] Người được giao việc gộp có quyền sửa Lead (CRM).
- [ ] Omni Admin biết thao tác "Xóa dữ liệu khách" ở `/app/omni-monitor`, và biết các **giới hạn ở mục 5** (không hứa với khách hơn khả năng).
