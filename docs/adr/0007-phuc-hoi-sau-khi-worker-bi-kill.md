# 0007 · Phục hồi sau khi worker bị kill (-9)

- Ngày: 2026-11-02
- Trạng thái: Đề xuất
- Prompt: V2-01 (gap-report 03.11, 10.05); liên quan V2-08 (kịch bản "Redis khởi động lại")

## Bối cảnh
D1-11 đo được: sau `kill -9` worker giữa lượt, tin của các khách đang xử lý đứng yên ~15 phút. Job bị bỏ lại trong `StartedJobRegistry` của RQ nên `enqueue(deduplicate=True)` của tác vụ quét (job_id `omni-identity::<khóa>`) bị bỏ qua cho tới khi RQ dọn (timeout job + chu kỳ bảo trì). Không mất tin (`docs/nghiem-thu/dot-1.md` mục 5, E3). Khi chạy quảng cáo, một đợt lead dồn về cùng lúc làm chỗ này đáng chú ý hơn.

## Các lựa chọn
1. Chấp nhận — giảm thiểu bằng supervisor khởi động lại worker ngay và cảnh báo "tin chờ lâu" (đã có, ngưỡng 5 phút). Không đổi mã.
2. Tác vụ quét enqueue tin `New` cũ hơn N phút (đề xuất N = 10) bằng `job_id` có hậu tố riêng (cùng cách worker đã đẩy job tiếp nối, ADR 0001) để bỏ qua job zombie. An toàn vì worker lấy khóa Redis theo khách trước khi xử lý (hai job cùng khách không chạy song song); đổi hành vi hàng đợi nên cần duyệt.
3. Giảm `timeout` job `omni` (hiện 300 giây) — ít tác dụng với zombie sau kill, ảnh hưởng job dài (bot).

## Quyết định (đề xuất)
Chọn 1 cho V2-01 (không đổi luồng dữ liệu khi chưa có staging để đo lại), ghi vào sổ tay sự cố; cân nhắc 2 nếu kịch bản "kill worker" ở V2-08 trên staging vẫn cho thời gian phục hồi quá 5 phút. Chưa làm 2 vì cần đo lại trên Redis/worker thật.

## Hệ quả
- `docs/11` phải ghi rõ: sau khi worker bị kill, tin có thể chậm tới ~15 phút; cảnh báo "tin chờ lâu" sẽ nổ.
- V2-08 đưa kịch bản này vào checklist đo trên staging.
