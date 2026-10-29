# D2-01 ★ · Interface tổng đài chung

**Phụ thuộc:** Đợt 1 đã phát hành. Có thể song song với D2-04. **Thời lượng dự kiến:** 1,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/07-kenh-tong-dai.md (toàn bộ), docs/02 mục 2, 3, 8 và dòng Omni Agent Map, docs/03 mục 1–3.

Nhiệm vụ: xây phần chung của kênh tổng đài, chạy được hoàn toàn với `FakeProvider`. Adapter Stringee làm ở D2-02.

Việc cần làm:
1. DocType Đợt 2 cho tổng đài: `Omni Call`, bảng con `Omni Agent Map` (trường `agent_map` trên Omni Channel), các trường Settings `missed_call_callback_minutes`, `callback_fallback_user`. Ràng buộc duy nhất `Omni Call.call_id`. Quyền theo docs/02 mục 16.
2. `channels/telephony/base.py`: `CallEvent`, Protocol `TelephonyProvider`, `LookupResult` (đúng docs/07 mục 1). `channels/telephony/registry.py`: tra provider theo `Omni Channel.telephony_provider`.
3. `channels/telephony/adapter.py::TelephonyAdapter` bọc provider thành `ChannelAdapter` (docs/07 mục 1, đoạn cuối): chỉ trạng thái cuối tạo `InboundEvent(event_type="call_ended", external_message_id=call_id)` — một cuộc gọi đúng một tin vào; sự kiện trung gian nối vào `Omni Call.raw_events` (docs/07 mục 2.2).
4. `channels/telephony/fake.py::FakeProvider` (chỉ nạp trong test và khi `developer_mode`): payload JSON đơn giản do ta định nghĩa, `fetch_recording` trả tệp âm thanh nhỏ mẫu, `list_calls` trả danh sách cấu hình được.
5. Webhook: `api/webhooks.py::telephony_event` (dùng khung chung, xác thực `t` = webhook_token bằng compare_digest, cộng xác thực riêng của provider) và `telephony_lookup` (đồng bộ, docs/07 mục 2.1: chỉ đọc, < 300 ms, lỗi gì cũng trả phản hồi rỗng hợp lệ của provider).
6. Worker cho `call_ended` (docs/07 mục 2.2 bước 1–4): định danh qua SĐT (tín hiệu mạnh), upsert `Omni Call`, upsert `CRM Call Log` dùng đúng tên trường đã xác nhận ở G1 (đọc lại crm_call_log.json; đặt ghi âm, tham chiếu Lead/Deal), ánh xạ agent → User. Cuộc gọi hiện trong tab Calls của Lead trong CRM.
7. Hộp thư: hội thoại hiển thị thêm dòng cuộc gọi (chiều, trạng thái, thời lượng, người nghe).
8. Màn hình giám sát: khối Tổng đài (cuộc gọi hôm nay, gọi nhỡ; các số khác bổ sung ở D2-03).

Test: các ca tra cứu, sự kiện, gửi trùng, sai thứ tự ở docs/07 mục 6 với FakeProvider; Call Log hiện qua API hoạt động của CRM; tra cứu khi DB bị làm chậm (mock) vẫn trả phản hồi hợp lệ.

Hoàn thành khi: test xanh; toàn bộ luồng cuộc gọi chạy với FakeProvider trên dev (dán bằng chứng: Lead, Omni Call, CRM Call Log tạo ra).

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Mở một Lead có cuộc gọi giả: tab Calls trong CRM hiện cuộc gọi.
2. Gọi `telephony_lookup` với token sai: không lộ thông tin khách.
