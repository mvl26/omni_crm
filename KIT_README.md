# Bộ kit triển khai `omni_crm` bằng Claude Code

Bộ kit này chứa toàn bộ đặc tả và câu lệnh (prompt) để các agent Claude Code xây module tích hợp **email, Zalo OA, tổng đài** với **Frappe CRM**, kèm bot AI chăm sóc và phân tích khách hàng. Anh chỉ cần chạy từng prompt theo thứ tự và duyệt kết quả ở các cổng kiểm tra.

## 1. Cấu trúc

```
crm_omnichannel_kit/
├── CLAUDE.md            # Quy tắc chung cho mọi agent — luôn nằm ở gốc repo app
├── README.md            # Tệp này
├── docs/                # Đặc tả kỹ thuật (nguồn sự thật)
│   ├── 00 … 12          # Tổng quan → vận hành → giả định cần xác nhận
│   └── adr/             # Ghi quyết định kiến trúc phát sinh
├── prompts/
│   ├── chung/           # Cách chạy prompt, prompt review độc lập, prompt sửa lỗi
│   ├── dot-1/           # Đợt 1: nền tảng + email + Zalo OA  (D1-01 … D1-11)
│   ├── dot-2/           # Đợt 2: tổng đài + bot phân tích + chạy ngầm  (D2-01 … D2-09)
│   └── dot-3/           # Đợt 3: bot nháp + ghi âm + mở tự động  (D3-01 … D3-06)
├── eval/                # Bộ đề kiểm thử bot: định dạng, mẫu, tiêu chí chấm
└── mau/                 # Mẫu kho tri thức, bảng điểm Lead để anh điền
```

## 2. Chuẩn bị trước khi chạy prompt đầu tiên

1. Máy dev có bench Frappe v15, đã cài app `crm` và một **site test riêng** (ví dụ `omni.test`).
2. Claude Code chạy tại thư mục bench (`frappe-bench/`).
3. Giải nén bộ kit vào một chỗ cố định, ví dụ `~/kits/crm_omnichannel_kit`. Prompt D1-01 sẽ tạo app và chép tài liệu vào repo.
4. Điền trước hai tệp trong `mau/` (kho tri thức, bảng điểm Lead) càng sớm càng tốt; bot cần chúng từ Đợt 2.
5. Đọc `docs/12-quyet-dinh-mo.md`: đó là các điểm agent sẽ phải xác nhận với API thật, và các quyết định anh cần chốt. Quan trọng nhất là **Q11 — chính sách gửi dữ liệu hội thoại khách sang dịch vụ AI**: chưa chốt thì Đợt 2–3 chỉ chạy được trên dữ liệu tổng hợp.

## 3. Cách chạy một prompt

Chi tiết ở `prompts/chung/00-cach-dung-prompt.md`. Tóm tắt:

1. **Mỗi prompt một phiên Claude Code mới** (`/clear` hoặc phiên mới), để agent đọc lại đặc tả, không mang nhầm ngữ cảnh.
2. Mở tệp prompt, chép **nguyên khối mã** (phần đóng khung) và dán vào Claude Code.
3. Agent làm xong sẽ báo cáo theo mẫu: đã làm gì, test nào chạy, kết quả, điều chưa chắc.
4. Anh (hoặc một dev) dùng mục **"Người duyệt kiểm tra"** cuối mỗi tệp prompt để duyệt nhanh.
5. Sau mỗi prompt quan trọng (đánh dấu ★), chạy **prompt review độc lập** (`prompts/chung/review-doc-lap.md`) ở một phiên khác. Agent review không viết code, chỉ tìm lỗi.
6. Có lỗi: dùng `prompts/chung/sua-loi.md`.

## 4. Thứ tự chạy

| Mã | Tệp | Việc | Phụ thuộc |
| --- | --- | --- | --- |
| D1-01 | dot-1/01-khoi-tao-app.md | Tạo app, CI, cấu trúc thư mục, chép tài liệu | — |
| D1-02 ★ | dot-1/02-mo-hinh-du-lieu.md | Toàn bộ DocType Đợt 1 + custom field CRM | D1-01 |
| D1-03 ★ | dot-1/03-cong-tiep-nhan.md | API nhận tin, xác thực, chống nhận trùng | D1-02 |
| D1-04 ★ | dot-1/04-hang-doi-worker.md | Hàng đợi, gộp tin, khóa theo khách, thử lại | D1-03 |
| D1-05 ★ | dot-1/05-dinh-danh-chong-trung.md | Chuẩn hóa, khớp khách, nghi trùng, gộp | D1-04 |
| D1-06 ★ | dot-1/06-cau-noi-crm.md | Tạo/cập nhật Lead, dòng thời gian, hộp thư | D1-05 |
| D1-07 | dot-1/07-kenh-email.md | Kênh email | D1-06 |
| D1-08 ★ | dot-1/08-kenh-zalo.md | Kênh Zalo OA | D1-06 |
| D1-09 ★ | dot-1/09-chieu-ra.md | Gửi trả lời từ CRM qua email/Zalo | D1-07, D1-08 |
| D1-10 | dot-1/10-giam-sat.md | Màn hình giám sát, cảnh báo, tác vụ quét | D1-09 |
| D1-11 ★ | dot-1/11-kiem-thu-dot-1.md | Kiểm thử tải, sự cố, nghiệm thu Đợt 1 | D1-10 |
| D2-01 ★ | dot-2/01-adapter-tong-dai.md | Interface tổng đài chung | Đợt 1 phát hành |
| D2-02 ★ | dot-2/02-stringee.md | Adapter Stringee | D2-01 |
| D2-03 | dot-2/03-goi-nho-va-doi-soat.md | Gọi nhỡ → việc gọi lại, đối soát đêm | D2-02 |
| D2-04 ★ | dot-2/04-ai-client-va-chi-phi.md | Client AI, hạn mức chi phí, nhật ký | Đợt 1 phát hành |
| D2-05 ★ | dot-2/05-bot-phan-tich.md | Bot trích xuất thông tin vào Lead | D2-04 |
| D2-06 | dot-2/06-cham-diem.md | Chấm điểm Lead theo bảng điểm | D2-05 |
| D2-07 ★ | dot-2/07-bot-tra-loi-chay-ngam.md | Bot soạn trả lời ở chế độ ngầm + chạy lại dữ liệu cũ | D2-05, D2-03 |
| D2-08 ★ | dot-2/08-bo-de-va-eval.md | Bộ đề, công cụ chấm bot | D2-07 |
| D2-09 ★ | dot-2/09-kiem-thu-dot-2.md | Nghiệm thu Đợt 2 | D2-03, D2-06, D2-08 |
| D3-01 ★ | dot-3/01-nhap-cho-sale.md | Giao diện duyệt nháp cho sale | Đợt 2 phát hành |
| D3-02 ★ | dot-3/02-chuyen-nguoi.md | Chuyển người, tạm dừng bot, thông báo | D3-01 |
| D3-03 | dot-3/03-thu-nghiem-stt.md | Thử nghiệm chuyển giọng nói (cổng quyết định) | Đợt 2 phát hành |
| D3-04 | dot-3/04-ghi-am-tom-tat.md | Ghi âm, chuyển văn bản, tóm tắt | D3-03 đạt |
| D3-05 ★ | dot-3/05-mo-tu-dong-theo-nhom.md | Mở bot tự gửi theo nhóm câu hỏi | D3-02 |
| D3-06 ★ | dot-3/06-kiem-thu-dot-3.md | Nghiệm thu Đợt 3 | D3-05 (và D3-04 nếu làm) |

Prompt cùng đợt không phụ thuộc nhau (ví dụ D1-07 và D1-08, hoặc D2-01 và D2-04) có thể chạy song song ở hai phiên, mỗi phiên một nhánh git.

## 5. Ba cổng anh phải tự duyệt

| Cổng | Khi nào | Anh kiểm tra |
| --- | --- | --- |
| Phát hành Đợt 1 | Sau D1-11 | Báo cáo nghiệm thu đạt; 1–2 sale dùng thật 1 tuần không mất tin, không trùng |
| Phát hành Đợt 2 | Sau D2-09 | Bot phân tích đạt ngưỡng trên bộ đề; gọi nhỡ tạo việc đúng |
| Phát hành Đợt 3 | Sau D3-06 | Bot nháp đạt ≥ 85% đúng, 0 lỗi nghiêm trọng; sale chấp nhận |

## 6. Về "bot chạy ngầm"

Kế hoạch triển khai nói bot chạy ngầm trên dữ liệu thật từ Đợt 1. Bộ kit làm điều này bằng hai cách: Đợt 1 **lưu đủ dữ liệu hội thoại** (tin khách + câu sale thật sự trả lời); Đợt 2 xây bot trả lời và **chạy lại toàn bộ dữ liệu từ Đợt 1** để chấm, rồi chạy ngầm tiếp trên tin mới. Kết quả tương đương bot chạy ngầm từ ngày đầu, nhưng không bắt Đợt 1 phải gánh thêm phần bot.
