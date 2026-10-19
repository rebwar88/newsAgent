"""Run the news agent and deliver its complete execution log to a private Telegram group.

Uses Telegram Bot API only; never calls Gemini for monitoring.
"""
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

import requests


def redact(value):
    for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_LOG_CHAT_ID"):
        secret = os.getenv(name, "")
        if secret:
            value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"bot[0-9]{5,}:[A-Za-z0-9_-]{20,}", "bot[REDACTED]", value)
    return value


def main():
    started = datetime.now(timezone.utc)
    result = subprocess.run([sys.executable, "-u", "main.py"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    finished = datetime.now(timezone.utc)
    body = redact(result.stdout)
    print(body, end="", flush=True)
    run_url = os.getenv("GITHUB_SERVER_URL", "https://github.com") + "/" + os.getenv("GITHUB_REPOSITORY", "rebwar88/newsAgent") + "/actions/runs/" + os.getenv("GITHUB_RUN_ID", "")
    header = (
        "NewsAgent full execution log\n"
        f"Started UTC: {started.isoformat()}\n"
        f"Finished UTC: {finished.isoformat()}\n"
        f"Exit code: {result.returncode}\n"
        f"Workflow: {run_url}\n"
        "----------------------------------------\n"
    )
    log_text = header + body
    with open("newsagent_run.log", "w", encoding="utf-8") as f:
        f.write(log_text)

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_LOG_CHAT_ID")
    if token and chat:
        endpoint = f"https://api.telegram.org/bot{token}/sendDocument"
        caption = ("❌ NewsAgent failed" if result.returncode else "📋 NewsAgent execution log") + "\n" + run_url
        try:
            with open("newsagent_run.log", "rb") as f:
                response = requests.post(endpoint, data={"chat_id": chat, "caption": caption[:1024]}, files={"document": ("newsagent_run.log", f, "text/plain")}, timeout=30)
            if not response.ok:
                print(f"Log delivery failed: HTTP {response.status_code}; verify Telegram group Chat ID and bot permissions.")
            else:
                print("Detailed log delivered to Telegram group.")
        except requests.RequestException as exc:
            print(f"Log delivery transport failed: {type(exc).__name__}")
    else:
        print("Telegram group logs not configured: add TELEGRAM_LOG_CHAT_ID as GitHub Actions secret.")
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
