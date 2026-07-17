#!/usr/bin/env python3
"""재수집한 OSM 선형 + 공식 사업목록 → road_signals.json 복귀 (2026-07-17).

road_restore.py가 받아온 선형(road_restore_progress.json)을 official_roads.json(공식 SSOT)과 매칭해
**실재가 공식 확인된 사업만** 지도층에 되돌린다. OSM은 선형(그림)만 제공하고, 존재·단계·명칭은 공식값을 쓴다.
사용: python3 road_merge_restored.py
"""
import json, re

CLASS_LVL = {"motorway": "A", "trunk": "B", "primary": "C", "secondary": "C", "tertiary": "C"}

def norm(s):
    return re.sub(r"[\s()·~\-—]|고속도로|고속국도|건설공사|구간|제\d+호선|지선", "", s or "")

def main():
    prog = json.load(open("road_restore_progress.json"))
    off = json.load(open("official_roads.json"))["roads"]
    rs = json.load(open("road_signals.json"))
    qd = json.load(open("road_unverified_queue.json"))

    def match(nm):
        n = norm(nm)
        for o in off:
            for m in o.get("match", []):
                if m in n or n in norm(o["name"]): return o
        return None

    exists = {f["properties"]["name"] for f in rs["features"] if f["properties"].get("kind") != "station"}
    added, skipped = [], []
    for nm, v in prog.items():
        if not v: skipped.append((nm, "OSM 선형 없음")); continue
        if nm in exists: skipped.append((nm, "이미 존재")); continue
        o = match(nm)
        if not o: skipped.append((nm, "공식 매칭 실패")); continue
        lvl = CLASS_LVL.get(v.get("cls", "primary"), "C")
        rs["features"].append({"type": "Feature", "properties": {
            "kind": "line", "mode": "road", "name": nm, "official_name": o["name"],
            "linekind": o.get("type", "도로"), "lvl": lvl,
            "status": o["stage"], "phase": o["phase"], "confirmed": o["phase"] in ("착공", "준공"),
            "owner": o.get("owner"), "src_tier": "official_report",
            "src_name": f"{o.get('owner','')} 공식 — {o['name']}", "src_url": o.get("src") or "",
            "geom_src": "OpenStreetMap(ODbL) — 선형 참고", "pt_type": "road_line", "audited": "2026-07-17",
            "has_official_ic": False,
            "milestones": [{"phase": o["phase"], "stage": o["stage"][:44], "state": "current", "src": o.get("owner")}]},
            "geometry": {"type": "MultiLineString", "coordinates": v["segs"]}})
        added.append((nm, o["name"], o["phase"]))

    # 공식 IC 보유 재판정(복귀분 포함)
    ics = [f for f in rs["features"] if f["properties"].get("kind") == "station"]
    icline = {f["properties"].get("line") for f in ics if (f["properties"].get("name_src") or "").startswith("국토부")}
    for f in rs["features"]:
        p = f["properties"]
        if p.get("kind") != "station": p["has_official_ic"] = p["name"] in icline

    json.dump(rs, open("road_signals.json", "w"), ensure_ascii=False, separators=(",", ":"))
    done = {nm for nm, _, _ in added}
    qd["queue"] = [x for x in qd["queue"] if x["osm_name"] not in done]
    json.dump(qd, open("road_unverified_queue.json", "w"), ensure_ascii=False, indent=1)

    lines = [f for f in rs["features"] if f["properties"].get("kind") != "station"]
    print(f"■ 복귀 {len(added)}건")
    for nm, on, ph in added: print(f"   {nm[:24]:26s} → {on[:40]:42s} [{ph}]")
    print(f"■ 건너뜀 {len(skipped)}건: {skipped}")
    print(f"결과: 도로 노선 {len(lines)} · 큐 잔여 {len(qd['queue'])}")

if __name__ == "__main__":
    main()
