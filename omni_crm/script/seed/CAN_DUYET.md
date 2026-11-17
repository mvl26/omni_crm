# Câu và nút "Cần duyệt"

Node có `status = "Cần duyệt"` chỉ gửi cho danh sách thử (`script_test_identities`). Truyền thông duyệt xong thì đổi sang "Đang dùng" trên CRM.
Câu do agent soạn theo mục "Tên và tính cách của bot": mở bằng "Dạ", kết bằng "ạ", 2–4 câu, hỏi tối đa 1 câu, xưng Miyano, gọi anh/chị. Các câu này **không** thêm giá, chiết khấu, hạn hay cam kết mới. Mốc thời gian chỉ dùng `{thoi_diem}` / `{ngay_lam_viec_tiep_theo}` theo quy tắc 3.5.

## 1. Câu kịch bản chưa có nguyên văn (agent soạn)

| Mã | Mục | Câu đề xuất | Lý do |
| --- | --- | --- | --- |
| `KHAC_KHACHCU` | 1.3 | Dạ, Miyano cảm ơn anh/chị đã tin dùng dịch vụ của Miyano ạ. Anh/chị cho Miyano xin số đơn hàng hoặc nội dung cần hỗ trợ để Miyano chuyển đúng bộ phận phụ trách ạ. | Kịch bản chỉ ghi "Hỏi số đơn / nội dung, chuyển bộ phụ trách". |
| `KHAC_HOPTAC` | 1.3 | Dạ, Miyano cảm ơn anh/chị đã quan tâm hợp tác cùng Miyano ạ. Anh/chị cho Miyano xin tên đơn vị và thông tin liên hệ để Miyano chuyển tới bộ phận phụ trách ạ. | Kịch bản chỉ ghi "Xin tên đơn vị, liên hệ; chuyển Mua hàng hoặc Lãnh đạo". |
| `KHAC_TUYENDUNG` (Tắt) | 1.3 | Dạ, Miyano cảm ơn anh/chị đã quan tâm đến cơ hội làm việc tại Miyano ạ. Anh/chị vui lòng gửi CV tới email {bo_sung_email_tuyen_dung} giúp Miyano nhé ạ. | Chỉ có "Gửi email nhận CV [Miyano bổ sung]". Đang Tắt vì thiếu email (xem EMPTY_SLOTS). |
| `HOI_EMAIL_BAOGIA` | 3.3.1 | Dạ, Miyano xin phép xin email của anh/chị để nhân viên gửi bản báo giá chi tiết ạ. | 3b "xin email (như Bước 3a)", nhưng câu 3a nói "gửi tài liệu". |
| `KHONG_SDT_CAMON` | 3.4 | Dạ vâng, Miyano cảm ơn anh/chị ạ. | Chỉ có "Cảm ơn, không hỏi lại". |
| `SAI_SDT` | 3.4 | Miyano chưa ghi nhận được số điện thoại này, anh/chị kiểm tra lại giúp Miyano ạ. | Kịch bản chỉ có câu cho email. |
| `BG_DIEUCHINH` | 3.3.1 | Dạ, anh/chị muốn điều chỉnh phần nào trong báo giá ạ? Anh/chị có thể đổi số lượng, mặt hàng hoặc hãng. | Chỉ có "hỏi khách muốn đổi gì (số lượng, mặt hàng, hãng)". |
| `BG_VT_CHUACOHANG` | 3.3.1 | Dạ, mặt hàng {ten_hang} đúng hãng anh/chị yêu cầu hiện Miyano chưa có sẵn ạ. Chuyên viên sẽ kiểm tra nguồn hàng và phản hồi anh/chị {thoi_diem} ạ. | Chỉ có "Báo rõ mặt hàng đó chưa có sẵn, nhân viên kiểm tra nguồn và phản hồi {thoi_diem}". |
| `BG_XACNHAN_TRONGGIO` | 3.3.4, 3.5 | Miyano cảm ơn anh/chị {ten}. Nhân viên Miyano sẽ gửi báo giá tới email {email} trong hôm nay ạ. Anh/chị kiểm tra giúp Miyano cả hộp thư Spam nhé. | **Câu hẹn báo giá khi bot không tự báo giá** (K7: chưa có bảng giá; vượt ngưỡng; khách cũ có giá riêng; quy mô vượt bậc). Theo mẫu `TL_XACNHAN_TRONGGIO`. |
| `BG_XACNHAN_NGOAIGIO` | 3.3.4, 3.5 | Miyano cảm ơn anh/chị {ten}. Nhân viên sẽ gửi báo giá tới email {email} vào {ngay_lam_viec_tiep_theo} ạ. | Như trên, theo mẫu `TL_XACNHAN_NGOAIGIO`. |
| `NL_DOIEMAIL` | 4.3 | Dạ, anh/chị cho Miyano xin email mới để nhân viên gửi lại tài liệu ạ. | Có nút [Đổi email khác] nhưng không có câu xin email mới. |
| `NL_CHUAKIPXEM_VATTU` | 4.3 | Dạ không sao ạ. Nếu anh/chị đang có danh mục cần mua hoặc cần cho gói thầu sắp tới, anh/chị gửi Miyano để báo giá nhé ạ. | Câu gốc mời demo, nhưng Vật tư không có demo (5.3); kịch bản chỉ đổi nút. Ghép từ câu 4.3 Vật tư. |
| `EMAIL_NL_TAILIEU_VATTU` | 4.5 | (email) …Nếu anh/chị đang có danh mục cần mua hoặc cần cho gói thầu sắp tới, anh/chị gửi Miyano để báo giá nhé ạ. … nhắn cho Miyano qua {kenh_chat}. | "Với Vật tư: thay đoạn mời demo bằng lời mời gửi danh mục". |
| `NL_BG_DOIEMAIL` | 4.6 | Dạ, anh/chị cho Miyano xin email mới để nhân viên gửi lại báo giá ạ. | 4.6 có [Chưa nhận được] nhưng không có nhánh riêng cho báo giá. |
| `NL_BG_GUILAI` | 4.6 | Miyano đã báo nhân viên gửi lại báo giá {thoi_diem} ạ. | Biến thể báo giá của câu 4.3. |
| `DEMO_GIOBAN` | 4.4 | Dạ, khung giờ này bên Miyano đã kín lịch ạ. Miyano đề xuất khung gần nhất là {khung_1}, anh/chị thấy có phù hợp không ạ? (nút [Đồng ý] [Giờ khác]) | Chỉ có "bận hoặc rơi vào ngày nghỉ thì đề xuất khung trống gần nhất". |
| `DEMO_HOI_EMAIL` | 4.4 | Dạ, anh/chị cho Miyano xin email để nhân viên gửi lời mời tham gia buổi demo ạ. | Câu xác nhận gửi lời mời tới {email}, nhưng khách đặt demo thẳng từ Bước 2 chưa có email. |
| `DEMO_XACNHAN_THUCONG` | 4.4, 4.7 | Dạ, Miyano xác nhận lịch demo {dich_vu}: {thu_ngay}, {gio_bat_dau}–{gio_ket_thuc}, hình thức {hinh_thuc}. Nhân viên Miyano sẽ gửi lời mời và đường link tham gia tới email {email} trước buổi demo. Miyano sẽ nhắc anh/chị trước buổi demo ạ. | **Chưa có tích hợp Microsoft 365/Teams** nên nhân viên gửi lời mời tay (CRM Task `demo_invite`). Bản nguyên văn `DEMO_XACNHAN` vẫn giữ "Đang dùng". Cần chốt dùng bản nào. |
| `DEMO_NHAC_1NGAY` | 4.4 | Miyano chào anh/chị {ten} ạ. Miyano xin nhắc lịch demo {dich_vu} vào {thu_ngay}, {gio_bat_dau}–{gio_ket_thuc}, hình thức {hinh_thuc}. Nếu cần đổi lịch, anh/chị bấm nút bên dưới giúp Miyano nhé ạ. (nút [Đổi lịch] [Huỷ lịch]) | Chỉ có "Nhắc lịch: trước 1 ngày và trước 1 giờ". |
| `DEMO_NHAC_1GIO` | 4.4 | Miyano chào anh/chị {ten} ạ. Buổi demo {dich_vu} của anh/chị sẽ bắt đầu lúc {gio_bat_dau} hôm nay, hình thức {hinh_thuc}. Miyano rất mong được gặp anh/chị ạ. (nút [Đổi lịch] [Huỷ lịch]) | Như trên. |
| `DEMO_HUY` | 4.4 | Dạ, Miyano đã ghi nhận anh/chị muốn huỷ lịch demo ạ. Anh/chị cho Miyano biết lý do được không ạ? (nút nguyên văn) | Chỉ có "hỏi nhẹ lý do bằng nút". |
| `DEMO_HUY_CAMON` | 4.4 | Dạ, Miyano cảm ơn anh/chị đã cho Miyano biết ạ. Khi nào cần, anh/chị cứ nhắn cho Miyano nhé. | Chỉ có "cảm ơn và chuyển Chăm sóc lại". |
| `DAT_HANG` | 5.3 | Miyano xin cảm ơn anh/chị đã tin tưởng lựa chọn Miyano ạ! Chuyên viên phụ trách sẽ gọi điện cho anh/chị {thoi_diem} để xác nhận đơn hàng, giao hàng và hoá đơn. | "cảm ơn và hẹn nhân viên gọi xác nhận đơn, giao hàng, hoá đơn (giống nhánh [Bắt đầu sử dụng])". Ghép từ `SD_BATDAU`. |
| `NHAC3N_B4_VATTU` | C.1 | Miyano chào anh/chị ạ. Anh/chị đã có thời gian xem {tai_lieu_bao_gia} chưa ạ? Nếu đang có danh mục cần mua hoặc cần cho gói thầu sắp tới, anh/chị gửi Miyano để báo giá nhé ạ. | "(Vật tư: mời gửi danh mục)". |
| `C3_KHONGLIENQUAN` | C.3 | Dạ, Miyano xin phép chỉ hỗ trợ các nội dung về dịch vụ của Miyano Việt Nam ạ. | Chỉ có "Trả lời lịch sự 1 câu, đưa lại menu". |
| `C4_XOADULIEU` (Tắt) | C.4 | Dạ, Miyano đã ghi nhận yêu cầu xoá dữ liệu của anh/chị ạ. Anh/chị xem hướng dẫn xoá dữ liệu tại {bo_sung_link_xoa_du_lieu}, bộ phận phụ trách sẽ liên hệ hỗ trợ anh/chị ạ. | Chỉ có "gửi link hướng dẫn xoá dữ liệu… và chuyển người phụ trách tuân thủ". Đang Tắt vì thiếu link. |

## 2. Câu nguyên văn nhưng nội dung chờ chốt

| Mã | Mục | Lý do |
| --- | --- | --- |
| `TAILIEU_VATTU`, `TAILIEU_PHANMEM`, `TAILIEU_SPD` | 3.2 | Câu hỏi nguyên văn. Nút tài liệu chờ [Miyano xác nhận danh sách tài liệu có sẵn] (3.2, 3.8). |
| `BG_SC_MATHANG`, `BG_SC_NGUOIDUNG`, `BG_SC_COSO` | 3.3.2 | Câu và nút nguyên văn. Tiêu chí và mốc chia quy mô [anh Xuân xác nhận] (2.8, 3.8). |
| `BAOGIA_SPD`, `BG_SPD_DANHMUC`, `BG_SPD_GIATRI` | 3.3.3 | Câu và nút nguyên văn. Tiêu chí quy mô [anh Xuân xác nhận]. |

## 3. Nhãn nút rút gọn (nhãn gốc > 20 ký tự, Zalo/Messenger)

Nhãn gốc ghi trong `notes` của node.

| Node | Nhãn gốc (số ký tự) | Nhãn dùng |
| --- | --- | --- |
| `TAILIEU_VATTU` | Hồ sơ pháp lý sản phẩm (22) | Hồ sơ pháp lý |
| `NL_BAOGIA`, `NL_BAOGIA_VATTU` | Đã nhận, cần trao đổi (21) | Đã nhận cần trao đổi |
| `SD_PHUHOP`, `SD_HOILAI` | Trao đổi thêm về chi phí (24) | Trao đổi về chi phí |
| `SD_TIMHIEUTHEM` | Kết nối hệ thống đang dùng (26) | Kết nối hệ thống |
| `CHOT_VATTU` | Cần điều chỉnh báo giá (22) | Điều chỉnh báo giá |
| `MENU_VATTU`, `HOITIEP_VATTU` | Gửi danh mục cần báo giá (24, bảng 2.1) | Gửi danh mục (đúng theo 2.2 và 2.6) |

## 4. Câu "Đang dùng" suy ra theo quy tắc (không cần duyệt, để biết)

- `NL_TAILIEU_VUAROI`: câu 4.3 với "Hôm qua" thay bằng "Vừa rồi", theo đúng quy tắc trong kịch bản.
- `MENU_CHINH`: đoạn 2 của tin chào 1.1.
- `NL_BG_CHUANHAN`: dùng lại câu 4.3 cho nhánh báo giá 4.6.
- `CHOT_DIEUCHINH`: dùng lại câu chiết khấu ở 3.3.1.
