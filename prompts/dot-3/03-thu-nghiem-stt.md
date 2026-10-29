# D3-03 · Thử nghiệm chuyển giọng nói (cổng quyết định)

**Phụ thuộc:** Đợt 2 đã phát hành (có ghi âm thật). Có thể song song với D3-01. **Thời lượng dự kiến:** 1,5 ngày.
**Cần anh chốt trước:** Q11 ở `docs/12` cho phép gửi ghi âm khách sang dịch vụ chuyển giọng nói. **Cần anh cung cấp:** danh sách 2–3 nhà cung cấp chuyển giọng nói tiếng Việt muốn thử và khóa API dùng thử (nhập vào biến môi trường trên máy thử, không gửi trong chat); 20 ghi âm thật đã được phép dùng nội bộ; người gõ tay bản chuẩn cho ít nhất 5 ghi âm.

```
Bạn là agent phát triển cho app Frappe `omni_crm` tại {{BENCH}}/apps/omni_crm. Đọc trước: CLAUDE.md, docs/08-bot-ai.md mục 9, docs/07 mục 2.4, docs/12 mục A (Q3, Q7).

Nhiệm vụ: đo chất lượng chuyển giọng nói tiếng Việt trên ghi âm thật của công ty để quyết định có làm tóm tắt cuộc gọi ở Đợt 3 hay không. Đây là thử nghiệm: KHÔNG bật gì trên production.

Việc cần làm:
1. `stt/base.py`: interface `STTProvider.transcribe(audio, mime, *, stereo) -> Transcript(text, segments, confidence)` đúng docs/08 mục 9.
2. Adapter cho từng nhà cung cấp anh liệt kê: {{DANH_SACH_STT}}. Đọc tài liệu chính thức từng bên (WebFetch), ghi link vào docs/12. Không tự đăng ký tài khoản.
3. Script `tests/stt_trial/run_trial.py`: đọc thư mục ghi âm {{THU_MUC_GHI_AM}} (ngoài repo), chạy mọi nhà cung cấp, lưu kết quả JSON ngoài repo; tính tỉ lệ lỗi từ (WER, sau bỏ dấu câu, chữ thường) trên các ghi âm có bản chuẩn; đo thời gian xử lý và chi phí ước tính theo bảng giá công bố.
4. Với mỗi nhà cung cấp, chạy bot phân tích (D2-05) trên văn bản thu được của 20 ghi âm và so trường trích xuất với bản chuẩn/nhận định của người duyệt (xuất bảng để người duyệt đánh dấu đúng/sai).
5. Viết `docs/nghiem-thu/stt-thu-nghiem.md`: bảng so sánh (WER, phân biệt người nói nếu có, tốc độ, chi phí/phút, điều khoản dữ liệu), kết luận theo ngưỡng đề xuất {{NGUONG_STT}} (mặc định nếu anh không đổi: **làm D3-04 nếu có nhà cung cấp WER ≤ 25% và trường trích xuất đúng ≥ 80% trên ghi âm**); nếu không đạt → đề xuất hoãn.

Ràng buộc: ghi âm và văn bản thu được không commit vào repo, không gửi ra ngoài các nhà cung cấp đang thử; xóa kết quả tạm sau khi xong (giữ bảng số liệu).

Hoàn thành khi: báo cáo thử nghiệm có số liệu thật và kết luận rõ; adapter của nhà cung cấp được đề xuất có test với tệp âm thanh mẫu nhỏ (mock HTTP).

Kết thúc bằng báo cáo theo mẫu ở prompts/chung/00-cach-dung-prompt.md mục 4.
```

## Người duyệt kiểm tra (cổng quyết định)

1. Đọc `docs/nghiem-thu/stt-thu-nghiem.md`. Đạt ngưỡng → chạy D3-04. Không đạt → bỏ qua D3-04; Đợt 3 chỉ gồm bot nháp và tự động theo nhóm.
2. Điều khoản dữ liệu của nhà cung cấp được chọn phù hợp chính sách công ty (Q7).
