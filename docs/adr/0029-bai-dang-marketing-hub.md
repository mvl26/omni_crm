# 0029 · Marketing Hub: bài đăng, lịch đăng, gắn Lead theo bài, tự đăng lên Facebook, chỉ số bài

- Ngày: 2026-11-17
- Trạng thái: Đề xuất (chờ Việt review)
- Prompt: không có prompt kit. Yêu cầu của người phụ trách ngày 17/11/2026: làm các phần CRM còn thiếu để chạy chiến dịch trong CRM; bỏ bước gửi Chi duyệt vì Chi tự tạo chiến dịch và bài; ảnh lưu trên MinIO Free ở nhánh riêng (ADR 0030). Kế hoạch: "Kế hoạch triển khai Marketing Hub trong CRM Miyano" V1.1 (`F:\DOC\Ke_hoach_Marketing_Hub_CRM_20261117_V1.1.docx`).
- Liên quan: ADR 0022, 0028 (kênh Meta Messenger, token System User); docs/15 mục 6 (quy kết chiến dịch); `spd/docs/24` (thử quyền đăng bài ở mức Standard Access).

## Bối cảnh

Đã kiểm trên mã máy chủ (nhánh `feat/connect-facebook-page` 917b1b3, đang chạy trên yhct từ 16/11):

- `Omni Campaign` có tên, nền tảng, 4 trạng thái, `utm_campaign`, ngày chạy, ngân sách, chi phí. Chỉ Omni Admin ghi được, Sales User chỉ đọc.
- Lead nhận `omni_campaign` (lần đầu), `omni_last_campaign`, 5 trường `omni_utm_*` trong `crm_bridge/form_lead.py::_attribution_values`. Form landing page (qua file JSON của miyano-intake hoặc Web Form) và Lead Ads đều đi qua hàm này. `omni_utm_content` chỉ là chữ, không nối tới bài nào.
- Không có chỗ lưu bài đăng, lịch đăng, chỉ số bài. Không có lời gọi Graph nào để đăng bài.
- Kênh `messenger-mvl` giữ token System User của Page Miyano Việt Nam. Quyền `pages_manage_posts`, `pages_read_engagement`, `read_insights` chưa có trên ứng dụng; việc thử Standard Access đang chờ (hạn 20/11, `spd/docs/24`).
- `CLAUDE.md` mục 3: thêm DocType ngoài danh sách phải có ADR và hỏi người phụ trách. Hai DocType dưới đây có trong kế hoạch V1.1 và được người phụ trách yêu cầu làm ngày 17/11.

## Các lựa chọn

### A. Chỗ lưu bài
1. **DocType mới `Omni Content Post`**, cộng danh mục `Omni Content Pillar`: có trạng thái, giờ đăng, ảnh, lịch, báo cáo, quyền riêng.
2. Ghi bài vào bình luận của chiến dịch (cách tạm của prompt M1): không lọc, không lịch, không đăng tự động được.
3. Dùng `CRM Task` của app crm: có ngày, Kanban; nhưng không có chỗ cho ảnh, mã bài, kết quả đăng; trộn với việc của sale.

### B. Duyệt bài
1. Workflow Nháp → Chờ duyệt → Đã duyệt (kế hoạch V1.1).
2. **Không có bước duyệt** (người phụ trách 17/11: Chi tự tạo và tự đăng). Cổng là nút "Lên lịch": chỉ bài đã lên lịch mới được job đăng, và job đọc lại trạng thái ngay trước khi gọi Meta.

### C. Nơi chạy việc đăng tự động
1. **Job nền của omni_crm** (mỗi phút): đăng và ghi kết quả cùng một chỗ, dùng lại token kênh và công tắc sandbox, có test trong repo.
2. n8n: thêm một hệ thống phải giữ token Page và gọi ngược vào CRM.

### D. Mất kết nối giữa lúc gọi Meta
1. Thử lại: có thể đăng hai lần vì Meta có thể đã nhận bài.
2. **Đánh dấu "Cần kiểm", không thử lại**: người soạn mở Page xem bài đã lên chưa rồi bấm nút tương ứng.

### E. Giao diện
1. **Desk** (`/app/omni-content-post`): form, danh sách, Calendar view, Script Report. Không vá app crm.
2. Patch frontend CRM theo nguyên mẫu: đẹp hơn nhưng vỡ khi nâng cấp app crm; để giai đoạn 4.

## Quyết định

Chọn A1, B2, C1, D2, E1.

- `Omni Content Post` (đặt tên theo `post_code`). Mã bài là chữ thường, số và gạch ngang, tối đa 40 ký tự, và được dùng làm `utm_content`. Bài thuộc một chiến dịch. Mỗi bài có: kênh (Facebook, Zalo OA, Khác), cách đăng (Đăng tay, Tự động), giờ đăng, tiêu đề, nội dung, một ảnh, trụ cột, giai đoạn phễu, link đích, link có UTM (tự sinh, chỉ đọc).
- Link có UTM: `utm_source` theo kênh (facebook, zalo, other), `utm_medium=social`, `utm_campaign` của chiến dịch, `utm_content` = mã bài. Thứ tự tham số giống link trong prompt M1. Tham số khác của link đích được giữ, `utm_*` cũ bị thay.
- Trạng thái: Nháp, Đã lên lịch, Đang đăng, Đã đăng, Lỗi, Cần kiểm, Bỏ lỡ, Đã huỷ. Chỉ đổi qua nút (API `api/content_posts.py`); lưu form không đổi được trạng thái. Bài đăng tay: dán link bài vào ô "Link bài" thì bài thành Đã đăng. Bài Đang đăng hoặc Đã đăng không sửa được nội dung, ảnh, giờ đăng.
- Đăng tự động (`marketing/publisher.py`, mỗi phút): chỉ khi `Omni Settings.content_publish_enabled = 1`, sandbox tắt, kênh trong `content_publish_channel` đang bật và đã bật chiều ra. Chiếm bài bằng `UPDATE … WHERE status = 'Đã lên lịch'` rồi commit trước khi gọi Meta. Quá cửa sổ đăng muộn (`content_late_window_minutes`, mặc định 30) thì Bỏ lỡ. Meta trả lỗi thì Lỗi. Lỗi mạng hoặc timeout thì Cần kiểm. Lỗi, Cần kiểm, Bỏ lỡ đều báo người tạo bài bằng Notification Log.
- Graph: lấy token Page từ token System User (`GET /{page}?fields=access_token`). Có ảnh thì `POST /{page}/photos` (ảnh gửi thẳng dạng multipart, không cần URL công khai); không ảnh thì `POST /{page}/feed` kèm `link`. Nội dung có `{link}` thì thay bằng link có UTM; không có thì link đi ở tham số `link` (feed) hoặc nối cuối chú thích (ảnh).
- Gắn Lead: `CRM Lead.omni_content_post` (Custom Field, chỉ đọc) gán ở lần chạm đầu, khi `utm_content` khớp mã bài. Tạo bài sau khi Lead đã vào (bài đăng tay trước 30/11) thì gán bù cho các Lead có `omni_utm_content` trùng mã bài mà chưa có bài.
- Chỉ số (`marketing/metrics.py`, 06:30 hằng ngày, công tắc `content_metrics_enabled`): bài tự đăng trong 30 ngày gần nhất lấy lượt cảm xúc, bình luận, chia sẻ từ bài, và các chỉ số insights trong `content_insights_metrics`. Bài đăng tay nhập số tay. Số Lead (không tính Lead thử) và số Deal tính lại mỗi đêm và hiện ngay trên form.
- Quyền: Omni Admin và Sales Manager tạo, sửa, lên lịch; Sales User chỉ đọc. `Omni Campaign` thêm quyền Sales Manager đọc, tạo, sửa; thêm mục tiêu, khách hàng đích, trang đích, mục tiêu số Lead, brief và trạng thái Đã huỷ. Không tạo vai trò mới.
- Báo cáo "Hiệu quả bài đăng": theo bài, lọc theo chiến dịch và khoảng ngày; có dòng "Không rõ bài" cho Lead của chiến dịch không gắn bài.

## Hệ quả

- Mặc định phát hành: `content_publish_enabled = 0`, `content_metrics_enabled = 0`. **Đổi ngày 18/11/2026** theo yêu cầu người phụ trách: patch `v3.enable_content_publish` bật `content_publish_enabled` và chọn kênh Meta Messenger duy nhất có Page ID khi triển khai; `publish_mode` mặc định Tự động. Khi token chưa có quyền, bài Tự động đến giờ chuyển Lỗi kèm mã Meta, không đăng gì. Bật chỉ khi lần thử `spd/docs/24` ĐẠT (hoặc Meta đã duyệt quyền) và token của kênh có `pages_manage_posts`, `pages_read_engagement` (chỉ số cần thêm `read_insights`). Token thử 4 quyền không dùng cho kênh đang chạy.
- Không có người duyệt: một người có vai trò Sales Manager lên lịch nhầm bài thì bài vẫn lên Page. Kiểm soát còn lại: công tắc chung, trạng thái phải là Đã lên lịch, cửa sổ đăng muộn, lịch sử sửa (`track_changes`).
- Tên chỉ số insights của Meta đổi theo phiên bản Graph. Mặc định `post_impressions_unique` **[Chưa xác nhận]** với Graph v23; chỉ số nào lỗi thì ghi vào `last_error` của bài, các số khác vẫn lưu.
- Ảnh lưu như File riêng tư của Frappe. Lưu ảnh trên MinIO là ADR 0030, nhánh `feat/marketing-hub-minio`.
- Chưa làm: sửa hoặc gỡ bài đã đăng từ CRM; nhiều ảnh mỗi bài; đăng Zalo OA; bình luận (App Review đợt 2); kiểm quyền đăng bài trong `token_check`.
- `tests/test_v2_02_checks.py` thêm các hàm whitelisted của `api/content_posts.py`.
