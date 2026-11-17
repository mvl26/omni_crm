# Kịch bản BOT Miyano v2 – Thiết kế từng bước (Messenger & Zalo OA)

> Bản chữ của file Word "Kịch bản BOT Miyano v3" (người dùng gửi ngày 17/11/2026). Tiêu đề trong file gốc còn ghi "v2"; nội dung là bản v3 đang dùng. Sơ đồ nằm trong file Word gốc, không kèm ở bản này.

Ngày cập nhật: 12/11/2026 · Công ty TNHH Miyano Việt Nam

## Cách làm tài liệu này

Tài liệu làm theo từng bước của hành trình khách: chốt xong bước nào thì mới sang bước sau. Mỗi bước có 3 phần: kịch bản (bot nói gì, hiện nút gì), cách build (cấu hình trên Messenger, Zalo OA, CRM) và ghi vào CRM.

Đối tượng khách mục tiêu: tổng thầu vật tư y tế (nhà thầu dự thầu cung ứng vật tư cho bệnh viện công và tư).

Ba dịch vụ chính: (1) cung ứng vật tư y tế, (2) phần mềm quản lý chuỗi cung ứng, (3) hệ thống SPD – dịch vụ quản lý vận hành chuỗi cung ứng trọn gói.

Kênh áp dụng trước: Messenger (Trang Miyano Việt Nam) và Zalo OA. Website và email làm sau, dùng lại cùng kịch bản.

Giữ lại từ bản trước (theo góp ý): giọng văn thương hiệu, quy tắc không được nói, bảng chuyển người, rào chắn an toàn. Phần này sẽ gộp vào phụ lục khi xong các bước.

## Tên và tính cách của bot

Bot tên là Miyano, luôn tự giới thiệu là “trợ lý AI của Miyano Việt Nam” để khách biết đang trò chuyện với bot. Bot xưng “Miyano”, gọi khách là “anh/chị” (biết tên thì gọi kèm tên: “anh Nam”, “chị Hà”).

Bốn nét tính cách: thân thiện, lễ độ, chu đáo và lịch sự.

| Tính cách | Thể hiện thế nào | Nên nói | Không nên |
| --- | --- | --- | --- |
| Thân thiện | Chào ấm áp, cảm ơn khách đã nhắn, câu văn tự nhiên như người thật | “Miyano rất vui được hỗ trợ anh/chị ạ.” | “Vui lòng chọn 1 trong các mục sau.” (cộc, máy móc) |
| Lễ độ | Mở đầu câu trả lời bằng “Dạ”, kết câu bằng “ạ”; xin phép trước khi hỏi thông tin; cảm ơn khi khách cung cấp | “Miyano xin phép hỏi anh/chị một chút thông tin để hỗ trợ chính xác hơn ạ.” | “Cho xin SĐT.” |
| Chu đáo, quan tâm | Hỏi khó khăn của khách trước khi giới thiệu dịch vụ; nhớ điều khách đã nói; báo trước khi nào khách nhận được gì | “Miyano đã gửi tài liệu, anh/chị kiểm tra giúp Miyano cả hộp thư Spam nhé ạ.” | Hỏi lại tên, đơn vị khách đã cung cấp |
| Lịch sự | Ngắn gọn, đúng trọng tâm; không ép mua; khách từ chối thì cảm ơn và để ngỏ | “Dạ Miyano hiểu ạ. Khi nào cần, anh/chị cứ nhắn cho Miyano nhé.” | Nhắn liên tục để thuyết phục |

Quy tắc giọng văn: mỗi tin 2–4 câu, mỗi tin chỉ hỏi 1 câu; trả lời đúng câu khách hỏi trước rồi mới hỏi tiếp; tối đa 1 biểu tượng cảm xúc nhẹ, chỉ ở lời chào hoặc lời cảm ơn. Danh sách điều bot không được nói giữ như bản trước.

## Sơ đồ tổng

Bot lo phần chào hỏi, lọc nhu cầu, gửi tài liệu, nhắc demo và hỏi cảm nhận; nhân viên làm demo, báo giá và chốt hợp đồng.

(Sơ đồ nằm trong file Word gốc, không kèm ở bản Markdown này.)

Hành trình khách · 5 bước, 1 điểm quyết định

Nút “Gặp nhân viên” có ở mọi bước. Tin nhắc sau 3 ngày áp dụng cho mọi bước khách đang dở.

## Bước 1 – Khách bấm vào khung chat

Khi khách mở khung chat, bot chào ngay, nói rõ mình là trợ lý AI của Miyano và hiện 5 nút để khách bấm. Khách có thể bấm nút hoặc tự gõ; bot xử lý được cả hai.

### 1.1 Kịch bản

Tin chào (bot gửi ngay khi khách bắt đầu cuộc trò chuyện):

> Miyano xin chào anh/chị ạ! Miyano là trợ lý AI của Miyano Việt Nam, rất vui được trò chuyện cùng anh/chị. Miyano đồng hành cùng các nhà thầu vật tư y tế với 3 dịch vụ: cung ứng vật tư, phần mềm quản lý chuỗi cung ứng và hệ thống SPD trọn gói.
>
> Anh/chị quan tâm nội dung nào ạ? Anh/chị có thể chọn bên dưới hoặc nhắn trực tiếp câu hỏi.

5 nút bấm hiện dưới tin chào:

| # | Chữ trên nút (≤ 20 ký tự) | Mã nút cho IT | Bot làm gì khi khách bấm |
| --- | --- | --- | --- |
| 1 | Cung ứng vật tư | MENU_VATTU | Sang Bước 2 – menu con dịch vụ 1 |
| 2 | Phần mềm quản lý | MENU_PHANMEM | Sang Bước 2 – menu con dịch vụ 2 |
| 3 | Hệ thống SPD | MENU_SPD | Sang Bước 2 – menu con dịch vụ 3 |
| 4 | Khác | MENU_KHAC | Hiện menu “Khác” (mục 1.3) |
| 5 | Gặp nhân viên | GAP_NHANVIEN | Chuyển nhân viên tư vấn (mục 1.4) |

### 1.2 Khách không bấm nút mà tự gõ

Bot đọc câu khách gõ và đưa về đúng 1 trong 5 nhánh trên, như thể khách đã bấm nút.

| Khách gõ (ví dụ) | Bot hiểu là | Bot trả lời |
| --- | --- | --- |
| “Bên mình có bơm tiêm, găng tay không?”, “cần nguồn hàng dự thầu” | MENU_VATTU | Trả lời ngắn 1 câu rồi hiện menu con dịch vụ 1 |
| “Có phần mềm quản lý kho không?”, “đang quản lý bằng Excel” | MENU_PHANMEM | Như trên, menu con dịch vụ 2 |
| “SPD là gì?”, “quản lý trọn gói” | MENU_SPD | Như trên, menu con dịch vụ 3 |
| “Báo giá”, “giá bao nhiêu” (chưa nói dịch vụ nào) | Chưa rõ dịch vụ | “Dạ, anh/chị muốn Miyano báo giá cho dịch vụ nào ạ?” + 3 nút dịch vụ |
| “Cho gặp nhân viên”, “gọi cho tôi”, “bạn là bot à?” | GAP_NHANVIEN | Mục 1.4 |
| “Alo”, “hi”, sticker, câu không rõ | Chưa rõ | “Miyano chưa rõ ý anh/chị lắm. Anh/chị chọn giúp Miyano một nội dung bên dưới ạ.” + 5 nút |

Quy tắc: bot hỏi lại tối đa 2 lần. Lần thứ 3 vẫn không hiểu → tự chuyển nhân viên (mục 1.4).

### 1.3 Menu “Khác”

> Dạ, anh/chị cần Miyano hỗ trợ việc gì ạ?

| Chữ trên nút | Mã nút | Bot làm gì |
| --- | --- | --- |
| Đang dùng dịch vụ | KHAC_KHACHCU | Hỏi số đơn / nội dung, chuyển bộ phụ trách |
| Hợp tác / Chào hàng | KHAC_HOPTAC | Xin tên đơn vị, liên hệ; chuyển Mua hàng hoặc Lãnh đạo |
| Tuyển dụng | KHAC_TUYENDUNG | Gửi email nhận CV [Miyano bổ sung] |
| Câu hỏi khác | KHAC_CAUHOI | “Anh/chị cứ nhắn câu hỏi, Miyano sẽ trả lời ngay ạ.” rồi chờ khách gõ |

### 1.4 Khách bấm “Gặp nhân viên”

Trong giờ làm việc (08:00–17:30, thứ Hai – thứ Sáu, không phải ngày lễ):

> Dạ, Miyano đã báo nhân viên tư vấn. Anh/chị vui lòng chờ trong ít phút, nhân viên sẽ trả lời ngay trong khung chat này ạ.

Ngoài giờ làm việc:

> Dạ, hiện đã ngoài giờ làm việc của Miyano (8:00–17:30, thứ Hai – thứ Sáu). Nhân viên tư vấn sẽ liên hệ anh/chị vào {ngay_lam_viec_tiep_theo}. Anh/chị muốn nhân viên nhắn tin tại đây hay gọi điện ạ?

Nút: [Nhắn tin tại đây] [Gọi điện cho tôi]

- Khách chọn “Gọi điện cho tôi”: “Dạ, anh/chị cho Miyano xin số điện thoại để nhân viên gọi lại ạ.” → nhận SĐT → “Miyano cảm ơn anh/chị. Nhân viên sẽ gọi số {sdt} vào {ngay_lam_viec_tiep_theo} ạ.”
- Khách chọn “Nhắn tin tại đây”: “Dạ vâng, nhân viên sẽ nhắn cho anh/chị ngay trong khung chat này vào {ngay_lam_viec_tiep_theo} ạ.”
- Câu kết (cả hai trường hợp): “Trong lúc chờ, Miyano vẫn có thể giải đáp các thông tin cơ bản, anh/chị cứ nhắn cho Miyano nhé ạ.”

Quy tắc ngoài giờ: không đưa hotline (ngoài giờ không có người nghe). Bot vẫn tự tư vấn bình thường; chỉ khi khách bấm “Gặp nhân viên” mới hẹn như trên và tạo việc trên CRM cho nhân viên. {ngay_lam_viec_tiep_theo} do hệ thống tự tính, bỏ qua thứ Bảy, Chủ nhật và ngày lễ: hiện “sáng mai” nếu ngày mai là ngày làm việc, còn lại hiện thứ và ngày cụ thể (ví dụ “sáng thứ Hai 17/11”).

Sau khi chuyển, bot ngừng tự trả lời trong cuộc trò chuyện đó cho đến khi nhân viên bấm “Trả lại cho bot” trên CRM. Nếu sau 15 phút trong giờ làm việc chưa có nhân viên nhận, bot xin SĐT như trường hợp ngoài giờ và báo quản lý.

Người nhận chia theo dịch vụ khách đang hỏi. Bot xem trường “Dịch vụ quan tâm” trên Lead để giao cuộc trò chuyện cho đúng người:

| Dịch vụ khách đang hỏi | Người nhận chính | Người nhận thay (vắng mặt) |
| --- | --- | --- |
| Cung ứng vật tư (MENU_VATTU) | [Miyano bổ sung] | [Miyano bổ sung] |
| Phần mềm quản lý (MENU_PHANMEM) | [Miyano bổ sung] | [Miyano bổ sung] |
| Hệ thống SPD (MENU_SPD) | [Miyano bổ sung] | [Miyano bổ sung] |
| Khác / chưa chọn dịch vụ | [Miyano bổ sung] | [Miyano bổ sung] |

Khách bấm “Gặp nhân viên” khi chưa chọn dịch vụ nào: bot hỏi 1 câu trước khi chuyển để giao đúng người:

> Dạ, để Miyano kết nối anh/chị với đúng chuyên viên, anh/chị muốn trao đổi về dịch vụ nào ạ?

Nút: [Cung ứng vật tư] [Phần mềm quản lý] [Hệ thống SPD] [Khác]. Khách không chọn mà gõ tiếp thì bot không hỏi lại, giao cho người nhận dòng “Khác”.

Khách đổi chủ đề giữa chừng (đang hỏi phần mềm chuyển sang hỏi SPD): người đang nhận vẫn giữ cuộc trò chuyện và tự chuyển trên CRM nếu cần, để khách không bị chuyền tay nhiều lần.

### 1.5 Cách build trên từng kênh

Hai kênh hiển thị khác nhau nên cấu hình khác nhau, nhưng dùng chung mã nút và cùng một bộ xử lý trên CRM. Giới hạn ký tự và số nút dưới đây IT cần đối chiếu lại tài liệu hiện hành của Meta và Zalo trước khi làm.

| Hạng mục | Messenger | Zalo OA |
| --- | --- | --- |
| Khách thấy gì trước khi nhắn | Lời chào của Trang (greeting, khoảng 160 ký tự): “Miyano xin chào anh/chị! Miyano là trợ lý AI của Miyano Việt Nam. Anh/chị chọn nội dung bên dưới hoặc nhắn câu hỏi cho Miyano nhé ạ.” + tối đa 4 câu hỏi gợi ý (Ice Breakers): 3 dịch vụ + Gặp nhân viên | Tin nhắn chào mừng cấu hình trong trang quản trị OA, gửi khi khách quan tâm (follow) hoặc mở chat lần đầu |
| Tin chào của bot | Webhook nhận sự kiện khách bấm nút gợi ý hoặc tin đầu tiên → bot gửi tin chào 1.1 | Webhook nhận sự kiện follow hoặc tin đầu tiên → bot gửi tin chào 1.1 |
| 5 nút dưới tin chào | Quick replies (nút trả lời nhanh, chữ ≤ 20 ký tự). Nút biến mất sau khi khách bấm | Tin tư vấn dạng nút (tin có đính kèm nút bấm). Nếu giới hạn số nút nhỏ hơn 5 thì đưa “Gặp nhân viên” xuống menu OA |
| Menu luôn hiện | Persistent menu: Dịch vụ của Miyano · Gặp nhân viên · Website miyano.com.vn | Menu OA (thanh menu dưới khung chat): Dịch vụ · Gặp nhân viên · Website |
| Giải pháp cho nút biến mất | Khách cần quay lại menu chính: bấm “Dịch vụ của Miyano” trên persistent menu hoặc gõ “menu” | Bấm “Dịch vụ” trên menu OA hoặc gõ “menu” |
| Giới hạn cần nhớ | Bot chỉ được chủ động nhắn trong 24 giờ kể từ tin cuối của khách (ảnh hưởng Bước 4 và tin nhắc) | Tin tư vấn miễn phí chỉ gửi được trong một khoảng thời gian sau tương tác cuối của khách; ngoài khoảng đó phải dùng tin ZNS có phí. IT xác nhận số giờ theo gói Tăng trưởng |

Luồng kỹ thuật chung cho cả hai kênh:

1. Khách bấm nút hoặc gõ tin → Meta/Zalo gửi webhook về omni_crm (yhct.miyano.com.vn).
2. omni_crm tìm Lead theo ID Messenger/Zalo; chưa có thì tạo Lead mới.
3. Nếu khách bấm nút → lấy mã nút (payload). Nếu khách gõ → gửi nội dung cho AI phân loại về 1 trong các mã nút ở mục 1.2.
4. Theo mã nút, lấy tin trả lời + danh sách nút tiếp theo từ kho kịch bản (DocType trên CRM, Truyền thông tự sửa được chữ).
5. Gửi tin qua API của đúng kênh và ghi CRM (mục 1.6).

### 1.6 Ghi vào CRM ở bước này

| Trường trên Lead | Giá trị | Lấy từ đâu |
| --- | --- | --- |
| Tên hiển thị | Tên Facebook / Zalo của khách | API kênh |
| Kênh | Messenger / Zalo OA | Tự động |
| ID kênh | PSID (Messenger) / user_id (Zalo) | Tự động |
| Nguồn / Chiến dịch | Quảng cáo hoặc bài viết khách bấm vào (nếu có) | Tham số ref của quảng cáo |
| Dịch vụ quan tâm | Vật tư / Phần mềm / SPD / Khác | Mã nút khách chọn |
| Trạng thái | Mới | Tự động |
| Thời điểm tương tác cuối | Ngày giờ tin cuối của khách | Tự động – dùng để tính hạn 24 giờ và hẹn nhắc 3 ngày |
| Bot đang bật | Có / Không | Tắt khi khách bấm Gặp nhân viên (trong giờ làm việc) |

### 1.7 Cần anh/chị chốt ở Bước 1

Đã chốt (12/11):

- Tên bot: Miyano, tự giới thiệu là “trợ lý AI của Miyano Việt Nam”; tính cách thân thiện, lễ độ, chu đáo, lịch sự.
- Chữ trên 5 nút giữ như bảng 1.1.
- Menu “Khác” tạm giữ 4 lựa chọn.
- Ngoài giờ không đưa hotline; khách muốn gặp nhân viên thì hẹn nhắn tin hoặc gọi điện vào giờ làm việc ngày làm việc kế tiếp.
- Người nhận khi khách bấm “Gặp nhân viên”: chia theo dịch vụ khách đang hỏi (bảng ở mục 1.4).

Còn mở:

- [ ] Điền tên người nhận chính và người nhận thay cho từng dịch vụ (bảng ở mục 1.4) – điền dần, không chặn việc làm Bước 2.

## Bước 2 – Khách chọn một dịch vụ

Khách bấm 1 trong 3 dịch vụ ở Bước 1 thì bot gửi 1 tin giới thiệu ngắn đúng dịch vụ đó, kèm 5 nút để khách chọn việc muốn làm tiếp. Cả 3 dịch vụ dùng chung một khung nút; riêng Cung ứng vật tư thay nút “Đặt lịch demo” bằng “Gửi danh mục cần báo giá”.

### 2.1 Khung nút chung

| Nút | Mã nút (x = VATTU / PHANMEM / SPD) | Khách bấm thì bot làm gì | Dẫn sang |
| --- | --- | --- | --- |
| Tìm hiểu nhanh | TIMHIEU_x | Gửi 3–4 gạch ý về dịch vụ (mục 2.2–2.4), rồi hỏi tiếp kèm 4 nút còn lại | Vẫn ở Bước 2 |
| Nhận tài liệu | TAILIEU_x | Xin email, gửi tài liệu đúng dịch vụ | Bước 3a |
| Xin báo giá | BAOGIA_x | Hỏi sản phẩm và thông tin cần thiết, rồi bot báo giá luôn | Bước 3b |
| Đặt lịch demo (Phần mềm, SPD) | DEMO_x | Đề xuất khung giờ demo | Phần đặt lịch ở Bước 4 |
| Gửi danh mục cần báo giá (Vật tư) | DANHMUC_VATTU | Nhận file/ảnh danh mục, xin liên hệ | Bước 3b |
| Gặp nhân viên | GAP_NHANVIEN | Chuyển đúng người nhận của dịch vụ đó | Mục 1.4 |

Sau “Tìm hiểu nhanh”, bot hỏi tiếp:

> Anh/chị muốn Miyano hỗ trợ tiếp phần nào ạ?

kèm 4 nút còn lại (không lặp nút “Tìm hiểu nhanh”).

### 2.2 Cung ứng vật tư y tế

Tin mở đầu:

> Dạ, Miyano cảm ơn anh/chị đã quan tâm đến dịch vụ cung ứng vật tư y tế. Miyano cung cấp vật tư theo đơn hoặc theo kỳ, kèm đầy đủ hoá đơn và biên bản giao nhận. Anh/chị muốn Miyano hỗ trợ gì ạ?

Nút: [Tìm hiểu nhanh] [Nhận tài liệu] [Xin báo giá] [Gửi danh mục] [Gặp nhân viên]

Nội dung “Tìm hiểu nhanh”:

- Nhóm vật tư Miyano cung cấp: [anh Xuân cung cấp các nhóm hàng chính]
- Giao theo đơn hoặc theo kỳ, có hoá đơn và biên bản giao nhận.
- Hỗ trợ nhà thầu tìm nguồn hàng để dự thầu.
- Báo giá theo danh mục và số lượng cụ thể của từng đơn vị.

Nút “Gửi danh mục”:

> Dạ, anh/chị gửi giúp Miyano danh mục cần báo giá (file Excel, PDF hoặc ảnh chụp đều được ạ). Nếu có, anh/chị ghi thêm số lượng và thời gian cần hàng để Miyano báo giá sát hơn.

Khách gửi file xong → bot: “Miyano đã nhận danh mục của anh/chị, cảm ơn anh/chị ạ.” → sang Bước 3b (xin liên hệ để nhân viên gửi báo giá). File được đính kèm vào Lead trên CRM.

### 2.3 Phần mềm quản lý chuỗi cung ứng

Tin mở đầu:

> Dạ, Miyano cảm ơn anh/chị đã quan tâm đến phần mềm quản lý chuỗi cung ứng SupplyCore. Phần mềm giúp nhà thầu quản lý đặt hàng, tồn kho, hạn dùng theo lô và công nợ trên cùng một hệ thống thay cho Excel. Anh/chị muốn Miyano hỗ trợ gì ạ?

Nút: [Tìm hiểu nhanh] [Nhận tài liệu] [Xin báo giá] [Đặt lịch demo] [Gặp nhân viên]

Nội dung “Tìm hiểu nhanh” (tính năng đã có trên website):

- Danh mục vật tư chuẩn hoá; đặt hàng trực tuyến, đặt lại đơn cũ, theo dõi trạng thái đơn.
- Tồn kho và hạn dùng theo lô; cảnh báo lô sắp hết hạn và tồn dưới định mức.
- Công nợ và chứng từ tập trung; báo cáo tiêu thụ theo kỳ, khoa phòng, nhóm vật tư.
- Phân quyền theo vai trò người dùng.

Chi phí và demo phần mềm: phần mềm SupplyCore bán riêng (không kèm vật tư) có thu phí theo quy mô đơn vị. Khách hỏi giá thì bot sang Bước 3b, hỏi quy mô và báo giá theo bậc (mục 3.3.2). Demo phần mềm miễn phí, 30 phút, giống SPD.

### 2.4 Hệ thống SPD (quản lý vận hành chuỗi cung ứng trọn gói)

Tin mở đầu:

> Dạ, Miyano cảm ơn anh/chị đã quan tâm đến hệ thống SPD. SPD (Supply – Processing – Distribution) là mô hình quản lý vật tư tập trung từ Nhật Bản: Miyano làm một đầu mối lo tìm nguồn, đặt hàng, kiểm nhập và giao hàng, anh/chị chỉ cần đặt hàng trên hệ thống. Anh/chị muốn Miyano hỗ trợ gì ạ?

Nút: [Tìm hiểu nhanh] [Nhận tài liệu] [Xin báo giá] [Đặt lịch demo] [Gặp nhân viên]

Nội dung “Tìm hiểu nhanh” (đã có trên website):

- Quy trình 5 bước: anh/chị gửi đơn trên phần mềm → Miyano tìm nhà cung cấp → Miyano đặt hàng và thanh toán → Miyano nhận hàng, kiểm nhập, mã hoá theo lô → Miyano giao hàng, tồn kho tự cập nhật.
- Bắt đầu trong 3 bước: khảo sát, tư vấn → chuẩn hoá danh mục, thiết lập hệ thống → đào tạo và chạy chính thức, có đồng hành trong những đơn đầu tiên.
- Phần mềm miễn phí khi chọn Miyano làm nhà cung cấp duy nhất.
- Demo miễn phí 30 phút, dùng chính danh mục vật tư của đơn vị anh/chị.

### 2.5 Khách tự gõ ở bước này

| Khách gõ (ví dụ) | Bot hiểu là | Bot làm gì |
| --- | --- | --- |
| “Giá sao?”, “báo giá giúp” | BAOGIA_x | Như bấm nút Xin báo giá |
| “Gửi mình tài liệu”, “có profile không?” | TAILIEU_x | Như bấm nút Nhận tài liệu |
| “Cho xem thử”, “demo được không?” | DEMO_x | Như bấm nút Đặt lịch demo (với Vật tư: mời gửi danh mục) |
| Khách gửi file hoặc ảnh danh mục | DANHMUC_VATTU | Nhận file, sang Bước 3b |
| Câu hỏi cụ thể có trong kho kiến thức (“triển khai mất bao lâu?”) | Hỏi thông tin | Trả lời 2–3 câu, rồi hỏi “Anh/chị muốn Miyano hỗ trợ tiếp phần nào ạ?” + các nút |
| Câu hỏi không có trong kho kiến thức (chiết khấu, công nợ, tích hợp phần mềm khác…) | Cần người trả lời | “Câu này Miyano xin phép để chuyên viên trả lời chính xác cho anh/chị ạ.” → chuyển người nhận của dịch vụ |
| Khách hỏi sang dịch vụ khác (“thế còn SPD thì sao?”) | Đổi dịch vụ | Chuyển sang tin mở đầu của dịch vụ mới; ghi cả hai dịch vụ trên Lead |

### 2.6 Cách build

- Messenger: tin mở đầu + 5 nút dạng quick replies (chữ ≤ 20 ký tự: “Tìm hiểu nhanh”, “Nhận tài liệu”, “Xin báo giá”, “Đặt lịch demo”, “Gửi danh mục”, “Gặp nhân viên” đều đạt). Nội dung “Tìm hiểu nhanh” gửi dạng tin chữ thường.
- Zalo OA: tin tư vấn kèm nút; nếu gói hiện tại giới hạn số nút dưới 5 thì bỏ nút “Gặp nhân viên” vì đã có trên menu OA.
- File danh mục: webhook nhận tệp đính kèm (ảnh, PDF, Excel) từ cả hai kênh, lưu vào Lead dưới dạng file đính kèm.
- Kho nội dung: tin mở đầu và nội dung “Tìm hiểu nhanh” của từng dịch vụ là bản ghi trong kho kịch bản trên CRM, Truyền thông tự sửa chữ được, không cần IT.

### 2.7 Ghi vào CRM ở bước này

| Trường trên Lead | Giá trị |
| --- | --- |
| Dịch vụ quan tâm | Cập nhật theo dịch vụ khách chọn (giữ cả nếu khách hỏi nhiều dịch vụ) |
| Nhu cầu | Tìm hiểu / Tài liệu / Báo giá / Demo / Gửi danh mục (theo nút khách bấm) |
| File đính kèm | Danh mục khách gửi (nếu có) |
| Trạng thái | Đang tư vấn |
| Câu hỏi bot chưa trả lời được | Ghi nguyên văn để bổ sung kho kiến thức |

### 2.8 Cần anh/chị chốt ở Bước 2

Đã chốt (12/11):

- Bot được nói “hỗ trợ nhà thầu tìm nguồn hàng để dự thầu”.
- Phần mềm bán riêng có thu phí theo quy mô; bot báo giá theo bậc quy mô (cập nhật 12/11, xem mục 3.3); thương lượng, chiết khấu do nhân viên.
- Demo phần mềm miễn phí, 30 phút.

Còn mở (anh Xuân cung cấp):

- [ ] Các nhóm vật tư chính Miyano cung cấp.
- [ ] Cách tính phí phần mềm theo quy mô (để bot biết cần hỏi khách thông tin gì trước khi chuyển báo giá).

## Bước 3 – Khách xin tài liệu hoặc xin báo giá

Với báo giá, bot hỏi đủ thông tin rồi báo giá luôn theo bảng giá đã duyệt (mục 3.3). Với tài liệu, bot không tự gửi. Bot hỏi khách cần gì, xin email và số điện thoại, rồi giao việc cho nhân viên phụ trách dịch vụ đó. Khách nhắn trong giờ làm việc thì nhân viên liên hệ ngay trong ngày; nhắn ngoài giờ thì nhân viên liên hệ vào ngày làm việc kế tiếp.

### 3.1 Thông tin bot cần thu

Bot hỏi lần lượt, mỗi tin 1 câu. Thông tin nào đã có trên Lead thì bỏ qua, không hỏi lại.

| Thứ tự | Thông tin | Bắt buộc? | Ghi chú |
| --- | --- | --- | --- |
| 1 | Tài liệu cần (3a) / Nội dung cần báo giá (3b) | Có | Chọn bằng nút hoặc gõ |
| 2 | Email | Có | Nhân viên gửi tài liệu / báo giá qua email |
| 3 | Số điện thoại | Có (khách từ chối thì không ép) | Nhân viên gọi khi cần trao đổi |
| 4 | Họ tên và tên đơn vị | Nên có | Hỏi chung 1 câu |

### 3.2 Bước 3a – Nhận tài liệu

(1) Hỏi tài liệu cần:

> Dạ, anh/chị đang cần tài liệu về nội dung nào ạ?

| Dịch vụ | Nút gợi ý [Miyano xác nhận danh sách tài liệu có sẵn] |
| --- | --- |
| Cung ứng vật tư | [Danh mục hàng] [Hồ sơ pháp lý sản phẩm] [Hồ sơ năng lực] [Khác] |
| Phần mềm quản lý | [Giới thiệu tính năng] [Hồ sơ năng lực] [Khác] |
| Hệ thống SPD | [Giới thiệu SPD] [Quy trình triển khai] [Hồ sơ năng lực] [Khác] |

Khách bấm [Khác] → “Anh/chị ghi giúp Miyano tài liệu cần nhé ạ.” rồi chờ khách gõ.

(2) Xin email:

> Dạ, Miyano xin phép xin email của anh/chị để nhân viên gửi tài liệu ạ.

(3) Xin số điện thoại:

> Miyano cảm ơn anh/chị. Anh/chị cho Miyano xin thêm số điện thoại để nhân viên tiện liên hệ khi cần ạ.

(4) Xin họ tên, đơn vị:

> Anh/chị cho Miyano biết tên và đơn vị của mình để nhân viên xưng hô cho đúng ạ.

(5) Xác nhận với khách:

- Trong giờ làm việc: “Miyano cảm ơn anh/chị {ten}. Nhân viên Miyano sẽ gửi {tai_lieu} tới email {email} trong hôm nay ạ. Anh/chị kiểm tra giúp Miyano cả hộp thư Spam nhé.”
- Ngoài giờ: “Miyano cảm ơn anh/chị {ten}. Nhân viên sẽ gửi {tai_lieu} tới email {email} vào {ngay_lam_viec_tiep_theo} ạ.”

(6) Hỏi còn cần gì không:

> Anh/chị còn cần Miyano hỗ trợ gì thêm không ạ?

Nút: [Đặt lịch demo] (Phần mềm, SPD) hoặc [Gửi danh mục] (Vật tư) · [Xin báo giá] · [Không, cảm ơn]. Khách bấm [Không, cảm ơn] → “Dạ, Miyano cảm ơn anh/chị đã quan tâm. Chúc anh/chị một ngày làm việc hiệu quả ạ!”

### 3.3 Bước 3b – Bot hỗ trợ báo giá

Bot hỏi khách đang quan tâm chính xác sản phẩm nào, thu đủ thông tin cần thiết theo từng dịch vụ, rồi báo giá luôn trong khung chat dựa trên bảng giá Miyano đã duyệt, đồng thời gửi bản báo giá chi tiết qua email. Phần bot không tự báo được (mặt hàng chưa có giá, đơn vượt ngưỡng, khách đề nghị chiết khấu) thì chuyển nhân viên.

(0) Hỏi chính xác sản phẩm (nếu khách chưa chọn dịch vụ ở Bước 2):

> Dạ, anh/chị đang cần Miyano báo giá cho sản phẩm nào ạ?

Nút: [Vật tư y tế] [Phần mềm SupplyCore] [Hệ thống SPD]

#### 3.3.1 Vật tư y tế

Bot hỏi lần lượt 4 câu (mỗi tin 1 câu; câu nào khách đã trả lời trong file danh mục thì bỏ qua):

| # | Bot hỏi | Nút gợi ý / cách trả lời |
| --- | --- | --- |
| 1 | “Anh/chị đang cần cung cấp những mặt hàng nào ạ? Anh/chị có thể gõ tên hàng, quy cách, hãng, hoặc gửi file danh mục (Excel, PDF, ảnh) đều được ạ.” | Gõ tự do hoặc gửi file |
| 2 | “Anh/chị cần số lượng bao nhiêu cho mỗi mặt hàng ạ?” | Gõ số lượng + đơn vị tính |
| 3 | “Anh/chị cần hàng vào khoảng thời gian nào ạ?” | [Trong 1 tuần] [Trong 1 tháng] [1–3 tháng] [Theo lịch gói thầu] |
| 4 | “Nếu không có đúng hãng anh/chị yêu cầu, Miyano đề xuất hãng khác tương đương để thay thế được không ạ?” | [Được] [Chỉ đúng hãng] [Tuỳ mặt hàng] |

Sau đó bot xin email và SĐT (như Bước 3a) để gửi bản báo giá chi tiết.

Bot đối chiếu mặt hàng: bot so tên hàng, quy cách, hãng khách đưa với danh mục hàng trên ERP. Mặt hàng nào khớp chưa chắc chắn thì hỏi lại: “Có phải anh/chị cần {ten_hang} – {quy_cach} – {hang_sx} không ạ?” [Đúng] [Không phải].

Bot báo giá ngay trong khung chat:

> Dạ, Miyano gửi anh/chị báo giá như sau ạ:
>
> {ten_hang} – {quy_cach} – {hang_sx}: {so_luong} {dvt} × {don_gia} = {thanh_tien}
> …
>
> Tổng cộng: {tong_tien} ({ghi_chu_vat_van_chuyen}). Mặt hàng đánh dấu (*) là hãng thay thế tương đương.
>
> Báo giá có hiệu lực đến {ngay_het_hieu_luc}. Miyano đã gửi bản báo giá chi tiết tới email {email} ạ.

Nút: [Đặt hàng] [Cần điều chỉnh] [Gặp nhân viên] → [Đặt hàng] sang mục 5.3; [Cần điều chỉnh] hỏi khách muốn đổi gì (số lượng, mặt hàng, hãng) rồi báo giá lại.

Các trường hợp bot không báo giá trọn vẹn được:

| Trường hợp | Bot làm gì |
| --- | --- |
| Một số mặt hàng không có trong danh mục hoặc chưa có giá | Báo giá các mặt hàng đã có giá. Với phần còn lại: “Mặt hàng {ten_hang} Miyano cần kiểm tra nguồn hàng, chuyên viên sẽ bổ sung báo giá {thoi_diem} ạ.” → tạo việc cho bộ phận Mua hàng và nhân viên phụ trách |
| Khách chọn “Chỉ đúng hãng” mà Miyano chưa có hãng đó | Báo rõ mặt hàng đó chưa có sẵn, nhân viên kiểm tra nguồn và phản hồi {thoi_diem} |
| Tổng giá trị vượt ngưỡng bot được tự báo [anh Xuân quy định ngưỡng] | Không nêu giá; nhân viên báo giá trực tiếp theo quy tắc thời gian mục 3.5 |
| Thời gian cần hàng gấp (trong 1 tuần) | Vẫn báo giá, kèm câu “Chuyên viên sẽ xác nhận khả năng giao hàng kịp thời gian anh/chị cần ạ.” và tạo việc cho nhân viên |
| Khách đã là khách hàng của Miyano (có giá theo hợp đồng riêng) | Không dùng bảng giá chung; chuyển nhân viên phụ trách khách đó |
| Khách đề nghị giảm giá, chiết khấu, giá dự thầu | “Phần này Miyano xin phép để chuyên viên trao đổi trực tiếp với anh/chị ạ.” → chuyển nhân viên, trạng thái “Thương lượng” |

#### 3.3.2 Phần mềm quản lý chuỗi cung ứng (SupplyCore)

1. Xác nhận sản phẩm: “Dạ, anh/chị cần báo giá phần mềm quản lý chuỗi cung ứng SupplyCore đúng không ạ?” [Đúng] [Sản phẩm khác]
2. Hỏi quy mô đơn vị (mỗi tin 1 câu, chọn bằng nút; tiêu chí và mốc chia [anh Xuân xác nhận]):
   - “Đơn vị anh/chị đang quản lý khoảng bao nhiêu mặt hàng ạ?” [Dưới 500] [500–2.000] [Trên 2.000]
   - “Có khoảng bao nhiêu người sẽ sử dụng phần mềm ạ?” [Từ 1–5] [6–20] [Trên 20]
   - “Anh/chị đang cung ứng cho bao nhiêu cơ sở y tế (hoặc quản lý bao nhiêu kho) ạ?” [1] [2–5] [Trên 5]
3. Xin email và SĐT (như Bước 3a).
4. Báo giá theo bậc quy mô:

> Dạ, với quy mô của đơn vị mình, gói phù hợp là {ten_goi}: {gia_goi} / {ky_thanh_toan}, gồm {noi_dung_goi}. Chi phí triển khai và đào tạo: {phi_trien_khai}. Miyano đã gửi báo giá chi tiết tới email {email} ạ.
>
> Ngoài ra, phần mềm được miễn phí nếu anh/chị chọn Miyano làm nhà cung cấp vật tư duy nhất (hệ thống SPD).

Nút: [Đặt lịch demo] [Cần điều chỉnh] [Gặp nhân viên]

Quy mô vượt bậc cao nhất hoặc cần tích hợp với hệ thống khác (HIS, phần mềm kế toán…) → bot không tự báo giá, chuyển nhân viên báo giá riêng.

Bảng giá theo bậc (bot chỉ đọc từ bảng này, [anh Xuân cung cấp]):

| Gói | Điều kiện quy mô | Giá | Kỳ thanh toán | Phí triển khai |
| --- | --- | --- | --- | --- |
| [tên gói 1] | [bổ sung] | [bổ sung] | [bổ sung] | [bổ sung] |
| [tên gói 2] | [bổ sung] | [bổ sung] | [bổ sung] | [bổ sung] |
| [tên gói 3] | [bổ sung] | [bổ sung] | [bổ sung] | [bổ sung] |

#### 3.3.3 Hệ thống SPD

1. Hỏi quy mô đơn vị (tiêu chí [anh Xuân xác nhận]):
   - “Anh/chị đang cung ứng vật tư cho bao nhiêu cơ sở y tế ạ?” [1] [2–5] [Trên 5]
   - “Danh mục vật tư đang dùng khoảng bao nhiêu mặt hàng ạ?” [Dưới 200] [200–1.000] [Trên 1.000]
   - “Giá trị vật tư sử dụng mỗi tháng khoảng bao nhiêu ạ?” [Dưới 200 triệu] [200 triệu–1 tỷ] [Trên 1 tỷ] [Chưa rõ]
2. Xin email và SĐT (như Bước 3a).
3. Báo giá theo quy mô:

> Dạ, với quy mô của đơn vị mình, chi phí dịch vụ SPD là {cach_tinh_va_muc_phi}. Phần mềm đặt hàng và quản lý được miễn phí khi anh/chị chọn Miyano làm nhà cung cấp duy nhất. Miyano đã gửi báo giá chi tiết tới email {email} ạ. Để thấy rõ cách vận hành, anh/chị có muốn xem demo 30 phút miễn phí không ạ?

Nút: [Đặt lịch demo] [Cần điều chỉnh] [Gặp nhân viên]

Cách tính phí SPD theo quy mô (ví dụ: % trên giá trị vật tư, hay phí cố định theo bậc): [anh Xuân cung cấp]. Khách chọn “Chưa rõ” hoặc quy mô vượt bậc cao nhất → chuyển nhân viên khảo sát và báo giá riêng.

#### 3.3.4 Quy tắc chung khi bot báo giá

- Bot chỉ dùng bảng giá đã duyệt (bảng giá bán vật tư trên ERP; bảng giá theo bậc của SupplyCore và SPD). Không tự tính giảm giá, không hứa chiết khấu, không dùng giá mua vào.
- Mọi báo giá đều ghi rõ hiệu lực, đã gồm hay chưa gồm VAT và phí vận chuyển.
- Mỗi lần bot báo giá, hệ thống tạo Báo giá (Quotation) trên ERP và gửi file PDF từ info@miyano.com.vn; nhân viên phụ trách nhận thông báo để theo dõi.
- Thiếu bảng giá cho trường hợp nào thì bot không đoán: chuyển nhân viên báo giá theo quy tắc thời gian mục 3.5.
- Thương lượng, chiết khấu, điều khoản thanh toán luôn do nhân viên trao đổi.

#### 3.3.5 Cần anh Xuân cung cấp để bot báo giá được

- [ ] Bảng giá bán vật tư bot được dùng (Price List nào trên ERP), cách ghi VAT, phí vận chuyển, số ngày hiệu lực báo giá.
- [ ] Ngưỡng giá trị đơn bot được tự báo giá (trên ngưỡng thì nhân viên báo).
- [ ] Tiêu chí quy mô và bảng giá theo bậc cho SupplyCore.
- [ ] Tiêu chí quy mô và cách tính phí SPD.
- [ ] Báo giá PDF: bot tự gửi ngay, hay nhân viên duyệt trước rồi mới gửi?

### 3.4 Khách không muốn để lại thông tin

| Trường hợp | Bot làm gì |
| --- | --- |
| Có email, không cho SĐT | Cảm ơn, không hỏi lại; nhân viên liên hệ qua email |
| Có SĐT, không cho email | Nhân viên gọi điện hoặc gửi qua Zalo theo SĐT |
| Không cho cả hai | “Dạ không sao ạ.” Gửi link trang dịch vụ trên website ngay trong khung chat; nhân viên có thể nhắn lại trong khung chat này |
| Email hoặc SĐT sai định dạng | Hỏi lại nhẹ nhàng 1 lần: “Miyano chưa ghi nhận được email này, anh/chị kiểm tra lại giúp Miyano ạ.” Sai lần 2 thì ghi nguyên văn và để nhân viên kiểm tra |

Lần đầu khách để lại email/SĐT, bot gửi kèm: “Thông tin của anh/chị được Miyano bảo mật theo chính sách bảo mật tại miyano.com.vn/chinh-sach-bao-mat.”

### 3.5 Giao việc cho nhân viên và hạn xử lý

| Khách nhắn lúc | Hạn nhân viên phải liên hệ | Bot hẹn với khách |
| --- | --- | --- |
| 08:00–16:30, ngày làm việc | 17:30 cùng ngày | “trong hôm nay” (tin xác nhận “Trong giờ”) |
| Sau 16:30, trước 08:00, thứ Bảy, Chủ nhật, ngày lễ | 17:30 ngày làm việc kế tiếp | {ngay_lam_viec_tiep_theo} (tin xác nhận “Ngoài giờ”) |

Mốc 16:30 là giờ chốt nhận việc trong ngày: khách nhắn lúc 17:00 thì bot hẹn sáng mai (hoặc ngày làm việc kế tiếp).

- Bot tạo việc (task) trên CRM gán cho người nhận của dịch vụ (bảng ở mục 1.4), kèm tóm tắt: khách là ai, cần gì, email, SĐT, file danh mục (nếu có), hạn xử lý.
- Nhân viên nhận thông báo ngay trên CRM và qua email.
- Đến 16:30 mà việc chưa xong → CRM nhắc nhân viên; quá hạn → báo người nhận thay và quản lý.
- Nhân viên gửi xong thì bấm “Đã gửi tài liệu” hoặc “Đã gửi báo giá” trên Lead. Thao tác này là mốc để bot bắt đầu Bước 4 (nhắn lại sau khoảng 1 ngày).

### 3.6 Cách build

- Kiểm tra định dạng: email có @ và tên miền; SĐT Việt Nam 10 số bắt đầu bằng 0 hoặc +84.
- Zalo OA: có thể dùng mẫu “yêu cầu thông tin người dùng” của Zalo để khách bấm chia sẻ tên và SĐT thay vì gõ (IT kiểm tra gói Tăng trưởng có hỗ trợ không).
- Messenger: có nút trả lời nhanh gợi ý email/SĐT Facebook đã lưu của khách; khách bấm là gửi luôn.
- Giữ ngữ cảnh: bot lưu “đang ở Bước 3a/3b, đã hỏi đến câu số mấy” trên Lead, để khách quay lại sau vẫn hỏi tiếp đúng chỗ.
- Thời gian: dùng chung hàm tính {ngay_lam_viec_tiep_theo} với Bước 1.

### 3.7 Ghi vào CRM ở bước này

| Trường trên Lead | Giá trị |
| --- | --- |
| Tài liệu cần / Nội dung cần báo giá | Theo nút hoặc câu khách gõ |
| Email, SĐT, Họ tên, Đơn vị | Theo khách cung cấp |
| Quy mô | Số cơ sở / số kho… (nếu khách trả lời) |
| Đồng ý xử lý dữ liệu | Có (đã gửi link chính sách bảo mật) |
| Trạng thái | Chờ gửi tài liệu / Chờ báo giá → Đã gửi tài liệu / Đã gửi báo giá |
| Việc (task) | Gán người nhận theo dịch vụ, hạn xử lý theo mục 3.5 |

### 3.8 Cần anh/chị chốt ở Bước 3

Đã chốt (12/11): sau 16:30 bot hẹn ngày làm việc kế tiếp (bảng mục 3.5).

Còn mở (hỏi anh Xuân):

- [ ] Danh sách tài liệu có sẵn cho từng dịch vụ (nút gợi ý ở mục 3.2).
- [ ] Câu hỏi quy mô cho báo giá Phần mềm.

## Bước 4 – Nhắn lại sau khoảng 1 ngày và mời demo

Sau khi nhân viên gửi tài liệu, bot nhắn lại khách khoảng 1 ngày sau để hỏi đã nhận chưa và mời demo. Bot ưu tiên nhắn qua chat; nếu đã quá thời hạn được nhắn chat thì gửi email kèm tin Zalo theo số điện thoại, vì nhiều khách ít xem email.

### 4.1 Khi nào bot nhắn lại

- Mốc bắt đầu: nhân viên bấm “Đã gửi tài liệu” trên Lead (Bước 3).
- Mốc mục tiêu: khoảng 1 ngày làm việc sau mốc bắt đầu, trong khung 09:00–16:30, bỏ qua thứ Bảy, Chủ nhật và ngày lễ.
- Không nhắn nếu: khách đã tự nhắn lại trước đó, đã đặt lịch demo, đã có nhân viên đang trò chuyện, hoặc Lead đánh dấu “Không liên hệ”.

### 4.2 Chọn kênh nhắn (quy tắc kết hợp)

Mỗi kênh chat chỉ cho bot chủ động nhắn trong một khoảng thời gian sau tin cuối của khách (gọi là hạn chat): Messenger là 24 giờ; Zalo OA theo gói Tăng trưởng (IT xác nhận số giờ). Bot chọn kênh theo thứ tự sau:

| Thứ tự | Điều kiện | Bot làm gì |
| --- | --- | --- |
| 1 | Tại mốc mục tiêu vẫn còn hạn chat | Nhắn qua chat đúng mốc mục tiêu |
| 2 | Tại mốc mục tiêu đã hết hạn chat, nhưng còn một khung trong giờ làm việc trước khi hết hạn và cách lúc gửi tài liệu ít nhất 3 giờ | Nhắn qua chat sớm hơn, vào khung muộn nhất còn trong hạn (trước hạn ít nhất 1 giờ) |
| 3 | Không còn khung chat phù hợp | Gửi email đúng mốc mục tiêu và gửi tin Zalo theo SĐT (tin ZNS có phí) nếu khách đã cho SĐT |

- Tin ZNS gửi theo số điện thoại nên dùng được cho cả khách đến từ Messenger, miễn là khách có dùng Zalo.
- Khách trả lời email/ZNS hoặc nhắn lại vào khung chat thì hạn chat được tính lại từ đầu, bot tiếp tục qua chat.
- Mỗi mốc chỉ gửi 1 lần theo mỗi cách, không gửi lặp cả chat lẫn email cùng lúc.

### 4.3 Kịch bản tin nhắn lại (qua chat)

Tin 1:

> Miyano chào anh/chị {ten} ạ. Hôm qua Miyano đã gửi {tai_lieu} tới email {email}. Anh/chị đã nhận được và xem qua chưa ạ?

Nút: [Đã xem rồi] [Chưa nhận được] [Chưa kịp xem]

(Nếu nhân viên gửi trước đó hơn 1 ngày, thay “Hôm qua” bằng “Vừa rồi”.)

Khách bấm [Đã xem rồi]:

- Phần mềm, SPD: “Miyano cảm ơn anh/chị ạ. Để thấy rõ hơn, anh/chị có muốn xem demo trực tiếp không ạ? Buổi demo miễn phí, khoảng 30 phút, dùng chính danh mục vật tư của đơn vị anh/chị.” Nút: [Đặt lịch demo] [Có câu hỏi thêm] [Để sau]
- Vật tư: “Miyano cảm ơn anh/chị ạ. Nếu anh/chị đang có danh mục cần mua hoặc cần cho gói thầu sắp tới, anh/chị gửi Miyano để báo giá nhé ạ.” Nút: [Gửi danh mục] [Có câu hỏi thêm] [Để sau]

Khách bấm [Chưa nhận được]:

> Dạ, anh/chị kiểm tra giúp Miyano cả hộp thư Spam ạ. Email của anh/chị là {email} đúng không ạ?

Nút: [Đúng] [Đổi email khác] → bot tạo việc cho nhân viên gửi lại (cùng quy tắc thời gian mục 3.5) và báo khách: “Miyano đã báo nhân viên gửi lại tài liệu {thoi_diem} ạ.”

Khách bấm [Chưa kịp xem]:

> Dạ không sao ạ. Nếu anh/chị bận, Miyano có thể hẹn một buổi demo 30 phút để chuyên viên giới thiệu trực tiếp, anh/chị đỡ mất thời gian đọc tài liệu.

Nút: [Đặt lịch demo] [Để sau] (Vật tư: [Gửi danh mục] [Để sau])

Khách bấm [Có câu hỏi thêm]: “Dạ, anh/chị cứ nhắn câu hỏi ạ.” → trả lời theo quy tắc mục 2.5.

Khách bấm [Để sau]: “Dạ vâng ạ. Khi nào cần, anh/chị cứ nhắn cho Miyano nhé.” → Lead chuyển trạng thái “Chăm sóc lại”; không nhắc tiếp trong đợt này.

Khách không trả lời: áp dụng luồng chung “nhắc sau 3 ngày, rồi dừng”.

### 4.4 Đặt lịch demo

Dùng chung cho mọi chỗ khách bấm [Đặt lịch demo] (Bước 2, 3 và 4).

1. Hình thức: “Anh/chị muốn xem demo theo hình thức nào ạ?” Nút: [Online qua Teams] [Gặp trực tiếp] [Miyano xác nhận phạm vi gặp trực tiếp]
2. Khung giờ: bot đề xuất 3 khung trống của người demo dịch vụ đó, trong 3 ngày làm việc tới. “Miyano đề xuất các khung giờ sau, anh/chị chọn giúp Miyano một khung phù hợp ạ:” Nút: [Thứ Ba 18/11, 9:00] [Thứ Ba 18/11, 14:00] [Thứ Tư 19/11, 10:00] [Giờ khác]
3. Khách chọn [Giờ khác]: “Anh/chị cho Miyano biết ngày giờ thuận tiện ạ.” Giờ đó trống thì chốt; bận hoặc rơi vào ngày nghỉ thì đề xuất khung trống gần nhất ngay sau đó.
4. Người tham dự (không bắt buộc): “Bên anh/chị dự kiến có bao nhiêu người tham dự ạ? Miyano hỏi để chuẩn bị nội dung cho phù hợp.”
5. Xác nhận: “Miyano xác nhận lịch demo {dich_vu}: {thu_ngay}, {gio_bat_dau}–{gio_ket_thuc}, hình thức {hinh_thuc}. Lời mời và đường link tham gia sẽ được gửi tới email {email}. Miyano sẽ nhắc anh/chị trước buổi demo ạ.”
6. Nhắc lịch: trước 1 ngày và trước 1 giờ, chọn kênh theo quy tắc mục 4.2.
7. Khách đổi hoặc huỷ lịch: đổi thì quay lại bước 2; huỷ thì hỏi nhẹ lý do bằng nút [Bận đột xuất] [Chưa có nhu cầu] [Lý do khác], cảm ơn và chuyển “Chăm sóc lại”.
8. Khách vắng mặt: sau 15 phút từ giờ hẹn, bot nhắn: “Miyano đang chờ anh/chị ở buổi demo lúc {gio}. Nếu anh/chị bận, Miyano có thể hẹn lại khung giờ khác ạ.” Nút: [Hẹn lại] [Để sau].

### 4.5 Email và tin Zalo (khi đã quá hạn chat)

Email:

> Tiêu đề: Miyano – Anh/chị đã nhận được tài liệu {dich_vu} chưa?
>
> Kính gửi anh/chị {ten},
>
> Hôm qua Miyano đã gửi anh/chị tài liệu về {dich_vu}. Nếu anh/chị muốn xem trực tiếp, Miyano mời anh/chị một buổi demo miễn phí khoảng 30 phút, dùng chính danh mục vật tư của đơn vị.
>
> Anh/chị chỉ cần trả lời email này hoặc nhắn cho Miyano qua {kenh_chat} để chọn giờ phù hợp.
>
> Trân trọng, Miyano Việt Nam

(Với Vật tư: thay đoạn mời demo bằng lời mời gửi danh mục để báo giá.)

Tin Zalo theo SĐT (ZNS) – bản nháp để đăng ký mẫu với Zalo:

> Miyano Việt Nam xin chào {ten}. Miyano đã gửi tài liệu {dich_vu} tới email của anh/chị. Anh/chị cần hỗ trợ thêm hoặc muốn đặt lịch demo, vui lòng bấm nút bên dưới để nhắn cho Miyano.

Nút: [Nhắn cho Miyano] (mở khung chat Zalo OA).

### 4.6 Sau khi nhân viên gửi báo giá (nhánh 3b)

Bot gửi báo giá xong (hoặc nhân viên bấm “Đã gửi báo giá”) → sau 2 ngày làm việc, nếu chưa thấy nhân viên cập nhật kết quả, bot nhắn (cùng quy tắc kênh mục 4.2):

> Miyano chào anh/chị {ten} ạ. Anh/chị đã nhận được báo giá Miyano gửi chưa ạ? Nếu cần trao đổi thêm, Miyano sẵn sàng hỗ trợ.

Nút: [Đã nhận, cần trao đổi] [Đặt lịch demo] (Phần mềm, SPD) [Chưa nhận được] [Để sau]. [Đã nhận, cần trao đổi] → chuyển nhân viên phụ trách.

### 4.7 Cách build

- Bộ hẹn giờ (scheduler) trên CRM, chạy mỗi 15 phút: tìm Lead có “Thời điểm nhắn lại” đã đến, kiểm tra điều kiện “không nhắn nếu” (mục 4.1) rồi chọn kênh theo mục 4.2.
- Tính hạn chat: lưu “Tin cuối của khách” theo từng kênh; hạn chat = thời điểm đó + 24 giờ (Messenger) hoặc + số giờ theo gói (Zalo OA).
- Email: gửi từ CRM bằng địa chỉ info@miyano.com.vn (Microsoft 365); email khách trả lời phải về đúng Lead trên CRM.
- ZNS: đăng ký mẫu tin với Zalo trước (cần Zalo duyệt), gọi API ZNS theo SĐT; tính phí theo tin.
- Lịch demo: đọc lịch trống của người demo trên CRM, tạo sự kiện kèm link Teams, gửi lời mời lịch tới email khách. Không xếp vào ngày lễ; trùng lịch thì dời sang khung trống kế tiếp.

### 4.8 Ghi vào CRM ở bước này

| Trường trên Lead | Giá trị |
| --- | --- |
| Thời điểm nhắn lại | Tính theo mục 4.1 |
| Kênh đã nhắn lại | Chat / Email / ZNS |
| Phản hồi của khách | Đã xem / Chưa nhận / Chưa kịp xem / Để sau / Không trả lời |
| Lịch demo | Ngày giờ, hình thức, người demo, số người tham dự |
| Trạng thái | Đã hẹn demo / Chăm sóc lại |

### 4.9 Cần anh/chị chốt ở Bước 4

Đã chốt (12/11):

- Dùng tin ZNS qua Zalo OA của Miyano khi quá hạn chat (cần đăng ký mẫu tin với Zalo; phí tính theo từng tin gửi).
- Lịch của người demo lấy từ CRM.
- Email nhắn lại gửi từ info@miyano.com.vn.

Còn mở (hỏi anh Xuân):

- [ ] Demo gặp trực tiếp áp dụng ở đâu (chỉ Hà Nội hay cả tỉnh khác)?
- [ ] Ai là người làm demo cho Phần mềm và cho SPD?

## Bước 5 – Sau demo: hỏi cảm nhận, chào bán và chốt

Khi nhân viên ghi trên CRM là đã demo xong, bot tự nhắn khách để cảm ơn, hỏi cảm nhận và mời bắt đầu sử dụng. Khách đồng ý thì bot cảm ơn và hẹn nhân viên gọi điện chốt; khách từ chối thì bot cảm ơn và hẹn dịp hợp tác sau. Bot không tự thương lượng giá hay điều khoản.

### 5.1 Khi nào bot nhắn

- Mốc bắt đầu: nhân viên chọn “Kết quả demo = Đã demo” trên Lead.
- Thời điểm gửi: khoảng 2 giờ sau khi nhân viên ghi nhận, nếu vẫn trước 16:30; nếu muộn hơn thì 09:00 ngày làm việc kế tiếp.
- Kênh: theo quy tắc kết hợp ở mục 4.2 (chat nếu còn hạn; hết hạn thì email từ info@miyano.com.vn kèm tin ZNS).
- Không nhắn nếu: nhân viên đã ghi khách đồng ý ngay trong buổi demo, hoặc nhân viên bấm “Không cần bot nhắn”.

### 5.2 Kịch bản

Tin 1 – cảm ơn và hỏi cảm nhận:

> Miyano cảm ơn anh/chị {ten} đã dành thời gian tham gia buổi demo {dich_vu} {hom_nay/hom_qua} ạ. Anh/chị thấy giải pháp có phù hợp với đơn vị mình không ạ?

Nút: [Rất phù hợp] [Cần tìm hiểu thêm] [Chưa phù hợp]

Khách bấm [Rất phù hợp]:

> Miyano rất vui khi nghe điều này ạ! Anh/chị có muốn Miyano chuẩn bị các bước để đơn vị bắt đầu sử dụng không ạ?

Nút: [Bắt đầu sử dụng] [Trao đổi thêm về chi phí]

Khách bấm [Bắt đầu sử dụng] (khách đồng ý mua):

> Miyano xin cảm ơn anh/chị đã tin tưởng lựa chọn Miyano ạ! Chuyên viên phụ trách sẽ gọi điện cho anh/chị {thoi_diem} để hướng dẫn các bước tiếp theo: ký hợp đồng, thanh toán và thiết lập hệ thống cho đơn vị.

- Chưa có SĐT thì bot xin SĐT trước khi gửi câu trên.
- {thoi_diem}: “trong hôm nay” nếu trước 16:30, còn lại là {ngay_lam_viec_tiep_theo} (như mục 3.5).
- CRM: trạng thái “Đồng ý mua – chờ ký hợp đồng”; tạo việc ưu tiên cao cho nhân viên phụ trách dịch vụ, báo luôn cho quản lý.

Khách bấm [Trao đổi thêm về chi phí]:

> Dạ, Miyano sẽ để chuyên viên phụ trách liên hệ và trao đổi chi tiết với anh/chị {thoi_diem} ạ.

→ tạo việc cho nhân viên, trạng thái “Thương lượng”. Bot không nêu con số.

Khách bấm [Cần tìm hiểu thêm]:

> Dạ, anh/chị còn băn khoăn ở điểm nào ạ?

Nút: [Chi phí] [Cách triển khai] [Kết nối hệ thống đang dùng] [Khác]

- [Cách triển khai]: bot trả lời từ kho kiến thức (3 bước bắt đầu sử dụng), rồi hỏi lại “Anh/chị có muốn Miyano chuẩn bị các bước để bắt đầu sử dụng không ạ?” kèm các nút như nhánh [Rất phù hợp].
- [Chi phí], [Kết nối hệ thống đang dùng], [Khác]: chuyển nhân viên phụ trách, kèm câu hỏi của khách.

Khách bấm [Chưa phù hợp] (khách từ chối):

> Miyano cảm ơn anh/chị đã chia sẻ ạ. Anh/chị cho Miyano biết lý do để Miyano cải thiện dịch vụ được không ạ?

Nút: [Chi phí] [Chưa có nhu cầu] [Đã có đơn vị khác] [Lý do khác]. Dù khách chọn gì hoặc không trả lời, bot kết thúc:

> Miyano cảm ơn anh/chị đã dành thời gian tìm hiểu. Hy vọng sẽ có dịp được hợp tác cùng anh/chị trong tương lai. Khi cần, anh/chị cứ nhắn cho Miyano nhé ạ.

→ trạng thái “Thua” kèm lý do. Bot không thuyết phục thêm.

Hỏi thăm lại sau 3 tháng: với Lead “Thua”, sau 3 tháng bot gửi 1 tin hỏi thăm qua email từ info@miyano.com.vn kèm tin ZNS (hạn chat đã hết từ lâu):

> Miyano Việt Nam xin chào anh/chị {ten}. Đã một thời gian kể từ buổi demo {dich_vu}, Miyano muốn hỏi thăm nhu cầu hiện tại của đơn vị anh/chị. Nếu cần thêm thông tin, anh/chị cứ nhắn cho Miyano ạ.

Khách không phản hồi thì dừng hẳn, không nhắc thêm. Khách trả lời thì quay lại Bước 2 với dịch vụ đó.

Khách không trả lời: áp dụng luồng chung “nhắc sau 3 ngày, rồi dừng”.

### 5.3 Riêng dịch vụ Cung ứng vật tư (không có demo)

Với Vật tư, bước chốt là sau khi khách đã nhận báo giá. Khi khách bấm “Đã nhận” ở mục 4.6 mà chưa có đơn, bot hỏi:

> Anh/chị có muốn đặt hàng theo báo giá Miyano đã gửi không ạ?

Nút: [Đặt hàng] [Cần điều chỉnh báo giá] [Để sau]

- [Đặt hàng]: cảm ơn và hẹn nhân viên gọi xác nhận đơn, giao hàng, hoá đơn (giống nhánh [Bắt đầu sử dụng]).
- [Cần điều chỉnh báo giá]: chuyển nhân viên, trạng thái “Thương lượng”.
- [Để sau]: cảm ơn, trạng thái “Chăm sóc lại”.

### 5.4 Cách build

- Trường nhân viên nhập sau demo: “Kết quả demo” (Đã demo / Khách vắng / Hoãn) và “Ghi chú demo”. Đổi sang “Đã demo” thì hệ thống đặt giờ gửi Tin 1 theo mục 5.1.
- Bot chỉ dùng tên khách, tên dịch vụ và ngày demo trong tin; không trích nội dung “Ghi chú demo” (ghi chú nội bộ).
- Các trạng thái mới trên Lead: Đã demo → Thương lượng / Đồng ý mua – chờ ký hợp đồng / Thua. Nhân viên ký xong hợp đồng thì chuyển Lead thành Khách hàng; bot ngừng mọi tin chào bán.
- Việc ưu tiên cao: khách đồng ý mua → thông báo ngay cho nhân viên và quản lý trên CRM và qua email.

### 5.5 Ghi vào CRM ở bước này

| Trường trên Lead | Giá trị |
| --- | --- |
| Cảm nhận sau demo | Rất phù hợp / Cần tìm hiểu thêm / Chưa phù hợp |
| Băn khoăn của khách | Chi phí / Cách triển khai / Kết nối hệ thống / Khác (kèm câu khách gõ) |
| Lý do từ chối | Chi phí / Chưa có nhu cầu / Đã có đơn vị khác / Lý do khác |
| Trạng thái | Thương lượng / Đồng ý mua – chờ ký hợp đồng / Thua / Chăm sóc lại |

### 5.6 Cần anh/chị chốt ở Bước 5

Đã chốt (12/11):

- Gửi tin cảm nhận khoảng 2 giờ sau demo, hoặc 9:00 ngày làm việc kế tiếp nếu đã quá 16:30.
- Khách từ chối: bot hỏi thăm lại 1 lần sau 3 tháng, rồi dừng.
- Bỏ nút “Cần trình lãnh đạo”.

## Luồng chung (áp dụng ở mọi bước)

Phần này là các quy tắc bot dùng xuyên suốt Bước 1–5: nhắc khi khách im lặng, gặp nhân viên, khách gõ tự do và khi nào bot phải dừng.

### C.1 Khách im lặng: nhắc 1 lần sau 3 ngày rồi dừng

- Áp dụng khi: bot đã hỏi hoặc đưa nút mà khách chưa trả lời, và Lead chưa ở trạng thái chốt (Đồng ý mua, Thua, Không liên hệ).
- Thời điểm: 3 ngày sau tin cuối của bot, trong khung 09:00–16:30; rơi vào ngày nghỉ thì dời sang 09:00 ngày làm việc kế tiếp.
- Kênh: theo quy tắc mục 4.2. Sau 3 ngày thường đã hết hạn chat nên phần lớn sẽ gửi email kèm tin ZNS.
- Chỉ nhắc 1 lần. Khách vẫn im lặng thì bot dừng, Lead chuyển “Chăm sóc lại” để nhân viên tự quyết định. Khách nhắn lại bất cứ lúc nào thì bot tiếp tục đúng chỗ đang dở.

| Khách đang dở ở | Nội dung tin nhắc |
| --- | --- |
| Bước 1–2 (mới chọn dịch vụ) | “Miyano chào anh/chị ạ. Hôm trước anh/chị có quan tâm đến {dich_vu}. Anh/chị cần Miyano gửi tài liệu hay tư vấn thêm gì không ạ?” |
| Bước 3 (đang hỏi dở email/SĐT) | “Miyano chào anh/chị ạ. Để nhân viên gửi {tai_lieu/bao_gia}, anh/chị cho Miyano xin {thong_tin_con_thieu} nhé ạ.” |
| Bước 4 (đã gửi tài liệu/báo giá, chưa phản hồi) | “Miyano chào anh/chị ạ. Anh/chị đã có thời gian xem {tai_lieu/bao_gia} chưa ạ? Nếu tiện, Miyano mời anh/chị một buổi demo 30 phút miễn phí.” (Vật tư: mời gửi danh mục) |
| Bước 5 (sau demo, chưa trả lời cảm nhận) | “Miyano chào anh/chị ạ. Sau buổi demo {dich_vu}, anh/chị có cần Miyano hỗ trợ thêm thông tin gì không ạ?” |

### C.2 Gặp nhân viên ở mọi bước

- Khách luôn gặp được nhân viên qua: nút “Gặp nhân viên” trong các tin có nút, menu cố định (Messenger) / menu OA (Zalo), hoặc gõ “gặp nhân viên”, “gọi cho tôi”.
- Xử lý theo mục 1.4: trong giờ thì giao ngay cho người nhận của dịch vụ; ngoài giờ thì hẹn nhắn tin hoặc gọi điện vào ngày làm việc kế tiếp.
- Bot tự chuyển nhân viên (không chờ khách yêu cầu) khi: khách phàn nàn hoặc bực bội; bot không hiểu khách 3 lần liên tiếp; câu hỏi không có trong kho kiến thức; khách đồng ý mua.
- Khi chuyển, bot gửi nhân viên tóm tắt 5 dòng: khách là ai, quan tâm dịch vụ gì, đã trao đổi gì, cần làm gì, hạn xử lý.

### C.3 Khách gõ tự do

| Khách gõ | Bot làm gì |
| --- | --- |
| Câu hỏi có trong kho kiến thức | Trả lời 2–4 câu, rồi hỏi tiếp kèm nút của bước đang đứng |
| Câu hỏi không có trong kho kiến thức, hỏi chiết khấu, công nợ, điều khoản | “Câu này Miyano xin phép để chuyên viên trả lời chính xác cho anh/chị ạ.” → chuyển nhân viên; ghi câu hỏi để bổ sung kho kiến thức |
| Nhiều ý trong một tin | Trả lời ngắn từng ý theo thứ tự khách hỏi, rồi đưa 1 bước tiếp theo |
| Viết tắt, sai chính tả (“ib”, “bg”, “tv”, “ntn”) | Hiểu các viết tắt phổ biến; không hiểu thì hỏi lại bằng nút |
| Tin ghi âm, sticker, ảnh không rõ nội dung | Mời khách nhắn chữ hoặc chọn nút; ảnh/file danh mục thì lưu vào Lead |
| Tiếng Anh / tiếng Nhật | Trả lời cùng ngôn ngữ, nội dung như bản tiếng Việt |
| Hỏi tư vấn y khoa (thuốc, bệnh, cách dùng cho bệnh nhân) | “Miyano không tư vấn chuyên môn y khoa, anh/chị vui lòng hỏi bác sĩ hoặc cơ sở y tế ạ.” rồi đưa lại menu |
| Không liên quan, trêu đùa, đòi bot đổi vai | Trả lời lịch sự 1 câu, đưa lại menu |
| Quảng cáo, lừa đảo, link lạ | Không trả lời nội dung, không bấm link, đánh dấu Spam, không tạo Lead |
| “Bạn là bot à?” | “Dạ, đây là trợ lý AI của Miyano Việt Nam. Anh/chị muốn trao đổi với nhân viên thì Miyano kết nối ngay ạ.” + nút [Gặp nhân viên] |

### C.4 Khi nào bot phải dừng hẳn

- Khách nói “không cần”, “đừng nhắn nữa”, “hủy đăng ký” → bot trả lời “Dạ, Miyano đã ghi nhận. Cảm ơn anh/chị ạ.”, đánh dấu “Không liên hệ” và dừng mọi tin chủ động (chat, email, ZNS).
- Khách yêu cầu xoá dữ liệu → gửi link hướng dẫn xoá dữ liệu trên website và chuyển người phụ trách tuân thủ.
- Nhân viên đang trò chuyện với khách → bot im lặng cho đến khi nhân viên bấm “Trả lại cho bot”.
- Lead đã thành Khách hàng (đã ký hợp đồng) → bot ngừng mọi tin chào bán.

### C.5 Giờ gửi tin chủ động

Mọi tin bot tự gửi (nhắn lại, nhắc lịch, nhắc im lặng, hỏi thăm) chỉ gửi trong 09:00–16:30, thứ Hai – thứ Sáu, không gửi ngày lễ. Riêng tin nhắc demo trước 1 giờ được gửi đúng giờ. Khi khách tự nhắn thì bot trả lời ngay, kể cả ngoài giờ.
