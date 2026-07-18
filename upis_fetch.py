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
        CHG = re.compile(r"변경|실효|폐지|정정|해제")
        for x in delta:
            t = title.get(x["ntfc"])
            x["first"] = None if t is None else not bool(CHG.search(t))
            if t: x["gosi_title"] = t[:70]
        first = [x for x in delta if x.get("first")]
        print(f"  고시 조인: 최초(신설) {len(first)} / 변경·미매칭 {len(delta)-len(first)}")
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
        feats.append({"type": "Feature", "properties": {
            "kind": "facility", "cat": cfg["cat"], "sub": cfg["sub"], "title": f"{p['name']} ({p['area_m2']:,}㎡)",
            "lvl": p["grade"], "template_type": "생활인프라", "current_stage": cfg["stage"], "phase": "인가",
            "status": "활성", "confirmed": False, "purp": cfg["purp"], "area_m2": p["area_m2"],
            "pt_type": "facility", "geo_prec": "parcel", "src_tier": "official_notice",
            "src_name": "국토부 도시계획 결정고시(브이월드)", "sources": [],
            "note": f"{cfg['label']} 결정 · {p.get('gosi_title','')[:40]}",
            "milestones": [{"phase": "인가", "stage": cfg["stage"], "date": gd, "state": "current", "src": "국토부 결정고시"},
                           {"phase": "착공", "stage": "조성·건립", "state": "future"},
                           {"phase": "준공", "stage": "개장·개원", "state": "future"}],
            "audited": datetime.date.today().isoformat()},
            "geometry": {"type": "Point", "coordinates": p["coord"]}})
    return feats

def main():
    from collections import Counter
    if not KEY: print("❌ VWORLD_KEY 필요"); sys.exit(1)
    t = sys.argv[sys.argv.index("--type") + 1] if "--type" in sys.argv else "park"
    if t not in TYPES: print(f"❌ 유형 미정의: {t} (가능: {list(TYPES)})"); sys.exit(1)
    cfg = TYPES[t]
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
