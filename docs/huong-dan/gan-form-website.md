# Gắn form website/landing page vào Omni CRM

Dành cho đội web. Form `LeadForm` của bộ nhận diện Miyano hiện gửi vào ERPNext Lead; chuyển sang gửi tới Omni CRM theo hướng dẫn dưới đây. **Chưa thử trên trang thật** (V2-05 chỉ kiểm bằng test tự động).

## 1. Chuẩn bị phía CRM (quản trị viên)

1. Tạo `Omni Channel` loại **Web Form** (bật `enabled`). Tên kênh dùng làm tham số `ch`.
2. Tạo `Omni Lead Form` nền tảng **Website**: `Mã biểu mẫu` = giá trị ô `form` mà trang gửi (BẮT BUỘC: form có mã chưa cấu hình bị bỏ qua và trả thành công giả, trừ khi quản trị bật `webform_accept_unknown_form`); thêm dòng ánh xạ nếu tên ô khác tên mặc định (bảng ở mục 3); đặt `Người phụ trách mặc định` nếu cần; chọn chiến dịch mặc định hoặc đặt `utm_campaign` trên `Omni Campaign` để tự khớp.
3. Nếu website khác tên miền với CRM: cấu hình CORS của site (`allow_cors` trong `site_config.json`) cho đúng tên miền website. Không dùng `*`.

## 2. Gửi form

`POST https://<crm>/api/method/omni_crm.api.webhooks.web_form?ch=<tên kênh>` với JSON (hoặc form-data), tối đa 64 KB.

```html
<form id="lead" method="post">
  <input name="full_name"> <input name="phone"> <input name="email">
  <input name="company_name"> <input name="tax_id"> <input name="message">
  <input type="hidden" name="form" value="landing-benh-vien-2026">
  <!-- Ô bẫy bot: ẩn bằng CSS (không dùng type=hidden), người thật không thấy và không điền -->
  <input name="website_url" tabindex="-1" autocomplete="off" style="position:absolute;left:-9999px">
  <label><input type="checkbox" name="consent_contact" value="1"> Tôi đồng ý để Miyano liên hệ</label>
</form>
<script src="https://<crm>/assets/omni_crm/js/omni_utm.js" defer></script>
<script>
document.getElementById("lead").addEventListener("submit", function (ev) {
  ev.preventDefault();
  OmniUtm.attach(this);                       // thêm utm_*, gclid, fbclid, landing_page, referrer, submission_id
  var data = Object.fromEntries(new FormData(this).entries());
  fetch("https://<crm>/api/method/omni_crm.api.webhooks.web_form?ch=<tên kênh>", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
  }).then(function (r) { return r.json(); }).then(function (j) {
    // j.message.submission_id: hiển thị lời cảm ơn; mã này chống gửi trùng
  });
});
</script>
```

- **Ô đồng ý không đánh dấu sẵn** (Luật 91/2025/QH15, Nghị định 356/2025/NĐ-CP): `consent_contact` chỉ có giá trị khi khách tự tích. Đồng ý từ form ẩn danh chỉ nằm trên dòng thời gian của Lead, KHÔNG nâng đồng ý của Contact có sẵn. MST khai trên form là tín hiệu yếu: không tự gắn vào nhà thầu có sẵn (chỉ ghi chú cho người xử lý). Hệ thống không gửi gì cho khách.
- Mã trả về: `message.submission_id`. Hệ thống không trả thông tin nội bộ.
- Giới hạn: mặc định 5 lần/10 phút mỗi IP và 300 lần/giờ toàn kênh (Omni Settings), quá thì trả 429. Ô bẫy bị điền: trả thành công giả, không lưu.
- Chống bot bằng Turnstile/reCAPTCHA (tùy chọn, tắt mặc định): đặt `Web Form: chống bot` + khóa bí mật trên kênh, và gửi token trong `captcha_token` (hoặc `cf-turnstile-response` / `g-recaptcha-response`).

## 3. Tên ô mặc định

`full_name, name`, `phone, phone_number, mobile`, `email`, `company, company_name`, `tax_id, mst`, `job_title`, `province, city`, `hospital_count`, `message, note`, `consent_contact`, `consent_marketing`. Ô khác khóa: thêm dòng ánh xạ ở Omni Lead Form, hoặc để nguyên (được lưu nguyên văn và hiện trên dòng thời gian).
