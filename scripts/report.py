#!/usr/bin/env python
"""데일리·위클리 리포트 생성 → 텔레그램 발송용 텍스트 + 차트 PNG.

    .venv/bin/python scripts/report.py            # 데일리
    .venv/bin/python scripts/report.py --weekly   # 위클리
    .venv/bin/python scripts/report.py --snapshot-only   # 기록만 (발송 안 함)

출력:
    reports/daily/YYYY-MM-DD.md   + nav.png, leaderboard.png ...
    reports/weekly/YYYY-WNN.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import charts, config as C, journal, snapshot
from bot.client import SSUClient

KST = timezone(timedelta(hours=9))
MY_ID = "s09"
REPO = Path(__file__).resolve().parent.parent


def won(x: float) -> str:
    return f"{x:,.0f}원"


def pct(x: float, nd: int = 4) -> str:
    return f"{x:+.{nd}f}%"


def build_daily(cli: SSUClient, nav_hist, rank_hist, pf, lb) -> tuple[str, list[Path]]:
    today = datetime.now(KST)
    d = today.date().isoformat()
    cur = nav_hist[-1]
    prev = snapshot.previous(nav_hist, d)

    lb_sorted = sorted(lb, key=lambda r: -float(r.get("equityKrw") or 0))
    my_rank = next((i for i, r in enumerate(lb_sorted, 1)
                    if r.get("id") == MY_ID), None)
    prev_rank = None
    if len(rank_hist) >= 2:
        prev_rank = rank_hist[-2].get("my_rank")

    L = []
    A = L.append
    A(f"## 📊 데일리 리포트 — {d} ({'월화수목금토일'[today.weekday()]})")
    A("")

    # 1. 계좌 요약
    eq = cur["equityKrw"]
    chg = eq - prev["equityKrw"] if prev else 0.0
    chg_pct = (eq / prev["equityKrw"] - 1) * 100 if prev else 0.0
    arrow = "▲" if chg > 0 else ("▼" if chg < 0 else "—")
    A("**1. 계좌**")
    A(f"총자산 **{won(eq)}**")
    if prev:
        A(f"전일 대비 {arrow} {won(abs(chg))} ({pct(chg_pct)})")
    else:
        A("전일 대비 — (첫 기록)")
    A(f"누적 수익률 **{pct(cur['returnPct'])}** · MDD {cur['maxDrawdownPct']:.4f}%")
    rank_txt = f"{my_rank}/{len(lb_sorted)}위"
    if prev_rank and my_rank:
        diff = prev_rank - my_rank
        rank_txt += f" ({'▲'+str(diff) if diff>0 else ('▼'+str(-diff) if diff<0 else '—')})"
    A(f"순위 **{rank_txt}**")
    A("")

    # 2. 어제 한 일
    A("**2. 거래**")
    fills = pf.get("fills") or []
    rejects = pf.get("rejects") or []
    convs = pf.get("conversions") or []
    new_trades = cur["trades"] - (prev["trades"] if prev else 0)
    if fills:
        A(f"체결 {len(fills)}건 (누적) / 신규 {new_trades}건")
        for f in fills[:6]:
            slip = (f["price"] / f["ref_price"] - 1) * 10000 if f.get("ref_price") else 0
            A(f"· `{f['symbol']}` {f['side']} {f['qty']:g} @ {f['price']:.6g} "
              f"(기준 {f.get('ref_price')}, 슬립 {slip:+.1f}bps, "
              f"수수료 {f.get('fee', 0):.6g})")
    else:
        A("체결 없음")
    if rejects:
        A("")
        A(f"⚠️ 거부 {len(rejects)}건 — 원문:")
        for r in rejects[:5]:
            A(f"· `{str(r)[:160]}`")
    else:
        A("거부 0건")
    if convs:
        A("")
        A(f"환전 {len(convs)}건")
        for c in convs[-3:]:
            if c.get("settled"):
                settled = "도착"
            else:
                eta = (c.get("settles_at_kst") or c.get("settlesAtKst")
                       or c.get("settlesAt"))
                if isinstance(eta, (int, float)):
                    eta = datetime.fromtimestamp(eta / 1000, KST).strftime("%m/%d %H:%M")
                settled = f"운송중 (도착 {eta})" if eta else "운송중"
            A(f"· {c.get('from_ccy') or c.get('from')}→{c.get('to_ccy') or c.get('to')} "
              f"{float(c.get('from_amt') or c.get('amount') or 0):,.0f} → "
              f"{float(c.get('to_amt') or c.get('received') or 0):,.4f} "
              f"· 비용 {won(float(c.get('cost_krw') or c.get('costKrw') or 0))} · {settled}")
    A("")

    # 3. 비용
    A("**3. 비용** (채점 항목)")
    fee, fx = cur["tradeFeeKrw"], cur["fxCostKrw"]
    dfee = fee - (prev["tradeFeeKrw"] if prev else 0)
    dfx = fx - (prev["fxCostKrw"] if prev else 0)
    A(f"어제 매매 {won(dfee)} + 환전 {won(dfx)} = **{won(dfee+dfx)}**")
    A(f"누적 매매 {won(fee)} + 환전 {won(fx)} = **{won(fee+fx)}** "
      f"(NAV의 {(fee+fx)/eq*100:.4f}%)")
    A(f"→ 본전까지 필요 수익률 **{(fee+fx)/eq*100:.4f}%**")
    A("")

    # 4. 포지션
    A("**4. 포지션**")
    positions = pf.get("positions") or []
    if positions:
        for p in positions:
            A(f"· `{p['symbol']}` {p['qty']:g} @ 평단 {p.get('avg_price', 0):.6g} "
              f"(실현 {p.get('realized', 0):+.6g})")
    else:
        A("보유 없음")
    w = cur["wallets"]
    A(f"지갑 KRW {w.get('KRW', 0):,.0f} / USD {w.get('USD', 0):,.2f} / "
      f"USDT {w.get('USDT', 0):,.4f}")
    if cur["transitKrw"] > 0:
        A(f"⏳ 운송중 {won(cur['transitKrw'])} — 쓸 수 없지만 평가에는 포함")
    if cur.get("byMarket"):
        parts = [f"{k} {v.get('valueKrw', 0):,.0f}원({v.get('positions', 0)}종목)"
                 for k, v in cur["byMarket"].items()]
        A("시장별: " + " / ".join(parts))
    A("")

    # 5. 리스크
    A("**5. 리스크 한도**")
    pos_pct = max((abs(float(p.get("qty", 0))) for p in positions), default=0)
    mkt_max = max((v.get("valueKrw", 0) / eq * 100
                   for v in (cur.get("byMarket") or {}).values()), default=0.0)
    fut_pct = cur["futuresEquityKrw"] / eq * 100 if eq else 0
    A(f"· 시장 최대 {mkt_max:.1f}% / {C.MAX_MARKET_PCT}%")
    A(f"· 선물 {fut_pct:.1f}% / {C.MAX_FUTURES_NOTIONAL_PCT}% · "
      f"레버리지 상한 {C.MAX_LEVERAGE}배 · 교차 금지")
    A(f"· 손실 {cur['returnPct']:+.4f}% / -{C.MAX_DRAWDOWN_PCT}%")
    A(f"· 오늘 주문 {journal.count_today('order')} / {C.MAX_ORDERS_PER_DAY}건")
    A("")

    # 6. 다음
    A("**6. 다음**")
    rules = cli.get_rules()
    opens = [i["label"] for i in rules["markets"].values() if i.get("open")]
    A(f"· 열린 장: {', '.join(opens) if opens else '없음'}")
    A(f"· 매매 마감까지 **{C.days_left():.1f}일** (11/30 자정)")
    fx_now = rules["fxNow"]
    A(f"· USD/KRW {fx_now['usdkrw']['rate']:,.2f} · "
      f"김프 {fx_now['upbit']['premiumPct']:+.2f}%")

    # ── 차트 ──
    outdir = REPO / "reports" / "daily"
    outdir.mkdir(parents=True, exist_ok=True)
    imgs = []
    p = charts.nav_chart(nav_hist, outdir / f"{d}_nav.png",
                         f"총자산 추이 ({d} 기준)")
    if p: imgs.append(p)
    imgs.append(charts.leaderboard_chart(
        rank_hist[-1]["teams"], outdir / f"{d}_leaderboard.png", MY_ID,
        f"순위표 {len(lb_sorted)}팀 ({d})"))
    p = charts.rank_trend_chart(rank_hist, outdir / f"{d}_rank.png")
    if p: imgs.append(p)
    return "\n".join(L), imgs


def build_weekly(cli: SSUClient, nav_hist, rank_hist, pf, lb) -> tuple[str, list[Path]]:
    today = datetime.now(KST)
    wk = today.strftime("%Y-W%V")
    week = [r for r in nav_hist
            if (today.date() - datetime.fromisoformat(r["date"]).date()).days <= 7]
    if not week:
        week = nav_hist[-7:]

    lb_sorted = sorted(lb, key=lambda r: -float(r.get("equityKrw") or 0))
    my_rank = next((i for i, r in enumerate(lb_sorted, 1)
                    if r.get("id") == MY_ID), None)
    cur = nav_hist[-1]

    L = []
    A = L.append
    A(f"## 📈 위클리 리포트 — {wk} ({today.date()})")
    A("")
    A("**주간 성과**")
    if len(week) >= 2:
        r0, r1 = week[0], week[-1]
        wret = (r1["equityKrw"] / r0["equityKrw"] - 1) * 100
        A(f"주간 수익률 **{pct(wret)}** ({r0['date']} → {r1['date']})")
        rets = [(week[i]["equityKrw"] / week[i-1]["equityKrw"] - 1)
                for i in range(1, len(week))]
        if len(rets) >= 2:
            import statistics as st
            vol = st.stdev(rets) * (252 ** 0.5) * 100
            mean = st.mean(rets) * 252 * 100
            A(f"연율 변동성 {vol:.2f}% · 샤프 {mean/vol:.2f}" if vol > 0
              else f"연율 변동성 0% (움직임 없음)")
        dd = snapshot.drawdown_series(week)
        A(f"주간 최대 낙폭 {min(v for _, v in dd):.4f}%")
    else:
        A(f"기록 {len(week)}일 — 주간 비교는 데이터가 더 쌓여야 합니다")
    A(f"누적 **{pct(cur['returnPct'])}** · MDD {cur['maxDrawdownPct']:.4f}%")
    A("")

    A("**순위**")
    A(f"현재 **{my_rank}/{len(lb_sorted)}위**")
    top5 = lb_sorted[:5]
    A("")
    A("상위 5팀 포지션 (서버가 공개하는 범위 — 시장 단위까지):")
    for i, t in enumerate(top5, 1):
        mk = t.get("byMarket") or {}
        detail = " / ".join(
            f"{k} {v.get('valueKrw',0)/1e4:,.0f}만({v.get('positions',0)})"
            for k, v in mk.items()) or "현금 100%"
        fut = f" · 선물 {t.get('futuresPositions',0)}포지션" if t.get("futuresPositions") else ""
        A(f"{i}. **{t['nickname']}** {pct(float(t['returnPct']))} · "
          f"거래 {t.get('trades',0)} · {detail}{fut}")
    A("")
    mine = next((r for r in lb_sorted if r["id"] == MY_ID), None)
    if mine:
        gap = lb_sorted[0]["equityKrw"] - mine["equityKrw"]
        A(f"1위와 격차 {won(gap)}")
    A("")

    A("**전략 점검**")
    A(f"· 누적 거래 {cur['trades']}건 · 환전 {cur['conversions']}건")
    A(f"· 누적 비용 {won(cur['tradeFeeKrw'] + cur['fxCostKrw'])} "
      f"(매매 {won(cur['tradeFeeKrw'])} / 환전 {won(cur['fxCostKrw'])})")
    A(f"· 백테스트 가정 비용: 코스피 왕복 {C.roundtrip_bps('kospi')/100:.2f}% · "
      f"나스닥 {C.roundtrip_bps('nasdaq')/100:.2f}% · 코인 {C.roundtrip_bps('crypto')/100:.2f}%")
    traded = [t for t in lb_sorted if t.get("trades", 0) > 0]
    if traded:
        import statistics as st
        A(f"· 거래한 팀 {len(traded)}/{len(lb_sorted)}팀, "
          f"평균 수익률 {st.mean([float(t['returnPct']) for t in traded]):+.3f}% "
          f"vs 무거래 팀 "
          f"{st.mean([float(t['returnPct']) for t in lb_sorted if t.get('trades',0)==0]):+.3f}%")
    A("")
    A(f"**마감까지 {C.days_left():.1f}일** · 발표 {' / '.join(C.PRESENTATION_DATES)}")

    outdir = REPO / "reports" / "weekly"
    outdir.mkdir(parents=True, exist_ok=True)
    imgs = []
    p = charts.nav_chart(nav_hist, outdir / f"{wk}_nav.png", f"총자산 추이 — {wk}")
    if p: imgs.append(p)
    imgs.append(charts.leaderboard_chart(
        rank_hist[-1]["teams"], outdir / f"{wk}_leaderboard.png", MY_ID,
        f"순위표 {len(lb_sorted)}팀 — {wk}"))
    p = charts.rank_trend_chart(rank_hist, outdir / f"{wk}_rank.png")
    if p: imgs.append(p)
    p = charts.cost_chart(nav_hist, outdir / f"{wk}_cost.png")
    if p: imgs.append(p)
    return "\n".join(L), imgs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weekly", action="store_true")
    ap.add_argument("--snapshot-only", action="store_true")
    args = ap.parse_args()

    cli = SSUClient()
    pf = cli.get_portfolio()
    lb = cli.get_leaderboard()

    snapshot.record_nav(pf)
    snapshot.record_rank(lb, MY_ID)
    nav_hist = snapshot.nav_history()
    rank_hist = snapshot.rank_history()

    if args.snapshot_only:
        print(f"스냅샷 기록: NAV {len(nav_hist)}일 / 순위 {len(rank_hist)}일")
        return 0

    if args.weekly:
        text, imgs = build_weekly(cli, nav_hist, rank_hist, pf, lb)
        out = REPO / "reports" / "weekly" / f"{datetime.now(KST).strftime('%Y-W%V')}.md"
    else:
        text, imgs = build_daily(cli, nav_hist, rank_hist, pf, lb)
        out = REPO / "reports" / "daily" / f"{datetime.now(KST).date()}.md"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print("\n" + "=" * 50)
    print("저장:", out)
    for p in imgs:
        print("MEDIA:" + str(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
