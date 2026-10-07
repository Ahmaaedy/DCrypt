import asyncio
import json

import httpx

from dcrypt_paste.filter import extract_candidates, score_message
from dcrypt_paste.normalizer import OllamaNormalizer, HeuristicNormalizer
from helpers import new_mint


def test_extracts_valid_mints_only():
    m = new_mint()
    usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
    text = f"buy $FOO CA: {m} (not {usdc}) and junk {'a' * 40}"
    assert extract_candidates(text) == [m]


def test_extracts_from_url():
    m = new_mint()
    assert extract_candidates(f"https://pump.fun/coin/{m}") == [m]
    assert extract_candidates(f"https://dexscreener.com/solana/{m}?x=1") == [m]


def test_scoring_orders_sensibly():
    m = new_mint()
    call = score_message(f"🚀 $FOO buy now CA {m} entry 0.0004 SL 0.0003 TP 0.001")
    news = score_message("Bitcoin ETF flows hit 3 billion this week, analysts say")
    assert call.score > 0.7 and call.mints == [m]
    assert news.score < 0.3 and not news.mints


def test_ollama_masks_addresses_and_never_trusts_llm_for_mint():
    m = new_mint()
    seen = {}

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen["prompt"] = body["messages"][1]["content"]
        out = {"is_signal": True, "side": "buy", "symbol": "FOO", "mint_ref": 1, "entry_price": 0.0004,
               "targets": [0.001]}
        return httpx.Response(200, json={"message": {"content": json.dumps(out)}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    n = OllamaNormalizer("http://x", "m", client=client)
    res = asyncio.run(n.normalize(f"buy $FOO {m} entry 0.0004", [m]))
    assert m not in seen["prompt"] and "<MINT_1>" in seen["prompt"]
    assert res.status == "ok" and res.mint == m and res.signal.entry_price == 0.0004


def test_ollama_ambiguous_and_errors():
    a, b = new_mint(), new_mint()

    def ambiguous(request):
        out = {"is_signal": True, "side": "buy", "mint_ref": None}
        return httpx.Response(200, json={"message": {"content": json.dumps(out)}})

    n = OllamaNormalizer("http://x", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(ambiguous)))
    assert asyncio.run(n.normalize(f"{a} {b}", [a, b])).status == "ambiguous_mint"

    bad = OllamaNormalizer("http://x", "m", client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"message": {"content": "nope"}}))))
    assert asyncio.run(bad.normalize(a, [a])).status == "invalid_output"

    down = OllamaNormalizer("http://x", "m", client=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert asyncio.run(down.normalize(a, [a])).status == "llm_error"


def test_heuristic_sell_is_not_buy():
    m = new_mint()
    r = asyncio.run(HeuristicNormalizer().normalize(f"sell $FOO {m}", [m]))
    assert r.signal.side == "sell"
