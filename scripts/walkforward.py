#!/usr/bin/env python
"""워크포워드 반복 검증 — 상한 조합이 구간을 바꿔도 버티는지 확인한다.

sweep_caps.py 는 6년 전체 성과 **하나**로 조합을 골랐다. 그러면 그 구간에
우연히 맞는 값을 최적이라고 고를 위험이 있다(보완점 1-3 "성과가 한 시기에 몰림").

여기서는 롤링 워크포워드를 돈다:
    [학습 train 일] 에서 성적 1위 조합을 고름 → [검증 test 일] 에 적용
    → 창을 test 만큼 밀어 반복

핵심 산출물은 **검증 구간만 이어붙인 OOS 곡선**이다. 학습에서 고른 조합이
다음 구간에서도 통했는지가 거기 드러난다. 세 가지를 같은 OOS 구간에서 비교:
  - 적응형: 매 구간 학습 1위를 따라감
  - 고정 20%/60% (현재 채택값)
  - 고정 15%/40% (이전 값)

함께 보는 것: 구간별 1위가 얼마나 자주 바뀌는가. 자주 바뀌면 그 '최적'은 노이즈다.

    .venv/bin/python scripts/walkforward.py
    .venv/bin/python scripts/walkforward.py --train 504 --test 126
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

import btlib
from bot import strategy

CAPS = [1.0, 0.30, 0.20, 0.15, 0.10, 0.05]
MKT_CAPS = [100.0, 60.0, 40.0]
METRIC_KEY = {"샤프": "sharpe", "CAGR": "CAGR", "칼마": "calmar"}


def label(cap: float, mc: float) -> str:
    c = "없음" if cap >= 1 else f"{cap:.0%}"
    m = "없음" if mc >= 100 else f"{mc:.0f}%"
    return f"{c}/{m}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=strategy.WINDOW)
    ap.add_argument("--gap", type=float, default=strategy.GAP_THRESHOLD)
    ap.add_argument("--days", type=int, default=1600)
    ap.add_argument("--train", type=int, default=378, help="학습 거래일 (기본 1.5년)")
    ap.add_argument("--test", type=int, default=126, help="검증 거래일 (기본 6개월)")
    ap.add_argument("--metric", default="샤프", choices=list(METRIC_KEY))
    args = ap.parse_args()
    mkey = METRIC_KEY[args.metric]

    print(f"데이터 수집...")
    px, rets, mkt, cost_vec, onehot = btlib.build_panel(args.days)
    print(f"  패널 {px.shape[0]}거래일 × {px.shape[1]}종목, "
          f"{px.index[0].date()} ~ {px.index[-1].date()}")
    R = rets.to_numpy()

    print("원본 GMV 경로 계산...")
    W_raw, ginfo = btlib.raw_gmv_path(rets, args.window)

    print(f"조합별 비중 경로 생성 ({len(CAPS)}×{len(MKT_CAPS)})...")
    combos = [(c, m) for c in CAPS for m in MKT_CAPS]
    Wmap = {}
    for cap, mc in combos:
        Wmap[(cap, mc)] = btlib.apply_caps(W_raw, mkt, cap, strategy.MIN_WEIGHT, mc)

    # 워크포워드 분할
    T = len(px)
    first = args.window + 2
    folds = []
    s = first
    while s + args.train + args.test <= T:
        folds.append((s, s + args.train, s + args.train + args.test))
        s += args.test
    if not folds:
        print("구간을 만들 수 없습니다 — train/test 를 줄이세요")
        return 1
    print(f"\n워크포워드 {len(folds)}구간 (학습 {args.train}일 → 검증 {args.test}일, "
          f"기준 {args.metric})")

    fixed_a = (0.20, 60.0)      # 현재 채택
    fixed_b = (0.15, 40.0)      # 이전 값

    rows = []
    oos = {"적응형": [], label(*fixed_a): [], label(*fixed_b): []}
    picks = []

    for k, (s0, s1, s2) in enumerate(folds, 1):
        tr_years = (px.index[s1 - 1] - px.index[s0]).days / 365.25
        te_years = (px.index[s2 - 1] - px.index[s1]).days / 365.25

        # 학습 구간에서 1위 고르기
        scores = {}
        for cap, mc in combos:
            res = btlib.simulate(Wmap[(cap, mc)], R, cost_vec, onehot,
                                 args.gap, s0, s1)
            if res is None:
                continue
            scores[(cap, mc)] = btlib.perf(res["nav"], tr_years)[mkey]
        if not scores:
            continue
        best = max(scores, key=lambda k_: scores[k_])
        picks.append(label(*best))

        # 검증 구간 성과
        row = {"구간": k,
               "학습": f"{px.index[s0].date()}~{px.index[s1-1].date()}",
               "검증": f"{px.index[s1].date()}~{px.index[s2-1].date()}",
               "학습1위": label(*best), "학습점수": scores[best]}
        for name, combo in [("적응형", best),
                            (label(*fixed_a), fixed_a),
                            (label(*fixed_b), fixed_b)]:
            res = btlib.simulate(Wmap[combo], R, cost_vec, onehot,
                                 args.gap, s1, s2)
            if res is None:
                row[name] = np.nan
                continue
            p = btlib.perf(res["nav"], te_years)
            row[name] = p["total"]
            row[f"{name}_샤프"] = p["sharpe"]
            oos[name].append(p["total"])
        rows.append(row)
        print(f"  {k:>2}. 학습 {row['학습']} → 1위 {row['학습1위']:>8} "
              f"({args.metric} {scores[best]:.2f})  |  검증 {row['검증']}  "
              f"적응 {row.get('적응형', float('nan')):+7.2%}  "
              f"{label(*fixed_a)} {row.get(label(*fixed_a), float('nan')):+7.2%}  "
              f"{label(*fixed_b)} {row.get(label(*fixed_b), float('nan')):+7.2%}",
              flush=True)

    df = pd.DataFrame(rows)
    out = Path("reports") / f"walkforward_t{args.train}_v{args.test}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"\n{'='*78}")
    print("OOS(검증 구간만 이어붙임) 누적 성과")
    print(f"{'='*78}")
    print(f"{'전략':<12} {'누적':>10} {'평균':>9} {'승률':>7} {'최악':>9} {'최고':>9}")
    print("-" * 78)
    summary = []
    for name, vals in oos.items():
        if not vals:
            continue
        v = np.array(vals)
        cum = float(np.prod(1 + v) - 1)
        summary.append({"전략": name, "OOS누적": cum, "평균": v.mean(),
                        "승률": (v > 0).mean(), "최악": v.min(), "최고": v.max(),
                        "구간수": len(v)})
        print(f"{name:<12} {cum:>+10.2%} {v.mean():>+9.2%} "
              f"{(v>0).mean():>7.0%} {v.min():>+9.2%} {v.max():>+9.2%}")

    print(f"\n[학습 1위가 얼마나 바뀌는가]")
    cnt = Counter(picks)
    for combo, n in cnt.most_common():
        print(f"  {combo:>8}  {n}/{len(picks)}회 1위  {'█'*n}")
    print(f"  → 서로 다른 조합 {len(cnt)}개가 1위를 나눠 가짐")
    if len(cnt) > len(picks) / 2:
        print(f"  ⚠ 1위가 자주 바뀐다 = 학습 구간의 '최적'은 상당 부분 노이즈다")

    sdf = pd.DataFrame(summary)
    sdf.to_csv(Path("reports") / f"walkforward_summary_t{args.train}_v{args.test}.csv",
               index=False, encoding="utf-8-sig")
    print(f"\nCSV: {out}")
    print("\n※ 생존편향은 이 검증으로 해결되지 않는다 — 유니버스가 여전히")
    print("   '현재 거래 가능한 종목'이다. 여기서 보는 것은 구간 안정성뿐이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
