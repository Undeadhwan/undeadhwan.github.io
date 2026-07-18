#!/usr/bin/env python3
"""공원 신규+확장 완전판 — 고시파일(정확 감지) + 브이월드(면적·좌표) 결합.

배경(2026-07-18 사용자 발견): V-World 폴리곤 ntfc가 낡아 **확장을 못 잡고**(오정대공원 2025 확장 미검출),
개발고시 재기록 오탐(34건 중 44%)도 냈다. → **고시파일이 주소스**(공원조성계획 결정/인가 = 신설+확장 정확),
V-World는 **이름매칭으로 면적·좌표만** 보강(ntfc 아닌 이름 매칭이라 낡음 무관).

파이프라인: 고시파일 공원조성 고시(2025-01+, 신설·확장, 개발고시·경미변경 배제) → 공원명 추출
 → VWorld 지오코더로 좌표 → 근처 V-World 공간시설 폴리곤 이름매칭 → 면적·정밀중심 → 5만㎡+ 컷·등급.
승격 기준(§7b): A≥30만/B≥10만/C≥5만㎡. 신설/확장 태그.

사용: VWORLD_KEY=xxx GOSI_CSV=/path/TN_NTFC.csv python3 gosi_park.py [--since 2025-01-01]
"""
import os, sys, json, csv, re, ssl, time, math, urllib.request, urllib.parse, datetime
csv.field_size_limit(10**7)
BASE = os.path.dirname(os.path.abspath(__file__))
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
KEY = os.environ.get("VWORLD_KEY", "").strip()
DOM = os.environ.get("VWORLD_DOMAIN", "undeadhwan.github.io").strip()
GOSI = os.environ.get("GOSI_CSV", "").strip()
SINCE = os.environ.get("GOSI_PARK_SINCE", "2025-01-01")
MIN_AREA = 50000
GRADE = ((300000, "A"), (100000, "B"), (50000, "C"))

CREATE = re.compile(r"공원.*조성계획.*(결정|인가)|공원조성계획.*(결정|인가)")
EXCL = re.compile(r"재개발|재건축|정비사업|지적재조사|철도|급행|역세권|장기전세|복합지구|산업단지|산단|물류|공공주택지구|실효|폐지|정정|경미|어린이공원|소공원")
NAME = re.compile(r"([가-힣0-9]{2,}(?:근린공원|문화공원|체육공원|수변공원|역사공원|생태공원|대공원|공원))")

def geocode(q):   # VWorld 장소검색 → 좌표
    p = {"service": "search", "request": "search", "version": "2.0", "crs": "EPSG:4326",
         "size": "3", "query": q, "type": "place", "format": "json", "key": KEY}
    try:
        with urllib.request.urlopen("https://api.vworld.kr/req/search?" + urllib.parse.urlencode(p), timeout=15, context=CTX) as r:
            j = json.loads(r.read())
        its = ((j.get("response") or {}).get("result") or {}).get("items") or []
        if its: return [round(float(its[0]["point"]["x"]), 6), round(float(its[0]["point"]["y"]), 6)]
    except Exception: pass
    return None

def upis_near(coord, name):   # 좌표 근처 공간시설 폴리곤 → 이름 근접 or 최대면적 → (면적, 정밀중심)
    x, y = coord
    box = f"BOX({x-0.02:.4f},{y-0.02:.4f},{x+0.02:.4f},{y+0.02:.4f})"
    p = {"service": "data", "request": "GetFeature", "data": "LT_C_UPISUQ153", "key": KEY, "domain": DOM,
         "format": "json", "size": "100", "geomFilter": box, "crs": "EPSG:4326", "attrFilter": f"dgm_ar:>=:{MIN_AREA}"}
    try:
        with urllib.request.urlopen(urllib.request.Request("https://api.vworld.kr/req/data?" + urllib.parse.urlencode(p), headers={"User-Agent": "hojaemap/1.0"}), timeout=20, context=CTX) as r:
            j = json.loads(r.read())
        fs = (((j.get("response") or {}).get("result") or {}).get("featureCollection") or {}).get("features") or []
    except Exception: return None
    # 공원 고유명(오정·설봉 등)이 V-World 폴리곤명에 들어있는 것만 매칭 — 이름 안 맞으면 버림(거대 녹지구역 오매칭 방지).
    core = re.sub(r"공원.*$|근린|문화|체육|수변|역사|생태|도시|자연", "", name)
    if len(core) < 2: return None   # 고유명 없는 '근린공원' 등은 매칭 불가 → 스킵
    for f in fs:
        nm = f["properties"].get("dgm_nm", "") or ""
        if core in nm:
            try: area = int(float(f["properties"].get("dgm_ar") or 0))
            except: area = 0
            # 15만㎡ 넘는 초거대는 공원구역(녹지대)일 수 있어 이름 일치해도 상한 검증
            if area > 3000000: continue   # 300만㎡+ = 공원구역, 개별공원 아님
            return (area, centroid(f.get("geometry", {})), True)
    return None

def centroid(geom):
    xs, ys = [], []
    def walk(c):
        if isinstance(c[0], (int, float)): xs.append(c[0]); ys.append(c[1])
        else:
            for x in c: walk(x)
    if geom.get("coordinates"): walk(geom["coordinates"])
    return [round(sum(xs)/len(xs), 6), round(sum(ys)/len(ys), 6)] if xs else None

def grade(a):
    for th, g in GRADE:
        if a >= th: return g
    return None

def main():
    from collections import Counter
    if not KEY or not GOSI or not os.path.exists(GOSI):
        print("❌ VWORLD_KEY + GOSI_CSV 필요"); sys.exit(1)
    # 1. 고시파일 공원조성 고시 파싱 (신설·확장, dedup by 공원명+시군구)
    rd = csv.reader(open(GOSI, encoding="cp949", errors="replace")); h = next(rd)
    gm, ti, di = h.index("고시관리코드"), h.index("제목"), h.index("고시일자")
    parks = {}
    for row in rd:
        if len(row) <= ti: continue
        t, d = row[ti], row[di]
        if d < SINCE or not CREATE.search(t) or EXCL.search(t): continue
        nm = NAME.search(t)
        if not nm: continue
        pname, sgg = nm.group(1), row[gm][:5]
        exp = ("변경" in t or "확장" in t)
        k = (pname, sgg)
        if k not in parks or d > parks[k]["gosi"]:
            parks[k] = {"name": pname, "sgg": sgg, "gosi": d, "expand": exp, "title": t[:60]}
    print(f"고시파일 공원조성 고시({SINCE}+, 신설·확장): {len(parks)}개 공원 (dedup)")

    # 2. 각 공원 → VWorld 지오코딩 + V-World 면적·좌표 → 5만㎡+ 컷
    feats, nomatch, small = [], 0, 0
    for i, (k, p) in enumerate(parks.items()):
        c = geocode(p["name"])
        time.sleep(0.15)
        if not c: nomatch += 1; continue
        u = upis_near(c, p["name"])
        time.sleep(0.15)
        area = u[0] if u else 0
        coord = (u[1] if u and u[1] else c)
        if area < MIN_AREA: small += 1; continue
        gd = grade(area)
        feats.append({"type": "Feature", "properties": {
            "kind": "facility", "cat": "인프라", "sub": "공원·녹지", "title": f"{p['name']} ({area:,}㎡)",
            "lvl": gd, "template_type": "생활인프라",
            "current_stage": "도시계획시설(공원) " + ("조성 확장 결정" if p["expand"] else "조성 결정"),
            "phase": "인가", "status": "활성", "confirmed": False, "purp": "공원", "area_m2": area,
            "pt_type": "facility", "geo_prec": "landmark" if not (u and u[1]) else "parcel",
            "src_tier": "official_notice", "src_name": "국토부 도시계획 결정고시", "sources": [],
            "note": f"신규{'·확장' if p['expand'] else ''} 대형공원({p['name']}) · 결정고시 {p['gosi']}",
            "milestones": [{"phase": "인가", "stage": "공원조성계획 " + ("확장 결정" if p["expand"] else "결정"),
                            "date": p["gosi"], "state": "current", "src": "국토부 결정고시"},
                           {"phase": "착공", "stage": "조성", "state": "future"},
                           {"phase": "준공", "stage": "개장", "state": "future"}],
            "expand": p["expand"], "audited": datetime.date.today().isoformat()},
            "geometry": {"type": "Point", "coordinates": coord}})
        if (i + 1) % 50 == 0: print(f"  ...{i+1}/{len(parks)} 처리 (신호 {len(feats)})")
    feats.sort(key=lambda f: f["properties"]["area_m2"], reverse=True)
    json.dump({"type": "FeatureCollection",
               "_note": f"신규+확장 대형공원(5만㎡+·공원조성 결정 {SINCE}+) — 고시파일 감지 + V-World 이름매칭 좌표·면적. gosi_park.py.",
               "collected": datetime.date.today().isoformat(), "since": SINCE,
               "candidates": len(parks), "signals": len(feats), "expand": sum(1 for f in feats if f["properties"]["expand"]),
               "geocode_fail": nomatch, "below_5만": small,
               "by_grade": dict(Counter(f["properties"]["lvl"] for f in feats)), "features": feats},
              open(f"{BASE}/park_signals.json", "w"), ensure_ascii=False, indent=1)
    print(f"  → park_signals.json: {len(feats)}건 (확장 {sum(1 for f in feats if f['properties']['expand'])} · 지오코딩실패 {nomatch} · 5만미만 {small})")
    print("  등급:", dict(Counter(f["properties"]["lvl"] for f in feats)))
    for f in feats[:14]:
        p = f["properties"]; print(f"    [{p['lvl']}]{'[확장]' if p['expand'] else ''} {p['area_m2']:>9,}㎡ | {p['title']} | {f['geometry']['coordinates']}")

if __name__ == "__main__":
    main()
