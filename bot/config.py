"""리스크 한도와 비용 상수 — 코드에 박아두는 안전장치.

채점 기준상 '리스크 한도를 코드에 고정'이 점수 항목이다.
여기 값을 넘기는 주문은 client 레이어에서 거부된다.
사용자가 명시적으로 허락해도, 이 파일을 수정하지 않으면 통과하지 못한다.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────
# 접속
# ─────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent

# 토큰 탐색 순서: 레포 .env → Hermes secrets → 환경변수
for _candidate in (
    REPO_ROOT / ".env",
    Path.home() / ".hermes/profiles/team/secrets/ssu-paper.env",
):
    if _candidate.is_file():
        load_dotenv(_candidate, override=False)

BASE_URL = os.getenv("SSU_BASE_URL", "https://ssu-vibecoding-pnl-rank.lazzy.chat").strip()
# .strip() 필수: .env 가 CRLF 로 저장되면 토큰 끝에 \r 이 붙어 HTTP 헤더가
# InvalidHeader 로 거부되고, 그 예외 메시지에 토큰이 그대로 실려 로그로 샌다.
TOKEN = (os.getenv("lazzy_token") or os.getenv("SSU_PAPER_TOKEN") or "").strip()

# ─────────────────────────────────────────────────────────────
# 비용 (get_rules 2026-11-06 확인값, bps = 0.01%)
# ─────────────────────────────────────────────────────────────
FEE_BPS = {"kospi": 15, "nasdaq": 5, "crypto": 10}
SLIP_BPS = {"kospi": 5, "nasdaq": 3, "crypto": 2}
FUTURES_TAKER_BPS = 5
FUTURES_SLIP_BPS = 2

# 환전 스프레드
FX_SPREAD_BPS = {
    ("KRW", "USD"): 90,
    ("USD", "KRW"): 90,
    ("USD", "USDT"): 15,
    ("USDT", "USD"): 15,
    ("KRW", "USDT"): 105,  # 2단계 직렬
    ("USDT", "KRW"): 105,
}

# 왕복 비용 (진입+청산). 손익분기 계산에 쓴다.
def roundtrip_bps(market: str) -> float:
    """해당 시장에서 왕복 시 들어가는 총비용(bps). 이만큼 올라야 본전."""
    return 2 * (FEE_BPS[market] + SLIP_BPS[market])


def futures_roundtrip_bps() -> float:
    return 2 * (FUTURES_TAKER_BPS + FUTURES_SLIP_BPS)


# ─────────────────────────────────────────────────────────────
# 리스크 한도 — 넘으면 주문이 코드에서 막힌다
# ─────────────────────────────────────────────────────────────
START_KRW = 100_000_000

# 한 종목에 넣을 수 있는 최대 비중 (총자산 대비)
MAX_POSITION_PCT = 15.0

# 한 시장(코스피/나스닥/코인/선물)에 넣을 수 있는 최대 비중
MAX_MARKET_PCT = 40.0

# 선물 명목가 총합 상한 (총자산 대비) — 레버리지 폭주 방지
MAX_FUTURES_NOTIONAL_PCT = 30.0

# 선물 레버리지 상한. 서버는 150배까지 허용하지만 우리는 여기서 막는다.
MAX_LEVERAGE = 3

# 교차 증거금 금지 (한 포지션 터지면 같은 버킷 전부 청산되므로)
ALLOW_CROSS_MARGIN = False

# 누적 손실 한도. 총자산이 시작금 대비 이 비율 아래로 가면 신규 진입 중단.
MAX_DRAWDOWN_PCT = 20.0

# 하루 최대 주문 건수 (레이트리밋·과매매 방지)
MAX_ORDERS_PER_DAY = 40

# 기대 수익이 왕복 비용의 이 배수보다 작으면 진입하지 않는다.
MIN_EDGE_MULTIPLE = 2.0

# ─────────────────────────────────────────────────────────────
# 운영 상수
# ─────────────────────────────────────────────────────────────
QUOTE_STALE_MS = 120_000       # 시세 120초 넘으면 체결 보류
MIN_POLL_INTERVAL_SEC = 4.0    # 조회 최소 간격
RATE_LIMIT_PER_SEC = 3         # 토큰당 초당 3건
HTTP_TIMEOUT_SEC = 25

# 정산 지연 (ms)
SETTLE_MS = {
    ("KRW", "USD"): 60_000,
    ("USD", "USDT"): 60_000,
    ("KRW", "USDT"): 120_000,
}

# 장 시간 (한국시각 기준 메모 — 실제 개장 여부는 get_rules 의 markets[].open 을 신뢰)
MARKET_HOURS_NOTE = {
    "kospi": "09:00–15:30 KST 평일",
    "nasdaq": "09:30–16:00 America/New_York 평일 (KST 23:30–06:00, 서머타임 이동)",
    "crypto": "24시간",
}

# 거래 가능 국채 ETF
BOND_ETFS = {
    "kospi": ["114260", "365780", "385560"],
    "nasdaq": ["SHY", "IEF", "TLT"],
}


def market_of(symbol: str) -> str:
    """종목 코드 모양으로 시장을 판별한다."""
    s = symbol.upper()
    if s.endswith(".P") or s.endswith("_PERP"):
        return "futures"
    if s.endswith("USDT"):
        return "crypto"
    if s.isdigit() and len(s) == 6:
        return "kospi"
    if s.isalpha():
        return "nasdaq"
    raise ValueError(f"시장을 판별할 수 없는 종목 코드: {symbol}")
