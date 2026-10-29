# D3-04 · Ghi âm, chuyển văn bản, tóm tắt cuộc gọi

**Phụ thuộc:** D3-03 kết luận "làm". Bỏ qua prompt này nếu cổng D3-03 không đạt. **Thời lượng dự kiến:** 1,5 ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/07 mục 2.4, docs/08-bot-ai.md mục 9, docs/nghiem-thu/stt-thu-nghiem.md (nhà cung cấp được chọn: {{STT_DUOC_CHON}}), docs/11 mục 6.

Việc cần làm:
1. Trường Đợt 3: `Omni Settings.recording_enabled`, `stt_provider` (Select), `stt_api_key` (Password), `stt_min_confidence` (Float, mặc định theo báo cáo thử nghiệm); trên Omni Call: `recording_file`, `transcript`, `summary`, `stt_status`. (`recording_retention_days` đã có từ D1-02.)
2. Job `channels/telephony/recording.py::fetch_recording(call)` (hàng đợi omni): tải bằng `provider.fetch_recording`, lưu `File` private gắn Omni Call (tên tệp không chứa SĐT), cập nhật CRM Call Log trường ghi âm trỏ file private; thử lại theo chính sách chung; lỗi vĩnh viễn → `stt_status = Failed` + lý do.
3. Job `stt_and_summarize(call)`: `transcribe` (stereo nếu tổng đài cung cấp 2 kênh) → `confidence < stt_min_confidence` → ghi "Bản ghi chất lượng thấp, nghe ghi âm", `stt_status = Skipped`; ngược lại lưu transcript, chạy phân tích (tái dùng `analyze` với đầu vào là transcript, vai trò khách/nhân viên theo kênh âm thanh) và tóm tắt 3–5 câu (`call_tool(kind="Summarize")`, câu lệnh `summarize_v1.md` mới, phong cách giống analyze_v1, nhấn mạnh: chỉ tóm tắt điều được nói, không suy đoán) → `Omni Call.summary` + một CRM Note trên Lead (trường theo G1).
4. Nối: worker `call_ended` có `recording_ref` và `recording_enabled` → enqueue fetch_recording → xong enqueue stt_and_summarize.
5. Tác vụ đêm xóa ghi âm quá `recording_retention_days` (xóa File, giữ transcript/summary trừ khi chính sách Q7 nói khác — đọc docs/12).
6. `erase_customer_data` (D1-10) mở rộng: xóa ghi âm, transcript, summary của Lead.
7. Quyền: chỉ người xem được Lead mới mở được file ghi âm (kiểm `has_permission` ở hook quyền của File hoặc qua API phát file riêng).

Test (FakeProvider, FakeSTT, FakeAIClient): luồng đầy đủ tạo file private, transcript, summary, Note; confidence thấp → Skipped; Sales User không có quyền Lead không tải được file; xóa theo hạn; erase xóa đủ.

Hoàn thành khi: test xanh; trên staging một cuộc gọi thật có tóm tắt hiện trên Lead trong vài phút sau khi gác máy.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra

1. Gọi thử một cuộc 1–2 phút: Lead có Note tóm tắt đúng ý chính.
2. Đăng nhập tài khoản sale khác không phụ trách Lead: không mở được ghi âm.
