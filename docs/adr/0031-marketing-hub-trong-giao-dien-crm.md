# 0031 · Marketing Hub trong giao diện CRM: menu Marketing, cửa sổ Thiết lập, mục Marketing trên trang Lead

- Ngày: 2026-11-17
- Trạng thái: Đề xuất (chờ Việt review)
- Prompt: không có prompt kit. Người phụ trách ngày 17/11/2026: "Theo hướng đã chốt, Marketing Hub phải nằm ngay trong giao diện CRM: menu trái, cửa sổ Thiết lập và trang Lead." Bố cục theo nguyên mẫu "Marketing Hub trong Frappe CRM Miyano".
- Liên quan: ADR 0016 (patch giao diện crm, `crm_frontend_patch`), ADR 0029 (bài đăng), ADR 0030 (ảnh trên MinIO). Thay quyết định E1 của ADR 0029 ("Desk") và quyết định 9 của kế hoạch Marketing Hub.

## Bối cảnh

- ADR 0029 làm màn hình bài đăng trên Desk (`/app/omni-content-post`). Người dùng CRM làm việc ở `/crm`; menu trái của `/crm` chỉ có các mục của app crm.
- Đường duy nhất đã dùng để thêm giao diện vào `/crm` mà không sửa repo crm là patch frontend trong `crm_frontend_patch/0001-zalo-tab.patch` (ADR 0016): áp bằng `apply.sh`, dựng bằng `build.sh`, khóa đúng crm v1.73.0.
- Nguyên mẫu có: nhóm menu Marketing (Chiến dịch, Lịch đăng content, Báo cáo marketing, Kho tri thức); trong Thiết lập, mục Mạng xã hội (nhóm Tích hợp) và nhóm Marketing (AI & kiểm duyệt, Nội dung & giọng văn); trên trang Lead, mục Marketing (chiến dịch, bài đăng, kênh/UTM, giai đoạn phễu).

## Các lựa chọn

### A. Đặt giao diện ở đâu
1. **Thêm vào patch frontend 0001** (trang mới, mục menu, mục Thiết lập, mục bên trang Lead): đúng chỗ người dùng làm việc; đổi lại là thêm tệp crm bị sửa (`router.js`, `AppSidebar.vue`, `Settings/Settings.vue`) và phải build lại giao diện khi triển khai.
2. Giữ Desk, chỉ thêm mục Marketing trên trang Lead bằng bố cục cột bên (`CRM Fields Layout`): không build, nhưng không đạt yêu cầu menu và Thiết lập.

### B. Phạm vi so với nguyên mẫu
1. **Chiến dịch, bài đăng, lịch, báo cáo, thiết lập, mục Lead**; không làm phần AI (soạn kế hoạch, chấm điểm, Kho tri thức hỏi đáp) và bỏ bước duyệt (ADR 0029).
2. Đủ nguyên mẫu kể cả AI: phụ thuộc giai đoạn 4 của kế hoạch, ngoài phạm vi.

## Quyết định

Chọn A1, B1.

- Menu trái: nhóm **Marketing** gồm **Chiến dịch** (`/crm/marketing/campaigns`), **Lịch đăng content** (`/crm/marketing/calendar`), **Báo cáo marketing** (`/crm/marketing/report`). Nhóm chỉ hiện khi `omni_crm.api.marketing_ui.ui_state` trả `enabled` (người dùng đọc được Omni Campaign); không có omni_crm thì nhóm ẩn, CRM chạy như cũ.
- Chiến dịch: danh sách lọc theo trạng thái, thêm và sửa chiến dịch, kế hoạch bài của chiến dịch; mỗi bài mở hộp soạn (nội dung, ảnh, giờ, kênh, cách đăng, trụ cột, phễu, link đích, link có UTM) với các nút Lên lịch, Huỷ lịch, Huỷ bài, Mở lại, Đã kiểm Page.
- Lịch: lưới tháng, bấm ngày xem bài trong ngày, trạng thái, lỗi, Xem bài, Đăng lại (bài Lỗi hoặc Bỏ lỡ).
- Báo cáo: lọc theo chiến dịch, kênh, khoảng ngày; số bài, đã đăng, cần xử lý, tỷ lệ đăng thành công, tiếp cận, tương tác, Lead, Deal; hiệu quả theo trụ cột, phân bổ theo phễu, bảng bài, lỗi đăng gần đây.
- Thiết lập (cửa sổ Settings của CRM): **Tích hợp › Mạng xã hội** (trạng thái kênh Page: Page, kết nối, kết quả kiểm token; liên kết cấu hình kênh trên Desk); nhóm **Marketing**: **Đăng bài & kho ảnh** (công tắc tự đăng, kênh, cửa sổ đăng muộn, chỉ số, kho ảnh S3, nút Kiểm kho ảnh S3) chỉ Omni Admin và System Manager sửa; **Nội dung** (trụ cột) cho người sửa được trụ cột.
- Trang Lead: mục **Marketing** đầu cột bên, chỉ đọc: chiến dịch đầu tiên, chiến dịch gần nhất, bài đăng (bấm mở hộp bài), kênh/UTM, giai đoạn phễu của bài, trang đích. Ẩn khi Lead chưa có dữ liệu marketing.
- Máy chủ: `api/marketing_ui.py`, mọi hàm kiểm quyền DocType hoặc quyền đọc Lead; ghi qua `Document.save` để giữ mọi kiểm tra của ADR 0029, 0030. Hàm thuần của giao diện ở `utils/marketing.js` có vitest.

## Hệ quả

- `0001-zalo-tab.patch` thêm 3 tệp crm bị sửa (`router.js`, `components/Layouts/AppSidebar.vue`, `components/Settings/Settings.vue`) ngoài 6 tệp cũ. Nâng cấp crm phải tạo lại patch như ADR 0016.
- Triển khai cần `apply.sh` (bản patch mới) và `build.sh` ngoài giờ làm việc; Desk vẫn dùng được song song.
- Không làm: phần AI và Kho tri thức của nguyên mẫu; cột "Chiến dịch nguồn" ở danh sách Lead (người dùng tự thêm cột trong `/crm`).
- `tests/test_v2_02_checks.py` thêm các hàm của `api/marketing_ui.py`.
