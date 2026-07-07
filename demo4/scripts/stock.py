#!/usr/bin/env python3
"""주가 추이 수집 (4순위, 상장사) - 네이버 금융 시세 기반.

대출심사 정보 파악 시스템의 '주가' sub-agent가 사용하는 결정적 수집 스크립트.
종목코드(또는 기업명)로 일별 시세를 가져와 기간 등락률·변동성·낙폭 등
대출심사에 참고할 주가 지표를 계산해 JSON으로 출력한다.

사용법:
    python stock.py 005930                 # 종목코드 직접 (키 불필요)
    python stock.py 005930 --days 180 --pretty
    python stock.py "삼성전자"             # 기업명 → DART로 코드 해소 (DART_API_KEY 필요)

API 키가 필요 없다 (네이버 금융 공개 시세). 단, 기업명 입력 시에만 DART 키 사용.
주가 급락·고변동성은 신용위험 신호일 수 있으나, 시장 전반 영향도 섞이므로 해석은 sub-agent 몫.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from datetime import datetime, timedelta

import requests

SISE_URL = "https://api.finance.naver.com/siseJson.naver"
TRADING_DAYS_PER_YEAR = 252  # 변동성 연율화용


def _fetch_ohlcv(code: str, days: int) -> list[dict]:
    end = datetime.now()
    start = end - timedelta(days=days)
    params = {
        "symbol": code,
        "requestType": 1,
        "startTime": start.strftime("%Y%m%d"),
        "endTime": end.strftime("%Y%m%d"),
        "timeframe": "day",
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
    resp = requests.get(SISE_URL, params=params, headers=headers, timeout=20)
    resp.raise_for_status()

    # 응답은 JS 배열 스타일(따옴표 혼용 + 공백/탭). 정리 후 JSON으로 파싱.
    text = resp.text.strip().replace("'", '"')
    rows = json.loads(text)
    if not rows or len(rows) < 2:
        return []
    header = rows[0]  # ['날짜','시가','고가','저가','종가','거래량','외국인소진율']
    out = []
    for r in rows[1:]:
        rec = dict(zip(header, r))
        out.append(
            {
                "date": str(rec.get("날짜")),
                "open": rec.get("시가"),
                "high": rec.get("고가"),
                "low": rec.get("저가"),
                "close": rec.get("종가"),
                "volume": rec.get("거래량"),
                "foreign_ratio": rec.get("외국인소진율"),
            }
        )
    return out


def _resolve_code(company: str) -> tuple[str, str]:
    """기업명 → (종목코드, 회사명). DART find_corp 재사용 (DART_API_KEY 필요)."""
    try:
        from dart_client import find_corp, get_api_key
    except ImportError:
        sys.exit("기업명 해소를 위해 dart_client.py가 같은 폴더에 있어야 합니다.")
    key = get_api_key()
    candidates = find_corp(company, key)
    listed = [c for c in candidates if c["listed"]]
    if not listed:
        sys.exit(f"'{company}' 의 상장 종목코드를 찾지 못했습니다 (비상장이거나 미상장).")
    return listed[0]["stock_code"], listed[0]["corp_name"]


def _pct(numer, denom):
    if denom in (None, 0):
        return None
    return round(numer / denom * 100, 2)


def analyze(series: list[dict]) -> dict:
    closes = [s["close"] for s in series if isinstance(s["close"], (int, float))]
    if len(closes) < 2:
        return {"상태": "시세 데이터 부족"}

    first, last = closes[0], closes[-1]
    high, low = max(closes), min(closes)

    # 일간 로그수익률 → 연율화 변동성(%)
    returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1]
    ]
    daily_vol = statistics.pstdev(returns) if len(returns) > 1 else 0.0
    annual_vol = round(daily_vol * math.sqrt(TRADING_DAYS_PER_YEAR) * 100, 2)

    volumes = [s["volume"] for s in series if isinstance(s["volume"], (int, float))]

    return {
        "거래일수": len(closes),
        "최근종가": last,
        "기간시작가": first,
        "기간등락률(%)": _pct(last - first, first),
        "기간최고가": high,
        "기간최저가": low,
        "최고점대비낙폭(%)": _pct(last - high, high),  # 음수일수록 고점 대비 하락
        "연율화변동성(%)": annual_vol,
        "평균거래량": round(statistics.mean(volumes)) if volumes else None,
        "최근외국인소진율(%)": series[-1].get("foreign_ratio"),
    }


def collect(arg: str, days: int, with_series: bool) -> dict:
    if re.fullmatch(r"\d{6}", arg):
        code, name = arg, None
    else:
        code, name = _resolve_code(arg)

    series = _fetch_ohlcv(code, days)
    if not series:
        return {"종목코드": code, "회사명": name, "error": "시세 조회 결과 없음"}

    result = {
        "종목코드": code,
        "회사명": name,
        "조회기간": f"최근 {days}일",
        "지표": analyze(series),
    }
    if with_series:
        # 웹 리포트 차트용 일별 종가 시계열 (날짜, 종가)
        result["종가시계열"] = [
            {"date": s["date"], "close": s["close"]} for s in series
        ]
    return result


def main():
    p = argparse.ArgumentParser(description="상장사 주가 추이 수집 (네이버 금융)")
    p.add_argument("target", help="6자리 종목코드 (예: 005930) 또는 기업명")
    p.add_argument("--days", type=int, default=365, help="조회 기간(일), 기본 365")
    p.add_argument("--series", action="store_true", help="일별 종가 시계열 포함(차트용)")
    p.add_argument("--pretty", action="store_true", help="들여쓰기 출력")
    args = p.parse_args()

    try:
        result = collect(args.target, args.days, args.series)
    except (requests.RequestException, ValueError) as e:
        result = {"target": args.target, "error": str(e)}

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
