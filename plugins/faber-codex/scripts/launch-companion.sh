#!/bin/sh
set -eu

plugin_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
version=$(tr -d '[:space:]' < "$plugin_root/VERSION")

case "$(uname -s)" in
  Darwin) target_os=darwin ;;
  Linux) target_os=linux ;;
  *) echo "Faber for Codex supports macOS and Linux." >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64) target_arch=amd64 ;;
  arm64|aarch64) target_arch=arm64 ;;
  *) echo "Faber for Codex supports amd64 and arm64 processors." >&2; exit 1 ;;
esac

companion="$plugin_root/bin/faber-companion_${target_os}_${target_arch}"
if [ ! -x "$companion" ]; then
  echo "The bundled Faber companion is missing or not executable: $companion" >&2
  exit 1
fi

export FABER_PRODUCT=codex
export FABER_PRODUCT_VERSION=$version
export FABER_MCP_TOOL_CATALOG="$plugin_root/tools/catalog.json"
exec "$companion" "$@"
