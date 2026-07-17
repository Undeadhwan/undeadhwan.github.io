#!/usr/bin/env python3
"""도로 신호 빌더 v2 — 공식 SSOT + OSM 선형 스냅샷 → road_signals.json / road_reference.json

**백지 재현 원칙(2026-07-17 사용자 확정)**: git clone → 이 스크립트 → 데이터 완성. 외부 API 없이도 동작한다.
  · official_roads.json  = 공식 사업 목록(존재·단계·주체) — SSOT. 갱신은 이 파일을 고친다
  · ic_names.json        = 공식 IC 명단(고시·보도) — IC의 **존재**가 곧 사업의 공식 근거(§2b)
  · road_osm_geom.json   = OSM 선형 스냅샷(그림만). 갱신: build_road.py(--fetch 경로)
  · 이 스크립트          = 병합 + 표준 필드(src_tier/geo_prec/pt_type/milestones)

규칙(DATA_STANDARD):
  · 공식 목록에 없는 OSM 노선 = 지도에 올리지 않는다 → road_unverified_queue.json (§2)
  · 개통완료(_opened_not_delta)는 호재 아님 → 제외 (§7 delta만)
  · **공식 IC가 존재하는 노선은 OSM 선형 미표시**(has_official_ic) — IC 점이 정확한 표현 (§2b)

사용: python3 build_road_v2.py
"""
import json, os, re

SNAP = "road_osm_geom.json"
OFFICIAL = "official_roads.json"
ICS = "ic_names.json"
OUT = "road_signals.json"
REF = "road_reference.json"
QUEUE = "road_unverified_queue.json"

def norm(s):
    return re.sub(r"[\s()·~\-—]|고속도로|고속국도|건설공사|구간|제\d+호선|지선", "", s or "")

def main():
    if not os.path.exists(SNAP):
        print(f"❌ {SNAP} 없음 — build_road.py로 OSM 선형을 먼저 수집하세요"); return
    snap = json.load(open(SNAP))["lines"]
    offd = json.load(open(OFFICIAL))
    off = offd["roads"]
    opened_names = set((offd.get("_opened_not_delta") or {}).get("names") or [])
    ics = json.load(open(ICS)) if os.path.exists(ICS) else []
    ic_lines = {x["line"] for x in ics}

    def match(nm):
        n = norm(nm)
        for o in off:
            for m in o.get("match", []):
                if m in n or n in norm(o["name"]): return o
        return None

    feats, ref, queue = [], [], []
    for nm, v in snap.items():
        if nm in opened_names:
            queue.append({"osm_name": nm, "reason": "개통완료(기존 도로) — delta 아님"}); continue
        o = match(nm)
        if not o:
            queue.append({"osm_name": nm, "reason": "공식 미확인 — 조사 대상"}); continue
        if o.get("opened"):
            queue.append({"osm_name": nm, "reason": f"개통완료 → {o['name']}"}); continue
        has_ic = nm in ic_lines
        feats.append({"type": "Feature", "properties": {
            "kind": "line", "mode": "road", "name": nm, "official_name": o["name"],
            "linekind": v.get("linekind") or o.get("type", "도로"), "lvl": v.get("lvl", "C"),
            "status": o["stage"], "phase": o["phase"], "confirmed": o["phase"] in ("착공", "준공"),
            "owner": o.get("owner"),
            "src_tier": "official_notice" if any(k in (o.get("src") or "") for k in ("ex.co.kr", "molit", "고시")) else "official_report",
            "src_name": f"{o.get('owner','')} 공식 — {o['name']}", "src_url": o.get("src") or "",
            "geom_src": "OpenStreetMap(ODbL) — 선형 참고", "pt_type": "road_line",
            "has_official_ic": has_ic,          # True면 UI가 OSM 선형을 숨김(§2b)
            "milestones": [{"phase": o["phase"], "stage": (o["stage"] or "")[:44], "state": "current", "src": o.get("owner")}],
            "audited": "2026-07-17"},
            "geometry": v["geometry"]})

    # ── 공식 IC 명단 → 점 신호 (IC의 존재 = 공식 근거) ──
    line_by_name = {f["properties"]["name"]: f["properties"] for f in feats}
    n_ic = 0
    for x in ics:
        lp = line_by_name.get(x["line"])
        if not lp:
            continue   # 노선이 지도에 없으면 IC도 생략(고아 방지)
        feats.append({"type": "Feature", "properties": {
            "kind": "station", "mode": "road", "name": x["name"], "line": x["line"],
            "lvl": lp.get("lvl", "A"), "status": lp.get("status"), "confirmed": lp.get("confirmed"),
            "linekind": "IC·JC", "name_src": x.get("src", "국토부 고시·보도"),
            "ic_status": x.get("status"), "loc": x.get("loc"),
            "pt_type": "road_ic",                                    # §4 — 역과 시각 구분
            "geo_prec": "dong" if "대표점" in (x.get("loc") or "") else "parcel",   # §3
            "src_tier": "official_notice",
            "milestones": lp.get("milestones") or [],
            "audited": "2026-07-17"},
            "geometry": {"type": "Point", "coordinates": [x["lng"], x["lat"]]}})
        n_ic += 1

    json.dump({"type": "FeatureCollection",
               "_note": "신규 도로 — 공식 소스(official_roads.json)로 존재·단계가 확인된 사업만. 선형=OSM 스냅샷(그림). "
                        "공식 IC가 있으면 IC 점이 기준(has_official_ic). build_road_v2.py 생성(백지 재현).",
               "features": feats}, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))

    # 대장
    used = set()
    ic_by_line = {}
    for f in feats:
        p = f["properties"]
        if p.get("kind") == "station":
            ic_by_line.setdefault(p["line"], []).append({"name": p["name"], "lng": f["geometry"]["coordinates"][0],
                                                         "lat": f["geometry"]["coordinates"][1], "src": p.get("name_src")})
    for f in feats:
        p = f["properties"]
        if p.get("kind") == "station": continue
        st = ic_by_line.get(p["name"], []) if p["name"] not in used else []
        if st: used.add(p["name"])
        ref.append({"name": p.get("official_name") or p["name"],
                    "kind": (p.get("linekind") or "도로") + (f" · {p.get('owner')}" if p.get("owner") else ""),
                    "phase": p.get("phase"), "stage": p.get("status"),
                    "geom": "IC 점 기준(공식) — OSM 선형 미표시" if p.get("has_official_ic") else "실선형(OSM 참고) · 위치·단계=공식",
                    "src": p.get("src_name") or "공식", "stations": st})
    json.dump({"_note": "도로 대장 — build_road_v2.py 생성", "lines": ref}, open(REF, "w"), ensure_ascii=False, indent=1)
    json.dump({"_note": "OSM 수집분 중 공식 근거 미확인·개통완료 — 지도 미표시. 공식 확인 시 official_roads.json 등재 후 자동 복귀.",
               "queue": queue}, open(QUEUE, "w"), ensure_ascii=False, indent=1)

    nl = sum(1 for f in feats if f["properties"]["kind"] == "line")
    print(f"road_signals.json 재생성: 노선 {nl} · IC {n_ic}")
    print(f"  공식 IC 보유(선형 숨김) {sum(1 for f in feats if f['properties'].get('has_official_ic'))} · 큐 {len(queue)}")

if __name__ == "__main__":
    main()
