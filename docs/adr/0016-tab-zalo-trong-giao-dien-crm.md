# 0016 · Tab "Zalo" (bong bóng chat + khung trả lời) trong trang Lead/Deal của CRM

- Ngày: 2026-11-04
- Trạng thái: Đã chấp nhận (chủ dự án chọn rõ)
- Prompt: V3-01

## Bối cảnh
ADR 0004 chọn "C + A": tin Zalo là `Communication` (`Chat`) nằm trong tab Hoạt động/Email của Lead, còn trả lời nằm ở Hộp thư Omni. Người dùng xem thực tế trên site thật và thấy: tin Zalo hiện như thẻ email, người gửi là mã số thô (`2716764713514648383 <null>`), "Đến:" trống, nút trả lời kiểu email gây nhầm. Sale muốn đọc và trả lời Zalo ngay trong trang Lead/Deal như tab WhatsApp của CRM.

Tab hội thoại mới không làm được bằng Custom Field, hook, Property Setter hay API: giao diện CRM là SPA Vue đã dựng sẵn, danh sách tab và vùng nội dung nằm trong mã frontend của app `crm` (`Lead.vue`, `Deal.vue`, `Activities.vue`, `ActivityHeader.vue`).

## Quyết định
1. **Ngoại lệ có chủ đích của quy tắc "không sửa app `crm`" (CLAUDE.md mục 2)**: chỉ phần **giao diện (frontend)** của `crm`, chỉ trên bản **v1.73.0** (commit `3942bf65625f0602e41a15d8657298f83dd80b4b`). Không sửa Python/DocType/API của `crm`, không sửa `frappe`. Mọi thứ khác (API, quyền, guard gửi, công tắc) vẫn nằm trong `omni_crm`.
2. **Không sửa tay trong `apps/crm`.** Thay đổi được giữ dạng **tệp patch** trong `omni_crm/crm_frontend_patch/` (`0001-zalo-tab.patch`, `apply.sh`, `build.sh`, `README.md`), tạo bằng `git diff` từ một worktree của crm ở đúng tag. Áp lại được trên bench khác; gỡ được (`apply.sh --revert`).
3. **Patch tối thiểu**: logic nằm trong tệp MỚI (`ZaloTab.vue`, `utils/zaloThread.js`, `composables/zalo.js`, `ZaloIcon.vue`, test); chỉ 4 tệp có sẵn bị sửa vài dòng (`pages/Lead.vue`, `pages/Deal.vue`: thêm một mục tab; `Activities.vue`: hiện `ZaloTab` khi tab là Zalo; `ActivityHeader.vue`: không hiện nút "New" mặc định ở tab Zalo).
4. **Mọi dữ liệu và việc gửi đi qua API whitelisted của `omni_crm`** (`omni_crm.api.zalo_tab`: `tab_state`, `get_conversation`, `send`), bọc `inbox.thread_data` và `inbox.send_zalo` (tách ra từ `get_thread`/`send_reply`, hành vi cũ không đổi). Frontend không lặp lại logic gửi hay guard; guard thật (khung 48 giờ/7 ngày, hạn mức, từ chối liên hệ, sandbox, độ dài) chạy ở máy chủ, giao diện chỉ hiển thị kết quả.
5. **Quyền (đã đọc `crm/permissions/org_hierarchy.py`, `hooks.py`)**: CRM Lead và CRM Deal đều có `has_permission` theo chủ sở hữu/được giao/phân cấp, và hai tài liệu có thể có chủ khác nhau. Hội thoại thuộc Lead (khóa `Omni Inbound/Outbound Message.lead`), nên API đòi **cả hai**: quyền đọc (xem) hoặc ghi (gửi) trên chính tài liệu đang mở **và** trên Lead gốc theo đúng quy tắc của Hộp thư (`check_lead_access`/`check_lead_write`). Chủ Deal mà không có quyền trên Lead gốc thì không xem được hội thoại: không mở rộng quyền xem Hộp thư. Deal tạo tay (không có `lead`) thì tab rỗng, khung soạn khóa kèm giải thích.
6. **Chỉ kênh Zalo OA.** `get_conversation` lọc `channel_type = "Zalo OA"`, `send` luôn gọi kênh Zalo OA (người nhận là định danh Zalo mới nhất của Lead, kể cả khi Lead đã thành Deal). Lead chỉ có email: khung soạn khóa, không gửi email. Lead có cả Zalo và email: chỉ gửi qua Zalo.
7. **Công tắc** `Omni Settings.zalo_tab_disabled` (Check, cực tính ngược, mặc định hiện; `flags.zalo_tab_enabled`). Tắt thì `tab_state` trả `enabled = false` và tab không hiện; Hộp thư và dòng thời gian không bị ảnh hưởng. Nếu `omni_crm` không cài trên site, lời gọi `tab_state` lỗi và tab không hiện (crm đã vá vẫn chạy bình thường).
8. **An toàn hiển thị**: nội dung khách chỉ hiện bằng nội suy văn bản (không `v-html`); đính kèm là liên kết chỉ khi URL `http(s)://` (lọc ở máy chủ rồi lọc lại ở client; đường `/files/` của Hộp thư bị bỏ ở tab này), `rel="noopener noreferrer"`.
9. **Bổ sung (OA gói Cơ bản, -224):** tab có nút **Mở chat Zalo OA** (`window.open` với `noopener,noreferrer`) mở đúng hội thoại bằng `https://oa.zalo.me/chat?uid=<mã Zalo>&oaid=<mã OA>` (`oaid` chữ thường; uid từ `Omni Identity.external_id`, oaid từ `Omni Channel.zalo_oa_id`; chỉ chuỗi số, thiếu thì mở `https://oa.zalo.me/chat` chung), luồng chính chỉ có nút và dòng "Mở chat với <tên khách>"; hướng dẫn tìm tay + mã Zalo + Sao chép chỉ hiện khi thiếu uid hoặc oaid (fallback); lý do chưa có tên Zalo hiện nhỏ bên dưới; API `get_conversation` trả khối `contact`, rỗng khi khách từ chối liên hệ hoặc không có định danh Zalo). **Nhúng iframe `oa.zalo.me/chat` KHÔNG khả thi (Chrome chặn, thử 2026-11-04) nên không làm.** `errors.py` thêm `-224` = PERMANENT "Gói OA hiện tại không hỗ trợ gửi tin qua API; hãy nhắn từ trang Zalo OA". `worker_hooks.fetch_profile` lưu lý do không lấy được tên Zalo (bộ nhớ đệm, không đổi DocType) và có lệnh tay `channels/zalo/profile_refresh.py`. Khung gửi qua API giữ nguyên nên vẫn chạy khi OA nâng gói. Không thêm tệp crm nào ngoài danh sách cũ.
10. Làm kèm (mục A của V3-01): tên người gửi tin Zalo vào dùng `Omni Identity.display_name`, rồi tên Lead/Deal, rồi mã rút gọn (`Zalo OA …8383`); sửa nguyên nhân gốc là worker ghi `identity` lên tin SAU khi ghi dòng thời gian nên lần đầu không có tên định danh. Lệnh sửa tin cũ chạy tay: `omni_crm.ops.backfill_sender.run` (dry-run mặc định).

## Rủi ro
- **Nâng cấp crm**: `git pull`/`bench update` trên `apps/crm` có thể xung đột với các tệp bị patch, hoặc ghi đè và làm mất tab (giao diện đã build nằm ngoài git). Patch chỉ áp lên đúng `v1.73.0`; `apply.sh` từ chối bản khác.
- **Build lỗi giữa chừng** làm hỏng giao diện CRM của mọi người dùng (Vite xóa thư mục build trước khi ghi).
- Tab chỉ có cho giao diện máy tính (Lead/Deal); trang di động (`MobileLead.vue`, `MobileDeal.vue`) chưa có.
- `get_docinfo` của CRM vẫn chỉ nạp 21 Communication; tab Zalo đọc từ Omni Inbound/Outbound Message (tối đa 300 tin mỗi loại), không bị giới hạn đó.

- **Các điểm còn lại sau review (đã biết, chấp nhận):** (a) `get_conversation` gọi cả `inbox.thread_data` (đo ~160 truy vấn, ~131 ms) dù tab chỉ cần phần Zalo; chấp nhận để dùng một nguồn dữ liệu và guard, tối ưu sau nếu cần. (b) `build.sh` chỉ phục hồi khi build BÁO LỖI (trap ERR), không bắt INT/TERM: Ctrl+C hoặc bị giết giữa chừng thì phải phục hồi tay (lệnh có trong README). (c) Thư mục sao lưu `.omni_zalo_backup/` nằm trong `apps/crm` và chưa nằm trong `.gitignore` của crm (làm `git status` của crm bẩn; xóa khi build đã ổn). (d) `send` không có idempotency phía máy chủ: bấm gửi hai lần ở hai tab/lần gọi khác nhau tạo hai tin; giao diện chỉ chặn trong cùng một tab (cờ `sending` giữ đến khi xong, kể cả bước xác nhận phí).

## Cách giảm rủi ro
- Patch nhỏ, ghim đúng commit; `apply.sh` kiểm commit, kiểm patch khớp, từ chối nếu tệp đích đang bị sửa; idempotent (`--check`, `--revert`).
- `build.sh` sao lưu giao diện đang chạy trước khi build và tự phục hồi nếu build lỗi; build thử trước ở worktree riêng, không build thẳng trên bench thật.
- Quy trình nâng cấp crm (README.md trong `crm_frontend_patch/`): gỡ patch, nâng cấp, kiểm patch có áp được lên bản mới (`git apply --check`), tạo lại patch cho bản mới rồi mới áp và build.
- Công tắc `zalo_tab_disabled` tắt tab tức thì mà không cần build lại; muốn bỏ hẳn thì `apply.sh --revert` + `build.sh`.
- Hộp thư Omni vẫn đầy đủ (kể cả xin SĐT, duyệt nháp bot) nên sale không phụ thuộc vào tab.

## Hệ quả
- CLAUDE.md của kit vẫn giữ quy tắc cũ; ngoại lệ chỉ áp cho phạm vi ADR này. Mọi thay đổi frontend `crm` khác cần ADR mới.
- Tài liệu cập nhật: docs/06 mục 8, docs/09, docs/12 (mục "Đã xác nhận"), hướng dẫn 02, 08, 13, tong-hop-he-thong, nhật ký điều phối.
