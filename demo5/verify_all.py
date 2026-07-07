"""12주 전체 교차검증: 스키마 통일 / 고객ID 일관성 / 값 무결성 / 추세 연속성."""
import pandas as pd, glob, os, sys

cal = pd.read_csv("data/calendar.csv")
cust = pd.read_csv("data/customers.csv")
master_ids = list(cust["고객ID"])

FILES = {
    "여신잔액.csv":     ["고객ID","기준일자","주차","상품구분","여신잔액"],
    "수신잔액.csv":     ["고객ID","기준일자","주차","상품구분","수신잔액"],
    "카드사용금액.csv": ["고객ID","기준일자","주차","카드구분","사용금액","사용건수"],
    "보험잔액.csv":     ["고객ID","기준일자","주차","보험잔액" if False else "상품구분","보험잔액"],
}
errors, weeks_seen = [], []

for _, row in cal.iterrows():
    w, base_date, folder = int(row["주차"]), row["기준일자"], row["폴더명"]
    wdir = f"data/{folder}"
    if not os.path.isdir(wdir):
        errors.append(f"[{folder}] 폴더 없음"); continue
    weeks_seen.append(w)
    for fname, cols in FILES.items():
        fp = f"{wdir}/{fname}"
        if not os.path.exists(fp):
            errors.append(f"[{folder}/{fname}] 파일 없음"); continue
        df = pd.read_csv(fp)
        if list(df.columns) != cols:
            errors.append(f"[{folder}/{fname}] 컬럼 불일치: {list(df.columns)}")
        if len(df) != 500:
            errors.append(f"[{folder}/{fname}] 행수 {len(df)} != 500")
        if list(df["고객ID"]) != master_ids:
            errors.append(f"[{folder}/{fname}] 고객ID 집합/순서 불일치")
        if df.isna().any().any():
            errors.append(f"[{folder}/{fname}] NaN 존재")
        if (df["주차"] != w).any():
            errors.append(f"[{folder}/{fname}] 주차 값 오류")
        if (df["기준일자"] != base_date).any():
            errors.append(f"[{folder}/{fname}] 기준일자 {base_date} 불일치")
        amt = cols[-1] if fname != "카드사용금액.csv" else "사용금액"
        if df[amt].min() < 0:
            errors.append(f"[{folder}/{fname}] 음수 금액")

# 추세 연속성: 전체 여신/수신/카드/보험 총합을 주차별로
def total(metric, col):
    s = {}
    for _, row in cal.iterrows():
        fp = f"data/{row['폴더명']}/{metric}"
        s[int(row['주차'])] = int(pd.read_csv(fp)[col].sum())
    return s

print("=== 교차검증 ===")
print(f"주차 폴더: {sorted(weeks_seen)}  (기대 1~12)")
print(f"고객 수(master): {len(master_ids)}")
print("\n주차별 전체 합계(원):")
tot = {
    "여신잔액": total("여신잔액.csv","여신잔액"),
    "수신잔액": total("수신잔액.csv","수신잔액"),
    "카드사용금액": total("카드사용금액.csv","사용금액"),
    "보험잔액": total("보험잔액.csv","보험잔액"),
}
hdr = "주차 " + " ".join(f"{k:>16}" for k in tot)
print(hdr)
for w in range(1,13):
    print(f"{w:>3}  " + " ".join(f"{tot[k][w]:>16,}" for k in tot))

print("\n오류:", len(errors))
for e in errors[:50]:
    print("  -", e)
print("\n결과:", "✅ ALL PASS" if not errors else "❌ FAIL")
sys.exit(1 if errors else 0)
