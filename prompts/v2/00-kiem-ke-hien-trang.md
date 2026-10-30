# V2-00 ★ · Kiểm kê hiện trạng và báo cáo khoảng cách v1 ↔ v2

**Phụ thuộc:** kit v1 đã chạy xong và merge vào `{{BASE_BRANCH}}`; đã gắn tag `v1-final`. **Thời lượng dự kiến:** nửa ngày. **Không viết code sản phẩm.**

```
Bạn là agent kiểm kê cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm, đã được xây bằng kit v1. Kit v2 nằm ở {{KIT_V2}}. Đọc trước: {{KIT_V2}}/README-v2.md, {{KIT_V2}}/docs/v2/CHANGELOG-v1-v2.md, {{KIT_V2}}/CLAUDE.md.

Nhiệm vụ: đưa tài liệu v2 vào repo mà không làm mất kết quả của v1, rồi lập báo cáo khoảng cách chính xác giữa code hiện có và đặc tả v2. Bạn KHÔNG sửa code sản phẩm ở bước này.

Bước 1 — Nhánh và mốc:
- Kiểm tra tag `v1-final` tồn tại; chưa có thì DỪNG và báo (không tự gắn tag).
- Tạo nhánh `v2/00-kiem-ke` từ `{{BASE_BRANCH}}`.

Bước 2 — Cập nhật tài liệu vào repo:
- Thay bằng bản của kit v2: CLAUDE.md, KIT_README.md (lấy từ README.md của kit v2), README-v2.md, docs/00–11, docs/13–15, docs/v2/, prompts/, eval/README.md, eval/rubric.md, mau/.
- docs/12-quyet-dinh-mo.md: GỘP, không ghi đè. Giữ nguyên mọi dòng ở mục C (Đã xác nhận) và mục D (Phát hiện) của repo. Ở mục A và B: cập nhật trạng thái theo kit v2 và thêm các dòng mới (V2-Q12–V2-Q15, G14–G16); quyết định nào repo đã ghi là đã chốt thì giữ kết quả của repo.
- GIỮ NGUYÊN: docs/adr/, docs/nghiem-thu/, docs/huong-dan/, eval/cases/, eval/reports/, mọi tệp agent v1 tạo mà kit không có.
- CLAUDE.md: trước khi thay, so với bản kit v1 gốc; nội dung agent v1 đã tự thêm (lệnh, đường dẫn, quy ước thực tế) → chép vào cuối bản mới dưới mục "Ghi chú từ v1".
- README.md của app (nếu có) giữ nguyên; README của kit đặt thành KIT_README.md.
- eval/mau-ca-kiem-thu.jsonl: thay bằng bản v2 (đây chỉ là mẫu định dạng; bộ đề thật nằm ở eval/cases/ và DB, giữ nguyên).
- Commit riêng: `docs: cập nhật đặc tả kit v2`.

Bước 3 — Trạng thái kỹ thuật:
- `bench --site {{SITE_TEST}} migrate` và `run-tests --app omni_crm`: ghi số test, số qua, thời gian.
- Liệt kê DocType, custom field (fixtures), hook, scheduler, công tắc thực sự có trong code.
- Đọc mọi ADR trong docs/adr/ và mục D của docs/12: ghi các quyết định v1 có thể mâu thuẫn với đặc tả v2.
- Đọc báo cáo nghiệm thu v1 (docs/nghiem-thu/): ghi cổng nào "chưa đo", lỗi còn mở, quyết định agent v1 tự đưa ra (ví dụ không dừng ở cổng D3-01).

Bước 4 — Báo cáo khoảng cách `docs/v2/gap-report.md`:
Một bảng duy nhất, mỗi dòng một yêu cầu của đặc tả v2. Duyệt lần lượt docs/02 (mọi DocType, trường, quyền), docs/03–09, docs/13, docs/14, docs/15, docs/10 (kịch bản test). Cột:
| Mã | Yêu cầu (trích ngắn + tham chiếu mục) | Hiện trạng: Có-đúng / Có-lệch / Thiếu / Không áp dụng | Bằng chứng (tệp:dòng, tên test) | Việc cần làm | Prompt v2 xử lý (V2-01…V2-08) | Rủi ro dữ liệu (có/không) |
- "Có-đúng" chỉ khi có test chứng minh. Không có test → "Có-lệch" với việc cần làm "viết test".
- Mọi dòng "Có-lệch" do ADR v1 có chủ đích: ghi ADR đó và đề xuất giữ hay đổi.
- Cuối báo cáo: (a) danh sách việc cho V2-01 (sửa lệch và nợ của phần đã có), (b) với V2-02…V2-06: phần nào đã có sẵn để bỏ qua, (c) các quyết định anh cần chốt trước khi chạy tiếp, (d) ước lượng thời lượng từng prompt v2 sau khi trừ phần đã có.

Bước 5 — Dữ liệu: đếm bản ghi hiện có trên {{SITE_STAGING}} (CRM Lead, Omni Identity, Omni Inbound Message, Omni Call, Omni Bot Draft…) để V2-02 biết quy mô chuyển đổi. Chỉ đọc.

Hoàn thành khi: tài liệu v2 đã vào repo đúng quy tắc gộp; gap-report.md đủ mọi yêu cầu của đặc tả v2, mỗi dòng có bằng chứng; test suite chạy được (ghi kết quả thật, kể cả nếu đỏ).

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4, thêm mục "Quyết định cần anh chốt trước V2-01".
```

## Người duyệt kiểm tra (cổng 1)

1. Mở `docs/v2/gap-report.md`: đọc phần cuối (danh sách việc, quyết định cần chốt).
2. Mở `docs/12`: các dòng ở mục C, D của v1 vẫn còn.
3. Đồng ý danh sách việc rồi mới chạy V2-01.
