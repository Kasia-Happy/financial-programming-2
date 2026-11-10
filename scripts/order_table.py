#!/usr/bin/env python
"""전체 매수 주문표를 표로 출력하고 CSV로 저장한다 — 주문은 내지 않는다.

    .venv/bin/python scripts/order_table.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from bot import config as C
from bot import data, strategy, universe
from bot.client import SSUClient


def main() -> int:
    cli = SSUClient()
    quotes = cli.get_quotes()
    assets = universe.build(quotes)
    px, fx = data.price_panel(assets, strategy.WINDOW)
    rets = data.returns(px)
    w, diag = strategy.target_weights(rets, strategy.WINDOW)

    pf = cli.get_portfolio()
    nav = float(pf["equityKrw"])
    hold = pd.Series(dtype=float)
    for p in pf.get("positions", []):
        s = str(p.get("symbol", "")).upper()
        v = p.get("valueKrw") or p.get("marketValueKrw") or 0
        if s:
            hold[s] = float(v)

    integer = {a.symbol: a.integer_shares for a in assets}
    o = strategy.plan_orders(w, hold, nav, px.iloc[-1], integer)
    if o.empty:
        print("낼 주문이 없습니다.")
        return 0

    names = {str(r["symbol"]).upper(): r.get("name") for r in quotes}
    o["name"] = [str(names.get(s, s))[:18] for s in o.symbol]
    o["fee_krw"] = [r.notional_krw * (C.FEE_BPS[r.market] + C.SLIP_BPS[r.market]) / 10000
                    for r in o.itertuples()]
    o = o.sort_values("notional_krw", ascending=False).reset_index(drop=True)

    out = Path("reports") / "order_table.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    o.to_csv(out, index=False, encoding="utf-8-sig")

    rules = cli.get_rules()
    print(f"기준일 {diag['asof']} · NAV {nav:,.0f}원 · 편입 {len(o)}종목 "
          f"· 사전변동성 {diag['ex_ante_vol']:.2%}/년")
    print(f"장 상태: " + " / ".join(
        f"{i['label']} {'열림' if i.get('open') else '닫힘'}"
        for i in rules["markets"].values()))

    print(f"\n{'#':>3} {'종목':<10} {'이름':<19} {'시장':<7} {'통화':<5} "
          f"{'수량':>14} {'금액(원)':>16} {'비중':>7} {'수수료':>11} {'본전':>6}")
    print("─" * 118)
    for i, r in enumerate(o.itertuples(), 1):
        print(f"{i:>3} {r.symbol:<10} {r.name:<19} {r.market:<7} {r.ccy:<5} "
              f"{r.qty:>14,.4g} {r.notional_krw:>16,.0f} {r.target_w*100:>6.2f}% "
              f"{r.fee_krw:>11,.0f} {r.breakeven_bps/100:>5.2f}%")
    print("─" * 118)
    print(f"{'합계':>3} {'':<10} {'':<19} {'':<7} {'':<5} {'':>14} "
          f"{o.notional_krw.sum():>16,.0f} {o.target_w.sum()*100:>6.2f}% "
          f"{o.fee_krw.sum():>11,.0f}")

    print("\n[시장별]")
    g = o.groupby("market").agg(건수=("symbol", "size"),
                                금액=("notional_krw", "sum"),
                                수수료=("fee_krw", "sum"))
    for m, r in g.iterrows():
        print(f"  {m:<8} {int(r.건수):>2}건  {r.금액:>16,.0f}원  "
              f"({r.금액/nav*100:>5.2f}%)  수수료 {r.수수료:>11,.0f}원")

    print("\n[통화별 · 환전 필요액]")
    cg = o.groupby("ccy").notional_krw.sum()
    fx_total = 0.0
    for ccy, amt in cg.items():
        if ccy == "KRW":
            print(f"  {ccy:<5} {amt:>16,.0f}원  환전 불필요")
        else:
            bps = C.FX_SPREAD_BPS[("KRW", ccy)]
            cost = amt * bps / 10000
            fx_total += cost
            sec = C.SETTLE_MS[("KRW", ccy)] // 1000
            print(f"  {ccy:<5} {amt:>16,.0f}원  × {bps/100:.2f}% = "
                  f"{cost:>13,.0f}원  (정산 {sec}초)")

    fee_total = o.fee_krw.sum()
    print(f"\n[총 진입비용]")
    print(f"  환전     {fx_total:>16,.0f}원")
    print(f"  매매     {fee_total:>16,.0f}원")
    print(f"  합계     {fx_total+fee_total:>16,.0f}원  "
          f"= NAV의 {(fx_total+fee_total)/nav*100:.3f}%")
    print(f"  → 본전까지 필요한 수익률 {(fx_total+fee_total)/nav*100:.3f}%")

    print(f"\nCSV 저장: {out}")
    print("(드라이런 — 주문을 내지 않았습니다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
