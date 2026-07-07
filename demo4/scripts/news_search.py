#!/usr/bin/env python3
"""뉴스/평판 리스크 수집 (3순위) - Google News RSS 기반.

대출심사 정보 파악 시스템의 '평판 리스크' sub-agent가 사용하는 결정적 수집 스크립트.
기업명으로 최근 뉴스를 가져오고, 여신(대출) 관점의 리스크 키워드로 1차 태깅한다.
어떤 기사가 실제로 유의미한지 최종 선별은 sub-agent(LLM)가 원문을 보고 판단한다.

사용법:
    python news_search.py "삼성전자"
    python news_search.py "삼성전자" --days 30 --limit 30 --pretty
    python news_search.py "삼성전자" --risk-only        # 리스크 태깅된 기사만

API 키가 필요 없다 (Google News RSS 공개 피드).
※ 더 정밀한 한국어 뉴스가 필요하면 Naver 뉴스 검색 API로 교체 가능 (Client ID/Secret 필요).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

import requests

GNEWS_RSS = "https://news.google.com/rss/search"

# 대출심사(여신) 관점의 리스크 키워드. 카테고리별로 묶어 어떤 종류의 위험인지 태깅한다.
RISK_KEYWORDS = {
    "법적/규제": [
        "소송", "고발", "기소", "구속", "압수수색", "검찰", "제재", "과징금",
        "영업정지", "행정처분", "담합", "벌금", "수사",
    ],
    "재무/신용": [
        "부도", "파산", "법정관리", "워크아웃", "자본잠식", "상장폐지",
        "감사의견", "거절", "연체", "채무불이행", "유동성 위기", "디폴트",
        "회생절차", "부실",
    ],
    "경영/사업": [
        "횡령", "배임", "분식회계", "리콜", "결함", "파업", "구조조정",
        "정리해고", "매각", "실적 악화", "적자 전환", "영업손실", "감원",
    ],
    "오너/평판": [
        "갑질", "오너 리스크", "논란", "비리", "탈세", "성추문", "먹튀",
    ],
}


# 단순 부분문자열 매칭 시 오탐이 나는 키워드는 정규식 경계로 보정한다.
#   '부도' ← '부도덕' 오탐,  '감원' ← '금감원' 오탐
KEYWORD_PATTERN_OVERRIDES = {
    "부도": r"부도(?!덕)",
    "감원": r"(?<!금)감원",
}


def _compile_keywords():
    compiled: dict[str, list[tuple[str, re.Pattern]]] = {}
    for category, kws in RISK_KEYWORDS.items():
        compiled[category] = [
            (kw, re.compile(KEYWORD_PATTERN_OVERRIDES.get(kw, re.escape(kw))))
            for kw in kws
        ]
    return compiled


_COMPILED_KEYWORDS = _compile_keywords()


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return html.unescape(text).strip()


def _tag_risk(text: str) -> dict:
    """제목+요약에서 리스크 키워드를 찾아 카테고리와 매칭 키워드를 반환."""
    hits: dict[str, list[str]] = {}
    for category, patterns in _COMPILED_KEYWORDS.items():
        matched = [kw for kw, pat in patterns if pat.search(text)]
        if matched:
            hits[category] = matched
    score = sum(len(v) for v in hits.values())
    return {"categories": list(hits.keys()), "matched_keywords": hits, "score": score}


def _parse_item(item: ET.Element) -> dict:
    title_raw = item.findtext("title") or ""
    # Google News 제목은 "헤드라인 - 언론사" 형태
    if " - " in title_raw:
        headline, source_from_title = title_raw.rsplit(" - ", 1)
    else:
        headline, source_from_title = title_raw, ""
    source = item.findtext("source") or source_from_title
    desc = _strip_html(item.findtext("description") or "")

    pub_raw = item.findtext("pubDate") or ""
    try:
        pub_dt = parsedate_to_datetime(pub_raw)
        pub_iso = pub_dt.astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError):
        pub_iso = pub_raw

    text_for_risk = f"{headline} {desc}"
    risk = _tag_risk(text_for_risk)
    return {
        "title": headline.strip(),
        "source": source.strip(),
        "date": pub_iso,
        "link": item.findtext("link"),
        "summary": desc,
        "risk_categories": risk["categories"],
        "risk_keywords": risk["matched_keywords"],
        "risk_score": risk["score"],
    }


def search_news(name: str, days: int | None, limit: int) -> list[dict]:
    query = name
    if days:
        query = f"{name} when:{days}d"  # Google News 최근 N일 필터
    params = {
        "q": query,
        "hl": "ko",       # 언어
        "gl": "KR",       # 지역
        "ceid": "KR:ko",  # 국가:언어
    }
    url = f"{GNEWS_RSS}?{urllib.parse.urlencode(params)}"
    resp = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = root.findall(".//item")
    articles = [_parse_item(it) for it in items[:limit]]
    return articles


def collect(name: str, days: int | None, limit: int, risk_only: bool) -> dict:
    articles = search_news(name, days, limit)
    flagged = [a for a in articles if a["risk_score"] > 0]
    # 리스크 점수 높은 순으로 정렬
    flagged.sort(key=lambda a: a["risk_score"], reverse=True)
    out_articles = flagged if risk_only else articles
    return {
        "query": name,
        "기간": f"최근 {days}일" if days else "전체(최신순)",
        "수집_기사수": len(articles),
        "리스크_태깅_기사수": len(flagged),
        "리스크_카테고리_요약": sorted(
            {c for a in flagged for c in a["risk_categories"]}
        ),
        "articles": out_articles,
    }


def main():
    p = argparse.ArgumentParser(description="기업 뉴스/평판 리스크 수집 (Google News)")
    p.add_argument("company", help="기업명 (예: 삼성전자)")
    p.add_argument("--days", type=int, default=None, help="최근 N일 이내 (미지정 시 전체)")
    p.add_argument("--limit", type=int, default=20, help="최대 기사 수 (기본 20)")
    p.add_argument("--risk-only", action="store_true", help="리스크 태깅된 기사만 출력")
    p.add_argument("--pretty", action="store_true", help="들여쓰기 출력")
    args = p.parse_args()

    try:
        result = collect(args.company, args.days, args.limit, args.risk_only)
    except requests.RequestException as e:
        result = {"query": args.company, "error": str(e)}

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
