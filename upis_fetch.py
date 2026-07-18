#!/usr/bin/env python3
"""브이월드 도시계획(공간시설) API → 신규 대형 공원 완전자동 수집 (전국·면적·좌표·고시일 자동).

배경(2026-07-18 소스 검증 최종): 신규 공원을 완전자동으로 잡는 소스 확정 = 브이월드 2D데이터 도시계획 API.
 · 표준데이터=기존공원(신설판별불가) / 고시파일=반기·본문에 면적없음 → 둘 다 반자동.
 · **브이월드 도시계획(공간시설) LT_C_UPISUQ153**는 면적(dgm_ar)·좌표(geometry)·결정고시일(ntfc_sn)을 **API가 직접** 제공.
 · 우리 기존 vworld 키로 동작(발급 불필요) — 실패원인은 domain 파라미터 누락이었음.

전자동 파이프라인: 전국 그리드 순회 → attrFilter로 면적 5만㎡+ 서버필터 → 공원만(어린이·소·광장·녹지 제외)
 → 결정고시일(ntfc NTC) delta 필터 → MultiPolygon 중심점 → 면적 등급. **사람 손 0.**
 승격 요건(§확정층: 면적·좌표·공식근거·고시일) 전부 자동 충족 → signals 층 직접 생성 가능.

승격 기준(§7b·사용자 확정): 공원 A≥30만㎡ / B≥10만㎡ / C≥5만㎡. delta=최근 결정고시(기본 2023+).

사용: VWORLD_KEY=xxx VWORLD_DOMAIN=undeadhwan.github.io python3 upis_fetch.py [--since 2023]
"""
import os, sys, json, ssl, re, time, math, csv, urllib.request, urllib.parse, datetime
csv.field_size_limit(10**7)

BASE = os.path.dirname(os.path.abspath(__file__))
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
KEY = os.environ.get("VWORLD_KEY", "").strip()
DOM = os.environ.get("VWORLD_DOMAIN", "undeadhwan.github.io").strip()
API = "https://api.vworld.kr/req/data"
SINCE = os.environ.get("UPIS_SINCE", "2023")   # 결정고시 연도 하한(delta)

# ── 유형별 설정(§7b) — 새 유형 추가는 여기 한 줄. 브이월드 도시계획 레이어코드 확인 완료(2026-07-18).
#    grade=(면적문턱,등급) 내림차순 · min_area=attrFilter 서버필터(=C문턱) · out=signals 파일
TYPES = {
    "park":     {"code": "LT_C_UPISUQ153", "kw": ("공원",), "no": ("어린이", "소공원", "녹지", "광장", "공공공지", "유원지 기타"),
                 "min_area": 50000, "grade": ((300000, "A"), (100000, "B"), (50000, "C")),
                 "cat": "인프라", "sub": "공원·녹지", "purp": "공원", "label": "신규 대형공원",
                 "stage": "도시계획시설(공원) 결정", "out": "park_signals.json"},
    "hospital": {"code": "LT_C_UPISUQ157", "kw": ("병원", "종합의료"), "no": ("화장", "묘지", "도축", "장사", "자연장"),
                 "min_area": 10000, "grade": ((50000, "A"), (20000, "B"), (10000, "C")),
                 "cat": "인프라", "sub": "병원", "purp": "의료", "label": "신규 종합병원",
                 "stage": "도시계획시설(종합의료) 결정", "out": "hospital_signals.json",
                 "note": "규모=부지면적 프록시(병상 미제공) — §7b 병상기준의 근사"},
}

# 전국 그리드(경위도). 본토+주요 섬(제주·강화 등) 포괄. 0.4° 타일.
LNG0, LNG1, LAT0, LAT1, STEP = 126.0, 130.9, 33.1, 38.65, 0.4

def fetch_box(code, min_area, box, page):
    p = {"service": "data", "request": "GetFeature", "data": code, "key": KEY, "domain": DOM,
         "format": "json", "size": "1000", "page": str(page), "geomFilter": box,
         "crs": "EPSG:4326", "attrFilter": f"dgm_ar:>=:{min_area}"}
    for attempt in range(3):
        try:
            req = urllib.request.Request(API + "?" + urllib.parse.urlencode(p), headers={"User-Agent": "hojaemap/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            resp = j.get("response", {})
            if resp.get("status") != "OK": return [], 0
            fc = (resp.get("result") or {}).get("featureCollection") or {}
            total = int(((resp.get("record") or {}).get("total")) or len(fc.get("features") or []))
            return fc.get("features") or [], total
        except Exception as e:
            if attempt == 2: print(f"  box {box} p{page} 실패: {e}"); return [], 0
            time.sleep(1.2 * (attempt + 1))
    return [], 0

def centroid(geom):
    xs, ys = [], []
    def walk(c):
        if isinstance(c[0], (int, float)): xs.append(c[0]); ys.append(c[1])
        else:
            for x in c: walk(x)
    walk(geom["coordinates"])
    return [round(sum(xs)/len(xs), 6), round(sum(ys)/len(ys), 6)] if xs else None

def grade_of(grade_tbl, a):
    for th, g in grade_tbl:
        if a >= th: return g
    return None

def collect(cfg):
    from collections import Counter
    seen, items, nb = {}, [], 0
    lat = LAT0
    while lat < LAT1:
        lng = LNG0
        while lng < LNG1:
            box = f"BOX({lng:.3f},{lat:.3f},{lng+STEP:.3f},{lat+STEP:.3f})"
            page = 1
            while True:
                feats, total = fetch_box(cfg["code"], cfg["min_area"], box, page)
                if not feats: break
                for f in feats:
                    p = f["properties"]; nm = p.get("dgm_nm", "") or ""
                    if not any(k in nm for k in cfg["kw"]): continue
                    if any(k in nm for k in cfg["no"]): continue
                    try: area = int(float(p.get("dgm_ar") or 0))
                    except: area = 0
                    if area < cfg["min_area"]: continue
                    m = re.search(r"NTC(\d{8})", p.get("ntfc_sn", "") or "")
                    gy = m.group(1) if m else ""
                    key = p.get("ntfc_sn") or p.get("present_sn") or (nm + str(area))
                    if key in seen: continue
                    seen[key] = 1
                    items.append({"name": nm, "area_m2": area, "grade": grade_of(cfg["grade"], area),
                                  "gosi": f"{gy[:4]}-{gy[4:6]}-{gy[6:8]}" if len(gy) == 8 else "",
                                  "gosi_yr": gy[:4], "ntfc": p.get("ntfc_sn", ""),
                                  "sgg_cd": p.get("signgu_se", ""), "coord": centroid(f.get("geometry", {}))})
                if page * 1000 >= total: break
                page += 1
            nb += 1; lng += STEP
        lat += STEP
    valid_yr = lambda y: y.isdigit() and SINCE <= y <= str(datetime.date.today().year)
    delta = [x for x in items if valid_yr(x["gosi_yr"])]
    # 최초/변경 판별 (고시파일 조인) — V-World는 최초·변경 구분 불가(검증: 공원 93%가 변경 재고시)
    GOSI = os.environ.get("GOSI_CSV", "").strip()
    if GOSI and os.path.exists(GOSI):
        title = {}
        rd = csv.reader(open(GOSI, encoding="cp949", errors="replace")); hh = next(rd)
        gm, ti = hh.index("고시관리코드"), hh.index("제목")
        for row in rd:
            if len(row) > ti: title[row[gm]] = row[ti]
        # 공원 '조성' 고유 고시만 = 진짜 신규(확장 포함). 개발 고시(재개발·산단·철도 등)가 공원 폴리곤을
        # 재기록한 오탐(2026-07-18 사용자 발견 — GTX-B가 부천종합운동장 공원 ntfc 재기록: 34건 중 44% 오탐)을 배제.
        # 신설·실시계획인가만(불확실한 변경 제외 — 확장은 V-World ntfc가 낡아 어차피 못 잡음 → gosi_fetch 몫).
        # 개발고시 재기록 오탐(GTX·재개발·산단 등)도 배제 — 2026-07-18 사용자 발견(34건 중 44% 오탐).
        CREATE = re.compile(r"공원.*조성계획.*(결정|인가)|도시.*계획시설.*공원.*결정|군계획시설.*공원.*(결정|인가)|공원조성계획.*결정")
        EXCLUDE = re.compile(r"재개발|재건축|정비사업|정비구역|지적재조사|철도|급행|역세권|장기전세|복합지구|주거형|산업단지|산단|물류단지|공공주택지구|실효|폐지|정정|경미|변경")
        NAMERE = re.compile(r"([가-힣0-9]{2,}(?:근린공원|문화공원|체육공원|수변공원|역사공원|생태공원|도시농업공원|공원))")
        for x in delta:
            t = title.get(x["ntfc"]) or ""
            x["gosi_title"] = t[:70]
            x["first"] = bool(CREATE.search(t)) and not bool(EXCLUDE.search(t))
            nm = NAMERE.search(t)   # 고시 제목에서 진짜 공원명 추출
            if nm: x["park_name"] = nm.group(1)
        first = [x for x in delta if x.get("first")]
        print(f"  고시 조인: 공원조성 고시(진짜 신규·확장) {len(first)} / 개발고시 재기록 오탐 제외 {len(delta)-len(first)}")
        delta = first
    else:
        print("  ⚠ GOSI_CSV 미지정 → 최초/변경 판별 불가(변경 포함)")
    delta.sort(key=lambda x: (x["gosi"], x["area_m2"]), reverse=True)
    return items, delta, nb

def build_signals(cfg, delta):
    """delta → signals FeatureCollection (지도 직접 로드용)."""
    feats = []
    for p in delta:
        if not p.get("coord"): continue
        gd = p["gosi"] if len(p["gosi"]) == 10 and "00" not in p["gosi"][5:] else (p["gosi_yr"] + "-01")
        # 표시명: 고시 제목의 실제 공원명 > dgm_nm 괄호 이름 > 유형라벨
        paren = re.search(r"\(([^)]*공원[^)]*)\)", p["name"])
        pname = p.get("park_name") or (paren.group(1) if paren else None) or p["name"].split("/")[0]
        feats.append({"type": "Feature", "properties": {
            "kind": "facility", "cat": cfg["cat"], "sub": cfg["sub"], "title": f"{pname} ({p['area_m2']:,}㎡)",
            "lvl": p["grade"], "template_type": "생활인프라", "current_stage": cfg["stage"], "phase": "인가",
            "status": "활성", "confirmed": False, "purp": cfg["purp"], "area_m2": p["area_m2"],
            "pt_type": "facility", "geo_prec": "parcel", "src_tier": "official_notice",
            "src_name": "국토부 도시계획 결정고시(브이월드)", "sources": [],
            "note": f"{cfg['label']}({pname}) · 결정고시 {gd}",
            "milestones": [{"phase": "인가", "stage": cfg["stage"], "date": gd, "state": "current", "src": "국토부 결정고시"},
                           {"phase": "착공", "stage": "조성·건립", "state": "future"},
                           {"phase": "준공", "stage": "개장·개원", "state": "future"}],
            "audited": datetime.date.today().isoformat()},
            "geometry": {"type": "Point", "coordinates": p["coord"]}})
    return feats

def collect_all(cfg):
    """delta·최초 필터 없이 min_area 이상 전체 인벤토리(좌표 포함) — 주간 self-diff 기준선용."""
    from collections import Counter
    seen, items, nb = {}, [], 0
    lat = LAT0
    while lat < LAT1:
        lng = LNG0
        while lng < LNG1:
            box = f"BOX({lng:.3f},{lat:.3f},{lng+STEP:.3f},{lat+STEP:.3f})"
            page = 1
            while True:
                feats, total = fetch_box(cfg["code"], cfg["min_area"], box, page)
                if not feats: break
                for f in feats:
                    p = f["properties"]; nm = p.get("dgm_nm", "") or ""
                    if not any(k in nm for k in cfg["kw"]) or any(k in nm for k in cfg["no"]): continue
                    try: area = int(float(p.get("dgm_ar") or 0))
                    except: area = 0
                    if area < cfg["min_area"]: continue
                    key = p.get("ntfc_sn") or p.get("present_sn") or (nm + str(area))
                    if key in seen: continue
                    seen[key] = 1
                    c = centroid(f.get("geometry", {}))
                    m = re.search(r"NTC(\d{8})", p.get("ntfc_sn", "") or "")
                    gy = (m.group(1) if m else "")
                    if c: items.append({"name": nm, "area_m2": area, "grade": grade_of(cfg["grade"], area),
                                        "gosi": f"{gy[:4]}-{gy[4:6]}-{gy[6:8]}" if len(gy) == 8 else "",
                                        "gosi_yr": gy[:4], "ntfc": p.get("ntfc_sn", ""), "coord": c})
                if page * 1000 >= total: break
                page += 1
            nb += 1; lng += STEP
        lat += STEP
    return items, nb

def weekly(cfg):
    """좌표 기반 self-diff — 지난 기준선에 없던 좌표의 공원 = 신규(고시파일 의존 없음).
    첫 실행은 기준선만 확립(신규 0). 이후 매주 새 좌표만 신규로 append."""
    import math
    base_path = f"{BASE}/upis_{cfg['sub'].split('·')[0]}_baseline.json"
    prev = json.load(open(base_path)) if os.path.exists(base_path) else None
    items, nb = collect_all(cfg)
    def near(c, coords):   # 300m 내 기존 좌표 있으면 동일 시설(변경)로 간주
        for x, y in coords:
            if abs(x-c[0]) < 0.004 and abs(y-c[1]) < 0.004 and math.hypot((x-c[0])*88800, (y-c[1])*111000) < 300:
                return True
        return False
    snapshot = {tuple(p["coord"]): p["area_m2"] for p in items}
    if prev is None:
        json.dump({"collected": datetime.date.today().isoformat(),
                   "parks": [[p["coord"][0], p["coord"][1], p["area_m2"]] for p in items]},
                  open(base_path, "w"), ensure_ascii=False)
        print(f"  [주간] 기준선 확립: {len(items)}건 · 신규 0 (다음 실행부터 diff)")
        return []
    # 좌표+면적 diff: 새 좌표=신설 / 기존 좌표인데 면적 20%+ 증가=확장 — 둘 다 신규 호재
    base = [(x, y, a) for x, y, a in prev.get("parks", [])] or [(x, y, 0) for x, y in prev.get("coords", [])]
    base_coords = [(x, y) for x, y, _ in base]
    def base_area(c):
        for x, y, a in base:
            if abs(x-c[0]) < 0.004 and abs(y-c[1]) < 0.004 and math.hypot((x-c[0])*88800, (y-c[1])*111000) < 300:
                return a
        return None
    new = []
    for p in items:
        ba = base_area(p["coord"])
        if ba is None: p["_why"] = "신설"; new.append(p)                     # 새 좌표 = 신설
        elif ba and p["area_m2"] > ba * 1.2: p["_why"] = "확장"; new.append(p)  # 면적 20%+ 증가 = 확장
    json.dump({"collected": datetime.date.today().isoformat(),
               "parks": [[p["coord"][0], p["coord"][1], p["area_m2"]] for p in items]},
              open(base_path, "w"), ensure_ascii=False)
    print(f"  [주간] 인벤토리 {len(items)} · 기준선 {len(base)} → 신규 {len(new)}(신설 {sum(1 for p in new if p.get('_why')=='신설')}·확장 {sum(1 for p in new if p.get('_why')=='확장')})")
    return new

def main():
    from collections import Counter
    if not KEY: print("❌ VWORLD_KEY 필요"); sys.exit(1)
    t = sys.argv[sys.argv.index("--type") + 1] if "--type" in sys.argv else "park"
    if t not in TYPES: print(f"❌ 유형 미정의: {t} (가능: {list(TYPES)})"); sys.exit(1)
    cfg = TYPES[t]

    # ── 주간 self-diff 모드: 좌표 diff로 신규만 park_signals에 append (고시파일 불필요) ──
    if "--weekly" in sys.argv:
        print(f"브이월드 {cfg['label']} 주간 self-diff ...")
        new = weekly(cfg)
        if not new: return
        existing = json.load(open(f"{BASE}/{cfg['out']}")) if os.path.exists(f"{BASE}/{cfg['out']}") else {"type": "FeatureCollection", "features": []}
        existing["features"] += build_signals(cfg, new)
        existing["weekly_updated"] = datetime.date.today().isoformat()
        json.dump(existing, open(f"{BASE}/{cfg['out']}", "w"), ensure_ascii=False, indent=1)
        print(f"  → {cfg['out']}에 신규 {len(new)}건 추가 (총 {len(existing['features'])})")
        return
    print(f"브이월드 도시계획 {cfg['label']} 전국 순회 — {cfg['sub']} {cfg['min_area']:,}㎡+ · 결정고시 {SINCE}+ ...")
    items, delta, nb = collect(cfg)
    feats = build_signals(cfg, delta)
    json.dump({"type": "FeatureCollection",
               "_note": f"{cfg['label']}({cfg['sub']} {cfg['min_area']:,}㎡+·최초결정 {SINCE}+) — 브이월드 도시계획 API 자동수집+고시파일 최초판별. upis_fetch.py --type {t}."
                        + (" " + cfg["note"] if cfg.get("note") else ""),
               "collected": datetime.date.today().isoformat(), "boxes": nb,
               "all_over_min": len(items), "delta_new": len(feats),
               "by_grade": dict(Counter(p["grade"] for p in delta)), "features": feats},
              open(f"{BASE}/{cfg['out']}", "w"), ensure_ascii=False, indent=1)
    print(f"  전국 {nb}박스 · {cfg['sub']} {len(items)}건 · 신규 {len(feats)}건 → {cfg['out']}")
    print("  등급:", dict(Counter(p["grade"] for p in delta)))
    for p in delta[:12]:
        print(f"    [{p['grade']}] {p['area_m2']:>9,}㎡ | 고시 {p['gosi']} | {p['name']} | {p['coord']}")

if __name__ == "__main__":
    main()
