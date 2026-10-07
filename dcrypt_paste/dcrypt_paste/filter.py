"""Cheap pre-filter: decides whether a message is worth sending to the LLM at all."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import base58

_B58 = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
_CASHTAG = re.compile(r"(?<![\w$])\$[A-Za-z][A-Za-z0-9]{1,14}\b")
_LINK = re.compile(r"(dexscreener\.com|birdeye\.so|pump\.fun|photon-sol|bullx\.io|gmgn\.ai|solscan\.io|jup\.ag)", re.I)
_KEYWORDS = re.compile(
    r"\b(buy|entry|enter|sl|stop|tp|target|targets|ca|contract|ape|long|launch|gem|mc|mcap)\b", re.I)
_NUM = re.compile(r"^[\$\(\[]?\d[\d,._]*[kmx%]?[\)\]]?$", re.I)

# addresses that appear in messages but are never the token being called
IGNORED = {
    "So11111111111111111111111111111111111111112",   # wSOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "11111111111111111111111111111111",              # system program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",   # token program
}


def _is_pubkey(s: str) -> bool:
    try:
        return len(base58.b58decode(s)) == 32
    except Exception:
        return False


def extract_candidates(text: str) -> list[str]:
    """Valid 32-byte base58 strings, de-duplicated, in order. May be mints OR pair addresses (from URLs)."""
    seen: list[str] = []
    for m in _B58.findall(text):
        if m not in IGNORED and m not in seen and _is_pubkey(m):
            seen.append(m)
    return seen


@dataclass
class FilterResult:
    score: float
    mints: list[str]
    features: dict = field(default_factory=dict)


def score_message(text: str) -> FilterResult:
    mints = extract_candidates(text)
    stripped = _B58.sub(" ", text)
    tokens = stripped.split()
    nums = sum(1 for t in tokens if _NUM.match(t))
    density = nums / len(tokens) if tokens else 0.0
    kw = len(set(k.lower() for k in _KEYWORDS.findall(stripped)))

    feats = {
        "mint": bool(mints),
        "cashtag": bool(_CASHTAG.search(stripped)),
        "link": bool(_LINK.search(text)),
        "keywords": kw,
        "num_density": round(density, 3),
    }
    score = 0.0
    score += 0.50 if feats["mint"] else 0.0
    score += 0.10 if feats["cashtag"] else 0.0
    score += 0.15 if feats["link"] else 0.0
    score += min(0.07 * kw, 0.20)
    score += min(density, 0.15)
    return FilterResult(score=round(min(score, 1.0), 3), mints=mints, features=feats)
