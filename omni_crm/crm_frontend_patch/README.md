# Patch giao diện tab "Zalo" cho Frappe CRM (V3-01, ADR 0016)

Ngoại lệ có chủ đích của quy tắc "không sửa app `crm`": chỉ **frontend**, chỉ bản **crm v1.73.0** (`3942bf65625f0602e41a15d8657298f83dd80b4b`, ghi ở `CRM_VERSION`). Lý do và rủi ro: `docs/adr/0016-tab-zalo-trong-giao-dien-crm.md`.

## Gồm gì

| Tệp | Việc |
| --- | --- |
| `0001-zalo-tab.patch` | `git diff` của crm v1.73.0: 5 tệp mới (`ZaloTab.vue`, `ZaloIcon.vue`, `composables/zalo.js`, `utils/zaloThread.js`, `tests/unit/zaloThread.test.js`) và 4 tệp sửa vài dòng (`pages/Lead.vue`, `pages/Deal.vue`, `Activities.vue`, `ActivityHeader.vue`) |
| `apply.sh` | Áp / kiểm (`--check`) / gỡ (`--revert`) patch. Idempotent. Từ chối nếu crm không đúng commit, nếu tệp đích đang bị sửa, hoặc patch không khớp |
| `build.sh` | Dựng lại giao diện (`yarn build`) bằng Node >= 20.19; sao lưu giao diện đang chạy và tự phục hồi nếu build lỗi |
| `CRM_VERSION` | tag và commit crm mà patch dành cho |

## Yêu cầu

- Node >= 20.19 (đã thử với 20.20.2) và yarn. Node 18 (Node mặc định của bench này) KHÔNG đủ. Không cài vào hệ thống: dùng nvm trong thư mục người dùng (`nvm install 20`); `build.sh` tự tìm trong PATH rồi `~/.nvm/versions/node/*`, hoặc đặt `NODE_BIN_DIR=/đường/dẫn/tới/bin`.
- `apps/crm` đúng commit trong `CRM_VERSION`, sạch ở 4 tệp bị sửa. (`frontend/node_modules` đã có từ lần cài crm; thiếu thì `build.sh` chạy `yarn install --frozen-lockfile`.)
- App `omni_crm` đã cài trên site (API `omni_crm.api.zalo_tab`) và đã `bench migrate` (trường `zalo_tab_disabled`). Không có `omni_crm` thì tab tự ẩn.

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
- Gỡ hẳn: `./apply.sh --revert && ./build.sh`. Chỉ ẩn tab (không build lại): tích `zalo_tab_disabled` trong Omni Settings.
- Giao diện đã build nằm ngoài git của crm: `bench update` có thể ghi đè mã nguồn (mất tab sau lần build kế tiếp) hoặc báo xung đột ở 4 tệp bị sửa.

## Nâng cấp crm sau này

1. `./apply.sh --revert` (đưa 4 tệp về nguyên bản; tệp mới bị xóa).
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

## Nhắn từ trang Zalo OA

Tab có nút **Mở chat Zalo OA** (https://oa.zalo.me/chat, cửa sổ mới 480 px) và khối tên/mã Zalo + **Sao chép**. Nhúng iframe KHÔNG khả thi (Zalo chặn, đã thử 2026-11-04). Tắt cả tab bằng `zalo_tab_disabled`.

## Kiểm sau khi build (làm bằng mắt, chưa ai làm trên site thật)

1. Mở một Lead có tin Zalo: thấy tab **Zalo** cuối dãy tab; hội thoại hiện bong bóng, khách bên trái.
2. Site đang sandbox: gửi thử một tin, bong bóng hiện bên phải với nhãn "Thử nghiệm (sandbox), chưa gửi tới khách".
3. Lead chỉ có email: khung soạn bị khóa kèm lý do.
4. Tích `zalo_tab_disabled`, tải lại: tab biến mất.
5. Bấm **Mở chat Zalo OA**: cửa sổ nhỏ mở trang oa.zalo.me/chat; **Sao chép** chép được mã Zalo; mở cửa sổ đúng trang chat.
