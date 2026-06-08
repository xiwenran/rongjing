#!/bin/bash
# 双击入口。真实打包逻辑放在 scripts/package_rongjing.sh，便于用 .app 启动器稳定调用。

DIR="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "$DIR/scripts/package_rongjing.sh" "$@"
