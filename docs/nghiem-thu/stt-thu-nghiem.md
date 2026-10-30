# Thử nghiệm chuyển giọng nói (D3-03): cổng quyết định cho D3-04

## 1. Kết luận hiện tại

**CHƯA ĐO.** Không có con số WER, tốc độ, chi phí hay tỉ lệ trường trích xuất đúng nào trong tài liệu này, và không có kết luận làm hay hoãn D3-04.

Còn thiếu:

- nhà cung cấp chuyển giọng nói (danh sách 2–3 bên, docs/12 Q13) và khóa dùng thử;
- 20 ghi âm thật đã được phép dùng nội bộ;
- bản chuẩn gõ tay cho ít nhất 5 ghi âm;
- quyết định Q11 (được gửi ghi âm khách sang dịch vụ ngoài không) và Q7 (điều khoản dữ liệu của nhà cung cấp, thời hạn lưu văn bản).

Đã làm được (không cần các thứ trên): interface, registry, nhà cung cấp giả, cổng Q11, phần tính toán và script thử nghiệm, đều có test. Chưa có adapter nhà cung cấp thật vì chưa có danh sách; không đoán API.

## 2. Ngưỡng và hai nhánh

Ngưỡng mặc định (docs/12 Q14): nhà cung cấp **ĐẠT** khi WER <= 25% **và** trường trích xuất đúng >= 80% trên ghi âm. WER chỉ được tin khi có từ 5 ghi âm có bản chuẩn trở lên (WER gộp: tổng lỗi / tổng số từ chuẩn). Thiếu WER, thiếu đánh dấu của người duyệt hoặc ít hơn 5 bản chuẩn thì trạng thái là **CHƯA ĐO**, không bao giờ là đạt hay không đạt.

| Kết quả sau khi có số liệu | Nhánh |
| --- | --- |
| Có ít nhất một nhà cung cấp ĐẠT | Làm D3-04 với nhà cung cấp đó (kèm Q7 đã chốt cho điều khoản dữ liệu) |
| Mọi nhà cung cấp đã đo và đều KHÔNG ĐẠT | Hoãn D3-04; Đợt 3 chỉ gồm bot nháp và mở tự động theo nhóm |
| Còn nhà cung cấp CHƯA ĐO và chưa ai ĐẠT | Chưa kết luận; bổ sung số liệu |

Hàm `omni_crm.stt.trial.overall` và `run_trial.summarize_review` tính nhánh này từ số liệu; người duyệt vẫn là người quyết định.

## 3. Bảng so sánh (điền khi đã chạy)

| Nhà cung cấp | Số ghi âm chạy / lỗi | Số bản chuẩn | WER gộp | Phân biệt người nói | Thời gian xử lý TB (giây) | Chi phí / phút | Trường trích xuất đúng | Điều khoản dữ liệu (Q7) | Trạng thái |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| (chưa chọn) | | | | | | | | | CHƯA ĐO |
| (chưa chọn) | | | | | | | | | CHƯA ĐO |
| (chưa chọn) | | | | | | | | | CHƯA ĐO |

Cột "Điều khoản dữ liệu" người duyệt đọc điều khoản chính thức của nhà cung cấp: có dùng âm thanh/văn bản để huấn luyện không, nơi đặt máy chủ, thời hạn lưu và xóa, có thỏa thuận xử lý dữ liệu không. Ghi link tài liệu vào docs/12.

## 4. Cách chạy trên máy có khóa (từng bước)

1. Chốt Q11 và Q7 với chủ dự án. Chỉ sau đó mới đặt `omni_ai_allow_customer_data = 1` trong site_config của MÁY THỬ (không phải production). Không có cờ này, nhà cung cấp thật chỉ nhận ghi âm gắn `"synthetic": true`.
2. Đặt khóa dùng thử vào biến môi trường của máy thử (tên do adapter quy định, ví dụ `STT_<TEN>_API_KEY`). Không dán khóa vào chat, repo hay site_config.
3. Thêm adapter cho từng nhà cung cấp đã chọn (mục 5), chạy test của adapter.
4. Chuẩn bị thư mục **ngoài repo**, ví dụ `~/stt_thu/ghi_am/`: mỗi ghi âm `c01.wav` (hoặc mp3, m4a, ogg, flac), bản chuẩn gõ tay tùy chọn `c01.txt` (cần cho ít nhất 5 ghi âm), meta tùy chọn `c01.json` như `{"stereo": true, "duration_seconds": 95}` (WAV tự đọc được thời lượng). Ghi âm tổng hợp, không có thông tin khách, mới được gắn `"synthetic": true`.
5. Bảng giá công bố của nhà cung cấp, do người dùng điền, ngoài repo: `{"TenNhaCungCap": {"per_minute": 0.006, "currency": "USD"}}`. Thiếu giá thì cột chi phí để trống, không bịa số.
6. Chạy:

   ```bash
   cd ~/frappe-bench
   bench --site <site_thu> execute omni_crm.tests.stt_trial.run_trial.run --kwargs \
     "{'audio_dir': '~/stt_thu/ghi_am', 'out_dir': '~/stt_thu/ket_qua', \
       'providers': ['TenNhaCungCap'], 'pricing_file': '~/stt_thu/bang_gia.json'}"
   ```

   Tương đương dòng lệnh: `python -m omni_crm.tests.stt_trial.run_trial --site <site_thu> --audio-dir ... --out-dir ... --provider TenNhaCungCap --pricing-file ...` (thêm `--no-analyze` để chỉ đo WER, thời gian, chi phí).
7. Kết quả trong `out_dir`: `ket-qua.json` (văn bản thu được từng ghi âm: có lời khách), `bang-tong-hop.csv` (WER, số lỗi, thời gian, chi phí; không có lời khách), `bang-danh-dau.csv` (mỗi trường bot trích xuất một dòng).
8. Người duyệt đọc từng dòng `bang-danh-dau.csv`, so với ghi âm/bản chuẩn, điền cột `correct` là `Đúng` hoặc `Sai` (để trống = chưa duyệt).
9. Gộp và lấy nhánh: `python -m omni_crm.tests.stt_trial.run_trial --site <site_thu> --out-dir ~/stt_thu/ket_qua --summarize-review`.
10. Điền bảng mục 3 từ `bang-tong-hop.csv` và bước 9, ghi kết luận theo mục 2.
11. Xóa `ket-qua.json`, `bang-danh-dau.csv` và ghi âm khỏi máy thử khi xong; giữ `bang-tong-hop.csv`. Gỡ `omni_ai_allow_customer_data` khỏi site_config máy thử nếu chỉ bật để thử.

Phép đo: WER dùng Unicode NFC, chữ thường, bỏ dấu câu và ký hiệu, nhưng **giữ dấu thanh** (mua và múa là hai từ khác). WER có thể vượt 100%. Tốc độ là thời gian xử lý của lời gọi (không gồm thử lại chờ). Bước phân tích chạy qua `ai_client.call_tool`, nên cần khóa Claude API, `daily_token_budget` > 0 và cũng chịu cổng Q11; ở môi trường chưa đủ điều kiện kết quả ghi "chưa đo" chứ không phải số 0.

## 5. Khung adapter nhà cung cấp (thêm khi đã chọn)

1. Tạo `omni_crm/stt/<ten>.py` với lớp thực hiện `STTProvider`: `name`, `is_real = True`, `transcribe(audio, mime, *, stereo) -> Transcript(text, segments[{speaker, start, end, text}], confidence)`.
2. Đăng ký lười: thêm `"TenNhaCungCap": ("omni_crm.stt.<ten>", "TenLop")` vào `_LAZY` của `omni_crm/stt/registry.py`.
3. Mọi lời gọi HTTP có timeout (`DEFAULT_TIMEOUT_SECONDS` = 10 giây; ghi âm dài có thể cần cấu hình riêng, ghi lý do). Lỗi 408/429/5xx, hết thời gian, mất kết nối ném `TransientSTTError`; lỗi còn lại ném `STTError`. Thử lại có giới hạn do `omni_crm.stt.client.transcribe` làm (tối đa 2 lần); adapter không tự thử lại.
4. Không bao giờ ghi khóa, nội dung âm thanh hay văn bản khách vào log, Error Log hay thông điệp lỗi. Khóa đọc từ biến môi trường hoặc trường Password (`get_password()`).
5. Không tự gọi adapter ngoài `omni_crm.stt.client.transcribe`: nó chặn nhà cung cấp `is_real` khi Q11 chưa chốt (site_config `omni_ai_allow_customer_data`), giống `bot/ai_client.py`.
6. Test với tệp âm thanh mẫu nhỏ và HTTP giả (mock), như test của Stringee: kiểm yêu cầu gửi đi đúng, phân tích phản hồi (kể cả phân biệt người nói), lỗi tạm thời/không tạm thời, khóa không lọt vào lỗi.
7. Đọc tài liệu chính thức của nhà cung cấp (không đoán), ghi link và điều đã xác nhận vào docs/12 mục "Đã xác nhận".

## 6. Rủi ro đã biết

- Nhãn `synthetic` lấy từ tệp meta cạnh ghi âm: gán nhầm sẽ đưa ghi âm thật qua cổng Q11. Chỉ gắn cho ghi âm do ta tự tạo.
- Bot phân tích đọc toàn văn bản như lời khách, không tách người nói; tỉ lệ trường đúng có thể cao hơn thực tế đối với nội dung do sale nói.
- Che SĐT/email (mặc định bật) làm bot không trích được liên hệ; xem docs/12 (`omni_ai_unmask_pii`) trước khi đọc kết quả trường `phones`/`emails`.
- Bản ghi tổng đài thường 8 kHz, ồn: WER trên ghi âm thật có thể cao hơn dữ liệu mẫu công khai của nhà cung cấp.
