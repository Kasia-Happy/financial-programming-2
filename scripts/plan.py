#!/usr/bin/env python
"""오늘의 GMV 목표 비중과 주문표를 계산한다 — 주문은 내지 않는다 (드라이런).

    .venv/bin/python scripts/plan.py              # 상한 적용 (기본)
    .venv/bin/python scripts/plan.py --no-cap     # 상한 없음 (원본 GMV, 비교용)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from bot import config as C
from bot import data, journal, strategy, universe
from bot.client import SSUClient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cap", action="store_true",
                    help="비중 상한·하한 없이 원본 GMV 비중 (비교용)")
    ap.add_argument("--window", type=int, default=strategy.WINDOW)
    ap.add_argument("--top", type=int, default=15, help="표시할 상위 종목 수")
    args = ap.parse_args()

    cap = 1.0 if args.no_cap else strategy.MAX_WEIGHT
    floor = 0.0 if args.no_cap else strategy.MIN_WEIGHT

    cli = SSUClient()
    quotes = cli.get_quotes()
    assets = universe.build(quotes)
    by_mkt = universe.by_market(assets)
    print(f"유니버스: {len(assets)}종목 "
          + " / ".join(f"{k} {len(v)}" for k, v in sorted(by_mkt.items())))

    print(f"\n과거 가격 수집 (윈도우 {args.window}거래일)...")
    px, fx = data.price_panel(assets, args.window)
    rets = data.returns(px)
    print(f"  패널 {px.shape[0]}거래일 × {px.shape[1]}종목, "
          f"{px.index[0].date()} ~ {px.index[-1].date()}, USD/KRW {fx.iloc[-1]:,.2f}")

    w, diag = strategy.target_weights(rets, args.window, cap, floor)
    w = w[w > 1e-9]
    print(f"\nGMV 목표 비중 ({'상한 없음' if args.no_cap else f'상한 {cap:.0%}·하한 {floor:.1%}'})")
    print(f"  투자가능 {diag['n_eligible']}종목 → 편입 {len(w)}종목, "
          f"수축강도 {diag['shrink']}, 사전변동성 {diag['ex_ante_vol']:.2%}/년")
    print(f"  최대 비중 {diag['max_weight']:.2%}, 기준일 {diag['asof']}")

    agg = w.groupby([C.market_of(s) for s in w.index]).sum()
    print("\n시장별 비중:")
    for m, v in agg.sort_values(ascending=False).items():
        flag = "  ← 시장 한도 초과" if v * 100 > C.MAX_MARKET_PCT else ""
        print(f"  {m:<8} {v:7.2%}{flag}")

    print(f"\n상위 {args.top}종목:")
    for s, v in w.head(args.top).items():
        print(f"  {s:<10} {v:7.2%}  ({C.market_of(s)})")
    if len(w) > args.top:
        print(f"  ... 외 {len(w)-args.top}종목")

    # 주문표 (현재 전액 현금이면 전부 신규 매수)
    pf = cli.get_portfolio()
    nav = float(pf["equityKrw"])
    holdings = pd.Series(dtype=float)
    for p in pf.get("positions", []):
        sym = str(p.get("symbol", "")).upper()
        val = p.get("valueKrw") or p.get("marketValueKrw") or 0
        if sym:
            holdings[sym] = float(val)

    px_last = px.iloc[-1]
    integer = {a.symbol: a.integer_shares for a in assets}
    orders = strategy.plan_orders(w, holdings, nav, px_last, integer)

    print(f"\n주문표 (총자산 {nav:,.0f}원, 최소주문 {strategy.MIN_ORDER_KRW:,.0f}원)")
    if orders.empty:
        print("  낼 주문이 없습니다.")
    else:
        buy = orders[orders.side == "buy"]
        sell = orders[orders.side == "sell"]
        print(f"  매수 {len(buy)}건 {buy.notional_krw.sum():,.0f}원 / "
              f"매도 {len(sell)}건 {sell.notional_krw.sum():,.0f}원")
        need = orders.groupby("ccy").notional_krw.sum()
        print("  통화별 필요액:", ", ".join(f"{k} {v:,.0f}원" for k, v in need.items()))
        for ccy in ("USD", "USDT"):
            if ccy in need.index:
                bps = C.FX_SPREAD_BPS[("KRW", ccy)]
                print(f"    → KRW→{ccy} 환전 {need[ccy]:,.0f}원 × {bps/100:.2f}% "
                      f"= 비용 {need[ccy]*bps/10000:,.0f}원 "
                      f"(정산 {C.SETTLE_MS[('KRW', ccy)]//1000}초)")
        est_fee = sum(r.notional_krw * (C.FEE_BPS[r.market] + C.SLIP_BPS[r.market]) / 10000
                      for r in orders.itertuples())
        print(f"  예상 매매비용 {est_fee:,.0f}원 ({est_fee/nav*100:.3f}% of NAV)")
        print(f"\n  {'종목':<10} {'방향':<5} {'수량':>12} {'금액(원)':>14} {'목표비중':>8} {'본전':>7}")
        for r in orders.head(25).itertuples():
            print(f"  {r.symbol:<10} {r.side:<5} {r.qty:>12,.4g} "
                  f"{r.notional_krw:>14,.0f} {r.target_w:>8.2%} "
                  f"{r.breakeven_bps/100:>6.2f}%")
        if len(orders) > 25:
            print(f"  ... 외 {len(orders)-25}건")

    journal.write("plan", diag=diag, cap=cap, floor=floor,
                  n_orders=0 if orders.empty else len(orders),
                  market_weights={k: round(float(v), 4) for k, v in agg.items()},
                  top=[(s, round(float(v), 4)) for s, v in w.head(10).items()])
    print("\n(드라이런 — 주문을 내지 않았습니다. logs/plan.jsonl 에 기록)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
