from __future__ import annotations

import argparse
import asyncio
import logging

from .config import Settings


def main() -> None:
    ap = argparse.ArgumentParser(prog="dcrypt")
    ap.add_argument("cmd", choices=["run", "dialogs", "stats"])
    args = ap.parse_args()
    s = Settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "run":
        from .app import run
        asyncio.run(run(s))
    elif args.cmd == "dialogs":
        from .ingest import list_dialogs
        asyncio.run(list_dialogs(s))
    else:
        from .db import Database
        from .stats import report

        async def _go() -> None:
            db = Database(s.db_url)
            await db.init()
            print(await report(db))
            await db.close()
        asyncio.run(_go())


if __name__ == "__main__":
    main()
