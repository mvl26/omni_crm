# 0015 · Công cụ chẩn đoán chữ ký Zalo: tệp riêng tư, không thêm DocType

- Ngày: 2026-11-03
- Trạng thái: Đã quyết
- Prompt: V21-05 (F5 bước 4)

## Bối cảnh
Công thức chữ ký webhook Zalo (Z1) chưa xác nhận được từ tài liệu chính thức (docs/12 C-V21-05). Cần chỗ ghi các webhook bị từ chối để người có OA thật đối chiếu. Yêu cầu: chỉ Omni Admin đọc, không chứa `oa_secret_key`, tự tắt sau tối đa 2 giờ, tự xóa sau 24 giờ, ưu tiên không thêm DocType (docs/02 là danh sách chuẩn).

## Các lựa chọn
1. **Error Log với tiêu đề cố định.** Không cần DocType, nhưng Error Log đọc được bởi mọi System Manager (không phải chỉ Omni Admin), có thể bị người khác xóa/xuất, và 200 ký tự đầu của thân (có thể có chữ của khách) sẽ nằm chung nơi chứa lỗi hệ thống.
2. **DocType mới "Omni Signature Probe".** Phân quyền chuẩn nhưng thêm DocType ngoài danh sách (cần ADR "Đề xuất" và hỏi), tốn migration cho công cụ dùng vài lần.
3. **Tệp JSON trong `<site>/private/omni_sigprobe/` (thư mục 0700, tệp 0600) + API `omni_crm.api.admin.zalo_signature_probes` chỉ cho Omni Admin.** Cùng cách với hàng dự phòng (ADR 0014); không thêm DocType; xóa bằng xóa tệp.

## Quyết định
Chọn 3 (`channels/zalo/sigprobe.py`).
- **Bật:** site_config `omni_zalo_signature_debug_until` (ISO datetime, có hoặc không có múi giờ). Hạn hiệu lực = min(giá trị, lần đầu hệ thống thấy đúng giá trị đó + 2 giờ). Dấu "lần đầu thấy" lưu bền trong `.armed` cùng thư mục để đặt hạn rất xa cũng không tự bật lại sau 2 giờ; đổi giá trị mới thì đếm lại. Sai định dạng, hết hạn, vắng = tắt. (Đọc chặt hơn câu "quá 2 giờ kể từ hiện tại thì cắt về 2 giờ" vốn làm cờ đặt xa bật mãi.)
- **Nội dung bản ghi:** giờ, kênh, lý do từ chối, tên header chữ ký, chữ ký nhận được, chữ ký hệ thống tính theo công thức hiện hành, tên các công thức ứng viên khớp chữ ký nhận được (5 công thức; mã băm của chúng không ghi), timestamp trong thân, có trường `mac` trong thân hay không, TÊN các header nhận được (không giá trị, nên không có Authorization/Cookie), độ dài thân, 200 ký tự đầu của thân. `oa_secret_key` được che (`[***]`) trước khi cắt nếu xuất hiện trong thân.
- **Giới hạn:** tối đa 200 bản ghi cùng lúc (kẻ lạ gửi webhook sai chữ ký hàng loạt không lấp đầy đĩa); mọi lỗi của công cụ bị nuốt để webhook không hỏng.
- **Xóa:** tệp quá 24 giờ bị xóa bởi tác vụ mỗi giờ `sigprobe.cleanup` và lúc đọc.
- **Công thức chữ ký KHÔNG đổi.**

## Hệ quả
- 200 ký tự đầu của thân có thể chứa dữ liệu cá nhân của khách: chỉ bật trên staging với OA thử, chỉ Omni Admin đọc, tự xóa. `erase_customer_data` không phủ các tệp này (hết hạn sau 24 giờ).
- Nhiều máy web không chung đĩa: bản ghi nằm ở máy nhận webhook; API đọc ở máy khác sẽ không thấy.
- Màn giám sát hiện dòng "Chẩn đoán chữ ký Zalo ĐANG BẬT" khi còn hạn.
