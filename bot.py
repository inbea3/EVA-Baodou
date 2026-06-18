"""微信 Bot：支持多微信账号各自扫码绑定，每人独立 EVA 会话。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from wechatbot import WeChatBot

import health
import storage

ANSI_RE = re.compile(r"\033\[[0-9;]*m")

APP_DIR = Path(__file__).resolve().parent
EVA_SCRIPT = APP_DIR / "eva.py"
EVA_TASK_TIMEOUT = int(os.environ.get("EVA_TASK_TIMEOUT", "600"))

DATA_DIR = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("EVA_DATA_DIR") or str(APP_DIR / "data")
LEGACY_CRED = Path(os.environ.get("WECHATBOT_CRED_PATH", Path(DATA_DIR) / "wechatbot" / "credentials.json")).expanduser()

_running: dict[str, asyncio.Task] = {}
_bind_lock = asyncio.Lock()


def eva_args(*extra):
    return [sys.executable, str(EVA_SCRIPT), *extra]


def run_eva(args, session_user_id: str, timeout: int = EVA_TASK_TIMEOUT) -> str:
    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["EVA_SESSION_KEY"] = session_user_id
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(APP_DIR),
            timeout=timeout,
            shell=False,
            env=env,
        )
        output = result.stdout or ""
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"执行失败：EVA 任务超时（>{timeout}s）"
    except Exception as e:
        return f"执行失败：{str(e)}"


def clean_eva_output(out: str) -> str:
    if "[*] EVA:" in out:
        out = out[out.rindex("[*] EVA:") + len("[*] EVA:") :]
    if "> 会话已保存到：" in out:
        out = out[: out.rindex("> 会话已保存到：")]
    return ANSI_RE.sub("", out).strip()


def _log_qr(url: str) -> None:
    health.set_bind_qr(url)
    print(f"\n{'=' * 60}\n请用微信扫描登录:\n{url}\n{'=' * 60}\n", flush=True)


def _creds_to_json(creds) -> dict:
    d = asdict(creds)
    return {
        "token": d["token"],
        "baseUrl": d.get("base_url") or d.get("baseUrl", ""),
        "accountId": d.get("account_id") or d.get("accountId", ""),
        "userId": d.get("user_id") or d.get("userId", ""),
        "savedAt": d.get("saved_at") or d.get("savedAt"),
    }


def _attach_handler(bot: WeChatBot, owner_user_id: str) -> None:
    @bot.on_message
    async def handle(msg):
        await bot.send_typing(msg.user_id)
        text = msg.text.strip()
        if text.lower() in ("/clear", "clear"):
            args = eva_args("-c")
        else:
            args = eva_args("-asu", text)
        await bot.reply(msg, clean_eva_output(run_eva(args, owner_user_id)))


async def _run_bot(account_id: str, owner_user_id: str, cred_path: Path) -> None:
    bot = WeChatBot(
        cred_path=str(cred_path),
        on_error=lambda err: print(f"[{account_id}] 微信错误: {err}", flush=True),
    )
    _attach_handler(bot, owner_user_id)
    await bot.login(force=False)
    print(f"> 已启动微信账号 {account_id}（user={owner_user_id}）", flush=True)
    await bot.start()


async def start_account(account_id: str, owner_user_id: str) -> None:
    cred_path = storage.wechat_cred_path(account_id)
    if not cred_path.exists():
        print(f"> 凭证文件缺失，跳过账号 {account_id}", flush=True)
        return
    if account_id in _running:
        return
    task = asyncio.create_task(_run_bot(account_id, owner_user_id, cred_path))
    _running[account_id] = task


async def bind_new_account() -> None:
    async with _bind_lock:
        pending = storage.wechat_cred_path("_pending")
        pending.parent.mkdir(parents=True, exist_ok=True)
        if pending.exists():
            pending.unlink()

        health.set_bind_qr("")
        with health.bind_status.lock:
            health.bind_status.state = "waiting"
            health.bind_status.message = "请扫码并在手机上确认"

        bot = WeChatBot(
            cred_path=str(pending),
            on_qr_url=_log_qr,
            on_scanned=lambda: print("已扫码，等待手机确认…", flush=True),
            on_expired=lambda: print("二维码已过期，等待刷新…", flush=True),
            on_error=lambda err: health.set_bind_error(str(err)),
        )
        try:
            creds = await bot.login(force=True)
        except Exception as e:
            health.set_bind_error(str(e))
            print(f"> 绑定失败: {e}", flush=True)
            return

        cred_json = _creds_to_json(creds)
        account_id = cred_json["accountId"]
        user_id = cred_json["userId"]
        final = storage.wechat_cred_path(account_id)
        final.write_text(json.dumps(cred_json, ensure_ascii=False, indent=2), encoding="utf-8")
        storage.save_wechat_account(account_id, user_id, cred_json)
        if pending.exists():
            pending.unlink()

        health.set_bind_done(f"绑定成功，账号 {account_id}")
        print(f"> 新微信账号已绑定: {account_id} (user={user_id})", flush=True)
        await start_account(account_id, user_id)


async def amain() -> None:
    storage.init_schema()
    storage.migrate_legacy_wechat_cred(LEGACY_CRED)
    storage.sync_wechat_cred_files()

    accounts = storage.list_wechat_accounts()
    if accounts:
        print(f"> 已加载 {len(accounts)} 个微信账号", flush=True)
        for acc in accounts:
            await start_account(acc["account_id"], acc["user_id"])
    else:
        print("> 尚无绑定微信。请打开 Railway 公网地址 /bind 扫码，或访问 Deploy Logs", flush=True)

    while True:
        done = [k for k, t in _running.items() if t.done()]
        for k in done:
            exc = _running[k].exception()
            if exc:
                print(f"> 账号 {k} 监听异常退出: {exc}", flush=True)
            del _running[k]
        await asyncio.sleep(5)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    health.start(on_bind=lambda: asyncio.run_coroutine_threadsafe(bind_new_account(), loop))
    mode = "Neon PostgreSQL" if storage.enabled() else "本地文件"
    print(f"> 存储模式: {mode}（每微信账号独立 EVA 会话）", flush=True)
    try:
        loop.run_until_complete(amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
