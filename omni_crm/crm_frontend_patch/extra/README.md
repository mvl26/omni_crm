# Bản vá giao diện bổ sung (áp tay, không qua apply.sh)

`bot-drafts-crm.patch` (V3-13): khung nháp bot trong tab Zalo của Lead/Deal (`BotDrafts.vue` + một chỗ gắn trong
`ZaloTab.vue`), để sale duyệt nháp ngay trong app CRM thay vì Hộp thư Desk.

- Tạo trên `ZaloTab.vue` đang chạy ở máy chủ miyano ngày 2026-11-16. Bản này gồm cả thay đổi chưa commit của người
  khác trong `0001-zalo-tab.patch`, nên KHÔNG đặt tên `NNNN-*.patch`. Nếu đặt vậy, `apply.sh` sẽ tự áp nó lên cây sạch và từ chối.
- Áp tay sau 0001/0002, tại `apps/crm`: `git apply <đường dẫn>/bot-drafts-crm.patch`, rồi chạy `../omni_crm/omni_crm/crm_frontend_patch/build.sh`.
- Gỡ: `git apply -R <đường dẫn>/bot-drafts-crm.patch`, rồi build lại.
- Khi người phụ trách 0001 commit bản mới, nên gộp bản vá này vào 0001.

## `demo-calendar.patch` (lịch demo ở trang danh sách Lead)

Khung nổi "Lịch demo" ở góc phải dưới trang danh sách Lead: lưới tuần Thứ Hai - Thứ Sáu, 8:00-17:30, mỗi khung 30 phút,
kèm danh sách lịch trong tuần. Dữ liệu từ `omni_crm.api.script.demo_calendar` (đọc `CRM Lead.omni_demo_at`).

- Thêm 1 tệp mới `frontend/src/components/DemoCalendar.vue` và 2 dòng trong `frontend/src/pages/Leads.vue` (tệp này không
  bị 0001/0002 sửa). Tạo trên crm v1.73.0 sạch.
- Áp tay tại `apps/crm`: `git apply <đường dẫn>/demo-calendar.patch`, rồi build giao diện.
- Gỡ: `git apply -R <đường dẫn>/demo-calendar.patch`, rồi build lại.
- App `omni_crm` chưa có API thì khung tự ẩn.
