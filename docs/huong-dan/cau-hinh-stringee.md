# Cấu hình Stringee cho kênh tổng đài

Dành cho người IT. Nguồn: developer.stringee.com (đối chiếu 2026-10-30). Mục "Chưa xác nhận" ở cuối nêu điều cần kiểm bằng cuộc gọi thử.

## 1. Chuẩn bị (phía CRM)

1. Vào Omni Channel, tạo kênh: Loại kênh = Telephony, Nhà cung cấp = Stringee, Đầu số (hotline) = số của Stringee.
2. Điền API key SID và API key secret (lấy ở Stringee Dashboard, mục API Keys). Hai trường này lưu dạng mật khẩu, không hiện lại.
3. Đặt `webhook_token`: chuỗi ngẫu nhiên từ 32 ký tự (ví dụ `openssl rand -hex 24`).
4. Điền bảng Omni Agent Map: `agent_ref` = số/ID máy nhánh hoặc user phía Stringee, `user` = nhân viên CRM.
5. Trên staging bật Omni Settings > Chế độ thử (sandbox): khi đó CRM không gọi API Stringee (Call log, ghi âm), nhưng vẫn nhận webhook và tra cứu.
6. Bật kênh (`enabled`) sau cùng.

## 2. Cấu hình trên trang quản trị Stringee

Thay `<site>` bằng tên miền CRM, `<kênh>` bằng tên Omni Channel (dùng `%20` cho khoảng trắng), `<token>` bằng `webhook_token`.

| Mục | Giá trị |
| --- | --- |
| URL sự kiện (`event_url`, đặt ở Number / Project) | `https://<site>/api/method/omni_crm.api.webhooks.telephony_event?ch=<kênh>&t=<token>` |
| URL tra cứu khách (`get_customer_info_url`) | `https://<site>/api/method/omni_crm.api.webhooks.telephony_lookup?ch=<kênh>&t=<token>` |
| Timeout tra cứu (`get_customer_info_timeout`) | 300 (mili-giây). CRM trả lời trong dưới 300 ms; quá hạn thì Stringee bỏ qua tra cứu, cuộc gọi vẫn chạy bình thường |

Lưu ý:
- Stringee gửi tra cứu bằng GET với `from`, `to`, `project`, nên URL tra cứu đã có `ch`/`t` rồi Stringee nối thêm tham số bằng `&`.
- Cả hai URL phải là HTTPS công khai. Token nằm trong query: cấu hình nginx/proxy để không ghi query vào access log.
- Cấu hình URL qua Call settings API (`icc-call-settings`) hoặc trang quản trị tương ứng; tên trường là `get_customer_info_url`, `get_customer_info_timeout`, `event_url`.

## 3. Chữ ký webhook

Stringee ký sự kiện (header `X-Stringee-Signature`, HMAC-SHA1 base64 của body) bằng "project's signing secret key" (theo tài liệu). CRM hiện kiểm bằng API key secret của kênh, nhiều khả năng KHÁC khóa ký của dự án: có header mà sai thì trả 401. Yêu cầu không có header vẫn được nhận nếu token đúng (giảm nhẹ tạm thời).

**ĐIỀU KIỆN CHẶN: không bật Stringee production cho đến khi staging xác nhận khóa ký (docs/12, S1)** hoặc có ADR thêm trường khóa ký riêng. Nếu mọi sự kiện thật bị 401 (Error Log "Omni webhook: xac_thuc_that_bai") thì khóa khác nhau. Để trống API key secret chỉ là cách tạm và có hệ quả: bỏ kiểm chữ ký nhưng mất JWT, nên không gọi được Call log, ghi âm, xác minh cuộc gọi.

## 4. Ghi âm (Đợt 3, chưa bật)

Khi cần: thêm hành động `record` (`format` mp3 hoặc wav, `eventUrl` trỏ về URL sự kiện ở trên) trước `connect` trong SCCO của answer URL. Stringee giữ file 30 ngày; CRM sẽ tải ngay sau cuộc gọi.

## 5. Thử

1. Gọi từ một điện thoại vào đầu số: chuông reo, CRM nhận yêu cầu tra cứu (xem tab Network/nginx log: HTTP 200).
2. Gác máy: trong vài chục giây có Omni Call, tin trong Hộp thư và CRM Call Log gắn Lead.
3. Gọi và để nhỡ: Omni Call trạng thái Missed.
4. Gửi lại cùng sự kiện: không tạo bản ghi thứ hai.

## 6. Đổi `webhook_token` khi lộ

1. Tạo token mới (≥ 32 ký tự), lưu vào Omni Channel.
2. Sửa `t=` trong CẢ HAI URL (sự kiện và tra cứu) trên Stringee.
3. Từ lúc lưu đến lúc sửa xong, sự kiện bị 401 và tra cứu trả "khách mới". Sự kiện bị bỏ sẽ được đối soát đêm bù lại (D2-03). Làm ngoài giờ cao điểm.
4. Nếu nghi API key lộ: tạo API key mới ở Stringee, nhập SID/secret mới vào kênh, xóa key cũ.

## 7. Chưa xác nhận (xem docs/12 mục B3)

- Định dạng JSON phản hồi tra cứu do ta tự định (`found`, `customer_name`, `lead`, `owner_name`, `lead_url`); Stringee chuyển nguyên vào `customDataFromYourServer`.
- Bí mật ký webhook có đúng là API key secret không (điều kiện chặn production).
- `id` trong Call log có trùng `call_id` của sự kiện không.
- Chưa thử với tài khoản Stringee thật.
