#!/usr/bin/env python
"""비중 상한 스윕 — 종목 상한 × 시장 상한 조합을 워크포워드로 비교한다.

보완점 4-1("채권 ETF 한 종목에 70~85% 쏠림")에 대한 근거를 만드는 스크립트.
상한 15%/40% 는 원래 리스크 한도에서 가져온 값이라 성과 근거가 없었다.
여기서 여러 값을 실제로 돌려 비교한다.

설계:
- 원본 GMV 해는 상한과 무관하므로 **하루 한 번만** 풀고(캐시), 조합별로는
  상한만 다시 적용한다. 안 그러면 조합 수 × 1500일 만큼 최적화를 푼다.
- 가상 포트폴리오는 매일 GMV 비중 유지(비용 없음), 실제 포트폴리오는 보유만
  하다가 괴리율 ≥ 임계값일 때 리밸런싱(비용 발생) — 라이브 전략과 동일.
- 비용은 서버 get_rules 실측값: 시장별 fee+slip 편도, 통화 주머니 간 환전 스프레드.
- 체결은 신호 다음 거래일(t+1) 종가 — 노트북 v2 와 같은 가정.

한계 (보고서에 반드시 명시):
- 유니버스가 **현재** 거래 가능 종목이라 생존편향이 있다. 상한이 높을수록
  쏠린 종목을 오래 들고 있어 편향을 더 받는다 → 상한 완화가 유리해 보이는
  방향으로 결과가 치우칠 수 있다.
- 기간이 약 6년(코인 상장 이후)뿐이고, 한 번의 전체 기간 성과라 구간 안정성은
  별도 확인이 필요하다.

    .venv/bin/python scripts/sweep_caps.py
    .venv/bin/python scripts/sweep_caps.py --window 126 --gap 0.09
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from bot import config as C
from bot import data, strategy, universe
from bot.client import SSUClient

ANN = 252
CCYS = ["KRW", "USD", "USDT"]


def build_panel(window: int, days: int):
    cli = SSUClient()
    assets = universe.build(cli.get_quotes())
    px, fx = data.price_panel(assets, days)
    rets = data.returns(px)
    mkt = np.array([C.market_of(s) for s in px.columns])
    cost_vec = np.array([(C.FEE_BPS[m] + C.SLIP_BPS[m]) / 10_000 for m in mkt])
    ccy_of = {"kospi": "KRW", "nasdaq": "USD", "crypto": "USDT"}
    onehot = np.array([[1.0 if ccy_of[m] == c else 0.0 for m in mkt] for c in CCYS])
    return px, rets, mkt, cost_vec, onehot


def raw_gmv_path(rets: pd.DataFrame, window: int):
    """하루 한 번 원본 GMV 를 푼다 (상한 적용 전). 조합 전체가 재사용한다."""
    R = rets.to_numpy()
    T, N = R.shape
    W = np.zeros((T, N))
    info = []
    t0 = time.time()
    for i in range(window, T):
        Rw = R[i - window + 1: i + 1]
        elig = ~np.isnan(Rw).any(axis=0)
        if elig.sum() >= 2:
            sub = Rw[:, elig]
            elig_idx = np.flatnonzero(elig)
            keep = sub.std(axis=0) > 1e-10
            if keep.sum() < 2:
                continue
            idx = elig_idx[keep]
            S, shrink = strategy.estimate_cov(Rw[:, idx])
            try:
                w, _ = strategy.gmv_long_only(S)
            except Exception:
                continue
            W[i, idx] = w
            info.append((i, len(idx), float(w.max()), shrink))
        if i % 300 == 0:
            print(f"    GMV {i}/{T} ({time.time()-t0:.0f}s)", flush=True)
    print(f"    GMV 완료 {time.time()-t0:.0f}s, 유효 {len(info)}일")
    return W, pd.DataFrame(info, columns=["i", "n_elig", "raw_max", "shrink"])


def apply_caps(W_raw: np.ndarray, mkt: np.ndarray, cap: float, floor: float,
               mkt_cap: float) -> np.ndarray:
    """원본 GMV 경로에 상한·하한을 적용한 비중 경로."""
    T, N = W_raw.shape
    out = np.zeros_like(W_raw)
    markets = sorted(set(mkt))
    sel = {m: (mkt == m) for m in markets}
    for i in range(T):
        w = W_raw[i]
        if w.sum() <= 0:
            continue
        live = w > 0
        try:
            capped = strategy.cap_weights(w[live].copy(), cap, floor)
        except (ValueError, RuntimeError):
            continue
        v = np.zeros(N)
        v[live] = capped
        if mkt_cap < 100:
            limit = mkt_cap / 100
            for _ in range(60):
                agg = {m: v[sel[m]].sum() for m in markets}
                over = [m for m in markets if agg[m] > limit + 1e-12]
                if not over:
                    break
                freed = 0.0
                for m in over:
                    scale = limit / agg[m]
                    freed += v[sel[m]].sum() * (1 - scale)
                    v[sel[m]] *= scale
                agg = {m: v[sel[m]].sum() for m in markets}
                room = {m: limit - agg[m] for m in markets if m not in over}
                tot = sum(r for r in room.values() if r > 0)
                if tot <= 1e-12:
                    break
                for m, r in room.items():
                    if r <= 0:
                        continue
                    head = np.clip(cap - v[sel[m]], 0, None)
                    if head.sum() <= 1e-15:
                        continue
                    add = freed * r / tot
                    v[sel[m]] += np.minimum(add * head / head.sum(), head)
                s = v.sum()
                if s > 0:
                    v /= s
        s = v.sum()
        if s > 0:
            out[i] = v / s
    return out


def simulate(W: np.ndarray, R: np.ndarray, cost_vec: np.ndarray,
             onehot: np.ndarray, gap_thr: float, start: int, lag: int = 1):
    """가상 GMV 대비 괴리율 리밸런싱 시뮬레이션. 반환: nav 경로, 기록."""
    T = R.shape[0]
    R0 = np.nan_to_num(R)
    fx_bps = {}
    for a in CCYS:
        for b in CCYS:
            if a != b:
                fx_bps[(a, b)] = C.FX_SPREAD_BPS.get((a, b),
                                                      C.FX_SPREAD_BPS.get((b, a), 0))

    def fx_cost(cur, tgt):
        d = tgt - cur
        out = [(CCYS[k], -d[k]) for k in range(3) if d[k] < -1e-9]
        inn = [(CCYS[k], d[k]) for k in range(3) if d[k] > 1e-9]
        if not out or not inn:
            return 0.0
        if len(out) == 1:
            src = out[0][0]
            return sum(a * fx_bps[(src, dst)] / 10_000 for dst, a in inn)
        dst = inn[0][0]
        return sum(a * fx_bps[(src, dst)] / 10_000 for src, a in out)

    def rebal(h, nav, w):
        tgt = w * nav
        tc = float(cost_vec @ np.abs(tgt - h))
        fc = fx_cost(onehot @ h, onehot @ tgt)
        newnav = nav - tc - fc
        return w * newnav, newnav, tc, fc, float(np.abs(tgt - h).sum() / 2 / nav)

    i0 = start
    while i0 < T and W[i0 - lag].sum() <= 0:
        i0 += 1
    if i0 >= T - 10:
        return None

    nav = 1.0
    h, nav, tc0, fc0, _ = rebal(np.zeros(R.shape[1]), nav, W[i0 - lag])
    v = 1.0
    nav_sync, v_sync = nav, v
    navs = [nav]
    costs = [tc0 + fc0]
    tc_sum, fc_sum, turn_sum, n_reb = tc0, fc0, 1.0, 0
    pending = None

    for i in range(i0 + 1, T):
        r = R0[i]
        h = h * (1 + r)
        nav = h.sum()
        if W[i - 1].sum() > 0:
            v *= 1 + W[i - 1] @ r

        if pending is not None and i - pending >= lag and W[pending].sum() > 0:
            h, nav, tc, fc, turn = rebal(h, nav, W[pending])
            tc_sum += tc
            fc_sum += fc
            turn_sum += turn
            n_reb += 1
            nav_sync, v_sync = nav, v
            pending = None
        elif pending is None and np.isfinite(gap_thr):
            g = abs(v / v_sync - nav / nav_sync) if v_sync > 0 and nav_sync > 0 else 0.0
            if g >= gap_thr:
                pending = i
        navs.append(nav)

    nav_s = pd.Series(navs)
    return dict(nav=nav_s, tc=tc_sum, fc=fc_sum, turnover=turn_sum,
                n_rebal=n_reb, i0=i0, T=len(nav_s))


def perf(nav: pd.Series, years: float) -> dict:
    r = nav.pct_change().dropna()
    cagr = nav.iloc[-1] ** (1 / years) - 1
    vol = r.std() * np.sqrt(ANN)
    dn = np.sqrt((np.minimum(r, 0) ** 2).mean()) * np.sqrt(ANN)
    mdd = float((nav / nav.cummax() - 1).min())
    return dict(CAGR=cagr, vol=vol,
                sharpe=r.mean() * ANN / vol if vol > 0 else 0.0,
                sortino=r.mean() * ANN / dn if dn > 0 else 0.0,
                MDD=mdd, calmar=cagr / abs(mdd) if mdd else 0.0,
                total=nav.iloc[-1] - 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, default=strategy.WINDOW)
    ap.add_argument("--gap", type=float, default=strategy.GAP_THRESHOLD)
    ap.add_argument("--days", type=int, default=1600)
    ap.add_argument("--floor", type=float, default=strategy.MIN_WEIGHT)
    args = ap.parse_args()

    print(f"데이터 수집 (윈도우 {args.window}, 요청 {args.days}일)...")
    px, rets, mkt, cost_vec, onehot = build_panel(args.window, args.days)
    print(f"  패널 {px.shape[0]}거래일 × {px.shape[1]}종목, "
          f"{px.index[0].date()} ~ {px.index[-1].date()}")
    R = rets.to_numpy()

    print("원본 GMV 경로 계산 (조합 전체가 재사용)...")
    W_raw, ginfo = raw_gmv_path(rets, args.window)
    print(f"  원본 최대비중: 중앙 {ginfo.raw_max.median():.1%}, "
          f"평균 {ginfo.raw_max.mean():.1%}, 최대 {ginfo.raw_max.max():.1%}")
    print(f"  수축강도 중앙 {ginfo.shrink.median():.3f}")

    start = args.window + 2
    years = (px.index[-1] - px.index[start]).days / 365.25

    caps = [1.0, 0.30, 0.20, 0.15, 0.10, 0.08, 0.05]
    mkt_caps = [100.0, 60.0, 50.0, 40.0, 34.0]

    rows = []
    for cap in caps:
        for mc in mkt_caps:
            W = apply_caps(W_raw, mkt, cap, args.floor, mc)
            valid = W.sum(axis=1) > 0
            if valid.sum() < 100:
                continue
            res = simulate(W, R, cost_vec, onehot, args.gap, start)
            if res is None:
                continue
            p = perf(res["nav"], years)
            nh = (W[valid] > 1e-9).sum(axis=1)
            wmax = W[valid].max(axis=1)
            agg = {m: W[valid][:, mkt == m].sum(axis=1).mean() for m in sorted(set(mkt))}
            rows.append({
                "종목상한": "없음" if cap >= 1 else f"{cap:.0%}",
                "시장상한": "없음" if mc >= 100 else f"{mc:.0f}%",
                "CAGR": p["CAGR"], "변동성": p["vol"], "샤프": p["sharpe"],
                "소르티노": p["sortino"], "MDD": p["MDD"], "칼마": p["calmar"],
                "누적": p["total"],
                "종목수": nh.mean(), "최대비중": wmax.mean(),
                "리밸": res["n_rebal"],
                "연회전율": res["turnover"] / years,
                "연매매비": res["tc"] / years, "연환전비": res["fc"] / years,
                "ko": agg.get("kospi", 0), "na": agg.get("nasdaq", 0),
                "cr": agg.get("crypto", 0),
                "_cap": cap, "_mc": mc,
            })
            print(f"  상한 {rows[-1]['종목상한']:>4} / 시장 {rows[-1]['시장상한']:>4}  "
                  f"CAGR {p['CAGR']:+7.2%} 샤프 {p['sharpe']:5.2f} "
                  f"MDD {p['MDD']:7.2%} 종목 {nh.mean():5.1f} "
                  f"리밸 {res['n_rebal']:>3}", flush=True)

    df = pd.DataFrame(rows)
    if df.empty:
        print("결과 없음")
        return 1

    for c in ["CAGR", "샤프", "소르티노", "칼마", "MDD"]:
        df[f"r_{c}"] = df[c].rank(ascending=False)
    df["평균순위"] = df[[f"r_{c}" for c in ["CAGR", "샤프", "소르티노", "칼마", "MDD"]]].mean(axis=1)
    df = df.sort_values("평균순위")

    out = Path("reports") / f"sweep_caps_w{args.window}_g{args.gap:.2f}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False, encoding="utf-8-sig")

    print(f"\n{'='*112}")
    print(f"워크포워드 결과  (윈도우 {args.window}일 · 괴리율 {args.gap:.0%} · "
          f"하한 {args.floor:.1%} · {years:.1f}년 · 비용 반영)")
    print(f"{'='*112}")
    print(f"{'종목상한':>6} {'시장상한':>6} {'CAGR':>8} {'변동성':>7} {'샤프':>6} "
          f"{'MDD':>8} {'칼마':>6} {'종목수':>6} {'최대비중':>7} {'리밸':>5} "
          f"{'회전율':>7} {'연비용':>7} {'순위':>5}")
    print("-" * 112)
    for r in df.itertuples():
        print(f"{r.종목상한:>6} {r.시장상한:>6} {r.CAGR:>+8.2%} {r.변동성:>7.2%} "
              f"{r.샤프:>6.2f} {r.MDD:>8.2%} {r.칼마:>6.2f} {r.종목수:>6.1f} "
              f"{r.최대비중:>7.1%} {r.리밸:>5} {r.연회전율:>7.2f} "
              f"{(r.연매매비 + r.연환전비):>7.2%} {r.평균순위:>5.1f}")

    print(f"\n[시장 배분 평균]")
    for r in df.head(8).itertuples():
        print(f"  상한 {r.종목상한:>4}/{r.시장상한:>4}  "
              f"코스피 {r.ko:>5.1%} 나스닥 {r.na:>5.1%} 코인 {r.cr:>5.1%}")

    print(f"\nCSV: {out}")
    print("\n※ 한계: 유니버스가 현재 거래 가능 종목이라 생존편향이 있다. 상한이 높을수록")
    print("   쏠린 종목을 오래 들고 있어 편향을 더 받으므로, 상한 완화가 유리해 보이는")
    print("   방향으로 결과가 치우칠 수 있다. 기간도 약 6년 한 구간뿐이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
