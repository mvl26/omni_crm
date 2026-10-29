# Mẫu cần điền

| Tệp | Ai điền | Khi nào cần | Ghi chú |
| --- | --- | --- | --- |
| `nhom-cau-hoi-mau.csv` | Trưởng sale | Trước D2-07 | Nhóm có `always_handoff = 1` luôn chuyển người |
| `kho-tri-thuc-mau.csv` | Phụ trách kho tri thức | Trước D2-07, càng sớm càng tốt | Mỗi dòng một cặp hỏi–đáp; nhiều cách hỏi xuống dòng trong cùng ô. **Không ghi giá.** Bot chỉ nói được điều có ở đây. `item_id` (KB-00001…) **không bao giờ đổi** cho một mục — thêm mục mới thì cấp số mới; nhập lại sẽ cập nhật theo `item_id`. `owner_email` là email đăng nhập ERP của người phụ trách (hệ thống tra ra User) |
| `bang-diem-lead-mau.csv` | Trưởng sale | Trước D2-06 | Số điểm là đề xuất; ngưỡng Lead nóng mặc định 60 |

Các ô trong ngoặc vuông `[...]` là chỗ cần thay bằng thông tin thật đã được duyệt công bố. Agent nhập các tệp này bằng lệnh nhập trong D2-06 và D2-07; không tự bịa nội dung.
