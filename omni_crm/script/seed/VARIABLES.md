# Kho kịch bản – biến, gợi ý action, quy ước (seed `nodes.json`)

Nguồn câu chữ: `docs/kich_ban_bot_miyano_v3.md`. Lược đồ: `docs/bot_v3_contract.md` §2.
Kiểm: `python3 -I omni_crm/script/seed/check_seed.py`. Lệnh này kiểm JSON, mã trùng, giá trị Select, nút ≤ 5, nhãn ≤ 20 ký tự (NFC), đích nút và `then:`/`goto:`, biến đã ghi ở đây, và câu "Đang dùng" phải nguyên văn kịch bản.

## 1. Biến trong `text` / nhãn nút

Biến viết dạng `{ten_bien}` theo kiểu `str.format`. Biến nào không có giá trị lúc gửi thì router **không gửi node** (không để lộ dấu ngoặc), trừ `{ten}` (xem dưới).

| Biến | Ý nghĩa | Nguồn / ghi chú |
| --- | --- | --- |
| `{ten}` | **Chỉ tên** khách (ví dụ "Nam", "Hà"), có thể **trống**. | Câu kịch bản đã viết sẵn "anh/chị {ten}", nên biến **không** được chứa "anh/chị", nếu không sẽ thành "anh/chị anh/chị". Trống thì render bỏ biến và gộp khoảng trắng ("anh/chị {ten}." → "anh/chị."). Gọi "anh Nam"/"chị Hà" khi biết giới tính là việc của renderer: thay cả cụm "anh/chị {ten}". |
| `{dich_vu}` | Tên dịch vụ đang tư vấn, viết thường để chèn giữa câu: "cung ứng vật tư y tế" / "phần mềm SupplyCore" / "hệ thống SPD" | Lead `omni_services` |
| `{ngay_lam_viec_tiep_theo}` | "sáng mai" nếu ngày mai là ngày làm việc, nếu không thì thứ và ngày, ví dụ "sáng thứ Hai 17/11" | `timeutil.next_working_day_text` (1.4, 3.5) |
| `{thoi_diem}` | "trong hôm nay" nếu trước 16:30 ngày làm việc, nếu không thì bằng `{ngay_lam_viec_tiep_theo}` | 3.5, 5.2 |
| `{email}` | Email khách | Lead. Khi ghi log phải che phần giữa. |
| `{sdt}` | SĐT khách | Lead. Khi ghi log phải che phần giữa. |
| `{tai_lieu}` | Tên tài liệu khách chọn hoặc tự ghi (3a) | `script_state.tai_lieu` |
| `{tai_lieu_bao_gia}` | "tài liệu" hoặc "báo giá" (hoặc tên tài liệu) | Kịch bản C.1 viết `{tai_lieu/bao_gia}`. Đã đổi tên vì có dấu "/". |
| `{thong_tin_con_thieu}` | Thông tin còn thiếu: "email" / "số điện thoại" / "email và số điện thoại" | C.1 |
| `{chinh_sach_bao_mat}` | Link chính sách bảo mật | `Omni Settings.script_privacy_url`. **Mặc định khi trống: `miyano.com.vn/chinh-sach-bao-mat`** (URL nguyên văn ở 3.4). |
| `{kenh_chat}` | Tên kênh chat khách đã dùng: "Zalo OA" / "Messenger" | 4.5 (email) |
| `{hom_nay_hom_qua}` | "hôm nay" hoặc "hôm qua" theo ngày demo | Kịch bản 5.2 viết `{hom_nay/hom_qua}`. Đã đổi tên. |
| `{khung_1}` `{khung_2}` `{khung_3}` | 3 khung demo trống, ví dụ "Thứ Ba 18/11, 9:00". Dùng làm **nhãn nút**, nên bản render phải ≤ 20 ký tự (dài nhất "Thứ Năm 20/11, 14:00" = 20). | Lịch người demo trong 3 ngày làm việc tới (4.4) |
| `{thu_ngay}` | Thứ và ngày demo, ví dụ "Thứ Ba 18/11" | 4.4 |
| `{gio_bat_dau}` `{gio_ket_thuc}` | Giờ bắt đầu và kết thúc demo | 4.4 |
| `{gio}` | Giờ hẹn demo (tin vắng mặt) | 4.4 bước 8 |
| `{hinh_thuc}` | "Online qua Teams" / "Gặp trực tiếp" | 4.4 |
| `{ten_hang}` `{quy_cach}` `{hang_sx}` | Tên hàng, quy cách, hãng sản xuất (đối chiếu ERP) | 3.3.1. Chỉ dùng ở node báo giá (đang Tắt) và `BG_VT_CHUACOGIA`/`BG_VT_CHUACOHANG`. |
| `{dong_bao_gia}` | Các dòng báo giá, mỗi dòng `{ten_hang} – {quy_cach} – {hang_sx}: {so_luong} {dvt} × {don_gia} = {thanh_tien}`, dòng có `(*)` là hãng thay thế | 3.3.1. Gộp các dòng "…" của kịch bản. Biến con: `{so_luong}`, `{dvt}`, `{don_gia}`, `{thanh_tien}`. |
| `{tong_tien}` | Tổng tiền | 3.3.1 (Tắt) |
| `{ghi_chu_vat_van_chuyen}` | Đã gồm hay chưa gồm VAT và phí vận chuyển | 3.3.1, 3.3.4. [anh Xuân cung cấp] |
| `{ngay_het_hieu_luc}` | Hạn hiệu lực báo giá | 3.3.1. [anh Xuân cung cấp số ngày] |
| `{ten_goi}` `{gia_goi}` `{ky_thanh_toan}` `{noi_dung_goi}` `{phi_trien_khai}` | Gói SupplyCore theo bậc | 3.3.2. Bảng giá [anh Xuân cung cấp] |
| `{cach_tinh_va_muc_phi}` | Cách tính và mức phí SPD | 3.3.3. [anh Xuân cung cấp] |
| `{bo_sung_email_tuyen_dung}` | Ô trống: email nhận CV | 1.3. [Miyano bổ sung] |
| `{bo_sung_nhom_vat_tu}` | Ô trống: các nhóm vật tư chính | 2.2. [anh Xuân cung cấp] |
| `{bo_sung_link_xoa_du_lieu}` | Ô trống: link hướng dẫn xoá dữ liệu trên website | C.4 |

Biến `{ngay}` có trong danh sách người điều phối gợi ý nhưng không dùng: kịch bản đã dùng `{thu_ngay}`.

## 2. Gợi ý `action`

`action` là chuỗi gợi ý cho router (K2 trở đi). Nhiều lệnh nối bằng `;` và chạy theo thứ tự **sau khi** gửi `text` (nếu có).

| Lệnh | Ý nghĩa |
| --- | --- |
| `menu` | Tin có menu (nút chọn nhánh). Khách gõ tự do ở đây thì phân loại theo `CLASSIFY_CODES.md`. |
| `reask` / `reask:<field>` | Hỏi lại. Đếm `script_reask_count`; lần thứ 3 thì tự chuyển nhân viên (1.2). `reask:email`/`reask:phone`: sai định dạng, chỉ hỏi lại 1 lần (3.4). |
| `kb_answer` | Chờ hoặc trả lời câu khách hỏi bằng Kho tri thức (2.5, C.3), sau đó gửi node hỏi tiếp của bước đang đứng. |
| `handoff` / `handoff:<SVC>` / `handoff:task` | Chuyển nhân viên (K3, 1.4). `<SVC>` thuộc bảng Omni Service Recipient (`VATTU`, `SUPPLYCORE`, `SPD`, `KHAC`, `TUAN_THU`, …). `handoff:task` chỉ tạo việc mà không tắt bot (ngoài giờ). |
| `pause_bot` | Tắt bot trong cuộc trò chuyện cho đến khi nhân viên bấm "Trả lại cho bot". |
| `service:<SVC>` | Ghi dịch vụ quan tâm (`VATTU`, `SUPPLYCORE`, `SPD`, `KHAC`) vào Lead `omni_services` (cộng dồn). |
| `need:<giá trị>` | Ghi Lead `omni_journey_need` (Tìm hiểu / Tài liệu / Báo giá / Demo / Gửi danh mục). |
| `journey:<trạng thái>` | `state.set_journey`, gồm cả CRM Lead.status theo bảng quy đổi. |
| `branch:3a` / `branch:3b` | Ghi nhánh thu thông tin vào `script_state`. |
| `collect:<field>` | Chờ khách trả lời, tách thông tin, lưu vào `<field>` (`email`, `phone`, `name_org`, `tai_lieu`, `items`, `quantity`, `need_by`, `substitute`, `file`, `order_info`, `demo_time`, `attendees`, `adjust`, `sc_*`, `spd_*`). Trường đã có trên Lead thì bỏ qua câu hỏi (3.1). |
| `answer:<field>=<giá trị>` | Node gắn với nút trả lời. Lưu giá trị của nút đã bấm. |
| `then:<CODE>` | Gửi tiếp node `<CODE>` (bỏ qua nếu đã có thông tin). |
| `goto:<CODE>` | Chuyển hẳn sang node `<CODE>` (dùng cho node điều hướng). `goto:BAOGIA_<dich_vu>` = BAOGIA_VATTU/PHANMEM/SPD theo dịch vụ đang tư vấn, chưa có dịch vụ thì `BAOGIA_HOISANPHAM`. |
| `quote_collect` / `quote_match` / `quote_show` / `quote:manual` | Nhánh báo giá (K4/K7). `quote_show` chỉ chạy khi công tắc `script_quote_enabled_<svc>` bật và có bảng giá; nếu không thì gửi `BG_XACNHAN_TRONGGIO`/`_NGOAIGIO`. `quote:manual` = nhân viên báo giá riêng. |
| `task:<loại>` | Tạo CRM Task (`tasks.create_task`). Loại: `docs`, `quote`, `resend_docs`, `resend_quote`, `purchasing`, `urgent`, `demo_invite`, `negotiate`, `won_high`. Hạn theo 3.5. |
| `notify:manager` | Báo quản lý (người nhận `QUAN_LY`). |
| `require:phone` | Chưa có SĐT thì hỏi `HOI_SDT` trước khi gửi câu. |
| `consent` | Ghi `omni_data_consent_at` (đã gửi câu chính sách bảo mật). |
| `send_link:service_page` | Gửi link trang dịch vụ trên website (ô trống, xem EMPTY_SLOTS). |
| `attach_file` | Đính kèm file khách gửi vào Lead. |
| `log_unanswered` | Ghi nguyên văn câu hỏi vào `omni_unanswered_questions`. |
| `demo` / `demo:slots` / `demo:book` / `demo:reschedule` / `demo:cancel` | Luồng đặt lịch 4.4 (K5). |
| `followup_docs` / `followup_quote` / `demo_remind_1d` / `demo_remind_1h` / `demo_noshow` / `silence_3d` / `postdemo_1` / `lost_3m` | Node gửi theo lịch, trùng `kind` của Omni Scheduled Message. |
| `schedule:<kind>` | Đặt lịch gửi node theo `kind`. |
| `cancel_followups` | Huỷ các tin lịch đang chờ của Lead (`scheduler.cancel_for`). |
| `email_template` / `zns_template` | Mẫu email (nhân viên gửi tay qua CRM Task khi `script_email_enabled` = 0) hoặc mẫu ZNS (`script_zns_enabled`). Không gửi qua chat. Dòng đầu email là "Tiêu đề: …". |
| `stop` / `do_not_contact` | Dừng hẳn, đánh dấu `omni_do_not_contact` (C.4). |
| `spam` / `no_lead` | Đánh dấu Spam, không trả lời, không tạo Lead (C.3). |

## 3. Quy ước

- **Mã dịch vụ.** Mã nút và mã node giữ hậu tố của kịch bản: `VATTU`, `PHANMEM`, `SPD` (ví dụ `MENU_PHANMEM`, `TIMHIEU_PHANMEM`). Trường `service` dùng Select của giao ước: `VATTU`, `SUPPLYCORE`, `SPD`. Quy đổi: `PHANMEM` ↔ `SUPPLYCORE`.
- **Biến thể theo dịch vụ.** Nếu có node `<TARGET>_<HẬU TỐ>` (hậu tố `VATTU`/`PHANMEM`/`SPD` theo dịch vụ đang tư vấn) thì router dùng node đó thay cho `<TARGET>`. Không có thì dùng `<TARGET>`. Ví dụ: `NL_DAXEM` → `NL_DAXEM_VATTU`, `TL_HOITHEM` → `TL_HOITHEM_VATTU`, `NL_BAOGIA` → `NL_BAOGIA_VATTU`, `NL_CHUAKIPXEM` → `NL_CHUAKIPXEM_VATTU`, `NHAC3N_B4` → `NHAC3N_B4_VATTU`, `EMAIL_NL_TAILIEU` → `EMAIL_NL_TAILIEU_VATTU`, `NL_BG_TRAODOI` → `NL_BG_TRAODOI_VATTU`. Node gốc là bản cho Phần mềm và SPD.
- **Node điều hướng.** `text` trống, `action` có lệnh, `status` = Đang dùng (ghi "Node điều hướng" trong `notes`). Đây là đích của nút trả lời hoặc nút chuyển nhánh, không có câu gửi khách. `store.render` trả None là đúng: router đọc `get_node(code)["action"]`. Khác với **ô trống** (text trống/có `{bo_sung_*}`, status Tắt), xem `EMPTY_SLOTS.md`.
- **Mã đặc biệt** (giao ước §4): `#GAP_NHANVIEN` → K3 handoff (node `GAP_NHANVIEN`). `#MENU` hoặc gõ "menu" → `MENU_CHINH`.
- **Payload nút Zalo** = `#` + `target`.
- Nút "Gặp nhân viên" chỉ có ở những tin kịch bản ghi nút đó. Ở các tin khác khách vẫn gặp được nhân viên qua menu OA hoặc gõ (C.2).
