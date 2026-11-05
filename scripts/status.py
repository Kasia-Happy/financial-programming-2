#!/usr/bin/env python
"""현재 상태 한눈에 보기 — 돈을 움직이지 않는 읽기 전용 점검.

    .venv/bin/python scripts/status.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as C
from bot.client import SSUClient
from bot import journal


def main() -> int:
    cli = SSUClient()

    h = cli.health()
    print(f"서버: ok={h.get('ok')} 학생={h.get('students')}명 "
          f"시세={h.get('quotes')}종목 선물마크={h.get('futuresMarks')}")

    pf = cli.get_portfolio()
    print(f"\n계정 {pf['id']} / 닉네임 {pf.get('nickname')}")
    print(f"  총자산   {pf['equityKrw']:>15,.0f} KRW  "
          f"(수익률 {pf['returnPct']:+.2f}%, MDD {pf['maxDrawdownPct']:.2f}%)")
    print(f"  현금     {pf['cashKrw']:>15,.0f} KRW")
    print(f"  운송중   {pf['transitKrw']:>15,.0f} KRW")
    print(f"  포지션   {pf['positionsKrw']:>15,.0f} KRW")
    print(f"  지갑     KRW {pf['wallets']['KRW']:,.0f} / "
          f"USD {pf['wallets']['USD']:,.2f} / USDT {pf['wallets']['USDT']:,.2f}")
    print(f"  환율     USD {pf['rate']['USD']:,.2f} / USDT {pf['rate']['USDT']:,.2f}")
    print(f"  거래 {pf['trades']}건, 누적수수료 {pf['tradeFeeKrw']:,.0f}원, "
          f"환전비용 {pf['fxCostKrw']:,.0f}원")

    if pf.get("inTransit"):
        print("\n운송 중:")
        for t in pf["inTransit"]:
            print(f"  {t}")

    if pf.get("positions"):
        print("\n포지션:")
        for p in pf["positions"]:
            print(f"  {p}")

    rules = cli.get_rules()
    print("\n장 상태:")
    for name, info in rules["markets"].items():
        state = "열림" if info.get("open") else "닫힘"
        print(f"  {info['label']:<6} {state}   수수료 {info['feeBps']}bps "
              f"+슬립 {info['slipBps']}bps  왕복 {C.roundtrip_bps(name):.0f}bps "
              f"({info['hours']})")

    print("\n선물 마크가 / 펀딩비:")
    for m in rules["futures"]["marks"]:
        fr = m["funding_rate"]
        print(f"  {m['symbol']:<13} {m['mark']:>12,.2f}  "
              f"펀딩 {fr*100:+.4f}%/8h  (연 {fr*3*365*100:+.1f}%)")

    fx = rules["fxNow"]
    print(f"\n환율: USD/KRW {fx['usdkrw']['rate']:,.2f} ({fx['usdkrw']['source']}) / "
          f"USDT/USD {fx['usdtusd']['rate']:.4f} ({fx['usdtusd']['source']})")
    print(f"  KRW/USDT {fx['krwPerUsdt']:,.2f} / "
          f"업비트 {fx['upbit']['rate']:,.0f} (김프 {fx['upbit']['premiumPct']:+.2f}%)")

    print(f"\n리스크 한도 (bot/config.py):")
    print(f"  한 종목 최대 {C.MAX_POSITION_PCT}% / 한 시장 최대 {C.MAX_MARKET_PCT}%")
    print(f"  선물 명목가 최대 {C.MAX_FUTURES_NOTIONAL_PCT}% / "
          f"레버리지 최대 {C.MAX_LEVERAGE}배 / 교차증거금 "
          f"{'허용' if C.ALLOW_CROSS_MARGIN else '금지'}")
    print(f"  손실 한도 -{C.MAX_DRAWDOWN_PCT}% / "
          f"하루 주문 {C.MAX_ORDERS_PER_DAY}건 "
          f"(오늘 {journal.count_today('order')}건)")

    journal.snapshot(pf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
