#!/usr/bin/env python3
"""Build the Day1 Company loan-review report payload from collectors."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import dart_client
import news_search
import stock


COMPANY = "데이원컴퍼니"
YEAR = 2025
STOCK_CODE = "373160"
OUT = Path(__file__).resolve().parent.parent / "meta" / "payload_dayone.json"


def _date(s: str | None) -> str | None:
    if not s:
        return s
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _pick_article(articles: list[dict], title_part: str) -> dict | None:
    for article in articles:
        if title_part in article.get("title", ""):
            return article
    return None


def _risk(article: dict, category: str, tone: str, summary: str) -> dict:
    return {
        "category": category,
        "tone": tone,
        "title": article.get("title"),
        "source": article.get("source"),
        "date": article.get("date"),
        "summary": summary,
    }


def _pick_risks(articles: list[dict]) -> list[dict]:
    picks = [
        (
            _pick_article(articles, "100만 회원"),
            "개인정보/보안",
            "bad",
            "교육 플랫폼 회원의 계좌·전화번호 유출이 보도되어 고객 신뢰, 보상·제재 가능성, B2B 거래 평판에 대한 확인이 필요합니다.",
        ),
        (
            _pick_article(articles, "카드·계좌 정보 포함"),
            "개인정보/보안",
            "bad",
            "카드·계좌 정보 포함 추가 유출 확인 보도로 사고 범위 확대 가능성이 제기되어 후속 공시·규제 대응 점검이 필요합니다.",
        ),
        (
            _pick_article(articles, "전환배치인가 퇴출인가"),
            "노무/조직문화",
            "warn",
            "전환배치·퇴출 논란 보도로 조직 안정성과 평판 리스크가 관찰됩니다.",
        ),
        (
            _pick_article(articles, "하던 업무 빼고 다시 지원하라"),
            "노무/조직문화",
            "warn",
            "인사 절차 논란 보도가 이어져 인력 운영 관련 잡음의 지속 여부를 모니터링할 필요가 있습니다.",
        ),
    ]
    return [_risk(a, c, t, s) for a, c, t, s in picks if a]


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
    risks = _pick_risks(news.get("articles", []))

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
                "2025년 연결 기준 부채비율 80.31%, 유동비율 205.95%, 자기자본비율 55.46%로 "
                "여신 관점의 재무 안정성은 양호합니다. 영업이익률 3.69%, 순이익률 3.83%로 "
                "흑자 수익성을 보이나 마진 수준은 높지 않아 향후 이익 지속성 확인이 필요합니다."
            ),
        },
        "reputation": {
            "period": news.get("기간"),
            "reviewed_count": news.get("수집_기사수"),
            "risks": risks,
            "assessment": {"level": "높음", "tone": "bad"},
            "comment": (
                "최근 180일 수집 기사 30건에서 개인정보 유출 관련 보도가 반복 확인됩니다. "
                "부도·회생·횡령·분식회계 보도는 수집 기사에서 확인되지 않았지만, 계좌·카드·주민번호 등 "
                "민감정보 유출 보도와 인사·조직문화 논란이 있어 평판 및 규제 대응 리스크는 높게 봅니다."
            ),
        },
        "stock": {
            "code": stk.get("종목코드"),
            "period": stk.get("조회기간"),
            "metrics": metrics,
            "series": stk.get("종가시계열", []),
            "assessment": {"level": "높음", "tone": "bad"},
            "comment": (
                "최근 365일 기준 기간등락률 -50.62%, 최고점 대비 낙폭 -51.6%로 시장 신뢰 약화 신호가 큽니다. "
                "연율화변동성 46.42%는 60% 초과 고변동 구간은 아니지만, 장기 하락폭이 커서 보조 참고지표상 "
                "주의가 필요합니다. 주가 하락은 시장 전반 영향도 받을 수 있어 단정하지 않아야 합니다."
            ),
        },
        "overall": {
            "level": "주의",
            "tone": "warn",
            "summary": (
                "데이원컴퍼니는 2025년 연결 재무 기준 부채비율과 유동비율이 안정권이고 흑자를 기록해 재무 자체의 "
                "즉시 경고 신호는 제한적입니다. 다만 최근 개인정보 유출 보도가 반복되고, 인사·조직문화 논란과 "
                "최근 1년 주가 급락이 함께 관찰됩니다. 공개 데이터 기준 대출 승인·거절을 단정하기보다, 사고 대응 "
                "진행 상황, 규제·보상 부담, 최신 실적과 현금흐름을 추가 확인하는 조건부 검토가 적절합니다."
            ),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(OUT)


if __name__ == "__main__":
    main()
