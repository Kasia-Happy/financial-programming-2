#!/usr/bin/env python
"""비중 상한 스윕 결과 시각화 — sweep_caps.py 가 만든 CSV 를 차트로.

    .venv/bin/python scripts/plot_sweep.py
    .venv/bin/python scripts/plot_sweep.py --csv reports/sweep_caps_w63_g0.09.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

plt.rcParams.update({
    "font.family": "Apple SD Gothic Neo",
    "axes.unicode_minus": False,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": "#b5b4ad",
    "axes.labelcolor": "#3a3a38",
    "figure.facecolor": "white",
    "font.size": 9,
    "axes.titlesize": 11,
})

ACCENT, CUR, BAD = "#7a6ff0", "#d14343", "#9aa0a6"
CAP_ORDER = ["없음", "30%", "20%", "15%", "10%", "8%", "5%"]
MC_ORDER = ["없음", "60%", "50%", "40%", "34%"]


def _cap_num(s: str) -> float:
    return 1.0 if s == "없음" else float(s.rstrip("%")) / 100


def heatmaps(df: pd.DataFrame, out: Path) -> Path:
    """종목상한 × 시장상한 격자에 샤프·CAGR·MDD·칼마를 히트맵으로."""
    metrics = [("샤프", "%.2f", "RdYlGn"), ("CAGR", "%.1f%%", "RdYlGn"),
               ("MDD", "%.1f%%", "RdYlGn"), ("칼마", "%.2f", "RdYlGn")]
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.4))
    for ax, (m, fmt, cmap) in zip(axes.ravel(), metrics):
        piv = df.pivot_table(index="종목상한", columns="시장상한", values=m)
        piv = piv.reindex(index=[c for c in CAP_ORDER if c in piv.index],
                          columns=[c for c in MC_ORDER if c in piv.columns])
        vals = piv.to_numpy(dtype=float)
        disp = vals * 100 if m in ("CAGR", "MDD") else vals
        im = ax.imshow(disp, cmap=cmap, aspect="auto")
        ax.set_xticks(range(len(piv.columns)), piv.columns)
        ax.set_yticks(range(len(piv.index)), piv.index)
        ax.set_xlabel("시장 비중 상한")
        ax.set_ylabel("종목 비중 상한")
        ax.set_title(f"{m}  (초록=좋음)", loc="left")
        for i in range(vals.shape[0]):
            for j in range(vals.shape[1]):
                if np.isnan(vals[i, j]):
                    continue
                txt = fmt % (disp[i, j])
                is_cur = piv.index[i] == "15%" and piv.columns[j] == "40%"
                ax.text(j, i, txt, ha="center", va="center", fontsize=8.5,
                        color="black",
                        fontweight="bold" if is_cur else "normal")
                if is_cur:
                    ax.add_patch(plt.Rectangle((j - .5, i - .5), 1, 1, fill=False,
                                               edgecolor=CUR, lw=2.4))
        fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    fig.suptitle("비중 상한 스윕 — 빨간 테두리 = 현재 설정 (종목 15% / 시장 40%)",
                 x=0.02, ha="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


def risk_return(df: pd.DataFrame, out: Path) -> Path:
    """위험-수익 산점도 + 종목상한별 샤프 곡선."""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 5.2))

    nomc = df[df.시장상한 == "없음"]
    withmc = df[df.시장상한 != "없음"]
    ax1.scatter(withmc.변동성 * 100, withmc.CAGR * 100, s=52,
                c="#bcc3cc", edgecolors="white", label="시장상한 적용", zorder=3)
    ax1.scatter(nomc.변동성 * 100, nomc.CAGR * 100, s=76, c=ACCENT,
                edgecolors="white", label="시장상한 없음", zorder=4)
    cur = df[(df.종목상한 == "15%") & (df.시장상한 == "40%")]
    if not cur.empty:
        ax1.scatter(cur.변동성 * 100, cur.CAGR * 100, s=190, marker="*",
                    c=CUR, edgecolors="white", zorder=6, label="현재 (15%/40%)")
    raw = df[(df.종목상한 == "없음") & (df.시장상한 == "없음")]
    if not raw.empty:
        ax1.scatter(raw.변동성 * 100, raw.CAGR * 100, s=120, marker="X",
                    c="#444", zorder=6, label="상한 없음 (원본 GMV)")
        ax1.annotate("원본 GMV\n한 종목 82.7%",
                     (float(raw.변동성.iloc[0]) * 100, float(raw.CAGR.iloc[0]) * 100),
                     textcoords="offset points", xytext=(14, 2), fontsize=8.5,
                     color="#444")
    for s in np.arange(0.5, 2.1, 0.5):
        x = np.linspace(1, 16, 20)
        ax1.plot(x, s * x, ls=":", lw=0.9, color="#ddd", zorder=1)
        ax1.annotate(f"샤프 {s:.1f}", (x[-1], s * x[-1]), fontsize=7.5,
                     color="#aaa", va="bottom", ha="right")
    ax1.set_xlabel("변동성 (%/년)")
    ax1.set_ylabel("CAGR (%/년)")
    ax1.set_title("위험 대비 수익 — 35개 조합", loc="left", pad=10)
    ax1.grid(color="#f0f0f0", lw=0.8)
    ax1.legend(frameon=False, fontsize=8.5, loc="lower right")
    ax1.set_ylim(0, None)

    for mc, g in df.groupby("시장상한"):
        g = g.copy()
        g["x"] = g.종목상한.map(_cap_num)
        g = g.sort_values("x")
        hi = mc == "없음"
        ax2.plot(g.x * 100, g.샤프, marker="o", ms=4.5,
                 lw=2.2 if hi else 1.2, color=ACCENT if hi else None,
                 alpha=1.0 if hi else 0.55, label=f"시장 {mc}", zorder=4 if hi else 2)
    if not cur.empty:
        ax2.scatter([15], cur.샤프, s=190, marker="*", c=CUR,
                    edgecolors="white", zorder=6)
        ax2.annotate("현재", (15, float(cur.샤프.iloc[0])),
                     textcoords="offset points", xytext=(6, -14),
                     color=CUR, fontsize=9, fontweight="bold")
    ax2.set_xlabel("종목 비중 상한 (%)  ·  100 = 상한 없음")
    ax2.set_ylabel("샤프")
    ax2.set_title("종목 상한에 따른 샤프 — 시장상한별", loc="left", pad=10)
    ax2.grid(color="#f0f0f0", lw=0.8)
    ax2.legend(frameon=False, fontsize=8.5, ncol=2)
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


def allocation(df: pd.DataFrame, out: Path) -> Path:
    """상위 조합의 시장 배분 + 비용 구조."""
    d = df.sort_values("평균순위").head(10).iloc[::-1]
    labels = [f"{r.종목상한}/{r.시장상한}" for r in d.itertuples()]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.5, 4.8))

    left = np.zeros(len(d))
    for col, name, color in [("ko", "코스피", "#5b8ff9"),
                             ("na", "나스닥", "#5ad8a6"),
                             ("cr", "코인", "#f6bd16")]:
        v = d[col].to_numpy() * 100
        ax1.barh(labels, v, left=left, color=color, label=name, height=0.68)
        for i, (val, l) in enumerate(zip(v, left)):
            if val > 6:
                ax1.text(l + val / 2, i, f"{val:.0f}", ha="center", va="center",
                         fontsize=8, color="white", fontweight="bold")
        left += v
    ax1.set_xlabel("평균 시장 배분 (%)")
    ax1.set_title("상위 10 조합의 시장 배분", loc="left", pad=10)
    ax1.legend(frameon=False, fontsize=8.5, ncol=3, loc="lower right")
    ax1.set_xlim(0, 100)
    for lbl in ax1.get_yticklabels():
        if lbl.get_text() == "15%/40%":
            lbl.set_color(CUR)
            lbl.set_fontweight("bold")

    d2 = df.copy()
    d2["총비용"] = (d2.연매매비 + d2.연환전비) * 100
    d2["x"] = d2.종목상한.map(_cap_num) * 100
    g = d2.groupby("x").agg(비용=("총비용", "mean"), CAGR=("CAGR", "mean"),
                            회전율=("연회전율", "mean")).reset_index().sort_values("x")
    ax2.bar(range(len(g)), g.비용, color="#f0a35e", width=0.56, label="연 거래비용 (%)")
    ax2.set_xticks(range(len(g)), [f"{int(x)}%" if x < 100 else "없음" for x in g.x])
    ax2.set_xlabel("종목 비중 상한")
    ax2.set_ylabel("연 거래비용 (% of NAV)", color="#c87f32")
    ax2.tick_params(axis="y", labelcolor="#c87f32")
    for i, v in enumerate(g.비용):
        ax2.text(i, v + 0.02, f"{v:.2f}", ha="center", fontsize=8, color="#c87f32")
    ax3 = ax2.twinx()
    ax3.plot(range(len(g)), g.CAGR * 100, color=ACCENT, lw=2.2, marker="o", ms=5,
             label="평균 CAGR (%)")
    ax3.set_ylabel("평균 CAGR (%/년)", color=ACCENT)
    ax3.tick_params(axis="y", labelcolor=ACCENT)
    ax3.spines["right"].set_visible(True)
    ax2.set_title("상한을 조이면 수익도 비용도 오른다", loc="left", pad=10)
    ax2.set_ylim(0, max(g.비용) * 1.45)      # 범례가 막대를 덮지 않게 여백
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax3.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, frameon=False, fontsize=8.5,
               loc="upper center", ncol=2, bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout()
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="reports/sweep_caps_w63_g0.09.csv")
    args = ap.parse_args()
    df = pd.read_csv(args.csv)
    outdir = Path("reports/sweep")
    outdir.mkdir(parents=True, exist_ok=True)
    p1 = heatmaps(df, outdir / "sweep_heatmap.png")
    p2 = risk_return(df, outdir / "sweep_risk_return.png")
    p3 = allocation(df, outdir / "sweep_allocation.png")
    for p in (p1, p2, p3):
        print(f"MEDIA:{p.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
