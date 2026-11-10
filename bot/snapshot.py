"""일별 스냅샷 저장소 — 서버가 과거 NAV 시계열을 주지 않으므로 직접 쌓는다.

`/api/history` 는 REST 에 없다(404). 전일 대비·주간 추이·MDD 곡선을 그리려면
매일 찍어서 `state/nav_history.jsonl` 에 남기는 수밖에 없다.

하루 한 줄(마지막 기록이 그날의 확정값). 같은 날 여러 번 찍으면 덮어쓴다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
NAV_FILE = STATE_DIR / "nav_history.jsonl"
RANK_FILE = STATE_DIR / "rank_history.jsonl"


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def _load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _save(path: Path, rows: list[dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def record_nav(portfolio: dict, date: str | None = None) -> dict:
    """계좌 스냅샷 1건 저장 (하루 한 줄, 같은 날이면 덮어씀)."""
    d = date or _today()
    pf = portfolio
    row = {
        "date": d,
        "ts": datetime.now(KST).isoformat(timespec="seconds"),
        "equityKrw": float(pf.get("equityKrw") or 0),
        "startKrw": float(pf.get("startKrw") or 0),
        "returnPct": float(pf.get("returnPct") or 0),
        "maxDrawdownPct": float(pf.get("maxDrawdownPct") or 0),
        "cashKrw": float(pf.get("cashKrw") or 0),
        "transitKrw": float(pf.get("transitKrw") or 0),
        "positionsKrw": float(pf.get("positionsKrw") or 0),
        "spotEquityKrw": float(pf.get("spotEquityKrw") or 0),
        "futuresEquityKrw": float(pf.get("futuresEquityKrw") or 0),
        "trades": int(pf.get("trades") or 0),
        "conversions": len(pf.get("conversions") or []),
        "tradeFeeKrw": float(pf.get("tradeFeeKrw") or 0),
        "fxCostKrw": float(pf.get("fxCostKrw") or 0),
        "wallets": pf.get("wallets") or {},
        "rate": pf.get("rate") or {},
        "byMarket": pf.get("byMarket") or {},
        "n_positions": len(pf.get("positions") or []),
        "n_rejects": len(pf.get("rejects") or []),
    }
    rows = [r for r in _load(NAV_FILE) if r.get("date") != d]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    _save(NAV_FILE, rows)
    return row


def record_rank(leaderboard: list[dict], my_id: str = "s09",
                date: str | None = None) -> dict:
    """순위표 스냅샷 (전체 팀 요약 + 우리 순위)."""
    d = date or _today()
    lb = sorted(leaderboard, key=lambda r: -float(r.get("equityKrw") or 0))
    my_rank = next((i for i, r in enumerate(lb, 1) if r.get("id") == my_id), None)
    row = {
        "date": d,
        "ts": datetime.now(KST).isoformat(timespec="seconds"),
        "n_teams": len(lb),
        "my_rank": my_rank,
        "teams": [{
            "id": r.get("id"),
            "nickname": r.get("nickname"),
            "equityKrw": float(r.get("equityKrw") or 0),
            "returnPct": float(r.get("returnPct") or 0),
            "maxDrawdownPct": float(r.get("maxDrawdownPct") or 0),
            "trades": int(r.get("trades") or 0),
            "conversions": int(r.get("conversions") or 0),
            "futuresPositions": int(r.get("futuresPositions") or 0),
            "fxCostKrw": float(r.get("fxCostKrw") or 0),
            "tradeFeeKrw": float(r.get("tradeFeeKrw") or 0),
            "byMarket": r.get("byMarket") or {},
            "teammates": r.get("teammates") or [],
        } for r in lb],
    }
    rows = [r for r in _load(RANK_FILE) if r.get("date") != d]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])
    _save(RANK_FILE, rows)
    return row


def nav_history(days: int | None = None) -> list[dict]:
    rows = _load(NAV_FILE)
    return rows[-days:] if days else rows


def rank_history(days: int | None = None) -> list[dict]:
    rows = _load(RANK_FILE)
    return rows[-days:] if days else rows


def previous(rows: list[dict], date: str) -> dict | None:
    """주어진 날짜 직전 기록 (전일 대비 계산용)."""
    earlier = [r for r in rows if r.get("date", "") < date]
    return earlier[-1] if earlier else None


def drawdown_series(rows: list[dict]) -> list[tuple[str, float]]:
    """NAV 기록에서 낙폭 곡선 계산 (시작금 기준, 고점 대비 %)."""
    out, peak = [], None
    for r in rows:
        eq = r["equityKrw"]
        peak = eq if peak is None else max(peak, eq)
        out.append((r["date"], (eq / peak - 1) * 100 if peak else 0.0))
    return out
