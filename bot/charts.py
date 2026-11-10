"""리포트 차트 — NAV·MDD·순위 추이를 PNG 로 그린다.

기록이 1~2일뿐이면 꺾은선이 점으로만 보이므로, 데이터가 쌓일수록 의미가 생긴다.
한글 폰트는 macOS 기본 'AppleGothic' 을 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

from . import snapshot

plt.rcParams.update({
    "font.family": "Apple SD Gothic Neo",   # AppleGothic 은 bold weight 가 없어 경고가 난다
    "axes.unicode_minus": False,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#b5b4ad",
    "axes.labelcolor": "#52514e",
    "figure.facecolor": "white",
    "axes.titlesize": 12,
    "font.size": 9,
})

UP, DOWN, ACCENT = "#d14343", "#2f6fdb", "#7a6ff0"


def _outdir(kind: str) -> Path:
    d = Path(__file__).resolve().parent.parent / "reports" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def nav_chart(rows: list[dict], path: Path, title: str = "총자산 추이") -> Path | None:
    """NAV + 낙폭 2단 차트.

    기록이 1~2일이면 축이 의미 없이 퍼지므로(y축 눈금이 전부 같은 값,
    x축이 1년 범위) 축 범위를 명시적으로 좁힌다.
    """
    if len(rows) < 1:
        return None
    df = pd.DataFrame(rows)
    df["d"] = pd.to_datetime(df["date"])
    start = df["startKrw"].iloc[-1] or df["equityKrw"].iloc[0]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(9, 5.6), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 1], "hspace": 0.12})

    ax1.plot(df["d"], df["equityKrw"], color=ACCENT, lw=1.8,
             marker="o" if len(df) < 15 else None, ms=3.5)
    ax1.axhline(start, color="#999", ls="--", lw=1, label=f"시작금 {start:,.0f}")
    last = df["equityKrw"].iloc[-1]
    ax1.scatter([df["d"].iloc[-1]], [last], color=UP if last >= start else DOWN,
                zorder=5, s=36)
    ax1.annotate(f"{last:,.0f}원\n({df['returnPct'].iloc[-1]:+.3f}%)",
                 (df["d"].iloc[-1], last), textcoords="offset points",
                 xytext=(8, 0), va="center", fontsize=9,
                 color=UP if last >= start else DOWN)
    ax1.set_title(title, loc="left", pad=10)
    ax1.set_ylabel("총자산 (원)")
    ax1.legend(frameon=False, fontsize=8, loc="lower left")
    ax1.grid(axis="y", color="#eee", lw=0.8)

    # y축: 값 변화가 아주 작아도 눈금이 구분되도록 범위와 포맷을 맞춘다
    vals = list(df["equityKrw"]) + [start]
    lo, hi = min(vals), max(vals)
    span = max(hi - lo, start * 0.0002)        # 최소 0.02% 폭은 확보
    ax1.set_ylim(lo - span * 0.6, hi + span * 0.6)
    if span < start * 0.01:                     # 변화가 1% 미만이면 만원 단위
        ax1.yaxis.set_major_formatter(lambda x, p: f"{x:,.0f}")
    else:
        ax1.yaxis.set_major_formatter(lambda x, p: f"{x/1e8:.3f}억")

    dd = snapshot.drawdown_series(rows)
    dd_x = [pd.to_datetime(d) for d, _ in dd]
    dd_y = [v for _, v in dd]
    ax2.fill_between(dd_x, dd_y, 0, color=DOWN, alpha=0.22)
    ax2.plot(dd_x, dd_y, color=DOWN, lw=1.3,
             marker="o" if len(dd) < 15 else None, ms=3.5)
    ax2.set_ylabel("낙폭 (%)")
    ax2.grid(axis="y", color="#eee", lw=0.8)
    worst = min(dd_y, default=0.0)
    ax2.set_ylim(min(worst * 1.3, -0.01), 0.005)
    ax2.annotate(f"최대 {worst:.3f}%", (0.01, 0.08), xycoords="axes fraction",
                 fontsize=8, color=DOWN)

    # x축: 기록이 적으면 범위를 하루 단위로 좁히고 눈금을 날짜마다
    if len(df) == 1:
        d0 = df["d"].iloc[0]
        ax2.set_xlim(d0 - pd.Timedelta(hours=12), d0 + pd.Timedelta(hours=12))
        ax2.set_xticks([d0])
    elif len(df) <= 14:
        ax2.set_xticks(list(df["d"]))
    for ax in (ax1, ax2):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate(rotation=0, ha="center")

    if len(df) == 1:
        ax1.text(0.5, 0.5, "기록 1일 — 추이는 데이터가 쌓이면 보입니다",
                 transform=ax1.transAxes, ha="center", va="center",
                 fontsize=10, color="#aaa")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def leaderboard_chart(teams: list[dict], path: Path, my_id: str = "s09",
                      title: str = "순위표") -> Path:
    """전체 팀 수익률 가로 막대 — 우리 팀 강조."""
    df = pd.DataFrame(teams).sort_values("returnPct")
    colors = [ACCENT if r.id == my_id else (UP if r.returnPct >= 0 else "#c9ccd1")
              for r in df.itertuples()]
    h = max(3.2, 0.26 * len(df) + 1.2)
    fig, ax = plt.subplots(figsize=(8.4, h))
    bars = ax.barh(df["nickname"].str[:16], df["returnPct"], color=colors, height=0.68)
    ax.axvline(0, color="#777", lw=1)
    ax.set_xlabel("수익률 (%)")
    ax.set_title(title, loc="left", pad=10)
    ax.grid(axis="x", color="#eee", lw=0.8)

    for b, r in zip(bars, df.itertuples()):
        x = b.get_width()
        ax.text(x + (0.01 if x >= 0 else -0.01), b.get_y() + b.get_height()/2,
                f"{x:+.3f}%", va="center",
                ha="left" if x >= 0 else "right", fontsize=7.5,
                color="#333" if r.id != my_id else ACCENT,
                fontweight="bold" if r.id == my_id else "normal")
    for lbl, r in zip(ax.get_yticklabels(), df.itertuples()):
        if r.id == my_id:
            lbl.set_color(ACCENT)
            lbl.set_fontweight("bold")
    ax.margins(x=0.16)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def rank_trend_chart(hist: list[dict], path: Path) -> Path | None:
    """우리 순위 추이 (기록 2일 이상일 때만)."""
    pts = [(r["date"], r["my_rank"], r["n_teams"]) for r in hist if r.get("my_rank")]
    if len(pts) < 2:
        return None
    fig, ax = plt.subplots(figsize=(8.4, 3))
    d = [pd.to_datetime(x[0]) for x in pts]
    ax.plot(d, [x[1] for x in pts], color=ACCENT, lw=1.8, marker="o", ms=4)
    ax.invert_yaxis()
    ax.set_ylabel("순위")
    ax.set_title(f"순위 추이 (전체 {pts[-1][2]}팀)", loc="left", pad=10)
    ax.grid(axis="y", color="#eee", lw=0.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.annotate(f"{pts[-1][1]}위", (d[-1], pts[-1][1]), textcoords="offset points",
                xytext=(8, 0), va="center", color=ACCENT, fontweight="bold")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def cost_chart(rows: list[dict], path: Path) -> Path | None:
    """누적 비용 추이 (매매수수료 vs 환전비용)."""
    if len(rows) < 2:
        return None
    df = pd.DataFrame(rows)
    df["d"] = pd.to_datetime(df["date"])
    fig, ax = plt.subplots(figsize=(8.4, 3))
    ax.stackplot(df["d"], df["tradeFeeKrw"], df["fxCostKrw"],
                 labels=["매매수수료", "환전비용"],
                 colors=["#f0a35e", "#6fb3d9"], alpha=0.85)
    ax.set_ylabel("누적 비용 (원)")
    ax.set_title("누적 거래비용", loc="left", pad=10)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    ax.grid(axis="y", color="#eee", lw=0.8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path
