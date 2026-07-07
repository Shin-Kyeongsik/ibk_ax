"""
분석 계층 — 원본 주간 CSV를 읽어 통계·특이점을 계산하고
집계 결과만 output/*.json 으로 출력한다. (웹은 이 JSON만 소비)

산출물:
  output/summary.json    : 4종 지표 × 주차별 통계 + 지점/연령/성별/상품 집계
  output/anomalies.json  : 특이점 ①통계이상치 ②급변 ③구조신호 ④지속추세
"""
import pandas as pd, numpy as np, json, os

DATA = "data"
OUT = "output"
os.makedirs(OUT, exist_ok=True)

# 지표 정의: (표시명, 파일명, 금액컬럼)
METRICS = [
    ("여신잔액", "여신잔액.csv", "여신잔액"),
    ("수신잔액", "수신잔액.csv", "수신잔액"),
    ("카드사용금액", "카드사용금액.csv", "사용금액"),
    ("보험잔액", "보험잔액.csv", "보험잔액"),
]

cal = pd.read_csv(f"{DATA}/calendar.csv").sort_values("주차")
cust = pd.read_csv(f"{DATA}/customers.csv")

def age_group(a):
    return f"{min(int(a)//10*10, 70)}대"
cust["연령대"] = cust["연령"].map(age_group)
CINFO = cust.set_index("고객ID")[["지점명", "연령대", "성별"]]

def load_metric(fname, amount):
    """한 지표를 12주 long-form으로 로드 (고객ID, 주차, 기준일자, 상품/카드구분, 금액)."""
    parts = []
    for _, r in cal.iterrows():
        fp = f"{DATA}/{r['폴더명']}/{fname}"
        df = pd.read_csv(fp)
        parts.append(df)
    long = pd.concat(parts, ignore_index=True)
    long = long.rename(columns={amount: "금액"})
    catcol = "카드구분" if "카드구분" in long.columns else "상품구분"
    long = long.rename(columns={catcol: "구분"})
    return long[["고객ID", "주차", "기준일자", "구분", "금액"]]

# ------------------------------------------------------------------ summary
LATEST = int(cal["주차"].max())
PREV = LATEST - 1
summary = {"기준정보": {"고객수": len(cust), "주차수": int(cal["주차"].max()),
                     "최신주차": LATEST, "최신기준일자": cal.iloc[-1]["기준일자"]},
           "지표": {}}
metric_long = {}

for name, fname, amount in METRICS:
    long = load_metric(fname, amount)
    metric_long[name] = long

    # 주차별 통계
    weekly = []
    prev_total = None
    for w in range(1, LATEST + 1):
        wk = long[long["주차"] == w]
        holders = wk[wk["금액"] > 0]["금액"]
        total = int(wk["금액"].sum())
        wow = None if prev_total in (None, 0) else round((total - prev_total) / prev_total * 100, 2)
        weekly.append({
            "주차": w,
            "기준일자": wk.iloc[0]["기준일자"],
            "총액": total,
            "평균_전체": int(round(wk["금액"].mean())),
            "평균_보유자": int(round(holders.mean())) if len(holders) else 0,
            "중앙값_보유자": int(holders.median()) if len(holders) else 0,
            "보유자수": int((wk["금액"] > 0).sum()),
            "전주대비증감률": wow,
        })
        prev_total = total

    # 최신 주차 집계 (지점/연령대/성별/상품)
    latest = long[long["주차"] == LATEST].merge(CINFO, on="고객ID", how="left")
    def agg_by(col):
        g = latest.groupby(col)["금액"]
        return {str(k): {"총액": int(v), "평균": int(round(latest.groupby(col)["금액"].mean()[k])),
                         "인원": int((latest.groupby(col)["금액"].apply(lambda s: (s > 0).sum()))[k])}
                for k, v in g.sum().items()}
    summary["지표"][name] = {
        "주차별": weekly,
        "지점별": agg_by("지점명"),
        "연령대별": agg_by("연령대"),
        "성별": agg_by("성별"),
        "상품구분별": agg_by("구분"),
    }

# ------------------------------------------------------------------ anomalies
anom = {"기준주차": LATEST, "직전주차": PREV, "지표별": {}}

for name, fname, amount in METRICS:
    long = metric_long[name]
    cur = long[long["주차"] == LATEST][["고객ID", "금액"]].rename(columns={"금액": "현재"})
    prv = long[long["주차"] == PREV][["고객ID", "금액"]].rename(columns={"금액": "직전"})
    m = cur.merge(prv, on="고객ID").merge(CINFO, on="고객ID", how="left")
    holders = m[m["현재"] > 0].copy()

    # ① 통계적 이상치: IQR 상단 밖 (Q3 + 1.5*IQR) + z-score(3σ)
    q1, q3 = holders["현재"].quantile([0.25, 0.75])
    iqr = q3 - q1
    fence = q3 + 1.5 * iqr
    mu, sd = holders["현재"].mean(), holders["현재"].std()
    holders["zscore"] = (holders["현재"] - mu) / sd
    out = holders[(holders["현재"] > fence) | (holders["zscore"].abs() >= 3)]
    out = out.sort_values("현재", ascending=False)
    outliers = [{"고객ID": r["고객ID"], "지점명": r["지점명"], "연령대": r["연령대"],
                 "금액": int(r["현재"]), "zscore": round(float(r["zscore"]), 2)}
                for _, r in out.head(20).iterrows()]

    # ② 급변: 전주 대비 ±30%
    ch = holders[holders["직전"] > 0].copy()
    ch["증감률"] = (ch["현재"] - ch["직전"]) / ch["직전"] * 100
    surge = ch[ch["증감률"] >= 30].sort_values("증감률", ascending=False)
    drop = ch[ch["증감률"] <= -30].sort_values("증감률")
    def rows(d):
        return [{"고객ID": r["고객ID"], "지점명": r["지점명"], "직전": int(r["직전"]),
                 "현재": int(r["현재"]), "증감률": round(float(r["증감률"]), 1)}
                for _, r in d.head(15).iterrows()]

    anom["지표별"].setdefault(name, {})
    anom["지표별"][name]["통계적이상치"] = {"기준_IQR상단": int(fence), "탐지수": int(len(out)), "목록": outliers}
    anom["지표별"][name]["급변"] = {"임계": "±30%", "급증수": int(len(surge)), "급감수": int(len(drop)),
                                "급증": rows(surge), "급감": rows(drop)}

# ③ 구조적 신호: 여신 증가(+5%↑) & 수신 감소(-5%↓) 동시
def wow_frame(name):
    long = metric_long[name]
    cur = long[long["주차"] == LATEST][["고객ID", "금액"]].rename(columns={"금액": f"{name}_현재"})
    prv = long[long["주차"] == PREV][["고객ID", "금액"]].rename(columns={"금액": f"{name}_직전"})
    f = cur.merge(prv, on="고객ID")
    f[f"{name}_증감률"] = np.where(f[f"{name}_직전"] > 0,
                                (f[f"{name}_현재"] - f[f"{name}_직전"]) / f[f"{name}_직전"] * 100, np.nan)
    return f
loan = wow_frame("여신잔액")
dep = wow_frame("수신잔액")
struct = loan.merge(dep, on="고객ID").merge(CINFO, on="고객ID", how="left")
sig = struct[(struct["여신잔액_증감률"] >= 5) & (struct["수신잔액_증감률"] <= -5)]
sig = sig.sort_values("여신잔액_증감률", ascending=False)
anom["구조적신호"] = {
    "정의": "여신 +5%↑ 이면서 동시에 수신 -5%↓ (재무악화 조기신호)",
    "탐지수": int(len(sig)),
    "목록": [{"고객ID": r["고객ID"], "지점명": r["지점명"], "연령대": r["연령대"],
             "여신증감률": round(float(r["여신잔액_증감률"]), 1),
             "수신증감률": round(float(r["수신잔액_증감률"]), 1)}
            for _, r in sig.head(20).iterrows()],
}

# ④ 지속 추세: 12주 순변화 상·하위 5% (지속 증가/감소 고객)
anom["지속추세"] = {}
for name, fname, amount in METRICS:
    long = metric_long[name]
    pivot = long.pivot_table(index="고객ID", columns="주차", values="금액")
    both = pivot[(pivot[1] > 0) & (pivot[LATEST] > 0)].copy()
    net = (both[LATEST] - both[1]) / both[1] * 100
    # 방향 일관성: 주간 증분 부호가 다수인 비율
    diffs = both.diff(axis=1).iloc[:, 1:]
    up_ratio = (diffs > 0).sum(axis=1) / diffs.shape[1]
    df = pd.DataFrame({"순증감률": net, "상승비율": up_ratio}).merge(CINFO, on="고객ID", how="left")
    # 지속추세 = 방향이 일관된(단조에 가까운) 고객만. 계단형 급등(이상치)은 제외.
    hi = df[df["상승비율"] >= 0.8].sort_values("순증감률", ascending=False).head(15)
    lo = df[df["상승비율"] <= 0.2].sort_values("순증감률").head(15)
    def trows(d):
        return [{"고객ID": idx, "지점명": r["지점명"], "순증감률": round(float(r["순증감률"]), 1),
                 "상승주비율": round(float(r["상승비율"]), 2)}
                for idx, r in d.iterrows()]
    anom["지속추세"][name] = {"정의": "12주 순증감률 상·하위", "지속증가": trows(hi), "지속감소": trows(lo)}

# ------------------------------------------------------------------ write
with open(f"{OUT}/summary.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
with open(f"{OUT}/anomalies.json", "w", encoding="utf-8") as f:
    json.dump(anom, f, ensure_ascii=False, indent=2)

# 콘솔 요약
print("=== 분석 완료 ===  최신주차:", LATEST)
for name, *_ in METRICS:
    w = summary["지표"][name]["주차별"][-1]
    a = anom["지표별"][name]
    print(f"[{name}] 총액 {w['총액']:,} / 전주대비 {w['전주대비증감률']}% "
          f"| 이상치 {a['통계적이상치']['탐지수']} 급증 {a['급변']['급증수']} 급감 {a['급변']['급감수']}")
print(f"[구조적신호] 여신↑&수신↓ 동시: {anom['구조적신호']['탐지수']}명")
print("출력: output/summary.json, output/anomalies.json")
