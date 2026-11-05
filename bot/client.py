"""SSU 모의투자 서버 클라이언트.

설계 원칙 (채점 기준에 맞춤):
- 주문 전 가능성 점검: 잔고 / 장 개장 / 시세 신선도. 거부를 맞고 알지 않는다.
- 리스크 한도는 config.py 에 박혀 있고 여기서 강제된다.
- 레이트리밋을 클라이언트에서 지킨다 (토큰당 초당 3건, 45명 공용 서버).
- 429는 Retry-After 만큼 쉬고 1회 재시도. 같은 거부를 반복하지 않는다.
- 502/타임아웃은 재주문하지 않고 포트폴리오로 체결 여부를 먼저 확인한다.
- 토큰은 절대 로그·예외 메시지에 넣지 않는다.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import requests

from . import config as C
from . import journal


class SSUError(RuntimeError):
    """서버가 거부했거나 통신이 실패했다. 원문 메시지를 담는다."""

    def __init__(self, message: str, status: int | None = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class RiskLimitError(RuntimeError):
    """config.py 의 리스크 한도에 막혔다. 서버에 가지도 않았다."""


class MarketClosedError(SSUError):
    pass


class StaleQuoteError(SSUError):
    pass


Side = Literal["buy", "sell"]


class SSUClient:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        self.base = (base_url or C.BASE_URL).rstrip("/")
        self._token = token or C.TOKEN
        if not self._token:
            raise SSUError(
                "토큰이 없습니다. .env 또는 "
                "~/.hermes/profiles/team/secrets/ssu-paper.env 에 lazzy_token 을 넣으세요."
            )
        self._sess = requests.Session()
        self._sess.headers.update({
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": "ssu-paper-bot/1.0",
        })
        self._call_times: list[float] = []
        self._rules_cache: dict | None = None
        self._rules_at: float = 0.0

    # ── 레이트리밋 ────────────────────────────────────────────
    def _throttle(self) -> None:
        now = time.monotonic()
        self._call_times = [t for t in self._call_times if now - t < 1.0]
        if len(self._call_times) >= C.RATE_LIMIT_PER_SEC:
            time.sleep(1.0 - (now - self._call_times[0]) + 0.05)
            now = time.monotonic()
            self._call_times = [t for t in self._call_times if now - t < 1.0]
        self._call_times.append(now)

    # ── HTTP ──────────────────────────────────────────────────
    def _request(self, method: str, path: str, payload: dict | None = None,
                 _retry: bool = True) -> Any:
        self._throttle()
        url = f"{self.base}{path}"
        try:
            resp = self._sess.request(method, url, json=payload,
                                      timeout=C.HTTP_TIMEOUT_SEC)
        except requests.RequestException as exc:
            journal.write("error", endpoint=path, error=type(exc).__name__)
            raise SSUError(f"{path} 통신 실패: {type(exc).__name__}") from exc

        if resp.status_code == 429 and _retry:
            wait = float(resp.headers.get("Retry-After", 1)) + 0.2
            time.sleep(min(wait, 30))
            return self._request(method, path, payload, _retry=False)

        if resp.status_code >= 500:
            journal.write("error", endpoint=path, status=resp.status_code,
                          body=resp.text[:500])
            raise SSUError(
                f"{path} 서버 오류 {resp.status_code}. 재주문하지 말고 "
                f"get_portfolio()로 체결 여부를 먼저 확인하세요.",
                resp.status_code, resp.text[:500])

        try:
            body = resp.json()
        except ValueError:
            body = resp.text

        if resp.status_code >= 400:
            journal.reject(path, payload or {}, resp.status_code, body)
            msg = body.get("error") or body.get("message") if isinstance(body, dict) else str(body)
            raise SSUError(str(msg), resp.status_code, body)

        return body

    # ── 조회 ──────────────────────────────────────────────────
    def get_rules(self, max_age_sec: float = 300) -> dict:
        if self._rules_cache and time.monotonic() - self._rules_at < max_age_sec:
            return self._rules_cache
        rules = self._request("GET", "/api/rules")
        self._rules_cache = rules
        self._rules_at = time.monotonic()
        return rules

    def get_portfolio(self) -> dict:
        return self._request("GET", "/api/me")

    def get_quotes(self) -> Any:
        return self._request("GET", "/api/quotes")

    def get_fx(self) -> dict:
        return self._request("GET", "/api/fx")

    def get_futures(self) -> dict:
        return self._request("GET", "/api/futures")

    def get_leaderboard(self) -> Any:
        return self._request("GET", "/api/leaderboard")

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    # ── 점검 (주문 전) ────────────────────────────────────────
    def quote_for(self, symbol: str) -> dict:
        """종목 시세 1건. 없으면 SSUError."""
        quotes = self.get_quotes()
        rows = quotes.get("quotes", quotes) if isinstance(quotes, dict) else quotes
        if isinstance(rows, dict):
            row = rows.get(symbol.upper())
            if row:
                return row if isinstance(row, dict) else {"price": row}
        elif isinstance(rows, list):
            for r in rows:
                if str(r.get("symbol", "")).upper() == symbol.upper():
                    return r
        raise SSUError(f"{symbol} 시세를 찾을 수 없습니다.")

    def assert_market_open(self, symbol: str) -> None:
        market = C.market_of(symbol)
        if market in ("crypto", "futures"):
            return
        rules = self.get_rules()
        info = rules.get("markets", {}).get(market, {})
        if not info.get("open", False):
            raise MarketClosedError(
                f"{market} 장이 닫혀 있습니다 ({info.get('hours', '?')})")

    def assert_quote_fresh(self, symbol: str) -> dict:
        q = self.quote_for(symbol)
        age_ms = q.get("ageMs")
        if age_ms is None:
            age_sec = q.get("ageSec") or q.get("age")
            age_ms = float(age_sec) * 1000 if age_sec is not None else None
        if age_ms is None and q.get("ts"):
            age_ms = time.time() * 1000 - float(q["ts"])
        if age_ms is not None and age_ms > C.QUOTE_STALE_MS:
            raise StaleQuoteError(
                f"{symbol} 시세가 {age_ms/1000:.0f}초 지났습니다 (한도 "
                f"{C.QUOTE_STALE_MS/1000:.0f}초). 잠시 뒤 다시.")
        return q

    # ── 리스크 한도 강제 ──────────────────────────────────────
    def check_risk(self, symbol: str, notional_krw: float,
                   leverage: int = 1, margin_mode: str = "isolated") -> None:
        """config.py 한도를 넘으면 서버에 보내지 않고 막는다."""
        pf = self.get_portfolio()
        equity = float(pf.get("equityKrw") or 0) or C.START_KRW

        if float(pf.get("returnPct") or 0) <= -C.MAX_DRAWDOWN_PCT:
            raise RiskLimitError(
                f"누적 손실 한도 도달 ({pf.get('returnPct')}% ≤ "
                f"-{C.MAX_DRAWDOWN_PCT}%). 신규 진입 중단.")

        pct = notional_krw / equity * 100
        if pct > C.MAX_POSITION_PCT:
            raise RiskLimitError(
                f"{symbol} 비중 {pct:.1f}% > 한도 {C.MAX_POSITION_PCT}% "
                f"(총자산 {equity:,.0f}원)")

        if journal.count_today("order") >= C.MAX_ORDERS_PER_DAY:
            raise RiskLimitError(f"오늘 주문 한도 {C.MAX_ORDERS_PER_DAY}건 도달.")

        if C.market_of(symbol) == "futures":
            if leverage > C.MAX_LEVERAGE:
                raise RiskLimitError(
                    f"레버리지 {leverage}배 > 한도 {C.MAX_LEVERAGE}배")
            if margin_mode == "cross" and not C.ALLOW_CROSS_MARGIN:
                raise RiskLimitError(
                    "교차 증거금 금지 (같은 버킷 포지션이 한꺼번에 청산됨). "
                    "isolated 를 쓰세요.")
            fut_pct = notional_krw / equity * 100
            if fut_pct > C.MAX_FUTURES_NOTIONAL_PCT:
                raise RiskLimitError(
                    f"선물 명목가 {fut_pct:.1f}% > 한도 "
                    f"{C.MAX_FUTURES_NOTIONAL_PCT}%")

    def breakeven_bps(self, symbol: str) -> float:
        """이 종목에서 왕복으로 얼마나 올라야 본전인가 (bps)."""
        market = C.market_of(symbol)
        if market == "futures":
            return C.futures_roundtrip_bps()
        return C.roundtrip_bps(market)

    # ── 환전 ──────────────────────────────────────────────────
    def convert_quote(self, from_cur: str, to_cur: str, amount: float,
                      note: str = "") -> dict:
        """견적만. 돈이 움직이지 않는다."""
        payload = {"from": from_cur, "to": to_cur, "amount": amount,
                   "dryRun": True}
        if note:
            payload["note"] = note
        return self._request("POST", "/api/convert", payload)

    def convert(self, from_cur: str, to_cur: str, amount: float,
                note: str = "", confirmed: bool = False) -> dict:
        """실제 환전. 즉시 실행되고 되돌릴 수 없다.

        confirmed=True 를 명시해야 실행된다 — 실수 방지.
        """
        if not confirmed:
            raise RiskLimitError(
                "환전은 confirmed=True 없이 실행하지 않습니다. "
                "먼저 convert_quote() 로 견적을 보고 사용자 승인을 받으세요.")
        payload = {"from": from_cur, "to": to_cur, "amount": amount}
        if note:
            payload["note"] = note
        res = self._request("POST", "/api/convert", payload)
        journal.write("convert", **{"from": from_cur, "to": to_cur,
                                    "amount": amount, "response": res})
        return res

    # ── 주문 ──────────────────────────────────────────────────
    def place_order(self, symbol: str, side: Side, qty: float,
                    note: str = "", reason: str = "",
                    confirmed: bool = False) -> dict:
        """현물 주문. 사전 점검 → 리스크 체크 → 전송 → 기록."""
        if not confirmed:
            raise RiskLimitError(
                "주문은 confirmed=True 없이 실행하지 않습니다.")

        self.assert_market_open(symbol)
        q = self.assert_quote_fresh(symbol)

        price = float(q.get("price") or q.get("last") or 0)
        notional = price * qty
        market = C.market_of(symbol)
        if market == "nasdaq":
            notional *= float(self.get_portfolio()["rate"]["USD"])
        elif market == "crypto":
            notional *= float(self.get_portfolio()["rate"]["USDT"])

        self.check_risk(symbol, notional)

        be = self.breakeven_bps(symbol)
        journal.decision(symbol, side, qty,
                         reason or "(근거 미기재)", be,
                         price=price, notional_krw=round(notional))

        payload = {"symbol": symbol, "side": side, "qty": qty}
        if note:
            payload["note"] = note
        res = self._request("POST", "/api/signal", payload)
        journal.order(symbol, side, qty, res, notional_krw=round(notional))
        return res

    def futures_order(self, symbol: str, side: Side, qty: float,
                      leverage: int = 1, margin_mode: str = "isolated",
                      note: str = "", reason: str = "",
                      confirmed: bool = False) -> dict:
        if not confirmed:
            raise RiskLimitError("선물 주문은 confirmed=True 없이 실행하지 않습니다.")

        marks = {m["symbol"]: m for m in self.get_rules()["futures"]["marks"]}
        mark = marks.get(symbol.upper())
        if not mark:
            raise SSUError(f"{symbol} 마크가를 찾을 수 없습니다.")
        usdt_krw = float(self.get_portfolio()["rate"]["USDT"])
        notional = float(mark["mark"]) * qty * usdt_krw

        self.check_risk(symbol, notional, leverage, margin_mode)

        journal.decision(symbol, side, qty,
                         reason or "(근거 미기재)",
                         C.futures_roundtrip_bps(),
                         mark=mark["mark"], leverage=leverage,
                         marginMode=margin_mode,
                         funding_rate=mark.get("funding_rate"),
                         notional_krw=round(notional))

        payload = {"symbol": symbol, "side": side, "qty": qty,
                   "marginMode": margin_mode, "leverage": leverage}
        if note:
            payload["note"] = note
        res = self._request("POST", "/api/futures/order", payload)
        journal.order(symbol, side, qty, res, leverage=leverage,
                      marginMode=margin_mode, notional_krw=round(notional))
        return res

    def futures_transfer(self, asset: str, direction: str, amount: float,
                         note: str = "", confirmed: bool = False) -> dict:
        if not confirmed:
            raise RiskLimitError("증거금 이체는 confirmed=True 없이 실행하지 않습니다.")
        payload = {"asset": asset, "direction": direction, "amount": amount}
        if note:
            payload["note"] = note
        res = self._request("POST", "/api/futures/transfer", payload)
        journal.write("transfer", asset=asset, direction=direction,
                      amount=amount, response=res)
        return res
