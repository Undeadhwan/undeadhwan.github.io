#!/usr/bin/env python3
"""건축인허가(국토부 건축HUB ArchPmsHubService) → 인프라·일자리 대형건물 발견(발견형 후보, confirmed:false).
서울 법정동 순회 → getApBasisOulnInfo(기본개요: 주용도·연면적·허가/착공/사용승인일 = 사실) →
대형(연면적≥1만㎡) + 주용도 인프라/일자리 분류 → 건물 dedup(최고단계) → VWorld 지오코딩 → arch_signals.json.

⚠️ 핵심: apis.data.go.kr 건축HUB는 **Accept 헤더 없으면 빈 200 반환**(2026-07-16 원인규명, "일일한도"·"승인대기" 진단 오류였음).
startDate/endDate = crtnDay(DB갱신일) 필터 = 최근 활동만(7442→946, 8배↓ = 발견형 신호에 적합).
동별개요(getApDongOulnInfo)는 날짜가 없어 사용불가 → 기본개요 단일 사용(대형건물 주용도 채움률 ~99%).
사용: DATA_KEY=통합키 VWORLD_KEY=xxx python3 arch_fetch.py
"""
import os, json, time, ssl, urllib.request, urllib.parse, socket, datetime
socket.setdefaulttimeout(30)
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
DKEY = os.environ.get("DATA_KEY", "").strip()
VKEY = os.environ.get("VWORLD_KEY", "").strip()
START = os.environ.get("ARCH_START", "20230101")   # crtnDay(DB갱신) 이후 = 최근 활동
MIN_AREA = float(os.environ.get("ARCH_MIN_AREA", "10000"))   # 연면적(㎡) 하한 = 대형만
RECENT = os.environ.get("ARCH_RECENT", "20220101")   # 계획·착공(진행중) 단계날짜 하한 = 오래 멈춘 것 제외
DONE_MONTHS = int(os.environ.get("ARCH_DONE_MONTHS", "12"))   # 준공은 실현된 level → 준공 후 N개월 이내만 호재로 유지(그 이상=이미 반영, §1.1 delta)
_t = datetime.date.today(); _m = _t.year * 12 + (_t.month - 1) - DONE_MONTHS
DONE_CUT = f"{_m // 12:04d}{_m % 12 + 1:02d}{_t.day:02d}"   # 오늘 - N개월 (YYYYMMDD)
GB_OK = set((os.environ.get("ARCH_GB", "신축")).split(","))   # 건축구분: 신축만. 용도변경·대수선=기존건물 리모델(노이즈), 증축=totArea가 증축후 건물전체 면적이라 작은 증축이 A급 뻥튀기(§P2/P5 위반, 예: 대학 계단교실 증축이 84만㎡) → 신축만 채택(신축 totArea=실제 신규건물 면적, 정직)
HDRS = {"User-Agent": "hojaemap/1.0", "Accept": "*/*"}   # ★ Accept 필수(없으면 빈 응답)
# 주용도 → 분류. 일자리 우선 매칭 후 인프라. 나머지(공동주택·단독주택 등 주거)는 스킵(정비·주거개발 소관)
JOB = ("업무", "공장", "교육연구", "연구소", "지식산업")
INFRA = ("판매", "의료", "문화 및 집회", "운수", "위락", "관광", "숙박", "근린생활", "방송통신", "종교", "장례")
PHASE_RANK = {"계획": 0, "착공": 1, "준공·개통": 2}
# 연면적(㎡) → 레벨 (스펙: 인프라 A5만/B1만/C, 일자리 A10만/B3만/C1만)
def level_of(cat, area):
    if cat == "인프라": return "A" if area >= 50000 else "B" if area >= 10000 else "C"
    return "A" if area >= 100000 else "B" if area >= 30000 else "C"   # 일자리
# 학교 처리(§7b 사용자 확정 2026-07-17): 일반 초중고·특수학교 = 배제(신도시 부속시설 + 학군 서열화 P1),
# 특목·자사·국제학교급 = 인프라. '교육연구시설' 용도가 일자리로 뭉뚱그려지던 것을 명칭으로 분기.
SCHOOL_SPECIAL = ("과학고", "외국어고", "외고", "국제고", "국제학교", "영재학교", "예술고", "자사고", "자율형사립")
SCHOOL_GENERAL = ("초등학교", "중학교", "고등학교", "특수학교", "유치원")
def cat_of(purp, name=""):
    if "교육연구" in purp:
        if any(k in name for k in SCHOOL_SPECIAL): return "인프라"
        if any(k in name for k in SCHOOL_GENERAL): return None   # 일반 학교 배제
        # 대학·연구소 등은 고용 시설 — 일자리 유지
    if any(k in purp for k in JOB): return "일자리"
    if any(k in purp for k in INFRA): return "인프라"
    return None
def phase_of(it):
    if (it.get("useAprDay") or "").strip(): return "준공·개통"
    if (it.get("realStcnsDay") or "").strip(): return "착공"
    return "계획"
def rel_date(it, ph):
    """단계별 '현재 국면'의 실제 날짜 = 최근성 판정 기준 (준공=사용승인일, 착공=착공일, 계획=허가일)."""
    d = {"준공·개통": it.get("useAprDay"), "착공": it.get("realStcnsDay"), "계획": it.get("archPmsDay")}.get(ph) or ""
    return d.strip()[:8]

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers=HDRS), context=CTX).read()

def fetch_dong(sgg, bjd, cap=30):
    """기본개요(getApBasisOulnInfo): 주용도(mainPurpsCdNm)+연면적(totArea)+허가/착공/사용승인일. startDate=crtnDay 필터."""
    items = []
    for pg in range(1, cap + 1):
        url = "https://apis.data.go.kr/1613000/ArchPmsHubService/getApBasisOulnInfo?" + urllib.parse.urlencode({
            "serviceKey": DKEY, "sigunguCd": sgg, "bjdongCd": bjd,
            "startDate": START, "endDate": "20261231", "numOfRows": 100, "pageNo": pg, "_type": "json"})
        try:
            d = json.loads(get(url).decode("utf-8", "replace"))
            body = d.get("response", {}).get("body", {})
            it = (body.get("items") or {}).get("item") or []
            if isinstance(it, dict): it = [it]
            items += it
            if len(it) < 100: break
        except Exception:
            break
        time.sleep(0.1)
    return items

def geocode(addr, cache):
    if addr in cache: return cache[addr]
    for typ in ("parcel", "road"):
        try:
            url = "https://api.vworld.kr/req/address?" + urllib.parse.urlencode({
                "service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326",
                "type": typ, "address": addr, "format": "json", "key": VKEY})
            d = json.loads(get(url).decode("utf-8", "replace"))
            if d.get("response", {}).get("status") == "OK":
                p = d["response"]["result"]["point"]
                cache[addr] = [round(float(p["x"]), 6), round(float(p["y"]), 6)]; return cache[addr]
        except Exception:
            pass
    cache[addr] = None; return None


# ── 마일스톤 산출 (DATA_STANDARD §3b): API 원문 날짜 = 완료 사실(date). 미래는 eta 없이 future(추정 금지).
def _d8(v):
    v = (v or "").strip()
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) == 8 and v.isdigit() else None

def milestones_of(pms, stcns, useapr):
    ms = []
    if _d8(pms):    ms.append({"phase": "인가", "stage": "건축허가", "date": _d8(pms), "state": "done", "src": "건축HUB"})
    if _d8(stcns):  ms.append({"phase": "착공", "stage": "착공",     "date": _d8(stcns), "state": "done", "src": "건축HUB"})
    if _d8(useapr): ms.append({"phase": "준공", "stage": "사용승인", "date": _d8(useapr), "state": "done", "src": "건축HUB"})
    if not ms: return ms
    ms[-1]["state"] = "current"
    order = ["인가", "착공", "준공"]
    last = order.index(ms[-1]["phase"])
    for ph in order[last + 1:]:
        ms.append({"phase": ph, "stage": {"착공": "착공", "준공": "사용승인"}.get(ph, ph), "state": "future"})
    return ms

def main():
    if not DKEY or not VKEY:
        print("DATA_KEY, VWORLD_KEY 필요"); return
    bdfile = os.environ.get("ARCH_BDONG", "seoul_bdong.geojson")   # 지역 파라미터: 서울/capital_bdong 등
    bd = json.load(open(bdfile))
    dongs, seen = [], set()
    for f in bd["features"]:
        cd = f["properties"]["EMD_CD"]   # 8자리 = 시군구(5)+동(3)
        if cd in seen: continue
        seen.add(cd)
        sido = f["properties"].get("sido", "서울")   # capital_bdong=경기/인천, seoul_bdong=서울(속성없음)
        dongs.append((cd[:5], cd[5:8] + "00", sido, f["properties"]["sgg"], f["properties"]["EMD_KOR_NM"]))
    print(f"{bdfile}: {len(dongs)}개 동 순회 (건축인허가 기본개요, 최근 {START}~, 연면적≥{int(MIN_AREA)}㎡)")
    gcache = json.load(open("arch_geo_cache.json")) if os.path.exists("arch_geo_cache.json") else {}
    cand = {}   # dedup key -> best record(최고 단계·최신 허가)
    scanned = big = 0
    for sgg, bjd, sidonm, sggnm, dongnm in dongs:
        for it in fetch_dong(sgg, bjd):
            try: area = float(it.get("totArea") or 0)
            except Exception: area = 0
            if area < MIN_AREA: continue
            purp = (it.get("mainPurpsCdNm") or "").strip()
            cat = cat_of(purp, (it.get("bldNm") or "").strip())
            if not cat: continue   # 주거(공동주택 등)·미분류 스킵
            if (it.get("archGbCdNm") or "").strip() not in GB_OK: continue   # 신축·증축만(리모델·용도변경 제외)
            ph = phase_of(it)
            rd = rel_date(it, ph)
            if not rd or rd < RECENT: continue   # 최근성 게이트: 옛 랜드마크(이미 반영된 level) 제외 = delta만(§1.1·§7)
            if ph == "준공·개통" and rd < DONE_CUT: continue   # 준공은 실현 = 준공 후 12개월 이내만(그 이상=이미 가격반영)
            big += 1
            addr = (it.get("platPlc") or "").strip()
            nm = (it.get("bldNm") or "").strip() or purp
            key = (addr, int(area))   # bldNm 변이 무시(같은 건물 주소+연면적 동일) → 인허가 중복 병합
            prev = cand.get(key)
            # 같은 건물의 여러 인허가 레코드 → 최고 단계, 동단계면 최신 날짜 유지
            if prev and (PHASE_RANK[ph], rd) <= (PHASE_RANK[prev["_ph"]], prev["_rd"]):
                continue
            cand[key] = {"_ph": ph, "_rd": rd, "_pms": (it.get("archPmsDay") or ""), "cat": cat, "area": area,
                         "purp": purp, "gb": (it.get("archGbCdNm") or "").strip(), "nm": nm, "addr": addr, "sido": sidonm, "sgg": sggnm, "dong": dongnm,
                         "pms": (it.get("archPmsDay") or "").strip() or None,
                         "stcns": (it.get("realStcnsDay") or "").strip() or None,
                         "useapr": (it.get("useAprDay") or "").strip() or None}
        scanned += 1
        if scanned % 50 == 0:
            print(f"  {scanned}/{len(dongs)} 동 · 대형매칭 {big} · 후보(dedup) {len(cand)}")
            json.dump(gcache, open("arch_geo_cache.json", "w"), ensure_ascii=False)
    # 지오코딩 + 신호화
    sigs = []
    for r in cand.values():
        c = geocode(r["addr"], gcache); time.sleep(0.08)
        if not c: continue
        dt = r["useapr"] or r["stcns"] or r["pms"]
        sigs.append({
            "title": f"{r['dong']} {r['nm']} ({r['purp']}, 연면적 {int(r['area']/1000)}천㎡)",
            "cat": r["cat"], "lvl": level_of(r["cat"], r["area"]), "area_m2": int(r["area"]), "purp": r["purp"], "arch_gb": r["gb"],
            "sd": r["sido"], "sg": r["sgg"], "dong": r["dong"], "addr": r["addr"], "confirmed": False,
            "current_stage": r["_ph"], "phase": r["_ph"], "status": "활성",
            "arch_pms_day": r["pms"], "real_stcns_day": r["stcns"], "use_apr_day": r["useapr"],
            "milestones": milestones_of(r["pms"], r["stcns"], r["useapr"]),   # §3b — 진행 이력(원문 일자)
            "src_tier": "official_report", "pt_type": "facility", "geo_prec": "parcel",   # DATA_STANDARD 표준 필드
            "rcept_dt": dt, "src": "https://www.hub.go.kr/portal/opn/tyb/idx-acpmslg.do", "geometry": {"coordinates": c}
        })
    outfile = os.environ.get("ARCH_OUT", "arch_signals.json")
    json.dump(gcache, open("arch_geo_cache.json", "w"), ensure_ascii=False)
    json.dump({"_note": "건축인허가(건축HUB 기본개요) 신축 대형건물(연면적1만㎡↑, 인프라·일자리) → 후보. 증축·용도변경·대수선 제외. 준공은 최근 12개월 이내만. 허가/착공/사용승인일·연면적 사실. 승격 전 후보층.",
               "signals": sigs}, open(outfile, "w"), ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    print(f"\n완료: 인프라·일자리 후보 {len(sigs)}건 → {outfile} | 분류:{dict(Counter(s['cat'] for s in sigs))} 레벨:{dict(Counter(s['lvl'] for s in sigs))} 단계:{dict(Counter(s['phase'] for s in sigs))} 시도:{dict(Counter(s['sd'] for s in sigs))}")
    for s in sorted(sigs, key=lambda s: -s["area_m2"])[:12]:
        print(f"  [{s['cat']} {s['lvl']}] {s['sg']} · {s['title'][:44]} · {s['phase']}")

if __name__ == "__main__":
    main()
