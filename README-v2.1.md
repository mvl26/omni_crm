# Kit v2.1 — sửa trước chiến dịch

Kit v2.1 chạy sau khi kit v2 xong (tag `v2-08`). Nó sửa bảy điểm kỹ thuật mà nghiệm thu v2 tìm ra, cập nhật bộ hướng dẫn cho đúng v2, và thêm lệnh kiểm cấu hình trước phát hành. Đặc tả: `docs/v2.1/dac-ta-v2.1.md`.

Chạy được ngay, song song với việc dựng staging và xin tài khoản thật. Không bước nào cần tài khoản Meta, Google hay Zalo thật. Phần cần hệ thống thật được ghi "chưa đo" để kiểm lại trên staging.

## 1. Chuẩn bị

1. Giải nén bản vá v2.1 vào thư mục kit đang dùng. `{{KIT_V21}}` là thư mục đó.
2. Trên repo app: `git checkout {{BASE_BRANCH}} && git pull`. Kiểm tra tag `v2-08` tồn tại.
3. Chép vào repo `docs/v2.1/`, `prompts/v2.1/` và `README-v2.1.md`, rồi commit `docs: thêm đặc tả kit v2.1`. Prompt V21-01 tự làm bước này nếu chưa có.

## 2. Thứ tự chạy

| Mã | Tệp | Việc | Đặc tả | Phụ thuộc |
| --- | --- | --- | --- | --- |
| V21-01 ★ | prompts/v2.1/01-hang-doi-lead.md | Hàng đợi riêng `omni_leads` cho lead quảng cáo | F1 | — |
| V21-02 | prompts/v2.1/02-token-meta.md | Cảnh báo token Meta sắp hết hạn | F2 | — |
| V21-03 | prompts/v2.1/03-keo-lai-meta.md | Đối soát tự kéo lại lead Meta sau sự cố Graph | F3 | V21-01 |
| V21-04 ★ | prompts/v2.1/04-webhook-khong-mat-tin.md | Webhook không trả 503 khi chờ khóa; hàng dự phòng | F4 | V21-01 |
| V21-05 | prompts/v2.1/05-zalo-goi-oa.md | Zalo theo gói OA 2026; công cụ chẩn đoán chữ ký | F5 | — |
| V21-06 ★ | prompts/v2.1/06-y-dinh-b2b.md | Ý định và nhóm câu hỏi B2B cho bot | F6 | — |
| V21-07 | prompts/v2.1/07-chot-chan-chu-de.md | Chốt chặn chủ đề không bị né bằng cách viết | F7 | V21-06 |
| V21-08 ★ | prompts/v2.1/08-lenh-kiem-truoc-phat-hanh.md | Lệnh `preflight` kiểm cấu hình trước phát hành | F9 | V21-01, 02, 04, 05 |
| V21-09 | prompts/v2.1/09-cap-nhat-huong-dan.md | Cập nhật bộ hướng dẫn 00–13 cho đúng v2 và v2.1 | F8 | V21-01 → 08 |
| V21-10 ★ | prompts/v2.1/10-hoi-quy-v21.md | Hồi quy, chạy lại thử tải và sự cố, cập nhật nghiệm thu | — | tất cả |

Chạy liền một mạch: `prompts/v2.1/ORCH-v21.md`. Bước có dấu ★ được review độc lập trước khi merge.

## 3. Sau khi xong v2.1

1. Chạy `preflight` với `target = staging` trên staging, sửa đến khi mọi mục ĐẠT.
2. Làm tiếp `docs/huong-dan/truoc-ngay-chay-chien-dich.md` với tài khoản thật: lead thử Meta và Google, công cụ chẩn đoán chữ ký Zalo, nhập danh sách nhà thầu thật.
3. Chạy UAT 2–3 sale, rồi đưa lên production với `preflight` và `target = production`.
