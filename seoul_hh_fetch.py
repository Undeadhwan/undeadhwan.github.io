#!/usr/bin/env python3
"""서울 정비사업 세대수 + **마일스톤** 채우기 — 서울 열린데이터광장 TbSeoulRedevStatus(OA-22856, 공공누리 1유형·상업OK).
472건(재개발·재건축)에서 두 가지를 신호에 주입한다:
  ① 건립세대수(TOT_BUILT_HOUSEHOLDS) → 등급 기준
  ② **단계별 날짜 → milestones[]** (DATA_STANDARD §3b) — 구역지정·추진위·조합설립·건축심의·사업시행인가·관리처분·이주·착공
     API가 각 단계의 실제 일자(YMD)를 준다. 지금까지 이 사실을 버리고 있었다(2026-07-17 보강).
매칭: 자치구 일치 + (정규화 구역명 상호포함 or 지번 일치).
갱신: 이 스크립트 재실행(API 실시간). 사용: python3 seoul_hh_fetch.py
"""
import ssl, json, re, urllib.request

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
KEY = json.load(open("secrets.local.json"))["seoul_opendata"]
if isinstance(KEY, dict): KEY = KEY.get("key") or list(KEY.values())[0]
SVC = "TbSeoulRedevStatus"

def fetch_all():
    url = f"http://openapi.seoul.go.kr:8088/{KEY}/json/{SVC}/1/1000/"
    d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "hojaemap/1.0"}), context=CTX).read().decode("utf-8"))
    return d[SVC]["row"]

def norm(s):
    """구역명 정규화: 공백·괄호·유형접미사 제거 → 매칭키"""
    s = re.sub(r"\(.*?\)", "", str(s or ""))
    s = re.sub(r"(주택|공동주택|도시정비형|주택정비형|재정비촉진|정비사업|정비구역|재개발사업|재건축사업|재개발|재건축|아파트|일대|일원|번지|구역|지구)", "", s)
    return re.sub(r"[\s\-·,]", "", s)

# ── 단계별 날짜 → 4단계 백본 마일스톤 (DATA_STANDARD §3b). 소스 원문 용어를 stage로 보존.
MS_MAP = [   # (API 필드, phase, 원문 용어)
    ("ZONE_DESIGNATION_INIT_YMD",       "계획", "정비구역 지정"),
    ("PROMOTION_COMMITTEE_YMD",         "계획", "추진위원회 승인"),
    ("ASSOCIATION_ESTABLISHMENT_YMD",   "인가", "조합설립 인가"),
    ("ARCHITECTURAL_REVIEW_YMD",        "인가", "건축심의"),
    ("BIZ_IMPLEMENTATION_INIT_YMD",     "인가", "사업시행 인가"),
    ("MGMT_DISPOSITION_INIT_YMD",       "인가", "관리처분 인가"),
    ("MIGRATION_START_YMD",             "착공", "이주 개시"),
    ("MIGRATION_END_YMD",               "착공", "이주 완료"),
    ("CONSTRUCTION_START_YMD",          "착공", "착공"),
]
PH_ORDER = ["계획", "인가", "착공", "준공"]

def milestones_of(r):
    """API 단계별 일자 → milestones[]. 완료된 것만 date(사실). 미래 단계는 eta 없이 future(추정 금지)."""
    ms = []
    for fld, ph, label in MS_MAP:
        v = (r.get(fld) or "").strip()
        if not v or v == "-": continue
        ms.append({"phase": ph, "stage": label, "date": v[:10], "state": "done", "src": "서울 열린데이터광장 OA-22856"})
    if ms:
        ms[-1]["state"] = "current"   # 마지막 완료 = 현재 단계
        last_i = PH_ORDER.index(ms[-1]["phase"])
        for ph in PH_ORDER[last_i + 1:]:
            ms.append({"phase": ph, "stage": {"인가": "인가", "착공": "착공", "준공": "준공"}.get(ph, ph), "state": "future"})
    return ms

def jibun_key(s):
    """지번 키: 동명+첫번지 (예: 사당동 63-1 → 사당동63)"""
    m = re.search(r"([가-힣]+동)\s*([0-9]+)", str(s or ""))
    return (m.group(1) + m.group(2)) if m else None

def main():
    rows = fetch_all()
    print(f"TbSeoulRedevStatus {len(rows)}건 수신 (공공누리 1유형)")
    # 인덱스: (자치구, 정규화명) / (자치구, 지번키)
    by_name, by_jibun = {}, {}
    for r in rows:
        hh = r.get("TOT_BUILT_HOUSEHOLDS")
        try: hh = int(float(hh))
        except Exception: hh = None
        # 세대수가 없어도 **마일스톤은 주입**한다(진행 이력은 그 자체로 사실) — 2026-07-17
        gu = (r.get("DISTRICT") or "").strip()
        rec = {"hh": hh, "zone": r.get("ZONE_NM"), "stage": r.get("BIZ_STAGE"), "exist": r.get("EXISTING_HOUSEHOLDS"),
               "ms": milestones_of(r)}
        n = norm(r.get("ZONE_NM"))
        if n: by_name.setdefault((gu, n), rec)
        jk = jibun_key(r.get("JIBUN_ADDR"))
        if jk: by_jibun.setdefault((gu, jk), rec)
    print(f"  건립세대수 보유: 이름키 {len(by_name)} · 지번키 {len(by_jibun)}")

    d = json.load(open("signals_jeongbi_all.json"))
    cands = d.get("cands") or []
    filled = already = 0
    SMALL_RE = re.compile(r"가로주택|소규모|모아타운|모아주택|자율주택|지역주택")   # 이 API(재개발·재건축 정비사업) 범위 밖 → 동명 어간 오매칭 방지
    for c in cands:
        if c.get("sd") != "서울": continue
        if c.get("hh_src") == "OA-22856": c["hh"] = None; c.pop("hh_src")   # 재실행: 이전 주입분 리셋(오매칭 교정 반영)
        c.pop("milestones", None)
        had_hh = bool(c.get("hh"))
        if had_hh: already += 1
        if SMALL_RE.search(c.get("n") or ""): continue   # 소규모 유형 스킵
        gu = (c.get("sg") or "").strip()
        n = norm(c.get("n"))
        rec = by_name.get((gu, n))
        if not rec and n:   # 상호 substring (짧은쪽이 긴쪽에 포함)
            for (g2, n2), r2 in by_name.items():
                if g2 == gu and n and n2 and (n in n2 or n2 in n) and min(len(n), len(n2)) >= 3:
                    rec = r2; break
        if not rec:
            jk = jibun_key(c.get("jibun") or c.get("dong"))
            if jk: rec = by_jibun.get((gu, jk))
        if rec:
            if rec["hh"] and not had_hh:
                c["hh"] = rec["hh"]; c["hh_src"] = "OA-22856"   # 출처표시(1유형)
                filled += 1
            if rec["ms"]:
                c["milestones"] = rec["ms"]   # 단계별 실제 일자(DATA_STANDARD §3b)
                c["ms_src"] = "OA-22856"
    json.dump(d, open("signals_jeongbi_all.json", "w"), ensure_ascii=False, separators=(",", ":"))
    seoul = [c for c in cands if c.get("sd") == "서울"]
    now = sum(1 for c in seoul if c.get("hh"))
    msn = sum(1 for c in seoul if c.get("milestones"))
    print(f"서울 {len(seoul)}건: 기존 {already} + 신규매칭 {filled} = 세대수 보유 {now}건 ({now*100//len(seoul)}%)")
    print(f"  **마일스톤 주입: {msn}건** (단계별 실제 일자 — 구역지정/추진위/조합설립/심의/인가/관리처분/이주/착공)")
    print("→ signals_jeongbi_all.json 갱신 (재배포 필요: cp dist·dist-root + BUILD++)")

if __name__ == "__main__":
    main()
