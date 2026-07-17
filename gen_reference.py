#!/usr/bin/env python3
"""노선·역/도로·IC 대장(rail_reference.html) 생성기 — rail_reference.json(+road_reference.json 있으면) → HTML.
철도/도로 탭 필터 지원. 스타일은 기존 대장 CSS(/tmp 아님, 아래 내장)와 동일 계열.
사용: python3 gen_reference.py  (수정 후 dist 배포 필요)
"""
import json, os, html

RAIL = json.load(open("rail_reference.json"))["lines"]
ROAD = json.load(open("road_reference.json"))["lines"] if os.path.exists("road_reference.json") else []

CSS = open("ref_style.css").read() if os.path.exists("ref_style.css") else None

def chip(stage):
    s = stage or ""
    if any(k in s for k in ("착공", "준공예정", "건설", "개통예정")): return ("var(--a-run)", "var(--a-run-bg)")
    if any(k in s for k in ("예비타당성", "예타", "적격성", "타당성")): return ("var(--a-feas)", "var(--a-feas-bg)")
    return ("var(--a-plan)", "var(--a-plan-bg)")

def card(r, pt_label="역 · IC"):
    c, bg = chip(r.get("stage"))
    geom = r.get("geom", "")
    gcls = "osm" if "실선" in geom else "corr"
    gmark = "━ 실선형" if "실선" in geom else "┄ 근사 회랑"
    rows = "".join(
        f'<tr><td class="i">{i+1}</td><td class="n">{html.escape(s["name"])}</td>'
        f'<td class="c">{s["lat"]:.5f}</td><td class="c">{s["lng"]:.5f}</td></tr>'
        for i, s in enumerate(r.get("stations", [])))
    body = (f'<div class="sts"><table><thead><tr><th></th><th>{pt_label}</th><th>위도(lat)</th><th>경도(lng)</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>') if rows else '<div class="empty">좌표 확보된 지점 없음 (선형만 표시)</div>'
    note = f'<div class="lnote">{html.escape(r["note"])}</div>' if r.get("note") else ""
    return (f'<div class="card"><div class="crow"><span class="nm">{html.escape(r["name"])}</span>'
            f'<span class="kind">{html.escape(r.get("kind") or "")}</span>'
            f'<span class="chip" style="color:{c};background:{bg}">{html.escape(r.get("stage") or "")}</span>'
            f'<span class="geom {gcls}">{gmark}</span>'
            f'<span class="src">{html.escape(r.get("src") or "")}</span></div>{note}{body}</div>')

def group(lines, title, pt_label):
    """단계별 그룹: 착공·건설 먼저, 계획 뒤"""
    run = [r for r in lines if chip(r.get("stage"))[0] == "var(--a-run)"]
    rest = [r for r in lines if r not in run]
    out = []
    if run:
        out.append(f'<div class="grp"><h2>{title} — 착공 · 건설중 · 준공예정</h2><span class="c">{len(run)}개</span><span class="bar"></span></div>')
        out += [card(r, pt_label) for r in run]
    if rest:
        out.append(f'<div class="grp"><h2>{title} — 계획 · 예타 · 구상</h2><span class="c">{len(rest)}개</span><span class="bar"></span></div>')
        out += [card(r, pt_label) for r in rest]
    return "".join(out)

def main():
    # 기존 html에서 CSS 재사용(스타일 일관)
    prev = open("rail_reference.html", encoding="utf-8").read()
    head = prev[:prev.find("</style>") + 8]
    # 필터 탭 CSS 추가(1회)
    if ".tabbar" not in head:
        head = head.replace("</style>", """
.tabbar{display:flex;gap:8px;margin:14px 0 4px}
.tab{padding:7px 18px;border:1.5px solid var(--line);border-radius:999px;background:var(--panel);color:var(--ink2);font-weight:700;font-size:14px;cursor:pointer}
.tab.on{background:var(--accent);border-color:var(--accent);color:#fff}
.lnote{font-size:12px;color:var(--ink2);margin:4px 0 6px}
</style>""")
    n_rail_st = sum(len(r.get("stations", [])) for r in RAIL)
    n_road_pt = sum(len(r.get("stations", [])) for r in ROAD)
    osm = sum(1 for r in RAIL + ROAD if "실선" in r.get("geom", ""))
    stats = (f'<div class="stats"><div class="stat"><b>{len(RAIL)}</b><span>철도 노선</span></div>'
             f'<div class="stat"><b>{n_rail_st}</b><span>역 좌표</span></div>'
             + (f'<div class="stat"><b>{len(ROAD)}</b><span>도로 노선</span></div>'
                f'<div class="stat"><b>{n_road_pt}</b><span>IC·JC 좌표</span></div>' if ROAD else "")
             + f'<div class="stat"><b>{osm}</b><span>OSM 실선형</span></div></div>')
    tabs = ('<div class="tabbar"><span class="tab on" onclick="pick(this,\'rail\')">🚈 철도</span>'
            '<span class="tab" onclick="pick(this,\'road\')">🛣 도로</span></div>') if ROAD else ""
    body = f"""
<div class="wrap"><div class="inner">
<div class="hd"><h1>신규 교통 노선 · 지점 좌표 대장</h1><p class="sub">호재맵에 적용된 계획·건설 철도/도로 노선과 역·IC 위치 — 호재영향권 산정 기준 좌표</p></div>
{stats}
<div class="note"><b>좌표 방침</b> — ━ 실선형은 OpenStreetMap(ODbL) 실제 선형, ┄ 근사 회랑은 계획 지점 위치를 이은 근사(지점 위치가 기준). 역·IC 좌표는 공식 계획(국가철도망·도시철도망·도로건설계획)의 계획 위치(파일럿). 가칭 표기는 역명·위치가 기본계획 확정 전임을 뜻함. 경로 미확정 순수구상은 §7 정직성 원칙상 미수록.</div>
{tabs}
<div id="sec-rail">{group(RAIL, "철도", "역 · 정거장")}</div>
<div id="sec-road" style="display:none">{group(ROAD, "도로", "IC · JC · 지점") if ROAD else ""}</div>
</div></div>
<script>
function pick(el,k){{document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));el.classList.add('on');
document.getElementById('sec-rail').style.display=k==='rail'?'':'none';
document.getElementById('sec-road').style.display=k==='road'?'':'none';}}
</script>"""
    open("rail_reference.html", "w", encoding="utf-8").write(head + body)
    print(f"rail_reference.html 재생성 — 철도 {len(RAIL)}노선·역 {n_rail_st} / 도로 {len(ROAD)}노선·지점 {n_road_pt}")

if __name__ == "__main__":
    main()
