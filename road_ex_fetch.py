#!/usr/bin/env python3
"""한국도로공사 계약정보 → 신설 고속도로 공식 사업 SSOT (2026-07-17 신설).

배경(사용자 확정 사상): "핵심은 호재를 정확히 발굴하고 진척단계·위치를 정확히 아는 것 — OSM은 가비지."
OSM은 존재·단계·명칭의 근거가 될 수 없다. 이 커넥터가 그 자리를 대체한다.

소스: https://www.ex.co.kr/portal/biz/contSct/contSctInfo.do?cnstSm=<연번>  (공개 계약정보)
얻는 것 — 전부 원문 사실:
  · 공식 공사명   "고속국도 제400호선 김포~파주간 건설공사[제1공구]"  ← 노선번호·구간·공구까지
  · 위치         "경기도 김포시 양촌읍 흥신리"                      ← 읍면동·리 단위
  · 계약일자      2019-02-27                                       ← 착공 시점 근거
  · 공정률(%)     41.5 ('25년04월말 기준)                          ← 진척 사실 + 기준시점
  · 공사비·시공사
공구(제N공구) → 사업 단위로 그루핑해 official_roads.json에 병합한다.

⚠ §7 "진척률 % 금지"와의 관계: 공정률은 **사이트에 표시하지 않는다**. 단계(착공/준공) 판정과
   신선도(stale 여부) 판단의 내부 근거로만 쓰고, 표시는 마일스톤(계약일·기준시점)까지만.

사용: python3 road_ex_fetch.py [시작연번] [끝연번]   (기본 5300~5900)
산출: road_ex_projects.json (공구 원본) + official_roads.json 병합 제안 출력
"""
import json, re, ssl, sys, time, socket, urllib.request

socket.setdefaulttimeout(60)
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", "Accept": "text/html,application/xhtml+xml,*/*"}
BEG = int(sys.argv[1]) if len(sys.argv) > 1 else 5300
END = int(sys.argv[2]) if len(sys.argv) > 2 else 5900

def fetch(i):
    url = f"https://www.ex.co.kr/portal/biz/contSct/contSctInfo.do?cnstSm={i}"
    for a in range(2):
        try:
            h = urllib.request.urlopen(urllib.request.Request(url, headers=UA), context=CTX, timeout=60).read()
            return h.decode("utf-8", "replace")
        except Exception:
            time.sleep(3)
    return None

def parse(h):
    t = re.sub(r"<[^>]+>", " ", h); t = re.sub(r"\s+", " ", t)
    def grab(pat):
        m = re.search(pat, t)
        return m.group(1).strip() if m else None
    name = grab(r"공사명\s+(.+?)\s+위치")
    if not name: return None
    loc = grab(r"위치\s+(.+?)\s+공사비")
    cost = grab(r"공사비\s+([\d,]+)")
    cdate = grab(r"계약일자\s+(\d{4}-\d{2}-\d{2})")
    prog = grab(r"전체 공정률\(%\)\s+([\d.]+)")
    basis = grab(r"공사진척상황\s*(?:&nbsp;)?\s*\('(\d{2})년(\d{2})월말 기준\)")
    m = re.search(r"\('(\d{2})년(\d{2})월말 기준\)", t)
    basis = f"20{m.group(1)}-{m.group(2)}" if m else None
    return {"name": name, "loc": loc, "cost_won": int(cost.replace(",", "")) if cost else None,
            "contract_date": cdate, "progress_pct": float(prog) if prog else None, "progress_basis": basis}

def project_of(name):
    """공구 단위 공사명 → 사업 단위 (…[제N공구] 제거)"""
    return re.sub(r"\s*\[제?\s*\d+\s*공구\]\s*$", "", name).strip()

def main():
    rows = []
    for i in range(BEG, END + 1):
        h = fetch(i)
        if not h: continue
        d = parse(h)
        if not d: continue
        d["id"] = i; d["project"] = project_of(d["name"])
        rows.append(d)
        print(f"  [{i}] {d['name'][:44]} · {d['loc'] or '-'} · 공정 {d['progress_pct']}% ({d['progress_basis']})")
        time.sleep(0.4)
    json.dump({"_note": "한국도로공사 계약정보 원본(공구 단위). 공정률은 내부 판정용 — 사이트 미표시(§7).",
               "rows": rows}, open("road_ex_projects.json", "w"), ensure_ascii=False, indent=1)

    # 사업 단위 집계 → official_roads 병합 후보
    proj = {}
    for r in rows:
        p = proj.setdefault(r["project"], {"gongu": 0, "locs": [], "dates": [], "prog": [], "basis": set()})
        p["gongu"] += 1
        if r["loc"]: p["locs"].append(r["loc"])
        if r["contract_date"]: p["dates"].append(r["contract_date"])
        if r["progress_pct"] is not None: p["prog"].append(r["progress_pct"])
        if r["progress_basis"]: p["basis"].add(r["progress_basis"])
    print(f"\n공구 {len(rows)} → 사업 {len(proj)}")
    out = []
    for nm, v in sorted(proj.items()):
        if not v["prog"]: continue
        lo, hi = min(v["prog"]), max(v["prog"])
        phase = "준공" if lo >= 99.5 else "착공"
        out.append({"name": nm, "phase": phase,
                    "stage": f"공사중 {v['gongu']}개 공구 (도로공사 계약정보 · 기준 {sorted(v['basis'])[-1] if v['basis'] else '-'})"
                             if phase == "착공" else "준공",
                    "owner": "도로공사", "type": "고속도로",
                    "first_contract": min(v["dates"]) if v["dates"] else None,
                    "_progress_range": [lo, hi], "_basis": sorted(v["basis"]),
                    "src": f"https://www.ex.co.kr/portal/biz/contSct/contSctInfo.do?cnstSm={[r['id'] for r in rows if r['project']==nm][0]}"})
        print(f"  · {nm[:50]} · {v['gongu']}공구 · 공정 {lo}~{hi}% · 최초계약 {min(v['dates']) if v['dates'] else '-'}")
    json.dump({"_note": "사업 단위 집계 — official_roads.json 병합 후보", "projects": out},
              open("road_ex_projects_grouped.json", "w"), ensure_ascii=False, indent=1)
    print(f"\n→ road_ex_projects.json ({len(rows)} 공구) · road_ex_projects_grouped.json ({len(out)} 사업)")

if __name__ == "__main__":
    main()
