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
SPATIAL = "LT_C_UPISUQ153"                 # 도시계획(공간시설): 공원·녹지·광장·공공공지
SINCE = os.environ.get("UPIS_SINCE", "2023")   # 결정고시 연도 하한(delta)
MIN_AREA = 50000                           # §7b 공원 편입 문턱 5만㎡
GRADE = ((300000, "A"), (100000, "B"), (50000, "C"))
PARK_KW = ("공원",)
PARK_NO = ("어린이", "소공원", "녹지", "광장", "공공공지", "유원지 기타")

# 전국 그리드(경위도). 본토+주요 섬(제주·강화 등) 포괄. 0.4° 타일.
LNG0, LNG1, LAT0, LAT1, STEP = 126.0, 130.9, 33.1, 38.65, 0.4

def fetch_box(box, page):
    p = {"service": "data", "request": "GetFeature", "data": SPATIAL, "key": KEY, "domain": DOM,
         "format": "json", "size": "1000", "page": str(page), "geomFilter": box,
         "crs": "EPSG:4326", "attrFilter": f"dgm_ar:>=:{MIN_AREA}"}
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

def grade(a):
    for th, g in GRADE:
        if a >= th: return g
    return None

def main():
    if not KEY: print("❌ VWORLD_KEY 필요"); sys.exit(1)
    print(f"브이월드 도시계획(공간시설) 전국 순회 — 공원 5만㎡+ · 결정고시 {SINCE}+ ...")
    seen, parks = {}, []
    nb = 0
    lat = LAT0
    while lat < LAT1:
        lng = LNG0
        while lng < LNG1:
            box = f"BOX({lng:.3f},{lat:.3f},{lng+STEP:.3f},{lat+STEP:.3f})"
            page = 1
            while True:
                feats, total = fetch_box(box, page)
                if not feats: break
                for f in feats:
                    p = f["properties"]; nm = p.get("dgm_nm", "") or ""
                    if not any(k in nm for k in PARK_KW): continue
                    if any(k in nm for k in PARK_NO): continue
                    try: area = int(float(p.get("dgm_ar") or 0))
                    except: area = 0
                    if area < MIN_AREA: continue
                    m = re.search(r"NTC(\d{8})", p.get("ntfc_sn", "") or "")
                    gy = m.group(1) if m else ""
                    key = p.get("ntfc_sn") or p.get("present_sn") or (nm + str(area))
                    if key in seen: continue
                    seen[key] = 1
                    parks.append({"name": nm, "area_m2": area, "grade": grade(area),
                                  "gosi": f"{gy[:4]}-{gy[4:6]}-{gy[6:8]}" if len(gy) == 8 else "",
                                  "gosi_yr": gy[:4], "ntfc": p.get("ntfc_sn", ""),
                                  "sgg_cd": p.get("signgu_se", ""), "coord": centroid(f.get("geometry", {}))})
                if page * 1000 >= total: break
                page += 1
            nb += 1
            lng += STEP
        lat += STEP
    # delta: 결정고시 연도 하한. 미상·불가능연도(1900/미래) 제외.
    valid_yr = lambda y: y.isdigit() and SINCE <= y <= str(datetime.date.today().year)
    delta = [p for p in parks if valid_yr(p["gosi_yr"])]

    # ── 최초/변경 판별 (고시파일 조인) ── V-World는 최초·변경 결정을 구분 못 함(검증: 93%가 변경 재고시).
    # ntfc_sn == 고시파일 고시관리코드 → 제목의 '변경/실효/폐지' 여부로 진짜 신설만 남긴다. (GOSI_CSV 있을 때만)
    GOSI = os.environ.get("GOSI_CSV", "").strip()
    if GOSI and os.path.exists(GOSI):
        title = {}
        rd = csv.reader(open(GOSI, encoding="cp949", errors="replace")); hh = next(rd)
        gm, ti = hh.index("고시관리코드"), hh.index("제목")
        for row in rd:
            if len(row) > ti: title[row[gm]] = row[ti]
        CHG = re.compile(r"변경|실효|폐지|정정|해제")
        matched = miss = 0
        for p in delta:
            t = title.get(p["ntfc"])
            if t is None: p["first"] = None; miss += 1; continue
            matched += 1
            p["first"] = not bool(CHG.search(t)); p["gosi_title"] = t[:70]
        first = [p for p in delta if p.get("first")]
        print(f"  고시 조인: 매칭 {matched}·미매칭 {miss} → 최초(신설) {len(first)} · 변경/실효 제외 {len(delta)-len(first)-miss}")
        delta = first
    else:
        print("  ⚠ GOSI_CSV 미지정 → 최초/변경 판별 불가(변경 재고시 포함됨). 국토부 도시계획 고시정보 CSV 경로를 지정하세요.")
    delta.sort(key=lambda p: (p["gosi"], p["area_m2"]), reverse=True)
    from collections import Counter
    json.dump({"_note": f"브이월드 도시계획(공간시설) → 신규 대형 공원(면적·좌표·고시일 API 자동). 공원 5만㎡+·결정고시 {SINCE}+. "
                        "면적 등급 A30만/B10만/C5만. 승격 요건 자동충족(면적·좌표·고시·공식) → signals 직접 생성 가능.",
               "collected": datetime.date.today().isoformat(), "boxes": nb, "since": SINCE,
               "all_parks_5만plus": len(parks), "delta_new": len(delta),
               "by_grade": dict(Counter(p["grade"] for p in delta)), "items": delta},
              open(f"{BASE}/upis_park_candidates.json", "w"), ensure_ascii=False, indent=1)
    print(f"  전국 {nb}박스 · 공원 5만㎡+ {len(parks)}건 · 신규(결정고시 {SINCE}+) {len(delta)}건")
    print("  등급:", dict(Counter(p["grade"] for p in delta)))
    for p in delta[:16]:
        print(f"    [{p['grade']}] {p['area_m2']:>9,}㎡ | 고시 {p['gosi']} | {p['name']} | {p['coord']}")

if __name__ == "__main__":
    main()
