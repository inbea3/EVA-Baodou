from wechatbot import WeChatBot
import os
import re
import sys
import subprocess
import threading
import time
from pathlib import Path

import storage

ANSI_RE = re.compile(r'\033\[[0-9;]*m')

APP_DIR = Path(__file__).resolve().parent
EVA_SCRIPT = APP_DIR / "eva.py"
EVA_TASK_TIMEOUT = int(os.environ.get("EVA_TASK_TIMEOUT", "600"))


def eva_args(*extra):
    return [sys.executable, str(EVA_SCRIPT), *extra]


def run_cli(args, timeout: int = EVA_TASK_TIMEOUT):
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=str(APP_DIR),
            timeout=timeout,
            shell=False,
            env=env,
        )
        output = f"{result.stdout}"
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"执行失败：EVA 任务超时（>{timeout}s）"
    except Exception as e:
        return f"执行失败：{str(e)}"


def clean_eva_output(out: str):
    eva_s = "[*] EVA:"
    if eva_s in out:
        out = out[out.rindex(eva_s)+len(eva_s):]

    session_s = "> 会话已保存到："
    if session_s in out:
        out = out[:out.rindex(session_s)]

    return ANSI_RE.sub('', out).strip()


def _cred_path() -> str:
    if path := os.environ.get("WECHATBOT_CRED_PATH"):
        return path
    if data_dir := os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("EVA_DATA_DIR"):
        return str(Path(data_dir) / "wechatbot" / "credentials.json")
    return "~/.wechatbot/credentials.json"


def _log_qr(url: str):
    print(f"\n{'=' * 60}\n请用微信扫描登录:\n{url}\n{'=' * 60}\n", flush=True)


def _sync_creds_after_login(cred_path: str):
    def worker():
        for _ in range(15):
            time.sleep(2)
            if storage.save_wechat_creds(cred_path):
                print("> 微信凭证已同步到数据库", flush=True)
                return
    threading.Thread(target=worker, daemon=True).start()


def main():
    storage.init_schema()
    cred_path = _cred_path()
    Path(cred_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

    if storage.restore_wechat_creds(cred_path):
        print("> 已从数据库恢复微信凭证", flush=True)

    bot = WeChatBot(
        cred_path=cred_path,
        on_qr_url=_log_qr,
        on_scanned=lambda: (
            print("已扫码，等待手机确认...", flush=True),
            _sync_creds_after_login(cred_path),
        ),
        on_expired=lambda: print("二维码已过期，等待刷新...", flush=True),
        on_error=lambda err: print(f"微信 Bot 错误: {err}", flush=True),
    )

    @bot.on_message
    async def handle(msg):
        await bot.send_typing(msg.user_id)
        text = msg.text.strip()
        if text.lower() in ['/clear', 'clear']:
            args = eva_args("-c")
        else:
            args = eva_args("-asu", text)
        await bot.reply(msg, clean_eva_output(run_cli(args)))

    mode = "Neon PostgreSQL" if storage.enabled() else "本地文件"
    print(f"> 存储模式: {mode}", flush=True)
    print(f"> 微信凭证路径: {cred_path}", flush=True)
    bot.run()


if __name__ == "__main__":
    main()
