from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import database
from aiogram import Bot
from utils.rating_channel_posts import refresh_rating_channel_posts


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh active live rating posts in Telegram channels."
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="Path to SQLite database file. Defaults to project DB_PATH.",
    )
    parser.add_argument(
        "--sport",
        dest="sport_key",
        default=None,
        help="Optional sport filter, for example CS2.",
    )
    parser.add_argument(
        "--entity-type",
        dest="entity_type",
        choices=["player", "team"],
        default=None,
        help="Optional entity type filter.",
    )
    parser.add_argument(
        "--rating-scope",
        dest="rating_scope",
        choices=["overall", "seasonal"],
        default=None,
        help="Optional rating scope filter.",
    )
    return parser.parse_args()


async def _run_refresh(args: argparse.Namespace) -> None:
    load_dotenv()
    bot_token = (os.getenv("BOT_TOKEN") or "").strip()
    if not bot_token:
        raise RuntimeError("Environment variable BOT_TOKEN is not set")

    if args.db_path:
        database.DB_PATH = Path(args.db_path)

    bot = Bot(token=bot_token)
    try:
        await refresh_rating_channel_posts(
            bot,
            sport_key=args.sport_key,
            entity_type=args.entity_type,
            rating_scope=args.rating_scope,
        )
    finally:
        await bot.session.close()


def main() -> int:
    args = _parse_args()
    asyncio.run(_run_refresh(args))
    print("Live rating posts refreshed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
