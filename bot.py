from wechatbot import WeChatBot
import os
import re
import sys
import subprocess
from pathlib import Path

ANSI_RE = re.compile(r'\033\[[0-9;]*m')

EVA_SCRIPT = Path(__file__).resolve().parent / "eva.py"

def eva_args(*extra):
    # Windows 下 subprocess(shell=False) 无法直接执行 PATH 里的 .cmd，需显式调用 python
    return [sys.executable, str(EVA_SCRIPT), *extra]

def run_cli(args, timeout: int = 300):
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
            cwd=os.getcwd(),
            timeout=timeout,
            shell=False,
            env=env,
        )
        output = f"{result.stdout}"
        if result.stderr:
            output += f"\nSTDERR:\n{result.stderr}"
        return output.strip() or "(no output)"
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


bot = WeChatBot()

@bot.on_message
async def handle(msg):
    await bot.send_typing(msg.user_id)
    text = msg.text.strip()
    if text.lower() in ['/clear', 'clear']:
        args = eva_args("-c")
    else:
        args = eva_args("-asu", text)
    await bot.reply(msg, clean_eva_output(run_cli(args)))

bot.run()