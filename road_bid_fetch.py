#!/usr/bin/env python3
"""나라장터 공사 입찰공고 → 시도/시군구 발주 도로·공원 사업 후보 발굴 (전국·발주 시점·완전 자동).

배경: 시도 발주 간선도로·연결도로·지하차도, 신규 공원 조성은 OSM·국토부 고속도로목록·고시파일(반기) 그물을
다 빠져나가거나 느리다(방화대로 누락 2026-07-18). 나라장터 공사공고가 우리 키로 즉시 동작하고 **매일 갱신**되며
**사업비(bdgtAmt·추정가격)를 제공**한다 — 규모(금액)로 간선/마을길·유지보수를 자동 판별할 수 있는 유일한 전국 소스.

**승격 기준(2026-07-18 사용자 확정 — 금액 OR 면적으로 규모 확인)**: 사업비(bdgtAmt) 기준 자동 등급.
 · 도로: A≥300억 / B≥100억 / C≥30억 / <30억=노이즈 제외
 · 공원: A≥200억 / B≥50억  / C≥10억 / <10억=노이즈 제외
 등급이 산정되면(문턱 이상) '승격가능', 미만은 제외. **사람 재량 없음 — 금액이 규칙으로 결정.**

위치: 3층 SSOT의 **후보층**. bid_candidates.json(후보)만 생성 → 사람은 좌표·경유지 확인 후
official_roads.json/signals.json으로 승격(등급·존재·금액은 자동, 좌표만 승격 시). 기존 등재분은 '기존' 표시.

라이선스: 나라장터 공사공고 = 공공누리 자유이용(상업 OK).

사용: DATA_KEY=통합키 python3 road_bid_fetch.py [--days 30]
"""
import os, sys, json, re, ssl, time, urllib.request, urllib.parse, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
KEY = os.environ.get("DATA_KEY", "").strip()
API = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService/getBidPblancListInfoCnstwkPPSSrch"

# 도로 신설·개량 = 호재. 유지보수·부대공사·시설부속도로는 신호 아님 → 제외.
ROAD_INC = ("도로개설", "도로 개설", "도로신설", "도로 신설", "연결도로", "우회도로", "국도대체우회",
           "도로확장", "도로 확장", "지하차도", "입체화", "교량가설", "대교 건설", "도로건설", "도로 건설",
           "도로개량", "간선도로", "광역도로", "도시고속화", "도시계획도로")
# 공원 신규 조성 = 호재(나라장터 조성공사 발주분 — 금액 자동 등급). 유지·부속은 제외.
PARK_INC = ("공원조성", "공원 조성", "근린공원", "문화공원", "체육공원", "수변공원", "역사공원",
            "도시숲", "호수공원", "생태공원", "테마공원")
PARK_SMALL_KW = ("어린이공원", "소공원", "쌈지", "포켓")
INCLUDE = ROAD_INC + PARK_INC
# 사업비(원) → 등급. 승격 문턱 미만은 노이즈(제외). 유형별(공원이 도로보다 저비용).
GRADE = {"도로": ((30_000_000_000, "A"), (10_000_000_000, "B"), (3_000_000_000, "C")),
         "공원": ((20_000_000_000, "A"), (5_000_000_000, "B"), (1_000_000_000, "C"))}
def grade_of(kind, amt):
    for th, g in GRADE[kind]:
        if amt >= th: return g
    return None   # 문턱 미만 = 제외
# 유지보수·부대·부속 잡음(30일 실측에서 확인 2026-07-18): 배터리교체·방재·재선충·주차장진입로·캠퍼스순환로 등
EXCLUDE = ("보수", "유지", "안전점검", "정밀안전", "전기공사", "정보통신", "통신공사", "조경", "청소",
           "제설", "포장보수", "표지판", "신호등", "가로등", "점검", "관리용역", "설계용역", "감리",
           "교체", "방재", "재선충", "배터리", "선로", "영상감시", "노후", "재포장", "부대", "소방",
           "주차장", "복지관", "캠퍼스", "힐링센터", "요양", "체육관", "저수지", "배수", "하수",
           "우수", "정비사업", "옹벽", "사면", "낙석",
         "유도등", "석면", "차선체계", "폐기물", "해체", "침수", "비상대피", "감시장치", "선로교체")
# 소로(소1-·소2-·소3-)는 이면도로 수준 — 간선 호재 아님. 중로 이상만.
SORO = re.compile(r"소[123]-|소로\d")
# 규모 잡음(30일 실측 2026-07-18): 마을길·시설진입로·자전거도로 등 지방 소규모는 호재 아님(§규모 문턱).
NOISE = ("마을", "진입도로", "보건지소", "정수사업소", "주유소", "자전거도로", "지장물", "철거",
         "정비공사", "확포장", "보행자도로", "후문", "광장", "체험장", "복구사업")
# 간선급 우선순위(★): 도시계획 대/중로, 국도·국지도·지방도 번호, 지역~지역 연결 = 사람이 먼저 볼 후보.
ARTERIAL = re.compile(r"대로\d|중로?\d|중\d-|대\d-|국도\d|국지도\d|지방도\d|[가-힣]+~[가-힣]+간|[가-힣]{2,}~[가-힣]{2,}")

def norm(s):
    s = re.sub(r"\([^)]*\)$", "", s or "")   # 말미 (전기)/(토목)/(통신) 부대 표기 제거 → 같은 사업 통합
    return re.sub(r"[\s()\[\]·~\-—,]|공사$|건설공사|외$|제\d+공구|\d+공구", "", s)

def fetch_window(beg, end):   # 단일 ≤30일 창 (나라장터 조회범위 제한)
    out, page = [], 1
    while True:
        q = urllib.parse.urlencode({
            "serviceKey": KEY, "type": "json", "inqryDiv": "1", "numOfRows": "300", "pageNo": page,
            "inqryBgnDt": beg.strftime("%Y%m%d") + "0000", "inqryEndDt": end.strftime("%Y%m%d") + "2359"})
        for attempt in range(3):
            try:
                req = urllib.request.Request(API + "?" + q, headers={"Accept": "application/json", "User-Agent": "hojaemap/1.0"})
                with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
                    d = json.loads(r.read().decode("utf-8", "replace"))
                break
            except Exception as e:
                if attempt == 2: print(f"  {beg}~{end} p{page} 실패: {e}"); return out
                time.sleep(1.5 * (attempt + 1))
        body = (d.get("response") or {}).get("body") or {}
        items = body.get("items") or []
        if isinstance(items, dict): items = items.get("item", [])
        if not items: break
        out += items
        if page * 300 >= int(body.get("totalCount") or 0): break
        page += 1; time.sleep(0.3)
    return out

def fetch(days):
    end = datetime.date.today()
    out = []
    while days > 0:
        span = min(days, 30)   # 30일 단위 창으로 분할 (API 조회범위 상한)
        wend = end; wbeg = end - datetime.timedelta(days=span)
        out += fetch_window(wbeg, wend)
        end = wbeg - datetime.timedelta(days=1); days -= span
        time.sleep(0.3)
    return out

def main():
    if not KEY:
        print("❌ DATA_KEY 환경변수 필요"); sys.exit(1)
    days = 90
    if "--days" in sys.argv: days = int(sys.argv[sys.argv.index("--days") + 1])
    print(f"나라장터 공사공고 수집 (최근 {days}일, 전국)...")
    raw = fetch(days)
    print(f"  전체 공사공고 {len(raw)}건")

    # 기존 official_roads.json 사업명(승격됨) — 후보에서 '기존'으로 표시
    off = json.load(open(f"{BASE}/official_roads.json"))["roads"]
    off_norm = [norm(o["name"]) for o in off] + [norm(m) for o in off for m in o.get("match", [])]

    def amt_of(it):   # 사업비 = 배정예산 우선, 없으면 추정가격
        for f in ("bdgtAmt", "presmptPrce", "mainCnsttyCnstwkPrearngAmt"):
            v = (it.get(f) or "").strip()
            if v and v.replace(".", "").isdigit() and float(v) > 0: return int(float(v))
        return 0

    cand, seen, dropped_small = [], {}, 0
    for it in raw:
        nm = (it.get("bidNtceNm") or "").strip()
        if not any(k in nm for k in INCLUDE): continue
        if any(k in nm for k in EXCLUDE): continue
        if any(k in nm for k in NOISE): continue
        if SORO.search(nm): continue
        # 유형 판정: 공원 키워드 우선(공원조성), 아니면 도로
        kind = "공원" if (any(k in nm for k in PARK_INC) and not any(k in nm for k in PARK_SMALL_KW) and "도로" not in nm) else "도로"
        if kind == "공원" and any(k in nm for k in PARK_SMALL_KW): continue
        amt = amt_of(it)
        g = grade_of(kind, amt)
        if g is None: dropped_small += 1; continue   # 사업비 문턱 미만 = 노이즈 제외(규칙)
        core = norm(nm)
        if core in seen:
            seen[core]["bids"] += 1
            if amt > seen[core]["amt"]: seen[core]["amt"] = amt; seen[core]["grade"] = grade_of(kind, amt)
            continue
        known = any(core and (core in o or o in core) for o in off_norm)
        rec = {"name": nm, "kind": kind, "grade": g, "amt": amt, "amt_억": round(amt / 1e8, 1),
               "inst": it.get("ntceInsttNm") or it.get("dminsttNm") or "",
               "notice_date": (it.get("bidNtceDt") or "")[:10], "notice_no": it.get("bidNtceNo") or "",
               "url": it.get("bidNtceUrl") or "",
               "status": "기존(official_roads 등재)" if known else "승격가능",
               "promote": (not known), "bids": 1}
        seen[core] = rec; cand.append(rec)

    new = [c for c in cand if c["promote"]]
    GORD = {"A": 0, "B": 1, "C": 2}
    cand.sort(key=lambda c: (not c["promote"], GORD.get(c["grade"], 9), -c["amt"]))
    by_kind = lambda k: [c for c in new if c["kind"] == k]
    json.dump({"_note": "나라장터 공사공고 → 도로·공원 사업 후보(§SSOT 후보층). 규모=사업비(bdgtAmt) 자동 등급(도로 A300/B100/C30억·공원 A200/B50/C10억). "
                        "문턱 미만 자동 제외. 승격 시 좌표·경유지만 확인(존재·금액·등급은 자동). 승격: official_roads.json/signals.json.",
               "collected": datetime.date.today().isoformat(), "total_bids": len(raw), "dropped_below_threshold": dropped_small,
               "candidates": len(cand), "new": len(new),
               "new_road": len(by_kind("도로")), "new_park": len(by_kind("공원")), "items": cand},
              open(f"{BASE}/bid_candidates.json", "w"), ensure_ascii=False, indent=1)
    print(f"  후보 {len(cand)}건 (승격가능 {len(new)}: 도로 {len(by_kind('도로'))}·공원 {len(by_kind('공원'))} / 기존 {len(cand)-len(new)}) · 사업비 문턱미달 {dropped_small}건 제외 → bid_candidates.json")
    print("  ── 승격가능(사업비 등급순) ──")
    for c in new[:16]:
        print(f"    [{c['kind']}·{c['grade']}] {c['amt_억']:>6}억 | {c['name'][:40]} | {c['inst'][:16]} | {c['notice_date']}")

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
