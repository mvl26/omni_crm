# 06 · Kênh Zalo OA (Đợt 1)

> **Cảnh báo cho agent:** Zalo thay đổi API và chính sách khá thường xuyên. Mọi chi tiết dưới đây (tên sự kiện, công thức chữ ký, endpoint, khung thời gian gửi) phải được **đối chiếu với tài liệu chính thức tại developers.zalo.me** trước khi code. Khác biệt ghi vào `docs/12-quyet-dinh-mo.md`. Các giá trị dễ thay đổi phải nằm trong cấu hình, không viết cứng.

## 1. Điều kiện trước

- OA đã xác thực; ứng dụng tạo trên Zalo for Developers và liên kết với OA.
- Tên miền webhook đã được xác thực với Zalo; webhook dùng HTTPS; server đặt tại Việt Nam để nhận đủ thông tin người dùng (theo khuyến nghị của Zalo).
- Webhook phải trả **200 trong vòng 5 giây** — cổng tiếp nhận của ta nhắm dưới 1 giây.
- Bật các sự kiện cần: người dùng gửi tin (văn bản, ảnh, tệp, sticker, vị trí), quan tâm/bỏ quan tâm, người dùng gửi thông tin (form yêu cầu thông tin), và sự kiện OA gửi tin (để ghi nhận tin nhân viên trả lời trực tiếp trên ứng dụng Zalo OA).

## 2. Cấp quyền và token

- Lấy token lần đầu bằng luồng OAuth v4 cho OA: trang cấp quyền `https://oauth.zaloapp.com/v4/oa/permission` → Zalo gọi về callback của ta với `code` → đổi lấy token tại `https://oauth.zaloapp.com/v4/oa/access_token`.
- Callback: `GET /api/method/omni_crm.api.webhooks.zalo_oauth_callback` — chỉ Omni Admin đã đăng nhập mới khởi động được luồng; kiểm tra `state` chống giả mạo; hỗ trợ PKCE nếu tài liệu yêu cầu.
- Làm mới: gọi cùng endpoint token với `grant_type=refresh_token`, `app_id`, `refresh_token`, header `secret_key`. **Mỗi lần làm mới nhận refresh token mới** → lưu ngay cả hai token và `zalo_token_expires_at` trong một transaction, trước khi dùng.
- Tác vụ `refresh_if_needed` mỗi 30 phút: làm mới khi còn dưới 2 giờ là hết hạn. Lỗi làm mới → cảnh báo ngay (email `alert_recipients`) vì quá hạn là mất khả năng gửi tin.
- Dùng khóa Redis `omni:zalo:refresh:{channel}` để hai tiến trình không làm mới cùng lúc (làm mới đồng thời sẽ làm hỏng refresh token).

## 3. Xác thực webhook

`channels/zalo/signature.py::verify(raw_body, headers, app_id, oa_secret_key) -> bool`

- Header chữ ký: `X-ZEvent-Signature`.
- Công thức thường được mô tả: `mac = sha256(app_id + raw_body + timestamp + oa_secret_key)` (hex), header có dạng `mac=<hex>`, `timestamp` lấy từ payload. **Xác nhận công thức chính xác trên tài liệu chính thức** và viết test bằng ví dụ trong tài liệu.
- So sánh bằng `hmac.compare_digest`.
- Từ chối nếu `timestamp` lệch quá 10 phút so với giờ máy chủ (chống phát lại); vẫn ghi nhận số lần từ chối để giám sát.
- **Công cụ chẩn đoán (V21-05, ADR 0015):** khi cần xác nhận công thức trên OA thật, bật `omni_zalo_signature_debug_until` trong site_config (tối đa 2 giờ kể từ lúc bật) để ghi các webhook bị từ chối vào tệp riêng tư; xem `docs/huong-dan/04-kenh-zalo-oa.md` mục 3A. Công thức KHÔNG đổi. **Trạng thái đối chiếu tài liệu: trang webhook của Zalo for Developers là SPA, bản đọc bằng máy không có nội dung nên CHƯA xác nhận Z1** (docs/12 C-V21-05).
- Có cờ cấu hình `zalo_skip_signature` **chỉ cho site dev**; production bật cờ này thì cổng từ chối khởi động xử lý và cảnh báo.

## 4. Ánh xạ sự kiện

Tên sự kiện cần xác nhận lại; bảng dưới là ánh xạ mong muốn:

| Sự kiện Zalo (tham khảo) | `event_type` nội bộ | Xử lý |
| --- | --- | --- |
| `user_send_text` | message | Văn bản → `text` |
| `user_send_image`, `user_send_file`, `user_send_sticker`, `user_send_location`, `user_send_audio` | message | `text` = mô tả ngắn ("[Ảnh]", "[Tệp: tên]"); đính kèm vào `attachments` |
| `user_submit_info` | user_info | SĐT, tên, địa chỉ khách gửi → `sender_profile`, tín hiệu **mạnh** |
| `follow` / `unfollow` | follow / unfollow | Cập nhật `Omni Identity.is_follower`; follow tạo Lead **chỉ khi** cấu hình cho phép (mặc định: không tạo Lead chỉ vì follow) |
| `oa_send_text`, `oa_send_image`… | oa_echo | Tin nhân viên gửi từ ứng dụng Zalo OA, không qua CRM → ghi vào dòng thời gian như tin ra `source = Sale (ngoài hệ thống)`; dùng làm "câu sale trả lời" để so với bot |
| Sự kiện khác | other | Lưu, `status = Ignored` |

`external_message_id` = mã tin (`message.msg_id`) nếu có; với follow/unfollow không có mã tin thì băm (`event_name`, `user_id`, `timestamp`).

`external_sender_id` = `sender.id` (user id theo OA). Với `oa_echo`, người nhận mới là khách → dùng `recipient.id`.

## 5. Hồ sơ người dùng

Lần đầu gặp một user id: gọi API lấy thông tin người dùng của OA (tên hiển thị, ảnh, thông tin khách đã chia sẻ nếu có) **trong worker**, không trong webhook. Lưu vào `Omni Identity`. Lỗi gọi API không làm hỏng lượt xử lý (chỉ ghi log).

## 6. Xin SĐT

Để gộp được với email và điện thoại, cần SĐT. Cách làm: gửi tin tư vấn mẫu **yêu cầu thông tin người dùng** (request user info). Quy tắc:

- Chỉ gửi khi Lead chưa có SĐT và `Omni Identity.phone_requested_at` trống hoặc cũ hơn 7 ngày; gửi xong ghi lại trường này.
- Đợt 1: sale bấm nút "Xin SĐT qua Zalo" trên hộp thư. Đợt 3: bot được phép tự gửi theo cấu hình.
- Khách gửi form → sự kiện `user_info` → tín hiệu mạnh.

## 7. Gửi tin

- Endpoint tin tư vấn: `POST https://openapi.zalo.me/v3.0/oa/message/cs`, header `access_token`, body `{"recipient":{"user_id":…},"message":{"text":…}}`. Xác nhận phiên bản API hiện hành.
- `can_send_now(identity, channel)` dựa trên `identity.last_inbound_at`:

| Thời gian từ tương tác cuối | Hành vi mặc định |
| --- | --- |
| ≤ `zalo_cs_free_hours` (48 giờ) | Gửi |
| Đến `zalo_cs_max_days` (7 ngày) | Có thể có phí: **chỉ sale** được gửi, có hộp xác nhận "Tin này có thể bị tính phí"; bot không gửi |
| Quá 7 ngày | `Blocked`, gợi ý sale liên hệ kênh khác |

Hai mốc 48 giờ và 7 ngày là THAM SỐ của kênh (`zalo_cs_free_hours`, `zalo_cs_max_days`), không phải sự thật đã xác nhận. Trang giá chính thức của Zalo (xem 7a) chỉ nói về "hạn mức tin Tư vấn ngoài khung 48h có sẵn của gói OA" và phí 55 đ/tin sau hạn mức; **trang không nói gì về mốc 7 ngày** (cả "7 ngày có phí" lẫn "sau 7 ngày bị từ chối" đều CHƯA xác nhận). Mức phí và việc có bị từ chối hay không theo gói và hạn mức: xác nhận với Zalo và hợp đồng OA của công ty. Từ 01/01/2026 các loại tin mẫu (ZNS, tin giao dịch, truyền thông) hợp nhất thành ZBS Template Message theo tài liệu cộng đồng (chưa đối chiếu chính thức).

### 7a. Gói OA và điều kiện API (V21-05)

Từ 1/6/2026 Zalo áp dụng bốn gói OA: Cơ bản, Tiêu chuẩn, Tăng trưởng, Toàn diện. Đã đối chiếu https://zalo.solutions/oa/pricing ngày 2026-11-03:

- **Điều kiện API (chính thức):** "Để liên kết ứng dụng với OA thông qua API, bạn cần sử dụng Gói tăng trưởng hoặc toàn diện." Vì vậy Cơ bản và Tiêu chuẩn có thể KHÔNG cho kết nối API, tức kênh này không chạy được trên hai gói đó.
- **Hạn mức tin tư vấn ngoài khung 48h:** hàng "Số lượng tin tư vấn ngoài khung 48h có sẵn/tháng" có hai giá trị 500 và 2.000; phí ngoài hạn mức "55 đ/tin". Trang (bản đọc được bằng máy) KHÔNG gắn giá trị với gói; các bài của bên thứ ba (Subiz, MWY Consulting) ghi 500 cho Tăng trưởng và 2.000 cho Toàn diện. Dùng làm gợi ý khi điền `zalo_cs_paid_quota_month`, vẫn phải đối chiếu trong trang quản trị OA.
- **Chưa xác nhận:** gói thật của OA công ty; việc tin gửi bằng API và tin gửi thẳng trên ứng dụng Zalo OA có cùng tính vào hạn mức không; tin nào bị tính là "tư vấn ngoài khung" khi follow/unfollow.

Cách hệ thống xử lý (không chặn gì, chỉ báo):

1. `Omni Channel.zalo_oa_package` (Chưa xác nhận / Cơ bản / Tiêu chuẩn / Tăng trưởng / Toàn diện, mặc định Chưa xác nhận) và `zalo_cs_paid_quota_month` (trống hoặc 0 = chưa biết).
2. Hộp thư, dưới khung soạn tin: "Đã dùng X / Y tin ngoài 48 giờ tháng này" hoặc "Đã gửi X tin ngoài 48 giờ tháng này (chưa biết hạn mức)". Đếm tin Sale đã gửi qua API (Sent, không sandbox, có `sent_by`) mà tại lúc gửi khách không nhắn trong `zalo_cs_free_hours` giờ trước (trừ `oa_echo`, `unfollow`), theo tháng lịch của site (`channels/zalo/quota.py`). Vượt hạn mức chỉ đổi màu chữ, không chặn gửi.
3. `/app/omni-monitor`, khối kênh: gói Chưa xác nhận, Cơ bản, Tiêu chuẩn hiện "Gói OA có thể chưa cho kết nối API". Không chặn kênh vì kết quả thật quyết định bằng phép thử trên OA.

- Ánh xạ mã lỗi trả về: người dùng chặn/bỏ quan tâm, quá khung, token hết hạn (→ làm mới rồi thử lại một lần), vượt hạn mức (→ thử lại sau). Bảng ánh xạ để trong `channels/zalo/errors.py`, có test.
- Độ dài văn bản tối đa theo giới hạn Zalo; vượt thì tách tin, giữ thứ tự.

## 8. Hiển thị hội thoại cho sale

App `crm` hiển thị email, bình luận, cuộc gọi, ghi chú, công việc trên Lead. Zalo chưa có chỗ riêng. Không sửa app `crm`, nên chọn một trong ba cách (agent làm spike ở D1-06, ghi ADR):

| Cách | Mô tả | Ưu | Nhược |
| --- | --- | --- | --- |
| A | Ghi mỗi tin Zalo thành `Communication` với `communication_medium` phù hợp (ví dụ `Chat`) tham chiếu Lead | Hiện trong hoạt động nếu CRM liệt kê loại này | Phải kiểm tra CRM có hiển thị không |
| B | Ghi mỗi tin thành `Comment` trên Lead, có tiền tố kênh | Chắc chắn hiện | Lẫn với bình luận nội bộ |
| C | Trang **Hộp thư Omni** riêng (`/app/omni-inbox` hoặc trang web trong app) có danh sách hội thoại, khung trả lời, liên kết hai chiều với Lead; kèm A hoặc B để Lead vẫn có dấu vết | Trải nghiệm trả lời tốt, có chỗ cho nút xin SĐT và duyệt nháp bot (Đợt 3) | Thêm một màn hình |

Đề xuất mặc định: **C + A** (A nếu CRM hiển thị được, nếu không thì C + B).

**Đã chọn ở D1-06: C + A** (CRM hiển thị được Communication `Chat`; xem `docs/adr/0004-cach-hien-thi-hoi-thoai-zalo.md`).

**Cập nhật V3-01 (ADR 0016): tab "Zalo" trong trang Lead/Deal.** Ngoại lệ có chủ đích duy nhất của quy tắc "không sửa `crm`": một patch **chỉ giao diện** (frontend) trên crm v1.73.0 thêm tab **Zalo** (cạnh Hoạt động/Email/WhatsApp) với:

- Hội thoại dạng bong bóng: khách bên trái; nhân viên, bot và nhân viên trả lời ngoài hệ thống (`oa_echo`) bên phải; đường phân cách ngày, giờ từng tin; nhãn **Sale** / **Bot**; tin sandbox có nhãn "Thử nghiệm (sandbox), chưa gửi tới khách"; tin đang gửi/lỗi/bị chặn có dòng trạng thái; đính kèm là liên kết chỉ khi `http(s)://`; nội dung khách hiện bằng văn bản thuần (không HTML).
- Khung soạn + nút **Gửi qua Zalo** luôn đi qua `omni_crm.api.zalo_tab.send` -> `inbox.send_zalo` -> Omni Outbound Message -> adapter Zalo (đúng kênh và định danh Zalo của Lead, kể cả khi Lead đã thành Deal). Mọi guard giữ nguyên ở máy chủ: sandbox, khung 48 giờ/7 ngày (xác nhận khi có phí), hạn mức, từ chối liên hệ, độ dài. Lead không có định danh Zalo: khung soạn khóa kèm giải thích; Lead có cả Zalo và email: chỉ gửi qua Zalo, không bao giờ gửi email từ tab này.
- Cập nhật tức thời qua sự kiện realtime `omni_inbox_update` (chỉ có `{lead, channels}`, không có nội dung) và tải lại qua API có kiểm quyền; thăm dò dự phòng 60 giây.
- Quyền: cần quyền trên tài liệu đang mở **và** trên Lead gốc (như Hộp thư). Tắt bằng `Omni Settings.zalo_tab_disabled` (docs/09). Chưa có trên giao diện di động của CRM.

**Nhắn từ trang chính thức của Zalo OA (V3-01, bổ sung).** OA gói Cơ bản không gửi được qua API (Zalo trả `-224`; docs/12 C-V3-01b), nên nhân viên nhắn từ trang chat của OA. Trong tab Zalo có nút **Mở chat Zalo OA** (cửa sổ mới 480 px, `window.open` với `noopener,noreferrer`) mở **đúng hội thoại của khách** bằng `https://oa.zalo.me/chat?uid=<mã người dùng Zalo>&oaid=<mã OA>` (**`oaid` viết thường**; viết `oaId` thì Zalo bỏ tham số; đã kiểm trên trình duyệt thật). `uid` = `Omni Identity.external_id` của định danh Zalo gắn Lead; `oaid` = `Omni Channel.zalo_oa_id` của kênh khách đã nhắn; cả hai chỉ nhận chuỗi số, thiếu hoặc không phải số thì mở trang chat chung `https://oa.zalo.me/chat` kèm dòng hướng dẫn tìm khách theo **tên** hoặc **mã Zalo** (rút gọn, có nút **Sao chép**). **Nhúng iframe `oa.zalo.me/chat` KHÔNG khả thi** (Chrome chặn, thử 2026-11-04) nên không làm. Tin nhắn từ trang OA quay lại Lead nhờ sự kiện `oa_echo` (bong bóng bên phải). Khách đã từ chối liên hệ hoặc Lead không có định danh Zalo: không hiện khối này. Khi OA nâng gói, khung gửi qua API trong tab hoạt động bình thường. Mã lỗi `-224` hiện thành "Gói OA hiện tại không hỗ trợ gửi tin qua API; hãy nhắn từ trang Zalo OA".

**Tên khách Zalo.** Tab và dòng thời gian hiện tên theo thứ tự `Omni Identity.display_name`, rồi tên Lead (sale sửa `first_name` của Lead được), rồi mã rút gọn; không bao giờ hiện mã số dài. Tên Zalo lấy bằng `fetch_profile` (`channels/zalo/worker_hooks.py`) khi tin tới; **không lấy được khi**: sandbox bật (không gọi API), kênh chưa có access token, Zalo trả lỗi (đặt thời gian chờ `PROFILE_COOLDOWN` 600 giây; gói Cơ bản có thể chặn cả API hồ sơ, `-224`), hoặc Zalo không trả tên. Lý do gần nhất được lưu (bộ nhớ đệm Redis, 30 ngày; không thêm trường DocType) và tab hiện "Chưa lấy được tên Zalo: <lý do>". Cập nhật tay cho khách thiếu tên, sau khi OA đủ gói và đã tắt sandbox: `bench --site <site> execute omni_crm.channels.zalo.profile_refresh.run` (xem trước) rồi `--kwargs "{'dry_run': 0}"` (bỏ qua thời gian chờ; không đổi tên Lead).

Tên người gửi tin vào ở dòng thời gian: `Omni Identity.display_name`, rồi tên Lead/Deal (ví dụ "Khách Zalo OA 8383"), rồi mã rút gọn `Zalo OA …8383`; không bao giờ hiện mã số thô. Tin cũ sửa bằng lệnh chạy tay `omni_crm.ops.backfill_sender.run` (mặc định chỉ xem trước). Cách áp patch và build: `omni_crm/crm_frontend_patch/README.md`.

## 9. Test bắt buộc

- Chữ ký đúng/sai/thiếu; timestamp quá cũ.
- Mỗi loại sự kiện ở mục 4 với payload mẫu (lưu trong `tests/fixtures/zalo/`).
- Zalo gửi lại cùng sự kiện → không trùng.
- Làm mới token: thành công lưu cả hai token; thất bại cảnh báo; hai tiến trình đồng thời chỉ một tiến trình làm mới.
- `can_send_now` ở cả ba khoảng thời gian; tin bot ở khoảng có phí bị chặn.
- Gửi tin ở `sandbox_mode` không gọi HTTP (dùng mock khẳng định không có request).
