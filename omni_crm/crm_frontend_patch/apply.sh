#!/usr/bin/env bash
# Áp (hoặc kiểm, hoặc gỡ) patch giao diện tab Zalo lên app `crm` (V3-01, ADR 0016).
#
#   apply.sh [--check] [--revert] [CRM_DIR]
#
# CRM_DIR mặc định: <bench>/apps/crm (bench suy từ vị trí của app omni_crm). Idempotent: patch đã áp thì thoát 0 không làm gì.
# Chỉ chạy trên đúng bản crm ghi trong CRM_VERSION; khác bản thì từ chối (không đoán).
# Chỉ sửa mã nguồn frontend (git apply). KHÔNG build: chạy build.sh sau đó.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH="$HERE/0001-zalo-tab.patch"
read -r WANT_TAG WANT_SHA < "$HERE/CRM_VERSION"
MODE=apply
CRM_DIR=""
for arg in "$@"; do
  case "$arg" in
    --check) MODE=check ;;
    --revert) MODE=revert ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) CRM_DIR="$arg" ;;
  esac
done
if [ -z "$CRM_DIR" ]; then
  CRM_DIR="$(cd "$HERE/../../../crm" 2>/dev/null && pwd)" || { echo "Không suy ra được thư mục app crm; truyền CRM_DIR." >&2; exit 2; }
fi
[ -d "$CRM_DIR/.git" ] || [ -f "$CRM_DIR/.git" ] || { echo "$CRM_DIR không phải kho git của app crm." >&2; exit 2; }
cd "$CRM_DIR"

HEAD_SHA="$(git rev-parse HEAD)"
if [ "$HEAD_SHA" != "$WANT_SHA" ]; then
  echo "TỪ CHỐI: crm đang ở $HEAD_SHA, patch chỉ dành cho $WANT_TAG ($WANT_SHA)." >&2
  echo "Nâng cấp crm: gỡ patch trước (--revert), nâng cấp, rồi làm lại patch cho bản mới (xem README.md)." >&2
  exit 3
fi

if git apply --reverse --check "$PATCH" >/dev/null 2>&1; then
  if [ "$MODE" = revert ]; then
    git apply --reverse "$PATCH"
    echo "Đã gỡ patch tab Zalo khỏi mã nguồn crm. Chạy build.sh để dựng lại giao diện."
  else
    echo "Patch tab Zalo đã được áp từ trước: không làm gì."
  fi
  exit 0
fi
if [ "$MODE" = revert ]; then
  echo "Patch chưa được áp (hoặc mã đã bị sửa khác đi): không có gì để gỡ." >&2
  exit 0
fi

# Các tệp patch chạm vào không được có thay đổi chưa commit của người khác.
DIRTY="$(git apply --numstat "$PATCH" | awk '{print $3}' | xargs -r git status --porcelain --untracked-files=all -- || true)"
if [ -n "$DIRTY" ]; then
  echo "TỪ CHỐI: các tệp patch chạm tới đang có thay đổi chưa commit:" >&2
  echo "$DIRTY" >&2
  exit 4
fi
if ! git apply --check "$PATCH"; then
  echo "TỪ CHỐI: patch không khớp mã crm hiện tại (dù cùng commit). Không sửa gì." >&2
  exit 5
fi
if [ "$MODE" = check ]; then
  echo "Kiểm xong: patch áp được sạch lên $WANT_TAG. (chưa sửa gì)"
  exit 0
fi
git apply "$PATCH"
echo "Đã áp patch tab Zalo lên crm $WANT_TAG. Bước tiếp: build.sh"
