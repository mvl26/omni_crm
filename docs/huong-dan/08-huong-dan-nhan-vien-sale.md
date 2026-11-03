# 08 · Hướng dẫn cho nhân viên sale

Dành cho: nhân viên sale và trưởng sale dùng **Hộp thư Omni** và Frappe CRM hằng ngày. Không cần biết lập trình.

> **Lưu ý về độ tin cậy của tài liệu này.** Các bước và tên nút dưới đây được lấy từ mã giao diện của Hộp thư (`omni_crm/omni_crm/page/omni_inbox/omni_inbox.js`), nên tên nút, nhãn và thông báo là chính xác theo mã. Nhưng **chưa ai bấm thử trên giao diện thật** (không có trình duyệt, không có sale thật khi nghiệm thu). Mỗi bước vì vậy nên coi là "chưa kiểm trên giao diện". Nếu thấy khác mô tả, hãy báo quản trị. Tài liệu chỉ mô tả bằng chữ, không có ảnh chụp.

Mục lục: [1. Mở Hộp thư](#1-mở-hộp-thư-omni) · [2. Danh sách hội thoại](#2-danh-sách-hội-thoại-bên-trái) · [3. Đọc một hội thoại](#3-đọc-một-hội-thoại-bên-phải) · [4. Trả lời Zalo](#4-trả-lời-khách-zalo) · [5. Email và điện thoại](#5-email-và-điện-thoại) · [6. Nháp của bot](#6-nháp-của-bot-khi-được-bật) · [7. Tạm dừng/tiếp tục bot](#7-tạm-dừng-và-cho-bot-tiếp-tục) · [8. Gọi lại khách gọi nhỡ](#8-gọi-lại-khách-gọi-nhỡ) · [9. Điểm Lead](#9-xem-điểm-lead) · [9A. Lead quảng cáo, nhà thầu, nhật ký](#9a-lead-quảng-cáo-nhà-thầu-và-nhật-ký-chăm-sóc) · [10. Hai Lead là một người](#10-hai-lead-cùng-một-người) · [11. Việc hằng ngày](#11-gợi-ý-nhịp-làm-việc-hằng-ngày) · [12. Sự cố thường gặp](#12-sự-cố-thường-gặp) · [13. Đã xong khi](#13-đã-xong-khi)

## 1. Mở Hộp thư Omni

Vào địa chỉ `https://<tên-miền-CRM>/app/omni-inbox`. Trang có tên **Hộp thư Omni** nhưng **chưa được đặt vào menu hay Workspace nào** (ghi nhận ở `docs/12` mục D), nên hãy lưu địa chỉ này vào dấu trang (bookmark) hoặc nhờ quản trị thêm lối tắt. Mỗi Lead do Omni tạo còn có một Comment chứa liên kết mở Hộp thư đúng khách đó (đường dẫn `/app/omni-inbox?lead=<mã Lead>`); trang Lead của CRM không hiện nút Hộp thư riêng. Bạn cần vai trò Sales User, Sales Manager, Omni Admin hoặc System Manager. Nếu có lời nhắn "Không có quyền xem hoặc không tìm thấy hội thoại", hãy hỏi quản trị.

Từ thông báo (chuông) "Có nháp bot chờ duyệt", "Bot chuyển người, mời xem" hoặc "Lead nóng", bấm vào cũng mở thẳng Hộp thư/hội thoại đúng khách.

Đầu trang có thể hiện khối **"Tình trạng hệ thống"** (chỉ hiện khi có sự cố), ví dụ "Zalo đang mất kết nối, tin đến vẫn được lưu" hoặc "Hệ thống xử lý tin đang chậm, tin vẫn được lưu". Nghĩa là: tin khách vẫn không mất; việc trả lời có thể tạm chậm. Hãy báo quản trị nếu kéo dài.

## 2. Danh sách hội thoại (bên trái)

Mỗi dòng là một khách (một Lead), có:

- **Tên khách**, kèm **tên đơn vị** (nhà thầu) nếu có, và thời gian tin gần nhất.
- Chấm màu nhỏ nghĩa là **chưa trả lời** (khách nhắn sau cùng, chưa ai trả lời).
- Nhãn **Nháp** nghĩa là có nháp của bot đang chờ bạn duyệt (mục 6).
- Đoạn tin cuối. Nếu tin cuối là của bạn, đầu dòng có chữ "Bạn:".
- Một **nhãn ưu tiên màu đỏ** (nếu có), theo thứ tự từ gấp nhất: *Khiếu nại*, *Gọi nhỡ chưa gọi lại*, *Quá hạn phản hồi* (chờ lâu hơn ngưỡng trong giờ làm việc), *Lead nóng có tin chờ*, *Có nháp bot chờ duyệt*, *Đang chờ*. Dòng nào gấp nhất nằm trên.
- Nhãn đỏ **Chưa ghi kết quả** khi bạn đã trả lời khách nhưng chưa ghi kết quả chăm sóc (mục 9A).
- Các nhãn kênh (Zalo OA, Email, Điện thoại) và tên người phụ trách Lead.

**Bộ lọc** ở trên cùng: ô **Tìm theo tên, SĐT, email, đơn vị**; danh sách chọn **Mọi kênh** (Zalo OA, Email, Điện thoại); bốn ô tích **Chưa trả lời**, **Của tôi**, **Cuộc gọi** (chỉ hội thoại điện thoại), **Có nháp bot**. Danh sách tự cập nhật khi có tin mới (không cần tải lại trang); nếu mất kết nối thì cập nhật mỗi phút.

Bạn chỉ thấy hội thoại của những Lead mà CRM cho phép bạn xem.

## 3. Đọc một hội thoại (bên phải)

Bấm vào một dòng ở bên trái. Bên phải hiện:

- **Đầu hội thoại**: tên khách, tên đơn vị kèm nhãn **Hạng A/B/C** nếu khách là nhà thầu đã có điểm phù hợp (mục 9A), SĐT, email, trạng thái Lead, các nhãn kênh của khách, bước tiếp theo đã hẹn (nếu có) và (nếu có) nhãn **Điểm N** (mục 9). Nếu khách đã **từ chối nhận liên hệ** thì SĐT/email được thay bằng dòng "Từ chối nhận liên hệ" và trang Chi tiết khách không hiện nút gọi hay nhắn: **tôn trọng, đừng tìm đường liên hệ khác**. Hai nút: **Mở hồ sơ chăm sóc** (trang Chi tiết khách, mục 9A) và **Mở Lead trong CRM** (hoặc **Mở Deal trong CRM** nếu Lead đã chuyển thành Deal, mở ở tab mới).
- **Tóm tắt do bot** (nếu bot phân tích đã bật): nhãn ý định và vài câu tóm tắt, kèm lưu ý "Tóm tắt do bot, có thể chưa chính xác". Đây là gợi ý, hãy kiểm tra lại với khách.
- **Các tin**, mới nhất ở cuối. Mỗi tin ghi kênh · người gửi · thời gian. Tin ra của bạn có trạng thái: **Đang gửi**, **Đã gửi**, **Lỗi**, **Bị chặn** (kèm lý do màu đỏ bên dưới). Chữ **thử nghiệm** nghĩa là hệ thống đang ở chế độ thử, tin **không** tới khách thật. Nhãn **Bot đã gửi** nghĩa là tin do bot tự gửi (hiếm, chỉ khi quản trị bật; mục 6). Tệp đính kèm hiện thành liên kết.
- Nếu hội thoại quá dài: "Chỉ hiện các tin gần nhất." Xem đầy đủ trong CRM.

## 4. Trả lời khách Zalo

1. Mở hội thoại. Ở dưới cùng có khung **Nhập tin trả lời Zalo**.
2. Gõ nội dung (tối đa 6.000 ký tự trong khung; Zalo có thể tách tin dài thành nhiều tin).
3. Bấm **Gửi qua Zalo**. Nút bị khóa tạm khi đang gửi để tránh gửi hai lần. Khung trống thì hiện nhắc "Nội dung tin nhắn đang trống".
4. Tin xuất hiện trong hội thoại với trạng thái "Đang gửi" rồi "Đã gửi". Nếu "Lỗi" hoặc "Bị chặn", đọc dòng lý do màu đỏ.

**Quy tắc khung thời gian của Zalo** (tính từ tin cuối của khách):

- Trong **48 giờ**: gửi bình thường.
- Từ 48 giờ đến **7 ngày**: Hộp thư hiện cảnh báo màu cam "Tin này có thể bị tính phí ..." và khi bấm Gửi sẽ hỏi lại "... Bạn vẫn muốn gửi?". Chỉ xác nhận khi thật cần; nếu có thể hãy gọi điện hoặc gửi email.
- Quá **7 ngày**: khung nhập bị khóa, hiện lý do "Đã quá 7 ngày kể từ tin cuối của khách, Zalo không cho gửi tin tư vấn; hãy liên hệ qua kênh khác."
- Khách chưa từng nhắn OA: không gửi được; dùng kênh khác.
- Dòng **Chế độ thử: không gửi thật** dưới khung nghĩa là hệ thống đang ở sandbox (tin chỉ ghi lại, không tới khách).
- Dưới khung còn có dòng **"Đã dùng X / Y tin ngoài 48 giờ tháng này"** (hoặc "Đã gửi X tin ngoài 48 giờ tháng này (chưa biết hạn mức)" khi quản trị chưa nhập hạn mức của gói OA). Đó là số tin có thể bị tính phí mà **đội sale** đã gửi qua hệ thống trong tháng. Chữ chuyển đỏ khi vượt hạn mức, nhưng hệ thống **không chặn** bạn gửi. Số này có thể thấp hơn thực tế vì tin trả lời thẳng trên ứng dụng Zalo OA không được đếm.

**Xin số điện thoại**: nếu Lead chưa có SĐT, có nút **Xin SĐT qua Zalo**. Bấm thì khách nhận một yêu cầu chia sẻ SĐT; khách đồng ý thì số được điền vào Lead. Mỗi khách chỉ xin được **một lần mỗi 7 ngày**; nếu đã có SĐT hoặc vừa xin thì hệ thống báo và không gửi. Có SĐT giúp hệ thống nhận ra cùng một khách trên nhiều kênh (tránh Lead trùng).

Nếu bạn trả lời khách trực tiếp trong ứng dụng Zalo OA (không qua CRM), tin đó vẫn được ghi vào dòng thời gian như tin của sale.

## 5. Email và điện thoại

- **Email**: Hộp thư cho bạn **đọc** thư của khách. Để trả lời, bấm **Trả lời email trong CRM** (mở Lead trong CRM) rồi dùng trình soạn thư của CRM trên Lead. Hộp thư không có khung soạn thư cho email.
- **Điện thoại**: cuộc gọi hiện trong hội thoại (để biết ai gọi, kết quả). Không có khung trả lời. Chi tiết cuộc gọi nằm trong CRM (nhật ký cuộc gọi của Lead).

## 6. Nháp của bot (khi được bật)

Chỉ có khi quản trị đặt chế độ **Draft**. Quy tắc quan trọng: **bot không bao giờ tự gửi ở chế độ này; tin đi luôn là tin của bạn**, và bạn chịu trách nhiệm nội dung bạn bấm gửi. Hướng dẫn ngắn có ở [duyet-nhap-bot.md](duyet-nhap-bot.md); phần dưới đây mô tả đầy đủ hơn.

1. Khi có nháp, bạn nhận thông báo **"Có nháp bot chờ duyệt: <mã Lead>"** (chuông), và hội thoại có nhãn **Nháp** ở danh sách. Thông báo chỉ có tên Lead và liên kết, không có nội dung tin.
2. Trong hội thoại, ngay dưới tin khách cuối có thẻ **"Nháp của bot — chưa gửi"** (kèm tên nhóm câu hỏi). Dưới nháp có dòng **Nguồn kho tri thức** với các mã mục (ví dụ KB-00012). **Bấm mã** để đọc câu trả lời chuẩn bot đã dựa vào; hãy đối chiếu trước khi gửi.
3. Ba lựa chọn:
   - **Gửi**: gửi nguyên văn.
   - **Sửa rồi gửi**: hiện khung sửa; sửa xong bấm **Gửi bản đã sửa** (hoặc **Hủy sửa** để quay lại). Không gửi được nếu khung trống.
   - **Bỏ**: không gửi gì; bạn tự trả lời.
   Khung có phí của Zalo (quá 48 giờ) vẫn hỏi xác nhận như khi bạn tự trả lời. Nháp **của email** chỉ để đọc hoặc Bỏ, không gửi từ đây ("Kênh này trả lời trong CRM, không gửi từ Hộp thư").
4. Sau khi Gửi hoặc Bỏ, hiện một hàng hỏi nhanh **"Nháp này: Đúng / Sai / Thiếu"** và **Bỏ qua**. Chấm không bắt buộc nhưng rất giúp quản lý cải thiện bot. Chấm **Sai** khi nháp sai về giá, cam kết hoặc y khoa. (Giao diện hiện chưa có ô ghi chú hay ô "nghiêm trọng"; nếu nháp sai nghiêm trọng, hãy báo trưởng sale/quản trị bằng cách khác.)
5. Thẻ **"Bot đề nghị chuyển người"** (kèm lý do): nghĩa là bot không tự soạn được câu trả lời (ví dụ khách hỏi giá, khiếu nại, đòi gặp người, hoặc ngoài kho tri thức). **Không có nút Gửi**; chỉ có **Bỏ**. Hãy trả lời khách như thường. Bạn sẽ nhận thông báo **"Bot chuyển người, mời xem: <mã Lead>"**; nếu cấu hình có email và bạn không mở thông báo trong 10 phút, bạn nhận thêm một thư nhắc.
6. Khách nhắn thêm trong lúc nháp đang chờ, hoặc sau 24 giờ không ai duyệt: nháp cũ **tự hết hạn** biến mất; nếu cần sẽ có nháp mới. Hãy duyệt sớm.
7. Nháp **có thể sai** dù đã qua chốt chặn. Luôn đọc kỹ giá, thời hạn, cam kết, SĐT, đường dẫn.

**Tin do bot tự gửi (hiếm, chỉ khi quản trị bật cho một nhóm câu hỏi):** tin có nhãn **Bot đã gửi** và tên nhóm; bạn có thể chấm **Đúng/Sai/Thiếu** ngay dưới tin. Nếu thấy bot nói sai, bấm **Sai**: nhóm đó **tự tắt** tự động gửi và báo cho quản lý; sau đó hãy gửi tin đính chính cho khách bằng tay. Nút **Tắt tự động nhóm này** chỉ dành cho quản trị.

Tin bot gửi lỗi: bạn nhận thông báo "Tin bot tự gửi bị lỗi, hãy kiểm tra trên Zalo: <mã Lead>". Khách có thể đã nhận tin; hãy kiểm tra trên Zalo trước khi gửi lại.

## 7. Tạm dừng và cho bot tiếp tục

Chỉ hiện khi bot đang bật (chế độ Shadow/Draft) và bạn có quyền sửa Lead đó.

- Khi bot chuyển người, nó **im** với khách đó (mặc định 24 giờ) để không soạn nháp chồng lên cuộc trao đổi của bạn. Đầu hội thoại hiện **"Bot đang tạm dừng đến <thời điểm>"** kèm nút **Cho bot tiếp tục**. Bấm nếu muốn bot soạn nháp cho các tin tiếp theo.
- Nếu bạn muốn bot ngừng soạn nháp cho một khách (ví dụ khách nhạy cảm): bấm **Tạm dừng bot với khách này**.
- Ai bấm, lúc nào, được ghi vào dòng thời gian của Lead.

## 8. Gọi lại khách gọi nhỡ

Việc gọi lại **không nằm ở Hộp thư** mà là một **việc (Task) trong CRM**:

1. Khi khách đã có Lead gọi tới mà không ai nghe máy, hệ thống tạo việc **"Gọi lại khách <tên/SĐT>"** (ưu tiên cao) giao cho người phụ trách Lead (hoặc người trực dự phòng).
2. Mở việc từ danh sách việc/tab Việc của Lead trong CRM (nơi cụ thể trên giao diện CRM của bạn: chưa kiểm) và xem **hạn**. Hạn tính trong giờ làm việc: gọi nhỡ ngoài giờ thì hạn là sáng ngày làm việc kế tiếp.
3. Gọi lại khách. Cuộc gọi được nghe máy → việc **tự đóng**. Khách gọi nhỡ nhiều lần khi việc còn mở: chỉ một việc, mô tả ghi "Khách đã gọi nhỡ N lần, lần cuối ...".
4. Việc quá hạn được quản trị theo dõi và nhắc. Nếu được giao một việc mà bạn không phải người phụ trách, báo trưởng sale.
5. Khách **ẩn số** gọi nhỡ: không có việc gọi lại (không biết số để gọi); cuộc gọi chỉ có trong nhật ký.

## 9. Xem điểm Lead

Điểm Lead do **luật cố định** của trưởng sale (không phải AI "nghĩ" ra) cộng từ những gì đã biết về khách. Với nhà thầu, các tín hiệu là: có SĐT, có email, đã gắn được nhà thầu, nhà thầu phản hồi lần đầu, đã trao đổi nhu cầu, hỏi cách vận hành hoặc gói dịch vụ, hỏi phí, yêu cầu đề xuất, người quyết định tham gia, nêu vướng mắc, liên hệ nhiều lần trong 30 ngày, khiếu nại (trừ điểm). Bảng điểm cụ thể do trưởng sale duyệt, bạn xem lý do cộng ngay trên nhãn.

- Trong Hộp thư: nhãn **Điểm N** ở đầu hội thoại. **Rê chuột** vào nhãn để xem lý do cộng điểm. Nhãn nổi bật khi Lead là **Lead nóng**.
- Với nhà thầu **đã có hạng phù hợp**, **Lead nóng** cần **cả hai**: điểm quan tâm đạt ngưỡng **và** hạng A hoặc B (mục 9A). Nhà thầu hạng C không bị gọi là "nóng" dù hỏi nhiều. Lead chưa gắn nhà thầu hoặc chưa có hạng thì chỉ xét điểm như trước.
- Khi Lead lần đầu vượt ngưỡng "Lead nóng", người phụ trách nhận thông báo "Lead nóng: <tên>, điểm <n>" kèm lý do. Chỉ báo một lần mỗi Lead.
- Điểm chỉ hiện khi quản trị đã bật chấm điểm. Điểm là tham khảo để ưu tiên, **không thay phán đoán của bạn**.
- Trên trang Lead của CRM, điểm (trường "Điểm Lead") và "Tóm tắt của bot" chỉ hiện nếu quản trị đã cấu hình bố cục trường của CRM; việc này **chưa làm** nên hiện tại hãy xem điểm trong Hộp thư hoặc trang Chi tiết khách. Bạn **không sửa tay** được điểm.

## 9A. Lead quảng cáo, nhà thầu và nhật ký chăm sóc

*Chưa kiểm trên giao diện:* mô tả dưới đây lấy từ mã nguồn, chưa có ai thao tác trên trình duyệt thật.

**Lead từ quảng cáo hoặc website.** Khi khách điền biểu mẫu Meta, Google hoặc website, hệ thống tạo Lead (hoặc gắn vào Lead đã có), giao cho một người và gửi thông báo "Lead mới từ quảng cáo: <tên>". Hệ thống **không gửi gì cho người điền form**: bạn là người liên hệ đầu tiên. Đồng hồ phản hồi chạy từ lúc Lead vào, chỉ đếm giờ làm việc; quá ngưỡng (mặc định 15 phút) bạn bị nhắc, quá gấp đôi thì trưởng nhóm được báo. Phản hồi đầu tiên được ghi khi bạn nhắn, gọi được nghe máy hoặc ghi nhật ký chăm sóc đầu tiên. Lead thử nghiệm (tiêu đề dòng thời gian có chữ "[Thử nghiệm]") không giao ai và không tính vào báo cáo. Chi tiết: [12](12-lead-quang-cao.md) và [13](13-dashboard-va-cham-soc.md).

**Nhà thầu.** Khách là công ty đã trúng gói thầu; trang **Chi tiết khách** (nút **Mở hồ sơ chăm sóc** ở đầu hội thoại) cho thấy tên công ty và mã số thuế, hạng phù hợp A/B/C, các gói thầu đã trúng và người liên hệ. Hạng C nghĩa là chưa đạt tiêu chí về số bệnh viện và số mã hàng; không có nghĩa là bỏ qua, nhưng ưu tiên gọi chủ động thấp hơn. Xem [13](13-dashboard-va-cham-soc.md).

**Nhật ký chăm sóc.** Sau mỗi lần trao đổi, hãy ghi kết quả và bước tiếp theo trong trang Chi tiết khách (tab **Nhật ký chăm sóc**, nút **Ghi nhật ký chăm sóc**). Hộp thư **không** có form ghi nhật ký; nó chỉ nhắc "Chưa ghi kết quả" và dẫn bạn sang trang Chi tiết khách. Hướng dẫn từng bước ở [13](13-dashboard-va-cham-soc.md) mục 4.

## 10. Hai Lead cùng một người

Hệ thống tự nhận ra khách qua SĐT/email/định danh kênh, nhưng khi không chắc nó tạo **nghi trùng**. Bạn xem ở `/app/omni-duplicate-candidate` (quyền đọc). Nếu thấy hai Lead là một người, báo trưởng sale/quản trị gộp (hoặc tự gộp nếu bạn được cấp quyền sửa cả hai Lead): xem [06](06-chong-trung-va-lead.md). Nếu thấy hai Lead trùng mà hệ thống **không** báo, hãy ghi lại và báo: đó là số liệu quan trọng.

## 11. Gợi ý nhịp làm việc hằng ngày

| Lúc | Việc |
| --- | --- |
| Đầu ngày | Mở Hộp thư, tích **Chưa trả lời** và **Của tôi**. Trả lời khách tồn từ hôm qua. Mở danh sách việc gọi lại quá hạn. |
| Trong ngày | Khi có thông báo (chuông), mở hội thoại, duyệt nháp bot (nếu có), trả lời. Khi khách đã có SĐT, đừng xin lại. |
| Zalo ngoài 48 giờ | Cân nhắc kênh khác trước khi xác nhận tin có phí. |
| Cuối ngày | Duyệt nốt các nháp còn chờ (đừng để hết hạn); chấm Đúng/Sai/Thiếu nếu còn nhớ; báo lỗi gặp trong ngày. |

## 12. Sự cố thường gặp

| Triệu chứng | Có thể do | Bạn làm gì |
| --- | --- | --- |
| Khách nhắn nhưng chưa thấy trong Hộp thư | Hệ thống đang xử lý (tới ~1-2 phút), hoặc chậm do tải | Chờ; nếu quá vài phút hoặc có khối "Tình trạng hệ thống", báo quản trị. Tin không bị mất |
| Tin bạn gửi báo "Lỗi" hoặc "Bị chặn" | Quá khung 7 ngày, kênh tắt chiều ra, token Zalo hết hạn, sandbox | Đọc lý do màu đỏ; báo quản trị nếu là lỗi kênh. Tin "Bị chặn" **không** tự gửi lại: gửi lại bằng tay nếu cần |
| Tin hiện "thử nghiệm" | Hệ thống đang ở chế độ thử | Tin **không** tới khách. Báo quản trị nếu đây là môi trường thật |
| Không có khung nhập tin | Hội thoại là email/điện thoại, hoặc bạn không có quyền sửa Lead, hoặc đã quá 7 ngày | Email: dùng CRM. Quyền: hỏi quản trị |
| Nháp biến mất | Khách vừa nhắn thêm hoặc quá 24 giờ | Chờ nháp mới hoặc trả lời tay |
| Không thấy nút Cho bot tiếp tục / Tạm dừng | Bot đang tắt, hoặc bạn không có quyền sửa Lead | Bình thường nếu bot tắt |
| Bấm Gửi nháp bị từ chối kèm thông báo | Khách vừa nhắn thêm trong lúc bạn xem, hoặc nháp đã hết hạn, hoặc quản trị vừa đổi chế độ bot | Mở lại hội thoại để thấy nháp mới (nếu có), hoặc trả lời tay |
| Khách hỏi giá mà bot "chuyển người" | Đúng thiết kế: bot **không bao giờ** báo giá | Trả lời theo bảng giá chính thức |
| Việc gọi lại không thấy | Khách ẩn số, hoặc chưa có Lead | Báo trưởng sale; xem nhật ký cuộc gọi |
| Hộp thư ghi "Từ chối nhận liên hệ", không thấy SĐT/email | Khách (hoặc nhà thầu) đã từ chối nhận liên hệ, hoặc đã bị xóa dữ liệu theo yêu cầu | Đúng thiết kế: không liên hệ chủ động. Nếu khách tự nhắn lại, hỏi trưởng sale |
| Lead quảng cáo mới nhưng bị nhắc "Quá hạn phản hồi" | Chưa có tin ra, cuộc gọi nghe máy hay nhật ký chăm sóc nào kể từ lúc Lead vào | Liên hệ khách, rồi ghi nhật ký ở trang Chi tiết khách để đồng hồ dừng |

## 13. Đã xong khi

Với mỗi nhân viên sale mới, người hướng dẫn kiểm cùng họ trên môi trường thử (staging, sandbox):

- [ ] Mở được Hộp thư; biết dùng bộ lọc **Chưa trả lời** / **Của tôi** / kênh.
- [ ] Đọc được một hội thoại Zalo, hiểu các nhãn trạng thái (Đã gửi/Lỗi/Bị chặn/thử nghiệm).
- [ ] Gửi được một tin Zalo thử và hiểu cảnh báo tin có phí.
- [ ] Biết trả lời email qua nút **Trả lời email trong CRM**.
- [ ] (Nếu bot Draft bật) Thử đủ 3 hành động Gửi / Sửa rồi gửi / Bỏ và chấm Đúng/Sai/Thiếu; hiểu thẻ "Bot đề nghị chuyển người".
- [ ] Biết bấm **Tạm dừng bot với khách này** / **Cho bot tiếp tục**.
- [ ] Biết tìm và xử lý việc "Gọi lại khách ..." trong CRM.
- [ ] Biết xem Điểm N và rê chuột để đọc lý do; hiểu "Lead nóng" của nhà thầu cần hạng A hoặc B.
- [ ] Biết mở **Mở hồ sơ chăm sóc**, đọc hạng và gói thầu của nhà thầu, và ghi một nhật ký chăm sóc (kết quả + bước tiếp theo + hạn).
- [ ] Biết khi nào bị nhắc "Quá hạn phản hồi" và hiểu hệ thống không tự gửi gì cho khách điền form.
- [ ] Biết báo hai Lead trùng cho trưởng sale.
