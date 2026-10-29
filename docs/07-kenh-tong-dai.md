# 07 · Kênh tổng đài (Đợt 2, ghi âm ở Đợt 3)

Nhà cung cấp tổng đài **chưa chốt**. Vì vậy kênh này gồm một **interface chung** và **adapter đầu tiên cho Stringee**. Đổi nhà cung cấp chỉ cần viết thêm một adapter; pipeline, DocType và CRM không đổi.

## 1. Interface

```python
# channels/telephony/base.py
@dataclass(frozen=True)
class CallEvent:
    call_id: str
    direction: str            # "Inbound" | "Outbound"
    from_number: str          # đã chuẩn hóa E.164 nếu được
    to_number: str
    status: str               # "Ringing" | "Answered" | "Missed" | "Busy" | "Failed" | "Voicemail" | "Ended"
    started_at: datetime | None
    answered_at: datetime | None
    ended_at: datetime | None
    duration_seconds: int | None
    agent_ref: str | None     # mã nhân viên/máy nhánh phía nhà cung cấp
    recording_ref: str | None
    raw: dict

@dataclass(frozen=True)
class LookupResult:
    is_new: bool              # True = chưa có Lead cho số này
    customer_name: str | None
    lead: str | None          # tên CRM Lead
    owner: str | None         # user phụ trách (email)
    owner_name: str | None
    lead_url: str | None      # đường dẫn mở Lead trong CRM

class TelephonyProvider(Protocol):
    name: str
    def verify_request(self, raw_body: bytes, headers: dict, query: dict, channel_doc) -> bool: ...
    def parse_event(self, raw_body: bytes, headers: dict, channel_doc) -> list[CallEvent]: ...
    def lookup_response(self, result: "LookupResult", channel_doc) -> dict | str: ...  # đúng định dạng nhà cung cấp chờ
    def parse_lookup_request(self, query: dict, raw_body: bytes) -> tuple[str, str]: ...  # (số gọi đến, số nhận)
    def fetch_recording(self, recording_ref: str, channel_doc) -> tuple[bytes, str]: ...  # (nội dung, mime)
    def list_calls(self, since: datetime, until: datetime, channel_doc) -> list[CallEvent]: ...
    def click_to_call(self, agent_ref: str, to_number: str, channel_doc) -> str: ...      # để sau; được phép NotImplementedError
```

`TelephonyAdapter` (thuộc `channels/`) bọc một `TelephonyProvider` để khớp `ChannelAdapter`: `parse()` đổi mỗi `CallEvent` kết thúc cuộc gọi thành `InboundEvent(event_type="call_ended", external_message_id=call_id, external_sender_id=<số khách>)`.

Số khách: cuộc gọi đến → `from_number`; gọi ra → `to_number`.

## 2. Luồng

### 2.1 Tra cứu khi chuông reo (đồng bộ)

`api/webhooks.py::telephony_lookup`:

1. Xác thực (mục 4). Phải trả lời trong thời gian nhà cung cấp chờ (Stringee cấu hình được `get_customer_info_timeout`) — nhắm < 300 ms.
2. Chuẩn hóa số → tìm `Omni Identity` loại Telephony, rồi `CRM Lead.omni_phone_e164`.
3. Trả: tên khách, mã Lead, sale phụ trách (lead owner), đường dẫn mở Lead. Không có → trả "Khách mới".
4. Chỉ đọc DB, **không ghi**, không enqueue. Lỗi bất kỳ → vẫn trả phản hồi rỗng hợp lệ (không làm hỏng cuộc gọi).

### 2.2 Sự kiện cuộc gọi

Vào cổng tiếp nhận như mọi kênh. Chỉ sự kiện **kết thúc** (trạng thái cuối) mới tạo `InboundEvent`, với `external_message_id = call_id` (nên `dedupe_key = "{channel}:{call_id}"` — một cuộc gọi chỉ một tin vào, kể cả khi đối soát đêm bù lại). Sự kiện trung gian (đổ chuông, nhấc máy) không tạo tin vào; nếu cần lưu thì nối vào `Omni Call.raw_events` (tạo `Omni Call` tạm nếu chưa có).

Worker với `event_type = call_ended`:

1. Định danh khách qua SĐT (tín hiệu mạnh) → Lead (tạo mới nếu chưa có, nguồn "Điện thoại").
2. Upsert `Omni Call` theo `call_id`.
3. Upsert `CRM Call Log` tương ứng (đọc `crm_call_log.json` để lấy đúng tên trường: loại cuộc gọi, trạng thái, thời lượng, số gọi/nhận, người nhận, ghi âm, tham chiếu Lead/Deal).
4. Ánh xạ `agent_ref` → User qua bảng `Omni Agent Map` của kênh.
5. Gọi nhỡ (đến, không ai nghe) → `crm_bridge/task.py::create_callback_task`:
   - Tạo `CRM Task` (xác nhận trường) tiêu đề "Gọi lại khách {tên/SĐT}", tham chiếu Lead, giao cho lead owner (không có → theo Assignment Rule của CRM hoặc người trực cấu hình), hạn = giờ gọi + `missed_call_callback_minutes` tính trong giờ làm việc.
   - Một khách gọi nhỡ nhiều lần trong khi việc gọi lại còn mở → **không tạo việc mới**, cập nhật số lần gọi nhỡ vào việc cũ.
   - Sau đó có cuộc gọi (đến hoặc đi) được nghe máy với khách này → tự đóng việc gọi lại.
6. Có `recording_ref` và công tắc ghi âm bật (Đợt 3) → enqueue `fetch_recording`.

### 2.3 Đối soát đêm

`reconcile_yesterday` (01:30): với mỗi kênh tổng đài bật, `list_calls(hôm qua)`; cuộc gọi nào chưa có `Omni Call` → tạo `InboundEvent` và lưu qua `gateway.save_inbound` (đi chung đường). Báo cáo số cuộc gọi bù vào màn hình giám sát. Số bù > 1% → cảnh báo (webhook có vấn đề).

### 2.4 Ghi âm (Đợt 3)

`fetch_recording(call)`: tải ngay sau cuộc gọi (nhà cung cấp có thể chỉ giữ file có hạn — Stringee giữ 30 ngày), lưu `File` **private** gắn `Omni Call`, rồi enqueue chuyển giọng nói (xem `docs/08` mục 9). Xóa file sau `recording_retention_days`.

## 3. Adapter Stringee

Đối chiếu tài liệu tại developer.stringee.com trước khi code. Những điểm đã đọc được:

| Việc | Cơ chế Stringee |
| --- | --- |
| Xác thực REST API | Header `X-STRINGEE-AUTH` chứa JWT HS256: header `{"typ":"JWT","alg":"HS256","cty":"stringee-api;v=1"}`, payload `{"jti","iss": <API key sid>,"exp","rest_api": true}`, ký bằng API key secret |
| Tra cứu khách | Cấu hình `get_customer_info_url`; Stringee gửi GET kèm số gọi đến (`from`), số nhận (`to`), mã dự án; có `get_customer_info_timeout` |
| Sự kiện cuộc gọi | Cấu hình `event_url`; Stringee gửi sự kiện cuộc gọi tới đây để lưu nhật ký, làm báo cáo |
| Ghi âm | Hành động `record` trong SCCO có `eventUrl`; xong ghi âm Stringee gửi `recording_url`, `call_id`, thời gian; tải file bằng GET kèm JWT |
| Thời hạn ghi âm | Lưu trên Stringee 30 ngày |
| Nhật ký cuộc gọi | Call log REST API, lọc theo mã cuộc gọi hoặc số, tối đa 100 bản ghi mỗi trang |

Việc agent phải tự xác nhận: định dạng chính xác phản hồi cho `get_customer_info_url`; tên trường và các trạng thái trong payload sự kiện; Stringee có ký webhook hay không. Nếu **không có chữ ký**: bảo vệ bằng `webhook_token` trong URL (mục 4) và trong worker gọi Call log API xác minh `call_id` có thật trước khi tạo việc gọi lại (đặt `Omni Call.unverified = 1` nếu API lỗi, vẫn xử lý nhưng cảnh báo).

## 4. Bảo mật webhook tổng đài

- URL chứa `ch` và `t` (`webhook_token`, chuỗi ngẫu nhiên ≥ 32 ký tự); so sánh bằng `hmac.compare_digest`.
- Nếu nhà cung cấp hỗ trợ chữ ký hoặc danh sách IP nguồn, dùng thêm.
- Token lộ → đổi token trên `Omni Channel` và cấu hình lại phía nhà cung cấp; ghi trong `docs/11-van-hanh.md`.

## 5. Dữ liệu bổ sung

- DocType bảng con **`Omni Agent Map`** (cha: Omni Channel, Đợt 2): `agent_ref` (Data), `user` (Link User), `extension` (Data).
- `Omni Settings.missed_call_callback_minutes` (Int, mặc định 30), `Omni Settings.callback_fallback_user` (Link User).

## 6. Test bắt buộc

- Tra cứu: số có Lead, số mới, số sai định dạng, DB chậm (mock) vẫn trả trong thời hạn.
- Sự kiện: nghe máy, gọi nhỡ, gọi ra; gửi trùng; sự kiện đến sai thứ tự (kết thúc trước bắt đầu).
- Gọi nhỡ 3 lần → 1 việc gọi lại; gọi lại thành công → việc tự đóng.
- Đối soát: 2 cuộc gọi thiếu → được bù đúng 2, không trùng.
- JWT Stringee: tạo đúng cấu trúc (test giải mã).
- Thay provider bằng `FakeProvider` trong test → toàn bộ luồng chạy không cần Stringee.
