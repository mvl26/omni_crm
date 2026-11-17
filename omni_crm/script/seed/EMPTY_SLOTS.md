# Ô còn trống trong kịch bản

Các chỗ kịch bản ghi [Miyano bổ sung], [anh Xuân cung cấp], [anh Xuân xác nhận] hoặc chưa có dữ liệu được để trống, không tự điền.

## 1. Node đang "Tắt" (không gửi cho đến khi điền và bật)

| Mã | Mục | Ô trống | Bật khi |
| --- | --- | --- | --- |
| `KHAC_TUYENDUNG` | 1.3 | Email nhận CV `{bo_sung_email_tuyen_dung}` [Miyano bổ sung]. Câu do agent soạn, xem CAN_DUYET. | Điền email, duyệt câu |
| `TIMHIEU_VATTU` | 2.2 | Gạch đầu "Nhóm vật tư Miyano cung cấp: `{bo_sung_nhom_vat_tu}`" [anh Xuân cung cấp các nhóm hàng chính]. 3 gạch còn lại đã nguyên văn. | Điền nhóm hàng (hoặc duyệt bản bỏ gạch đầu) |
| `BG_VT_XACNHAN_HANG`, `BG_VT_HANG_DUNG`, `BG_VT_HANG_SAI` | 3.3.1 | Đối chiếu mặt hàng với danh mục hàng trên ERP. K7 chưa nối ERP. | Có đối chiếu ERP |
| `BG_VT_KETQUA` | 3.3.1 | Bảng giá bán vật tư (Price List nào), cách ghi VAT/vận chuyển `{ghi_chu_vat_van_chuyen}`, số ngày hiệu lực `{ngay_het_hieu_luc}`, ngưỡng giá trị bot được tự báo [anh Xuân cung cấp / quy định ngưỡng] (3.3.5). | Có bảng giá + `script_quote_enabled_vattu` = 1 |
| `BG_SC_KETQUA` | 3.3.2 | Bảng giá theo bậc SupplyCore: tên gói, điều kiện, giá, kỳ thanh toán, phí triển khai [anh Xuân cung cấp]. | Có bảng giá + `script_quote_enabled_supplycore` = 1 |
| `BG_SPD_KETQUA` | 3.3.3 | Cách tính phí SPD theo quy mô `{cach_tinh_va_muc_phi}` [anh Xuân cung cấp]. | Có cách tính + `script_quote_enabled_spd` = 1 |
| `ZNS_TAILIEU` | 4.5 | Mẫu ZNS phải đăng ký và được Zalo duyệt; chưa có `script_zns_template_id`. | Zalo duyệt mẫu + `script_zns_enabled` = 1 |
| `C4_XOADULIEU` | C.4 | Link hướng dẫn xoá dữ liệu trên website `{bo_sung_link_xoa_du_lieu}`. Câu do agent soạn. | Có link, duyệt câu |

## 2. Ô cấu hình (không phải câu chữ, router/cấu hình cần)

| Ô | Mục | Nơi điền |
| --- | --- | --- |
| Người nhận chính / thay cho Vật tư, Phần mềm, SPD, Khác [Miyano bổ sung] | 1.4 | `Omni Settings.script_recipients` |
| Người nhận "Mua hàng" và "Lãnh đạo" cho `KHAC_HOPTAC` | 1.3 | Chưa có dòng trong Select của Omni Service Recipient. Tạm dùng `KHAC`. |
| Người phụ trách tuân thủ (xoá dữ liệu) | C.4 | `script_recipients` dòng `TUAN_THU` |
| Danh sách tài liệu có sẵn từng dịch vụ [Miyano xác nhận] | 3.2, 3.8 | `script_docs_list`. Nút ở `TAILIEU_*` đang "Cần duyệt". |
| Link trang dịch vụ trên website (khách không cho email và SĐT) | 3.4 | Chưa có trường. `KHONG_THONGTIN` chỉ gửi "Dạ không sao ạ." cho đến khi có link. |
| Link chính sách bảo mật | 3.4 | `script_privacy_url` (trống thì dùng `miyano.com.vn/chinh-sach-bao-mat`) |
| Tiêu chí, mốc quy mô SupplyCore và SPD [anh Xuân xác nhận] | 3.3.2, 3.3.3 | Câu và nút đang "Cần duyệt" |
| Báo giá PDF: bot tự gửi hay nhân viên duyệt trước | 3.3.5 | `script_quote_review_required` (mặc định 1) |
| Phạm vi demo gặp trực tiếp (chỉ Hà Nội hay cả tỉnh khác) [Miyano xác nhận] | 4.4, 4.9 | Chưa chốt. Nút [Gặp trực tiếp] vẫn hiện. |
| Người làm demo cho Phần mềm và SPD | 4.9 | `script_recipients` dòng `DEMO_SUPPLYCORE`, `DEMO_SPD` |
| Số giờ được nhắn tin tư vấn miễn phí của Zalo OA theo gói Tăng trưởng | 1.5, 4.2 | `Omni Channel.zalo_cs_free_hours` (IT xác nhận) |
| Cách tính phí phần mềm theo quy mô (bot cần hỏi gì) | 2.8 | Chưa chốt |
