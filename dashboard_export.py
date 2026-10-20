"""Publish sanitized per-run telemetry to the static dashboard."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

raw = Path("newsagent_run.log").read_text(encoding="utf-8") if Path("newsagent_run.log").exists() else ""
lines = raw.splitlines()
events, issues = [], []
stages = {key: {"status": "unknown", "detail": "No event recorded"} for key in ("fetch", "discovery", "selection", "gemini", "telegram", "state")}
found = None
published = 0
exit_code = None
for line in lines:
    if line.startswith("Exit code:"):
        try:
            exit_code = int(line.partition(":")[2].strip())
        except ValueError:
            pass
    stage = None
    if "Checking for new articles" in line:
        stage = "fetch"
        stages[stage] = {"status": "ok", "detail": "Source collection started"}
    elif "Found " in line and " new items" in line:
        stage = "discovery"
        m = re.search(r"Found (\d+) new items", line)
        if m:
            found = int(m.group(1))
            stages["fetch"] = {"status": "ok", "detail": "Telegram source collection completed"}
            stages[stage] = {"status": "ok", "detail": f"{found} items found"}
    elif "Processing item from" in line:
        stage = "selection"
        stages[stage] = {"status": "ok", "detail": "Article selected for processing"}
    elif any(x in line for x in ("Gemini", "AI rewrite", "quota", "cooldown")):
        stage = "gemini"
        if any(x in line for x in ("Error calling", "AI rewrite failed", "HTTP 4", "HTTP 5")):
            stages[stage] = {"status": "failed", "detail": line[:220]}
        elif any(x in line for x in ("cooldown active", "quota exhausted")):
            stages[stage] = {"status": "blocked", "detail": line[:220]}
        else:
            stages[stage] = {"status": "ok", "detail": "Gemini stage reached"}
    elif "Successfully published" in line:
        stage = "telegram"
        stages["gemini"] = {"status": "ok", "detail": "AI article generated"}
        stages[stage] = {"status": "ok", "detail": "Telegram confirmed publication"}
    elif "Telegram publication failed" in line or "Telegram rejected" in line:
        stage = "telegram"
        stages[stage] = {"status": "failed", "detail": line[:220]}
    elif "Completed run" in line:
        stage = "state"
        m = re.search(r"Processed (\d+) articles", line)
        if m:
            published = int(m.group(1))
        stages[stage] = {"status": "ok", "detail": line[:220]}
    if any(x in line for x in ("Error ", "failed", "cooldown active", "quota exhausted", "rejected")):
        issues.append(line[:250])
    if stage:
        events.append({"stage": stage, "message": line[:250]})

def safe(value):
    value = re.sub(r"https?://\S+", "[URL]", value)
    value = re.sub(r"bot\d{5,}:[A-Za-z0-9_-]+", "[TOKEN]", value)
    for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_LOG_CHAT_ID"):
        secret = os.getenv(name, "")
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value

if exit_code not in (None, 0):
    stages["state"] = {"status": "failed", "detail": f"Exit code {exit_code}"}
    issues.append(f"Agent exited with code {exit_code}")
run_url = "https://github.com/" + os.getenv("GITHUB_REPOSITORY", "rebwar88/newsAgent") + "/actions/runs/" + os.getenv("GITHUB_RUN_ID", "")
start = next((line.partition(": ")[2] for line in lines if line.startswith("Started UTC:")), None)
end = next((line.partition(": ")[2] for line in lines if line.startswith("Finished UTC:")), None)
duration = None
try:
    duration = round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1)
except (TypeError, ValueError):
    pass
log_keywords = ("Checking for new", "Found ", "Processing item", "Gemini", "AI rewrite", "cooldown", "quota", "published", "Completed run", "Telegram rejected", "Error ", "Waiting ")
public_log = "\n".join(safe(line) for line in lines if any(key in line for key in log_keywords))[-12000:]
payload = {"run_id": os.getenv("GITHUB_RUN_ID", ""), "run_url": run_url, "started_at": start, "finished_at": end, "duration_seconds": duration, "exit_code": exit_code, "found": found, "published": published, "stages": stages, "issues": [safe(x) for x in issues[-20:]], "events": [{"stage": x["stage"], "message": safe(x["message"])} for x in events[-60:]], "log": public_log}

Path("docs").mkdir(exist_ok=True)
Path("docs/status.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print("Dashboard telemetry exported")
