#!/usr/bin/env python3
"""국토부 도시계획 고시정보(TN_NTFC) → 신규 공원·도로 '결정 고시' 후보 발굴 (전국·결정 시점).

배경(2026-07-18 소스 검증): 신규 공원/도로를 잡는 유일한 전국 소스는 **도시계획 고시 전문**이다.
 · 전국도시공원정보 표준데이터 = 기존 공원 등록부(연 스냅샷) — 신설 판별 불가(환호·황성 등 기존공원의 재고시일이 최신으로 찍힘). 소스 부적합 확정.
 · SafeMap = 서비스 중단. 지구단위계획 API = 개발구역(주거개발용, 별건).
 → 고시정보(TN_NTFC.csv, 국토부 data.go.kr fileData)가 신규 '결정' 고시를 위치·이름과 함께 담는다.

한계(검증됨): 고시 **본문에 면적(㎡)이 없다** — 관계도서(첨부 도면)에만. 5만㎡ 컷은 자동 불가.
 → 이 스크립트는 후보(감지)만 만든다. 면적·좌표 확정은 승격 시 운영자가 고시 첨부로 확인(§3층 SSOT).

필터: '최초 결정'만(변경·실효·폐지·정정 제외), 어린이공원·소공원 제외(규모 문턱), 도로는 소로 제외.
갱신: TN_NTFC는 다운로드 스냅샷 → 주기적 재다운로드(국토부 도시계획 고시정보). 경로는 GOSI_CSV로 지정.

사용: GOSI_CSV=/path/TN_NTFC.csv python3 gosi_fetch.py
"""
import os, csv, re, json, datetime

csv.field_size_limit(10**7)
BASE = os.path.dirname(os.path.abspath(__file__))
GOSI = os.environ.get("GOSI_CSV", "").strip()
SINCE = os.environ.get("GOSI_SINCE", "2025-01-01")   # 신규 결정 하한(delta 창)
PARK_STD = os.environ.get("PARK_STD", "").strip()    # 전국도시공원 표준데이터 CSV(있으면 기존공원 네거티브 필터)

CHANGE = re.compile(r"변경|실효|폐지|정정|해제|완료 공고|준공")
PARK_SMALL = re.compile(r"어린이공원|소공원|진입로|부진입|소로|이면도로")   # 규모·부속 잡음
PARK_HIT = re.compile(r"조성계획|공원.*결정|공원 지정|국립공원|근린공원|문화공원|체육공원|수변공원|역사공원|도시자연공원")
ROAD_HIT = re.compile(r"도로.*(결정|개설|건설|신설)|우회도로|국도|지방도.*결정")
ROAD_SMALL = re.compile(r"소로|이면도로|농어촌도로|진입로|부진입|우회전차로|완료 공고")
# 단계 판정: 조성계획/도로 결정 = 계획 단계 신호 / 실시계획인가 = 착공 임박(인가)
def phase_of(t):
    if "실시계획" in t or "사업시행자 지정" in t: return "인가"
    if "조성계획" in t and "결정" in t: return "계획"
    if "결정" in t: return "계획"
    return "계획"

def loc_of(title):
    m = re.search(r"\[([^\]]+)\]", title)
    return (m.group(1).strip() if m else "")[:40]

def main():
    if not GOSI or not os.path.exists(GOSI):
        print("❌ GOSI_CSV 경로 필요 (국토부 도시계획 고시정보 TN_NTFC.csv)"); return
    # 기존 공원명 집합(표준데이터) — 있으면 '이미 존재'로 표시(신설 신뢰도 판정 보조)
    existing = set()
    if PARK_STD and os.path.exists(PARK_STD):
        for r in csv.DictReader(open(PARK_STD, encoding="cp949", errors="replace")):
            nm = (r.get("공원명") or "").strip()
            if nm: existing.add(re.sub(r"\s|공원$", "", nm))

    f = open(GOSI, encoding="cp949", errors="replace"); rd = csv.reader(f); h = next(rd)
    ti, di, ci, si = h.index("제목"), h.index("고시일자"), h.index("내용"), h.index("고시진행상태")
    park, road = [], []
    for row in rd:
        if len(row) <= ci: continue
        t, d = row[ti], row[di]
        if not (SINCE <= d <= "2099-12-31"): continue
        if CHANGE.search(t): continue                 # 변경·실효 = 신규 아님
        is_park = "공원" in t and PARK_HIT.search(t) and not PARK_SMALL.search(t)
        is_road = ROAD_HIT.search(t) and not ROAD_SMALL.search(t) and "공원" not in t
        if not (is_park or is_road): continue
        pname = re.sub(r"\s|공원$", "", re.sub(r".*[:\[(]", "", t.split("공원")[0] + "공원")) if is_park else ""
        rec = {"date": d, "title": t[:90], "loc": loc_of(t), "phase": phase_of(t),
               "status": row[si], "kind": "공원" if is_park else "도로",
               "note": "면적·좌표는 고시 첨부(관계도서)에서 승격 시 확인",
               "already_exists": bool(is_park and pname and pname in existing)}
        (park if is_park else road).append(rec)
    park.sort(key=lambda r: r["date"], reverse=True)
    road.sort(key=lambda r: r["date"], reverse=True)
    park_new = [p for p in park if not p["already_exists"]]
    json.dump({"_note": "국토부 도시계획 고시정보 → 신규 공원·도로 결정 후보(후보층). 최초결정만(변경·실효 제외). "
                        "면적 5만㎡컷·좌표는 고시 첨부로 승격 시 확인(본문 미포함). 승격: signals.json/official_roads.json.",
               "source_snapshot": os.path.basename(GOSI), "since": SINCE,
               "collected": datetime.date.today().isoformat(),
               "park": len(park), "park_new": len(park_new), "road": len(road),
               "park_items": park, "road_items": road},
              open(f"{BASE}/gosi_candidates.json", "w"), ensure_ascii=False, indent=1)
    print(f"신규 결정 고시({SINCE}~): 공원 {len(park)}건(기존공원 재고시 아닌 것 {len(park_new)}) · 도로 {len(road)}건")
    print("── 공원 신규 후보(상위) ──")
    for p in park_new[:12]:
        print(f"  {p['date']} | {p['title'][:56]}{' ['+p['loc']+']' if p['loc'] else ''}")
    print("── 도로 신규 후보(상위) ──")
    for r in road[:8]:
        print(f"  {r['date']} | {r['title'][:56]}{' ['+r['loc']+']' if r['loc'] else ''}")

if __name__ == "__main__":
    main()
