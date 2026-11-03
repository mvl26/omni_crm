# 11 · Nhà thầu và nhập danh sách

Dành cho: quản trị hệ thống (vai trò **Omni Admin**) làm việc nhập; trưởng sale đọc mục 1, 2, 6 và 7 để hiểu hạng A/B/C. Nguồn kỹ thuật: `docs/14-nha-thau-b2b.md`, `docs/11-van-hanh.md` mục "Lưu ý V2-03", `docs/adr/0012-...`. Bạn không cần đọc các tệp đó để làm theo hướng dẫn này.

> **Hiện trạng trung thực.** Việc nhập danh sách đã được kiểm bằng test tự động và dữ liệu tổng hợp trên site thử. **Chưa có tệp danh sách thật nào được nhập**, chưa chạy trên staging, và giao diện form nhập chưa ai mở bằng trình duyệt. Mọi tên nút và đường dẫn bên dưới lấy từ mã nguồn: coi như **Chưa kiểm trên giao diện**. Nguồn danh sách (lấy từ trang đấu thầu công khai hay mua gói dữ liệu) **chưa được chủ dự án và pháp chế chốt** (V2-Q13); hiện chỉ nhập từ tệp Excel do người của công ty chuẩn bị.

Mục lục: [1. Khái niệm](#1-khái-niệm-nhà-thầu-gói-thầu-hạng) · [2. Chuẩn bị tệp](#2-chuẩn-bị-tệp-excel) · [3. Chạy thử](#3-chạy-thử-không-ghi-gì-vào-crm) · [4. Xử lý "cần xác nhận" và lỗi](#4-đọc-báo-cáo-và-xử-lý-cần-xác-nhận) · [5. Nhập thật](#5-nhập-thật) · [6. Điểm phù hợp](#6-điểm-phù-hợp-và-hạng) · [7. Sau khi nhập](#7-sau-khi-nhập-sale-thấy-gì) · [8. Sự cố thường gặp](#8-sự-cố-thường-gặp) · [9. Đã xong khi](#9-đã-xong-khi)

## 1. Khái niệm: nhà thầu, gói thầu, hạng

Khách hàng mục tiêu của Miyano là **nhà thầu đã trúng gói cung cấp vật tư y tế, hóa chất cho bệnh viện** qua mua sắm công. Sản phẩm là dịch vụ vận hành hợp đồng (Gói Điều phối, Gói Trọn gói). Trong hệ thống:

| Khái niệm | Là gì trong CRM | Khóa nhận biết |
| --- | --- | --- |
| Nhà thầu | Một **Organization** của Frappe CRM (có thêm các trường `omni_*`) | **Mã số thuế** (MST), duy nhất |
| Gói thầu đã trúng | Bản ghi **Omni Bid Award** (bệnh viện, số TBMT, loại hàng, ngày phê duyệt, giá trị, số mã hàng, thời gian thực hiện) | Mã số thuế + số TBMT + bệnh viện |
| Người liên hệ | **Contact** gắn với nhà thầu, kèm vai trò, nguồn thông tin, đồng ý nhận liên hệ | SĐT chuẩn hóa hoặc email, trong cùng nhà thầu |
| Cơ hội bán dịch vụ | **Lead** ở giai đoạn **Tiềm năng**, một Lead đang mở cho mỗi nhà thầu | Lead trỏ tới nhà thầu |

Hệ thống tính **điểm phù hợp** 0 đến 100 và **hạng A, B, C** cho mỗi nhà thầu từ các gói thầu của họ (mục 6). Việc này tính bằng công thức cố định, không phải AI.

**Hệ thống không gửi gì cho nhà thầu khi nhập.** Nhập danh sách chỉ ghi dữ liệu. Liên hệ chủ động là việc của sale, từng nhà thầu một; không có chức năng gửi email hay tin hàng loạt.

## 2. Chuẩn bị tệp Excel

Dùng đúng mẫu `mau/Mau_DanhSach_NhaThau_TiemNang.xlsx` trong thư mục app (hoặc bấm nút **Tải mẫu trống** ở mục 3). **Không đổi tên sheet, không đổi dòng 2 (mã trường).** Mẫu có bảy sheet:

| Sheet | Dùng để | Nhập? |
| --- | --- | --- |
| `HuongDan` | Hướng dẫn điền ngay trong tệp | Không |
| `TongHop` | Số liệu tổng hợp và 20 nhà thầu điểm cao nhất, tự tính bằng công thức | Không (tự tính) |
| `NhaThau` | Mỗi dòng một nhà thầu | **Có** |
| `GoiThau` | Mỗi dòng một gói thầu đã trúng (gói liên danh: một dòng cho mỗi thành viên) | **Có** |
| `NguoiLienHe` | Mỗi dòng một người liên hệ của nhà thầu | **Có** |
| `ThamSo`, `DanhMuc` | Tiêu chí, trọng số chấm điểm và danh mục các ô chọn | Không (chỉnh `ThamSo` chỉ để so sánh) |

Quy tắc điền:

1. **Dữ liệu thật bắt đầu từ dòng 4.** Dòng 1 là tiêu đề, dòng 2 là mã trường (chữ xám nhỏ, không đổi), dòng 3 nền cam nhạt là dòng ví dụ: để nguyên, hệ thống luôn bỏ qua dòng 3.
2. Cột có dấu `*` là bắt buộc. Bắt buộc theo sheet: `NhaThau` cần mã số thuế và tên; `GoiThau` cần mã số thuế, bệnh viện, loại hàng, ngày phê duyệt; `NguoiLienHe` cần mã số thuế, họ tên và **nguồn thông tin** (lấy thông tin liên hệ từ đâu; bắt buộc vì đây là dữ liệu cá nhân).
3. **Mã số thuế** có 10 chữ số, hoặc dạng chi nhánh 10 số + gạch + 3 số (ví dụ `0123456789-001`). Nhập dạng chữ (text), tránh Excel đổi thành số mất số 0 đầu. Sai độ dài thì dòng đó bị từ chối và báo lý do.
4. **Viết tên bệnh viện thống nhất** (đúng tên trên hệ thống đấu thầu). Viết khác nhau sẽ bị đếm thành hai bệnh viện. CRM chuẩn hóa tên (bỏ dấu, chữ thường, viết tắt BV, BVĐK, TTYT) nên số bệnh viện trong CRM có thể ít hơn số trong tệp nếu tệp viết không thống nhất.
5. Cột `calc_*` (xám, công thức) tự tính, **không sửa, không xóa công thức**; hệ thống không nhập các cột này mà dùng để đối chiếu.
6. Cột **Đồng ý nhận liên hệ** chỉ ghi lại tình trạng (Có, Chưa hỏi, Từ chối). Tệp **không bao giờ** nới một người đang ở "Từ chối" thành "Có".
7. Người đã bị "Xóa dữ liệu khách" theo yêu cầu thì hệ thống **không tạo lại** (nó nhớ bằng mã băm, không nhớ tên).
8. Giới hạn: tệp tối đa 15 MB, không có macro; tối đa 5.000 dòng nhà thầu, 25.000 dòng gói thầu, 15.000 dòng người liên hệ. Quá thì từ chối cả tệp.
9. Mẫu dựng sẵn công thức cho 1.000 nhà thầu, 5.000 gói thầu, 3.000 người liên hệ; cần hơn thì kéo công thức xuống.

Tệp chứa họ tên, SĐT, email cá nhân: **chỉ lưu nơi an toàn, không dán vào chat, không đưa vào kho mã**. Hệ thống chỉ nhận tệp **riêng tư** (private).

## 3. Chạy thử (không ghi gì vào CRM)

Luôn chạy thử trước, trên **bản sao staging** nếu có.

1. Kiểm công tắc `prospect_import_enabled` ở Omni Settings đang **bật** ([02](02-cau-hinh-omni-settings.md) mục 9B) và máy chủ có **worker `omni`** đang chạy (việc nhập chạy nền ở hàng đợi `omni`).
2. Mở **Desk > `/app/omni-prospect-import`** (đường dẫn theo quy ước Frappe, chưa kiểm trên giao diện) và bấm **Thêm mới**. Chỉ Omni Admin và System Manager dùng được.
3. Ở ô **Tệp .xlsx**, đính kèm tệp và **đánh dấu tệp riêng tư**. Ô **Chạy thử (không ghi gì vào CRM)** được tích sẵn. Lưu.
4. Bấm nút **Chạy thử** trong nhóm nút **Nhập**. Màn hình báo "Đã đưa vào hàng đợi, tải lại sau ít phút".
5. Chờ vài phút rồi tải lại form. Trạng thái đi từ `Queued` sang `Running` rồi `Done`. Nếu là `Refused`, đọc ô **Thông báo** (công tắc tắt, tệp sai mẫu, tệp quá lớn, không phải tệp riêng tư...). Nếu `Running` quá lâu mà worker đã chết, form cho phép chạy lại.
6. Đọc báo cáo (mục 4). **Chạy thử chỉ đọc**: không tạo nhà thầu, Lead, người liên hệ hay gói thầu nào.

Nút **Tải mẫu trống** (cùng nhóm nút trên form) tải về tệp mẫu để điền.

Từ dòng lệnh (chạy trong `<bench-dir>`; đường dẫn tuyệt đối chỉ dùng được ở dòng lệnh, chạy ngay trong tiến trình):

```bash
bench --site <site> execute omni_crm.prospect.importer.import_workbook --kwargs "{'file_url_or_path': '/duong/dan/ngoai/repo/danh-sach.xlsx', 'dry_run': True, 'now': True}"
```

Lệnh in tên bản ghi nhật ký (ví dụ `NHAP-2026-00004`). Đọc báo cáo chữ của nó:

```bash
bench --site <site> execute omni_crm.prospect.importer.print_log_report --kwargs "{'log': 'NHAP-2026-00004'}"
```

## 4. Đọc báo cáo và xử lý "cần xác nhận"

Trên form (hoặc trong lệnh `print_log_report`) bạn thấy:

- **Số dòng theo sheet**: với mỗi sheet NhaThau, GoiThau, NguoiLienHe có bốn con số: tạo mới, cập nhật, bỏ qua, lỗi.
- **Số mục cần xác nhận**: hệ thống gặp tình huống nó **không tự quyết** và **giữ nguyên, không nhập** các dòng liên quan.
- **Tệp lỗi** (ô "Tệp lỗi (sửa rồi nhập lại)"): tệp liệt kê các dòng lỗi và lý do để bạn sửa rồi nhập lại.
- **Cảnh báo**: ví dụ "trường X đã được sửa trong CRM sau lần nhập trước, giữ nguyên"; "tham số chấm điểm trong tệp khác Omni Settings"; "N dòng bị giữ lại vì nhà thầu chờ xác nhận (không nhập, không phải lỗi)".
- **Đối chiếu với tệp**: hệ thống tính lại điểm phù hợp trong CRM và so với cột tính sẵn của tệp, liệt kê chênh lệch và so **top 10** hai bên có khớp không.

### Các loại "cần xác nhận" và cách xử lý

| Loại | Nghĩa là | Việc cần làm |
| --- | --- | --- |
| Nhà thầu trong tệp **trùng tên** với nhà thầu đã có nhưng **chưa có / khác mã số thuế** | Có thể là cùng một công ty nhập hai lần | Quyết định xem có phải cùng một công ty không. Nếu phải: **gộp hai nhà thầu** (bên dưới) rồi nhập lại. Nếu không phải: đổi tên một bên cho khác nhau |
| **Cùng mã số thuế nhưng khác tên** ngay trong tệp | Gõ nhầm mã hoặc tên | Sửa tệp cho đúng **một** tên cho mã đó rồi nhập lại. **Không dòng nào của mã đó được nhập** cho tới khi sửa |
| Mã số thuế đã có trong CRM nhưng **tên trong tệp khác tên CRM** | Hệ thống giữ tên CRM, không đổi | Nếu tên CRM sai, đổi tên bằng chức năng Đổi tên (Rename) của Frappe |
| Người liên hệ trùng SĐT/email với Contact của **nhà thầu khác** | Có thể cùng một người làm cho hai công ty | Kiểm tra có phải cùng người. Hệ thống không chuyển Contact giữa các nhà thầu |
| Người liên hệ khớp **nhiều Contact** trong cùng nhà thầu | Danh sách Contact trong CRM đang trùng | Gộp các Contact trùng rồi nhập lại |
| Tệp ghi giai đoạn **Deal** (Đề xuất gói, Đàm phán, Ký hợp đồng) nhưng **chưa có người liên hệ** | Chuyển Lead thành Deal cần ít nhất một người liên hệ | Thêm người vào sheet NguoiLienHe rồi nhập lại; Lead tạm ở giai đoạn "Đã trao đổi nhu cầu" |

**Gộp hai nhà thầu trùng.** Hiện chức năng này **chưa có nút trên giao diện**: chỉ có lệnh (chạy trong `<bench-dir>`, bằng quyền quản trị) hoặc gọi API do Omni Admin thực hiện. Trước khi gộp, **sao lưu** vì không có nút hoàn tác. Hàm chuyển Contact, gói thầu, Lead, Deal sang nhà thầu *chính*, từ chối gộp nếu hai bên có mã số thuế khác nhau, rồi xóa nhà thầu *phụ*. Tên nhà thầu là mã định danh trong CRM (xem ở danh sách Organization):

```bash
bench --site <site> execute omni_crm.api.organization.merge_organizations --kwargs "{'primary': '<nhà thầu giữ lại>', 'secondary': '<nhà thầu bị gộp>'}"
```

Lệnh này **chưa được thử trên dữ liệu thật** và cách gọi bằng `bench execute` (quyền Administrator) chưa kiểm. Lưu ý: nếu site có cài ứng dụng `himedic_crm`, việc gộp **chưa chuyển** các tham chiếu của ứng dụng đó (ghi ở `docs/12`). Chưa thử trên dữ liệu thật.

**Tên bệnh viện gần giống.** Hệ thống có bảng **Omni Hospital Alias** (`/app/omni-hospital-alias`) để gộp các tên viết khác của cùng một bệnh viện. Danh sách cặp tên giống nhau (độ giống từ 0,85) lấy bằng API `omni_crm.api.organization.list_similar_hospitals`; việc xem và gộp hiện chỉ làm bằng bảng này chứ chưa có màn hình duyệt riêng (**Chưa kiểm trên giao diện**).

Sau khi xử lý, **chạy thử lại** tới khi "cần xác nhận" và lỗi chỉ còn những điều bạn chấp nhận.

## 5. Nhập thật

1. Chỉ nhập thật khi: chạy thử không còn lỗi chặn, "cần xác nhận" đã xử lý hoặc chấp nhận bỏ, top 10 khớp với sheet `TongHop` (hoặc chênh lệch đã giải thích), **và đã sao lưu** ([01](01-cai-dat-va-ha-tang.md) mục 8).
2. Mở lại bản ghi nhật ký, bấm **Nhập** > **Nhập thật** và xác nhận "Nhập thật vào CRM? Nên chạy thử và xem báo cáo trước." Hoặc dòng lệnh với `'dry_run': False`.
3. Hệ thống làm lần lượt: nhà thầu (tạo hoặc cập nhật theo mã số thuế), gói thầu (theo khóa gói), người liên hệ, chuyển Deal nếu tệp ghi giai đoạn Deal, tạo **Lead ở giai đoạn Tiềm năng** cho nhà thầu chưa có Lead đang mở, rồi tính điểm phù hợp. Người phụ trách Lead mới lấy từ cột `owner_email` của tệp (không có thì theo quy tắc phân công của CRM).
4. Mỗi 100 dòng hệ thống ghi lại một lần; **lỗi một dòng không làm hỏng cả tệp**. Nhà thầu đã có Lead đang mở thì **không tạo Lead mới** và **giữ nguyên người phụ trách**, chỉ tính lại điểm. Trường mà người dùng đã sửa tay trong CRM sau lần nhập trước thì **không bị ghi đè**.
5. **Nhập lại cùng tệp không tạo trùng.** Có thể nhập lại nhiều lần sau khi sửa tệp.

**Quay lui.** Importer không xóa gì. Muốn bỏ một lần nhập: lọc gói thầu và nhà thầu có nguồn dữ liệu "Nhập danh sách nhà thầu (NHAP-…)" và xử lý tay, hoặc khôi phục bản sao lưu trước khi nhập. Tắt hẳn chức năng: bỏ tích `prospect_import_enabled` (không xóa dữ liệu đã nhập).

## 6. Điểm phù hợp và hạng

Điểm tính từ các gói thầu **trong khoảng thời gian xét** (mặc định 24 tháng) và **đúng loại hàng được tính điểm** (mặc định: VTYT tiêu hao, Hóa chất – sinh phẩm). Với mỗi nhà thầu:

- **Số bệnh viện** khác nhau; **số mã hàng** (lấy số lớn hơn giữa "số mã hàng ước tính" trong tệp và số mã hàng của gói lớn nhất); **tổng giá trị** (tỷ đồng); và **còn hợp đồng đang thực hiện** hay không (tính cả gói ngoài khoảng xét, miễn còn hiệu lực).
- **Tiêu chí bắt buộc:** tối thiểu **5 bệnh viện** và **200 mã hàng** (chỉnh ở Omni Settings, [02](02-cau-hinh-omni-settings.md) mục 9B).
- **Điểm 0 đến 100** = 40 (bệnh viện, đầy khi đạt 10) + 30 (mã hàng, đầy khi đạt 500) + 20 (giá trị, đầy khi đạt 20 tỷ) + 10 (còn hợp đồng). Các con số trong ngoặc là mặc định, chỉnh được.
- **Hạng:** **A** = đạt tiêu chí bắt buộc và điểm từ 70; **B** = đạt tiêu chí bắt buộc nhưng dưới 70; **C** = chưa đạt tiêu chí bắt buộc.

Trọng số, mức tối đa, ngưỡng hạng A và tiêu chí bắt buộc là **đề xuất ban đầu, chưa được trưởng sale duyệt**. Điểm tính lại khi gói thầu hoặc dữ liệu nhà thầu đổi và mỗi đêm lúc 02:20 (vì "còn hiệu lực" phụ thuộc ngày). Công tắc `fit_scoring_enabled` tắt = giữ nguyên điểm cũ.

Điểm phù hợp **khác** điểm quan tâm: phù hợp nói nhà thầu đáng theo đuổi đến đâu theo hồ sơ thầu; quan tâm nói họ phản hồi với chúng ta đến đâu ([02](02-cau-hinh-omni-settings.md) mục 8). Ưu tiên liên hệ:

| | Quan tâm thấp | Quan tâm cao |
| --- | --- | --- |
| **Phù hợp A hoặc B** | Ưu tiên gọi chủ động | **Nóng**: báo sale ngay, mục tiêu là đề xuất gói |
| **Phù hợp C** | Nuôi dưỡng nhẹ, không gọi | Tư vấn, kiểm tra lại dữ liệu gói thầu |

"Lead nóng" của nhà thầu đã có hạng cần cả điểm quan tâm vượt ngưỡng **và** hạng A hoặc B.

## 7. Sau khi nhập: sale thấy gì

- Lead mới ở giai đoạn **Tiềm năng**, có tên công ty và mã số thuế.
- Trong **Tổng quan CSKH** ([13](13-dashboard-va-cham-soc.md)): danh sách **"Phù hợp A/B, chưa liên hệ"**, số nhà thầu theo hạng và khu vực, phễu giai đoạn.
- Trong **Chi tiết khách** của từng Lead: khối **Mức phù hợp** (đạt hay chưa từng tiêu chí, điểm, hạng, cách tính), tab **Gói thầu đã trúng** (bệnh viện, số TBMT, loại hàng, ngày, giá trị, mã hàng, còn hiệu lực) và danh sách người liên hệ có nhãn "Từ chối nhận liên hệ" nếu có.
- Người liên hệ có nhãn **"Từ chối nhận liên hệ"** hoặc đã bị xóa dữ liệu thì SĐT, email và nút gọi hoặc nhắn bị ẩn.
- Giai đoạn bán hàng B2B: Tiềm năng, Đã liên hệ, Đã trao đổi nhu cầu (Lead); Đề xuất gói, Đàm phán, Ký hợp đồng (Deal); Không phù hợp (kèm lý do mất). Bảng ánh xạ từ trạng thái Lead cũ sang giai đoạn mới **chỉ in ra, chưa áp dụng** và đang chờ trưởng sale duyệt.

## 8. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Nút **Chạy thử** và **Nhập thật** không hiện | Bản ghi mới chưa lưu, chưa đính kèm tệp, hoặc đang `Queued`/`Running` | Lưu bản ghi và đính kèm tệp; chờ việc đang chạy xong |
| Trạng thái `Refused` | Công tắc `prospect_import_enabled` tắt; tệp sai mẫu (đổi tên sheet, đổi mã trường dòng 2); tệp quá lớn hoặc có macro. Tệp không đánh dấu riêng tư thì báo lỗi ngay khi chạy | Đọc ô **Thông báo** hoặc thông báo lỗi; sửa theo mục 2 |
| Mãi `Queued` | Không có worker `omni` | [01](01-cai-dat-va-ha-tang.md) mục 3 |
| Mã số thuế báo sai độ dài, hoặc Excel mất số 0 đầu | Ô đang định dạng số | Định dạng cột thành văn bản (text), nhập lại |
| Số bệnh viện trong CRM ít hơn trong tệp | Tên viết khác nhau được chuẩn hóa về một | Bình thường; kiểm bảng Omni Hospital Alias |
| Top 10 trong CRM không khớp sheet `TongHop` | Tham số `ThamSo` trong tệp khác Omni Settings, hoặc tên bệnh viện viết không thống nhất | Đọc cảnh báo "tham số chấm điểm trong tệp khác Omni Settings"; đồng bộ hai bên |
| Nhiều dòng "bị giữ lại vì nhà thầu chờ xác nhận" | Mục "cần xác nhận" chưa xử lý | Mục 4 |
| Nhà thầu có Lead nhưng Lead không đổi người phụ trách | Đúng thiết kế: Lead đang mở giữ nguyên người phụ trách | Đổi người phụ trách tay trong CRM |
| Điểm không đổi sau khi sửa `fit_*` | Điểm tính lại khi dữ liệu đổi và lúc 02:20 | Chờ hoặc nhập lại tệp |

## 9. Đã xong khi

- [ ] `prospect_import_enabled` bật; worker `omni` chạy; đã sao lưu trước khi nhập thật.
- [ ] Tệp theo đúng mẫu, dữ liệu từ dòng 4; mã số thuế dạng văn bản; mọi người liên hệ có nguồn thông tin.
- [ ] **Chạy thử** xong ở trạng thái `Done`, đã đọc số dòng, tệp lỗi, cảnh báo và đối chiếu.
- [ ] Mọi mục "cần xác nhận" đã xử lý (gộp, đổi tên, sửa tệp) hoặc chủ dự án chấp nhận bỏ.
- [ ] Top 10 trong CRM đã so với sheet `TongHop`; chênh lệch (nếu có) đã giải thích.
- [ ] **Nhập thật** xong; thử mở 2 đến 3 nhà thầu ở Chi tiết khách: có hạng, có gói thầu, có người liên hệ.
- [ ] Ghi lại (ngày, người, tên tệp, mã nhật ký `NHAP-…`) để truy vết và để quay lui nếu cần.
- [ ] Đã nói rõ với đội sale: hệ thống không gửi gì cho nhà thầu; liên hệ chủ động là từng người một ([13](13-dashboard-va-cham-soc.md)); chưa có kết luận pháp lý về liên hệ chủ động (V2-Q12).
