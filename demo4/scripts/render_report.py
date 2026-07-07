#!/usr/bin/env python3
"""대출심사 요약 리포트 HTML 렌더러.

supervisor가 sub-agent 결과를 종합해 만든 구조화 JSON을 받아, 고정 템플릿에
값만 채워 자기완결형(self-contained) HTML 리포트를 생성한다. LLM이 매번 HTML을
새로 짜지 않으므로 레이아웃이 안정적이고 재현성이 있다.

사용법:
    python render_report.py payload.json -o report.html
    cat payload.json | python render_report.py -o report.html
    python render_report.py payload.json           # stdout으로 HTML 출력

payload.json 스키마는 이 파일 하단 SAMPLE_PAYLOAD 참고.
색상은 각 섹션의 tone("good"|"warn"|"bad")으로 결정된다(라벨 문구와 분리).
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime

TONE_COLORS = {
    "good": "#1a7f4b",  # 녹색: 양호/저위험
    "warn": "#c47f17",  # 주황: 주의
    "bad": "#c0392b",   # 적색: 위험
    "none": "#6b7280",  # 회색: 해당없음/정보없음
}


def esc(x) -> str:
    return html.escape(str(x)) if x is not None else "—"


def _fmt_won(v):
    """원 단위 큰 숫자를 조/억 단위로 사람이 읽기 좋게."""
    if not isinstance(v, (int, float)):
        return "—"
    n = int(v)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_0000_0000_0000:  # 조
        return f"{sign}{n/1_0000_0000_0000:.2f}조원"
    if n >= 1_0000_0000:       # 억
        return f"{sign}{n/1_0000_0000:.1f}억원"
    return f"{sign}{n:,}원"


def _badge(tone: str, label: str) -> str:
    color = TONE_COLORS.get(tone, TONE_COLORS["none"])
    return f'<span class="badge" style="background:{color}">{esc(label)}</span>'


def _sparkline_svg(series: list[dict], width=680, height=190) -> str:
    """일별 종가 시계열을 인라인 SVG 라인차트로. 외부 의존성 없음.

    라벨은 모두 플롯 영역 밖(상/하/우 여백)에 배치해 선과 겹치지 않게 한다.
    """
    pts = [(s.get("date"), s.get("close")) for s in series
           if isinstance(s.get("close"), (int, float))]
    if len(pts) < 2:
        return '<p class="muted">차트를 그릴 시세 데이터가 부족합니다.</p>'
    closes = [c for _, c in pts]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1
    # 비대칭 여백: 오른쪽은 최근가 라벨용으로 넓게, 상/하는 최고/최저 라벨용.
    pad_l, pad_r, pad_t, pad_b = 12, 84, 26, 26
    w, h = width - pad_l - pad_r, height - pad_t - pad_b
    n = len(pts)

    def X(i):
        return pad_l + (w * i / (n - 1))

    def Y(v):
        return pad_t + h - (h * (v - lo) / span)

    line = " ".join(f"{X(i):.1f},{Y(c):.1f}" for i, c in enumerate(closes))
    area = f"{pad_l},{pad_t+h:.1f} " + line + f" {pad_l+w:.1f},{pad_t+h:.1f}"
    last = closes[-1]
    lx, ly = X(n - 1), Y(last)
    label_y = min(max(ly, pad_t + 6), pad_t + h - 2)  # 상/하단 밖으로 안 나가게 클램프
    up = last >= closes[0]
    stroke = "#1a7f4b" if up else "#c0392b"
    fill = "rgba(26,127,75,.08)" if up else "rgba(192,57,43,.08)"
    return f'''<svg viewBox="0 0 {width} {height}" class="chart" role="img">
  <polyline points="{area}" fill="{fill}" stroke="none"/>
  <polyline points="{line}" fill="none" stroke="{stroke}" stroke-width="2"/>
  <circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" fill="{stroke}"/>
  <text x="{lx+7:.1f}" y="{label_y+4:.1f}" class="lastlbl" fill="{stroke}">{last:,}</text>
  <text x="{pad_l}" y="{pad_t-10}" class="axis">최고 {hi:,}</text>
  <text x="{pad_l}" y="{height-8}" class="axis">최저 {lo:,}</text>
</svg>
  <p class="muted">{esc(pts[0][0])} ~ {esc(pts[-1][0])} · 일별 종가</p>'''


def _section_header(title: str, assessment: dict | None) -> str:
    badge = ""
    if assessment:
        badge = _badge(assessment.get("tone", "none"), assessment.get("level", ""))
    return f'<div class="sec-head"><h2>{esc(title)}</h2>{badge}</div>'


# ---------------------------------------------------------------------------
# 섹션 렌더러
# ---------------------------------------------------------------------------
def render_company(c: dict) -> str:
    rows = [
        ("대표자", c.get("ceo")), ("업종", c.get("industry")),
        ("설립일", c.get("established")), ("상장구분", c.get("listing")),
        ("사업자번호", c.get("biz_no")), ("주소", c.get("address")),
    ]
    cells = "".join(
        f'<div class="kv"><span class="k">{esc(k)}</span>'
        f'<span class="v">{esc(v)}</span></div>' for k, v in rows
    )
    return f'<section><h2>기업 개요</h2><div class="kv-grid">{cells}</div></section>'


def render_financial(f: dict) -> str:
    if not f:
        return ""
    ratios = f.get("ratios", {})
    cards = "".join(
        f'<div class="metric"><div class="mv">{esc(v)}{"%" if v is not None else ""}</div>'
        f'<div class="ml">{esc(k)}</div></div>'
        for k, v in ratios.items()
    )
    accounts = f.get("accounts", {})
    acc_rows = "".join(
        f"<tr><td>{esc(k)}</td><td class='num'>{_fmt_won(v)}</td></tr>"
        for k, v in accounts.items()
    )
    acc_tbl = (
        f'<table class="acc"><thead><tr><th>주요 계정</th><th class="num">금액</th>'
        f"</tr></thead><tbody>{acc_rows}</tbody></table>" if acc_rows else ""
    )
    meta = f"{esc(f.get('fiscal_year'))} 사업연도 · {esc(f.get('fs_type'))} 재무제표"
    comment = f'<p class="comment">{esc(f.get("comment"))}</p>' if f.get("comment") else ""
    return f'''<section>
  {_section_header("재무 건전성", f.get("assessment"))}
  <p class="muted">{meta}</p>
  <div class="metrics">{cards}</div>
  {acc_tbl}
  {comment}
</section>'''


def render_reputation(r: dict) -> str:
    if not r:
        return ""
    risks = r.get("risks", [])
    if risks:
        items = "".join(
            f'''<li class="risk">
  {_badge(x.get("tone","warn"), x.get("category","리스크"))}
  <div class="risk-body"><div class="risk-title">{esc(x.get("title"))}</div>
  <div class="muted">{esc(x.get("source"))} · {esc(x.get("date"))}</div>
  {f'<div class="risk-sum">{esc(x.get("summary"))}</div>' if x.get("summary") else ""}
  </div></li>''' for x in risks
        )
        body = f'<ul class="risks">{items}</ul>'
    else:
        body = '<p class="muted">검토 기간 내 유의미한 부정 이슈가 확인되지 않았습니다.</p>'
    meta = f"검토 기간 {esc(r.get('period'))} · 기사 {esc(r.get('reviewed_count'))}건 검토"
    comment = f'<p class="comment">{esc(r.get("comment"))}</p>' if r.get("comment") else ""
    return f'''<section>
  {_section_header("평판·뉴스 리스크", r.get("assessment"))}
  <p class="muted">{meta}</p>
  {body}{comment}
</section>'''


def render_stock(s: dict) -> str:
    if not s:
        return ""
    if s.get("not_listed"):
        return ('<section><h2>주가 동향</h2>'
                '<p class="muted">비상장 기업으로 주가 데이터가 없습니다.</p></section>')
    m = s.get("metrics", {})
    cards = "".join(
        f'<div class="metric"><div class="mv">{esc(v)}</div>'
        f'<div class="ml">{esc(k)}</div></div>' for k, v in m.items()
    )
    chart = _sparkline_svg(s.get("series", []))
    comment = f'<p class="comment">{esc(s.get("comment"))}</p>' if s.get("comment") else ""
    return f'''<section>
  {_section_header("주가 동향", s.get("assessment"))}
  <p class="muted">종목코드 {esc(s.get("code"))} · {esc(s.get("period"))}</p>
  {chart}
  <div class="metrics">{cards}</div>
  {comment}
</section>'''


def render_overall(o: dict) -> str:
    if not o:
        return ""
    return f'''<section class="overall">
  {_section_header("종합 심사 의견", o)}
  <p>{esc(o.get("summary"))}</p>
  <p class="disclaimer">※ 본 리포트는 공개 데이터(DART·뉴스·시세) 기반 의사결정 보조 자료이며,
  최종 여신 판단은 심사역의 검토를 따릅니다.</p>
</section>'''


def build_html(p: dict) -> str:
    company = p.get("company", {})
    name = company.get("name", "기업")
    overall = p.get("overall", {})
    gen = p.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    header_badge = _badge(overall.get("tone", "none"),
                          f"신용리스크: {overall.get('level','미평가')}") if overall else ""
    return f'''<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>대출심사 리포트 - {esc(name)}</title>
<style>
:root {{ --ibk:#00457c; --ink:#1f2937; --muted:#6b7280; --line:#e5e7eb; --bg:#f6f7f9; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:-apple-system,'Malgun Gothic',sans-serif; color:var(--ink);
  background:var(--bg); line-height:1.6; }}
.wrap {{ max-width:860px; margin:0 auto; padding:32px 20px 64px; }}
header.top {{ background:var(--ibk); color:#fff; border-radius:14px; padding:24px 28px;
  display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }}
header.top h1 {{ margin:0; font-size:24px; }}
header.top .sub {{ opacity:.85; font-size:13px; margin-top:4px; }}
section {{ background:#fff; border:1px solid var(--line); border-radius:14px;
  padding:22px 24px; margin-top:18px; }}
h2 {{ font-size:18px; margin:0 0 14px; }}
.sec-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:6px; }}
.sec-head h2 {{ margin:0; }}
.badge {{ color:#fff; font-size:13px; font-weight:600; padding:4px 12px; border-radius:999px;
  white-space:nowrap; }}
.kv-grid {{ display:grid; grid-template-columns:repeat(2,1fr); gap:10px 24px; }}
.kv {{ display:flex; justify-content:space-between; border-bottom:1px dashed var(--line);
  padding:6px 0; }}
.kv .k {{ color:var(--muted); }}
.metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
  gap:12px; margin:14px 0; }}
.metric {{ background:var(--bg); border-radius:10px; padding:14px; text-align:center; }}
.metric .mv {{ font-size:20px; font-weight:700; }}
.metric .ml {{ font-size:12px; color:var(--muted); margin-top:4px; }}
table.acc {{ width:100%; border-collapse:collapse; margin-top:8px; font-size:14px; }}
table.acc th, table.acc td {{ padding:8px 10px; border-bottom:1px solid var(--line);
  text-align:left; }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.risks {{ list-style:none; padding:0; margin:8px 0 0; }}
.risk {{ display:flex; gap:12px; padding:12px 0; border-bottom:1px solid var(--line);
  align-items:flex-start; }}
.risk-title {{ font-weight:600; }}
.risk-sum {{ font-size:13px; color:#374151; margin-top:4px; }}
.chart {{ width:100%; height:auto; }}
.chart .axis {{ font-size:11px; fill:var(--muted); }}
.chart .lastlbl {{ font-size:12px; font-weight:700; }}
.muted {{ color:var(--muted); font-size:13px; margin:6px 0; }}
.comment {{ background:#f0f6fb; border-left:3px solid var(--ibk); padding:10px 14px;
  border-radius:6px; margin-top:14px; font-size:14px; }}
.overall {{ border:2px solid var(--ibk); }}
.disclaimer {{ color:var(--muted); font-size:12px; margin-top:14px; }}
footer {{ text-align:center; color:var(--muted); font-size:12px; margin-top:24px; }}
@media(max-width:600px){{ .kv-grid{{grid-template-columns:1fr;}} }}
</style></head>
<body><div class="wrap">
<header class="top">
  <div><h1>{esc(name)} 대출심사 정보 요약</h1>
  <div class="sub">생성 {esc(gen)} · 공개 데이터 기반 여신심사 보조 리포트</div></div>
  {header_badge}
</header>
{render_company(company)}
{render_financial(p.get("financial"))}
{render_reputation(p.get("reputation"))}
{render_stock(p.get("stock"))}
{render_overall(overall)}
<footer>IBK AX 실습 · 대출심사 정보 파악 데모</footer>
</div></body></html>'''


def main():
    ap = argparse.ArgumentParser(description="대출심사 리포트 HTML 렌더러")
    ap.add_argument("payload", nargs="?", help="입력 JSON 파일 (미지정 시 stdin)")
    ap.add_argument("-o", "--out", help="출력 HTML 경로 (미지정 시 stdout)")
    args = ap.parse_args()

    raw = open(args.payload, encoding="utf-8").read() if args.payload else sys.stdin.read()
    payload = json.loads(raw)
    html_out = build_html(payload)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(html_out)
        print(f"리포트 생성: {args.out}")
    else:
        print(html_out)


if __name__ == "__main__":
    main()
