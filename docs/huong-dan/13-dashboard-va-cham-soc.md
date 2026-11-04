# 13 · Tổng quan CSKH, Chi tiết khách và Nhật ký chăm sóc

Dành cho: **trưởng sale** (mục 2, 5) và **nhân viên sale** (mục 3, 4). Nguồn kỹ thuật: `docs/13-dashboard-cskh.md`, `docs/14-nha-thau-b2b.md`. Bạn không cần mở các tệp đó.

> **Hiện trạng trung thực.** Ba màn hình này đã được kiểm bằng test tự động và dữ liệu tổng hợp. **Chưa ai mở chúng bằng trình duyệt thật**, chưa có ảnh chụp, chưa có sale thật dùng thử. Nhãn, tên nút và bố cục dưới đây lấy từ mã nguồn của giao diện nên tên chính xác, nhưng cách chúng hiển thị và cảm giác sử dụng thì **Chưa kiểm trên giao diện**. Nếu thấy khác mô tả, hãy báo quản trị. Kịch bản cho 2 đến 3 sale thử nằm ở `docs/nghiem-thu/uat-v2.md` (chưa chạy).

Mục lục: [1. Ba màn hình](#1-ba-màn-hình-và-ai-dùng) · [2. Tổng quan CSKH](#2-tổng-quan-cskh-cho-trưởng-sale) · [3. Chi tiết khách / Chi tiết nhà thầu](#3-chi-tiết-khách-và-chi-tiết-nhà-thầu) · [4. Nhật ký chăm sóc](#4-nhật-ký-chăm-sóc-từng-bước) · [5. Nhịp làm việc của trưởng sale](#5-nhịp-làm-việc-của-trưởng-sale) · [6. Công tắc và cấu hình](#6-công-tắc-và-cấu-hình) · [7. Sự cố thường gặp](#7-sự-cố-thường-gặp) · [8. Đã xong khi](#8-đã-xong-khi)

## 1. Ba màn hình và ai dùng

| Màn hình | Địa chỉ | Dùng để | Ai |
| --- | --- | --- | --- |
| **Tổng quan CSKH** | `/app/omni-dashboard` | Hôm nay có gì cần xử lý, kênh nào đông, ai đang quá tải, chiến dịch nào ra Lead | Trưởng sale; sale thấy phần của mình |
| **Hộp thư Omni** | `/app/omni-inbox` | Đọc và trả lời mọi kênh, duyệt nháp bot ([08](08-huong-dan-nhan-vien-sale.md)) | Sale |
| **Chi tiết khách** | `/app/omni-customer/<mã Lead>` | Trang đầy đủ một khách hoặc một nhà thầu: giai đoạn, hoạt động mọi kênh, nhật ký chăm sóc, gói thầu, điểm | Sale, trưởng sale |

Cả ba dành cho vai trò Sales User, Sales Manager, Omni Admin, System Manager. Sale **chỉ thấy Lead mà CRM cho phép bạn xem** (thường là Lead của mình); trưởng nhóm (người có vai trò đặt ở Omni Settings, `team_lead_role`, mặc định Sales Manager) thấy toàn đội. Từ Hộp thư vào Chi tiết khách bằng nút **Mở hồ sơ chăm sóc**; từ Tổng quan, nút **Mở** của bảng "Cần xử lý ngay" mở đúng hội thoại trong Hộp thư, còn bấm tên nhà thầu trong danh sách "Phù hợp A/B, chưa liên hệ" mở Chi tiết khách. Các trang này không thay trang Lead của Frappe CRM; nút **Mở Lead trong CRM** nối sang đó. Ngay trong trang Lead/Deal của CRM có thêm tab **Zalo** để đọc và trả lời Zalo mà không phải mở Hộp thư ([08](08-huong-dan-nhan-vien-sale.md) mục 4A); nhật ký chăm sóc vẫn ghi ở trang Chi tiết khách.

Các trang chưa được đặt vào menu hay Workspace nào (xem [08](08-huong-dan-nhan-vien-sale.md) mục 1): hãy lưu địa chỉ vào dấu trang hoặc nhờ quản trị thêm lối tắt.

## 2. Tổng quan CSKH (cho trưởng sale)

Mở `/app/omni-dashboard`. Đầu trang có tiêu đề, giờ cập nhật, danh sách chọn kỳ **Hôm nay / 7 ngày / 30 ngày**, và (nếu bạn là trưởng nhóm) danh sách chọn **Toàn đội / Của tôi**. Nút chính duy nhất: **Mở hộp thư**. Số liệu lưu đệm khoảng một phút nên có thể chậm một nhịp.

**Các ô chỉ số** (hàng đầu; ô nào thuộc tính năng chưa bật thì **biến mất**, không hiện số 0):

| Ô | Nghĩa | Hiện khi |
| --- | --- | --- |
| Hội thoại chờ trả lời | Số Lead mà tin cuối là tin khách, chưa ai trả lời | Luôn |
| Phản hồi lần đầu (TB) | Thời gian trung bình (phút làm việc) từ lúc Lead vào đến phản hồi đầu tiên, với Lead tạo trong kỳ | Luôn |
| Lead mới | Lead tạo trong kỳ, có ghi kênh đầu | Luôn |
| Gọi nhỡ chưa gọi lại (kèm số quá hạn) | Việc gọi lại còn mở | Có kênh tổng đài bật |
| Lead nóng | Lead đang mở đạt ngưỡng điểm | Chấm điểm bật và bảng điểm không rỗng |
| Nháp bot chờ duyệt | Nháp Draft chưa quyết | Bot ở chế độ Draft |

Ô có thể kèm mức đổi so với kỳ liền trước cùng độ dài, chỉ hiện khi so sánh có nghĩa. Màu xanh hay đỏ theo nghĩa nghiệp vụ (thời gian phản hồi giảm là tốt), không theo chiều lên xuống.

**Bảng "Cần xử lý ngay"** (tối đa 20 dòng, cho biết "n/tổng"): cột Khách hàng (tên và đơn vị), Kênh, Tin gần nhất, Đã chờ (phút), **Lý do ưu tiên**, Phụ trách, nút **Mở**. Sắp theo mức gấp rồi theo thời gian chờ giảm dần:

1. Khiếu nại (ý định khiếu nại ở lượt gần nhất, chưa có tin trả lời).
2. Gọi nhỡ chưa gọi lại.
3. Quá hạn phản hồi (chờ lâu hơn ngưỡng `response_sla_minutes` trong giờ làm việc).
4. Lead nóng có tin chờ.
5. Có nháp bot chờ duyệt.
6. Còn lại đang chờ.

**Các khối khác:**

- **Theo kênh**: số liên hệ và Lead mới theo kênh, kèm thanh tỉ lệ.
- **Bot hôm nay** (ẩn khi `bot_reply_mode` là Off): nháp đã soạn, gửi nguyên văn, sửa/bỏ, chuyển người và lý do.
- **Theo nhân viên**: Đang phụ trách, Chờ trả lời, Phản hồi lần đầu TB, Gọi lại quá hạn, Nháp đã duyệt. Dùng để thấy ai đang quá tải, **không** dùng để xếp hạng người khi dữ liệu còn ít.
- **Phễu giai đoạn**: số Lead theo giai đoạn bán hàng nhà thầu.
- **Nhà thầu theo hạng và khu vực**: số nhà thầu hạng A, B, C theo miền Bắc/Trung/Nam ([11](11-nha-thau-va-nhap-danh-sach.md) mục 6).
- **Phù hợp A/B, chưa liên hệ**: nhà thầu hạng A hoặc B mà chưa ai phản hồi hoặc Lead vẫn ở "Tiềm năng", sắp theo điểm phù hợp; mỗi dòng có tên, mã số thuế, hạng, điểm, khu vực, người phụ trách và nút **Ghi nhật ký**. Đây là danh sách **ưu tiên gọi chủ động** (liên hệ từng nhà thầu một, không gửi hàng loạt).
- **Theo chiến dịch**: với mỗi chiến dịch hoặc nguồn: Lead, Hợp lệ, % phản hồi trong hạn, phản hồi lần đầu (trung vị, phút), nhà thầu hạng A/B, đã trao đổi nhu cầu trở lên, Deal, Chi phí, Chi phí mỗi Lead hợp lệ. Lead thử không được tính; chi phí tính trên cả chiến dịch chứ không theo kỳ ([12](12-lead-quang-cao.md) mục 7.4).

Cách đọc khôn ngoan: số trên Tổng quan là **số đếm từ dữ liệu hệ thống**, chưa được đối chiếu với số đo thật từ tuần chạy thử. Đừng đưa vào báo cáo cho lãnh đạo như chỉ số đã kiểm chứng trước khi có tuần chạy thử.

## 3. Chi tiết khách và Chi tiết nhà thầu

Mở bằng **Mở hồ sơ chăm sóc** ở Hộp thư, hoặc bấm tên nhà thầu ở danh sách "Phù hợp A/B, chưa liên hệ" của Tổng quan. Đường dẫn trên cùng: Hộp thư / Lead / mã Lead. Khi khách là một nhà thầu, **tiêu đề là tên công ty kèm mã số thuế (MST)** và nhãn **Hạng A/B/C**; có nhãn đỏ **Lead nóng** (kèm điểm) hoặc **Từ chối nhận liên hệ** khi cần.

**Thao tác nhanh** ở đầu trang (hiện theo quyền và trạng thái): **Gọi** (hiện SĐT, bấm để gọi bằng điện thoại của máy bạn), **Nhắn Zalo** (mở Hộp thư), **Gửi email** (mở trình soạn của CRM trên Lead), **Ghi nhật ký chăm sóc**, **Mở Lead trong CRM**, và nút chính **Chuyển thành Deal**. Khách đã từ chối nhận liên hệ thì không có Gọi, Nhắn, Gửi email. **Chuyển thành Deal** dùng đúng hành động có sẵn của CRM và hỏi xác nhận trước ("Chuyển Lead này thành Deal?"); nên chuyển khi đã gửi đề xuất gói. Nút không hiện nếu Lead đã là Deal hoặc bạn không có quyền sửa.

**Thanh giai đoạn** hiện các bước Lead rồi Deal, bước hiện tại tô đậm: Tiềm năng, Đã liên hệ, Đã trao đổi nhu cầu (Lead); Đề xuất gói, Đàm phán, Ký hợp đồng (Deal); Không phù hợp (kèm lý do).

**Ba cột:**

| Cột trái | Cột giữa (các tab) | Cột phải |
| --- | --- | --- |
| Thông tin liên hệ và nhu cầu (bấm **Sửa** để sửa tại chỗ; chỗ bạn sửa tay thì bot không ghi đè). **Kênh đã liên kết** (Zalo OA, Email, Điện thoại; có nhãn "Quan tâm OA" nếu khách đang theo dõi OA). **Người liên hệ** (tên, chức danh, vai trò, nhãn đồng ý hoặc **Từ chối**, nút Gọi nếu được phép) | **Hoạt động**: mọi sự kiện theo ngày, mới nhất trước; lọc Tất cả / Tin nhắn / Cuộc gọi / Nhật ký / Hệ thống. **Nhật ký chăm sóc** (mục 4). **Gói thầu đã trúng** (chỉ nhà thầu): bảng Bệnh viện, Số TBMT, Loại hàng, Ngày, Giá trị, Mã hàng, Còn hiệu lực (Còn / Hết). **Cuộc gọi** (nếu có tổng đài) | **Mức phù hợp** (hạng, điểm, đạt hay chưa từng tiêu chí; mở "Cách tính" để xem công thức; có dòng cảnh báo nếu điểm lưu cũ hơn tham số hiện hành, sẽ tính lại ban đêm). **Điểm quan tâm** (điểm trên ngưỡng và các lý do cộng). **Gói đề xuất**. **Việc cần làm** (việc của Lead; tích để hoàn thành). **Nghi trùng** (hai nút **Gộp vào Lead này** / **Không phải trùng**). **Quản lý** (số ngày liên hệ trong 30 ngày) |

Các dòng "Hệ thống" trong tab Hoạt động ghi: tạo Lead, đổi người phụ trách hoặc trạng thái, lần đầu thành Lead nóng, gộp kênh hoặc gộp Lead. Một dòng "Ý định gần nhất" (nguồn Bot) hiện ở nhóm nhu cầu khi bot đã phân tích ([07](07-bot-ai.md) mục 3A).

**Lưu ý khi dùng:** **Gộp vào Lead này** là gộp thật và **không có hoàn tác**; nếu chưa chắc bấm **Không phải trùng** ([06](06-chong-trung-va-lead.md)). Hạng C không có nghĩa là bỏ: nó cho biết nhà thầu chưa đạt tiêu chí về số bệnh viện và số mã hàng theo hồ sơ thầu.

## 4. Nhật ký chăm sóc từng bước

**Nhật ký chăm sóc** ghi lại mỗi **phiên chăm sóc** với khách: chuỗi trao đổi liên tục trên một kênh, kết thúc khi khách im lặng quá `care_session_gap_hours` (mặc định 4 giờ); mỗi cuộc gọi là một phiên riêng. Hệ thống **tự tạo** phiên khi có tin vào hoặc ra (không tạo cho thông báo quan tâm/bỏ quan tâm, thư tự động, tin chế độ thử). Việc của bạn là **ghi kết quả**.

**Quan trọng:** phiên do hệ thống tự tạo từ tin của khách **không** tính là bạn đã phản hồi. Chỉ khi **bạn** ghi nhật ký (hoặc gửi tin ra, hoặc cuộc gọi đi được nghe máy) thì "phản hồi lần đầu" mới được ghi và đồng hồ hạn phản hồi mới dừng.

Cách ghi (Chưa kiểm trên giao diện):

1. Mở **Chi tiết khách** của Lead.
2. Bấm **Ghi nhật ký chăm sóc** ở đầu trang (hoặc mở tab **Nhật ký chăm sóc** rồi bấm nút tạo ở đó; từ Tổng quan, nút **Ghi nhật ký** ở dòng nhà thầu trong danh sách "Phù hợp A/B, chưa liên hệ" cũng dẫn tới đúng khách). Hộp thư **không** có form ghi nhật ký: nó chỉ nhắc "Chưa ghi kết quả".
3. Điền form:
   - **Kênh**: Điện thoại, Zalo OA, Email, Gặp trực tiếp, Khác (điền sẵn nếu đang có phiên mở).
   - **Thời gian**: lúc đã xảy ra trao đổi (để trống = bây giờ). Không thể sớm hơn lúc tạo Lead hay muộn hơn bây giờ; hệ thống tự kẹp lại.
   - **Tóm tắt**: vài câu về nội dung trao đổi (bot hoặc hệ thống có thể đã điền sẵn từ tin đầu; bạn sửa được).
   - **Kết quả** (chọn một): Đã tư vấn · Đã gửi tài liệu · Đã gửi báo giá · Hẹn gọi lại · Chờ khách phản hồi · Đã chốt đơn · Không có nhu cầu · Không liên lạc được · Đã xử lý khiếu nại.
   - **Bước tiếp theo** và **Hạn**: **bắt buộc với mọi kết quả, trừ "Đã chốt đơn" và "Không có nhu cầu"**. Có hạn thì phải có bước tiếp theo.
   - Ô **Tạo việc trong CRM** (tích sẵn): tạo một việc (CRM Task) giao người phụ trách Lead, hạn theo ô Hạn; sửa hạn sau này trên nhật ký thì việc cập nhật theo, không tạo việc thứ hai.
4. Bấm **Lưu**.

Quy ước kết quả (cũng hiện ngay cạnh form):

- **Hẹn gọi lại**: phải có hạn cụ thể.
- **Không liên lạc được**: **chưa tính là đã liên hệ**: Lead vẫn ở giai đoạn cũ.
- **Đã chốt đơn**: hệ thống chỉ **gợi ý** chuyển thành Deal ("Gợi ý: chuyển Lead thành Deal"); bạn tự quyết bằng nút **Chuyển thành Deal**.
- Các kết quả khác với Lead đang ở **Tiềm năng**: Lead tự chuyển sang **Đã liên hệ** (chỉ tiến, không lùi).

Nhắc việc: phiên có tin ra của sale mà sau **2 giờ** chưa có kết quả hiện nhãn đỏ **Chưa ghi kết quả** (ở Hộp thư và tab nhật ký); khoảng **17:45** mỗi ngày bạn nhận một thông báo tổng hợp các phiên chưa ghi (không báo từng phiên). Nhật ký là dữ liệu của Lead nên theo quyền xem Lead; khi gộp Lead, nhật ký được chuyển theo; "Xóa dữ liệu khách" xóa nội dung tóm tắt.

## 5. Nhịp làm việc của trưởng sale

| Lúc | Việc |
| --- | --- |
| Đầu ngày | Mở Tổng quan, kỳ **Hôm nay**, chọn **Toàn đội**. Đọc **Cần xử lý ngay** từ trên xuống: khiếu nại và gọi nhỡ trước. Phân lại việc nếu có người quá tải ở **Theo nhân viên** |
| Có chiến dịch chạy | Xem **Theo chiến dịch**: Lead mới, % phản hồi trong hạn, trung vị phản hồi lần đầu. Nếu % trong hạn thấp, kiểm giờ làm việc, số sale trực, và `response_sla_minutes` có hợp lý không |
| Giữa ngày | Xem **Phù hợp A/B, chưa liên hệ**: nhắc người phụ trách gọi chủ động từng nhà thầu (không gửi hàng loạt) |
| Cuối ngày | Nhắc đội ghi nhật ký; xem số phiên "Chưa ghi kết quả" |
| Hằng tuần | Đọc kỳ **7 ngày**; xem lại bảng điểm quan tâm và hạng ([02](02-cau-hinh-omni-settings.md) mục 8 và 9B) với kết quả thực tế; báo quản trị nếu thấy số bất thường |

## 6. Công tắc và cấu hình

Quản trị chỉnh ở Omni Settings ([02](02-cau-hinh-omni-settings.md) mục 9C): `dashboard_enabled` (tắt = Tổng quan báo "Dashboard CSKH đang tắt"), `care_log_enabled` (tắt = không tự tạo phiên, không có thông báo 17:45), `response_sla_minutes` (15), `care_session_gap_hours` (4), `team_lead_role` (Sales Manager). Khối của tính năng chưa bật (gọi nhỡ, Lead nóng, bot) tự ẩn.

## 7. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Tổng quan hiện "Dashboard CSKH đang tắt" | `dashboard_enabled` tắt | Quản trị bật lại |
| Không thấy lựa chọn **Toàn đội** | Bạn không có vai trò trưởng nhóm | Hỏi quản trị `team_lead_role` |
| Số trên Tổng quan chưa đổi dù vừa xử lý | Số liệu lưu đệm khoảng một phút | Chờ một phút rồi tải lại |
| Ô Lead nóng, Gọi nhỡ, Bot vắng mặt | Tính năng đó đang tắt (đúng thiết kế) | Không phải lỗi; xem [02](02-cau-hinh-omni-settings.md) |
| Khối **Theo chiến dịch** trống | Chưa có Lead quảng cáo trong kỳ, hoặc Lead là Lead thử | Chọn kỳ dài hơn; Lead thử không được tính |
| Không có nút **Chuyển thành Deal** | Lead đã là Deal, bạn không có quyền sửa, hoặc chưa đủ điều kiện | Hỏi trưởng sale |
| Báo lỗi khi lưu nhật ký: cần bước tiếp theo và hạn | Kết quả không thuộc nhóm miễn | Điền bước tiếp theo và hạn (mục 4) |
| Nhật ký có nhưng Lead vẫn "Tiềm năng" | Kết quả là "Không liên lạc được" | Đúng thiết kế |
| Mở Chi tiết khách báo không có quyền | Lead không thuộc quyền xem của bạn | Hỏi trưởng sale |
| Điểm phù hợp có dòng cảnh báo "điểm lưu đã cũ" | Tham số tính điểm vừa đổi | Hệ thống tự tính lại ban đêm |

## 8. Đã xong khi

Cho mỗi trưởng sale và sale mới, người hướng dẫn kiểm cùng họ trên staging (với `sandbox_mode` bật):

- [ ] Mở được Tổng quan; biết đổi kỳ và (trưởng nhóm) đổi **Toàn đội / Của tôi**; đọc được bảng "Cần xử lý ngay" và hiểu thứ tự ưu tiên.
- [ ] Mở được Chi tiết khách từ Hộp thư; đọc được hạng, mức phù hợp và gói thầu của một nhà thầu mẫu.
- [ ] Ghi được một nhật ký chăm sóc đủ kết quả, bước tiếp theo, hạn; thấy việc được tạo đúng một lần và Lead chuyển "Đã liên hệ".
- [ ] Hiểu "Không liên lạc được" không đẩy giai đoạn và "Đã chốt đơn" chỉ gợi ý chuyển Deal.
- [ ] Biết nhãn "Chưa ghi kết quả" và thông báo 17:45.
- [ ] Hiểu khách "Từ chối nhận liên hệ" thì không gọi hay nhắn; hệ thống không gửi gì hàng loạt.
- [ ] Trưởng sale đọc được **Theo chiến dịch** và biết giới hạn của con số (đếm từ dữ liệu hệ thống, chưa đối chiếu tuần chạy thử).
- [ ] Mọi điểm lệch giữa giao diện thật và mô tả ở tệp này đã được ghi lại để sửa tài liệu (trường hợp còn "Chưa kiểm trên giao diện" thì ghi rõ ai đã thử, ngày nào).
