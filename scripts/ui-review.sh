#!/bin/sh
# Read-only Impeccable entrypoint. Never installs hooks or falls back to a global tool.
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
skill_dir="$project_root/.agents/skills/impeccable"
case "${1:-}" in
  context|detect|engine-probe|--version|--help) ;;
  *) echo 'Only read-only context, detect, engine-probe, --version and --help are allowed.' >&2; exit 2 ;;
esac
version=$(tr -d '[:space:]' < "$skill_dir/scripts/VERSION")
engine="$project_root/.impeccable/engine/bin/$version/impeccable"
if [ ! -x "$engine" ]; then
  echo 'Project engine missing. Follow docs/ui-audit/TOOLCHAIN.md; no automatic download or global fallback.' >&2
  exit 1
fi
case "$(uname -s):$(uname -m)" in
  Darwin:x86_64) asset=impeccable-darwin-x64 ;;
  Darwin:arm64) asset=impeccable-darwin-arm64 ;;
  Linux:x86_64) asset=impeccable-linux-x64 ;;
  Linux:aarch64) asset=impeccable-linux-arm64 ;;
  *) echo 'Unsupported platform; inspect toolchain setup manually.' >&2; exit 1 ;;
esac
expected=$(awk -v v="$version" -v a="$asset" '$1 == v && $2 == a {print $3}' "$skill_dir/scripts/engine-checksums.txt")
if command -v shasum >/dev/null 2>&1; then
  actual=$(shasum -a 256 "$engine"); actual=${actual%% *}
else
  actual=$(sha256sum "$engine"); actual=${actual%% *}
fi
if [ -z "$expected" ] || [ "$actual" != "$expected" ]; then
  echo 'Engine checksum mismatch; refusing to execute.' >&2; exit 1
fi
export IMPECCABLE_HOME="$project_root/.impeccable/engine"
export IMPECCABLE_SKILL_DIR="$skill_dir"
export IMPECCABLE_SELF="$skill_dir/scripts/impeccable"
export IMPECCABLE_HOOK_DISABLED=1
cd "$project_root"
exec "$engine" "$@"
