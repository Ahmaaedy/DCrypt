import os
from datetime import timedelta

import base58

from dcrypt_paste.config import Settings
from dcrypt_paste.db import Database, Repo
from dcrypt_paste.executors.paper import PaperExecutor
from dcrypt_paste.models import RawMessage, TokenInfo, utcnow
from dcrypt_paste.normalizer import HeuristicNormalizer
from dcrypt_paste.notify import Notifier
from dcrypt_paste.pipeline import Pipeline
from dcrypt_paste.positions import PositionManager
from dcrypt_paste.validator import Validator


def new_mint() -> str:
    return base58.b58encode(os.urandom(32)).decode()


class FakePrices:
    def __init__(self):
        self.tokens: dict[str, TokenInfo] = {}

    def add(self, mint=None, **kw) -> TokenInfo:
        mint = mint or new_mint()
        base = dict(mint=mint, symbol="TST", pair_address="pair", dex="raydium", price_native=1e-6,
                    price_usd=1e-4, liquidity_usd=100_000, volume_h24=500_000,
                    pair_created_at=utcnow() - timedelta(hours=3), buys_m5=40, sells_m5=20,
                    change_m5=5.0, change_h1=20.0)
        base.update(kw)
        self.tokens[mint] = TokenInfo(**base)
        return self.tokens[mint]

    def set(self, mint, **kw):
        self.tokens[mint] = self.tokens[mint].model_copy(update=kw)

    async def get_tokens(self, mints):
        return {m: self.tokens[m] for m in mints if m in self.tokens}

    async def get_token(self, mint):
        return self.tokens.get(mint)

    async def resolve(self, address):
        return self.tokens.get(address)

    async def sol_usd(self):
        return 100.0


def msg(text, channel=1, mid=1, age_s=1.0, name="chan"):
    ts = utcnow() - timedelta(seconds=age_s)
    return RawMessage(channel_id=channel, channel_name=name, message_id=mid, ts=ts, text=text)


async def make_env(tmp_path, **overrides):
    s = Settings(db_url=f"sqlite+aiosqlite:///{tmp_path}/t.db", llm_backend="heuristic",
                 kill_file=str(tmp_path / "KILL"), _env_file=None, **overrides)
    db = Database(s.db_url)
    await db.init()
    repo = Repo(db)
    prices = FakePrices()
    ex = PaperExecutor(s)
    validator = Validator(s, repo, prices, ex, None)
    pm = PositionManager(s, repo, prices, ex, Notifier())
    pipe = Pipeline(s, repo, HeuristicNormalizer(), prices, validator, pm)
    return s, db, repo, prices, validator, pm, pipe
