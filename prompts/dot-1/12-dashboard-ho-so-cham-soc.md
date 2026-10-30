# D1-12 ★ · Dashboard CSKH và hồ sơ chăm sóc

**Phụ thuộc:** D1-10, D1-14. Chạy **trước** D1-11 (D1-11 kiểm thử cả phần này). **Thời lượng dự kiến:** 2,5 ngày.
**Tham chiếu thiết kế:** canvas "Dashboard CSKH Omni" (người duyệt mở để so sánh); agent làm theo `docs/13`.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/13-dashboard-cskh.md (toàn bộ — đặc tả chính), docs/14-nha-thau-b2b.md mục 3, 4, 6, 8 (thay đổi cho khách nhà thầu — bắt buộc áp dụng), docs/02 mục 2 và danh sách DocType, docs/04 mục 5 (gộp Lead), docs/11 mục 6 (xóa dữ liệu khách), mã Hộp thư hiện có (D1-06, D1-09).

Nhiệm vụ: nhật ký phiên chăm sóc theo format chuẩn, màn hình Hồ sơ chăm sóc, màn hình Tổng quan CSKH (các khối của Đợt 1), và chỉnh Hộp thư cho khớp docs/13 mục 2.

Việc cần làm:
0. `utils/business_hours.py` theo đặc tả D2-03 bước 1 (`add_business_minutes`, `is_business_time`, `next_business_start`, `business_minutes_between`) đọc `Omni Settings.business_hours` và trường mới `holidays` (JSON, mặc định rỗng). Có test qua đêm, cuối tuần, thứ Bảy nửa ngày, ngày lễ. D1-15 và D2-03 dùng lại module này.
1. DocType `Omni Care Log` đúng docs/13 mục 3.2 (đặt tên `OCL-.YYYY.-.#######`, chỉ mục (`lead`, `last_activity_at`)); trường Settings ở docs/13 mục 5. Quyền: Sales User đọc/ghi qua API theo quyền Lead; ghi `outcome`, `next_action`, `next_due`, `summary` — không sửa trường hệ thống.
2. `crm_bridge/care_log.py`:
   - `touch_session(lead, channel, at, kind, user=None, call=None)`: tạo phiên mới nếu không có phiên cùng kênh có `last_activity_at` trong `care_session_gap_hours`, ngược lại cập nhật; `summary` tự điền là trích 200 ký tự tin khách đầu phiên (Đợt 2 bot sẽ thay bằng tóm tắt); cuộc gọi luôn là phiên riêng (khung cho Đợt 2).
   - Gọi từ worker sau mỗi lượt xử lý tin vào và từ `send_outbound` khi gửi thành công (không gọi cho follow/unfollow, Ignored, tin sandbox).
   - `save_outcome(log, outcome, next_action, next_due, create_task)`: kiểm tra quy tắc bắt buộc ở docs/13 mục 3.2; tạo/cập nhật CRM Task (tên trường theo G1) giao người phụ trách Lead; ghi `outcome_by/at`.
   - Tác vụ cuối ngày (17:45 giờ làm việc): thông báo tổng hợp cho mỗi sale số phiên "Chưa ghi kết quả".
3. Mở rộng gộp Lead (`identity/merge.py`) chuyển cả `Omni Care Log`; mở rộng `erase_customer_data` xóa `summary`. Thêm test cho cả hai.
4. Trang Chi tiết Lead `omni-customer` (route nhận tên Lead) theo docs/13 mục 3b: đầu trang (thao tác nhanh, thanh giai đoạn lấy từ CRM Lead Status), ba cột, các tab; tab "Hoạt động" gộp sự kiện từ conversation_turns, Omni Care Log, Omni Call (khi có), Version/Comment hệ thống, có bộ lọc; các khối/tab của Đợt 2–3 (Cuộc gọi, Điểm, Nhu cầu) ẩn khi tính năng chưa có. Tab "Nhật ký chăm sóc" là nội dung màn 3: khối Thông tin khách hàng 3 nhóm, mỗi trường có nhãn nguồn Kênh/Bot/Sale (Bot = trường `omni_*` không nằm trong `omni_manual_fields`; Sale = có trong đó hoặc trường CRM người dùng sửa; Kênh = từ Identity/sender_profile) — trường Đợt 2 chưa có thì ẩn nhóm "Nhu cầu"; sửa trường tại chỗ (ghi CRM Lead, đánh dấu sửa tay); bảng Nhật ký chăm sóc (mới nhất trước, phân trang 20); form Ghi nhật ký + khối Quy ước kết quả (docs/13 mục 3.3).
5. Trang Tổng quan `omni-dashboard`: theo docs/13 mục 1, CHỈ các khối Đợt 1 (Hội thoại chờ trả lời, Phản hồi lần đầu TB, Lead mới, bảng Cần xử lý ngay với lý do "Quá hạn phản hồi"/"Đang chờ", Theo kênh cho Email và Zalo, Theo nhân viên). Các khối Đợt 2–3 để điểm nối có docstring (`dashboard.blocks` đăng ký khối theo công tắc), không hiện số 0. API `api/dashboard.py::snapshot(period, scope)` theo docs/13 mục 4; phạm vi: Sales User chỉ thấy Lead mình xem được, người có `team_lead_role` thấy toàn đội.
6. Hộp thư: bổ sung theo docs/13 mục 2 — bộ lọc dạng chip, nhãn ưu tiên mỗi hội thoại, khối hồ sơ rút gọn bên phải (kèm "Bước tiếp theo" và nút "Mở hồ sơ chăm sóc"), nút "Ghi nhật ký chăm sóc" mở form ngay trong Hộp thư, nhãn "Chưa ghi kết quả".
7b. Khách nhà thầu (docs/14 mục 8): trang `omni-customer` mở theo Lead nhưng trình bày **Chi tiết nhà thầu** — tiêu đề tên công ty + mã số thuế; cột trái thêm danh sách người liên hệ (vai trò, đồng ý nhận liên hệ; `Từ chối` thì ẩn nút gọi/nhắn); tab **Gói thầu đã trúng** (bảng Omni Bid Award, sắp ngày mới nhất, cột còn hiệu lực); cột phải khối **Mức phù hợp** (từng tiêu chí đạt/chưa, điểm, hạng, nút "Cách tính" giải thích công thức với tham số hiện hành) cạnh **Điểm quan tâm**. Tổng quan: phễu theo giai đoạn docs/14 mục 4, bảng "Phù hợp A/B, chưa liên hệ" (sắp theo điểm phù hợp, nút "Ghi nhật ký" mở form), số nhà thầu theo hạng × khu vực. Hộp thư: tên công ty và hạng cạnh tên người nhắn.
7. Giao diện theo bộ nhận diện Miyano mô tả ở đầu docs/13 (font, màu, viền, một nút chính mỗi màn hình, nhãn trạng thái có chữ). Dùng CSS biến trong một tệp `public/css/omni.css` (bật qua hooks `app_include_css` chỉ cho các trang của app — kiểm tra không làm đổi giao diện trang khác của Desk/CRM).

Test:
- Phiên: 3 tin trong 1 giờ → 1 phiên; tin sau 5 giờ → phiên mới; cuộc gọi → phiên riêng; tin sandbox không tạo phiên.
- save_outcome: thiếu bước tiếp theo khi kết quả "Hẹn gọi lại" → lỗi rõ; có hạn + tích tạo việc → 1 CRM Task; sửa hạn → việc cập nhật, không tạo việc thứ hai.
- Chỉ số: dựng dữ liệu giả có kiểm soát, so từng chỉ số docs/13 mục 1.2 (Đợt 1) với giá trị tính tay; phản hồi lần đầu chỉ đếm giờ làm việc.
- Thứ tự "Cần xử lý ngay" đúng quy tắc mục 1.3 (phần Đợt 1).
- Quyền: Sales User không thấy Lead ngoài quyền trong cả 3 màn hình và API.
- Hiệu năng snapshot với 100.000 tin vào (ghi số đo).

- Tab Hoạt động: đủ các loại sự kiện, đúng thứ tự, lọc đúng; Sales User không có quyền Lead → bị từ chối.

Hoàn thành khi: test xanh; ảnh chụp 4 màn hình (Tổng quan, Hộp thư, Chi tiết Lead tab Hoạt động, tab Nhật ký chăm sóc) trên dev với dữ liệu giả, kèm so sánh ngắn với thiết kế (chỗ nào khác và vì sao).

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Mở canvas "Dashboard CSKH Omni" cạnh màn hình thật: bố cục, nhãn, thứ tự khối khớp.
2. Trả lời một khách trong Hộp thư, đợi 2 giờ (hoặc đổi giờ trên dev): hiện "Chưa ghi kết quả"; ghi "Hẹn gọi lại" có hạn → có việc trong CRM.
3. Đăng nhập bằng một sale: Tổng quan chỉ có số của Lead mình phụ trách.
