#!/usr/bin/env python3
"""호재맵 데이터 QA 게이트 — DATA_STANDARD.md 자동 검사 (2026-07-17 신설).

배포 전 필수: `python3 qa_check.py` → 오류(ERR) 0이어야 배포한다.
정확성을 사람의 성실성이 아니라 절차로 보장하기 위한 장치.

검사(§9): 좌표 충돌 · 노선 내 역 중복 · 필수 필드 · OSM 단독근거 · 국외 좌표 · 정밀도 · 개통분 혼입
종료코드: 오류 있으면 1
"""
import json, math, os, sys
from collections import Counter, defaultdict

ERR, WARN = [], []
def err(cat, msg): ERR.append((cat, msg))
def warn(cat, msg): WARN.append((cat, msg))

def meters(a, b):
    return math.hypot((a[0] - b[0]) * 88800, (a[1] - b[1]) * 110540)

def load(fn):
    return json.load(open(fn)) if os.path.exists(fn) else None

def pts_of(fc, kind="station"):
    return [f for f in (fc or {}).get("features", []) if f["properties"].get("kind") == kind]

def main():
    rail = load("rail_signals.json"); road = load("road_signals.json")
    sigs = (load("signals.json") or {}).get("signals", [])
    rst = pts_of(rail); ric = pts_of(road)
    rlines = [f for f in (rail or {}).get("features", []) if f["properties"].get("kind") == "line"]
    dlines = [f for f in (road or {}).get("features", []) if f["properties"].get("kind") != "station"]

    # ── 1. 좌표 충돌: 유형이 다른 지점이 100m 내 (서로 다른 시설이 같은 점 = 거짓)
    for a in rst:
        for b in ric:
            d = meters(a["geometry"]["coordinates"], b["geometry"]["coordinates"])
            if d < 100:
                err("좌표충돌", f"{round(d)}m — 철도역 '{a['properties']['name']}'({a['properties'].get('line')}) "
                              f"↔ 도로IC '{b['properties']['name'][:30]}' : 서로 다른 시설이 같은 좌표")
    # ── 2. 같은 노선 내 역 중복 (200m)
    byline = defaultdict(list)
    for f in rst: byline[f["properties"].get("line")].append(f)
    for ln, fs in byline.items():
        for i, a in enumerate(fs):
            for b in fs[i + 1:]:
                d = meters(a["geometry"]["coordinates"], b["geometry"]["coordinates"])
                if d < 200:
                    err("역중복", f"{round(d)}m — {ln}: '{a['properties']['name']}' ↔ '{b['properties']['name']}'")
    # ── 3. 필수 필드
    for f in rst + ric:
        p = f["properties"]
        miss = [k for k in ("src_tier", "geo_prec", "pt_type") if not p.get(k)]
        if miss:
            warn("필수필드", f"{p.get('name','?')[:26]} ({p.get('line','')[:18]}) — 누락: {','.join(miss)}")
    # ── 4. OSM 단독 근거가 지도층에 존재
    # osm = 존재·단계의 근거가 OSM뿐 → 오류. osm_geom = 공식 확인된 사업의 '위치'만 OSM(연결로 실측) → 허용
    for f in dlines + rlines:
        if f["properties"].get("src_tier") == "osm":
            err("OSM단독", f"지도층에 비공식 근거: {f['properties'].get('name')}")
    # ── 5. 국외 좌표
    def foreign(x, y):
        return (x >= 129.0 and y <= 34.95) or not (124.0 < x < 132.0 and 33.0 < y < 38.7)
    for f in rst + ric:
        x, y = f["geometry"]["coordinates"]
        if foreign(x, y):
            err("국외좌표", f"{f['properties'].get('name')} [{x},{y}]")
    # ── 6. 정밀도 분포
    prec = Counter(f["properties"].get("geo_prec", "미상") for f in rst + ric)
    rough = prec.get("dong", 0) + prec.get("sgg", 0) + prec.get("미상", 0)
    tot = sum(prec.values()) or 1
    if rough / tot > 0.5:
        warn("정밀도", f"근사(dong/sgg/미상) 좌표가 {rough}/{tot} ({rough*100//tot}%) — 정밀화 필요")
    # ── 7. 개통분 혼입 (level은 호재 아님)
    for f in rlines + dlines:
        p = f["properties"]
        if p.get("phase") == "준공" or "개통 완료" in (p.get("status") or ""):
            warn("개통혼입", f"{p.get('official_name') or p.get('name')} — 준공/개통 완료가 delta층에 존재")
    # ── 7b. road_ref 병합 무결성: 확정 신호가 가리키는 도로가 실제로 존재하는가
    road_names = [(f["properties"].get("name") or "") + "|" + (f["properties"].get("official_name") or "")
                  for f in dlines]
    for s_ in sigs:
        rr = s_.get("road_ref")
        if rr and not any(rr in n for n in road_names):
            err("병합실패", f"확정신호 {s_.get('id')} road_ref='{rr}' — 매칭되는 도로 노선 없음(IC·선형 유실)")

    # ── 7c. 전용도로 영향권 미산정: 진출입 지점(IC·진입로)이 없으면 그 호재는 집계에서 통째로 빠진다.
    #        지도엔 보이는데 아파트 카운팅엔 안 잡히는 '죽은 신호' — 조용히 넘어가면 못 찾으므로 경고로 노출.
    EX_TYPES = ("고속도로", "자동차전용", "도시고속화")
    sig_pts = {}
    for f in ric:
        p = f["properties"]
        if p.get("signal") is not False:
            sig_pts.setdefault(p.get("line"), 0)
            sig_pts[p["line"]] += 1
    for f in dlines:
        p = f["properties"]
        acc = p.get("access")
        is_ramp = acc == "ramp" if acc else any(k in (p.get("official_type") or p.get("linekind") or "") for k in EX_TYPES)
        if not is_ramp: continue
        if not sig_pts.get(p["name"]):
            warn("영향권없음", f"{(p.get('official_name') or p['name'])[:44]} [{p.get('phase')}] — "
                              f"전용도로인데 진출입 지점(IC·진입로) 0 → 영향권 미산정(집계에서 빠짐)")

    # ── 7d. 공식 사업의 지도 부재: official_roads의 비개통 사업인데 road_signals에 피처가 0개.
    #        미착공이라 OSM 선형이 없고 anchors도 없으면 선도 점도 없이 통째로 사라진다(2026-07-17 발견: 29건).
    #        '공식 확인된 사업이 지도에 없다'는 조용한 탈락이므로 반드시 노출한다.
    offd = load("official_roads.json") or {}
    if offd:
        on_map = set()
        for f in (road or {}).get("features", []):
            p = f["properties"]
            for k in (p.get("official_name"), p.get("name"), p.get("line_official")):
                if k: on_map.add(k)
        for o in offd.get("roads", []):
            if o.get("opened"): continue
            if o["name"] not in on_map:
                warn("지도부재", f"{o['name'][:44]} [{o.get('phase')}] — 공식 사업인데 지도 피처 0 "
                                f"(OSM 선형·anchors 모두 없음 → official_roads.json에 anchors 추가)")

    # ── 7e. 앵커 노선 급반전(지그재그): 근사 선형에서 연속 구간이 150° 이상 꺾이며 양변이 1km를 넘으면
    #        역/경유점의 좌표·순서 오류 신호다(§3c). 2026-07-17 전수 스캔: 11개 노선 14건(5호선 김포검단 등).
    import math
    def _bear(a, b): return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))
    def _turn(a, b, c):
        t = abs(_bear(b, c) - _bear(a, b)); return t if t <= 180 else 360 - t
    for f in rlines + dlines:
        p = f["properties"]
        if not (p.get("approx") or "앵커" in (p.get("geom_src") or "") or "근사" in (p.get("geom_src") or "")): continue
        if p.get("route_verified"): continue   # 사람이 "실제 노선이 원래 이렇게 꺾임"을 확인·기록한 경우(§3c)
        cs = f["geometry"]["coordinates"]
        if f["geometry"]["type"] == "MultiLineString": cs = [c for seg in cs for c in seg]
        for i in range(1, len(cs) - 1):
            if _turn(cs[i-1], cs[i], cs[i+1]) > 150 and meters(cs[i-1], cs[i]) > 1000 and meters(cs[i], cs[i+1]) > 1000:
                warn("급반전", f"{(p.get('official_name') or p.get('name'))[:36]} — {i}번째 점에서 "
                              f"{round(_turn(cs[i-1], cs[i], cs[i+1]))}° 반전 (좌표·순서 확인 필요, §3c)")

    # ── 7f. 공원(시설) 신호 품질 — 2026-07-18 오탐 사고 절차화(사용자 지적).
    #        ① 공원명 미추출(유형라벨만) ② 좌표 없음 ③ 근거고시가 공원 고유 고시 아님(개발고시 재기록 오탐)
    park = load("park_signals.json")
    if park:
        GENERIC = ("근린공원 (", "문화공원 (", "체육공원 (", "수변공원 (", "역사공원 (", "공원 (")
        DEV = ("재개발", "재건축", "정비사업", "지적재조사", "철도", "급행", "역세권", "산업단지", "산단", "공공주택지구")
        for f in park.get("features", []):
            p = f["properties"]; t = p.get("title", "")
            if any(t.startswith(g) for g in GENERIC):
                warn("공원명", f"{t} — 공원 고유명 미추출(고시 제목에 이름 없음). 유형라벨로 표시 중")
            if not (f.get("geometry") or {}).get("coordinates"):
                err("공원좌표", f"{t} — 좌표 없음(지도 표시 불가)")
            note = p.get("note", "")
            if any(k in note for k in DEV):
                warn("공원오탐", f"{t} — 근거가 개발고시({[k for k in DEV if k in note]}) — 공원 고유 고시 아님, 오탐 의심")

    # ── 8. 확정 신호 필수 근거
    for s in sigs:
        if not s.get("src_tier"): warn("필수필드", f"확정신호 {s.get('id')} src_tier 없음")
        if not (s.get("sources") or []): warn("근거", f"확정신호 {s.get('id')} sources[] 비어있음")

    print("═" * 68)
    print(f"  호재맵 QA — 철도역 {len(rst)} · 도로IC {len(ric)} · 노선 {len(rlines)+len(dlines)} · 확정 {len(sigs)}")
    print("═" * 68)
    print(f"\n■ 좌표 정밀도: {dict(prec)}")
    if ERR:
        print(f"\n❌ 오류 {len(ERR)}건 — 배포 불가")
        for c, m in ERR[:30]: print(f"   [{c}] {m}")
        if len(ERR) > 30: print(f"   ... 외 {len(ERR)-30}건")
    else:
        print("\n✅ 오류 0 — 표준 통과")
    if WARN:
        print(f"\n⚠ 경고 {len(WARN)}건")
        for c, m in WARN[:12]: print(f"   [{c}] {m}")
        if len(WARN) > 12: print(f"   ... 외 {len(WARN)-12}건")
    return 1 if ERR else 0

if __name__ == "__main__":
    sys.exit(main())
