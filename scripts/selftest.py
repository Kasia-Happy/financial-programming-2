#!/usr/bin/env python
"""가드 검증 — 돈을 움직이지 않고 안전장치가 실제로 막는지 확인한다.

    .venv/bin/python scripts/selftest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as C
from bot.client import (SSUClient, RiskLimitError, MarketClosedError,
                        SSUError)

PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def expect_raise(name: str, exc_type, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exc_type as e:
        results.append((PASS, name, str(e)[:90]))
    except Exception as e:  # noqa: BLE001
        results.append((FAIL, name, f"다른 예외 {type(e).__name__}: {e}"[:90]))
    else:
        results.append((FAIL, name, "막히지 않았다 (통과해버림)"))


def expect_ok(name: str, fn, *args, **kwargs) -> None:
    try:
        out = fn(*args, **kwargs)
        results.append((PASS, name, str(out)[:90]))
    except Exception as e:  # noqa: BLE001
        results.append((FAIL, name, f"{type(e).__name__}: {e}"[:90]))


def main() -> int:
    cli = SSUClient()

    # 1. 종목 코드 → 시장 판별
    cases = [("005930", "kospi"), ("NVDA", "nasdaq"), ("BTCUSDT", "crypto"),
             ("BTCUSDT.P", "futures"), ("BTCUSD_PERP", "futures")]
    for sym, want in cases:
        got = C.market_of(sym)
        results.append((PASS if got == want else FAIL,
                        f"시장판별 {sym}", f"{got} (기대 {want})"))

    # 2. confirmed 없이 주문 → 막혀야 한다
    expect_raise("confirmed 없는 현물주문 차단", RiskLimitError,
                 cli.place_order, "BTCUSDT", "buy", 0.001)
    expect_raise("confirmed 없는 선물주문 차단", RiskLimitError,
                 cli.futures_order, "BTCUSDT.P", "buy", 0.001)
    expect_raise("confirmed 없는 환전 차단", RiskLimitError,
                 cli.convert, "KRW", "USDT", 1000)
    expect_raise("confirmed 없는 증거금이체 차단", RiskLimitError,
                 cli.futures_transfer, "USDT", "in", 100)

    # 3. 비중 한도 — 총자산의 99%를 한 종목에 → 막혀야 한다
    expect_raise("한 종목 비중 한도", RiskLimitError,
                 cli.check_risk, "BTCUSDT", C.START_KRW * 0.99)

    # 4. 레버리지 한도
    expect_raise("레버리지 한도", RiskLimitError,
                 cli.check_risk, "BTCUSDT.P", C.START_KRW * 0.01, 50)

    # 5. 교차 증거금 금지
    expect_raise("교차 증거금 금지", RiskLimitError,
                 cli.check_risk, "BTCUSDT.P", C.START_KRW * 0.01, 2, "cross")

    # 6. 한도 안쪽은 통과해야 한다
    expect_ok("한도 내 현물 통과", cli.check_risk, "BTCUSDT",
              C.START_KRW * 0.05)
    expect_ok("한도 내 선물(isolated 2배) 통과", cli.check_risk,
              "BTCUSDT.P", C.START_KRW * 0.05, 2, "isolated")

    # 7. 닫힌 장 감지 (코스피가 닫혀 있을 때만 유효)
    rules = cli.get_rules()
    if not rules["markets"]["kospi"]["open"]:
        expect_raise("닫힌 장 감지(코스피)", MarketClosedError,
                     cli.assert_market_open, "005930")
    else:
        expect_ok("코스피 개장 중 통과", cli.assert_market_open, "005930")

    # 8. 손익분기 계산
    for sym in ("005930", "NVDA", "BTCUSDT", "BTCUSDT.P"):
        be = cli.breakeven_bps(sym)
        results.append((PASS, f"손익분기 {sym}", f"{be:.0f}bps = {be/100:.2f}%"))

    # 9. 환전 견적 (dryRun — 돈 안 움직임)
    expect_ok("환전 견적 dryRun", cli.convert_quote, "KRW", "USDT", 1_000_000)

    # ── 출력 ──
    width = max(len(n) for _, n, _ in results)
    npass = sum(1 for s, _, _ in results if s == PASS)
    for status, name, detail in results:
        mark = "✓" if status == PASS else "✗"
        print(f"{mark} {name:<{width}}  {detail}")
    print(f"\n{npass}/{len(results)} 통과")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
