# UAT Đợt 1: tuần chạy thử với 1-2 sale

Mục đích: sale thật dùng Hộp thư Omni và Lead trong Frappe CRM trong 1 tuần làm việc, để (a) bắt lỗi mà test tự động không thấy (giao diện, thói quen), (b) lấy số đo cho các chỉ số ở `docs/nghiem-thu/dot-1.md` mục 4.

## 0. Điều kiện trước ngày đầu (người phụ trách hệ thống làm, KHÔNG phải sale)

Chưa đạt mục nào thì chưa bắt đầu tuần chạy thử. Danh sách đầy đủ ở `docs/nghiem-thu/dot-1.md` mục 8.

- [ ] Z1/G7 (công thức chữ ký Zalo), G8, G9 đã đối chiếu với tài liệu Zalo chính thức hoặc OA thật, có link (docs/12 mục B2).
- [ ] Đã chốt R1 (docs/12 mục D, "Review D1-05, R1") hoặc ghi rõ chấp nhận rủi ro gộp sai.
- [ ] Redis queue của bench chạy khỏe (cổng 11000 đang hỏng RDB trên máy dev); có tiến trình `bench worker --queue omni` (tối thiểu 2).
- [ ] `developer_mode` TẮT trên site (kiểm `frappe.conf.developer_mode`), `zalo_skip_signature` không bật.
- [ ] `Omni Settings.sandbox_mode`: giai đoạn đầu giữ BẬT 1 ngày để sale xem hộp thư mà chưa gửi thật; tắt có chủ đích khi người phụ trách đồng ý (chỉ định người nhận thử).
- [ ] Email Account của hộp thư bán hàng: cờ "tạo Lead từ thư đến" của CRM = 0 và `append_to` trống (ADR 0005).
- [ ] Đã chọn 1-2 sale, tạo tài khoản role Sales User, kiểm Lead được giao đúng người (Q4: quy tắc gán chưa chốt, có thể gán tay).
- [ ] `alert_recipients` đã điền, người nhận biết ý nghĩa thư cảnh báo.

## 1. Việc hằng ngày của sale

| Giờ | Việc | Làm ở đâu |
| --- | --- | --- |
| Đầu ngày | Mở Hộp thư `/app/omni-inbox`, lọc "Chưa trả lời" và "Của tôi". Trả lời khách tồn từ hôm qua | Hộp thư |
| Trong ngày | Khách nhắn Zalo hoặc gửi email tới hộp thư bán hàng: đợi Lead xuất hiện (tối đa 1 phút), mở Lead, trả lời. Ghi giờ khách nhắn và giờ mình trả lời nếu thấy chậm | Hộp thư, Lead |
| Khi Zalo ngoài 48 giờ | Hệ thống hỏi xác nhận tính phí; sale chỉ xác nhận khi thật sự cần. Quá 7 ngày: không gửi được, dùng kênh khác | Hộp thư |
| Khi khách chưa có SĐT | Bấm "Xin SĐT qua Zalo" (tối đa 1 lần / 7 ngày) | Hộp thư |
| Khi thấy 2 Lead là một người | Mở "Nghi trùng", gộp hoặc bỏ qua. Nếu Lead trùng mà hệ thống không báo: ghi lại (đây là số liệu quan trọng) | Omni Duplicate Candidate |
| Cuối ngày | Điền mẫu ghi nhận (mục 3), báo lỗi gặp trong ngày | Bảng ghi nhận |

Việc của người quản trị hệ thống mỗi ngày: xử lý tin Manual (`/app/omni-monitor` > "Xử lý lại"), xem nghi trùng quá hạn, chạy `measure_dot1.py` cho ngày hôm đó (mục 4 của `dot-1.md`) và lưu kết quả.

## 2. Kịch bản kiểm tay (mỗi sale làm ít nhất 1 lần trong tuần)

| # | Kịch bản | Cách làm | Mong đợi |
| --- | --- | --- | --- |
| U-1 | Khách mới nhắn Zalo | Nhờ một người nhắn OA từ tài khoản Zalo chưa từng nhắn | Trong 1 phút có Lead mới, tên là tên hiển thị Zalo, có tin trong dòng thời gian |
| U-2 | Khách nhắn 3 tin liền | Gửi 3 tin cách nhau vài giây | 1 Lead, 3 dòng trong dòng thời gian |
| U-3 | Trả lời từ hệ thống | Bấm trả lời trong Hộp thư | Khách nhận tin (khi sandbox tắt); trạng thái "Đã gửi" |
| U-4 | Email vào | Gửi thư từ Gmail cá nhân tới hộp thư bán hàng | 1 Lead, thư hiện trong tab hoạt động CRM, chữ ký và phần trích dẫn không làm rối |
| U-5 | Cùng khách qua 2 kênh | Khách nhắn Zalo, để lại SĐT bằng form, rồi gửi email có chữ ký cùng SĐT | Vẫn 1 Lead, hoặc 1 Lead + 1 nghi trùng (ghi lại kết quả) |
| U-6 | Thư tự động | Gửi thư có tiêu đề "Automatic reply" hoặc từ no-reply | Không sinh Lead |
| U-7 | Khách hỏi lại sau 3 ngày | Nhắn lại sau 48 giờ, sale trả lời | Hệ thống cảnh báo có phí, đòi xác nhận |
| U-8 | Hai sale cùng mở một Lead | Sale B mở Lead của sale A | Sale B không xem được nếu Lead không giao cho B (trừ Sales Manager) |
| U-9 | Lead trùng thật | Tạo Lead tay cùng SĐT với khách đang nhắn | Có "Nghi trùng", không tự gộp |

## 3. Mẫu ghi nhận hằng ngày (sale điền, một dòng cho mỗi việc đáng ghi)

Tệp bảng tính chung, các cột:

| Ngày | Sale | Giờ | Loại (Lỗi / Chậm / Trùng / Góp ý / Bình thường) | Kênh | Lead (mã) | Mô tả ngắn, mong đợi gì, thấy gì | Ảnh chụp (nếu có) | Mức độ (Chặn / Cao / Thấp) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

Riêng chỉ số "phản hồi lần đầu": với 10 khách đầu tiên mỗi ngày, ghi **giờ khách nhắn** và **giờ mình trả lời**. Con số này đối chiếu với số script đo (sale không phải tính).

## 4. Cách báo lỗi

1. Lỗi chặn (không nhận được tin, không gửi được, Lead sai người, khách nhận tin sai): báo ngay cho người quản trị hệ thống bằng điện thoại hoặc nhóm chat; **không** thử lại nhiều lần.
2. Lỗi khác: ghi vào bảng mục 3 trong ngày.
3. Người quản trị đưa lỗi vào `docs/12-quyet-dinh-mo.md` mục D (cột Mức độ) và quyết sửa ngay hay để sau.
4. Không được chụp ảnh nội dung chat của khách gửi ra ngoài nhóm dự án.

## 5. Điều kiện dừng thử sớm

Dừng chạy thử (đặt `enabled` của kênh = 0, tin vẫn được lưu) khi: có tin gửi nhầm khách; Lead bị gộp sai người; mất tin được xác nhận; hoặc cảnh báo "Tin chờ xử lý quá lâu" không tự hết sau 30 phút.

## 6. Kết thúc tuần

- Chạy `measure_dot1.py` cho cả tuần, điền cột "Đo được" của `dot-1.md` mục 4.
- Họp 30 phút với sale: gom danh sách lỗi và góp ý, phân loại.
- Người phụ trách ký: "Mở cho toàn đội" / "Sửa rồi chạy thử thêm" / "Dừng".
