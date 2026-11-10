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
# 시작 자본. **서버 값이 정답이다** — 하드코딩에 의존하지 말고
# get_rules()["startKrw"] / get_portfolio()["equityKrw"] 를 우선 쓴다.
# 이 값은 서버를 못 읽을 때의 폴백이고, 환경변수 SSU_START_KRW 로 덮을 수 있다.
#
# 2026-11-10 조교 공지: 투자금 1억 → **100억**, 매매 마감 11/30 자정, 발표 12/1·12/8.
# 같은 날 서버 확인값은 아직 1억이었다(startKrw=100,000,000). 서버가 바뀌면
# 코드 수정 없이 자동 반영된다 — 아래 폴백은 공지값(100억)으로 맞춰 둔다.
START_KRW = int(os.getenv("SSU_START_KRW", 10_000_000_000))

# 최소 주문 금액은 자본 규모에 비례해야 한다. 자본이 100배가 되면
# 50만원 주문은 비중 0.005% 로 무의미해지고 주문 수만 폭증한다.
MIN_ORDER_BPS = 5          # 총자산의 0.05%
MIN_ORDER_FLOOR_KRW = 500_000


def min_order_krw(nav_krw: float | None = None) -> float:
    """총자산에 비례한 최소 주문 금액 (바닥값 50만원)."""
    nav = float(nav_krw) if nav_krw else float(START_KRW)
    return max(MIN_ORDER_FLOOR_KRW, nav * MIN_ORDER_BPS / 10_000)

# 한 종목에 넣을 수 있는 최대 비중 (총자산 대비)
#
# 2026-11-11 상한 스윕(scripts/sweep_caps.py, 6년 워크포워드, 비용 반영)
# 근거로 15% → 20% 로 완화. 35개 조합 중:
#   상한 없음  : CAGR +1.29%  샤프 0.51  — 한 종목에 82.7% 쏠려 사실상 채권 단일 보유
#   종목 15%   : 샤프 1.62 (시장상한 없을 때 최고)
#   종목 20%   : 샤프 1.52  칼마 1.60  MDD -8.71%  ← 종합 1위
#   종목 30%   : 칼마 1.74 이지만 CAGR +12.35% 로 낮고 쏠림이 다시 커짐
# 20% 는 샤프·칼마·MDD 가 모두 상위이면서 쏠림을 실질적으로 막는 구간이다.
MAX_POSITION_PCT = 20.0

# 한 시장(코스피/나스닥/코인/선물)에 넣을 수 있는 최대 비중
#
# 같은 스윕에서 시장 상한은 조일수록 성과가 나빠졌다 — 코인 편입을 강제하기
# 때문이다 (40% 를 걸면 코인 비중이 0.7% → 22.1% 로 올라가며 샤프 1.52 → 1.30).
# 다만 상한을 완전히 없애면 코스피가 60% 를 넘어 단일 시장·단일 통화 위험이
# 커지므로, 성과와 분산의 절충으로 60% 를 쓴다 (샤프 1.40, 코인 4.2%, 종합 10위).
MAX_MARKET_PCT = 60.0

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
# 일정 (2026-11-10 조교 공지)
# ─────────────────────────────────────────────────────────────
# 매매 마감 11/30 자정 = 12/01 00:00 KST. 그 이후 신규 진입을 하지 않는다.
TRADING_DEADLINE_KST = "2026-12-01T00:00:00+09:00"
PRESENTATION_DATES = ("2026-12-01", "2026-12-08")


def deadline_dt():
    """매매 마감 시각 (datetime, KST)."""
    from datetime import datetime
    return datetime.fromisoformat(TRADING_DEADLINE_KST)


def days_left() -> float:
    """매매 마감까지 남은 일수 (음수면 마감 지남)."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=9)))
    return (deadline_dt() - now).total_seconds() / 86400


def past_deadline() -> bool:
    return days_left() <= 0

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
