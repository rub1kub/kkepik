"""Owner-only moderation preview. Never sends an announcement to schedule users."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OWNER = 1084693264
AUDIT = ROOT / "audits/schedule-repair-20261013"
TEXT = (
    "В расписании на 13 октября была ошибка: бот пропустил продолжение таблицы "
    "на следующей странице PDF. У части групп потерялись пары, а кому-то пришло "
    "сообщение, что группы нет в файле.\n\n"
    "Исправил. Предыдущее сообщение с расписанием уже обновлено — проверьте "
    "его ещё раз. Извините за путаницу."
)


def call(token, method, payload):
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            value = json.loads(response.read(1024 * 1024))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Telegram rejected owner preview (HTTP {exc.code})") from None
    except (OSError, ValueError):
        raise RuntimeError("Uncertain Telegram response; do not automatically resend") from None
    if not value.get("ok"):
        raise RuntimeError("Telegram rejected owner preview")
    return value["result"]


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def main(send=False):
    if not send:
        print(json.dumps({"mode": "dry-run", "announcement": TEXT,
                          "audience_messages_sent": 0}, ensure_ascii=False))
        return
    from dotenv import dotenv_values
    from message_registry import record_message

    env = dotenv_values(ROOT / ".env")
    token = env.get("BOT_TOKEN")
    if not token:
        raise ValueError("Bot credentials missing")
    me = call(token, "getMe", {})
    owner = call(token, "getChat", {"chat_id": OWNER})
    if me.get("username") != "kkepik_bot" or not me.get("is_bot"):
        raise ValueError("Unexpected bot")
    if owner.get("id") != OWNER or owner.get("type") != "private" or owner.get("username") != "rub1kub":
        raise ValueError("Unexpected moderation recipient")
    if AUDIT.is_symlink():
        raise ValueError("Invalid audit directory")
    AUDIT.mkdir(mode=0o700, exist_ok=True)
    AUDIT.chmod(0o700)
    write_private(AUDIT / "moderation-intent.json", {
        "status": "pending_owner_approval", "approved": False,
        "announcement": TEXT, "broadcast_scheduled": False,
        "attempted_at": datetime.now(timezone.utc).isoformat(),
    })
    result = call(token, "sendMessage", {
        "chat_id": OWNER,
        "text": "Текст на согласование. Пока отправляю только вам:\n\n" + TEXT,
    })
    if result.get("chat", {}).get("id") != OWNER:
        raise ValueError("Unexpected preview result")
    write_private(AUDIT / "moderation-result.json", {
        "status": "pending_owner_approval", "message_id": result["message_id"],
        "broadcast_scheduled": False,
    })
    record_message(
        platform="telegram", direction="outgoing", chat_id=OWNER,
        message_id=result["message_id"], message_kind="schedule_repair_preview",
        telegram_method="sendMessage", schedule_date="13.10.2026",
        schedule_type="groups", entity="pdf-page-continuation-20261013",
        content=result["text"], db_path=env.get("MESSAGE_REGISTRY_PATH") or ROOT / "data/message_registry.db")
    print(json.dumps({"status": "pending_owner_approval", "owner_preview_sent": True,
                      "audience_messages_sent": 0, "broadcast_scheduled": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-owner", action="store_true")
    try:
        main(parser.parse_args().send_owner)
    except Exception as exc:
        print(json.dumps({"status": "stopped", "error_type": type(exc).__name__}))
        sys.exit(1)
