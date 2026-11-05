#!/usr/bin/env python
"""백테스트 비용 상수를 서버 get_rules 실측값과 대조한다.

노트북에 박아둔 COST / FX_COST 가 서버 규칙과 어긋나면 비용을 반영하지 않은
백테스트가 되므로(채점 항목), 돌리기 전에 이걸로 확인한다.
수업 중 서버 규칙이 바뀌면 여기서 바로 잡힌다.

    .venv/bin/python scripts/verify_costs.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import config as C
from bot.client import SSUClient

REPO = Path(__file__).resolve().parent.parent
NOTEBOOKS = ["gmv_backtest.ipynb", "gmv_backtest_v2.ipynb",
             "gmv_backtest_downside.ipynb"]

# 노트북 시장명 → 서버 시장명
MKT = {"KOSPI": "kospi", "NASDAQ": "nasdaq", "CRYPTO": "crypto"}


def notebook_costs(path: Path) -> tuple[dict, dict]:
    """노트북 설정 셀에서 COST / FX_COST 숫자를 뽑아낸다."""
    nb = json.loads(path.read_text(encoding="utf-8"))
    src = "\n".join("".join(c["source"]) for c in nb["cells"]
                    if c["cell_type"] == "code")

    cost: dict[str, float] = {}
    m = re.search(r"^COST\s*=\s*\{(.*?)^\}", src, re.M | re.S)
    if m:
        for key, expr in re.findall(r'"(\w+)":\s*([0-9.+\s]+?)[,\n]', m.group(1)):
            cost[key] = eval(expr.strip(), {"__builtins__": {}})  # 숫자 식만

    fx: dict[frozenset, float] = {}
    m = re.search(r"^FX_COST\s*=\s*\{(.*?)^\}", src, re.M | re.S)
    if m:
        for a, b, val in re.findall(
                r'frozenset\(\{"(\w+)",\s*"(\w+)"\}\):\s*([0-9.]+)', m.group(1)):
            fx[frozenset({a, b})] = float(val)
    return cost, fx


def main() -> int:
    cli = SSUClient()
    rules = cli.get_rules()

    # 서버 실측값
    srv_spot = {name: (info["feeBps"] + info["slipBps"]) / 10_000
                for name, info in rules["markets"].items()}
    srv_fx = {}
    for pair in rules["fx"]["pairs"]:
        srv_fx[frozenset({pair["from"], pair["to"]})] = pair["spreadBps"] / 10_000

    print("서버 실측 (get_rules)")
    for name, info in rules["markets"].items():
        print(f"  {info['label']:<6} 수수료 {info['feeBps']:>2}bps + 슬립 "
              f"{info['slipBps']}bps = 편도 {srv_spot[name]*100:.2f}% "
              f"→ 왕복 {srv_spot[name]*200:.2f}%")
    for k, v in srv_fx.items():
        print(f"  환전 {'↔'.join(sorted(k)):<12} {v*100:.2f}%")
    fut = rules["futures"]
    print(f"  선물 taker {fut['takerBps']}bps + 슬립 {fut['slipBps']}bps "
          f"= 편도 {(fut['takerBps']+fut['slipBps'])/100:.2f}%")

    ok = True

    # bot/config.py 대조
    print("\nbot/config.py")
    for name in srv_spot:
        mine = (C.FEE_BPS[name] + C.SLIP_BPS[name]) / 10_000
        good = abs(mine - srv_spot[name]) < 1e-9
        ok &= good
        print(f"  {'✓' if good else '✗'} {name:<8} {mine*100:.2f}% "
              f"(서버 {srv_spot[name]*100:.2f}%)")
    for k, v in srv_fx.items():
        a, b = sorted(k)
        bps = C.FX_SPREAD_BPS.get((a, b)) or C.FX_SPREAD_BPS.get((b, a))
        if bps is None:
            print(f"  ✗ 환전 {a}↔{b} 상수 없음")
            ok = False
            continue
        mine = bps / 10_000
        good = abs(mine - v) < 1e-9
        ok &= good
        print(f"  {'✓' if good else '✗'} 환전 {a}↔{b:<5} {mine*100:.2f}% "
              f"(서버 {v*100:.2f}%)")
    fut_mine = (C.FUTURES_TAKER_BPS + C.FUTURES_SLIP_BPS) / 10_000
    fut_srv = (fut["takerBps"] + fut["slipBps"]) / 10_000
    good = abs(fut_mine - fut_srv) < 1e-9
    ok &= good
    print(f"  {'✓' if good else '✗'} 선물     {fut_mine*100:.2f}% "
          f"(서버 {fut_srv*100:.2f}%)")

    # 노트북 대조
    for nbname in NOTEBOOKS:
        path = REPO / nbname
        if not path.is_file():
            print(f"\n{nbname}: 파일 없음 — 건너뜀")
            continue
        cost, fx = notebook_costs(path)
        print(f"\n{nbname}")
        if not cost:
            print("  ✗ COST 를 찾지 못했습니다")
            ok = False
        for nb_key, srv_key in MKT.items():
            if nb_key not in cost:
                print(f"  ✗ {nb_key} 없음")
                ok = False
                continue
            good = abs(cost[nb_key] - srv_spot[srv_key]) < 1e-9
            ok &= good
            print(f"  {'✓' if good else '✗'} {nb_key:<8} {cost[nb_key]*100:.2f}% "
                  f"(서버 {srv_spot[srv_key]*100:.2f}%)")
        for k, v in srv_fx.items():
            a, b = sorted(k)
            if k not in fx:
                print(f"  ✗ 환전 {a}↔{b} 없음")
                ok = False
                continue
            good = abs(fx[k] - v) < 1e-9
            ok &= good
            print(f"  {'✓' if good else '✗'} 환전 {a}↔{b:<5} {fx[k]*100:.2f}% "
                  f"(서버 {v*100:.2f}%)")

    # 펀딩비 — 선물 보유 비용 (백테스트에서 선물을 쓰면 반영해야 함)
    print("\n선물 펀딩비 (현재)")
    for m in fut["marks"]:
        fr = m["funding_rate"]
        print(f"  {m['symbol']:<13} {fr*100:+.4f}%/8h → 연 {fr*3*365*100:+.1f}% "
              f"({'롱 부담' if fr > 0 else '숏 부담'})")

    print(f"\n{'전부 일치' if ok else '불일치 있음 — 노트북/config 를 서버 값으로 맞추세요'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
