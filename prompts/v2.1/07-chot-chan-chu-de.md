# V21-07 · Chốt chặn chủ đề không bị né bằng cách viết

**Phụ thuộc:** V21-06. **Thời lượng dự kiến:** nửa ngày.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/v2.1/dac-ta-v2.1.md mục F7, docs/08 mục 5.4 và 7, bot/guards.py và test hiện có của chốt chặn chủ đề, docs/nghiem-thu/v2.md (các ca lọt và chặn nhầm đã ghi).

Tạo nhánh v21/07-chot-chan-chu-de từ {{BASE_BRANCH}}.

Nhiệm vụ: làm đúng F7.
1. Hàm normalize_for_guard(text) -> str thuần, có docstring, theo các bước F7 mục 1 (chữ thường; bỏ dấu kể cả đ→d; 1→i, 0→o, 3→e, 4→a, @→a, $→s; gộp chuỗi chữ đơn cách bởi khoảng trắng/dấu câu; bỏ ký tự lặp quá 2 lần).
2. Từ khóa chủ đề chuẩn hóa cùng cách lúc nạp; so khớp cả dạng có khoảng trắng và dạng viết liền. So khớp trên văn bản gốc và văn bản chuẩn hóa; một bên khớp là chặn.
3. Bộ ca tests/fixtures/guards/topic_cases.json: ≥ 40 ca né phải chặn (cách chữ, số thay chữ, không dấu, viết liền, xen emoji, chữ hoa thường lẫn lộn — cho phí dịch vụ, chiết khấu, tỷ lệ %, đàm phán, điều khoản, hợp đồng, bảo lãnh) và ≥ 20 câu thường không nên chặn (giờ làm việc, địa chỉ, hỏi cách vận hành, "100 phần trăm hài lòng", "thỏa thuận lịch hẹn"…).
4. Test đọc bộ ca: 40/40 ca né bị chặn là bắt buộc; số ca chặn nhầm in ra và ghi vào báo cáo (không bắt buộc bằng 0).
5. Đo thời gian: normalize + match < 5 ms cho tin 2.000 ký tự (ghi số đo).

Hoàn thành khi: test xanh; báo cáo có bảng ca lọt (phải 0) và ca chặn nhầm.

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```
