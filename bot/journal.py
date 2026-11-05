"""거래 기록 — 회고용. 시각·종목·방향·근거·예상비용을 JSONL로 남긴다.

채점에서 "실패를 숫자로 설명할 수 있는가"가 항목이라 기록이 자료가 된다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _path(kind: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return LOG_DIR / f"{kind}.jsonl"


def write(kind: str, **fields: Any) -> dict:
    """한 줄 기록. kind: decision | order | reject | convert | error | snapshot"""
    rec = {"ts": _now(), "kind": kind, **fields}
    with _path(kind).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def decision(symbol: str, side: str, qty: float, reason: str,
             expected_cost_bps: float, **extra: Any) -> dict:
    """주문 전 판단 근거. 주문이 거부돼도 이 기록은 남는다."""
    return write("decision", symbol=symbol, side=side, qty=qty,
                 reason=reason, expected_cost_bps=expected_cost_bps, **extra)


def order(symbol: str, side: str, qty: float, response: dict, **extra: Any) -> dict:
    return write("order", symbol=symbol, side=side, qty=qty,
                 response=response, **extra)


def reject(endpoint: str, payload: dict, status: int, body: Any) -> dict:
    """거부 원문을 그대로 남긴다 — 같은 거부 반복 방지에 쓴다."""
    safe = {k: v for k, v in payload.items()}
    return write("reject", endpoint=endpoint, payload=safe,
                 status=status, body=body)


def snapshot(portfolio: dict) -> dict:
    """포트폴리오 스냅샷 — MDD 추적용."""
    return write("snapshot",
                 equityKrw=portfolio.get("equityKrw"),
                 returnPct=portfolio.get("returnPct"),
                 maxDrawdownPct=portfolio.get("maxDrawdownPct"),
                 cashKrw=portfolio.get("cashKrw"),
                 transitKrw=portfolio.get("transitKrw"),
                 wallets=portfolio.get("wallets"),
                 positions=portfolio.get("positions"))


def read(kind: str, limit: int = 50) -> list[dict]:
    p = _path(kind)
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(x) for x in lines[-limit:]]


def count_today(kind: str) -> int:
    """오늘(KST) 기록 건수 — 하루 주문 한도 체크용."""
    today = datetime.now(KST).date().isoformat()
    return sum(1 for r in read(kind, limit=10_000)
               if r.get("ts", "").startswith(today))
