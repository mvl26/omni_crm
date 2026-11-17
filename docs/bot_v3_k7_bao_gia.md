# Bot báo giá (K7): đã làm gì, còn thiếu gì, bật thế nào

Cập nhật 17/11/2026. Tài liệu này dành cho người phụ trách kinh doanh. Kịch bản gốc: `docs/kich_ban_bot_miyano_v3.md` mục 3.3.

## 1. Hiện trạng

Bot **chưa tự báo giá**. Khi khách xin báo giá, bot hỏi các thông tin cần thiết, gửi một câu hẹn, rồi giao việc cho nhân viên báo giá. Bot không nêu bất kỳ con số nào về giá, chiết khấu hay phí.

Lý do: chưa có bảng giá được duyệt cho bot dùng và chưa nối với ERP. Ba công tắc báo giá (vật tư, SupplyCore, SPD) đều đang tắt. Kể cả khi ai đó bật nhầm công tắc, bot vẫn không báo giá, vì hệ thống còn kiểm xem đã đủ dữ liệu ở mục 2 chưa.

## 2. Những thứ còn cần anh Xuân cung cấp

Bot chỉ báo giá được khi có đủ các mục dưới đây (kịch bản mục 3.3.5).

Vật tư y tế:

- [ ] Bảng giá bán bot được dùng: tên Bảng giá (Price List) trên ERP.
- [ ] Cách ghi VAT trong báo giá: giá đã gồm hay chưa gồm VAT, và câu chữ ghi thế nào.
- [ ] Phí vận chuyển: đã gồm hay tính riêng, và câu chữ ghi thế nào.
- [ ] Ngưỡng giá trị đơn bot được tự báo. Đơn vượt ngưỡng thì nhân viên báo.

Phần mềm SupplyCore:

- [ ] Tiêu chí chia quy mô: xác nhận ba câu hỏi và các mốc đang dùng trong kịch bản (số mặt hàng, số người dùng, số cơ sở hoặc kho).
- [ ] Bảng giá theo bậc: tên từng gói, điều kiện quy mô, giá, kỳ thanh toán, nội dung gói, phí triển khai và đào tạo.

Hệ thống SPD:

- [ ] Tiêu chí chia quy mô: xác nhận ba câu hỏi và các mốc (số cơ sở y tế, số mặt hàng, giá trị vật tư mỗi tháng).
- [ ] Cách tính phí: theo phần trăm giá trị vật tư hay phí cố định theo bậc, và mức cụ thể.

Chung cho cả ba dịch vụ:

- [ ] Số ngày hiệu lực của một báo giá.
- [ ] Báo giá PDF: bot tự gửi ngay, hay nhân viên duyệt rồi mới gửi. Hệ thống đang để mặc định là nhân viên duyệt trước.
- [ ] Tài khoản kết nối ERP để bot đọc giá và tạo Báo giá (Quotation). Tài khoản này chỉ cần quyền đọc bảng giá bán và tạo báo giá, không cần quyền xem giá mua vào.

## 3. Đã xây xong

- Ba công tắc bật riêng cho từng dịch vụ trong Omni Settings, mặc định tắt: `script_quote_enabled_vattu`, `script_quote_enabled_supplycore`, `script_quote_enabled_spd`.
- Công tắc "nhân viên duyệt báo giá trước khi gửi" (`script_quote_review_required`), mặc định bật.
- Bảng kiểm đầu vào: hệ thống liệt kê từng mục ở phần 2 và báo mục nào đã có, mục nào thiếu. Hiện tất cả đều thiếu.
- Đường dự phòng: khi bot chưa được phép báo giá, bot gửi câu hẹn "nhân viên sẽ gửi báo giá" và tạo việc cho nhân viên phụ trách dịch vụ. Việc này ghi kèm các câu trả lời khách đã cho: mặt hàng, số lượng, thời gian cần hàng, quy mô.
- Chốt chặn con số: trước khi một câu báo giá được gửi, hệ thống so từng con số trong câu với bảng giá và cấu hình. Có con số lạ thì câu bị chặn. Chốt này để sẵn cho lúc bật báo giá, nhằm bảo đảm bot không tự nghĩ ra giá.
- Các câu báo giá trong kho kịch bản (`BG_VT_KETQUA`, `BG_SC_KETQUA`, `BG_SPD_KETQUA`) đã nhập nguyên văn và đang ở trạng thái "Tắt".

## 4. Chưa xây

Các phần này chờ dữ liệu ở phần 2:

- Đọc giá từ ERP và đối chiếu tên hàng khách gõ với danh mục hàng.
- Tính tổng tiền, chọn gói theo bậc quy mô, tính phí SPD.
- Tạo Báo giá trên ERP và gửi file PDF.
- Các trường nhập những mục ở phần 2 vào Omni Settings.

## 5. Cách bật sau này

1. Anh Xuân cung cấp đủ các mục ở phần 2 cho một dịch vụ. Có thể làm từng dịch vụ, không cần chờ cả ba.
2. Đội kỹ thuật nhập dữ liệu, xây phần đọc giá cho dịch vụ đó, rồi thử với danh sách khách thử.
3. Truyền thông duyệt câu báo giá trong kho kịch bản, chuyển ô từ "Tắt" sang "Đang dùng".
4. Bật công tắc của dịch vụ đó. Nên giữ "nhân viên duyệt trước khi gửi" trong giai đoạn đầu.
5. Cần dừng thì tắt công tắc. Bot quay về cách hiện tại: hẹn khách và giao nhân viên báo giá.

Những việc luôn do nhân viên làm, kể cả sau khi bật: thương lượng, chiết khấu, giá dự thầu, điều khoản thanh toán, đơn vượt ngưỡng, khách đã có giá theo hợp đồng riêng.
