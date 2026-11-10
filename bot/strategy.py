"""GMV(최소분산) 포트폴리오 + 괴리율 리밸런싱 — 백테스트 노트북에서 이식.

출처: `gmv_backtest_v2.ipynb` (3-2 active-set GMV, estimate_cov, 괴리율 규칙)

전략:
1. 가상 포트폴리오 — 매일 롤링 윈도우 공분산으로 GMV 비중을 유지 (비용 없음, 기준선)
2. 실제 포트폴리오 — 시작 시 가상과 맞춘 뒤 매매하지 않고 보유 (비중이 흘러감)
3. 괴리율 = |가상 누적수익률 − 실제 누적수익률| 이 임계값 이상이면 리밸런싱

라이브에서 백테스트와 달라지는 점:
- 백테스트는 종가 1개로 하루를 처리하지만, 라이브는 장 시간·정산 지연이 실재한다.
  리밸런싱은 **같은 통화 안에서 먼저** 하고 환전이 필요한 부분은 분리한다.
- 괴리율 기준점(`sync`)은 상태 파일에 저장해 재시작 후에도 이어진다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config as C

# ─────────────────────────────────────────────────────────────
# 전략 파라미터 — 백테스트 선택값 (3M 윈도우 · 괴리율 9%)
# ─────────────────────────────────────────────────────────────
WINDOW = 63                 # 롤링 윈도우 (거래일) = 3개월
GAP_THRESHOLD = 0.09        # 괴리율 임계값 9%

# 보완점 4-1: GMV 가 채권 ETF 한 종목에 70~85% 쏠린다. 서버는 분배금을 주지
# 않아 그 종목의 가격 수익률은 연 1.1%. 상한을 걸어 쏠림을 막는다.
MAX_WEIGHT = C.MAX_POSITION_PCT / 100     # 종목별 비중 상한 (리스크 한도와 동일)

# 보완점 4-2: 목표 금액이 1주 가격보다 작은 종목이 많아 관리 부담만 늘린다.
MIN_WEIGHT = 0.005          # 0.5% 미만 비중은 버리고 재정규화
# 최소 주문 금액은 config.min_order_krw(nav) 로 총자산에 비례해 계산한다
# (자본이 1억 → 100억으로 바뀌면 고정 50만원은 비중 0.005% 로 무의미해진다)


def estimate_cov(Rw: np.ndarray) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf 수축 공분산 (상관계수만 수축, 각 자산 분산은 보존).

    Rw: (T일 × N자산) 수익률 행렬, NaN 없음.
    반환: (N×N) 공분산, 수축 강도.
    """
    T, N = Rw.shape
    X = Rw - Rw.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd > 0, sd, 1e-12)

    Z = X / sd
    Cm = Z.T @ Z / T
    delta = ((Cm - np.eye(N)) ** 2).sum() / N
    Z2 = Z ** 2
    beta = ((Z2.T @ Z2) / T - Cm ** 2).sum() / (N * T)
    shrink = 0.0 if delta == 0 else min(beta, delta) / delta
    C_s = shrink * np.eye(N) + (1 - shrink) * Cm
    return sd[:, None] * C_s * sd[None, :], float(shrink)


def gmv_long_only(S: np.ndarray, w0: np.ndarray | None = None,
                  tol: float = 1e-12, max_iter: int = 2000) -> tuple[np.ndarray, int]:
    """롱온리 GMV: min wᵀΣw s.t. Σw=1, w≥0. primal active-set 으로 정확히 푼다.

    범용 최적화(SLSQP)보다 수백 배 빠르고 같은 해를 준다 (노트북에서 검증됨).
    """
    N = S.shape[0]
    scale = np.median(np.diag(S))
    S = S / (scale if scale > 0 else 1.0)
    w = (np.full(N, 1.0 / N) if w0 is None else w0.astype(float).copy())
    fixed = w <= 0
    if fixed.all():
        fixed = np.zeros(N, dtype=bool)
        w = np.full(N, 1.0 / N)
    w[fixed] = 0.0

    for it in range(max_iter):
        F = np.flatnonzero(~fixed)
        if len(F) == 0:
            break
        x = np.linalg.solve(S[np.ix_(F, F)], np.ones(len(F)))
        w_star = x / x.sum()
        p = w_star - w[F]

        if np.abs(p).max() < 1e-12:
            lam = 1.0 / x.sum()
            mu = S @ w - lam
            mu[~fixed] = np.inf
            j = int(np.argmin(mu))
            if mu[j] >= -tol:
                w = np.clip(w, 0, None)
                return w / w.sum(), it
            fixed[j] = False
        else:
            neg = p < -1e-15
            steps = np.full(len(F), np.inf)
            steps[neg] = -w[F][neg] / p[neg]
            k = int(np.argmin(steps))
            alpha = min(1.0, steps[k])
            w[F] += alpha * p
            if alpha < 1.0:
                fixed[F[k]] = True
                w[F[k]] = 0.0
    raise RuntimeError("GMV active-set 수렴 실패")


def cap_weights(w: np.ndarray, cap: float = MAX_WEIGHT,
                floor: float = MIN_WEIGHT) -> np.ndarray:
    """비중 상한·하한을 적용하고 재정규화한다.

    순서가 중요하다: **상한 먼저, 하한 나중.**
    원본 GMV 는 한 종목에 80~90% 가 쏠리고 나머지는 전부 소수점 비중이다.
    하한을 먼저 걸면 그 소수점들이 전멸해 7종목만 남고 분산이 오히려 나빠진다
    (실측: 사전변동성 6.98% → 10.59%). 상한으로 먼저 펼친 뒤 하한을 걸어야
    종목 수가 유지된다.

    상한 c 를 지키려면 비중>0 종목이 최소 ⌈1/c⌉ 개 필요하다. 종목이 그보다
    적으면 지킬 수 없으므로(infeasible) 1/n 까지 완화하고, 그조차 하드 리스크
    한도를 넘으면 ValueError — 조용히 한도를 넘기지 않는다.
    """
    w = np.clip(w.astype(float), 0, None)
    if w.sum() <= 0:
        return w
    w = w / w.sum()

    def _apply_cap(v: np.ndarray, c: float) -> np.ndarray:
        n = int((v > 0).sum())
        if c >= 1.0 or n == 0:
            return v
        if c * n < 1.0:
            relaxed = 1.0 / n
            hard = C.MAX_POSITION_PCT / 100
            if relaxed > hard + 1e-12:
                raise ValueError(
                    f"종목이 {n}개뿐이라 비중 상한 {c:.1%}를 지킬 수 없습니다 "
                    f"(최소 {int(np.ceil(1/c))}개 필요). 균등배분해도 "
                    f"{relaxed:.1%} > 리스크 한도 {hard:.1%}")
            c = relaxed
        for _ in range(1000):
            over = v > c + 1e-15
            if not over.any():
                break
            excess = (v[over] - c).sum()
            v[over] = c
            free = (~over) & (v > 0) & (v < c - 1e-15)
            if not free.any():
                break
            room = (c - v[free]).sum()
            if excess <= room + 1e-15:
                v[free] += excess * v[free] / v[free].sum()
            else:
                v[free] = c
        else:
            raise RuntimeError("비중 상한 적용이 수렴하지 않았습니다")
        s = v.sum()
        if s <= 0:
            raise ValueError("상한 적용 후 비중 합이 0입니다")
        return v / s

    # 1) 상한 먼저 — 쏠린 비중을 펼친다
    w = _apply_cap(w, cap)

    # 2) 하한 — 펼친 뒤에도 너무 작은 비중은 버린다 (관리 부담만 큼)
    if floor > 0:
        w[w < floor] = 0.0
        if w.sum() <= 0:
            raise ValueError(f"하한 {floor:.1%} 적용 후 남는 종목이 없습니다")
        w = w / w.sum()
        w = _apply_cap(w, cap)          # 재정규화로 상한을 다시 넘을 수 있음

    if cap < 1.0:
        n = int((w > 0).sum())
        eff_cap = max(cap, 1.0 / n) if n else cap
        if (w > eff_cap + 1e-9).any():
            raise RuntimeError(
                f"상한 위반이 남았습니다: 최대 {w.max():.4f} > {eff_cap:.4f}")
    return w


def cap_market_weights(w: pd.Series, max_market_pct: float = C.MAX_MARKET_PCT,
                       cap: float = MAX_WEIGHT) -> pd.Series:
    """시장별 비중 상한을 적용한다 (config.MAX_MARKET_PCT).

    한 시장이 한도를 넘으면 그 시장 안에서 비례 축소하고, 줄인 만큼을
    여유 있는 시장에 비례 배분한다. 종목 상한은 그대로 지킨다.
    """
    limit = max_market_pct / 100
    if w.empty or limit >= 1.0:
        return w
    mkt = pd.Series([C.market_of(s) for s in w.index], index=w.index)
    v = w.astype(float).copy()
    v = v / v.sum()

    for _ in range(100):
        agg = v.groupby(mkt).sum()
        over = agg[agg > limit + 1e-12]
        if over.empty:
            break
        # 초과 시장을 한도까지 축소
        freed = 0.0
        for m in over.index:
            sel = mkt == m
            scale = limit / agg[m]
            freed += v[sel].sum() * (1 - scale)
            v[sel] = v[sel] * scale
        # 여유 있는 시장에 배분 (종목 상한 내에서)
        agg = v.groupby(mkt).sum()
        room_mkt = {m: limit - agg.get(m, 0.0) for m in agg.index
                    if m not in over.index}
        total_room = sum(r for r in room_mkt.values() if r > 0)
        if total_room <= 1e-12:
            break
        for m, room in room_mkt.items():
            if room <= 0:
                continue
            sel = mkt == m
            add = freed * room / total_room
            headroom = (cap - v[sel]).clip(lower=0)
            if headroom.sum() <= 1e-15:
                continue
            v[sel] += (add * headroom / headroom.sum()).clip(upper=headroom)
        v = v / v.sum()
    return v[v > 1e-9]


def target_weights(rets: pd.DataFrame, window: int = WINDOW,
                   cap: float = MAX_WEIGHT,
                   floor: float = MIN_WEIGHT,
                   market_cap_pct: float = C.MAX_MARKET_PCT
                   ) -> tuple[pd.Series, dict]:
    """최근 `window` 거래일 수익률로 오늘의 GMV 목표 비중을 계산한다.

    투자 가능 자산 = 윈도우 동안 수익률이 하루도 빠짐없이 있고 표준편차 > 0.
    종목 상한 → 하한 → 시장 상한 순으로 적용한다.
    반환: (종목코드 → 비중) Series, 진단 정보.
    """
    if len(rets) < window:
        raise ValueError(f"수익률이 {len(rets)}일뿐입니다 (윈도우 {window}일 필요)")

    Rw = rets.iloc[-window:]
    elig = Rw.notna().all(axis=0) & (Rw.std(axis=0) > 1e-10)
    cols = list(Rw.columns[elig])
    if len(cols) < 2:
        raise ValueError(f"투자 가능 자산이 {len(cols)}개뿐입니다")

    S, shrink = estimate_cov(Rw[cols].to_numpy())
    w_raw, iters = gmv_long_only(S)
    raw_max = float(w_raw.max())
    w_capped = cap_weights(w_raw, cap, floor)

    s = pd.Series(w_capped, index=cols)
    s = s[s > 1e-9]
    if market_cap_pct < 100:
        s = cap_market_weights(s, market_cap_pct, cap)

    # 사전 변동성은 최종 비중으로 다시 계산
    idx = [cols.index(c) for c in s.index]
    S_sub = S[np.ix_(idx, idx)]
    wv = s.to_numpy()
    ex_ante_vol = float(np.sqrt(wv @ S_sub @ wv * 252))

    mkt_agg = s.groupby([C.market_of(x) for x in s.index]).sum()
    diag = {
        "window": window,
        "n_eligible": len(cols),
        "n_holdings": int(len(s)),
        "shrink": round(shrink, 4),
        "iters": iters,
        "ex_ante_vol": round(ex_ante_vol, 4),
        "raw_max_weight": round(raw_max, 4),
        "max_weight": round(float(s.max()), 4),
        "cap": cap,
        "floor": floor,
        "market_cap_pct": market_cap_pct,
        "market_weights": {k: round(float(v), 4) for k, v in mkt_agg.items()},
        "asof": str(rets.index[-1].date()),
    }
    return s.sort_values(ascending=False), diag


def gap(virtual_nav: float, actual_nav: float,
        virtual_sync: float, actual_sync: float) -> float:
    """괴리율 = |가상 누적수익률 − 실제 누적수익률| (마지막 리밸런싱 기준).

    노트북과 같은 정의. 기준점은 리밸런싱할 때마다 초기화한다.
    """
    if virtual_sync <= 0 or actual_sync <= 0:
        return 0.0
    return abs(virtual_nav / virtual_sync - actual_nav / actual_sync)


def plan_orders(target_w: pd.Series, holdings_krw: pd.Series, nav_krw: float,
                prices_krw: pd.Series, integer: dict[str, bool],
                min_order_krw: float | None = None) -> pd.DataFrame:
    """목표 비중 → 주문표. 같은 통화 안에서 처리할 수 있는 것부터 낸다.

    target_w   : 종목 → 목표 비중
    holdings_krw: 종목 → 현재 보유 평가액 (원화)
    nav_krw    : 총자산 (원화)
    prices_krw : 종목 → 1주 가격 (원화 환산)
    integer    : 종목 → 정수 주 제약 여부
    min_order_krw: 생략하면 총자산에 비례해 계산 (config.min_order_krw)
    """
    if min_order_krw is None:
        min_order_krw = C.min_order_krw(nav_krw)
    syms = sorted(set(target_w.index) | set(holdings_krw.index))
    rows = []
    for s in syms:
        tgt_krw = float(target_w.get(s, 0.0)) * nav_krw
        cur_krw = float(holdings_krw.get(s, 0.0))
        diff = tgt_krw - cur_krw
        px = float(prices_krw.get(s, float("nan")))
        if not np.isfinite(px) or px <= 0:
            continue
        if abs(diff) < min_order_krw:
            continue

        qty = abs(diff) / px
        if integer.get(s, True):
            qty = float(np.floor(qty))
            if qty < 1:
                continue
        if qty <= 0:
            continue

        market = C.market_of(s)
        rows.append({
            "symbol": s,
            "side": "buy" if diff > 0 else "sell",
            "qty": qty,
            "market": market,
            "ccy": {"kospi": "KRW", "nasdaq": "USD", "crypto": "USDT"}[market],
            "price_krw": px,
            "notional_krw": qty * px,
            "target_w": float(target_w.get(s, 0.0)),
            "current_w": cur_krw / nav_krw if nav_krw else 0.0,
            "breakeven_bps": C.roundtrip_bps(market),
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 매도 먼저 (현금을 만든 뒤 매수) → 같은 통화 안에서 해결되는 양을 늘린다
    return df.sort_values(["side", "notional_krw"],
                          ascending=[True, False]).reset_index(drop=True)
