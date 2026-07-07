"""
공유 기반(foundation) 생성 스크립트.
- customers.csv        : 고객 마스터 (12주 내내 고정, 모든 sub-agent가 동일하게 사용)
- customers_base.csv   : 주차별 값 계산에 쓰는 내부 파라미터 (최종 파일에는 노출 안 함)
- calendar.csv         : 12주차 달력 (주차번호 -> 기준일자, 폴더명)
이 스크립트는 '한 번만' 내가 실행한다. sub-agent는 이 결과를 읽기만 한다.
"""
import numpy as np
import pandas as pd
from datetime import date, timedelta

RNG = np.random.default_rng(20260707)  # 기반은 완전 재현 가능하게 고정 시드
N = 500                                 # 고객 수 (12주 내내 고정)
N_WEEKS = 12

# ---------------------------------------------------------------- 고객 마스터
surnames = list("김이박최정강조윤장임한오서신권황안송류전홍")
given = ["민준","서연","도윤","하은","시우","지우","예준","수아","주원","지호",
         "지훈","서준","하준","은우","유진","지원","현우","다은","건우","채원"]
branches = [
    ("0011","을지로본점"), ("0025","여의도영업부"), ("0102","강남중앙"),
    ("0210","분당서현"), ("0333","판교테크노"), ("0450","송도국제"),
    ("0512","부산서면"), ("0678","대전둔산"),
]

cust_ids = [f"C{100001+i}" for i in range(N)]
names = [RNG.choice(surnames) + RNG.choice(given) for _ in range(N)]
ages = RNG.integers(20, 71, N)
genders = RNG.choice(["M", "F"], N)
br_idx = RNG.integers(0, len(branches), N)
join_start = date(2015, 1, 1)
join_dates = [ (join_start + timedelta(days=int(RNG.integers(0, 3800)))).isoformat() for _ in range(N) ]

customers = pd.DataFrame({
    "고객ID": cust_ids,
    "고객명": names,
    "연령": ages,
    "성별": genders,
    "지점코드": [branches[i][0] for i in br_idx],
    "지점명": [branches[i][1] for i in br_idx],
    "가입일자": join_dates,
})
customers.to_csv("data/customers.csv", index=False, encoding="utf-8-sig")

# ---------------------------------------------------------------- 내부 파라미터
def base_col(zero_ratio, lo, hi):
    """일부 고객은 0(해당 상품 미보유), 나머지는 [lo,hi] 만원 단위 기반값."""
    vals = RNG.integers(lo, hi, N).astype(float) * 10000
    has = RNG.random(N) > zero_ratio
    return np.where(has, vals, 0.0)

def drift_col(lo, hi):
    return np.round(RNG.uniform(lo, hi, N), 4)  # 주당 증감률

base = pd.DataFrame({
    "고객ID": cust_ids,
    "여신_상품구분": RNG.choice(["신용대출","주택담보대출","전세자금대출"], N),
    "여신_base":  base_col(0.35, 500, 12000),   # 500만~1.2억
    "여신_drift": drift_col(-0.010, 0.015),
    "수신_상품구분": RNG.choice(["입출금예금","정기예금","적금"], N),
    "수신_base":  base_col(0.05, 50, 8000),      # 50만~8천만
    "수신_drift": drift_col(-0.008, 0.012),
    "카드_구분": RNG.choice(["신용카드","체크카드"], N),
    "카드_base":  base_col(0.15, 5, 400),        # 주간 5만~400만
    "카드_drift": drift_col(-0.020, 0.020),
    "보험_상품구분": RNG.choice(["보장성보험","저축성보험"], N),
    "보험_base":  base_col(0.40, 100, 5000),     # 100만~5천만
    "보험_drift": drift_col(-0.005, 0.010),
})
base.to_csv("data/customers_base.csv", index=False, encoding="utf-8-sig")

# ---------------------------------------------------------------- 달력 (12주)
# 기준일: 매주 금요일 스냅샷. W12가 2026-07-03(금)이 되도록 역산.
last_friday = date(2026, 7, 3)
rows = []
for w in range(1, N_WEEKS + 1):
    d = last_friday - timedelta(weeks=(N_WEEKS - w))
    rows.append({"주차": w, "기준일자": d.isoformat(), "폴더명": f"week_{w:02d}"})
calendar = pd.DataFrame(rows)
calendar.to_csv("data/calendar.csv", index=False, encoding="utf-8-sig")

print("customers:", customers.shape, "| base:", base.shape, "| weeks:", len(calendar))
print(calendar.to_string(index=False))
