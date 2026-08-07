#!/bin/sh
set -eu

plugin_root=${CLAUDE_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
version=$(tr -d '[:space:]' < "$plugin_root/VERSION")

case "$(uname -s)" in
  Darwin) target_os=darwin ;;
  Linux) target_os=linux ;;
  *) echo "Faber supports Claude Code on macOS and Linux." >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64) target_arch=amd64 ;;
  arm64|aarch64) target_arch=arm64 ;;
  *) echo "Faber supports amd64 and arm64 processors." >&2; exit 1 ;;
esac

companion="$plugin_root/bin/faber-companion_${target_os}_${target_arch}"
if [ ! -x "$companion" ]; then
  echo "The bundled Faber companion is missing or not executable: $companion" >&2
  exit 1
fi

export FABER_PRODUCT=claude-code
export FABER_PRODUCT_VERSION=$version
exec "$companion" "$@"
