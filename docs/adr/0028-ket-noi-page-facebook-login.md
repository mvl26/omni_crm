# 0028 · Nút "Connect Facebook Page": kết nối Page bằng Facebook Login for Business, đăng ký webhook giữ trường đang có

- Ngày: 2026-11-14
- Trạng thái: Đề xuất (chờ người phụ trách duyệt)
- Prompt: không có prompt kit; yêu cầu của người phụ trách ngày 14/11/2026 ("Việc tiếp theo là làm trang Connect Facebook Page trong CRM"), theo hồ sơ App Review `F:\DOC\Meta_App_Review_Miyano_Intake_20261113_V1.2` mục 4
- Liên quan: ADR 0022 (kênh Meta Messenger), ADR 0023, ADR 0027; `spd/docs/12`, `spd/docs/21`; miyano-intake nhánh `deploy/yhct` (`intake/channels/meta.py::PAGE_FIELDS`)

## Bối cảnh

Đã kiểm (13–14/11/2026):

- Ứng dụng Meta 1012569668539635 đã Live, doanh nghiệp đã xác minh 12/11, nhưng mọi quyền vẫn ở Standard Access: chỉ người có vai trò trong ứng dụng nhắn được vào CRM. Phải qua App Review cho `pages_messaging`, `pages_manage_metadata`, `pages_show_list`.
- Trang tài liệu của cả ba quyền yêu cầu video quay toàn bộ bước đăng nhập Facebook và cảnh người dùng cấp quyền. CRM chạy bằng token System User dán tay vào `meta_page_access_token`, nên không có cảnh đó; `pages_show_list` và `pages_manage_metadata` cũng không có màn hình nào dùng chúng.
- Sản phẩm Facebook Login for Business đã được thêm vào ứng dụng nhưng chưa có Configuration nào (kết quả kiểm C2 ngày 13/11).
- Webhook của Page trỏ về miyano-intake. Page đang đăng ký các trường trong `PAGE_FIELDS` của intake nhánh `deploy/yhct`: `messages, message_echoes, message_reactions, messaging_postbacks, messaging_referrals, standby, leadgen`. `POST /{page}/subscribed_apps` nhận một danh sách trường và danh sách này thay cho danh sách cũ, nên gửi thiếu `leadgen` là Lead Ads ngừng vào CRM.
- Đã có khuôn OAuth phía máy chủ cho Zalo (`api/webhooks.py::zalo_oauth_start/callback`, `channels/zalo/token.py`): state trong cache, chuyển hướng toàn trang, chỉ Omni Admin.

## Các lựa chọn

### A. Cách đăng nhập
1. **OAuth phía máy chủ** (hộp thoại `/dialog/oauth` với `config_id`, đổi `code` bằng App Secret trên máy chủ) — App Secret không ra trình duyệt, cùng khuôn với Zalo. Nhược: phải khai đúng URL callback trong Facebook Login for Business.
2. JavaScript SDK (`FB.login`) trong Desk — phải nạp script của Meta vào Desk, token người dùng nằm ở trình duyệt.

### B. Dùng token lấy được vào việc gì
1. **Chỉ để kết nối**: liệt kê Page, đăng ký webhook, đọc lại kết quả. Không lưu token Page; vận hành vẫn dùng token System User — không đổi đường gửi tin đang chạy, token không phụ thuộc mật khẩu của một người.
2. Thay token System User bằng token Page vừa lấy — một nguồn token duy nhất, nhưng token gắn với tài khoản Facebook của người bấm nút, đổi mật khẩu hay rời công ty là kênh dừng.

### C. Trường webhook
1. **Đọc trường đang có, gộp với `PAGE_FIELDS`, rồi mới POST** — không làm rơi trường của tính năng khác.
2. POST đúng danh sách của Messenger — đơn giản nhưng xóa `leadgen`.

### D. Chỗ đặt nút
1. **Form Omni Channel loại Meta Messenger** (nơi đã có App ID, App Secret, Page ID), cùng chỗ với nút "Kết nối Zalo OA".
2. Trang riêng trong giao diện CRM (`/crm`) — phải vá giao diện app `crm`, khối lượng lớn.

## Quyết định

Chọn A1, B1, C1, D1.

- Ô mới `meta_login_config_id` nhận Configuration ID. Khối "Facebook Page connection" (chỉ hiện với kênh Meta Messenger) có thêm 4 ô chỉ đọc: `meta_page_name`, `meta_connected_at`, `meta_connected_by`, `meta_subscribed_fields`.
- Luồng: nút → `api.meta_connect.start` → hộp thoại Facebook → `api.meta_connect.callback` → form kênh với `?fb_connect=<phiên>` → hộp "Select the Facebook Page to connect" (`api.meta_connect.pages`, chỉ trả mã và tên Page) → "Subscribe Page" (`api.meta_connect.subscribe`) → thông báo "Facebook Page connected" kèm trường đã đăng ký, dòng xanh "Connected to … · Webhook fields: …".
- An toàn: mọi hàm đòi Omni Admin. State dùng một lần và gắn với người bấm nút; user token chỉ nằm trong cache tối đa 10 phút và bị xóa sau khi đăng ký; không trả token cho trình duyệt; không ghi token vào log hay thông điệp lỗi. Không đổi Page đã gắn với kênh: chọn Page khác thì báo lỗi. Sandbox bật thì không gọi Meta.
- Chuỗi hiện cho người dùng viết bằng tiếng Anh, có bản dịch trong `translations/vi.csv`, vì video App Review quay giao diện tiếng Anh.

## Hệ quả

- Việc phía Meta (người làm, trước khi dùng nút): tạo một Configuration trong Facebook Login for Business, loại token "User access token", tài sản Pages, quyền `pages_show_list`, `pages_manage_metadata`, `pages_messaging`; khai `https://yhct.miyano.com.vn/api/method/omni_crm.api.meta_connect.callback` vào Valid OAuth Redirect URIs; dán Configuration ID vào kênh `messenger-mvl`. Nếu hộp thoại báo URL bị chặn vì URL callback CRM tự dựng ra `http://` hoặc kèm cổng (proxy không gửi `X-Forwarded-Proto`), đặt site_config `omni_meta_oauth_redirect_uri` bằng đúng URL đã khai. Trường `feed` (bình luận, đợt 2 App Review) chưa nằm trong `PAGE_FIELDS`; khi làm đợt 2 thì thêm vào cả intake lẫn đây.
- `PAGE_FIELDS` phải giữ trùng với miyano-intake `deploy/yhct`. Đổi một bên thì đổi bên kia.
- Hồ sơ App Review phải ghi rõ: nút này dùng Facebook Login for Business để kết nối Page; gửi và nhận tin hằng ngày dùng token System User (Meta dặn ứng dụng dùng System User phải nói rõ).
- `tests/test_v2_02_checks.py` có thêm 4 hàm whitelisted của `api/meta_connect.py`.
- Chưa thử với Meta thật: hộp thoại, đổi code và `POST subscribed_apps` mới kiểm bằng giả lập. Lần chạy thật đầu tiên trên yhct là phép thử.
