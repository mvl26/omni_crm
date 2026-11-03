# 01 · Cài đặt và hạ tầng

Dành cho: quản trị hệ thống (người có quyền shell trên máy chủ bench). Làm xong tệp này thì máy chủ sẵn sàng nhận tin, nhưng **chưa có kênh nào chạy**: kênh cấu hình ở các tệp 03, 04, 05.

Mục lục: [1. Điều kiện](#1-điều-kiện-trước-khi-cài) · [2. Cài app](#2-cài-app) · [3. Worker omni](#3-worker-omni-và-supervisor) · [4. Scheduler](#4-scheduler) · [5. Redis](#5-redis) · [6. Nginx](#6-nginx) · [7. Email gửi đi](#7-email-gửi-đi-mặc-định) · [8. Sao lưu](#8-sao-lưu) · [9. sandbox_mode](#9-sandbox_mode) · [10. Cờ site_config](#10-cờ-cấp-hạ-tầng-trong-site_config) · [11. Không bật ở production](#11-không-được-bật-ở-production) · [12. Sự cố thường gặp](#12-sự-cố-thường-gặp) · [13. Đã xong khi](#13-đã-xong-khi)

Quy ước lệnh: mỗi khối lệnh ghi rõ **chạy ở đâu**. `<site>` là tên site (ví dụ `crm.example.vn`), `<bench-dir>` là thư mục bench (ví dụ `/home/frappe/frappe-bench`). Mọi giá trị trong tài liệu là giá trị giả.

## 1. Điều kiện trước khi cài

- Frappe Framework v15 và app `crm` (Frappe CRM) bản tương thích v15 đã cài trên site. `omni_crm` khai báo `required_apps = ["crm"]` (xem `omni_crm/hooks.py`), thiếu `crm` thì không cài được.
- MariaDB, Redis (hai phiên bản: cache và queue), Python theo bench.
- Một site thử (staging) riêng. **Không chạy thử trên site có dữ liệu khách thật** trước khi qua quy trình ở [10-quy-trinh-len-san-xuat.md](10-quy-trinh-len-san-xuat.md).
- Tên miền HTTPS công khai cho site (Zalo và tổng đài gọi vào webhook; Zalo yêu cầu HTTPS).

## 2. Cài app

Chạy **trong thư mục bench** (`cd <bench-dir>`), bằng người dùng chạy bench:

```bash
# 1. Lấy mã app (thay <URL-repo-omni_crm> bằng địa chỉ git của công ty; nhánh/tag lấy theo bản phát hành)
bench get-app <URL-repo-omni_crm> --branch develop

# 2. Cài vào site
bench --site <site> install-app omni_crm

# 3. Áp DocType, Custom Field, vai trò (fixtures)
bench --site <site> migrate
```

Điều xảy ra (theo `omni_crm/install.py` và `hooks.py`):

- Sau khi cài, `Omni Settings.sandbox_mode` được đặt **bật** (an toàn: chưa gửi gì ra ngoài).
- `migrate` tạo hai vai trò **Omni Admin** và **Omni Knowledge Editor**, các Custom Field `omni_*` trên CRM Lead (điểm, tóm tắt bot, SĐT chuẩn hóa, ...) và ghi phiên bản câu lệnh bot vào Omni Settings (các trường chỉ đọc).
- Các hook (`doc_events`, `scheduler_events`) bắt đầu hoạt động ngay sau khi cài, nhưng bot và các kênh đều **tắt** cho tới khi bạn bật.

Gán vai trò: vào `/app/user`, mở tài khoản quản trị Omni, thêm vai trò **Omni Admin**. Tài khoản **Administrator** có quyền mọi nơi, nhưng nên dùng tài khoản cá nhân có vai trò Omni Admin cho việc hằng ngày. (Quyền của `Omni Settings` chỉ ghi cho Omni Admin; vai trò System Manager không được liệt kê.)

Kiểm tra sau cài (chạy trong `<bench-dir>`):

```bash
bench --site <site> list-apps          # phải có omni_crm
bench --site <site> migrate            # chạy lần hai: không lỗi, không thay đổi gì
```

## 3. Worker omni và supervisor

App xử lý tin trong hai hàng đợi riêng: **`omni`** (email, Zalo, tổng đài, bot, tin ra) và **`omni_leads`** (lead quảng cáo: Meta Lead Ads, Google Lead Form, Web Form; từ V21-01). Các hàng đợi này **không tồn tại** nếu `common_site_config.json` chưa khai báo; thiếu `omni` thì mọi lần xếp việc báo lỗi "Queue should be one of ..." (docs/12 mục C, G6). Thiếu `omni_leads` thì app **không lỗi**: lead quảng cáo rơi về hàng đợi `omni` (dùng chung worker với chat, chậm khi chat đông) và ghi cảnh báo "chưa khai báo hàng đợi omni_leads" tối đa một lần mỗi giờ.

**Bước 1. Khai báo hàng đợi.** Sửa `<bench-dir>/sites/common_site_config.json`, thêm khóa `workers` (tệp này dùng chung cả bench; chỉ thêm khóa, đừng đổi gì khác):

```json
"workers": {
  "omni": { "timeout": 600, "background_workers": 8 },
  "omni_leads": { "timeout": 600, "background_workers": 2 }
}
```

Các khóa khác của tệp giữ nguyên. `omni_leads` cần **từ 2 worker**, đề xuất **3-4 worker ở production** (xem công thức bên dưới; chưa đo staging); `omni` theo lưu lượng chat.

- `timeout`: giây. Bản code đặt thời gian tối đa 600 giây cho việc bot (hằng `JOB_TIMEOUT` trong `bot/analyze.py` và `bot/reply.py`), và `docs/01-kien-truc.md` khuyên 600. Máy dev đang để 300 (`docs/12`, G6). **Chưa kiểm** giá trị 300 hay 600 ảnh hưởng thế nào lên việc bot dài: dùng 600 ở production.
- `background_workers`: số tiến trình worker cho hàng đợi này khi bench tự sinh cấu hình supervisor (xem công thức bên dưới).

**Bước 2. Sinh cấu hình supervisor.** Mẫu `supervisor.conf` của bench (đã đọc: `bench/config/templates/supervisor.conf`) có vòng lặp tạo một chương trình `<bench>-frappe-omni-worker` cho mỗi khóa trong `workers`, nên chạy trong `<bench-dir>`:

```bash
bench setup supervisor            # sinh lại config/supervisor.conf
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status         # phải thấy ...-frappe-omni-worker:... (đủ số worker omni) và ...-frappe-omni_leads-worker:... (≥ 2) ở trạng thái RUNNING
```

Nếu bạn quản lý supervisor/systemd bằng tay, chương trình cần chạy đúng lệnh sau (trong `<bench-dir>`, người dùng chạy bench):

```bash
bench worker --queue omni
bench worker --queue omni_leads   # ít nhất 2 tiến trình
```

Sau khi đổi khóa `workers` phải **khởi động lại** gunicorn, worker và scheduler (Frappe đọc khóa này một lần mỗi tiến trình). Khai báo `omni_leads` mà không chạy worker nào lắng nghe thì lead quảng cáo nằm chờ trong hàng đợi và không thành Lead: khai báo và chạy worker phải đi cùng nhau.

> Lưu ý: tệp `config/supervisor.conf` sinh trước khi thêm khóa `workers` sẽ **không có** worker omni. Phải sinh lại như trên. Đây là sai sót dễ gặp nhất: tin vẫn được nhận (trạng thái `New`) nhưng không bao giờ thành Lead.

**Số worker cần bao nhiêu** (docs/11 mục D1-11): mỗi lượt xử lý giữ một worker khoảng `debounce_seconds` (mặc định 5 giây) cộng khoảng 1 giây xử lý, nên công suất xấp xỉ `số_worker / 6` khách mỗi giây.

> **số_worker ≥ tin_mỗi_giây_giờ_cao_điểm × 6 × 1,5**, tối thiểu 2.

**Hàng đợi `omni_leads`:** mỗi lead cũng giữ một worker ~`debounce_seconds` + xử lý, nên công suất ~`số_worker / 6` lead mỗi giây; 2 worker đủ cho khoảng 0,33 lead/giây (200 lead trong 10 phút) và nên thêm worker khi chiến dịch dồn lead (số đo trên site test ở `docs/11-van-hanh.md`, mục "V21-01": 2 worker đủ cho đúng 200 lead/10 phút, không có dư địa; chưa đo staging).

Ví dụ: cao điểm 1 tin/giây từ các khách khác nhau → 1 × 6 × 1,5 = 9 worker. Số liệu đo (máy dev 16 nhân, không phải staging): 4 worker xử lý ~0,75 tin/giây; 12 worker giữ backlog dưới ~200 tin. Khi tin của cùng một khách đến dồn thì chúng gộp thành một lượt nên công suất cao hơn. Cảnh báo "Tin chờ xử lý quá lâu" (tin `New` cũ hơn 5 phút) là tín hiệu cần thêm worker. Cuộc gọi dùng chung `debounce_seconds` nên cũng giữ worker ~6 giây.

## 4. Scheduler

Các tác vụ định kỳ (quét tin kẹt, cảnh báo, đối soát cuộc gọi đêm, làm mới token Zalo, dọn payload, ngắt mạch bot) chạy bằng scheduler của Frappe. Chạy trong `<bench-dir>`:

```bash
bench --site <site> scheduler enable
bench --site <site> scheduler status     # phải báo "enabled"
```

Danh sách tác vụ (từ `omni_crm/hooks.py`):

| Lịch | Việc |
| --- | --- |
| Mỗi 5 phút | Quét tin kẹt/thử lại, kiểm cảnh báo, tạo bù việc gọi lại, so nháp ngầm với câu sale, hết hạn nháp Draft quá 24 giờ, email nhắc chuyển người |
| Mỗi 30 phút | Làm mới token Zalo khi còn dưới 2 giờ |
| 01:30 hằng đêm | Đối soát cuộc gọi hôm qua |
| 02:00 hằng đêm | Xóa `raw_payload` quá hạn |
| Hằng giờ | Kiểm ngắt mạch bot tự gửi |

Scheduler tắt thì tin vẫn nhận nhưng mọi việc bù và cảnh báo dừng. Nếu không bật được ngay, có nút "Quét ngay" ở `/app/omni-monitor` (tệp [09](09-van-hanh-hang-ngay.md)).

## 5. Redis

- Cần **hai** Redis chạy khỏe: cache và queue (cổng ghi trong `common_site_config.json`, khóa `redis_cache`, `redis_queue`).
- Redis được dùng cho: hàng đợi `omni`, khóa chống gộp trùng khách, khóa làm mới token Zalo, bộ đếm token AI, ngắt mạch AI, cờ chống lặp cảnh báo.
- **Redis queue chết**: webhook vẫn lưu tin và trả 200 (tin ở trạng thái `New`); khi Redis lên lại, tác vụ quét đẩy lại (tin phải cũ hơn 2 phút, chu kỳ 5 phút). Việc đã nằm trong hàng đợi lúc Redis chết thì mất, và được tác vụ quét tạo bù. Worker phải do supervisor tự khởi động lại.
- **Lưu ý môi trường dev**: Redis queue của bench dev (cổng 11000) bị hỏng tệp RDB (`redis_queue.rdb` ~2,7 GB), `bench start` khởi động lại liên tục ở trạng thái "đang nạp dữ liệu" (docs/12 mục D; `docs/nghiem-thu/dot-3.md`). Hệ quả: một số test tích hợp lỗi/skip trên máy đó. **Không mang tệp RDB này sang staging/production.** Việc sửa là của chủ bench dev (xóa RDB hỏng sau khi sao lưu, khởi động lại Redis); chi tiết ở [10](10-quy-trinh-len-san-xuat.md) mục dọn dẹp.
- Production: dùng Redis riêng, đặt `maxmemory` hợp lý cho cache, bật lưu bền (AOF hoặc RDB) cho queue, giám sát dung lượng đĩa.

## 6. Nginx

Webhook (Zalo, tổng đài) là đường công khai duy nhất không cần đăng nhập. Code đã chặn thân request quá **1 MB** (trả 413) và giới hạn ghi Error Log 1 dòng/phút/kênh, nhưng nginx chặn sớm hơn. Cần:

1. **Giới hạn kích thước thân** cho các đường `omni_crm.api.webhooks.*` (docs/11 D1-10): `client_max_body_size 1m;`.
2. **Giới hạn tốc độ** (rate limit) cho các đường này, để bên ngoài không làm nghẽn gunicorn. Code **không** tự giới hạn tốc độ.
3. **Không ghi query string vào access log** cho webhook tổng đài: `webhook_token` nằm trong URL (`...&t=<token>`).

Ví dụ (minh họa, **chưa kiểm trên nginx thật**, điều chỉnh theo `config/nginx.conf` do `bench setup nginx` sinh; hãy chạy `sudo nginx -t` trước khi nạp lại):

```nginx
# trong khối http {} (một lần)
limit_req_zone $binary_remote_addr zone=omni_webhook:10m rate=20r/s;

# trong khối server {} của site, đặt TRƯỚC location / chung của frappe
location ~ ^/api/method/omni_crm\.api\.webhooks\. {
    client_max_body_size 1m;
    limit_req zone=omni_webhook burst=40 nodelay;
    access_log /var/log/nginx/omni_webhook.log omni_noquery;   # định dạng log không có $args (tự định nghĩa)
    proxy_pass http://frappe-bench-frappe;                      # đổi theo upstream bench đã sinh
    # ... các proxy_set_header giống location / của bench
}
```

Mức `rate` cần đối chiếu với lưu lượng thật (Zalo gửi lại sự kiện khi không nhận 200 trong 5 giây; tổng đài có thể gửi nhiều sự kiện mỗi cuộc gọi). Đặt quá thấp sẽ làm mất sự kiện (đối soát đêm của tổng đài bù được, Zalo thì chưa kiểm).

Quan trọng: `gunicorn -t` (thời gian chờ) mặc định của bench là 120 giây. Khi bảng tin vào bị khóa, webhook có thể chờ lâu thay vì trả lời trong 5 giây (docs/11 D1-11). Theo dõi độ trễ DB nếu thấy Zalo gửi lại nhiều.

## 7. Email gửi đi mặc định

Hệ thống gửi thư cho chính nhân viên trong hai trường hợp: **thư cảnh báo** (tới `alert_recipients`) và **thư nhắc chuyển người** (khi `handoff_notify_channels` có `email`). Cả hai đi qua hàng đợi email của Frappe, nên cần:

1. Một **Email Account** gửi đi mặc định (Desk: `/app/email-account`, đánh dấu "Default Outgoing"), đã thử gửi được.
2. `bench` có tiến trình lập lịch email (scheduler bật, mục 4).
3. Người nhận có địa chỉ email thật trong User.

Khi `sandbox_mode` bật, thư nhắc chuyển người **không** được xếp (chỉ ghi log). Mã cảnh báo (`monitoring/alerts.py`) không kiểm sandbox cho thư cảnh báo nên thư vẫn đi nếu `alert_recipients` có địa chỉ (đọc từ mã, chưa kiểm thực tế; xem [09](09-van-hanh-hang-ngay.md)). Thông báo trong hệ thống (Notification Log, chuông) luôn tạo, không cần email.

Hộp thư **nhận** khách (kênh Email) là Email Account khác với tài khoản gửi mặc định này nếu muốn; xem [03-kenh-email.md](03-kenh-email.md).

## 8. Sao lưu

- Tối thiểu mỗi ngày một bản. Chạy trong `<bench-dir>` (ví dụ bằng cron của người dùng bench):

```bash
bench --site <site> backup --with-files
```

- Chép bản sao lưu (`sites/<site>/private/backups/`) ra nơi khác máy chủ. Kiểm tra khôi phục trên site thử **mỗi quý** (docs/11 mục 6).
- Bản sao lưu chứa tin nhắn và thông tin khách: bảo vệ như dữ liệu khách.
- **Cẩn thận khi chép dữ liệu production về dev/staging**: token Zalo trong bản chép là token thật; làm mới token ở site dev sẽ làm hỏng token production (refresh token dùng một lần). Chép xong phải bật `sandbox_mode` ngay (sandbox chặn mọi HTTP ra Zalo kể cả làm mới token) và xóa/đổi các Password của kênh.
- Việc **xóa dữ liệu theo hạn** (`raw_payload` sau 180 ngày mặc định) chỉ áp cho DB hiện tại, không áp cho bản sao lưu cũ.

## 9. sandbox_mode

`Omni Settings.sandbox_mode` (Desk: `/app/omni-settings`, mục "Chung", nhãn **Chế độ thử (sandbox)**). Mặc định **bật**.

| Bật | Tắt |
| --- | --- |
| Chiều ra chỉ ghi log, **không gọi API thật**: không gửi tin Zalo, không làm mới token Zalo, không đổi mã cấp quyền OAuth, không gọi Call log/ghi âm Stringee, không xếp email nhắc chuyển người, đối soát đêm tổng đài bị bỏ qua. Tin ra mang nhãn "thử nghiệm" (`is_sandbox`). | Chiều ra gọi API thật. Chỉ tắt ở production, hoặc ở staging có chủ đích kèm người nhận thử do người phụ trách chỉ định. |

Đáng chú ý:

- Sandbox **không** chặn lời gọi AI. AI thật bị chặn bằng cổng Q11 (mục 10: `omni_ai_allow_customer_data`).
- Sandbox bật thì nút **Kết nối Zalo OA** vẫn chuyển hướng được nhưng bước đổi mã lấy token không chạy (docs/09 mục 5). Lấy token lần đầu cần tắt sandbox có chủ đích hoặc nhập token tay: xem [04](04-kenh-zalo-oa.md).
- Production phải **tắt** sandbox: quên tắt thì khách không nhận được trả lời mà hộp thư vẫn báo "thử nghiệm". Màn hình `/app/omni-monitor` hiện trạng thái công tắc đầu trang.

## 10. Cờ cấp hạ tầng trong site_config

Các cờ này nằm ở `<bench-dir>/sites/<site>/site_config.json` (**của đúng site cần**, xem cảnh báo bên dưới), **ngoài cơ sở dữ liệu**: vì vậy người dùng trong CRM, kể cả Omni Admin, không bật được qua giao diện. Đây là chủ ý, để quyết định nhạy cảm do người vận hành đặt sau khi chủ dự án chốt. Đã liệt kê từ mã nguồn:

| Khóa | Mặc định khi vắng | Tác dụng | Rủi ro khi bật |
| --- | --- | --- | --- |
| `omni_ai_allow_customer_data` | Không bật | Cho phép gửi **hội thoại/cuộc gọi của khách thật** sang dịch vụ AI (Claude API) và chuyển giọng nói. Không bật thì lời gọi AI thật chỉ chạy với hội thoại tổng hợp (`synthetic`). Bộ đề từ hội thoại thật (`build_from_real`) cũng cần cờ này. | Dữ liệu khách rời hệ thống. **Chỉ bật khi chủ dự án chốt Q11.** |
| `omni_ai_unmask_pii` | Không bật (tức là **che** SĐT/email trước khi gửi AI) | Bật = gửi SĐT, email thật sang AI. | Lộ thông tin liên hệ cho dịch vụ ngoài; chỉ bật khi Q11 cho phép rõ. Che SĐT cũng làm cổng `phan_tich` khó đạt (20 ca bộ đề có SĐT). |
| `omni_auto_send_allowed` | Không bật (chặn) | Khóa tổng cho **bot tự gửi cho khách**. Vắng/0 thì không nhóm nào bật được Auto, và mọi tin Bot đang chờ bị chặn. | Bot nói thẳng với khách không qua người duyệt. **Chỉ đặt sau khi Q1 chốt** và sau khi có số liệu từng nhóm. Xem [07](07-bot-ai.md). |
| `omni_ai_timeout_seconds` | 60 | Thời gian chờ tối đa mỗi lần gọi AI (giây). | Đặt quá thấp: nhiều lần lỗi và ngắt mạch. |
| `omni_eval_judge_model` | `claude-sonnet-5-5` | Mô hình chấm cho bộ đề. | Đổi mô hình giám khảo làm số điểm không so sánh được với lần trước. |
| `omni_zalo` | Không đặt | Từ điển ghi đè các hằng API Zalo (`api_base`, `send_path`, `signature_header`, `max_text_length`, ...). Dùng khi Zalo đổi API mà chưa kịp cập nhật app. | Sai giá trị làm Zalo ngừng gửi/nhận. Giá trị mặc định chưa được đối chiếu với tài liệu Zalo chính thức (docs/12 mục B2). |
| `omni_webhook_lock_wait_seconds` | 3 | Thời gian webhook chờ khóa DB trước khi ghi **hàng dự phòng** (V21-04; trước đây là thời gian chờ rồi trả 503, mặc định 5). Giá trị cũ vẫn chạy, không lỗi. | Đặt quá cao làm request giữ worker web lâu; không còn gây mất tin. |
| `omni_spool_max_bytes` / `omni_spool_max_lines` / `omni_spool_expire_days` | 50 MB / 5.000 / 14 | Giới hạn hàng dự phòng webhook tại `<site>/private/omni_spool` (chứa dữ liệu cá nhân chưa vào DB: thư mục 0700, tệp 0600). Đầy thì cảnh báo và webhook trả 503. | Đặt cao làm giữ nhiều dữ liệu cá nhân ngoài DB; đặt `expire_days` thấp có thể xóa tin chưa chèn được. |
| `developer_mode` | Tắt (cờ chuẩn của Frappe) | Xem mục 11. | Xem mục 11. |

Cách đặt (chạy trong `<bench-dir>`, ví dụ bật khóa AI cho một site **chỉ khi đã được phép**):

```bash
bench --site <site> set-config omni_ai_allow_customer_data 1
bench restart          # hoặc: sudo supervisorctl restart all
```

Cách gỡ (khẩn cấp): sửa `sites/<site>/site_config.json`, xóa khóa (hoặc đặt 0), rồi khởi động lại. Xóa `omni_auto_send_allowed` có tác dụng tức thì với tin Bot đang chờ (chuyển `Blocked`).

> **Không đặt `omni_auto_send_allowed` (và các cờ trên) trong `common_site_config.json`.** Tệp đó áp cho MỌI site của bench, kể cả dev/staging. Chỉ đặt trong `sites/<site>/site_config.json` của đúng site production được phép.

Khóa API Claude **không** ở site_config: nhập vào trường **Khóa Claude API** (`ai_api_key`, kiểu Password) trong Omni Settings; chỉ nhập trên máy chủ, không dán vào chat/tài liệu/log.

## 11. Không được bật ở production

| Thứ | Vì sao cấm |
| --- | --- |
| `developer_mode` (site_config) | Mở đường **bỏ qua kiểm chữ ký webhook Zalo** (`zalo_skip_signature` chỉ có tác dụng khi `developer_mode` bật), cho phép chọn nhà cung cấp tổng đài "Fake" và STT "Fake", và bật các chế độ gỡ lỗi của Frappe. Màn hình giám sát hiện `developer_mode` màu đỏ khi bật. Kiểm: `bench --site <site> console`, rồi `frappe.conf.developer_mode`. |
| `zalo_skip_signature` (trường trên **Omni Channel** loại Zalo OA, không phải site_config) | Bỏ kiểm chữ ký: ai biết URL cũng giả được tin khách. Mã chỉ cho phép khi `developer_mode` bật; trên production bật cờ này thì cổng **từ chối** xử lý và cảnh báo. Vẫn phải để tắt. |
| `sandbox_mode` bật | Khách không nhận trả lời (xem mục 9). |
| `omni_auto_send_allowed` khi chưa có quyết định Q1 | Xem mục 10. |
| Nhà cung cấp tổng đài `Fake` | Mã tự chặn ngoài môi trường dev/test, nhưng không đặt. |

Danh sách kiểm trước mỗi lần phát hành: `developer_mode` tắt · `zalo_skip_signature` tắt · `sandbox_mode` đúng ý định · `omni_auto_send_allowed` vắng (trừ khi đã được duyệt) · scheduler bật · worker `omni` chạy.

## 12. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Tin chỉ ở trạng thái `New`, không thành Lead | Chưa có worker `omni` (hoặc thiếu khóa `workers` ở `common_site_config.json`), Redis queue chết | `sudo supervisorctl status`; mục 3; `bench doctor` (trong `<bench-dir>`) |
| Thẻ "Hàng dự phòng webhook" có tin / cảnh báo "Tin nằm lâu trong hàng dự phòng" (V21-04) | DB khóa hoặc mất kết nối lúc webhook đến; tác vụ quét `omni_crm.ingest.spool.drain` (mỗi phút) chưa chèn lại được (scheduler tắt, DB còn lỗi) | Bật scheduler (mục 4), kiểm DB, chạy tay `bench --site <site> execute omni_crm.ingest.spool.drain`; thư mục `private/omni_spool` phải ghi được (0700) |
| Lỗi "Queue should be one of ..." trong log | Thiếu `workers.omni` trong `common_site_config.json` | Thêm như mục 3, khởi động lại |
| Log có cảnh báo "chưa khai báo hàng đợi omni_leads" / lead quảng cáo chậm khi chat đông | Thiếu `workers.omni_leads` nên lead đi chung hàng đợi `omni` | Khai báo `omni_leads`, sinh lại supervisor, chạy ≥ 2 worker, khởi động lại |
| Lead quảng cáo ở `New` mãi dù đã khai báo `omni_leads` | Không có worker lắng nghe `omni_leads` (supervisor sinh trước khi khai báo), hoặc tiến trình chưa khởi động lại | `supervisorctl status`; `bench setup supervisor`; mục 3 |
| Hết cảnh báo/việc bù không chạy | Scheduler tắt | `bench --site <site> scheduler enable` |
| Sau khi sao lưu/chép site, Zalo báo lỗi token | Token trong bản chép đã bị làm mới ở nơi khác | Cấp quyền lại qua nút "Kết nối Zalo OA" ([04](04-kenh-zalo-oa.md)) |
| `bench migrate` báo thiếu app `crm` | Chưa cài `crm` | Cài `crm` trước |
| Thư cảnh báo không tới | Chưa có Email Account gửi đi mặc định, `alert_recipients` trống | Mục 7; `02` mục "Cảnh báo" |
| Sau khi khởi động lại worker vẫn có cảnh báo "Tin chờ xử lý quá lâu" ~15 phút | Job bị bỏ lại trong sổ đăng ký RQ sau khi worker bị kill | Chờ tự phục hồi hoặc bấm "Quét ngay" (docs/11 D1-11) |

## 13. Đã xong khi

- [ ] `bench --site <site> list-apps` có `omni_crm`; `bench --site <site> migrate` chạy hai lần liên tiếp đều sạch.
- [ ] Vai trò Omni Admin gán cho ít nhất hai người; mở được `/app/omni-settings`.
- [ ] `common_site_config.json` có `workers.omni` và `workers.omni_leads`; `supervisorctl status` thấy worker omni RUNNING (≥ 2) và worker omni_leads RUNNING (≥ 2).
- [ ] `bench --site <site> scheduler status` báo enabled.
- [ ] Redis cache và queue chạy khỏe; không dùng RDB hỏng.
- [ ] Nginx có giới hạn thân 1 MB và rate limit cho webhook; access log webhook không ghi query.
- [ ] Email Account gửi đi mặc định gửi thử được.
- [ ] Sao lưu hằng ngày chạy và đã thử khôi phục trên site thử.
- [ ] `sandbox_mode` đúng ý định của môi trường; `developer_mode` tắt trên production.
- [ ] Chưa đặt `omni_auto_send_allowed` và `omni_ai_allow_customer_data` (trừ khi chủ dự án đã chốt).
