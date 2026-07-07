#!/usr/bin/env python3
"""Build the Kakao loan-review report payload from repository collectors."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import dart_client
import news_search
import stock


COMPANY = "카카오"
YEAR = 2025
STOCK_CODE = "035720"
OUT = Path(__file__).resolve().parent.parent / "meta" / "payload_kakao.json"


def _date(s: str | None) -> str | None:
    if not s:
        return s
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _pick_articles(articles: list[dict]) -> list[dict]:
    selected = []
    seen_titles = set()
    for article in articles:
        title = article.get("title", "")
        if "파업" not in title:
            continue
        if title in seen_titles:
            continue
        seen_titles.add(title)
        selected.append(
            {
                "category": "경영/사업",
                "tone": "warn",
                "title": title,
                "source": article.get("source"),
                "date": article.get("date"),
                "summary": "성과급 갈등과 노사 교섭 결렬로 창사 첫 파업 또는 부분파업이 보도되어 단기 운영 안정성과 평판 측면의 모니터링이 필요합니다.",
            }
        )
        if len(selected) >= 4:
            break
    return selected


def main() -> None:
    key = dart_client.get_api_key()
    dart = dart_client.collect(COMPANY, YEAR, key)
    news = news_search.collect(COMPANY, 180, 30, False)
    stk = stock.collect(STOCK_CODE, 365, True)

    info = dart["기본정보"]
    fin = dart["재무정보"]
    ratios = fin.get("재무비율", {})
    accounts = fin.get("주요계정(원)", {})
    metrics = stk.get("지표", {})
    risks = _pick_articles(news.get("articles", []))

    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "company": {
            "name": info.get("회사명"),
            "ceo": info.get("대표자"),
            "industry": info.get("업종코드"),
            "established": _date(info.get("설립일")),
            "listing": "상장" if info.get("상장주식코드") else "비상장",
            "biz_no": info.get("사업자번호"),
            "address": info.get("주소"),
        },
        "financial": {
            "fiscal_year": fin.get("조회연도"),
            "fs_type": fin.get("재무제표구분"),
            "ratios": ratios,
            "accounts": accounts,
            "assessment": {"level": "양호", "tone": "good"},
            "comment": (
                "2025년 연결 기준 부채비율 82.49%, 유동비율 140.94%, 자기자본비율 54.8%로 "
                "여신 관점의 기본 재무 안정성은 양호합니다. 영업이익률 9.04%, 순이익률 6.4%로 "
                "수익성도 흑자를 유지하고 있어 재무 자체의 즉시 경고 신호는 제한적입니다."
            ),
        },
        "reputation": {
            "period": news.get("기간"),
            "reviewed_count": news.get("수집_기사수"),
            "risks": risks,
            "assessment": {"level": "보통", "tone": "warn"},
            "comment": (
                "최근 180일 수집 기사 30건 중 리스크 태깅 기사는 15건이며, 핵심 신호는 파업 및 "
                "노사 갈등 관련 반복 보도입니다. 부도, 회생, 횡령, 분식회계 등 중대 신용 사건은 "
                "수집 기사에서 확인되지 않았지만, 핵심 서비스 운영과 대외 평판에 대한 단기 모니터링이 필요합니다."
            ),
        },
        "stock": {
            "code": stk.get("종목코드"),
            "period": stk.get("조회기간"),
            "metrics": metrics,
            "series": stk.get("종가시계열", []),
            "assessment": {"level": "보통", "tone": "warn"},
            "comment": (
                "최근 365일 기준 기간등락률 -41.1%, 최고점 대비 낙폭 -47.71%, 연율화변동성 48.51%로 "
                "시장 평가는 약세와 높은 불확실성을 반영합니다. 다만 주가 지표는 시장 전반 요인의 영향을 "
                "받을 수 있어 신용판단의 보조 참고지표로 해석해야 합니다."
            ),
        },
        "overall": {
            "level": "보통",
            "tone": "warn",
            "summary": (
                "카카오는 2025년 연결 재무 기준 레버리지와 단기 지급능력이 안정권이고 흑자 수익성을 보입니다. "
                "반면 최근 노사 갈등 및 창사 첫 파업 관련 보도가 반복되고, 최근 1년 주가가 큰 폭으로 하락해 "
                "평판 및 시장 신뢰 측면의 주의 신호가 있습니다. 공개 데이터 기준으로 즉각적인 고위험 재무 신호는 "
                "제한적이나, 여신 검토 시 노사 이슈의 지속 여부와 주가 약세의 원인, 최신 실적 흐름을 함께 확인하는 것이 적절합니다."
            ),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
