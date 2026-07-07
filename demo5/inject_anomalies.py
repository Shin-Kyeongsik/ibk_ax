"""
현실적 특이점 주입기.
생성된 12주 데이터가 통계적으로 너무 매끈해 탐지할 신호가 없으므로,
소수(33명)의 지정 고객에게만 각 유형별 실제 이상 신호를 심는다.
나머지 고객/셀은 전혀 건드리지 않는다. (구조·행수·컬럼 불변)
"""
import pandas as pd, numpy as np, os

DATA = "data"
cal = pd.read_csv(f"{DATA}/calendar.csv").sort_values("주차")
weeks = list(cal["주차"])
LATEST, PREV = weeks[-1], weeks[-2]
folder = dict(zip(cal["주차"], cal["폴더명"]))

ids = list(pd.read_csv(f"{DATA}/customers.csv")["고객ID"])
rng = np.random.default_rng(424242)
picked = list(rng.choice(ids, size=33, replace=False))
g = iter(picked)
def take(n): return [next(g) for _ in range(n)]

OUT_LOAN  = take(4)   # ① 여신 초고액 이상치
OUT_CARD  = take(4)   # ① 카드 초고액 이상치
SURGE     = take(5)   # ② 여신 급증
DROP      = take(5)   # ② 수신 급감
STRUCT    = take(5)   # ③ 여신↑ & 수신↓ 동시
TREND_UP  = take(5)   # ④ 여신 지속 증가
TREND_DN  = take(5)   # ④ 수신 지속 감소

# overrides[파일][주차] = {고객ID: 값}
overrides = {"여신잔액.csv": {}, "수신잔액.csv": {}, "카드사용금액.csv": {}}
def put(fname, week, cid, val):
    overrides[fname].setdefault(week, {})[cid] = int(val)

# ① 통계 이상치: 최근 9~12주 고수준 유지
for c in OUT_LOAN:
    for w in [9, 10, 11, 12]:
        put("여신잔액.csv", w, c, rng.integers(750, 960) * 1_000_000)
for c in OUT_CARD:
    for w in [9, 10, 11, 12]:
        put("카드사용금액.csv", w, c, rng.integers(25, 42) * 1_000_000)

# ② 급변: 직전주 대비 큰 폭 변동
for c in SURGE:
    put("여신잔액.csv", PREV, c, 90_000_000)
    put("여신잔액.csv", LATEST, c, 150_000_000)   # +66%
for c in DROP:
    put("수신잔액.csv", PREV, c, 85_000_000)
    put("수신잔액.csv", LATEST, c, 34_000_000)    # -60%

# ③ 구조적 신호: 여신 급증 + 수신 급감 동시
for c in STRUCT:
    put("여신잔액.csv", PREV, c, 70_000_000)
    put("여신잔액.csv", LATEST, c, 91_000_000)    # +30%
    put("수신잔액.csv", PREV, c, 60_000_000)
    put("수신잔액.csv", LATEST, c, 39_000_000)    # -35%

# ④ 지속 추세: 12주 단조 증가/감소
for c in TREND_UP:
    for i, w in enumerate(weeks):
        put("여신잔액.csv", w, c, 30_000_000 * (1.06 ** i))
for c in TREND_DN:
    for i, w in enumerate(weeks):
        put("수신잔액.csv", w, c, 90_000_000 * (0.95 ** i))

# ------------------------------------------------------------- 적용
amount_col = {"여신잔액.csv": "여신잔액", "수신잔액.csv": "수신잔액", "카드사용금액.csv": "사용금액"}
touched = 0
for fname, byweek in overrides.items():
    col = amount_col[fname]
    for w, mapping in byweek.items():
        fp = f"{DATA}/{folder[w]}/{fname}"
        df = pd.read_csv(fp)
        mask = df["고객ID"].isin(mapping)
        df.loc[mask, col] = df.loc[mask, "고객ID"].map(mapping).astype(int)
        assert not df[col].isna().any() and (df[col] >= 0).all() and len(df) == 500
        df.to_csv(fp, index=False, encoding="utf-8-sig")
        touched += int(mask.sum())

print(f"주입 완료: 지정 고객 {len(picked)}명, 수정 셀 {touched}개")
print("유형별 고객:")
for nm, lst in [("여신이상치", OUT_LOAN), ("카드이상치", OUT_CARD), ("여신급증", SURGE),
                ("수신급감", DROP), ("구조신호", STRUCT), ("여신지속증가", TREND_UP),
                ("수신지속감소", TREND_DN)]:
    print(f"  {nm}: {lst}")
