"""과거 가격 수집 — 공분산 추정용.

서버는 시세 히스토리를 주지 않으므로 외부에서 받는다:
- 코스피·나스닥: yfinance (`auto_adjust=False` 의 `Close` = 분할만 반영, 배당 미반영)
- 코인: Binance klines (종가)
- USD/KRW: yfinance `KRW=X`

가격 기준을 **배당·분배금 미반영**으로 맞춘 이유: 서버가 분배금을 지급하지
않고 가격 변동만 반영하기 때문. (DataGuide 수정주가도 같은 기준임을 확인했다 —
KT&G adj_close/close = 1.0, 삼성전자는 분할 비율만 반영.)

캐시: `cache/prices_*.csv`. 같은 날 두 번 받지 않는다 (45명이 같은 서버를 쓰고
yfinance 도 과하게 부르면 429가 난다).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from .universe import Asset

KST = timezone(timedelta(hours=9))
CACHE_DIR = Path(__file__).resolve().parent.parent / "cache"
BINANCE = "https://api.binance.com/api/v3/klines"


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"prices_{name}.csv"


def _fresh(path: Path, max_age_sec: float) -> bool:
    return path.is_file() and (time.time() - path.stat().st_mtime) < max_age_sec


def _read_cache(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df


def fetch_yf(tickers: list[str], days: int, name: str,
             max_age_sec: float = 3600) -> pd.DataFrame:
    """yfinance 일별 종가 (분할만 반영). 캐시가 신선하면 재사용."""
    path = _cache_path(name)
    if _fresh(path, max_age_sec):
        return _read_cache(path)

    import yfinance as yf

    period_days = int(days * 1.9) + 40        # 휴장일 여유
    raw = yf.download(tickers, period=f"{period_days}d", interval="1d",
                      auto_adjust=False, progress=False, threads=True)
    if raw is None or len(raw) == 0:
        raise RuntimeError(f"yfinance 응답이 비었습니다: {name}")
    close = raw["Close"] if "Close" in raw else raw
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    close = close.dropna(how="all")
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    close.index.name = "date"
    close.to_csv(path)
    return close


def fetch_binance(symbols: list[str], days: int,
                  max_age_sec: float = 3600) -> pd.DataFrame:
    """Binance 일봉 종가 (UTC 기준 일자)."""
    path = _cache_path("crypto")
    if _fresh(path, max_age_sec):
        return _read_cache(path)

    # Binance 는 한 번에 최대 1000 봉만 준다. days 가 그보다 크면
    # startTime 을 밀어가며 여러 번 받아 이어붙여야 한다.
    # (이게 없으면 몇 년을 요청해도 조용히 마지막 1000일만 들어온다.)
    need_ms = int((days + 40) * 86_400_000)
    start0 = int(time.time() * 1000) - need_ms
    series: dict[str, pd.Series] = {}
    for sym in symbols:
        rows: list = []
        start = start0
        while True:
            try:
                resp = requests.get(BINANCE, timeout=20, params={
                    "symbol": sym, "interval": "1d",
                    "startTime": start, "limit": 1000})
                resp.raise_for_status()
                page = resp.json()
            except (requests.RequestException, ValueError):
                break
            if not page:
                break
            rows.extend(page)
            if len(page) < 1000:               # 마지막 페이지
                break
            start = page[-1][0] + 86_400_000   # 다음 날부터
            time.sleep(0.12)                   # 공개 API 예절
        if not rows:
            continue
        idx = pd.to_datetime([r[0] for r in rows], unit="ms").normalize()
        s = pd.Series([float(r[4]) for r in rows], index=idx)
        series[sym] = s[~s.index.duplicated(keep="last")]
        time.sleep(0.12)

    if not series:
        raise RuntimeError("Binance 종가를 하나도 받지 못했습니다.")
    df = pd.DataFrame(series).sort_index()
    df.index.name = "date"
    df.to_csv(path)
    return df


def fetch_fx(days: int, max_age_sec: float = 3600) -> pd.Series:
    """USD/KRW 일별 (원화 환산용)."""
    df = fetch_yf(["KRW=X"], days, "fx", max_age_sec)
    return df.iloc[:, 0].rename("USDKRW")


def price_panel(assets: list[Asset], window: int,
                max_age_sec: float = 3600) -> tuple[pd.DataFrame, pd.Series]:
    """원화 환산 가격 패널 + USD/KRW 시계열.

    열 이름 = 서버 종목 코드. 거래일 달력은 코스피·나스닥 합집합.
    코인은 365일 거래라 주식 휴장일엔 직전 값으로 채워진다(ffill).
    """
    ks = [a for a in assets if a.market == "kospi"]
    nq = [a for a in assets if a.market == "nasdaq"]
    cr = [a for a in assets if a.market == "crypto"]

    frames: list[pd.DataFrame] = []
    if ks:
        df = fetch_yf([a.yf for a in ks if a.yf], window, "kospi", max_age_sec)
        df.columns = [str(c).replace(".KS", "") for c in df.columns]
        frames.append(df)
    if nq:
        df = fetch_yf([a.yf for a in nq if a.yf], window, "nasdaq", max_age_sec)
        frames.append(df)
    if cr:
        frames.append(fetch_binance([a.symbol for a in cr], window, max_age_sec))

    px_local = pd.concat(frames, axis=1).sort_index()
    fx = fetch_fx(window, max_age_sec)

    # 거래일 달력 = 주식시장 달력 (코인 단독 거래일은 제외)
    stock_cols = [a.symbol for a in ks + nq if a.symbol in px_local.columns]
    cal = px_local.index if not stock_cols else \
        px_local.index[px_local[stock_cols].notna().any(axis=1)]
    px_local = px_local.reindex(cal).ffill()

    fx_aligned = fx.reindex(fx.index.union(cal)).ffill().reindex(cal).ffill().bfill()

    # USD·USDT 자산을 원화로 환산 (USDT ≈ USD 로 취급)
    px = px_local.copy()
    usd_syms = [a.symbol for a in nq + cr if a.symbol in px.columns]
    px[usd_syms] = px_local[usd_syms].mul(fx_aligned, axis=0)
    return px, fx_aligned


def returns(px: pd.DataFrame) -> pd.DataFrame:
    """일간 수익률. 데이터가 없는 날은 NaN 유지 (0으로 채우지 않는다)."""
    r = px.pct_change(fill_method=None)
    return r.where(px.notna() & px.shift(1).notna())
