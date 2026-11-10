"""상한 스윕·워크포워드가 공유하는 백테스트 엔진.

sweep_caps.py 와 walkforward.py 가 같은 시뮬레이션을 쓰도록 분리했다.
비용은 서버 get_rules 실측값(시장별 fee+slip, 통화 주머니 간 환전 스프레드),
체결은 신호 다음 거래일(t+1) 종가 가정.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from bot import config as C
from bot import data, strategy, universe
from bot.client import SSUClient

ANN = 252
CCYS = ["KRW", "USD", "USDT"]
CCY_OF = {"kospi": "KRW", "nasdaq": "USD", "crypto": "USDT"}


def build_panel(days: int):
    """가격 패널·수익률·시장·비용벡터·통화 원핫."""
    cli = SSUClient()
    assets = universe.build(cli.get_quotes())
    px, fx = data.price_panel(assets, days)
    rets = data.returns(px)
    mkt = np.array([C.market_of(s) for s in px.columns])
    cost_vec = np.array([(C.FEE_BPS[m] + C.SLIP_BPS[m]) / 10_000 for m in mkt])
    onehot = np.array([[1.0 if CCY_OF[m] == c else 0.0 for m in mkt] for c in CCYS])
    return px, rets, mkt, cost_vec, onehot


def raw_gmv_path(rets: pd.DataFrame, window: int, verbose: bool = True):
    """하루 한 번 원본 GMV(상한 적용 전)를 푼다. 모든 조합이 재사용한다."""
    R = rets.to_numpy()
    T, N = R.shape
    W = np.zeros((T, N))
    info = []
    t0 = time.time()
    for i in range(window, T):
        Rw = R[i - window + 1: i + 1]
        elig = ~np.isnan(Rw).any(axis=0)
        if elig.sum() < 2:
            continue
        eidx = np.flatnonzero(elig)
        keep = Rw[:, eidx].std(axis=0) > 1e-10
        if keep.sum() < 2:
            continue
        idx = eidx[keep]
        S, shrink = strategy.estimate_cov(Rw[:, idx])
        try:
            w, _ = strategy.gmv_long_only(S)
        except Exception:
            continue
        W[i, idx] = w
        info.append((i, len(idx), float(w.max()), shrink))
        if verbose and i % 400 == 0:
            print(f"    GMV {i}/{T} ({time.time()-t0:.0f}s)", flush=True)
    if verbose:
        print(f"    GMV 완료 {time.time()-t0:.0f}s, 유효 {len(info)}일")
    return W, pd.DataFrame(info, columns=["i", "n_elig", "raw_max", "shrink"])


def apply_caps(W_raw: np.ndarray, mkt: np.ndarray, cap: float, floor: float,
               mkt_cap: float) -> np.ndarray:
    """원본 GMV 경로에 종목상한 → 하한 → 시장상한을 적용."""
    T, N = W_raw.shape
    out = np.zeros_like(W_raw)
    markets = sorted(set(mkt))
    sel = {m: (mkt == m) for m in markets}
    limit = mkt_cap / 100
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
                    v[sel[m]] += np.minimum(freed * r / tot * head / head.sum(), head)
                s = v.sum()
                if s > 0:
                    v /= s
        s = v.sum()
        if s > 0:
            out[i] = v / s
    return out


def _fx_cost_fn():
    bps = {}
    for a in CCYS:
        for b in CCYS:
            if a != b:
                bps[(a, b)] = C.FX_SPREAD_BPS.get(
                    (a, b), C.FX_SPREAD_BPS.get((b, a), 0))

    def fx_cost(cur, tgt):
        d = tgt - cur
        out = [(CCYS[k], -d[k]) for k in range(3) if d[k] < -1e-9]
        inn = [(CCYS[k], d[k]) for k in range(3) if d[k] > 1e-9]
        if not out or not inn:
            return 0.0
        if len(out) == 1:
            src = out[0][0]
            return sum(a * bps[(src, dst)] / 10_000 for dst, a in inn)
        dst = inn[0][0]
        return sum(a * bps[(src, dst)] / 10_000 for src, a in out)
    return fx_cost


def simulate(W: np.ndarray, R: np.ndarray, cost_vec: np.ndarray,
             onehot: np.ndarray, gap_thr: float,
             start: int, end: int | None = None, lag: int = 1):
    """[start, end) 구간 괴리율 리밸런싱 시뮬레이션. nav 는 1.0 에서 시작."""
    T = R.shape[0] if end is None else min(end, R.shape[0])
    R0 = np.nan_to_num(R)
    fx_cost = _fx_cost_fn()

    def rebal(h, nav, w):
        tgt = w * nav
        tc = float(cost_vec @ np.abs(tgt - h))
        fc = fx_cost(onehot @ h, onehot @ tgt)
        newnav = nav - tc - fc
        return (w * newnav, newnav, tc, fc,
                float(np.abs(tgt - h).sum() / 2 / nav) if nav else 0.0)

    i0 = start
    while i0 < T and W[max(i0 - lag, 0)].sum() <= 0:
        i0 += 1
    if i0 >= T - 5:
        return None

    nav = 1.0
    h, nav, tc0, fc0, _ = rebal(np.zeros(R.shape[1]), nav, W[i0 - lag])
    v = 1.0
    nav_sync, v_sync = nav, v
    navs = [nav]
    tc_sum, fc_sum, turn_sum, n_reb = tc0, fc0, 1.0, 0
    pending = None

    for i in range(i0 + 1, T):
        h = h * (1 + R0[i])
        nav = h.sum()
        if W[i - 1].sum() > 0:
            v *= 1 + W[i - 1] @ R0[i]
        if pending is not None and i - pending >= lag and W[pending].sum() > 0:
            h, nav, tc, fc, turn = rebal(h, nav, W[pending])
            tc_sum += tc
            fc_sum += fc
            turn_sum += turn
            n_reb += 1
            nav_sync, v_sync = nav, v
            pending = None
        elif pending is None and np.isfinite(gap_thr):
            g = (abs(v / v_sync - nav / nav_sync)
                 if v_sync > 0 and nav_sync > 0 else 0.0)
            if g >= gap_thr:
                pending = i
        navs.append(nav)

    return dict(nav=pd.Series(navs), tc=tc_sum, fc=fc_sum,
                turnover=turn_sum, n_rebal=n_reb, i0=i0, i1=T)


def perf(nav: pd.Series, years: float) -> dict:
    r = nav.pct_change().dropna()
    if len(r) < 2 or years <= 0:
        return dict(CAGR=0.0, vol=0.0, sharpe=0.0, sortino=0.0,
                    MDD=0.0, calmar=0.0, total=0.0)
    cagr = float(nav.iloc[-1]) ** (1 / years) - 1
    vol = float(r.std() * np.sqrt(ANN))
    dn = float(np.sqrt((np.minimum(r, 0) ** 2).mean()) * np.sqrt(ANN))
    mdd = float((nav / nav.cummax() - 1).min())
    return dict(CAGR=cagr, vol=vol,
                sharpe=float(r.mean() * ANN / vol) if vol > 0 else 0.0,
                sortino=float(r.mean() * ANN / dn) if dn > 0 else 0.0,
                MDD=mdd, calmar=cagr / abs(mdd) if mdd else 0.0,
                total=float(nav.iloc[-1]) - 1)
