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

    # ── 전략 (bot/strategy.py) ──
    import numpy as np
    import pandas as pd
    from bot import strategy as S

    # 10. GMV 가 scipy SLSQP 와 같은 해를 주는가
    rng = np.random.default_rng(7)
    T, N = 63, 60
    F = rng.normal(0, 0.01, (T, 3))
    Rm = F @ rng.normal(0, 1, (3, N)) + rng.normal(0, 0.012, (T, N))
    Sig, shrink = S.estimate_cov(Rm)
    w_gmv, _ = S.gmv_long_only(Sig)
    try:
        from scipy.optimize import minimize
        res = minimize(lambda x: x @ Sig @ x, np.full(N, 1 / N),
                       jac=lambda x: 2 * Sig @ x, bounds=[(0, 1)] * N,
                       constraints=[{"type": "eq", "fun": lambda x: x.sum() - 1}],
                       method="SLSQP", options={"ftol": 1e-16, "maxiter": 2000})
        v_as, v_sl = w_gmv @ Sig @ w_gmv, res.x @ Sig @ res.x
        good = v_as <= v_sl * (1 + 1e-6)
        results.append((PASS if good else FAIL, "GMV = SLSQP 해",
                        f"분산차 {abs(v_as-v_sl)/v_sl:.1e}"))
    except ImportError:
        results.append((PASS, "GMV = SLSQP 해", "scipy 없음 — 건너뜀"))

    good = abs(w_gmv.sum() - 1) < 1e-10 and (w_gmv >= -1e-12).all()
    results.append((PASS if good else FAIL, "GMV 제약(합1·비음수)",
                    f"합 {w_gmv.sum():.10f}, 최소 {w_gmv.min():.2e}"))

    # 11. 종목 상한이 실제로 걸리는가 (쏠린 비중 입력)
    skew = np.zeros(50)
    skew[0] = 0.88
    skew[1:30] = 0.12 / 29
    capped = S.cap_weights(skew, cap=0.15, floor=0.005)
    good = capped.max() <= 0.15 + 1e-9 and abs(capped.sum() - 1) < 1e-9
    results.append((PASS if good else FAIL, "종목 상한 적용",
                    f"88% → {capped.max():.2%}, {int((capped>0).sum())}종목"))

    # 12. 상한 먼저·하한 나중 (순서가 뒤바뀌면 종목이 전멸한다)
    good = int((capped > 0).sum()) >= 20
    results.append((PASS if good else FAIL, "상한→하한 순서 (분산 유지)",
                    f"{int((capped>0).sum())}종목 남음 (20개 이상이어야 함)"))

    # 13. 실행 불가능한 상한은 조용히 넘기지 않고 예외
    expect_raise("상한 infeasible 차단", ValueError,
                 S.cap_weights, np.array([0.8, 0.1, 0.06, 0.03, 0.01]),
                 cap=0.15, floor=0.005)

    # 14. 시장 상한
    w_mkt = pd.Series({"005930": 0.5, "000660": 0.3, "NVDA": 0.15,
                       "BTCUSDT": 0.05})
    out = S.cap_market_weights(w_mkt, max_market_pct=40.0, cap=0.15)
    agg = out.groupby([C.market_of(s) for s in out.index]).sum()
    good = (agg <= 0.40 + 1e-9).all() and abs(out.sum() - 1) < 1e-9
    results.append((PASS if good else FAIL, "시장 상한 40%",
                    "kospi 80% → " + ", ".join(f"{k} {v:.0%}" for k, v in agg.items())))

    # 15. 괴리율 정의 (리밸런싱하면 0으로 초기화)
    g0 = S.gap(1.0, 1.0, 1.0, 1.0)
    g1 = S.gap(1.10, 1.00, 1.0, 1.0)
    good = abs(g0) < 1e-12 and abs(g1 - 0.10) < 1e-12
    results.append((PASS if good else FAIL, "괴리율 계산",
                    f"동기화직후 {g0:.4f}, 가상+10% {g1:.4f}"))

    # 16. 최소 주문금액 미만은 주문표에서 빠지는가
    tw = pd.Series({"005930": 0.5, "000660": 0.5})
    orders = S.plan_orders(tw, pd.Series(dtype=float), 1_000_000,
                           pd.Series({"005930": 70_000, "000660": 300_000}),
                           {"005930": True, "000660": True},
                           min_order_krw=500_000)
    good = len(orders) == 2 and (orders.qty == orders.qty.astype(int)).all()
    results.append((PASS if good else FAIL, "주문표 정수주·최소금액",
                    f"{len(orders)}건, 수량 {list(orders.qty)}"))

    tiny = S.plan_orders(pd.Series({"005930": 0.001}), pd.Series(dtype=float),
                         1_000_000, pd.Series({"005930": 70_000}),
                         {"005930": True}, min_order_krw=500_000)
    results.append((PASS if tiny.empty else FAIL, "최소금액 미만 주문 제외",
                    f"{len(tiny)}건 (0이어야 함)"))

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
