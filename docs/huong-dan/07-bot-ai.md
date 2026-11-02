# 07 · Bot AI: bật từng bước an toàn

Dành cho: quản trị hệ thống (Omni Admin), phụ trách kho tri thức (Omni Knowledge Editor), trưởng sale. Đặc tả: `docs/08-bot-ai.md`, bộ đề: `eval/README.md`, cổng nghiệm thu: `docs/nghiem-thu/dot-3.md`.

> **Hiện trạng: không có số liệu nào về chất lượng bot thật.** Khi viết tài liệu này, chưa chạy lần nào với AI thật hay khách thật: không có khóa AI, 220 ca bộ đề đều là ca tổng hợp chưa duyệt (0 ca thật), Q11 chưa chốt. Mọi con số 100% trong báo cáo `-fake.md` là của client giả, chỉ chứng minh hạ tầng chấm điểm chạy, **không phải điểm của bot**. Cổng `nhap` ở trạng thái **CHƯA ĐO** (= không qua). Vì vậy mặc định: `bot_analyze_enabled = 0`, `bot_reply_mode = Off`, mọi nhóm `auto_send_enabled = 0`, không đặt `omni_auto_send_allowed`.

Bot làm 4 việc theo thứ tự rủi ro tăng dần: **phân tích** (trích thông tin vào Lead, sai thì sale sửa) → **chấm điểm Lead** (luật cố định, không do AI) → **soạn trả lời** (Shadow/Draft/tự gửi) → tóm tắt cuộc gọi (**chưa có**, [02](02-cau-hinh-omni-settings.md) mục 10).

Mục lục: [0. Nguyên tắc](#0-nguyên-tắc-an-toàn) · [1. Khóa AI và Q11](#1-khóa-ai-và-cổng-q11) · [2. Kho tri thức](#2-kho-tri-thức) · [3. Nhóm câu hỏi](#3-nhóm-câu-hỏi) · [4. Bảng điểm](#4-bảng-điểm-lead) · [5. Bộ đề](#5-chạy-bộ-đề-và-các-cổng) · [6. Bật phân tích/chấm điểm](#6-bật-phân-tích-và-chấm-điểm) · [7. Shadow](#7-shadow-bot-chạy-ngầm) · [8. Draft](#8-draft-nháp-cho-sale-duyệt) · [9. Auto](#9-auto-tự-gửi-theo-nhóm-cuối-cùng) · [10. Đọc báo cáo](#10-đọc-các-báo-cáo) · [11. Ngân sách và ngắt mạch](#11-ngân-sách-và-ngắt-mạch) · [12. Tắt khẩn](#12-tắt-khẩn-cấp) · [13. Sự cố](#13-sự-cố-thường-gặp) · [14. Đã xong khi](#14-đã-xong-khi)

## 0. Nguyên tắc an toàn

- **Nội dung khách là dữ liệu, không phải lệnh.** Bot được thiết kế bọc lời khách trong thẻ dữ liệu; kiểm bằng 10 ca "chèn lệnh" trong bộ đề (chỉ kiểm được với AI thật).
- Bot **chỉ nói được điều có trong kho tri thức**. Giá, chiết khấu, thời hạn giao, bảo hành, cam kết: nếu không ghi nguyên văn trong kho thì bị chốt chặn bằng mã đổi thành "chuyển người".
- Nhóm câu hỏi "Hỏi giá", "Đặt hàng", "Khiếu nại", "Khác" đặt **luôn chuyển người** (`always_handoff`).
- **Mỗi bước dưới đây có điều kiện vào; không nhảy bước.** Bật bước sau khi chưa đủ điều kiện bước trước là cách làm bot nói sai với khách thật.
- Tắt từng phần được ngay, không mất dữ liệu (mục 12).

## 1. Khóa AI và cổng Q11

**Q11** (chính sách gửi dữ liệu hội thoại khách sang dịch vụ AI) là quyết định của chủ dự án, **chưa chốt**; nó chặn mọi lần chạy AI với dữ liệu khách thật.

1. Nhập khóa: **Omni Settings > Khóa Claude API** (`ai_api_key`), chỉ trên máy chủ, không dán vào chat/log.
2. Khi Q11 đã được chốt cho phép: đặt `omni_ai_allow_customer_data = 1` trong `sites/<site>/site_config.json` của đúng site ([01](01-cai-dat-va-ha-tang.md) mục 10). Cân nhắc kỹ `omni_ai_unmask_pii`: mặc định SĐT/email được che trước khi gửi AI.
3. Đặt `daily_token_budget` thấp khi thử ([02](02-cau-hinh-omni-settings.md) mục 7).
4. Kiểm tra khóa bằng **hội thoại tổng hợp** (không dữ liệu khách; cổng Q11 không chặn vì tổng hợp). Chạy trong `<bench-dir>`:

```bash
bench --site <site> execute omni_crm.bot.smoke.run
```

Lệnh gọi Claude API thật một lần và in một dòng `Omni Bot Run` (trạng thái `Success`, số token, độ trễ). Thêm `--kwargs "{'fake': True}"` để chạy bằng client giả (không cần khóa). Kết quả: xem tại `/app/omni-bot-run`. **Chưa chạy thử với khóa thật** khi viết tài liệu này.

Nếu `omni_ai_allow_customer_data` chưa bật, mọi lời gọi AI với hội thoại khách thật bị bỏ qua và `Omni Bot Run` ghi `Skipped Switch` (lý do "Chính sách gửi dữ liệu khách sang AI (Q11) chưa được chốt"). Chưa có khóa API thì cũng `Skipped Switch` với lý do "Chưa cấu hình khóa Claude API".

## 2. Kho tri thức

Kho tri thức (DocType **Omni Knowledge Item**) là các cặp hỏi–đáp mà bot được phép dùng. Bot chỉ nói điều có ở đây.

**Quy tắc nội dung** (từ `mau/README.md`):

- Mỗi dòng một cặp hỏi–đáp; nhiều cách hỏi cùng một ý thì xuống dòng trong cùng ô "Câu hỏi mẫu".
- **Không ghi giá.** Không ghi thông tin chưa được công bố chính thức.
- Mã mục `KB-00001`, `KB-00002`, ... **không bao giờ đổi** cho một mục; thêm mục mới thì cấp số mới. Nhập lại cùng mã sẽ cập nhật mục cũ.
- Ô còn dấu ngoặc vuông `[...]` là chỗ chưa điền: mục đó **không được nhập** (hệ thống không bịa nội dung).
- Tối đa **150 mục đang dùng**: vượt ngưỡng này bot **không soạn trả lời** cho tới khi có thiết kế truy hồi (ghi Error Log tiêu đề "Omni: kho tri thức vượt ngưỡng truy hồi"). Dùng `active` (Đang dùng) để tạm loại mục.

**Nhập từ CSV** (mẫu: `mau/kho-tri-thuc-mau.csv`, cột `item_id, question_group, question, answer, source, owner_email`). Nhóm câu hỏi phải **tồn tại trước** (mục 3). Chạy trong `<bench-dir>`, với đường dẫn tuyệt đối:

```bash
bench --site <site> execute omni_crm.bot.knowledge.import_items --kwargs "{'path': '/duong/dan/tuyet/doi/kho-tri-thuc-da-duyet.csv'}"
```

Kết quả: số mục tạo/cập nhật/bỏ qua và danh sách cảnh báo (nhóm không tồn tại, còn `[...]`, `owner_email` không tra ra User). Mỗi lần kho đổi, `Omni Settings.kb_version` tự tăng.

**Biên tập trong Desk** (vai trò **Omni Knowledge Editor**; Omni Admin cũng làm được): `/app/omni-knowledge-item`. Các trường: Mã mục, Nhóm câu hỏi, **Đang dùng**, Tài liệu gốc, Người phụ trách, Duyệt lần cuối, Câu hỏi mẫu, Câu trả lời chuẩn. Biên tập viên có quyền đọc/ghi/tạo/xóa mục và nhóm, **chỉ đọc** nháp bot và bộ đề, và xem được ba báo cáo bot. Sale (Sales User) chỉ đọc kho. (Giao diện chưa kiểm.)

Vòng việc hằng tuần của biên tập viên: xem báo cáo (mục 10) để biết câu nào bot phải chuyển người vì "ngoài kho" và nháp nào sale sửa nặng/bỏ → cập nhật kho → **chạy lại bộ đề** (mục 5). Đổi kho mà không chạy lại bộ đề là đổi hành vi bot không kiểm soát.

## 3. Nhóm câu hỏi

Mỗi mục kho thuộc một **Omni Question Group**. Bot phân loại câu khách vào một nhóm; nhóm quyết định có được tự gửi không (mục 9).

Nhập mẫu (8 nhóm: Giờ làm việc, Địa chỉ và liên hệ, Thông tin sản phẩm, Thủ tục mua hàng, **Hỏi giá**, **Đặt hàng**, **Khiếu nại**, **Khác**; bốn nhóm cuối `always_handoff = 1`). Chạy trong `<bench-dir>`:

```bash
bench --site <site> execute omni_crm.bot.knowledge.import_groups --kwargs "{'path': '/duong/dan/tuyet/doi/nhom-cau-hoi.csv'}"
```

Cột: `group_name, always_handoff (0/1), description`. Mô tả giúp bot phân loại. Nhóm `always_handoff` luôn thành "chuyển người", kể cả khi bot đề xuất trả lời, và **không bao giờ** bật tự gửi được.

Trưởng sale rà danh sách nhóm trước khi nhập (tên nhóm và mô tả là cách công ty phân loại câu hỏi của khách).

## 4. Bảng điểm Lead

Xem [02](02-cau-hinh-omni-settings.md) mục 8. Trưởng sale duyệt bảng điểm (Q5); nhập bằng `omni_crm.bot.scoring.import_rules`. Bảng điểm mẫu chỉ là đề xuất. Chưa nhập bảng điểm thì bật `lead_scoring_enabled` cũng không chấm gì.

## 5. Chạy bộ đề và các cổng

**Bộ đề** (`eval/README.md`) trả lời câu hỏi "bot đã đủ tốt để bật chưa". Mỗi ca là một dòng JSON (`.jsonl`) vào DocType **Omni Eval Case**. Thành phần tối thiểu 200 ca: 100 ca có trong kho (Reply đúng), 25 hỏi giá, 15 khiếu nại, 25 ngoài kho (Handoff, không bịa), 10 đòi gặp người, 10 chèn lệnh, 20 trích thông tin, 5 y khoa ngoài tài liệu.

Hiện có sẵn 220 ca **tổng hợp** (`eval/cases/synthetic-2026-10-30.jsonl`, bịa, chưa duyệt) kèm kho bịa `eval/kb-tong-hop.csv`. Chúng chỉ kiểm hạ tầng; **cổng chỉ tính khi chạy AI thật trên bộ đề đã được người duyệt.** Ca thật (từ hội thoại thật Đợt 1, che SĐT/email/tên) lấy bằng `omni_crm.bot.eval.builder.build_from_real` (cần Q11) và phải được người duyệt trước khi dùng (importer từ chối ca chưa duyệt).

Lệnh (chạy trong `<bench-dir>`, đường dẫn tuyệt đối):

```bash
# nhập bộ đề
bench --site <site> execute omni_crm.bot.eval.importer.import_jsonl --kwargs "{'path': '/duong/dan/tuyet/doi/bo-de-da-duyet.jsonl'}"

# chạy một cổng: phan_tich | nhap | doi_cau_lenh
bench --site <site> execute omni_crm.bot.eval.run.run_cli --kwargs "{'gate': 'phan_tich', 'allow_real': True, 'reviewed': True}"
```

- `'reviewed': True` là lời **khai** rằng người có trách nhiệm đã duyệt bộ đề: chỉ đặt khi thật sự đã duyệt, nếu không kết quả không được tính là đo.
- Không `allow_real`: client thật bị từ chối; không có khóa thì mọi ca ghi **"chưa đo"** và cổng KHÔNG qua. `fake: True` chỉ để kiểm hạ tầng.
- Kết quả ghi `Omni Eval Run` (`/app/omni-eval-run`) và báo cáo Markdown `eval/reports/<ngày>-<phiên bản>.md` liệt kê ca trượt kèm lý do.
- Phần "câu trả lời đạt" do mô hình giám khảo chấm theo `eval/rubric.md`; **người chấm lại ngẫu nhiên 20%** và mọi ca giám khảo cho trượt (`export_regrade_csv` rồi `import_regrade`). Chưa chấm lại đủ thì cổng không qua. Lần chạy đạt dùng làm mốc bằng `accept_run`.

| Cổng | Điều kiện đạt (từ `bot/eval/grading.py`) | Dùng để |
| --- | --- | --- |
| `phan_tich` | Trường trích xuất đúng ≥ 90% | Bật bot phân tích (mục 6) |
| `nhap` | Trường đúng ≥ 90%, hành động đúng ≥ 90%, trả lời đạt ≥ 85%, **lỗi nghiêm trọng = 0**, không còn ca "chưa đo", đã chấm lại đủ mẫu 20% | Bật Draft (mục 8) |
| `doi_cau_lenh` | Không chỉ số nào giảm quá 2 điểm phần trăm so với lần được chấp nhận gần nhất, lỗi nghiêm trọng = 0 | Đổi câu lệnh hoặc mô hình khi đang chạy |

**Trạng thái hiện tại:** cả ba cổng **chưa đo**. Lần chạy `nhap` không client ghi 220/220 ca "chưa đo" (`EVR-2026-00415`).

## 6. Bật phân tích và chấm điểm

Điều kiện: mục 1 xong, kho và nhóm đã nhập, **cổng `phan_tich` đạt** bằng AI thật trên bộ đề đã duyệt.

1. Đặt `bot_analyze_enabled = 1`. (Với `sandbox_mode` bật thì lời gọi AI vẫn xảy ra: sandbox chỉ chặn chiều gửi.)
2. Sau mỗi lượt có tin khách, bot trích: tên, đơn vị, SĐT, email, nhu cầu, sản phẩm, số lượng, thời điểm, mức gấp, ý định, tóm tắt. Mỗi trường phải có **trích dẫn nguyên văn** từ lời khách làm căn cứ, không có căn cứ thì bỏ. Kết quả ghi vào các trường `omni_*` của Lead (Nhu cầu, Sản phẩm quan tâm, Số lượng, Thời điểm mua dự kiến, Mức gấp, Ý định gần nhất, Tóm tắt của bot). **Không ghi đè** trường sale đã sửa tay, và không ghi đè SĐT/email đã có.
3. Hộp thư hiện tóm tắt bot và nhãn ý định (kèm câu "Tóm tắt do bot, có thể chưa chính xác").
4. Khi sẵn sàng chấm điểm (bảng điểm đã duyệt): `lead_scoring_enabled = 1`. Vượt `hot_lead_threshold` thì người phụ trách nhận thông báo "Lead nóng: ..., điểm ...".

Các trường bot ghi trên form Lead của CRM có hiện hay không phụ thuộc cấu hình bố cục trường (Fields Layout) của CRM, **việc chưa làm** (`docs/nghiem-thu/dot-3.md`, "Cấu hình còn lại"); điểm và tóm tắt luôn xem được trong Hộp thư.

## 7. Shadow: bot chạy ngầm

Điều kiện: mục 6 chạy ổn một thời gian (Bot Run phần lớn `Success`), Q11 đã chốt.

1. `bot_reply_mode = Shadow`.
2. Với mỗi lượt khách, bot soạn nháp **ngầm** (`Omni Bot Draft`, chế độ Shadow); **sale không thấy gì**, không thông báo, bot không im, khách không nhận gì. Khi sale trả lời khách trong 24 giờ sau, hệ thống ghi câu của sale cạnh nháp để so (độ khác biệt 0 = giống hệt).
3. Đọc báo cáo **Bot ngầm** (mục 10): nhóm nào bot hay chuyển người, nháp nào khác câu sale nhất, sale trả lời ngoài kho ở đâu → bổ sung kho tri thức.
4. Tùy chọn: chạy lại dữ liệu cũ (`omni_crm.bot.replay.replay`, với khoảng thời gian có sale trả lời) để có số liệu sớm; chạy theo lô nhỏ, chịu hạn mức token. Lệnh này cần Q11 và **chưa được chạy thử** khi viết tài liệu này.

Ở Shadow bot **không bao giờ** gửi gì cho khách.

## 8. Draft: nháp cho sale duyệt

**Điều kiện vào (Q12, đã chốt):** cổng `nhap` được **đo bằng AI thật trên bộ đề đã duyệt và đạt** (hành động đúng ≥ 90%, trả lời đạt ≥ 85%, 0 lỗi nghiêm trọng), ghi lại mã `Omni Eval Run`; Q11 chốt; Q6, Q8 chốt hoặc chấp nhận mặc định tạm; staging có OA thử; có sale tham gia tuần chạy thử. **Hiện chưa đủ điều kiện nào ở mức "đã đo".**

1. Trên staging trước: `sandbox_mode` bật, `bot_reply_mode = Draft`, một Lead thử có chủ là sale tham gia.
2. Sale thấy nháp trong Hộp thư ([08](08-huong-dan-nhan-vien-sale.md), và [duyet-nhap-bot.md](duyet-nhap-bot.md)): Gửi, Sửa rồi gửi, Bỏ, rồi chấm Đúng/Sai/Thiếu. **Bot không bao giờ tự gửi ở Draft**: tin đi là tin của sale (`source = Sale`).
3. Nhóm luôn chuyển người, hoặc bot không soạn được: thẻ "Bot đề nghị chuyển người", sale nhận Notification Log, bot **im** với khách đó `handoff_pause_hours` giờ (24 giờ mặc định) hoặc đến khi sale bấm "Cho bot tiếp tục".
4. Tuần chạy thử: theo `docs/nghiem-thu/uat-dot-3.md` (mục 2-5 và 8). Đo bằng `measure_dot3.py` (chạy trong `<bench-dir>`):

```bash
bench --site <site> execute omni_crm.tests.acceptance.measure_dot3.run --kwargs '{"start": "2026-11-23 00:00:00", "end": "2026-11-30 00:00:00"}'
```

Chỉ số cần: nháp đúng (gửi nguyên văn hoặc sửa nhẹ) ≥ 85%, lỗi nghiêm trọng = 0, không có tin bot sai tới khách thật. Thiếu mẫu thì lệnh in "chưa đủ dữ liệu", không bao giờ ngầm ghi "ĐẠT". Nháp tới 24 giờ không ai quyết hoặc khách nhắn thêm thì tự hết hạn.

5. Chỉ sau tuần chạy thử đạt mới bật Draft cho sale ở production: tắt sandbox có chủ đích (xem [10](10-quy-trinh-len-san-xuat.md)).

## 9. Auto: tự gửi theo nhóm (cuối cùng)

Auto là mức rủi ro cao nhất: bot nói thẳng với khách không qua người duyệt. **Mặc định tắt ở hai tầng và chỉ mở sau khi có số liệu thật từng nhóm.** `bot_reply_mode` vẫn là `Draft` (không có giá trị "Auto" ở ô này); Auto nằm ở từng nhóm câu hỏi.

**Điều kiện trước:**

1. **Q1 được chủ dự án chốt** (cho phép bot tự gửi). Hiện chưa chốt.
2. Cổng `nhap` đạt, đã chạy Draft với sale thật.
3. Người **vận hành** đặt `omni_auto_send_allowed = 1` trong `sites/<site>/site_config.json` của site production được phép ([01](01-cai-dat-va-ha-tang.md) mục 10). Không ở dev/staging khi chưa cho phép.
4. Nhóm câu hỏi đủ **điều kiện số liệu 14 ngày gần nhất**: ≥ 30 nháp đã quyết định (gửi nguyên văn + sửa rồi gửi + bỏ), ≥ 95% trong đó là gửi nguyên văn hoặc sửa nhẹ (độ khác biệt ≤ 0,1), **0** nháp bị đánh dấu nghiêm trọng hoặc chấm "Sai" kèm ghi chú, và nhóm không phải `always_handoff`.

**Cách bật cho một nhóm** (chỉ **Omni Admin**; chưa kiểm trên giao diện thật):

1. Mở form nhóm: `/app/omni-question-group/<tên nhóm>`.
2. Bấm **Bật tự động gửi**. Hộp thoại hiện số liệu: mẫu đã quyết định / tối thiểu 30; số gửi nguyên văn hoặc sửa nhẹ và tỉ lệ / cần 95%; số nghiêm trọng / chấm Sai kèm ghi chú; số đang chờ / hết hạn (không tính). Nếu khóa hạ tầng đang đóng, hộp thoại báo "Khóa hạ tầng omni_auto_send_allowed đang đóng: không bật được".
3. Đủ điều kiện: bấm **Xác nhận bật**. Chưa đủ: chỉ còn **Bật cưỡng bức** kèm lý do (≥ 10 ký tự, ghi lại vào nhóm); khóa hạ tầng và nhóm luôn chuyển người vẫn chặn kể cả cưỡng bức. Nên **không** cưỡng bức.
4. Bật thử đầu tiên: nhóm an toàn nhất (ví dụ "Giờ làm việc") và **`sandbox_mode` bật** (tin bot sinh ra là tin thử, không gửi gì). Chỉ tắt sandbox để gửi thật tới đúng tài khoản thử do người phụ trách chỉ định.

**Bot tự gửi chỉ khi tất cả đều đúng:** kênh Zalo OA và đang bật chiều ra; trong khung miễn phí 48 giờ; trong `bot_active_hours`; bot chưa bị tạm dừng; sale chưa trả lời sau tin khách; có phân tích của lượt và ý định không phải "Khiếu nại", khách không đòi gặp người; chưa quá 3 tin bot cho khách này trong 24 giờ; qua mọi chốt chặn. Không đủ thì nháp ở lại làm nháp thường cho sale (hoặc kèm cờ `info:auto_khong_gui:<mã>`).

**Tự tắt (ngắt mạch):** nhóm tự rơi về Draft, người dùng Omni Admin và Sales Manager nhận Notification Log "Bot tự gửi nhóm X đã tự tắt: <lý do>", khi:

1. Sale chấm **Sai** (hoặc đánh dấu nghiêm trọng) một tin bot đã tự gửi.
2. Khách khiếu nại trong 24 giờ sau tin bot.
3. Tỉ lệ chuyển người 24 giờ gần nhất ≥ 2 lần tỉ lệ 14 ngày trước (có đủ mẫu), kiểm hằng giờ.

**Chưa có thời gian chờ (cooldown)** sau khi tự tắt: nếu số liệu 14 ngày vẫn đạt, quản trị có thể bật lại ngay. Quy ước đề xuất: mỗi lần tự tắt phải có người xem nguyên nhân (và sửa kho tri thức) trước khi bật lại. Có nên bắt buộc cooldown hay không là quyết định mở (`docs/12` mục D).

**Tắt một nhóm:** nút **Tắt tự động gửi** trên form nhóm (hộp thoại hỏi lý do), hoặc nút **Tắt tự động nhóm này** cạnh tin bot trong Hộp thư (nút này chỉ hiện với người **vừa** là Omni Admin **vừa** đọc được Lead đó; Omni Admin mặc định không đọc Lead nên đường chắc chắn là form nhóm), hoặc sale chấm "Sai" tin bot (nhóm tự tắt).

Tin bot trong Hộp thư có nhãn **"Bot đã gửi"** và tên nhóm; tin bot gửi lỗi thì sale phụ trách nhận thông báo "Tin bot tự gửi bị lỗi, hãy kiểm tra trên Zalo" (nháp không tự về Draft vì khách có thể đã nhận).

## 10. Đọc các báo cáo

Mở trong Desk (Báo cáo / Report): **Bot ngầm**, **Bot nháp**, **Bot tự gửi** (đường dẫn dạng `/app/query-report/Bot nháp`; chưa kiểm trên giao diện). Quyền: Omni Admin và Omni Knowledge Editor. Các báo cáo chỉ có **số đếm**, không có nội dung hội thoại (trừ chế độ "Nháp khác câu sale nhất" của Bot ngầm).

**Bot ngầm** (nháp Shadow). Bộ lọc: Cách xem (*Theo nhóm* / *Nháp khác câu sale nhất*), Từ ngày, Đến ngày, Số nháp. Cột theo nhóm: Số nháp · Chuyển người · Tỉ lệ chuyển người (%) · Đã so với sale · Sale trả lời ngoài kho · Khác biệt trung bình · các khoảng Khác biệt 0-0.2, 0.2-0.5, 0.5-1. Đọc: **độ khác biệt 0 = nháp giống hệt câu sale**; số càng gần 1 càng khác. Tỉ lệ chuyển người cao ở nhóm "thông tin" nghĩa là kho thiếu; "Sale trả lời ngoài kho" cao nghĩa là kho thiếu mục sale vẫn trả lời được. Xem "Nháp khác câu sale nhất" để thấy ví dụ cụ thể và bổ sung kho.

**Bot nháp** (nháp Draft). Bộ lọc: Cách xem (*Theo nhóm* / *Theo tuần*), Từ ngày, Đến ngày. Cột: Nhóm câu hỏi · Số nháp · Chuyển người · Đang chờ · Hết hạn · Đã quyết định · Gửi nguyên văn · Sửa rồi gửi · Bỏ · Tỉ lệ nguyên văn / sửa / bỏ (%) · Chấm Đúng / Sai / Thiếu · Nghiêm trọng. Đọc: tỉ lệ **bỏ** cao hoặc **chấm Sai** là dấu hiệu kho lỗi thời hoặc nhóm chưa nên mở rộng; **nghiêm trọng > 0** phải tìm nguyên nhân ngay. Tỉ lệ tính trên nháp Reply đã có quyết định; nháp chuyển người đếm riêng. "Hết hạn" cao nghĩa là sale không kịp duyệt.

**Bot tự gửi** (nhóm Auto). Cột: Nhóm câu hỏi · Đang tự gửi · Luôn chuyển người · Đủ điều kiện mở · Nháp đã quyết định (14 ngày) · Tỉ lệ đạt (%) · Nghiêm trọng · Tin bot đã gửi · Chấm Đúng / Sai / Thiếu · Số lần tự tắt. Đọc: dùng cột "Đủ điều kiện mở" để chọn nhóm; **số lần tự tắt tăng** là dấu hiệu nhóm chưa an toàn.

## 11. Ngân sách và ngắt mạch

- **Ngân sách**: `daily_token_budget` ([02](02-cau-hinh-omni-settings.md) mục 7). Hết thì bot dừng, mỗi lần gọi ghi `Skipped Budget`, cảnh báo `ai_het_han_muc` (một lần mỗi ngày); cảnh báo `ai_gan_han_muc` khi dùng > 80%. Xem khối "Bot AI" ở `/app/omni-monitor`: token hôm nay / hạn mức, số lần gọi 24 giờ, lần lỗi, lần bị bỏ qua do hạn mức. Đây là hạn mức mềm: có thể vượt nhẹ khi nhiều worker chạy song song.
- **Ngắt mạch AI** (tự động): 5 lần lỗi AI liên tiếp → trong 5 phút mọi lời gọi ghi `Skipped Switch` và cảnh báo `ai_ngat_mach`. Kênh, Lead, chấm điểm không bị ảnh hưởng; nháp Shadow vẫn ghi "chuyển người" khi kho rỗng. Để mở lại ngay: xóa khóa cache `omni:ai:breaker_open` (ví dụ `bench --site <site> clear-cache` trong `<bench-dir>`; lệnh này xóa **toàn bộ** cache của site nên chỉ dùng khi cần, chưa kiểm hệ quả).
- **Chi phí tăng vọt**: xem `Omni Bot Run` theo giờ và theo loại (Analyze/Reply/Summarize/Judge); giảm hạn mức; kiểm tra vòng lặp gọi lại.
- Mỗi lời gọi AI tạo một `Omni Bot Run` (không lưu toàn văn hội thoại). Xem `/app/omni-bot-run`, lọc Trạng thái ≠ Success để tìm lỗi (`Error`, `Invalid Output`, `Skipped Budget`, `Skipped Switch`).

## 12. Tắt khẩn cấp

Theo thứ tự từ nhẹ đến nặng (chạy tại Omni Settings trừ khi ghi khác):

| Tình huống | Làm gì | Ảnh hưởng |
| --- | --- | --- |
| Một nhóm tự gửi nói sai | Nút "Tắt tự động gửi"/"Tắt tự động nhóm này" của nhóm; sale gửi đính chính; sửa kho; chạy lại bộ đề | Chỉ nhóm đó về Draft |
| Mọi tự gửi | Xóa `omni_auto_send_allowed` khỏi `sites/<site>/site_config.json` và khởi động lại | Tin bot đang chờ `Blocked`; câu giữ chân hệ thống cũng bị chặn; mọi nhóm về Draft |
| Nháp bot sai/bất thường | `bot_reply_mode = Shadow` hoặc `Off` | Sale không nhận nháp mới; nháp đã gửi là tin của sale |
| Dừng mọi AI | `bot_reply_mode = Off` **và** `bot_analyze_enabled = 0` (quay lui mức 2) | Hệ thống thành "kênh vào CRM" như Đợt 1: tin vào, Lead, dòng thời gian đủ; 0 lời gọi AI, 0 nháp, 0 tin Bot |
| Chi phí chạy mất kiểm soát | `daily_token_budget = 0` | Mọi lời gọi AI dừng ngay |
| Chưa được phép gửi dữ liệu khách ra ngoài | Xóa `omni_ai_allow_customer_data` | Lời gọi AI với dữ liệu thật bị chặn |

Nháp Pending đã tạo trước đó không gửi được khi bot đã Off. Bật lại KHÔNG tự gửi lại tin đã `Blocked`.

## 13. Sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Bật bot mà không có nháp/phân tích | Chưa có khóa; Q11 chưa mở; ngân sách = 0; ngắt mạch mở; worker `omni` không chạy; kho > 150 mục; bot đang tạm dừng với khách đó | Xem `Omni Bot Run` (trạng thái, cột lỗi), Error Log; mục 11 |
| Mọi nháp là "chuyển người" | Kho rỗng hoặc nhóm không khớp | Nhập kho thật (mục 2) |
| Bộ đề báo "chưa đo" cho mọi ca | Không có khóa AI hoặc không `allow_real` | Mục 1, mục 5 |
| Nhập kho báo "có ô còn trong ngoặc vuông" | Còn `[...]` chưa điền | Điền nội dung thật đã duyệt |
| Nhập kho báo nhóm chưa tồn tại | Chưa nhập nhóm | Mục 3 trước |
| Muốn bật Auto nhưng nút báo khóa hạ tầng đóng | Chưa đặt `omni_auto_send_allowed` (đúng thiết kế) | Chỉ khi Q1 chốt, mục 9 |
| Bot nhắc giá/cam kết | Kho có ghi giá, hoặc lỗi chốt chặn | Xóa giá khỏi kho; báo nhà phát triển kèm ví dụ (lỗi nghiêm trọng) |
| Sale báo nháp sai/bất thường | Kho lỗi thời | Quay lui mục 12; sửa kho; chạy lại bộ đề |

## 14. Đã xong khi

- [ ] Khóa AI nhập, `bot.smoke.run` thành công, Bot Run `Success`; Q11 đã được chủ dự án chốt trước khi bật `omni_ai_allow_customer_data`.
- [ ] Nhóm câu hỏi và kho tri thức **thật, đã duyệt** đã nhập (không còn `[...]`, không có giá); số mục ≤ 150.
- [ ] Bảng điểm do trưởng sale duyệt (nếu bật chấm điểm).
- [ ] Bộ đề có ca thật đã duyệt; cổng `phan_tich` **đo bằng AI thật và đạt** trước khi bật phân tích.
- [ ] Cổng `nhap` **đo bằng AI thật, đã chấm lại 20%, đạt**, có mã `Omni Eval Run`, trước khi bật Draft.
- [ ] Tuần chạy thử Draft với sale thật; `measure_dot3` cho thấy nháp đúng ≥ 85%, nghiêm trọng = 0.
- [ ] Chỉ khi Q1 chốt: `omni_auto_send_allowed` được người vận hành đặt; từng nhóm Auto được bật bằng nút sau khi đủ điều kiện số liệu; mỗi nhóm mới bật có người theo dõi báo cáo "Bot tự gửi".
- [ ] Cả nhóm biết cách tắt khẩn (mục 12) và ai có quyền làm.

## 15. Bot cho nhà thầu (V2-06)

- Câu lệnh v2 đã có trong kho mã nhưng **chưa dùng**; bản đang chạy là v1. Muốn thử v2 chỉ trong bộ đề: `prompt_versions={"analyze": "v2", "reply": "v2"}`. Ghim v2 cho production chỉ sau khi cổng `doi_cau_lenh` đo bằng AI thật (V2-07).
- Nhập nhóm + kho tri thức mẫu: `bench --site <site> execute omni_crm.bot.knowledge.import_b2b`. Nhóm vào chế độ Shadow; kho mẫu vào **không hoạt động**, chờ phòng kinh doanh điền, duyệt rồi bật từng mục trong Omni Knowledge Item (nhập lại không bật/tắt mục nào).
- Bảng điểm B2B: `import_rules` với `merge=True` (quy tắc cũ bị tắt, không xóa). Chấm lại Lead đang mở: patch `rescore_open_leads` (dry run mặc định, không báo Lead nóng).
- Trường mới trên Lead: MST khách nêu (chưa xác minh), bệnh viện, vướng mắc, vai trò. MST bot trích không tự gắn nhà thầu ở kênh công khai; sale xác nhận rồi gắn tay.
- Tắt khẩn: `bot_analyze_enabled = 0`, `bot_reply_mode = Off`, `lead_scoring_enabled = 0` như v1; nhóm có thể tắt riêng bằng `active = 0`.
