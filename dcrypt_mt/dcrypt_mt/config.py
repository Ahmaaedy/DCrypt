from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

SNAPSHOT_OFFSETS_S = (60, 300, 900, 3600)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- telegram
    tg_api_id: int = 0
    tg_api_hash: str = ""
    tg_session: str = "dcrypt"
    tg_session_string: str = ""  # optional StringSession for hosts without a writable session file
    tg_channels: str = ""
    queue_size: int = 1000
    workers: int = 4

    # --- storage
    db_url: str = "sqlite+aiosqlite:///dcrypt.db"

    # --- pre-filter / normalizer
    filter_threshold: float = 0.5
    llm_backend: Literal["ollama", "heuristic"] = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    llm_concurrency: int = 2
    llm_timeout_s: float = 30.0

    # --- mode / live wiring
    mode: Literal["paper", "live"] = "paper"
    live_confirm: str = ""
    wallet_secret_key: str = ""
    rpc_url: str = ""
    jupiter_base_url: str = "https://lite-api.jup.ag/swap/v1"
    jupiter_api_key: str = ""
    slippage_bps_buy: int = 300
    slippage_bps_sell: int = 1000
    max_priority_lamports: int = 1_000_000
    max_roundtrip_loss_pct: float = 25.0
    max_price_impact_pct: float = 5.0
    max_live_position_sol: float = 0.05  # hard cap while live is unproven

    # --- paper fill model (deliberately pessimistic)
    paper_base_slippage_bps: float = 100.0
    paper_exit_penalty_bps: float = 200.0
    paper_fee_sol: float = 0.002

    # --- validator / risk (global across all channels)
    position_size_sol: float = 0.1
    min_position_sol: float = 0.02
    max_positions: int = 5
    max_exposure_sol: float = 0.5
    daily_loss_limit_sol: float = 0.3
    liq_cap_pct: float = 1.0  # position <= this % of pool liquidity
    min_liquidity_usd: float = 15_000
    max_signal_age_s: float = 60
    min_age_min: float = 5
    cooldown_min: float = 60
    min_channels: int = 1
    channel_min_trades: int = 20
    channel_min_avg_pnl_pct: float = -5.0
    kill_file: str = "KILL"
    require_no_freeze: bool = True
    require_no_mint_authority: bool = True
    require_onchain_checks: bool = True

    # --- paper vs live pre-trade gate
    paper_use_jupiter_quotes: bool = True

    # --- momentum confirmation at signal time (DexScreener 5m/1h data)
    momentum_gates: bool = True
    min_m5_txns: int = 10
    min_buy_ratio: float = 0.55
    min_change_m5_pct: float = 0.0
    max_change_m5_pct: float = 40.0  # extension cap: skip if already pumped
    max_change_h1_pct: float = 300.0

    # --- acceleration gate (baseline-relative, stateless)
    require_acceleration: bool = True
    min_txn_accel: float = 1.5
    min_volume_accel: float = 1.5
    accel_min_age_min: float = 60.0
    young_token_policy: Literal["skip", "reject"] = "reject"

    # --- exits
    poll_interval_s: float = 5.0
    hard_stop_pct: float = 15.0
    tp1_mult: float = 2.0
    scale_out_fraction: float = 0.5
    trail_arm_pct: float = 20.0
    trail_pct: float = 20.0
    time_stop_min: float = 15.0
    time_stop_min_gain_pct: float = 5.0
    max_hold_min: float = 240.0
    liq_drop_exit_pct: float = 40.0
    fade_exit: bool = True
    fade_buy_ratio: float = 0.40
    max_price_misses: int = 12
    max_sell_failures: int = 5

    # --- notifications
    notify_bot_token: str = ""
    notify_chat_id: str = ""

    @property
    def channel_list(self) -> list[str | int]:
        out: list[str | int] = []
        for c in (x.strip() for x in self.tg_channels.split(",")):
            if not c:
                continue
            out.append(int(c) if c.lstrip("-").isdigit() else c)
        return out

    def assert_live_ready(self) -> None:
        if self.live_confirm != "I_UNDERSTAND_THE_RISK":
            raise SystemExit("MODE=live requires LIVE_CONFIRM=I_UNDERSTAND_THE_RISK")
        if not (self.wallet_secret_key and self.rpc_url):
            raise SystemExit("MODE=live requires WALLET_SECRET_KEY and RPC_URL")
        if self.position_size_sol > self.max_live_position_sol:
            raise SystemExit(
                f"position_size_sol {self.position_size_sol} exceeds max_live_position_sol "
                f"{self.max_live_position_sol}; raise the cap deliberately if intended"
            )
