#!/usr/bin/env python3
"""신설 도로의 **진출입 지점(IC·진입로)** 도출 → road_ramps.json 스냅샷 (build_road_v2가 병합).

배경(2026-07-17 사용자 지적: "광명서울고속도로 IC가 표시 안 된다"):
전용도로(고속도로 등)는 영향권이 IC 기준인데(§6-2 도로 이원 관리), OSM에 IC 이름 노드가 거의 없어
IC=0 → 영향권 미산정 상태였다. 그러나 OSM에는 **건설중 연결로(construction=*_link) way가 600여 개** 있고,
연결로가 모이는 지점 = IC·JC라는 것은 도로 구조상의 사실이다. 이를 클러스터링해 지점을 도출한다.

- 위치 = OSM 연결로 클러스터 중심(사실). 이름 = OSM에 없음 → ic_names.json(공식 보도 조사분)으로 근접 매칭.
  이름을 못 찾으면 "OO 접속부(이름 미상)"로 정직 표기(§P2) — 위치는 사실, 이름만 미상.
- 노선 귀속 = road_signals.json의 노선 선형 중 클러스터에서 가장 가까운 것(600m 이내). 그 외는 버림(무관 램프).

전용도로(고속도로·자동차전용·**고속화도로**)는 진출입 지점에서만 타고 내린다 → 그 지점이 영향권의 주체.
공식 IC 명단(ic_names.json)이 없는 노선은 **OSM 연결로 클러스터 = 진입로(사실)**로 채운다. 이름은 미상이나 위치는 사실.
(2026-07-17 사용자: "올림픽대로·내부순환로 같은 고속화도로도 IC라 안 하지만 진입로를 IC처럼 호재로")

사용: python3 build_road_ic.py   → road_ramps.json (백지 재현용 스냅샷)
"""
import json, math, os, ssl, urllib.request, urllib.parse

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
BBOXES = ["33.00,124.60,35.00,129.75", "35.00,124.60,36.40,129.75",
          "36.40,124.60,37.40,129.75", "37.40,124.60,38.65,129.75"]
CLUSTER_M = 1600     # 이 거리 안의 연결로는 같은 IC로 묶음(나들목 1곳의 램프가 넓게 퍼짐)
MIN_RAMPS = 2        # 램프 1개짜리 클러스터는 IC로 보기 약함(단순 진출입·노이즈) → 제외
ATTACH_M = 700       # 노선 선형에서 이 거리 안이어야 그 노선의 IC로 귀속
NAME_M = 2500        # 공식 IC 좌표와 이 거리 안이면 이름 부여

def meters(a, b):
    dx = (a[0] - b[0]) * 88800; dy = (a[1] - b[1]) * 110540
    return math.hypot(dx, dy)

def ov(bbox):
    q = f"""[out:json][timeout:180];
(
  way["highway"="construction"]["construction"~"link$"]({bbox});
  way["highway"="proposed"]["proposed"~"link$"]({bbox});
);
out center tags;"""
    req = urllib.request.Request("https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "hojaemap/1.0"})
    with urllib.request.urlopen(req, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def main():
    import time
    pts = []
    for i, bb in enumerate(BBOXES):
        for attempt in range(3):
            try:
                d = ov(bb); break
            except Exception as e:
                print(f"  밴드{i} 재시도 {attempt+1}: {e}"); time.sleep(20)
        else:
            print(f"  밴드{i} 실패 — 건너뜀"); continue
        for e in d.get("elements", []):
            c = e.get("center")
            if c: pts.append(([round(c["lon"], 6), round(c["lat"], 6)], e.get("tags", {})))
        print(f"  밴드{i}: 연결로 누적 {len(pts)}")
        time.sleep(8)

    # 1) 연결로 → IC 클러스터
    clusters = []
    for c, t in pts:
        hit = None
        for cl in clusters:
            if meters(c, cl["c"]) < CLUSTER_M: hit = cl; break
        if hit:
            hit["pts"].append(c)
            hit["c"] = [sum(p[0] for p in hit["pts"]) / len(hit["pts"]), sum(p[1] for p in hit["pts"]) / len(hit["pts"])]
            if t.get("destination") and not hit.get("dest"): hit["dest"] = t["destination"]
        else:
            clusters.append({"c": c, "pts": [c], "dest": t.get("destination")})
    print(f"연결로 {len(pts)} → IC 클러스터 {len(clusters)}")

    # 2) 노선 귀속
    rs = json.load(open("road_signals.json"))
    lines = [f for f in rs["features"] if f["properties"].get("kind") != "station"]
    def line_pts(f):
        g = f["geometry"]
        segs = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        return [p for seg in segs for p in seg]
    lp = [(f, line_pts(f)) for f in lines]

    names = []
    if os.path.exists("ic_names.json"):
        names = json.load(open("ic_names.json"))   # [{"line":..,"name":..,"lng":..,"lat":..,"type":..,"status":..}]
        print(f"공식 IC 명단 {len(names)}건 로드")

    out = []
    # 0) 공식 명단(고시·보도) = 1급 소스 — 그대로 IC 점 등록. OSM 클러스터는 명단 없는 노선의 보조.
    named_lines = set()
    line_prop = {f["properties"]["name"]: f["properties"] for f in lines}
    for n in names:
        p = line_prop.get(n["line"])
        if not p:
            print(f"  ⚠ 명단의 노선이 수집분에 없음: {n['line']} / {n['name']}"); continue
        named_lines.add(n["line"])
        out.append({"type": "Feature",
            "properties": {"kind": "station", "name": n["name"], "mode": "road", "line": n["line"],
                           "lvl": p.get("lvl", "A"), "status": p.get("status"), "confirmed": p.get("confirmed", False),
                           "linekind": "IC·JC", "name_src": n.get("src", "공식 고시·보도"),
                           "ic_status": n.get("status"), "loc": n.get("loc"), "audited": "2026-07-17"},
            "geometry": {"type": "Point", "coordinates": [n["lng"], n["lat"]]}})
    for cl in clusters:
        best, bd = None, 9e9
        for f, ps in lp:
            d = min(meters(cl["c"], p) for p in ps)
            if d < bd: bd, best = d, f
        if bd > ATTACH_M: continue           # 신설 노선과 무관한 연결로
        if len(cl["pts"]) < MIN_RAMPS: continue   # 램프 부족 = IC 근거 약함(§P2 — 추정 표시 금지)
        p = best["properties"]
        if p["name"] in named_lines: continue     # 공식 명단이 있는 노선은 명단이 SSOT — 중복 방지
        # 이름: OSM destination → 미상 (명단 매칭은 위에서 처리)
        nm, src = None, None
        if cl.get("dest"):
            nm, src = f"{cl['dest']} 방면 접속부", "OSM destination"
        if not nm:
            nm, src = f"{p['name']} 접속부 (이름 미상)", "OSM 연결로 클러스터"
        out.append({"type": "Feature",
            "properties": {"kind": "station", "name": nm, "mode": "road", "line": p["name"],
                           "lvl": p.get("lvl", "A"), "status": p.get("status"), "confirmed": p.get("confirmed", False),
                           "linekind": "IC·JC", "name_src": src, "ramps": len(cl["pts"]), "audited": "2026-07-17"},
            "geometry": {"type": "Point", "coordinates": [round(cl["c"][0], 6), round(cl["c"][1], 6)]}})

    # 램프 클러스터 = 진입로 스냅샷 (공식 IC 명단이 없는 노선용). build_road_v2.py가 읽어 병합.
    ramps = [{"line": f["properties"]["line"], "name": f["properties"]["name"],
              "lng": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1],
              "ramps": f["properties"].get("ramps"), "src": f["properties"].get("name_src")}
             for f in out if f["properties"].get("name_src") != "국토부 고시·보도(2026-07 조사)"]
    json.dump({"_note": "OSM 연결로 클러스터 → 진출입 지점(진입로) 스냅샷. 위치=사실(연결로가 모이는 지점), 이름=미상. "
                        "공식 IC 명단(ic_names.json)이 있는 노선은 제외. build_road_v2.py가 병합. 갱신: python3 build_road_ic.py",
               "ramps": ramps}, open("road_ramps.json", "w"), ensure_ascii=False, indent=1)
    from collections import Counter
    print(f"진입로(램프 클러스터) {len(ramps)}개 → road_ramps.json")
    print("  노선별:", dict(Counter(f["properties"]["line"] for f in out).most_common(10)))
    print("  이름 출처:", dict(Counter(f["properties"]["name_src"] for f in out)))

if __name__ == "__main__":
    main()
