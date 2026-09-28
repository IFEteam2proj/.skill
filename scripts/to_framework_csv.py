"""
견해 모듈 결과(bl_input/)를 framework-main CSV 규격서(CSV_SPECIFICATION.md)의 4개 파일로 변환한다.

입력 (모듈 공통 형식)
  <module_dir>/bl_input/assets.csv   order, PERMNO, TICKER, COMNAM
  <module_dir>/bl_input/P.csv        [date, hold_until,] view, <PERMNO...>   (date 없으면 1행을 모든 날짜에 사용)
  <module_dir>/bl_input/Q.csv        date, hold_until, <view>               (월 수익률 단위)
  <module_dir>/bl_input/Omega.csv    date, hold_until, <view>|<view>         (월 단위 분산)

출력 (<module_dir>/framework_csv/)
  market_weights.csv, covariance_matrix.csv, investor_views.csv, monthly_returns.csv

단위 변환: framework 는 연율 기준 → Σ·Q·Ω 모두 ×12 (BL 사후 기대수익률도 정확히 ×12가 되어 월 단위 결과와 일치).
Ω 는 View_Description 의 "(Omega var: x)" 로 넘긴다 (bl_engine.parse_omega_var_from_string 이 읽음).

사용법
  python to_framework_csv.py <module_dir> [--stock-data CSV] [--out DIR] [--cov-window 60] [--tau 0.05]
"""

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

FILES = ["market_weights.csv", "covariance_matrix.csv", "investor_views.csv", "monthly_returns.csv"]


def fmt(x):
    """지수 표기 없이 소수점 숫자로 쓴다 (규격 체크리스트 6번)."""
    s = f"{float(x):.10f}".rstrip("0")
    s = s + "0" if s.endswith(".") else s
    return "0.0" if s in ("-0.0", "0.0") else s


def find_stock_data(start: Path) -> Path:
    for d in [start, *start.parents]:
        f = d / "Project_Data" / "IFE_term_stock_data.csv"
        if f.is_file():
            return f
    sys.exit("[오류] Project_Data/IFE_term_stock_data.csv 를 찾지 못했습니다. --stock-data 로 지정하세요.")


def load_views(bl: Path, permnos):
    Q = pd.read_csv(bl / "Q.csv", dtype={"date": str, "hold_until": str})
    O = pd.read_csv(bl / "Omega.csv", dtype={"date": str, "hold_until": str})
    P = pd.read_csv(bl / "P.csv", dtype={"date": str, "hold_until": str})

    view_cols = [c for c in Q.columns if c not in ("date", "hold_until")]
    if len(view_cols) != 1:
        sys.exit(f"[오류] 날짜당 견해가 {len(view_cols)}개입니다. framework 백테스트는 행 순서로 수익률과 짝을 지어서 "
                 "날짜당 견해 1개만 지원합니다.")
    view = view_cols[0]

    if "date" in P.columns:
        P = P.set_index("date")
    else:  # 근석 형식: P 1행 고정
        P = pd.DataFrame([P.iloc[0]] * len(Q), index=Q["date"])
    P = P.reindex(Q["date"])[[str(p) for p in permnos]].astype(float)
    if P.isna().any().any():
        sys.exit("[오류] P.csv 에 Q.csv 날짜가 빠져 있습니다.")

    omega = O.set_index("date").reindex(Q["date"]).iloc[:, -1].astype(float)
    return view, Q.set_index("date")[["hold_until", view]], P, omega


def monthly_returns(stock_csv: Path, permnos):
    df = pd.read_csv(stock_csv, usecols=["PERMNO", "date", "ADJ_PRC"], parse_dates=["date"])
    df = df[df["PERMNO"].isin(permnos) & (df["ADJ_PRC"] > 0)].copy()
    df["ym"] = df["date"].dt.to_period("M")
    df = df.sort_values(["PERMNO", "date"]).drop_duplicates(["PERMNO", "ym"], keep="last")
    prc = df.pivot(index="ym", columns="PERMNO", values="ADJ_PRC")
    prc = prc.reindex(pd.period_range(prc.index.min(), prc.index.max(), freq="M"))[list(permnos)]
    ret = prc / prc.shift(1) - 1.0
    # 월말 표시일: 데이터에 있는 그 달의 마지막 거래일
    last_day = df.groupby("ym")["date"].max().dt.strftime("%Y-%m-%d")
    return ret, last_day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("module_dir", type=Path)
    ap.add_argument("--stock-data", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--cov-window", type=int, default=60)
    ap.add_argument("--tau", type=float, default=0.05, help="Confidence 역산용 (framework 기본 τ)")
    a = ap.parse_args()

    mod = a.module_dir.resolve()
    bl = mod / "bl_input"
    out = (a.out or mod / "framework_csv").resolve()
    out.mkdir(parents=True, exist_ok=True)
    stock_csv = a.stock_data or find_stock_data(mod)

    assets = pd.read_csv(bl / "assets.csv").sort_values("order")
    permnos = assets["PERMNO"].astype(int).tolist()
    tickers = assets["TICKER"].astype(str).str.strip().tolist()
    if len(set(tickers)) != len(tickers):
        sys.exit(f"[오류] 티커 중복: {tickers}")

    view, Q, P, omega_m = load_views(bl, permnos)
    ret, last_day = monthly_returns(stock_csv, permnos)

    # --- Σ: 첫 견해 날짜까지 최근 N개월 표본공분산 (BL_Platform '고정' 방식과 동일), 연율화 ---
    first = pd.Period(Q.index[0], freq="M")
    win = ret.loc[first - a.cov_window + 1:first].dropna()
    if len(win) < 24:
        sys.exit(f"[오류] 공분산 추정 관측치 부족 ({len(win)}개월).")
    S_ann = win.cov().values * 12.0
    S_ann = (S_ann + S_ann.T) / 2.0

    # 1. market_weights.csv — 발행주식수가 없어 시가총액 계산 불가 → 동일가중
    pd.DataFrame({"Asset": tickers, "MarketWeight": [fmt(1.0 / len(tickers))] * len(tickers)}) \
        .to_csv(out / "market_weights.csv", index=False)

    # 2. covariance_matrix.csv
    cov_df = pd.DataFrame([[fmt(v) for v in row] for row in S_ann], columns=tickers)
    cov_df.insert(0, "Asset", tickers)
    cov_df.to_csv(out / "covariance_matrix.csv", index=False)

    # 3. investor_views.csv
    rows, clipped = [], 0
    for d in Q.index:
        p = P.loc[d].values
        q_ann = float(Q.loc[d, view]) * 12.0
        om_ann = float(omega_m.loc[d]) * 12.0
        base = a.tau * float(p @ S_ann @ p)
        conf = base / (base + om_ann)  # (1-C)/C·τPΣPᵀ = Ω 가 되는 C
        if not 0.01 <= conf <= 0.99:
            clipped += 1
            conf = min(0.99, max(0.01, conf))
        rows.append([f"[{d}] {view} (Omega var: {fmt(om_ann)})", fmt(q_ann), fmt(round(conf, 4))]
                    + [fmt(round(x, 10)) for x in p])
    pd.DataFrame(rows, columns=["View_Description", "Target_Return", "Confidence"] + tickers) \
        .to_csv(out / "investor_views.csv", index=False)

    # 4. monthly_returns.csv — 견해 i 의 보유월(hold_until) 수익률을 i 번째 행에 (framework 는 행 순서로 짝지음)
    hold = [pd.Period(h, freq="M") for h in Q["hold_until"]]
    R = ret.reindex(hold)
    if R.isna().any().any():
        sys.exit(f"[오류] 보유월 수익률 결측: {R[R.isna().any(axis=1)].index.tolist()}")
    ret_df = pd.DataFrame([[fmt(v) for v in r] for r in R.values], columns=tickers)
    ret_df.insert(0, "Date", [last_day[h] for h in hold])
    ret_df.to_csv(out / "monthly_returns.csv", index=False)

    # --- 규격 체크리스트 검증 (다시 읽어서 확인) ---
    w = pd.read_csv(out / "market_weights.csv")
    c = pd.read_csv(out / "covariance_matrix.csv")
    v = pd.read_csv(out / "investor_views.csv")
    m = pd.read_csv(out / "monthly_returns.csv")
    C = c[tickers].values
    Pv = v[tickers].values
    rel = np.abs(Pv).sum(axis=1) != 1.0  # 절대 전망(단일 자산 1.0)이 아닌 행
    checks = {
        "1 자산명 4개 파일 일치": list(w["Asset"]) == list(c["Asset"]) == list(c.columns[1:])
                                 == list(v.columns[3:]) == list(m.columns[1:]) == tickers,
        "2 공분산 첫 열 Asset·대칭·대각>0": c.columns[0] == "Asset" and np.allclose(C, C.T) and (np.diag(C) > 0).all(),
        "3 상대 전망 P 행 합 = 0": bool(np.allclose(Pv[rel].sum(axis=1), 0.0, atol=1e-9)),
        "4 View_Description 에 [YYYY-MM-DD]": bool(v["View_Description"].str.match(r"^\[\d{4}-\d{2}-\d{2}\]").all()),
        "5 수익률 Date 형식·오름차순": bool(m["Date"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all()
                                      and m["Date"].is_monotonic_increasing),
        "6 % · 지수표기 없음": not any(re.search(r"%|\d[eE][+-]?\d", (out / f).read_text(encoding="utf-8"))
                                     for f in FILES),
        "견해 수 = 수익률 행 수": len(v) == len(m),
        "Target_Return ≠ 0 (0이면 framework가 행을 버려 짝이 밀림)": bool((v["Target_Return"].abs() > 1e-9).all()),
    }

    print(f"입력: {mod}")
    print(f"출력: {out}")
    print(f"  종목 {len(tickers)}개 | 견해 {len(v)}개 ({v['View_Description'].iloc[0][:12]} ~ "
          f"{v['View_Description'].iloc[-1][:12]}) | 수익률 {m['Date'].iloc[0]} ~ {m['Date'].iloc[-1]}")
    print(f"  Σ: {win.index[0]} ~ {win.index[-1]} ({len(win)}개월) ×12 | Q·Ω ×12 | 시장가중치: 동일가중")
    if clipped:
        print(f"  ! Confidence 역산값 {clipped}개가 [0.01, 0.99] 밖이라 잘랐습니다 (Omega var 태그가 우선 적용되므로 결과엔 영향 없음).")
    ok = True
    for k, passed in checks.items():
        print(f"  [{'OK' if passed else 'FAIL'}] {k}")
        ok &= passed
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
