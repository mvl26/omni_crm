# Cách dùng prompt

## 1. Mỗi prompt một phiên

- Mở phiên Claude Code mới (hoặc `/clear`) tại thư mục bench trước mỗi prompt. Agent sẽ đọc lại `CLAUDE.md` và đặc tả, không mang ngữ cảnh cũ.
- Tạo nhánh git theo mã prompt trước khi chạy: `git checkout -b d1-03-cong-tiep-nhan` (agent tự làm nếu anh không làm).
- Chép **nguyên khối mã** (phần đóng khung) của tệp prompt, thay các biến `{{...}}` rồi dán vào Claude Code.

## 2. Biến dùng chung

| Biến | Ý nghĩa | Ví dụ |
| --- | --- | --- |
| `{{BENCH}}` | Đường dẫn bench | `/home/frappe/frappe-bench` |
| `{{SITE_TEST}}` | Site chỉ dùng để test | `omni.test` |
| `{{SITE_STAGING}}` | Site staging (từ D1-11) | `staging.miyano.local` |
| `{{KIT}}` | Nơi giải nén bộ kit | `/home/frappe/kits/crm_omnichannel_kit` |
| `{{REPO_URL}}` | Repo GitHub của app (không bắt buộc) | `git@github.com:mvl26/omni_crm.git` |
| `{{LICENSE}}` | Giấy phép khi tạo app (D1-01) | `agpl-3.0` — hỏi pháp chế |
| `{{BRANCH}}`, `{{BASE_BRANCH}}`, `{{MA_PROMPT}}` | Dùng trong prompt review | `d1-03-cong-tiep-nhan`, `main`, `D1-03` |
| `{{MO_TA_LOI}}`, `{{CACH_TAI_HIEN}}`, `{{NGUON}}`, `{{TAI_LIEU}}`, `{{TEN_NGAN}}` | Dùng trong prompt sửa lỗi | |
| `{{DANH_SACH_STT}}` | Nhà cung cấp chuyển giọng nói muốn thử (D3-03) | Tên 2–3 nhà cung cấp |
| `{{THU_MUC_GHI_AM}}` | Thư mục ghi âm thử, nằm ngoài repo (D3-03) | `/home/frappe/stt_trial/` |
| `{{STT_DUOC_CHON}}` | Nhà cung cấp được chọn sau D3-03 (D3-04) | |
| `{{NGUONG_STT}}` | Ngưỡng quyết định làm tóm tắt cuộc gọi (D3-03) | "WER ≤ 25%, trường đúng ≥ 80%" |

## 3. Khi agent dừng giữa chừng

Agent được dặn dừng khi: thiết kế phải đổi (ADR "Đề xuất"), lỗi quá 3 lần sửa, thiếu thông tin chỉ anh có (khóa API, tài khoản). Khi đó:

1. Đọc báo cáo và tệp ADR/`docs/12-quyet-dinh-mo.md` mà agent ghi.
2. Trả lời ngay trong phiên đó (agent còn ngữ cảnh), ví dụ: "Đồng ý phương án 2 trong ADR 0003, làm tiếp."
3. Nếu không chắc, chạy prompt review độc lập để có ý kiến thứ hai.

## 4. Mẫu báo cáo cuối mà mọi prompt yêu cầu

```markdown
## Báo cáo <mã prompt>

**Kết quả:** Hoàn thành | Hoàn thành một phần | Dừng chờ quyết định

### Đã làm
- …

### Tệp thay đổi
- `omni_crm/...` — mô tả một dòng

### Kiểm thử
- Lệnh: `bench --site … run-tests --app omni_crm`
- Kết quả: N test, N qua, 0 lỗi (dán đoạn cuối output)
- Kiểm tra tay đã làm: …

### Giả định
- Đã xác nhận: G… (ghi ở docs/12 mục C)
- Còn mở: …

### Khác đặc tả / ADR
- …

### Việc còn lại, rủi ro
- …

### Người duyệt tự kiểm tra trong 5 phút
1. …
```

## 5. Sau mỗi prompt có dấu ★

Chạy `prompts/chung/review-doc-lap.md` ở phiên khác. Chỉ merge nhánh khi review không còn lỗi mức "Chặn".
