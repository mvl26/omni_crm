#!/usr/bin/env bash
# Áp (hoặc kiểm, hoặc gỡ) các patch giao diện của omni_crm lên app `crm` (V3-01 ADR 0016; V3-10 ADR 0028).
#
#   apply.sh [--check] [--revert] [CRM_DIR]
#
# CRM_DIR mặc định: <bench>/apps/crm (bench suy từ vị trí của app omni_crm). Idempotent: patch đã áp thì thoát 0 không làm gì.
# Chỉ chạy trên đúng bản crm ghi trong CRM_VERSION; khác bản thì từ chối (không đoán).
# Chỉ sửa mã nguồn frontend (git apply). KHÔNG build: chạy build.sh sau đó.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Các patch áp theo thứ tự tên (0001 tab Zalo..., 0002 Marketing Hub - ADR 0028); gỡ theo thứ tự ngược lại.
mapfile -t PATCHES < <(ls "$HERE"/[0-9][0-9][0-9][0-9]-*.patch | sort)
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

applied() { git apply --reverse --check "$1" >/dev/null 2>&1; }

if [ "$MODE" = revert ]; then
  for (( i=${#PATCHES[@]}-1; i>=0; i-- )); do
    P="${PATCHES[$i]}"; N="$(basename "$P")"
    if applied "$P"; then
      git apply --reverse "$P"
      echo "Đã gỡ $N khỏi mã nguồn crm."
    else
      echo "$N chưa được áp (hoặc mã đã bị sửa khác đi): bỏ qua." >&2
    fi
  done
  echo "Chạy build.sh để dựng lại giao diện."
  exit 0
fi

TODO=()
for P in "${PATCHES[@]}"; do
  N="$(basename "$P")"
  if applied "$P"; then
    echo "$N đã được áp từ trước: không làm gì."
    continue
  fi
  # Các tệp patch chạm vào không được có thay đổi chưa commit của người khác.
  DIRTY="$(git apply --numstat "$P" | awk '{print $3}' | xargs -r git status --porcelain --untracked-files=all -- || true)"
  if [ -n "$DIRTY" ]; then
    echo "TỪ CHỐI ($N): các tệp patch chạm tới đang có thay đổi chưa commit:" >&2
    echo "$DIRTY" >&2
    exit 4
  fi
  if ! git apply --check "$P"; then
    echo "TỪ CHỐI ($N): patch không khớp mã crm hiện tại (dù cùng commit). Không sửa gì." >&2
    exit 5
  fi
  TODO+=("$P")
done
if [ "$MODE" = check ]; then
  echo "Kiểm xong: ${#TODO[@]} patch áp được sạch lên $WANT_TAG. (chưa sửa gì)"
  exit 0
fi
for P in "${TODO[@]}"; do
  git apply "$P"
  echo "Đã áp $(basename "$P") lên crm $WANT_TAG."
done
echo "Bước tiếp: build.sh"
