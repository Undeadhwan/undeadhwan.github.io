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
