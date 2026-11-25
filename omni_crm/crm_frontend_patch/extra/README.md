# Bản vá giao diện bổ sung (áp tay, không qua apply.sh)

`bot-drafts-crm.patch` (V3-13): khung nháp bot trong tab Zalo của Lead/Deal (`BotDrafts.vue` + một chỗ gắn trong
`ZaloTab.vue`), để sale duyệt nháp ngay trong app CRM thay vì Hộp thư Desk.

- Tạo trên `ZaloTab.vue` đang chạy ở máy chủ miyano ngày 2026-11-16. Bản này gồm cả thay đổi chưa commit của người
  khác trong `0001-zalo-tab.patch`, nên KHÔNG đặt tên `NNNN-*.patch`. Nếu đặt vậy, `apply.sh` sẽ tự áp nó lên cây sạch và từ chối.
- Áp tay sau 0001/0002, tại `apps/crm`: `git apply <đường dẫn>/bot-drafts-crm.patch`, rồi chạy `../omni_crm/omni_crm/crm_frontend_patch/build.sh`.
- Gỡ: `git apply -R <đường dẫn>/bot-drafts-crm.patch`, rồi build lại.
- Khi người phụ trách 0001 commit bản mới, nên gộp bản vá này vào 0001.

## `demo-calendar.patch` (trang "Lịch demo")

Trang riêng `/crm/demo-calendar` (mục "Lịch demo" ở thanh bên, dưới "Call Logs"): lưới tháng giống trang "Lịch đăng
content", mỗi lịch demo là một dòng trong ô ngày, bấm vào mở Lead. Dữ liệu từ `omni_crm.api.script.demo_calendar`
(đọc `CRM Lead.omni_demo_at`); khung của Lead người xem không được đọc chỉ hiện "Đã kín".

- Thêm tệp mới `frontend/src/pages/DemoCalendar.vue`, một route trong `router.js` và một dòng trong `AppSidebar.vue`.
- Tạo trên `router.js` / `AppSidebar.vue` ĐANG CHẠY ở máy chủ miyano ngày 2026-11-18 (đã có 0001 + 0002 + thay đổi chưa
  commit của người khác), nên KHÔNG đặt tên `NNNN-*.patch` và không áp được lên crm sạch.
- Áp tay sau 0001/0002, tại `apps/crm`: `git apply <đường dẫn>/demo-calendar.patch`, rồi `build.sh`.
- Gỡ: `git apply -R <đường dẫn>/demo-calendar.patch`, rồi build lại.
