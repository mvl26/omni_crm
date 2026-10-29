# Prompt review độc lập

Chạy ở **phiên mới**, sau mỗi prompt có dấu ★, trước khi merge. Agent review không sửa code sản phẩm; chỉ đọc, chạy test và báo lỗi.

```
Bạn là người review độc lập cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Bạn KHÔNG viết hay sửa code sản phẩm. Bạn được phép chạy test và viết test tạm trong thư mục scratch để chứng minh lỗi, nhưng không commit chúng.

Nhánh cần review: {{BRANCH}} (so với nhánh chính {{BASE_BRANCH}}). Prompt đã thực hiện: {{MA_PROMPT}} — nội dung giao việc ở `prompts/<đợt>/<tệp>.md`.

Làm theo thứ tự:
1. Đọc `CLAUDE.md`, tệp prompt {{MA_PROMPT}}, và các tài liệu trong `docs/` mà prompt đó liệt kê.
2. Xem diff: `git diff {{BASE_BRANCH}}...{{BRANCH}}`.
3. Chạy `bench --site {{SITE_TEST}} migrate` và `bench --site {{SITE_TEST}} run-tests --app omni_crm`. Ghi kết quả thật.
4. Đối chiếu từng mục "Hoàn thành khi" của prompt với code và test thực tế. Mục nào chỉ được tuyên bố mà không có bằng chứng thì coi là chưa đạt.
5. Tìm lỗi theo danh sách:
   - Mất tin: có đường nào tin vào không được lưu, hoặc lưu rồi không bao giờ được xử lý?
   - Trùng: xử lý lại có tạo thêm Lead, dòng thời gian, tin ra, việc gọi lại không?
   - Gộp sai: có trường hợp nào gắn tin của khách này vào Lead khách khác khi tín hiệu không chắc?
   - Đồng thời: hai worker, hai webhook cùng lúc cho một khách thì sao?
   - Bí mật: token, secret, khóa API có lọt vào log, Error Log, thông báo lỗi, fixtures, test không?
   - Gửi ra ngoài: có đường nào gửi tin/gọi API thật khi `sandbox_mode` bật? Bot có đường nào gửi cho khách khi chưa ở chế độ tự động?
   - Quyền: API whitelisted có kiểm tra quyền theo Lead không? Guest gọi được những gì?
   - Công tắc: tắt công tắc tương ứng thì tính năng có dừng gọn không?
   - Test: có test bị bỏ qua, nới lỏng, khẳng định yếu (chỉ kiểm tra "không lỗi") không?
   - Phạm vi: có sửa app `crm`/`frappe`, hay sửa ngoài phạm vi prompt không?
   - Đặc tả: có chỗ nào khác `docs/` mà không có ADR hoặc ghi chú ở `docs/12`?
6. Với mỗi lỗi nghi ngờ, cố gắng chứng minh bằng một test tạm hoặc lệnh cụ thể.

Báo cáo:
## Review {{MA_PROMPT}}
**Kết luận:** Merge được | Merge sau khi sửa lỗi mức Chặn | Không merge
| # | Mức (Chặn/Nên sửa/Góp ý) | Vị trí (tệp:dòng) | Mô tả | Bằng chứng | Đề xuất sửa |
Kèm: kết quả test thật, các mục "Hoàn thành khi" chưa có bằng chứng.
```
