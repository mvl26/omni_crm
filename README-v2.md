# Kit v2 — sửa và cải thiện sau khi chạy xong kit v1

Kit v2 chạy **trên codebase `omni_crm` đã xây bằng kit v1**. Nó không làm lại từ đầu. Nó kiểm kê những gì v1 đã làm, sửa chỗ lệch, rồi bổ sung bốn mảng mới:

| Mảng | Vì sao | Đặc tả |
| --- | --- | --- |
| A. Khách hàng là nhà thầu (B2B) | Đối tượng thật là nhà thầu trúng thầu cung cấp vật tư cho bệnh viện, không phải khách lẻ | `docs/14` |
| B. Lead từ quảng cáo Meta, Google, form website | Yêu cầu của chiến dịch marketing đầu tiên | `docs/15` |
| C. Dashboard CSKH, Chi tiết nhà thầu, nhật ký chăm sóc | Quản lý tập trung mọi tương tác theo từng khách | `docs/13` |
| D. Đóng các cổng còn mở của v1 | Ví dụ cổng chất lượng bot chưa đo vì chưa có khóa AI thật | `docs/08` mục 10, `eval/` |

Danh sách thay đổi chi tiết giữa đặc tả v1 và v2: `docs/v2/CHANGELOG-v1-v2.md`.

## 1. Khi nào chạy

- Sau khi v1 chạy xong D3-06 và đã merge vào nhánh chính đang dùng (ví dụ `develop`).
- Gắn tag cho trạng thái cuối của v1 trước khi bắt đầu: `git tag v1-final`. Mọi bước v2 phải quay lui được về tag này.

## 2. Chuẩn bị (trước V2-00)

1. Giải nén kit v2 vào một thư mục **khác** với kit v1, ví dụ `~/kits/crm_omnichannel_kit_v2`. Biến `{{KIT_V2}}` trỏ tới thư mục này.
2. Chốt hoặc ghi rõ "chưa chốt" cho các quyết định trong `docs/12` mục A, đặc biệt:
   - **Q11**: có được gửi hội thoại khách sang dịch vụ AI không. Cần cho V2-07.
   - **V2-Q12**: liên hệ chủ động và Nghị định 91/2020.
   - **V2-Q13**: nguồn dữ liệu đấu thầu, tức cào tự động hay mua gói API.
   - **V2-Q15**: ngày chạy chiến dịch và câu hỏi trên form quảng cáo.
3. Chuẩn bị quyền truy cập: ứng dụng Meta và Page công ty, Google Ads có biểu mẫu khách hàng tiềm năng, khóa Claude API (nếu Q11 cho phép), file danh sách nhà thầu đã đổ vào mẫu `mau/Mau_DanhSach_NhaThau_TiemNang.xlsx`.

## 3. Thứ tự chạy

| Mã | Tệp | Việc | Phụ thuộc |
| --- | --- | --- | --- |
| V2-00 ★ | prompts/v2/00-kiem-ke-hien-trang.md | Cập nhật tài liệu vào repo (không mất kết quả v1), chạy test, lập báo cáo khoảng cách v1 ↔ v2 | v1 xong |
| V2-01 ★ | prompts/v2/01-sua-no-v1.md | Sửa các mục lệch và nợ kỹ thuật mà V2-00 tìm ra | V2-00 |
| V2-02 ★ | prompts/v2/02-mo-hinh-nha-thau.md | Mô hình nhà thầu, điểm phù hợp, giai đoạn bán hàng; chuyển đổi Lead đang có | V2-01 |
| V2-03 ★ | prompts/v2/03-nhap-danh-sach-nha-thau.md | Nhập danh sách nhà thầu từ mẫu Excel | V2-02 |
| V2-04 ★ | prompts/v2/04-dashboard-chi-tiet-nha-thau.md | Dashboard CSKH, Chi tiết nhà thầu, nhật ký chăm sóc (làm mới hoặc mở rộng) | V2-03 |
| V2-05 ★ | prompts/v2/05-lead-quang-cao.md | Lead từ Meta, Google, form website; chiến dịch; SLA phản hồi | V2-04 |
| V2-06 ★ | prompts/v2/06-bot-cho-nha-thau.md | Bot phân tích và trả lời theo bối cảnh nhà thầu; kho tri thức, bảng điểm, bộ đề mới | V2-02 (chạy song song với V2-03 → V2-05 được) |
| V2-07 | prompts/v2/07-do-cong-ai-that.md | Đo các cổng chất lượng bot bằng AI thật | V2-06, Q11 đã chốt cho phép |
| V2-08 ★ | prompts/v2/08-nghiem-thu-san-sang-chien-dich.md | Hồi quy toàn bộ, kiểm thử tải đợt quảng cáo, UAT, danh sách kiểm tra trước ngày chạy chiến dịch | V2-05, V2-06 (V2-07 nếu có) |

Muốn chạy liền một mạch (chế độ tự động, có agent con) thì dùng `prompts/v2/ORCH-dieu-phoi.md`. Prompt này chạy các bước theo đúng thứ tự trên, review độc lập sau mỗi bước ★, và dừng đúng chỗ cần anh quyết.

## 4. Quy tắc khác với v1

1. **Mở rộng, không viết lại.** Mỗi prompt v2 đọc code hiện có trước. Tính năng đã có và đúng đặc tả thì giữ nguyên, kèm test chứng minh. Thiếu thì bổ sung, lệch thì sửa, và ghi lại lý do.
2. **Không làm mất dữ liệu.** Migration chỉ thêm. Patch chuyển đổi dữ liệu phải chạy lại được và có bản chạy thử (dry run). Chạy trên bản sao dữ liệu staging trước.
3. **Không xóa kết quả v1.** Giữ nguyên các mục C, D của `docs/12` (giả định đã xác nhận, phát hiện), `docs/adr/`, `docs/nghiem-thu/`, `docs/huong-dan/`.
4. **Cổng chưa đo thì ghi là chưa đo.** Không coi một cổng là đạt khi chưa có số đo thật. Tính năng phụ thuộc cổng đó giữ tắt mặc định, giống cách agent v1 đã làm ở D3-01.

## 5. Ba cổng anh tự duyệt (và một điểm dừng phụ)

| Cổng | Sau bước | Anh kiểm tra |
| --- | --- | --- |
| Báo cáo khoảng cách | V2-00 | Đồng ý danh sách việc v2 sẽ làm và các chỗ v1 lệch đặc tả |
| Dữ liệu nhà thầu thật | V2-03 | Nhập file thật trên staging: điểm và hạng khớp sheet TongHop |
| Sẵn sàng chiến dịch | V2-08 | Danh sách kiểm tra trước ngày chạy đạt hết; lead thử Meta và Google vào đúng chiến dịch và đúng sale |

Điểm dừng phụ sau V2-02: anh duyệt bảng ánh xạ trạng thái Lead cũ sang giai đoạn B2B và danh sách Lead mơ hồ, trước khi patch chuyển đổi chạy thật trên staging.

V2-07 bị bỏ qua nếu Q11 chưa chốt cho phép. Khi đó các cổng bot ghi "chưa đo", mọi nhóm câu hỏi giữ ở Shadow, và V2-08 vẫn chạy.
