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
RAMPS = "road_ramps.json"
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
    used_official = set()
    for nm, v in snap.items():
        if nm in opened_names:
            queue.append({"osm_name": nm, "reason": "개통완료(기존 도로) — delta 아님"}); continue
        o = match(nm)
        if not o:
            queue.append({"osm_name": nm, "reason": "공식 미확인 — 조사 대상"}); continue
        if o.get("opened"):
            queue.append({"osm_name": nm, "reason": f"개통완료 → {o['name']}"}); continue
        has_ic = nm in ic_lines
        used_official.add(o["name"])
        feats.append({"type": "Feature", "properties": {
            "kind": "line", "mode": "road", "name": nm, "official_name": o["name"],
            "linekind": v.get("linekind") or o.get("type", "도로"),
            "official_type": o.get("type"),      # 접근 유형 판정 기준(전용/일반) — OSM 이름보다 정확
            "access": o.get("access"), "access_note": o.get("access_note"),   # 영향권 방식 명시(ramp/line) — type 추론의 예외
            "lvl": v.get("lvl", "C"),
            "status": o["stage"], "phase": o["phase"], "confirmed": o["phase"] in ("착공", "준공"),
            "owner": o.get("owner"),
            "src_tier": "official_notice" if any(k in (o.get("src") or "") for k in ("ex.co.kr", "molit", "고시")) else "official_report",
            "src_name": f"{o.get('owner','')} 공식 — {o['name']}", "src_url": o.get("src") or "",
            "geom_src": "OpenStreetMap(ODbL) — 선형 참고", "pt_type": "road_line",
            "has_official_ic": has_ic,          # True면 UI가 OSM 선형을 숨김(§2b)
            "milestones": [{"phase": o["phase"], "stage": (o["stage"] or "")[:44], "state": "current", "src": o.get("owner")}],
            "audited": "2026-07-17"},
            "geometry": v["geometry"]})

    # ── OSM 선형이 없는 공식 사업 → anchors(공식 경유 대표점)로 근사 회랑 ──
    # 미착공 사업은 OSM에 construction 태그가 없어 선형이 존재하지 않는다(구조적).
    # 사업이 공식 확인됐는데 지도에서 빠지면 안 되므로, official_roads.json의 anchors로 근사선을 만든다(§7 점선 문법).
    for o in off:
        if o["name"] in used_official or o.get("opened") or not o.get("anchors"): continue
        a = o["anchors"]
        if len(a) < 2: continue
        feats.append({"type": "Feature", "properties": {
            "kind": "line", "mode": "road", "name": o["name"], "official_name": o["name"],
            "linekind": o.get("type", "도로"), "official_type": o.get("type"),
            "access": o.get("access"), "access_note": o.get("access_note"),
            "lvl": "A" if o.get("type") == "고속도로" else "B",
            "status": o["stage"], "phase": o["phase"], "confirmed": o["phase"] in ("착공", "준공"),
            "owner": o.get("owner"), "src_tier": "official_report",
            "src_name": f"{o.get('owner','')} 공식 — {o['name']}", "src_url": o.get("src") or "",
            "geom_src": "공식 경유 대표점 연결(근사) — 미착공이라 실선형 미존재", "approx": True,
            "pt_type": "road_line", "has_official_ic": o["name"] in ic_lines,
            "note": o.get("anchor_note"),
            "milestones": [{"phase": o["phase"], "stage": (o["stage"] or "")[:44], "state": "current", "src": o.get("owner")}],
            "audited": "2026-07-17"},
            "geometry": {"type": "LineString", "coordinates": a}})

    # ── 공식 IC 명단 → 점 신호 ──
    # **JC(분기점)는 호재가 아니다**(2026-07-17 사용자 지적): 고속도로끼리 연결하는 지점이라
    # 일반 차량이 진출입할 수 없다 → 그 동네의 접근성을 개선하지 않는다(§7 거리≠수혜).
    # IC(나들목)만 진출입 가능 = 호재. JC는 노선의 접속 구조 사실로만 보존(signal:false → 지도·영향권 제외).
    line_by_name = {f["properties"]["name"]: f["properties"] for f in feats}
    n_ic = n_jc = 0
    for x in ics:
        lp = line_by_name.get(x["line"])
        if not lp:
            continue   # 노선이 지도에 없으면 IC도 생략(고아 방지)
        is_sig = x.get("signal", True)          # JC=False → 호재 아님
        feats.append({"type": "Feature", "properties": {
            "kind": "station", "mode": "road", "name": x["name"], "line": x["line"],
            "lvl": lp.get("lvl", "A"), "status": lp.get("status"), "confirmed": lp.get("confirmed"),
            "linekind": "IC" if is_sig else "JC(분기점)", "name_src": x.get("src", "국토부 고시·보도"),
            "ic_status": x.get("status"), "loc": x.get("loc"),
            "signal": is_sig,                                        # False = 지도·영향권 제외(호재 아님)
            "pt_role": x.get("pt_role", "ic"),
            "pt_type": "road_ic" if is_sig else "road_jc",           # §4 — 역과 시각 구분
            "geo_prec": "dong" if "대표점" in (x.get("loc") or "") else "parcel",   # §3
            "src_tier": "official_notice",
            "milestones": lp.get("milestones") or [],
            "audited": "2026-07-17"},
            "geometry": {"type": "Point", "coordinates": [x["lng"], x["lat"]]}})
        if is_sig: n_ic += 1
        else: n_jc += 1

    # ── 진입로(OSM 연결로 클러스터) 병합 — 공식 IC 명단이 없는 **전용도로**의 영향권 주체 ──
    # 전용도로(고속도로·자동차전용·고속화도로)는 진출입 지점에서만 타고 내린다. 공식 IC 명단이 없으면
    # 영향권이 통째로 죽으므로(2026-07-17 점검: 24개 중 16개), OSM 연결로가 모이는 지점 = 진입로(위치=사실)로 채운다.
    # 이름은 미상 → "…진입로(이름 미상)"로 정직 표기(§P2).
    n_ramp = 0
    if os.path.exists(RAMPS):
        EXCL = ("고속도로", "자동차전용", "도시고속화")
        for r in json.load(open(RAMPS))["ramps"]:
            lp = line_by_name.get(r["line"])
            if not lp: continue
            if r["line"] in ic_lines: continue            # 공식 IC 명단이 있으면 그쪽이 SSOT
            acc = lp.get("access")
            t = (lp.get("official_type") or lp.get("linekind") or "")
            is_ramp = acc == "ramp" if acc else any(k in t for k in EXCL)
            if not is_ramp: continue    # 선 회랑이 영향권인 도로는 진입로 점 불필요
            feats.append({"type": "Feature", "properties": {
                "kind": "station", "mode": "road", "name": f"{(lp.get('official_name') or r['line'])[:22]} 진입로(이름 미상)",
                "line": r["line"], "lvl": lp.get("lvl", "A"), "status": lp.get("status"),
                "confirmed": lp.get("confirmed"), "linekind": "진입로", "name_src": "OSM 연결로 클러스터",
                "signal": True, "pt_role": "ramp", "pt_type": "road_ic",
                "geo_prec": "landmark", "src_tier": "osm_geom",   # 위치는 사실(연결로), 이름만 미상
                "milestones": lp.get("milestones") or [], "ramps": r.get("ramps"), "audited": "2026-07-17"},
                "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]}})
            n_ramp += 1

    json.dump({"type": "FeatureCollection",
               "_note": "신규 도로 — 공식 소스(official_roads.json)로 존재·단계가 확인된 사업만. 선형=OSM 스냅샷(그림). "
                        "전용도로는 진출입 지점(공식 IC 또는 OSM 진입로)이 영향권 주체. build_road_v2.py 생성(백지 재현).",
               "features": feats}, open(OUT, "w"), ensure_ascii=False, separators=(",", ":"))

    # 대장
    used = set()
    ic_by_line = {}
    for f in feats:
        p = f["properties"]
        if p.get("kind") == "station":
            ic_by_line.setdefault(p["line"], []).append({"name": p["name"] + ("" if p.get("signal", True) else " · 분기점(호재 아님)"),
                                                         "lng": f["geometry"]["coordinates"][0],
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
    print(f"road_signals.json 재생성: 노선 {nl} · **IC(호재) {n_ic}** · JC(제외) {n_jc} · **진입로(OSM) {n_ramp}**")
    print(f"  공식 IC 보유(선형 숨김) {sum(1 for f in feats if f['properties'].get('has_official_ic'))} · 큐 {len(queue)}")

if __name__ == "__main__":
    main()
