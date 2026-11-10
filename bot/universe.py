"""거래 유니버스 — 서버 종목 코드 ↔ 외부 데이터 소스 티커 매핑.

서버 `/api/quotes` 가 주는 종목이 곧 유니버스다. 공분산 추정에 쓸 과거 가격은
외부에서 받아야 하므로(서버는 시세 히스토리를 주지 않음) 소스별 티커로 변환한다.

- 코스피 `005930`  → yfinance `005930.KS`
- 나스닥 `NVDA`    → yfinance `NVDA`
- 코인   `BTCUSDT` → Binance klines `BTCUSDT`

선물(`.P`, `_PERP`)은 GMV 유니버스에서 제외한다 — 기초자산이 현물과 같아
공분산이 거의 겹치고, 증거금·펀딩비 모델링이 따로 필요하다.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config as C


@dataclass(frozen=True)
class Asset:
    symbol: str        # 서버 종목 코드 (주문에 쓰는 값)
    market: str        # kospi | nasdaq | crypto
    ccy: str           # KRW | USD | USDT
    yf: str | None     # yfinance 티커 (코인은 None)
    integer_shares: bool

    @property
    def is_crypto(self) -> bool:
        return self.market == "crypto"


MARKET_CCY = {"kospi": "KRW", "nasdaq": "USD", "crypto": "USDT"}
INTEGER_SHARES = {"kospi": True, "nasdaq": True, "crypto": False}


def to_asset(symbol: str) -> Asset | None:
    """서버 종목 코드 → Asset. 선물이면 None (GMV 유니버스 제외)."""
    market = C.market_of(symbol)
    if market == "futures":
        return None
    yf = None
    if market == "kospi":
        yf = f"{symbol}.KS"
    elif market == "nasdaq":
        yf = symbol
    return Asset(symbol=symbol, market=market, ccy=MARKET_CCY[market],
                 yf=yf, integer_shares=INTEGER_SHARES[market])


def build(quotes: list[dict]) -> list[Asset]:
    """`/api/quotes` 응답 → 현물 유니버스 (선물 제외, 종목코드 정렬)."""
    out: list[Asset] = []
    for row in quotes:
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue
        a = to_asset(sym)
        if a is not None:
            out.append(a)
    return sorted(out, key=lambda a: (a.market, a.symbol))


def by_market(assets: list[Asset]) -> dict[str, list[Asset]]:
    out: dict[str, list[Asset]] = {}
    for a in assets:
        out.setdefault(a.market, []).append(a)
    return out
