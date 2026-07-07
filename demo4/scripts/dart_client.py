#!/usr/bin/env python3
"""DART Open API 클라이언트 - 기업 기본정보(2순위) + 재무정보(1순위) 수집.

대출심사 정보 파악 시스템의 '재무/기본정보' sub-agent가 사용하는 결정적(deterministic)
데이터 수집 스크립트. 기업명을 입력하면 DART에서 기업개황과 재무제표를 가져와
주요 재무비율을 계산해 JSON으로 출력한다.

사용법:
    python dart_client.py "삼성전자"
    python dart_client.py "삼성전자" --year 2023 --pretty

DART 인증키는 이 파일에 하드코딩돼 있다(DEFAULT_API_KEY). 데모 편의를 위한 것으로,
환경변수 DART_API_KEY 를 설정하면 그 값이 우선한다.
※ 실제 운영/공개 배포 시에는 키를 코드에서 분리할 것.
API 키 발급: https://opendart.fss.or.kr/  (무료, 회원가입 후 인증키 신청)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import requests

BASE_URL = "https://opendart.fss.or.kr/api"
CACHE_DIR = Path(__file__).parent / ".cache"
CORPCODE_CACHE = CACHE_DIR / "CORPCODE.xml"

# 데모용 하드코딩 DART 인증키.
# ⚠️ 실제 운영/공개 배포 시에는 환경변수나 시크릿 매니저로 분리할 것.
#    (환경변수 DART_API_KEY 가 설정돼 있으면 그 값을 우선 사용한다.)
DEFAULT_API_KEY = "de5585dc0c3dc5eaf00ce8d5e1d4f422d03bae44"

# DART 표준 계정 태그(account_id) → 우리가 쓰는 이름.
# 값이 없을 때를 대비해 account_nm(계정명) 키워드로도 보조 매칭한다.
BS_ACCOUNTS = {
    "자산총계": ("ifrs-full_Assets", ["자산총계"]),
    "부채총계": ("ifrs-full_Liabilities", ["부채총계"]),
    "자본총계": ("ifrs-full_Equity", ["자본총계"]),
    "유동자산": ("ifrs-full_CurrentAssets", ["유동자산"]),
    "유동부채": ("ifrs-full_CurrentLiabilities", ["유동부채"]),
}
IS_ACCOUNTS = {
    "매출액": ("ifrs-full_Revenue", ["매출액", "수익(매출액)", "영업수익"]),
    "영업이익": ("dart_OperatingIncomeLoss", ["영업이익"]),
    "당기순이익": ("ifrs-full_ProfitLoss", ["당기순이익"]),
}


class DartError(Exception):
    """DART API가 정상(000) 이외 상태코드를 반환했을 때."""


def get_api_key() -> str:
    # 환경변수가 있으면 우선, 없으면 하드코딩된 데모 키로 폴백.
    return os.environ.get("DART_API_KEY") or DEFAULT_API_KEY


def _get(endpoint: str, params: dict, key: str) -> dict:
    """JSON 엔드포인트 호출 + 상태코드 검사."""
    params = {"crtfc_key": key, **params}
    resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    status = data.get("status")
    if status != "000":
        raise DartError(f"[{status}] {data.get('message', '알 수 없는 오류')}")
    return data


# ---------------------------------------------------------------------------
# 1. 기업명 → 고유번호(corp_code) 해소
# ---------------------------------------------------------------------------
def _ensure_corpcode(key: str) -> Path:
    """전체 기업 고유번호 목록(CORPCODE.xml)을 내려받아 캐시. 이미 있으면 재사용."""
    if CORPCODE_CACHE.exists():
        return CORPCODE_CACHE
    CACHE_DIR.mkdir(exist_ok=True)
    resp = requests.get(
        f"{BASE_URL}/corpCode.xml", params={"crtfc_key": key}, timeout=60
    )
    resp.raise_for_status()
    # 실패 시 XML 에러 메시지가 zip 대신 올 수 있음
    if resp.content[:2] != b"PK":
        try:
            msg = ET.fromstring(resp.content).findtext("message")
        except ET.ParseError:
            msg = resp.text[:200]
        raise DartError(f"고유번호 목록 조회 실패: {msg}")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        with zf.open(zf.namelist()[0]) as f:
            CORPCODE_CACHE.write_bytes(f.read())
    return CORPCODE_CACHE


def find_corp(name: str, key: str) -> list[dict]:
    """기업명으로 후보 목록 반환. 상장사(stock_code 보유)를 앞에 정렬."""
    path = _ensure_corpcode(key)
    root = ET.parse(path).getroot()
    exact, partial = [], []
    for item in root.iter("list"):
        corp_name = (item.findtext("corp_name") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        entry = {
            "corp_code": (item.findtext("corp_code") or "").strip(),
            "corp_name": corp_name,
            "stock_code": stock_code or None,
            "listed": bool(stock_code),
        }
        if corp_name == name:
            exact.append(entry)
        elif name in corp_name:
            partial.append(entry)
    # 정확 일치 우선, 그 안에서 상장사 우선
    ordered = sorted(exact, key=lambda e: not e["listed"])
    if ordered:
        return ordered
    return sorted(partial, key=lambda e: not e["listed"])[:10]


# ---------------------------------------------------------------------------
# 2. 기업개황 (2순위: 기본정보)
# ---------------------------------------------------------------------------
def get_company(corp_code: str, key: str) -> dict:
    d = _get("company.json", {"corp_code": corp_code}, key)
    return {
        "회사명": d.get("corp_name"),
        "영문명": d.get("corp_name_eng"),
        "대표자": d.get("ceo_nm"),
        "법인구분": d.get("corp_cls"),  # Y:유가 K:코스닥 N:코넥스 E:기타
        "사업자번호": d.get("bizr_no"),
        "법인등록번호": d.get("jurir_no"),
        "주소": d.get("adres"),
        "홈페이지": d.get("hm_url"),
        "업종코드": d.get("induty_code"),
        "설립일": d.get("est_dt"),
        "결산월": d.get("acc_mt"),
        "상장주식코드": d.get("stock_code") or None,
    }


# ---------------------------------------------------------------------------
# 3. 재무제표 (1순위: 재무정보) + 비율 계산
# ---------------------------------------------------------------------------
def _pick(items: list[dict], account_id: str, name_kws: list[str], sj_div: str):
    """재무제표 항목 목록에서 특정 계정의 당기금액을 숫자로 추출."""
    # 1차: account_id 정확 매칭
    for it in items:
        if it.get("sj_div") == sj_div and it.get("account_id") == account_id:
            return _to_num(it.get("thstrm_amount"))
    # 2차: 계정명 키워드 매칭
    for it in items:
        if it.get("sj_div") != sj_div:
            continue
        nm = (it.get("account_nm") or "").replace(" ", "")
        if any(kw.replace(" ", "") in nm for kw in name_kws):
            return _to_num(it.get("thstrm_amount"))
    return None


def _to_num(s):
    if s in (None, "", "-"):
        return None
    try:
        return int(str(s).replace(",", ""))
    except ValueError:
        return None


def _ratio(numer, denom, pct=True):
    if numer is None or denom in (None, 0):
        return None
    r = numer / denom
    return round(r * 100, 2) if pct else round(r, 2)


def get_financials(corp_code: str, key: str, year: int) -> dict:
    """단일회사 전체 재무제표(사업보고서 기준). 연결(CFS) 우선, 없으면 개별(OFS)."""
    reprt_code = "11011"  # 사업보고서(연간)
    items, fs_div_used = None, None
    for fs_div in ("CFS", "OFS"):
        try:
            d = _get(
                "fnlttSinglAcntAll.json",
                {
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": reprt_code,
                    "fs_div": fs_div,
                },
                key,
            )
            items = d.get("list", [])
            fs_div_used = "연결" if fs_div == "CFS" else "개별"
            break
        except DartError:
            continue
    if not items:
        return {"조회연도": year, "상태": "재무제표 없음(비상장/미제출 가능)"}

    vals = {k: _pick(items, aid, kws, "BS") for k, (aid, kws) in BS_ACCOUNTS.items()}
    vals.update(
        {k: _pick(items, aid, kws, "IS") for k, (aid, kws) in IS_ACCOUNTS.items()}
    )
    # 손익계산서가 IS가 아닌 CIS(포괄손익)로만 올 때 보조 조회
    for k, (aid, kws) in IS_ACCOUNTS.items():
        if vals[k] is None:
            vals[k] = _pick(items, aid, kws, "CIS")

    ratios = {
        "부채비율(%)": _ratio(vals["부채총계"], vals["자본총계"]),
        "유동비율(%)": _ratio(vals["유동자산"], vals["유동부채"]),
        "자기자본비율(%)": _ratio(vals["자본총계"], vals["자산총계"]),
        "영업이익률(%)": _ratio(vals["영업이익"], vals["매출액"]),
        "순이익률(%)": _ratio(vals["당기순이익"], vals["매출액"]),
    }
    return {
        "조회연도": year,
        "재무제표구분": fs_div_used,
        "주요계정(원)": vals,
        "재무비율": ratios,
    }


# ---------------------------------------------------------------------------
# 오케스트레이션
# ---------------------------------------------------------------------------
def collect(name: str, year: int, key: str) -> dict:
    candidates = find_corp(name, key)
    if not candidates:
        return {"query": name, "error": f"'{name}' 에 해당하는 기업을 찾지 못했습니다."}
    target = candidates[0]
    result = {
        "query": name,
        "선택된_기업": target,
        "기본정보": get_company(target["corp_code"], key),
        "재무정보": get_financials(target["corp_code"], key, year),
    }
    if len(candidates) > 1:
        result["기타_후보"] = candidates[1:6]
    return result


def main():
    p = argparse.ArgumentParser(description="DART 기업 기본정보 + 재무정보 수집")
    p.add_argument("company", help="기업명 (예: 삼성전자)")
    p.add_argument("--year", type=int, default=2023, help="재무제표 사업연도 (기본 2023)")
    p.add_argument("--pretty", action="store_true", help="사람이 읽기 좋게 들여쓰기 출력")
    args = p.parse_args()

    key = get_api_key()
    try:
        result = collect(args.company, args.year, key)
    except (DartError, requests.RequestException) as e:
        result = {"query": args.company, "error": str(e)}

    print(
        json.dumps(
            result, ensure_ascii=False, indent=2 if args.pretty else None
        )
    )


if __name__ == "__main__":
    main()
