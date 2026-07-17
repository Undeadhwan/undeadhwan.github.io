#!/usr/bin/env python3
"""신규 도로 노선(호재) → §7 렌더 GeoJSON. 철도(build_rail.py)와 동일 문법 — OSM 실제 선형 자동 수집.
소스: OSM Overpass (highway=construction/proposed × motorway·trunk·primary = 고속도로·간선 신설/확장/지하화). ODbL.
IC·JC = 점 관리(철도 역과 동일 개념): construction/proposed motorway_junction 노드 + 수집 도로명 내 'IC/JC/나들목' 지명.
등급(교통>도로, LEVEL_DESC 일치): motorway(고속도로·지하화)=A / trunk(간선·대형터널)=B / primary(일반 확장·우회)=C.
사용: python3 build_road.py → road_signals.json + road_reference.json (gen_reference.py가 대장에 도로 탭 생성)
"""
import json, os, re, urllib.request, urllib.parse, ssl, socket
socket.setdefaulttimeout(180)
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
# 전국(본토+제주, 울릉·독도 제외 — 2026-07-16 전국 확장). 단일 bbox는 Overpass 504 → 위도 밴드 분할 수집.
BBOXES = ["33.00,124.60,35.00,129.75", "35.00,124.60,36.40,129.75",
          "36.40,124.60,37.40,129.75", "37.40,124.60,38.65,129.75"]
TOL = 0.00008

CLASS_LVL = {"motorway": "A", "motorway_link": "A", "trunk": "B", "trunk_link": "B", "primary": "C"}
CLASS_NM = {"motorway": "고속도로", "trunk": "간선도로", "primary": "일반도로(주요)"}
DROP = re.compile(r"램프|ramp|진입로$")
# OSM 이름 정규화: 같은 회랑이 옛 사업명/전체 사업명/구간명으로 제각각 태깅 → 대표명으로 통합(중복 호재 방지).
# 평택파주고속도로(전체 사업) 중 공사중 구간=광명~서울, 옛 사업명=수원문산 → "광명서울고속도로" 하나로.
CANON = {
    "수원문산고속도로": "광명서울고속도로(평택파주선 광명~서울 구간)",
    "평택파주고속도로": "광명서울고속도로(평택파주선 광명~서울 구간)",
    "광명서울고속도로": "광명서울고속도로(평택파주선 광명~서울 구간)",
    "39번국도 연장 (구상)": "39번 국도 연장 (구상)",
}
IC_NAME = re.compile(r"(IC|JC|나들목|분기점)$")   # 이름이 IC/JC인 way = 나들목 시설 → 점(철도 역 개념) 전환

def overpass_one(bbox):
    q = f"""[out:json][timeout:180];
(
  way["highway"="construction"]["construction"~"^(motorway|trunk|primary)"]["name"]({bbox});
  way["highway"="proposed"]["proposed"~"^(motorway|trunk|primary)"]["name"]({bbox});
  way["highway"="proposed"]["name"]({bbox})["proposed"!~"."];
  node["construction:highway"="motorway_junction"]({bbox});
  node["proposed:highway"="motorway_junction"]({bbox});
  node["highway"="motorway_junction"]["construction"]({bbox});
);
out geom tags;"""
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "hojaemap/1.0"})
    with urllib.request.urlopen(req, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def overpass():
    """밴드별 수집 → 요소 id 중복 제거 병합 (밴드 경계에 걸친 way는 양쪽에 등장)"""
    import time as _t
    seen = set(); elements = []
    for i, bb in enumerate(BBOXES):
        for attempt in range(3):
            try:
                d = overpass_one(bb); break
            except Exception as e:
                print(f"  밴드 {i} 시도{attempt+1} 실패: {e}"); _t.sleep(20)
        else:
            raise RuntimeError(f"밴드 {i} 수집 실패")
        n = 0
        for el in d.get("elements", []):
            k = (el.get("type"), el.get("id"))
            if k in seen: continue
            seen.add(k); elements.append(el); n += 1
        print(f"  밴드 {i} ({bb}): +{n}개 (누적 {len(elements)})")
        _t.sleep(8)
    return {"elements": elements}


# ── 국외 배제 필터 (2026-07-17): 전국 bbox 사각형에 일본 쓰시마·규슈 북부가 걸려 西九州自動車道 등이 수집됨.
# 시군구 폴리곤 대조는 매립지·해상교량에서 한국 도로를 오탐 제거해(국도38호선 연장 등) 부적합 →
# 지리 규칙으로 배제: 쓰시마(lat 34.0~34.8·lng 129.15~129.5)·규슈(lat<34.0·lng>129.3)는 한국 영토와 겹치지 않음.
# (한국 남동단: 거제 ~128.9E/34.7N, 부산 가덕 128.8E/35.0N — 위 영역과 경도·위도가 분리됨)
def is_foreign(x, y):
    if x >= 129.0 and y <= 34.95: return True    # 쓰시마·이키·규슈 북부
    if y < 33.05 and x > 127.0: return True      # 제주 남단 밖(일본 방면 해역)
    return False
def in_korea(pts):
    for x, y in pts[:: max(1, len(pts) // 8)]:
        if is_foreign(x, y): return False
    return True

def dp(pts, tol):
    if len(pts) < 3: return pts
    ax, ay = pts[0]; bx, by = pts[-1]; dx, dy = bx-ax, by-ay; seg = dx*dx+dy*dy or 1e-18
    dmax, idx = 0.0, 0
    for i in range(1, len(pts)-1):
        px, py = pts[i]; t = max(0, min(1, ((px-ax)*dx+(py-ay)*dy)/seg))
        d = ((px-(ax+t*dx))**2 + (py-(ay+t*dy))**2)**0.5
        if d > dmax: dmax, idx = d, i
    return dp(pts[:idx+1], tol)[:-1] + dp(pts[idx:], tol) if dmax > tol else [pts[0], pts[-1]]

def road_class(t):
    v = t.get("construction") or t.get("proposed") or ""
    for k in ("motorway", "trunk", "primary"):
        if v.startswith(k): return k
    return None


# ── 공식 오버레이 (2026-07-17): OSM 수집분에 official_roads.json(공식 SSOT)을 덮어씌운다.
#    이 단계가 없으면 재실행 시 공식 사업명·단계·마일스톤이 사라지고 OSM 원본으로 회귀한다(백지 재현 원칙).
def apply_official(feats):
    import re as _re
    if not os.path.exists("official_roads.json"): 
        print("  ⚠ official_roads.json 없음 — 공식 오버레이 생략"); return feats, []
    off = json.load(open("official_roads.json"))["roads"]
    opened = set((json.load(open("official_roads.json")).get("_opened_not_delta") or {}).get("names") or [])
    def norm(x): return _re.sub(r"[\s()·~\-—]|고속도로|고속국도|건설공사|구간|제\d+호선|지선", "", x or "")
    def match(nm):
        n = norm(nm)
        for o in off:
            for m in o.get("match", []):
                if m in n or n in norm(o["name"]): return o
        return None
    keep, dropped = [], []
    for f in feats:
        p = f["properties"]
        if p.get("kind") == "station": keep.append(f); continue
        if p["name"] in opened: dropped.append((p["name"], "개통완료(기존 도로) — delta 아님")); continue
        o = match(p["name"])
        if not o: dropped.append((p["name"], "공식 미확인 — 큐")); continue
        if o.get("opened"): dropped.append((p["name"], f"개통완료 → {o['name']}")); continue
        p["official_name"] = o["name"]; p["status"] = o["stage"]; p["phase"] = o["phase"]
        p["confirmed"] = o["phase"] in ("착공", "준공"); p["owner"] = o.get("owner")
        p["src_tier"] = "official_notice" if ("ex.co.kr" in (o.get("src") or "") or "molit" in (o.get("src") or "")) else "official_report"
        p["src_name"] = f"{o.get('owner','')} 공식 — {o['name']}"; p["src_url"] = o.get("src") or ""
        p["geom_src"] = "OpenStreetMap(ODbL) — 선형 참고"; p["pt_type"] = "road_line"
        p["milestones"] = [{"phase": o["phase"], "stage": (o["stage"] or "")[:44], "state": "current", "src": o.get("owner")}]
        p["audited"] = "2026-07-17"
        keep.append(f)
    # 공식 IC 보유 노선 → OSM 선형 미표시 플래그 (사용자 확정: IC의 '존재'가 기준)
    if os.path.exists("ic_names.json"):
        icl = {x["line"] for x in json.load(open("ic_names.json"))}
        for f in keep:
            if f["properties"].get("kind") != "station":
                f["properties"]["has_official_ic"] = f["properties"]["name"] in icl
    return keep, dropped

def main():
    data = overpass()
    lines = {}; ics = []
    for e in data.get("elements", []):
        t = e.get("tags", {}); nm = (t.get("name") or "").strip()
        if e.get("type") == "node":   # IC·JC 점(철도 역 개념)
            inm = nm or (t.get("ref") or "")
            if not inm: continue
            under = bool(t.get("construction:highway") or t.get("construction"))
            ics.append({"name": inm if re.search(r"IC|JC|나들목|분기점", inm) else inm + "IC",
                        "lng": round(e["lon"], 6), "lat": round(e["lat"], 6),
                        "confirmed": under, "status": "건설중" if under else "계획"})
            continue
        if not nm or DROP.search(nm) or "geometry" not in e: continue
        cls = road_class(t)
        if not cls: continue
        rw = "construction" if t.get("highway") == "construction" else "proposed"
        if IC_NAME.search(nm):   # IC/JC 이름의 way → 점 전환(세그먼트 중심)
            g = e["geometry"]; cx = sum(p["lon"] for p in g) / len(g); cy = sum(p["lat"] for p in g) / len(g)
            ics.append({"name": nm, "lng": round(cx, 6), "lat": round(cy, 6),
                        "confirmed": rw == "construction", "status": "건설중" if rw == "construction" else "계획"})
            continue
        nm = CANON.get(nm, nm)   # 동일 회랑 이름 통합
        key = (nm, rw)
        seg = [[round(p["lon"], 6), round(p["lat"], 6)] for p in e["geometry"]]
        if not in_korea(seg): continue   # 국외(일본 규슈·쓰시마 등 bbox 걸침) 제외
        lines.setdefault(key, {"cls": cls, "segs": [], "tunnel": t.get("tunnel") in ("yes", "building_passage") or "지하" in nm})
        lines[key]["segs"].append(dp(seg, TOL))
    # ── 일반화 중복 정리(전체관점·SSOT): 이름과 무관하게 공간 중첩으로 같은 회랑 탐지 ──
    # 규칙: X의 샘플점 90%+가 Y의 300m 내이면 ①X가 하위등급 → Y에 흡수(부속 접속도로·구간 명칭)
    #       ②동급 상호중첩 → 하나로 병합(세그먼트 합침). OSM 이름변이(CANON 밖) 자동 방어.
    import math
    def _pts(v):
        out = []
        for seg in v["segs"]: out += seg[::max(1, len(seg) // 30)]
        return out
    def _m(a, b):
        x = (b[0]-a[0])*111320*math.cos((a[1]+b[1])/2*math.pi/180); y = (b[1]-a[1])*110540
        return math.hypot(x, y)
    def _ov(A, B):
        pa, pb = _pts(A), _pts(B)
        if not pa or not pb: return 0
        return sum(1 for p in pa if min(_m(p, q) for q in pb) < 300) / len(pa)
    RANK = {"motorway": 3, "trunk": 2, "primary": 1}
    keys = list(lines.keys())
    absorbed = set()
    for i in range(len(keys)):
        for j in range(len(keys)):
            if i == j: continue
            ka, kb = keys[i], keys[j]
            if ka in absorbed or kb in absorbed: continue
            A, B = lines[ka], lines[kb]
            if _ov(A, B) < 0.9: continue
            ra, rb = RANK.get(A["cls"], 0), RANK.get(B["cls"], 0)
            if ra < rb:      # 하위등급 A가 상위 B에 포함 → 흡수
                absorbed.add(ka); print(f"  ⚠ 흡수: {ka[0]} → {kb[0]} (하위등급 접속/구간)")
            elif ra == rb and _ov(B, A) >= 0.5 and ka[1] == kb[1]:   # 동급 상호중첩 → 병합
                B["segs"] += A["segs"]; absorbed.add(ka)
                print(f"  ⚠ 병합: {ka[0]} → {kb[0]} (동급 동일 회랑)")
    for k in absorbed: lines.pop(k, None)
    # IC 중복 제거(같은 이름 여러 way/node → 좌표 평균)
    icd = {}
    for ic in ics:
        g = icd.setdefault(ic["name"], {"lngs": [], "lats": [], "confirmed": ic["confirmed"], "status": ic["status"]})
        g["lngs"].append(ic["lng"]); g["lats"].append(ic["lat"]); g["confirmed"] = g["confirmed"] or ic["confirmed"]
    ics = [{"name": n, "lng": round(sum(v["lngs"]) / len(v["lngs"]), 6), "lat": round(sum(v["lats"]) / len(v["lats"]), 6),
            "confirmed": v["confirmed"], "status": v["status"]} for n, v in icd.items()]
    feats = []; ref = []
    for (nm, rw), v in lines.items():
        lvl = CLASS_LVL.get(v["cls"], "C")
        if v["tunnel"] and v["cls"] != "motorway": lvl = "B"   # 대형터널·지하화 간선 = B(LEVEL_DESC)
        st = "건설중" if rw == "construction" else "계획"
        feats.append({"type": "Feature", "properties": {
            "kind": "line", "mode": "road", "name": nm, "linekind": CLASS_NM.get(v["cls"], "도로"),
            "lvl": lvl, "status": st, "confirmed": rw == "construction", "approx": False,
            "src": "OpenStreetMap(ODbL)", "src_url": "https://www.openstreetmap.org"},
            "geometry": {"type": "MultiLineString", "coordinates": v["segs"]}})
        ref.append({"name": nm, "kind": CLASS_NM.get(v["cls"], "도로") + (" · 지하화/터널" if v["tunnel"] else ""),
                    "phase": "착공" if rw == "construction" else "계획",
                    "stage": "건설중" if rw == "construction" else "계획(OSM proposed)",
                    "geom": "실선형(OSM)", "src": "OpenStreetMap(ODbL)",
                    "stations": [{"name": ic["name"], "lat": ic["lat"], "lng": ic["lng"]} for ic in ics
                                 if min((abs(ic["lng"] - q[0]) + abs(ic["lat"] - q[1]) for seg in v["segs"] for q in seg), default=9) < 0.02]})
    # IC 점을 가장 가까운 수집 도로선(2km 내)에 귀속, 없으면 독립
    for ic in ics:
        feats.append({"type": "Feature", "properties": {
            "kind": "station", "mode": "road", "name": ic["name"], "line": "(도로 IC)",
            "status": ic["status"], "confirmed": ic["confirmed"]},
            "geometry": {"type": "Point", "coordinates": [ic["lng"], ic["lat"]]}})
    feats, dropped = apply_official(feats)   # 공식 오버레이 — 미확인분은 큐로
    if dropped:
        json.dump({"_note": "OSM 수집분 중 공식 근거 미확인 — 지도 미표시. 공식 확인 시 official_roads.json 등재 후 복귀.",
                   "queue": [{"osm_name": n, "reason": r} for n, r in dropped]},
                  open("road_unverified_queue.json", "w"), ensure_ascii=False, indent=1)
    json.dump({"type": "FeatureCollection",
               "_note": "신규 도로 — **공식 소스(official_roads.json)로 존재·단계가 확인된 사업만**. 선형은 OSM(ODbL) 참고. build_road.py가 생성(백지 재현).",
               "features": feats}, open("road_signals.json", "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump({"_note": "도로 대장 — gen_reference.py가 rail_reference.html 도로 탭으로 렌더", "lines": ref},
              open("road_reference.json", "w"), ensure_ascii=False, indent=1)
    from collections import Counter
    print(f"도로 노선 {len(lines)}개(건설중 {sum(1 for k in lines if k[1]=='construction')}) · IC {len(ics)}개 → road_signals.json")
    for (nm, rw), v in sorted(lines.items()):
        print(f"  [{CLASS_LVL.get(v['cls'])}] {nm[:36]} · {v['cls']}{' · 터널/지하' if v['tunnel'] else ''} · {'건설중' if rw=='construction' else '계획'}")

if __name__ == "__main__":
    main()
