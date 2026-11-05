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

## 실행 방법

### 1. 패키지 설치
```bash
pip install pandas numpy scipy matplotlib yfinance requests python-calamine jupyter
```

### 2. 경로 수정
노트북 첫 부분의 경로가 작성자 컴퓨터 기준 절대경로로 되어 있습니다. 실행 전에 본인 컴퓨터의 프로젝트 폴더 경로로 바꿔 주세요.
```python
Path("/Users/kasia/Desktop/학교/26-2/금프2/프로젝트")  # → 본인 경로로 수정
```

### 3. 실행 순서
1. 데이터 수집 노트북 실행 (순서 무관)
2. 백테스트 노트북 실행

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
- [ ] 에이전트 코드: 시세 조회 → GMV 비중 계산 → 괴리율 확인 → 주문
- [ ] 주문 전 확인: 잔고, 장 시간, 시세 신선도, 환전 도착 여부
- [ ] 거부·오류 대응: 거부 메시지를 읽고 같은 거부를 반복하지 않기
- [ ] 로깅: 무엇을 왜 했는지 기록 (회고용)
- [ ] 리스크 한도를 코드에 고정 (한 종목 최대 비중 등)
- [ ] 서버 끊김·오래된 시세에서 멈췄다가 이어서 돌기
- [ ] 백테스트 매매 수수료(`COST`)를 `get_rules`의 실제 값으로 맞추기
- [ ] [양방향_전략_보완점.md](양방향_전략_보완점.md)의 보완점 반영
- [ ] 성적 구간(마지막 한 달) 시작일 공지 확인
