#!/usr/bin/env bash
# Áp (hoặc kiểm, hoặc gỡ) patch giao diện Marketing Hub (ADR 0031) lên app `crm`, CHỒNG lên patch 0001 (tab Zalo, Messenger).
#
#   apply-marketing.sh [--check] [--revert] [CRM_DIR]
#
# Cần 0001 đã áp (apply.sh) và crm đúng bản trong CRM_VERSION. Không đụng 0001: production đang chạy 0001 thì chỉ cần
# script này rồi build.sh. Idempotent: 0002 đã áp thì thoát 0 không làm gì. Gỡ cả hai: gỡ 0002 (script này --revert) TRƯỚC.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$HERE/0001-zalo-tab.patch"
PATCH="$HERE/0002-marketing-hub.patch"
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
  exit 3
fi

if git apply --reverse --check "$PATCH" >/dev/null 2>&1; then
  if [ "$MODE" = revert ]; then
    git apply --reverse "$PATCH"
    echo "Đã gỡ patch Marketing khỏi mã nguồn crm (0001 giữ nguyên). Chạy build.sh để dựng lại giao diện."
  else
    echo "Patch Marketing đã được áp từ trước: không làm gì."
  fi
  exit 0
fi
if [ "$MODE" = revert ]; then
  echo "Patch Marketing chưa được áp (hoặc mã đã bị sửa khác đi): không có gì để gỡ." >&2
  exit 0
fi

if ! git apply --reverse --check "$BASE" >/dev/null 2>&1; then
  echo "TỪ CHỐI: patch 0001 (tab Zalo, Messenger) chưa được áp. Chạy apply.sh trước, rồi mới chạy script này." >&2
  exit 6
fi

# Tệp chỉ 0002 chạm tới (không thuộc 0001) không được có thay đổi chưa commit của người khác.
BASE_FILES="$(git apply --numstat "$BASE" | awk '{print $3}')"
OWN_FILES="$(git apply --numstat "$PATCH" | awk '{print $3}' | grep -vxF -f <(printf '%s\n' "$BASE_FILES") || true)"
DIRTY="$(printf '%s\n' "$OWN_FILES" | xargs -r git status --porcelain --untracked-files=all -- || true)"
if [ -n "$DIRTY" ]; then
  echo "TỪ CHỐI: các tệp patch Marketing chạm tới đang có thay đổi chưa commit:" >&2
  echo "$DIRTY" >&2
  exit 4
fi
if ! git apply --check "$PATCH"; then
  echo "TỪ CHỐI: patch Marketing không khớp mã crm hiện tại. Không sửa gì." >&2
  exit 5
fi
if [ "$MODE" = check ]; then
  echo "Kiểm xong: patch Marketing áp được sạch lên $WANT_TAG + 0001. (chưa sửa gì)"
  exit 0
fi
git apply "$PATCH"
echo "Đã áp patch Marketing lên crm $WANT_TAG. Bước tiếp: build.sh"
