# D2-03 · Gọi nhỡ, việc gọi lại, đối soát đêm

**Phụ thuộc:** D2-02. **Thời lượng dự kiến:** 1 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/07-kenh-tong-dai.md mục 2.2 (bước 5), 2.3, docs/11 mục 1 (khối Tổng đài).

Việc cần làm:
1. `utils/business_hours.py`: `add_business_minutes(dt, minutes)`, `is_business_time(dt)`, `next_business_start(dt)` đọc `Omni Settings.business_hours` (JSON), múi giờ của hệ thống. Test kỹ: qua đêm, qua cuối tuần, thứ Bảy nửa ngày, ngày lễ (trường `Omni Settings.holidays` — tạo ở prompt này, mặc định rỗng; ghi vào docs/12 mục D là việc cần anh điền).
2. `crm_bridge/task.py::create_callback_task(call)` đúng docs/07 mục 2.2 bước 5: tạo CRM Task (tên trường theo G1), giao người phụ trách Lead hoặc `callback_fallback_user`, hạn theo giờ làm việc; gộp nhiều cuộc gọi nhỡ vào một việc đang mở (cập nhật mô tả "Khách đã gọi nhỡ N lần, lần cuối …"); tự đóng việc khi có cuộc gọi nghe máy với khách đó sau thời điểm tạo việc.
3. `channels/telephony/registry.py::reconcile_yesterday()` đúng docs/07 mục 2.3; bật lịch 01:30 trong hooks.py; ghi kết quả (tổng, đã có, bù thêm) vào cache cho màn hình giám sát; cảnh báo khi bù > 1%.
4. Màn hình giám sát: bổ sung số việc gọi lại quá hạn, số bù từ đối soát; cảnh báo việc gọi lại quá hạn > 0 (gửi cho người được giao và Omni Admin, chống lặp).

Test: ca gọi nhỡ ở docs/07 mục 6; đối soát bù đúng 2 cuộc thiếu, chạy lại không bù thêm; hạn gọi lại đúng khi gọi nhỡ lúc 17:20 thứ Sáu (giờ làm 08:00–17:30, thứ Bảy 08:00–12:00).

Hoàn thành khi: test xanh; trên staging để nhỡ một cuộc gọi thật/giả → có việc gọi lại đúng người, đúng hạn.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Gọi nhỡ 2 lần liên tiếp: chỉ 1 việc gọi lại, mô tả ghi 2 lần.
2. Gọi lại khách và khách nghe máy: việc tự đóng.
