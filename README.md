# 금융프로그래밍2 프로젝트를 위한 폴더

## 프로젝트 개요
- 과제 목표: AI를 통해 모의투자 자동매매 트레이딩 봇 만들기
- 과제 환경:
  - Python 3.13
  - Jupyter Notebook
  - 모의투자 서버: [모의 트레이딩 하네스](https://ssu-vibecoding-pnl-rank.lazzy.chat/guide.html) (MCP 또는 HTTP API)
    - [과제 브리프](https://ssu-vibecoding-pnl-rank.lazzy.chat/brief) · [사용 안내](https://ssu-vibecoding-pnl-rank.lazzy.chat/guide.html)
    - 서버 상태 확인: `/api/health`
- 계좌: 1억 원(KRW) 하나를 학기 내내 사용 (초기화 없음)
- 평가: **마지막 한 달**의 원화 환산 총자산 손익 + MDD, 시스템 완성도, 전략 노트, 회고
- 투자 대상: 코스피 104개, 나스닥 105개, 코인 현물 20개, 코인 무기한 선물 6개 → [tradable_assets.md](tradable_assets.md)
- 현재 전략: GMV(Global Minimum Variance) 포트폴리오 추종 + 괴리율 기준 리밸런싱

## 폴더 구조

### 1. 데이터 수집
| 노트북 | 하는 일 | 데이터 출처 | 만드는 파일 |
|---|---|---|---|
| `kospi_close_30y.ipynb` | 코스피 종목 30년 종가 | DataGuide 엑셀 | `kospi_close_30y.csv`, `kospi_close_30y_summary.csv` |
| `kospi_adj_close_30y.ipynb` | 코스피 종목 30년 수정주가 | DataGuide 엑셀 | `kospi_adj_close_30y.csv`, `kospi_adj_close_30y_summary.csv` |
| `nasdaq_30y.ipynb` | 나스닥 종목 30년 가격 (원주가 복원 포함) | yfinance | `nasdaq_close_30y.csv`, `nasdaq_adj_close_30y.csv`, `nasdaq_raw_close_30y.csv`, `nasdaq_splits_30y.csv`, `nasdaq_30y_summary.csv` |
| `crypto_binance.ipynb` | 코인 현물·무기한 선물 일봉 | Binance 공개 API | `crypto_ohlcv_1d.csv`, `crypto_spot_close.csv`, `crypto_perp_close.csv`, `crypto_summary.csv` |
| `crypto_funding.ipynb` | 무기한 선물 펀딩비 | Binance 공개 API | `crypto_funding_events.csv`, `crypto_funding_daily.csv`, `crypto_funding_summary.csv` |

### 2. 백테스트
| 노트북 | 내용 | 결과 폴더 |
|---|---|---|
| `gmv_backtest.ipynb` | GMV 추종 + 양방향 괴리율 리밸런싱 (기본) | `backtest/` |
| `gmv_backtest_v2.ipynb` | v2: 괴리율 범위 확장 + 다음 날(t+1) 체결 | `backtest_v2/` |
| `gmv_backtest_downside.ipynb` | 하방 괴리율만 보고 리밸런싱 | `backtest_downside/` |

결과 폴더에는 성과표(`results_*.csv`), NAV(`nav_*.csv`), 리밸런싱 기록(`rebalance_log_*.csv`), GMV 비중(`gmv_weights_*.csv`), 주문표(`orders_*.csv`), 그림(`fig*.png`)이 들어 있습니다.

### 3. 메모
- [tradable_assets.md](tradable_assets.md): 거래 가능 종목 목록 (2026-11-01 서버 시세 기준)
- [양방향_전략_보완점.md](양방향_전략_보완점.md): 기본 전략의 보완점 정리

## 트레이딩 봇 (`bot/`, `scripts/`)
수업 과제용. SSU 모의투자 서버에 붙어 **스스로 판단하고 주문하는** 봇.
성적은 마지막 한 달의 실제 손익(원화 환산 총자산, MDD 포함)으로 매긴다.

계정: `s09` / 닉네임 `양교수님사랑단` / 시작금 1억 KRW

### 구조

```
bot/
  config.py    리스크 한도·비용 상수·일정 (여기 값을 넘기는 주문은 코드에서 막힌다)
  client.py    서버 클라이언트 — 사전점검·리스크강제·레이트리밋·거부처리
  universe.py  서버 종목코드 ↔ 외부 데이터 티커 매핑 (선물은 GMV에서 제외)
  data.py      과거 가격 수집 (yfinance·Binance), 원화 환산 패널, 캐시
  strategy.py  GMV + 괴리율 리밸런싱 (노트북에서 이식), 비중 상한, 주문표
  snapshot.py  일별 NAV·순위 기록 (서버가 과거 시계열을 안 주므로 직접 쌓음)
  charts.py    리포트 차트 (NAV+낙폭, 순위표, 순위추이, 비용추이)
  journal.py   거래 기록 (JSONL) — 회고 자료
scripts/
  status.py       읽기 전용 상태 점검 (돈 안 움직임)
  selftest.py     가드·전략 검증 (안전장치가 실제로 막는지)
  verify_costs.py 비용 상수 ↔ 서버 get_rules 대조
  plan.py         오늘의 GMV 목표 비중·주문표 (드라이런)
  report.py       데일리/위클리 리포트 생성 (.md + .png)
logs/          거래·판단·거부 기록 (gitignore)
cache/         과거 가격 캐시 (gitignore)
state/         NAV·순위 기록 (gitignore — 잔고 포함)
reports/       생성된 리포트 (저장소에 남김)
```

### 투자금·일정 (2026-11-10 공지 반영)

- 투자금 **100억 KRW** (1억에서 변경, 전 팀 계좌 초기화)
- 매매 마감 **11/30 자정** · 발표 12/1, 12/8
- 코드는 `get_rules()["startKrw"]` / `get_portfolio()["equityKrw"]` 를 **서버에서 읽는다.**
  `config.START_KRW` 는 서버를 못 읽을 때의 폴백일 뿐이라, 투자금이 또 바뀌어도 자동 반영된다.
- 리스크 한도는 전부 **비율** 기준이므로 자본 규모가 바뀌어도 그대로 유효하다.
- 최소 주문 금액만 자본에 비례한다 (`config.min_order_krw`, NAV의 0.05%,
  바닥값 50만원) — 100억에서는 500만원. 고정 50만원이면 비중 0.005% 주문이
  수백 건 나온다.

### 리포트

```bash
.venv/bin/python scripts/report.py            # 데일리
.venv/bin/python scripts/report.py --weekly   # 위클리
```

`reports/daily/`, `reports/weekly/` 에 `.md` + `.png` 로 저장된다.

- 데일리: 계좌 / 어제 체결·거부·환전 / **비용 집계** / 포지션 / 리스크 한도 / 다음 행동
- 위클리: 주간 성과·23팀 전체 순위표·상위 5팀 포지션(서버가 공개하는 시장 단위까지)·전략 점검
- 서버에 `/api/history` 가 없어서(404) 전일 대비·추이는 `state/` 에 직접 쌓은
  스냅샷으로 계산한다. 하루라도 안 찍으면 그날 비교가 빈다.

### 전략: GMV + 괴리율 리밸런싱

`gmv_backtest_v2.ipynb` 에서 이식. 윈도우 3개월(63거래일), 괴리율 임계값 9%.

1. 가상 포트폴리오 — 매일 GMV 비중 유지 (비용 없음, 기준선)
2. 실제 포트폴리오 — 시작 시 맞춘 뒤 보유 (비중이 흘러감)
3. 괴리율 = |가상 누적수익률 − 실제 누적수익률| ≥ 9% 이면 리밸런싱

**원본 GMV 의 쏠림 문제** (보완점 4-1): 라이브 데이터로 측정하니 한 종목에
**88.16%**, 코스피에 **93%** 가 쏠렸다. 서버는 분배금을 주지 않아 채권 ETF 의
가격 수익률은 연 1.1% — 자산 대부분이 0% 수익에 묶인다. 그래서 세 단계로 막는다.

| 단계 | 효과 (2026-11-10 실측) |
|---|---|
| 원본 GMV | 최대 88.16%, 코스피 93%, 사전변동성 1.55% |
| + 종목 상한 15% | 최대 15.00%, 46종목 |
| + 하한 0.5% | 35종목 (소액 포지션 제거) |
| + 시장 상한 40% | 최대 11.77%, 코스피 40%·나스닥 40%·코인 20%, 변동성 10.62% |

**상한을 하한보다 먼저 적용한다.** 순서를 바꾸면 쏠린 비중 외의 소수점들이
전멸해 7종목만 남고 분산이 오히려 나빠진다 (변동성 6.91% → 10.59%).
상한이 실행 불가능하면(종목 < ⌈1/상한⌉) 조용히 넘기지 않고 예외를 던진다.

### 셋업

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python requests python-dotenv pandas numpy
cp .env.example .env    # lazzy_token 채우기
```

토큰은 `.env` 또는 `~/.hermes/profiles/team/secrets/ssu-paper.env`.
**절대 커밋하지 않는다** — 커밋 이력에 남으면 감점이고 계좌는 복구되지 않는다.

### 사용

```bash
.venv/bin/python scripts/status.py        # 현재 상태
.venv/bin/python scripts/selftest.py      # 가드·전략 검증 (29개 체크)
.venv/bin/python scripts/verify_costs.py  # 비용 상수 ↔ 서버 get_rules 대조
.venv/bin/python scripts/plan.py          # 오늘의 GMV 비중·주문표 (드라이런)
.venv/bin/python scripts/plan.py --no-cap # 상한 없는 원본 GMV (비교용)
```

`.env` 를 Windows 에서 만들면 CRLF 로 저장돼 토큰 끝에 `\r` 이 붙고
HTTP 헤더가 거부된다. `config.py` 가 `.strip()` 으로 처리하지만, 가능하면
LF 로 저장할 것.

### 안전장치 (bot/config.py)

| 한도 | 값 | 이유 |
|---|---|---|
| 한 종목 비중 | 15% | 단일 종목 사고로 계좌가 흔들리지 않게 |
| 한 시장 비중 | 40% | 시장 전체 급락 노출 제한 |
| 선물 명목가 | 30% | 레버리지 폭주 방지 |
| 레버리지 | 3배 | 서버는 150배 허용 — 우리가 낮춘다 |
| 교차 증거금 | 금지 | 같은 버킷 포지션이 한꺼번에 청산됨 |
| 누적 손실 | -20% | 도달 시 신규 진입 중단 |
| 하루 주문 | 40건 | 과매매·레이트리밋 방지 |
| 최소 엣지 | 왕복비용 × 2 | 비용을 못 넘는 진입 거부 |

주문·환전·이체는 전부 `confirmed=True`를 명시해야 실행된다.

### 비용 (손익분기)

| 시장 | 수수료 | 슬립 | **왕복** |
|---|---|---|---|
| 코스피 | 0.15% | 0.05% | **0.40%** |
| 나스닥 | 0.05% | 0.03% | **0.16%** |
| 코인 현물 | 0.10% | 0.02% | **0.24%** |
| 선물 | 0.05% | 0.02% | **0.14%** + 펀딩비 |

환전: KRW↔USD 0.90% / USD↔USDT 0.15% / **KRW→USDT 1.05%** (2단계 직렬)
정산 지연: KRW↔USD 1분, USD↔USDT 1분, KRW→USDT 약 2분.
운송 중인 돈은 **못 쓰지만 평가엔 도착 통화 기준으로 반영** → 그 사이 환율 변동은 그대로 손익.

경로를 바꿔도 환전 비용은 동일하다 — **무위험 차익은 수학적으로 없다.**

### 종목 코드 → 시장 자동 판별

| 모양 | 시장 | 예 | 결제통화 |
|---|---|---|---|
| 숫자 6자리 | 코스피 | `005930` | KRW |
| 알파벳 | 나스닥 | `NVDA` | USD |
| …USDT | 코인 현물 | `BTCUSDT` | USDT |
| ….P | USD-M 무기한 | `BTCUSDT.P` | USDT 증거금 |
| …_PERP | COIN-M 무기한 | `BTCUSD_PERP` | 코인 증거금 |

현물 `BTCUSDT`와 선물 `BTCUSDT.P`는 **다른 종목**이다.

### 제출물 (예정)

1. 에이전트 코드 전체 (토큰 제외, 환경변수로)
2. 전략 노트 1~2장 — 무엇을 보고 언제 사고 파는가, 비용 반영 방식, 리스크 한도와 이유
3. 회고 1~2장 — 가장 크게 깨진 거래 하나를 기록으로 되짚고 무엇을 바꿀 것인가

## 실행 방법

### 1. 패키지 설치
```bash
pip install pandas numpy scipy matplotlib yfinance requests python-calamine jupyter
```

### 2. 데이터 폴더 지정 (환경변수)
노트북 경로는 환경변수로 뺐습니다. 하드코딩된 절대경로는 없습니다.

```bash
export FP2_PROJECT_DIR=/path/to/프로젝트        # CSV·결과물 폴더 (기본: 노트북이 있는 폴더)
export FP2_DATAGUIDE_DIR=/Volumes/USB_128GB/DataGuide  # 코스피 원본 (DataGuide USB)
```

둘 다 생략하면 `FP2_PROJECT_DIR`은 현재 폴더가 되고, DataGuide 경로는 기존 USB 경로를 씁니다.

### 3. 실행 순서
1. 데이터 수집 노트북 실행 (순서 무관)
2. 백테스트 노트북 실행

### 4. 비용 상수 점검 (백테스트 전)
```bash
.venv/bin/python scripts/verify_costs.py
```
노트북의 `COST`·`FX_COST`와 `bot/config.py`를 서버 `get_rules` 실측값과 대조합니다.
**2026-11-06 확인: 전부 일치.** 수업 중 서버 규칙이 바뀌면 이걸로 바로 잡힙니다.

## 데이터
CSV와 PNG 파일은 용량이 커서 GitHub에 올리지 않습니다 (`.gitignore`로 제외).

- **나스닥·코인**: 위의 데이터 수집 노트북을 실행하면 다시 받을 수 있습니다.
- **코스피**: DataGuide 엑셀 원본이 필요해서 노트북으로 다시 만들 수 없습니다. CSV 파일은 따로 공유합니다. (TODO: 공유 링크)

## 서버 규칙 요약
| 항목 | 내용 |
|---|---|
| 결제 통화 | 코스피 KRW · 나스닥 USD · 코인 USDT |
| 환전 수수료 | KRW↔USD 0.90%, USD↔USDT 0.15%, KRW↔USDT 1.05% (편도) |
| 환전 도착 | KRW↔USD 약 1분, USD↔USDT 1분, KRW→USDT 약 2분 (도착 전에는 사용 불가) |
| 장 시간 (한국시각) | 코스피 09:00–15:30, 나스닥 23:30–06:00 (서머타임에 따라 이동), 코인 24시간 |
| 시세 신선도 | 120초가 지난 시세로는 체결 보류 |
| 제한 | 코스피 공매도 불가, 현물 레버리지 없음 |
| 선물 | 마크가격 기준 청산 (바이낸스 유지증거금 테이블), 펀딩비 8시간마다 명목가 기준 |

정확한 매매 수수료 등 전체 규칙은 `get_rules`(`GET /api/rules`)로 확인합니다.

## 주의사항
- **서버 토큰은 `.env` 파일에만 넣고, 코드에는 환경변수로만 읽습니다.** 토큰이 커밋에 남아 있으면 그 자체로 감점이고, 남이 계좌를 청산시킬 수 있습니다.
  ```
  # .env
  PAPER_TOKEN=여기에_토큰
  ```
  ```python
  import os
  TOKEN = os.environ["PAPER_TOKEN"]
  ```
- 토큰을 단체 채팅이나 공유 화면에 붙여넣지 않습니다.
- 서버는 45명이 같이 쓰므로 API를 필요할 때만 호출합니다 (`get_quotes`의 경과 초를 보고 판단).
- 같은 노트북을 여러 명이 동시에 수정하면 충돌이 나기 쉬우니, 작업 전에 `git pull`을 하고 담당을 나눠서 작업합니다.

## 팀원 및 역할
| 이름 | 담당 |
|---|---|
| (TODO) | |

## 제출물 체크리스트
제출처·마감은 담당 조교 공지를 따릅니다.
- [ ] **저장소**: 에이전트 코드 전부 (토큰 제외, 환경변수로)
- [ ] **전략 노트 (1~2쪽)**: 무엇을 보고 언제 사고파는가 / 비용을 어떻게 계산에 넣었는가 / 리스크 한도와 그 숫자의 근거
- [ ] **회고 (1~2쪽)**: 가장 크게 잃은 거래 하나를 기록으로 되짚기 (잃은 적이 없으면 가장 크게 딴 거래가 운이었는지 설계였는지)

## TODO
- [ ] 모의투자 서버 연동 (토큰은 `.env`)
- [x] 에이전트 코드: 시세 조회 → GMV 비중 계산 → 주문표 (`scripts/plan.py`, 드라이런까지)
- [ ] 괴리율 상태 저장·리밸런싱 루프 (재시작 후 기준점 유지)
- [x] 주문 전 확인: 잔고, 장 시간, 시세 신선도
- [ ] 환전 도착 여부(`settlesAtKst`) 대기 처리
- [x] 거부·오류 대응: 거부 원문 기록, 429 Retry-After, 5xx 는 재주문 금지
- [x] 로깅: 판단근거·주문·거부·스냅샷 JSONL (회고용)
- [x] 리스크 한도를 코드에 고정 (종목 15%·시장 40%·레버리지 3배·손실 -20%)
- [ ] 서버 끊김·오래된 시세에서 멈췄다가 이어서 돌기
- [x] 백테스트 매매 수수료(`COST`)를 `get_rules`의 실제 값으로 맞추기 — **이미 일치했음**, `scripts/verify_costs.py`로 자동 검증
- [ ] [양방향_전략_보완점.md](양방향_전략_보완점.md)의 보완점 반영
- [ ] 성적 구간(마지막 한 달) 시작일 공지 확인
