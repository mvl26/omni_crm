# Mẫu cần điền

| Tệp | Ai điền | Khi nào cần | Ghi chú |
| --- | --- | --- | --- |
| `Mau_DanhSach_NhaThau_TiemNang.xlsx` | Đội IT (đổ dữ liệu), trưởng sale (chỉnh tham số) | Ngay — dùng được trước khi có CRM; nhập vào CRM ở D1-14 | Đọc sheet HuongDan. Không đổi tên sheet và mã trường dòng 2. Công thức tương thích Excel 2016 |
| `nhom-cau-hoi-mau.csv` | Trưởng sale | Trước D2-07 | Nhóm có `always_handoff = 1` luôn chuyển người |
| `kho-tri-thuc-mau.csv` | Phụ trách kho tri thức | Trước D2-07, càng sớm càng tốt | Mỗi dòng một cặp hỏi–đáp; nhiều cách hỏi xuống dòng trong cùng ô. **Không ghi giá.** Bot chỉ nói được điều có ở đây. `item_id` (KB-00001…) **không bao giờ đổi** cho một mục — thêm mục mới thì cấp số mới; nhập lại sẽ cập nhật theo `item_id`. `owner_email` là email đăng nhập ERP của người phụ trách (hệ thống tra ra User) |
| `bang-diem-lead-mau.csv` | Trưởng sale | Trước D2-06 | Bản v1 (tín hiệu hiện có của `Omni Score Rule`); `bot/scoring.py::SAMPLE_FILE` và test v1 đọc tệp này. Số điểm là đề xuất; ngưỡng Lead nóng mặc định 60 |
| `bang-diem-lead-b2b-mau.csv` | Trưởng sale | Trước V2-06 | Bản v2 (tín hiệu B2B, docs/14 mục 3). **Chưa nhập được**: enum tín hiệu B2B đổi ở V2-02/V2-06, khi đó `SAMPLE_FILE` trỏ sang tệp này |

Các ô trong ngoặc vuông `[...]` là chỗ cần thay bằng thông tin thật đã được duyệt công bố. Agent nhập các tệp này bằng lệnh nhập trong D2-06 và D2-07; không tự bịa nội dung.
