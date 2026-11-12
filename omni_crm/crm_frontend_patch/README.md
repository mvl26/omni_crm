# Patch giao diện tab "Zalo" cho Frappe CRM (V3-01, ADR 0016)

Ngoại lệ có chủ đích của quy tắc "không sửa app `crm`": chỉ **frontend**, chỉ bản **crm v1.73.0** (`3942bf65625f0602e41a15d8657298f83dd80b4b`, ghi ở `CRM_VERSION`). Lý do và rủi ro: `docs/adr/0016-tab-zalo-trong-giao-dien-crm.md`.

## Gồm gì

| Tệp | Việc |
| --- | --- |
| `0001-zalo-tab.patch` | `git diff` của crm v1.73.0: 17 tệp mới (`ZaloTab.vue`, `ZaloIcon.vue`, `composables/zalo.js`, `utils/zaloThread.js`, `tests/unit/zaloThread.test.js`; V3-05 thêm `AssistantTab.vue`, `composables/assistant.js`, `utils/assistantChat.js`, `tests/unit/assistantChat.test.js` cho tab **Trợ lý**; `utils/callNotes.js`, `tests/unit/callNotes.test.js` cho nội dung cuộc gọi; `composables/leadContact.js` cho nút **Tạo liên hệ**; V3-07 thêm nút **Xin số điện thoại** và dòng quỹ tin ngoài 48 giờ trong `ZaloTab.vue`; ADR 0023 thêm `MessengerTab.vue`, `MessengerIcon.vue`, `composables/messenger.js`, `utils/messengerThread.js`, `tests/unit/messengerThread.test.js` cho tab **Messenger**) và 6 tệp sửa vài dòng (`Activities/CallArea.vue` hiện nội dung cuộc gọi, ADR 0016 mục 14, `pages/Lead.vue`, `pages/Deal.vue`, `Activities.vue`, `ActivityHeader.vue`, `SidePanelLayout.vue` chỉ thêm CSS, ADR 0016 mục 12). Tab Messenger chỉ thêm dòng vào 4 tệp đã có trong danh sách, không thêm tệp crm bị sửa mới |
| `apply.sh` | Áp / kiểm (`--check`) / gỡ (`--revert`) patch. Idempotent. Từ chối nếu crm không đúng commit, nếu tệp đích đang bị sửa, hoặc patch không khớp |
| `build.sh` | Dựng lại giao diện (`yarn build`) bằng Node >= 20.19; sao lưu giao diện đang chạy và tự phục hồi nếu build lỗi |
| `CRM_VERSION` | tag và commit crm mà patch dành cho |

## Yêu cầu

- Node >= 20.19 (đã thử với 20.20.2) và yarn. Node 18 (Node mặc định của bench này) KHÔNG đủ. Không cài vào hệ thống: dùng nvm trong thư mục người dùng (`nvm install 20`); `build.sh` tự tìm trong PATH rồi `~/.nvm/versions/node/*`, hoặc đặt `NODE_BIN_DIR=/đường/dẫn/tới/bin`.
- `apps/crm` đúng commit trong `CRM_VERSION`, sạch ở 6 tệp bị sửa. (`frontend/node_modules` đã có từ lần cài crm; thiếu thì `build.sh` chạy `yarn install --frozen-lockfile`.)
- App `omni_crm` đã cài trên site (API `omni_crm.api.zalo_tab`, `omni_crm.api.messenger_tab`) và đã `bench migrate` (trường `zalo_tab_disabled`; ADR 0023 thêm `messenger_tab_disabled`, `messenger_in_activity_enabled`, `messenger_media_send_enabled`). Không có `omni_crm` (hoặc bản `omni_crm` cũ chưa có API Messenger) thì tab tương ứng tự ẩn.

## Cách áp trên một bench khác

```bash
cd <bench>/apps/omni_crm/omni_crm/crm_frontend_patch
./apply.sh --check          # chỉ kiểm, không sửa gì
./apply.sh                  # áp lên <bench>/apps/crm (hoặc: ./apply.sh /đường/dẫn/crm)
./build.sh                  # dựng giao diện (vài phút đến vài chục phút tùy máy)
bench --site <site> clear-cache
```

Sau đó tải lại trang CRM bằng Ctrl+Shift+R (CRM là PWA, có thể giữ bản cũ). Không cần `bench build` và không cần restart. Thời gian: `apply.sh` vài giây; `build.sh` xem báo cáo V3-01 (đo trên máy dev đang tải cao: nhiều phút).

## Rủi ro và quay lui

- `vite build` xóa thư mục `crm/public/frontend` rồi ghi mới. Nếu bị ngắt giữa chừng (Ctrl+C, mất điện, hết bộ nhớ) giao diện CRM của mọi người dùng hỏng cho tới khi build lại. `build.sh` đã chép bản đang chạy vào `<crm>/.omni_zalo_backup/` và tự phục hồi khi build **báo lỗi**; nếu tiến trình bị giết cứng thì phục hồi tay: `rm -rf crm/public/frontend && cp -a .omni_zalo_backup/frontend crm/public/frontend && cp -a .omni_zalo_backup/crm.html crm/www/crm.html`.
- Nên làm ngoài giờ làm việc, hoặc build thử ở một worktree riêng rồi mới chép.
- Gỡ hẳn: `./apply.sh --revert && ./build.sh`. Chỉ ẩn tab (không build lại): tích `zalo_tab_disabled` (tab Zalo) hoặc `messenger_tab_disabled` (tab Messenger) trong Omni Settings.
- Giao diện đã build nằm ngoài git của crm: `bench update` có thể ghi đè mã nguồn (mất tab sau lần build kế tiếp) hoặc báo xung đột ở 6 tệp bị sửa.

## Nâng cấp crm sau này

1. `./apply.sh --revert` (đưa 6 tệp về nguyên bản; tệp mới bị xóa).
2. Nâng cấp crm (`bench update --apps crm` hoặc cách của bạn).
3. Tạo lại patch cho bản mới (mục dưới); sửa tay phần xung đột nếu `git apply --check` báo (thường chỉ là `tabs` trong `Lead.vue`/`Deal.vue`).
4. Sửa `CRM_VERSION`, `./apply.sh`, `./build.sh`.

## Tạo lại patch (cho bản crm khác)

```bash
git -C <bench>/apps/crm worktree add --detach <bench>/worktrees/crm-x <tag-moi>
# áp tay các thay đổi (hoặc git apply --3way ...), thử build và test trong worktree:
cd <bench>/worktrees/crm-x/frontend && ln -s <bench>/apps/crm/frontend/node_modules node_modules
yarn build && npx vitest run && npx eslint <các tệp>
# xuất patch: tệp mới cần `git add -N` để có trong diff
cd .. && git add -N frontend/src frontend/tests && git diff -- frontend/src frontend/tests > <omni_crm>/crm_frontend_patch/0001-zalo-tab.patch
git worktree remove --force <bench>/worktrees/crm-x
```

Không đưa `auto-imports.d.ts`, `components.d.ts` hay thư mục build vào patch.

Worktree phải nằm ở `<bench>/worktrees/<tên>`: `src/socket.js` của crm đọc `../../../../sites/common_site_config.json`, đặt chỗ khác thì `vite build` báo "Could not resolve" (lỗi môi trường, không phải lỗi patch). vitest không cần điều này.

Số test hiện tại (`npx vitest run`, 2026-11-12, ADR 0026): **10 tệp, 207 test** (ADR 0023: 10 tệp, 192 test; trước tab Messenger: 9 tệp, 167 test).

## Nhắn từ trang Zalo OA

Tab có nút **Mở chat Zalo OA** (https://oa.zalo.me/chat, cửa sổ mới 480 px) (đủ uid+oaid thì chỉ nút và dòng "Mở chat với <tên>"; thiếu thì hiện hướng dẫn tìm tay, mã Zalo + **Sao chép**). Mở đúng hội thoại bằng `?uid=<mã Zalo>&oaid=<mã OA>` (oaid chữ thường; thiếu thông tin thì mở trang chung). Nhúng iframe KHÔNG khả thi (Chrome chặn, thử 2026-11-04). Tắt cả tab bằng `zalo_tab_disabled`.

Lưu ý: `build.sh` chỉ phục hồi khi build báo lỗi (không bắt Ctrl+C/kill) và thư mục `.omni_zalo_backup/` nằm trong `apps/crm`, chưa được crm ignore: xóa nó khi giao diện mới đã ổn.

## Tab Messenger (ADR 0023)

Tab **Messenger** cạnh tab Zalo trên trang Lead và Deal, chỉ hiện khi `omni_crm.api.messenger_tab.tab_state` trả `enabled` (công tắc `messenger_tab_disabled`; lời gọi lỗi thì ẩn). Logic nằm ở `MessengerTab.vue` và hàm thuần `utils/messengerThread.js` (vitest); gom bong bóng, giờ, thẻ tóm tắt của bot dùng lại `utils/zaloThread.js` bằng import, KHÔNG sửa `ZaloTab.vue` hay `zaloThread.js`.

- Đính kèm của khách là tệp đã lưu trong CRM: ảnh vẽ nội tuyến, video/âm thanh có trình phát (`<video controls>`, `<audio controls>`), CHỈ khi URL là `/files` hoặc `/private/files` sạch (cùng luật `isInlineImageUrl`, không `..`, `//`, khoảng trắng); tệp khác và URL http(s) chỉ là liên kết (`noopener noreferrer`); tệp không lưu được hiện "📎 tên (ghi chú)", không liên kết. Không `v-html`.
- Khung soạn: ô chữ, **Gửi qua Messenger**, nút 📎 chỉ khi máy chủ trả `compose.can_send_media` (công tắc `messenger_media_send_enabled`). Tệp tải lên CRM như tab Zalo (`upload_file`, riêng tư, gắn Lead/Deal đang mở) rồi gọi `send_attachment`; loại và cỡ kiểm ngay khi chọn theo `compose.media_accept`, `compose.media_max_mb` (máy chủ kiểm lại). Có lời nhắn thì máy chủ gửi thành tin riêng trước tệp. Không có xác nhận phí, quỹ tin hay nút xin số như Zalo. Cờ `sending` giữ suốt lúc tải tệp và gửi (không gửi đôi); chữ đã đi mà tệp bị chặn thì ô chữ được xóa để bấm lại không gửi chữ hai lần.
- Nút **Mở hộp thư Meta** mở `contact.inbox_url` (chỉ nhận `https://business.facebook.com/`, tab mới, `noopener,noreferrer`); liên kết thiếu hoặc lạ thì mở hộp thư chung của Meta Business Suite kèm nhắc chọn đúng Page.
- Mẫu tin nhắn (ADR 0026): nút **Mẫu tin** cạnh nút 📎, chỉ hiện khi `compose.can_send`. Lần đầu mở bảng chọn mới gọi `omni_crm.api.messenger_tab.get_templates` (`doctype`, `name` như `get_conversation`; một resource mỗi tab, không thăm dò; đang tải / lỗi tải hiện ngay trong bảng, lỗi có nút **Thử lại**). Máy chủ đã lọc, sắp xếp và điền sẵn nội dung theo Lead và người dùng. Mỗi mục: tiêu đề, dưới là ghi chú chữ mờ; mẫu lỗi (`error`) bị khóa và hiện "Mẫu lỗi: …"; hơn 5 mẫu thì có ô tìm theo tiêu đề, ghi chú, nội dung (không phân biệt hoa thường và dấu). Chưa có mẫu: "Chưa có mẫu tin nhắn" kèm liên kết Desk `/app/omni-chat-template` (tab mới, `noopener noreferrer`). Chọn mẫu CHỈ điền vào ô soạn (ô đang có chữ khác thì hỏi `window.confirm` như tab Zalo trước khi thay), con trỏ về cuối ô; KHÔNG tự gửi. Mẫu dài hơn `compose.max_chars` vẫn được điền: nút Gửi khóa và có dòng báo độ dài. Hàm thuần `templateList`, `filterTemplates`, `applyTemplate`, `overLimit` trong `utils/messengerThread.js` (vitest); resource ở `useMessengerTemplates` (`composables/messenger.js`). Không thêm tệp crm nào ngoài 4 tệp Messenger đã có.

## Kiểm sau khi build (làm bằng mắt, chưa ai làm trên site thật)

1. Mở một Lead có tin Zalo: thấy tab **Zalo** cuối dãy tab; hội thoại hiện bong bóng, khách bên trái.
2. Site đang sandbox: gửi thử một tin, bong bóng hiện bên phải với nhãn "Thử nghiệm (sandbox), chưa gửi tới khách".
3. Lead chỉ có email: khung soạn bị khóa kèm lý do.
4. Tích `zalo_tab_disabled`, tải lại: tab biến mất.
5. Bấm **Mở chat Zalo OA**: cửa sổ nhỏ mở trang oa.zalo.me/chat; khi thiếu uid/oaid: hướng dẫn tìm tay + **Sao chép** chép được mã Zalo; mở cửa sổ đúng hội thoại của khách (kiểm địa chỉ có `uid=` và `oaid=`).
6. (V3-05) Tin khách gửi ảnh/GIF/sticker từ CDN Zalo (host *.zdn.vn...) hiện thành ảnh thu nhỏ trong bong bóng, bấm mở ảnh gốc tab mới; tệp hiện tên tệp; host lạ hoặc ảnh lỗi tải chỉ hiện liên kết có nhãn. Làm bằng mắt, chưa ai làm.
7. (V3-09) Khách đã liên kết Zalo, đang trong 48 giờ: bấm 📎, chọn ảnh JPG/PNG/GIF hoặc tệp PDF/DOC/DOCX/CSV, gõ chú thích (không bắt buộc), Gửi qua Zalo. Bong bóng bên phải hiện ảnh thu nhỏ hoặc tên tệp; tệp xuất hiện trong tab Đính kèm của Lead. Tệp sai loại hoặc quá 5 MB bị từ chối ngay khi chọn. Công tắc `zalo_media_send_enabled` tắt: nút 📎 chỉ hiện với tài khoản Zalo thử nghiệm.
8. (ADR 0023) Mở một Lead có tin Messenger: thấy tab **Messenger** sau tab Zalo (cả trang Deal của Lead đó); khách bên trái, ảnh khách gửi hiện thu nhỏ, video/âm thanh phát được trong bong bóng, tệp khác là liên kết tải; tệp Meta không cho tải hiện "📎 tên (ghi chú)".
9. (ADR 0023) Trả lời bằng chữ trong 24 giờ: bong bóng bên phải; ngoài 24 giờ hoặc Lead không có Messenger: khung soạn khóa kèm lý do. Tích `messenger_media_send_enabled`: nút 📎 hiện; chọn ảnh/video/tệp (sai loại hoặc quá 25 MB bị từ chối ngay), gõ lời nhắn (không bắt buộc), Gửi qua Messenger: tệp xuất hiện ở tab Đính kèm của Lead, bong bóng hiện lời nhắn rồi tệp. Bấm **Mở hộp thư Meta**: tab mới mở hộp thư Meta Business Suite của Page.
10. (ADR 0023) Tích `messenger_tab_disabled`, tải lại: tab Messenger biến mất, tab Zalo không đổi.
11. (ADR 0026) Tạo vài mẫu ở Desk `/app/omni-chat-template` (một mẫu cố ý sai biến). Mở Lead có Messenger còn trong 24 giờ, bấm **Mẫu tin**: danh sách đúng thứ tự máy chủ, ghi chú chữ mờ, mẫu sai hiện "Mẫu lỗi: …" và không bấm được. Ô soạn trống, chọn một mẫu: chữ đã điền tên khách và tên người dùng, con trỏ ở cuối ô, tin CHƯA được gửi. Gõ chữ khác rồi chọn mẫu: hiện hộp hỏi; Hủy giữ nguyên chữ, OK thay bằng mẫu. Hơn 5 mẫu: ô tìm lọc được khi gõ không dấu (vd. "chao hoi"). Không có mẫu nào áp dụng: thấy "Chưa có mẫu tin nhắn" và liên kết mở Desk ở tab mới. Ngoài 24 giờ (khung soạn khóa): không có nút **Mẫu tin**.
