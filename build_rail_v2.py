#!/usr/bin/env python3
"""철도 신호 빌더 v2 — rail_official.json(공식 SSOT) + OSM 선형 → rail_signals.json / rail_reference.json

**백지 재현 원칙(2026-07-17 사용자 확정)**: "다른 웹사이트를 새로 만들어 백지에서 실행해도 제대로 채워져야 한다."
그러려면 조사로 얻은 공식 사실이 *스크립트 안 딕셔너리*가 아니라 *데이터 파일*에 있어야 한다.
  · rail_official.json  = 공식 사실(노선 단계·마일스톤·역 명단·좌표) — git 보관, 갱신은 이 파일을 고친다
  · OSM(선택)          = 선형(그림)만. 없으면 역 앵커를 이어 근사 회랑(§7 점선 문법)
  · 이 스크립트        = 병합 + 표준 필드 부여(DATA_STANDARD: src_tier/geo_prec/pt_type/milestones)

구 build_rail.py/build_rail_all.py는 OSM raw를 전제해 재실행이 불가능해졌다(주석 참조). v2가 이를 대체한다.

사용: python3 build_rail_v2.py [--osm]   (--osm 이면 OSM 선형 재수집, 없으면 기존 rail_signals.json의 선형 재사용)
"""
import json, os, sys, ssl, time, urllib.parse, urllib.request

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
OFFICIAL = "rail_official.json"
OUT = "rail_signals.json"
REF = "rail_reference.json"
USE_OSM = "--osm" in sys.argv

def osm_line(name):
    """OSM에서 그 노선의 건설중/계획 선형 수집 (실패 시 None → 앵커 근사로 폴백)"""
    q = (f'[out:json][timeout:60];(relation["route"="railway"]["name"="{name}"];'
         f'way["railway"~"^(construction|proposed)$"]["name"="{name}"];);out geom tags;')
    try:
        req = urllib.request.Request("https://overpass-api.de/api/interpreter",
            data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "hojaemap/1.0"})
        d = json.loads(urllib.request.urlopen(req, context=CTX, timeout=70).read().decode("utf-8", "replace"))
        segs = [[[round(p["lon"], 6), round(p["lat"], 6)] for p in e["geometry"]]
                for e in d.get("elements", []) if "geometry" in e]
        return segs or None
    except Exception:
        return None

SNAP = "rail_osm_geom.json"

def snap_geom():
    """OSM 선형 스냅샷(git 보관) — 외부 의존 없이 백지에서도 선형을 채운다. 갱신은 --osm."""
    if not os.path.exists(SNAP): return {}
    try:
        return {k: v["geometry"] for k, v in json.load(open(SNAP))["lines"].items()}
    except Exception:
        return {}

def save_snap(geoms):
    """--osm 재수집 시 스냅샷도 갱신 (다음 백지 실행이 이 선형을 씀)"""
    if not geoms: return
    old = json.load(open(SNAP))["lines"] if os.path.exists(SNAP) else {}
    for k, g in geoms.items(): old[k] = {"geometry": g, "src": "OpenStreetMap(ODbL)"}
    json.dump({"_note": "OSM 철도 선형 스냅샷 — 외부 의존 제거(백지 재현). 갱신: build_rail_v2.py --osm. ODbL 출처표시.",
               "lines": old}, open(SNAP, "w"), ensure_ascii=False, separators=(",", ":"))

def main():
    off = json.load(open(OFFICIAL))
    prev = snap_geom()      # OSM 선형 스냅샷(백지에서도 존재)
    fetched = {}
    feats, ref = [], []
    stat = {"osm": 0, "anchor": 0, "snap": 0, "none": 0}

    for ln in off["lines"]:
        nm = ln["name"]
        sts = ln.get("stations") or []
        pts = [[s["lng"], s["lat"]] for s in sts]

        # ── 선형 결정: OSM(요청 시) → 기존 재사용 → 역 앵커 근사 ──
        geom = None
        if ln.get("geom") == "osm":
            if USE_OSM:
                segs = osm_line(ln.get("full") or nm)
                if segs:
                    geom = {"type": "MultiLineString", "coordinates": segs}
                    fetched[nm] = geom; stat["osm"] += 1
                time.sleep(2)
            if not geom and nm in prev:
                geom = prev[nm]; stat["snap"] += 1
        # 폴백: 선형이 없으면 역 앵커 연결(근사). 역 1개뿐이면 점 신호만(선 없음) — §7 지도 정직성
        if not geom and len(pts) >= 2:
            geom = {"type": "LineString", "coordinates": pts}; stat["anchor"] += 1
        if not geom: stat["none"] += 1

        approx = not (ln.get("geom") == "osm" and geom and stat)  # OSM 실선형이 아니면 근사
        if geom:
            feats.append({"type": "Feature", "properties": {
                "kind": "line", "name": nm, "full": ln.get("full") or nm, "linekind": ln.get("linekind"),
                "lvl": ln.get("lvl"), "phase": ln.get("phase"), "status": ln.get("status"),
                "confirmed": ln.get("confirmed"), "dashed": ln.get("dashed", True),
                "approx": ln.get("geom") != "osm", "drop": ln.get("drop"),
                "note": ln.get("note"), "src_name": ln.get("src_name"), "src_url": ln.get("src_url"),
                "src_tier": ln.get("src_tier") or "official_report",      # DATA_STANDARD §2
                "milestones": ln.get("milestones") or [],                  # §3b
                "audited": ln.get("audited"), "geom_src": "OpenStreetMap(ODbL) — 선형 참고" if ln.get("geom") == "osm" else "역 앵커 연결(근사)"},
                "geometry": geom})

        for s in sts:
            feats.append({"type": "Feature", "properties": {
                "kind": "station", "name": s["name"], "line": nm,
                "confirmed": ln.get("confirmed"), "status": ln.get("status"), "phase": ln.get("phase"),
                "st_status": s.get("st_status"), "approx": s.get("approx", True),
                "geo_prec": s.get("geo_prec") or "dong",                   # §3
                "pt_type": "rail_station",                                  # §4
                "src_tier": ln.get("src_tier") or "official_report",
                "milestones": ln.get("milestones") or [],
                "note": s.get("note"), "audited": ln.get("audited"),
                "drop": ln.get("drop")},
                "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]}})

        ref.append({"name": nm, "kind": ln.get("linekind"), "phase": ln.get("phase"), "stage": ln.get("status"),
                    "geom": "OSM 실선형" if ln.get("geom") == "osm" else ("근사 회랑(역 앵커 연결)" if len(pts) >= 2 else "대표점만"),
                    "src": ln.get("src_name") or "공식",
                    "stations": [{"name": s["name"], "lng": s["lng"], "lat": s["lat"]} for s in sts]})

    json.dump({"type": "FeatureCollection",
               "_note": "철도 신호 — rail_official.json(공식 SSOT) + 선형(OSM/앵커). build_rail_v2.py가 생성(백지 재현 가능).",
               "features": feats}, open(OUT, "w"), ensure_ascii=False, indent=1)
    json.dump({"_note": "철도 대장 — build_rail_v2.py 생성", "lines": ref}, open(REF, "w"), ensure_ascii=False, indent=1)
    nl = sum(1 for f in feats if f["properties"]["kind"] == "line")
    ns = sum(1 for f in feats if f["properties"]["kind"] == "station")
    print(f"rail_signals.json 재생성: 노선 {nl} · 역 {ns}")
    if USE_OSM: save_snap(fetched)   # 스냅샷 갱신 → 다음 백지 실행이 이 선형을 사용
    print(f"  선형: OSM수집 {stat['osm']} · 스냅샷 {stat['snap']} · 앵커근사 {stat['anchor']} · 없음(역<2) {stat['none']}")
    print(f"  마일스톤 보유 노선 {sum(1 for l in off['lines'] if l.get('milestones'))}")

if __name__ == "__main__":
    main()
