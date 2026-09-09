#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE="$PROJECT_DIR/native/PageCurlRenderer.swift"
OUTPUT="${1:-$PROJECT_DIR/build/page_curl/PageCurlRenderer}"
OUTPUT_DIR="$(dirname "$OUTPUT")"
MODULE_CACHE="$PROJECT_DIR/build/page_curl/module-cache"

SWIFTC="$(xcrun --find swiftc)"
SDK_PATH="$(xcrun --sdk macosx --show-sdk-path)"
TARGET="$(uname -m)-apple-macosx13.0"
mkdir -p "$OUTPUT_DIR"
mkdir -p "$MODULE_CACHE"

CLANG_MODULE_CACHE_PATH="$MODULE_CACHE" "$SWIFTC" \
  -O \
  -sdk "$SDK_PATH" \
  -target "$TARGET" \
  -module-cache-path "$MODULE_CACHE" \
  -Xcc "-fmodules-cache-path=$MODULE_CACHE" \
  -framework Foundation \
  -framework CoreGraphics \
  -framework CoreImage \
  -framework ImageIO \
  -framework UniformTypeIdentifiers \
  "$SOURCE" \
  -o "$OUTPUT"

echo "$OUTPUT"
