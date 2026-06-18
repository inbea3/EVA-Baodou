#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

DATA_DIR="${RAILWAY_VOLUME_MOUNT_PATH:-${EVA_DATA_DIR:-$APP_DIR/data}}"
mkdir -p "$DATA_DIR/wechatbot" "$DATA_DIR/.eva/sessions" "$APP_DIR/.eva"

export EVA_HOME="${EVA_HOME:-$DATA_DIR/.eva}"
export WECHATBOT_CRED_PATH="${WECHATBOT_CRED_PATH:-$DATA_DIR/wechatbot/credentials.json}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1

if [[ -z "${EVA_API_KEY:-}" ]]; then
  echo "错误：未设置 EVA_API_KEY，请在 Railway Variables 中配置。"
  exit 1
fi

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "> 存储: Neon PostgreSQL (DATABASE_URL 已配置)"
  python -c "
import storage
try:
    storage.init_schema()
    print('> 数据库表已就绪')
except Exception as e:
    print(f'错误：{e}')
    raise
" || exit 1
else
  echo "> 存储: 本地文件 (未设置 DATABASE_URL，建议生产环境配置 Neon)"
  if [[ ! -f "$EVA_HOME/EVA.md" && -f "$APP_DIR/EVA.md.example" ]]; then
    cp "$APP_DIR/EVA.md.example" "$EVA_HOME/EVA.md"
    echo "> 已从 EVA.md.example 初始化 $EVA_HOME/EVA.md"
  fi
fi

echo "> EVA_HOME=$EVA_HOME"
echo "> WECHATBOT_CRED_PATH=$WECHATBOT_CRED_PATH"
echo "> PORT=${PORT:-8080} (Railway 健康检查)"
echo "> 启动微信 Bot..."

exec python -u bot.py
