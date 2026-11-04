#!/usr/bin/env bash
# Dựng lại giao diện crm sau khi áp patch tab Zalo (V3-01, ADR 0016).
#
#   build.sh [CRM_DIR]
#
# Cần Node >= 20.19 (yêu cầu của bộ công cụ: eslint 10, vitest 4). Tìm theo thứ tự: biến NODE_BIN_DIR, node trong PATH, ~/.nvm (bản >= 20.19 mới nhất).
# KHÔNG cài Node vào hệ thống. An toàn: sao lưu giao diện đang chạy (crm/public/frontend, crm/www/crm.html) trước khi
# build; build lỗi thì tự phục hồi bản sao lưu. Bản sao lưu để ở <crm>/.omni_zalo_backup/ (có thể xóa khi đã ổn).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRM_DIR="${1:-$(cd "$HERE/../../../crm" 2>/dev/null && pwd)}"
[ -d "$CRM_DIR/frontend" ] || { echo "Không thấy $CRM_DIR/frontend" >&2; exit 2; }

ok_node() { # $1 = đường dẫn node; thành công nếu >= 20.19
  local v; v="$("$1" -v 2>/dev/null | sed 's/^v//')" || return 1
  [ -n "$v" ] || return 1
  [ "$(printf '%s\n20.19.0\n' "$v" | sort -V | head -n1)" = "20.19.0" ]
}
NODE_DIR=""
if [ -n "${NODE_BIN_DIR:-}" ] && ok_node "$NODE_BIN_DIR/node"; then NODE_DIR="$NODE_BIN_DIR"
elif command -v node >/dev/null && ok_node "$(command -v node)"; then NODE_DIR="$(dirname "$(command -v node)")"
else
  for d in $(ls -d "$HOME"/.nvm/versions/node/v*/bin 2>/dev/null | sort -V -r); do
    if ok_node "$d/node"; then NODE_DIR="$d"; break; fi
  done
fi
[ -n "$NODE_DIR" ] || { echo "Không tìm thấy Node >= 20.19. Cài bằng nvm vào thư mục người dùng (nvm install 20) hoặc đặt NODE_BIN_DIR. Chưa sửa gì." >&2; exit 3; }
export PATH="$NODE_DIR:$PATH"
echo "Dùng Node $(node -v) tại $NODE_DIR"
command -v yarn >/dev/null || { echo "Thiếu yarn (npm i -g yarn trong thư mục Node ở trên)." >&2; exit 3; }

cd "$CRM_DIR"
BK="$CRM_DIR/.omni_zalo_backup"
rm -rf "$BK"; mkdir -p "$BK"
[ -d crm/public/frontend ] && cp -a crm/public/frontend "$BK/frontend"
[ -f crm/www/crm.html ] && cp -a crm/www/crm.html "$BK/crm.html"
restore() {
  echo "BUILD LỖI: phục hồi giao diện cũ từ $BK" >&2
  cd "$CRM_DIR"
  rm -rf crm/public/frontend
  [ -d "$BK/frontend" ] && cp -a "$BK/frontend" crm/public/frontend
  [ -f "$BK/crm.html" ] && cp -a "$BK/crm.html" crm/www/crm.html
}
trap 'restore; exit 1' ERR

cd frontend
[ -d node_modules ] || yarn install --frozen-lockfile
yarn build
trap - ERR
echo "Build xong. Xóa bộ nhớ đệm để trình duyệt nhận giao diện mới:  bench --site <site> clear-cache  (và tải lại trang CRM, Ctrl+Shift+R)."
