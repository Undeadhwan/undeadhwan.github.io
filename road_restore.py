#!/usr/bin/env python3
"""공식 확인된 도로사업의 OSM 선형 재수집 → road_signals.json 복귀 (2026-07-17).

배경: OSM 수집분 51개 중 공식 사업으로 확인된 것을 지도에 되돌린다.
      큐(road_unverified_queue.json)에는 앵커 좌표만 있어 선형을 다시 받아야 한다.
※ OSM은 **선형(그림)만** 제공한다 — 존재·단계·명칭은 official_roads.json(공식)이 SSOT.
사용: python3 road_restore.py   (진행상황 road_restore_progress.json에 증분 저장 → 재실행 시 이어받기)
"""
import json, os, ssl, time, urllib.parse, urllib.request

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
PROG = "road_restore_progress.json"
TARGET = ["울산외곽순환고속도로", "서산영덕고속도로", "당진청주고속도로", "익산평택고속도로",
          "동해고속도로", "동해고속도로(속초-고성)", "팔공산 관통 고속도로", "광주외곽순환고속도로",
          "광주북부순환도로 1공구", "광주북부순환도로-호남고속도로연결로(2029예정)", "호남고속도로",
          "마산거제간도로", "용진-우아간", "다사~왜관간 광역도로", "다사-왜관간 광역도로",
          "상화로 지하도로", "농소~강동 도로", "행복대로", "검단-경명로간 도로",
          "동탄-천리 지방도", "국도47호선 지하화 이설구간", "국도 제38호선 연장"]

def ov(nm):
    q = f'[out:json][timeout:60];way["highway"~"^(construction|proposed)$"]["name"="{nm}"](33.0,124.6,38.65,129.75);out geom tags;'
    for a in range(3):
        try:
            req = urllib.request.Request("https://overpass-api.de/api/interpreter",
                data=urllib.parse.urlencode({"data": q}).encode(), headers={"User-Agent": "hojaemap/1.0"})
            return json.loads(urllib.request.urlopen(req, context=CTX, timeout=70).read().decode("utf-8", "replace"))
        except Exception:
            time.sleep(10)
    return {"elements": []}

def dp(pts, tol=0.00008):
    if len(pts) < 3: return pts
    ax, ay = pts[0]; bx, by = pts[-1]; dx, dy = bx - ax, by - ay; seg = dx * dx + dy * dy or 1e-18
    dmax, idx = 0.0, 0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]; t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / seg))
        d = ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5
        if d > dmax: dmax, idx = d, i
    return dp(pts[:idx + 1], tol)[:-1] + dp(pts[idx:], tol) if dmax > tol else [pts[0], pts[-1]]

def main():
    out = json.load(open(PROG)) if os.path.exists(PROG) else {}
    q = {x["osm_name"] for x in json.load(open("road_unverified_queue.json"))["queue"]}
    todo = [t for t in TARGET if t in q and t not in out]
    print(f"복귀 대상 {len(todo)} (완료분 {len(out)})")
    for nm in todo:
        d = ov(nm)
        els = [e for e in d.get("elements", []) if e.get("tags", {}).get("name") == nm and "geometry" in e]
        if not els:
            out[nm] = None; print(f"  {nm[:26]:28s} — OSM way 0")
        else:
            t = els[0]["tags"]
            out[nm] = {"segs": [dp([[round(p["lon"], 6), round(p["lat"], 6)] for p in e["geometry"]]) for e in els],
                       "rw": "construction" if t.get("highway") == "construction" else "proposed",
                       "cls": (t.get("construction") or t.get("proposed") or "primary").split("_")[0]}
            print(f"  {nm[:26]:28s} way {len(els)}")
        json.dump(out, open(PROG, "w"))   # 증분 저장
        time.sleep(3)
    ok = {k: v for k, v in out.items() if v}
    print(f"완료: 선형 확보 {len(ok)} / 시도 {len(out)}")

if __name__ == "__main__":
    main()
