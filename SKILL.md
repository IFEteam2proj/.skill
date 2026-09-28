---
name: bl-framework-csv
description: 견해 모듈 결과(bl_input/ 의 assets·P·Q·Omega.csv)를 framework-main CSV 규격서(CSV_SPECIFICATION.md)의 4개 업로드 파일(market_weights, covariance_matrix, investor_views, monthly_returns)로 변환하고 규격 체크리스트를 검증한다. "framework 규격으로 바꿔줘", "CSV 규격에 맞춰", "Streamlit 앱에 올릴 CSV", 새 견해 모듈 결과를 framework-main 에 넣을 때 사용.
---

# 견해 모듈 결과 → framework-main CSV 변환

## 언제 쓰나
`BL_Platform/Module_Based_ML/<모듈>/.../bl_input/` 형식(상준·근석 공통)의 결과를
`framework-main` Streamlit 앱에 업로드할 4개 CSV로 바꿀 때.

## 실행
프로젝트 루트(`IFE_term_project/`)에서, `bl_input/` 이 들어 있는 폴더마다 한 번씩:

```bash
python .claude/skills/bl-framework-csv/scripts/to_framework_csv.py "<bl_input 의 상위 폴더>"
# 예: python .claude/skills/bl-framework-csv/scripts/to_framework_csv.py "BL_Platform/Module_Based_ML/Module(상준)/outputs_ANN/P1"
```

- 결과는 `<폴더>/framework_csv/` 에 생긴다. 원래 `bl_input/` 은 건드리지 않는다 (BL_Platform 이 계속 읽음).
- 주가 데이터는 위쪽 폴더에서 `Project_Data/IFE_term_stock_data.csv` 를 자동으로 찾는다. 못 찾으면 `--stock-data` 로 지정.
- Windows 콘솔에서 한글이 깨지면 `PYTHONIOENCODING=utf-8` 을 붙인다.
- 끝에 체크리스트 결과가 `[OK]/[FAIL]` 로 나온다. 하나라도 FAIL 이면 종료코드 1 → 원인을 사용자에게 알린다.

## 변환 규칙 (사용자에게 설명할 때 참고)
| 파일 | 만드는 방법 |
|---|---|
| `market_weights.csv` | 동일가중 1/N. 원자료에 발행주식수가 없어 시가총액을 못 구함 (BL_Platform 기본값과 같음) |
| `covariance_matrix.csv` | 첫 견해 날짜까지 최근 60개월 월수익률 표본공분산(ddof=1) × 12 (BL_Platform '고정 Σ'와 같음) |
| `investor_views.csv` | `View_Description = "[날짜] 견해명 (Omega var: Ω×12)"`, `Target_Return = Q×12`, P 는 PERMNO → 티커 |
| `monthly_returns.csv` | 각 견해의 보유월(hold_until) 실현 수익률, 수정주가 기준 단순수익률 |

- **×12 이유**: 규격이 연율 기준이다. Σ·Q·Ω 를 모두 ×12 하면 BL 사후 기대수익률도 정확히 ×12 가 되어 월 단위 결과와 같다.
- **Ω**: framework 엔진은 설명에 `(Omega var: x)` 가 있으면 그 값을 Ω 로 쓴다 (`bl_engine.parse_omega_var_from_string`). 그래서 모듈 Ω 가 그대로 반영된다.
- **Confidence**: τ=0.05 에서 `(1−C)/C·τPΣPᵀ = Ω` 가 되는 C 를 역산해 넣는다. 태그가 지워져도 신뢰도 방식으로 거의 같은 Ω 가 나온다. [0.01, 0.99] 밖이면 잘린다.
- **행 순서 = 짝**: framework 백테스트는 날짜가 아니라 **행 번호**로 견해와 수익률을 짝짓는다. 그래서 견해 i 행과 수익률 i 행이 같은 보유월이 되게 만들고, Q=0 인 행이 없는지 확인한다 (Q=0 행은 앱이 버려서 짝이 밀림).

## 제약 (FAIL 시 안내)
- 날짜당 견해가 2개 이상이면 변환하지 않는다 (위 행 순서 방식 때문에 framework 가 처리 못함).
- `constraints.json` 의 종목당 최대 비중은 규격에 칸이 없어 옮겨지지 않는다. framework 앱은 상한 없이 최적화한다.
