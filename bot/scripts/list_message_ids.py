#!/usr/bin/env python3
"""List stored message IDs for targeted inspection or later edits."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from message_registry import list_messages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", dest="schedule_date")
    parser.add_argument("--kind", dest="message_kind")
    parser.add_argument("--chat-id")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    rows = list_messages(
        schedule_date=args.schedule_date,
        message_kind=args.message_kind,
        chat_id=args.chat_id,
        limit=args.limit,
    )
    for row in reversed(rows):
        print(
            " | ".join(
                str(row.get(key) or "-")
                for key in (
                    "platform",
                    "direction",
                    "chat_id",
                    "message_id",
                    "message_kind",
                    "schedule_date",
                    "schedule_type",
                    "entity",
                    "file_name",
                )
            )
        )


if __name__ == "__main__":
    main()
