#!/usr/bin/env python3
"""DART(전자공시) → 일자리 호재 후보 신호. 신규시설투자등 + 유형자산양수결정 공시를 수집.
위치=회사 소재지(company API, 본사 기준 근사 — 시설 소재지는 서식 불안정으로 V2), 레벨=투자금액.
후보층(confirmed:false) — 운영자 승격 대상. 사용: DART_KEY=xxx VWORLD_KEY=xxx python3 dart_fetch.py [bgn_de] [end_de]
"""
import os, sys, json, time, ssl, io, zipfile, re, urllib.request, urllib.parse, socket
socket.setdefaulttimeout(30)
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
DKEY = os.environ.get("DART_KEY", "").strip()
VKEY = os.environ.get("VWORLD_KEY", "").strip()
BGN = sys.argv[1] if len(sys.argv) > 1 else "20250715"
END = sys.argv[2] if len(sys.argv) > 2 else "20260714"
CAP = 900   # 처리 상한(과도한 호출 방지)

# 전국 16시도 (2026-07-16 전국 확장 — 주소 접두 → 표준 시도 약칭). 구명칭(전라북도 등) 병기.
# ※ 2026-07-01 광역행정통합: 전라남도+광주광역시 → 전남광주통합특별시. 구명칭 주소(등기부 등 미갱신분)도 통합 약칭으로 흡수.
SIDO = {"서울": "서울", "인천": "인천", "경기": "경기", "부산": "부산", "대구": "대구",
        "대전": "대전", "울산": "울산", "세종": "세종", "강원": "강원",
        "충청북": "충북", "충청남": "충남", "전북": "전북", "전라북": "전북",
        "전남광주": "전남광주", "전라남": "전남광주", "광주": "전남광주",
        "경상북": "경북", "경상남": "경남", "제주": "제주"}
def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "hojaemap/1.0"}), context=CTX).read()
def dget(path, **params):
    params["crtfc_key"] = DKEY
    return json.loads(get("https://opendart.fss.or.kr/api/" + path + "?" + urllib.parse.urlencode(params)).decode("utf-8", "replace"))

def _d8(v):
    """YYYYMMDD → YYYY-MM-DD (마일스톤 date용)"""
    v = (v or "").strip()
    return f"{v[:4]}-{v[4:6]}-{v[6:8]}" if len(v) == 8 and v.isdigit() else None

def date_windows(bgn, end, days=80):
    """DART list API가 넓은 기간을 거부 → days일 창으로 분할"""
    from datetime import datetime, timedelta
    b = datetime.strptime(bgn, "%Y%m%d"); e = datetime.strptime(end, "%Y%m%d")
    while b <= e:
        w = min(b + timedelta(days=days), e)
        yield b.strftime("%Y%m%d"), w.strftime("%Y%m%d")
        b = w + timedelta(days=1)

def list_disc(ty, want):
    """ty별, 기간을 창으로 나눠 페이지 순회 → report_nm에 want 포함(정정 제외)"""
    out = []
    for bgn, end in date_windows(BGN, END):
        for pg in range(1, 40):
            d = dget("list.json", bgn_de=bgn, end_de=end, pblntf_ty=ty, page_count=100, page_no=pg)
            if d.get("status") != "000": break
            for x in d.get("list", []):
                nm = x.get("report_nm", "")
                if want in nm and "정정" not in nm:
                    out.append(x)
            if pg >= int(d.get("total_page", 1)): break
            time.sleep(0.15)
    return out

def region_of(adr):
    """주소 → (시도약칭, 시군구). SIDO 표 기준 — 표가 바뀌면(전국 확장 등) 캐시 주소로 재산출."""
    sd = next((SIDO[k] for k in SIDO if adr.startswith(k)), None)
    m = re.match(r"[가-힣]+(?:특별시|광역시|특별자치시|특별자치도|도)\s+([가-힣]+[시군구])", adr)
    return sd, (m.group(1) if m else None)

def company_region(cc, cache):
    if cc in cache:
        adr = cache[cc][2]   # 캐시는 주소만 신뢰 — sd/sg는 현재 SIDO 표로 항상 재산출(수도권 시절 None 오염 방지)
        sd, sg = region_of(adr)
        cache[cc] = (sd, sg, adr)
        return cache[cc]
    try:
        d = dget("company.json", corp_code=cc)
        adr = (d.get("adres") or "").strip()
    except Exception:
        adr = ""
    sd, sg = region_of(adr)
    cache[cc] = (sd, sg, adr)
    return cache[cc]

def invest_amount(rn):
    """공시 원문에서 투자금액(원) 추출 → 억원. 실패 0."""
    try:
        raw = get(f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={DKEY}&rcept_no={rn}")
        z = zipfile.ZipFile(io.BytesIO(raw)); t = z.read(z.namelist()[0]).decode("utf-8", "replace")
        txt = re.sub(r"<[^>]+>", " ", t)
        cands = re.findall(r"(?:투자금액|양수금액|취득금액|거래금액)[^0-9]{0,25}([0-9,]{5,})", txt)
        vals = [int(c.replace(",", "")) for c in cands if c.replace(",", "").isdigit()]
        return max(vals) / 1e8 if vals else 0   # 억원
    except Exception:
        return 0

def level_of(eok):
    # 투자금액 기준(사용자 확정 2026-07-16): A≥5천억 / B 1천~5천억 / C 300억~1천억 / 300억미만=제외
    if eok >= 5000: return "A"
    if eok >= 1000: return "B"
    if eok >= 300: return "C"
    return None   # 300억 미만 = 신호 아님(제외)

def geocode(addr, cache):
    if addr in cache: return cache[addr]
    q = re.sub(r"\s+", " ", addr).strip()
    for typ in ("road", "parcel"):
        try:
            url = "https://api.vworld.kr/req/address?" + urllib.parse.urlencode({
                "service": "address", "request": "getcoord", "version": "2.0", "crs": "epsg:4326",
                "type": typ, "address": q, "format": "json", "key": VKEY})
            d = json.loads(get(url).decode("utf-8", "replace"))
            if d.get("response", {}).get("status") == "OK":
                p = d["response"]["result"]["point"]
                cache[addr] = [round(float(p["x"]), 6), round(float(p["y"]), 6)]; return cache[addr]
        except Exception:
            pass
    cache[addr] = None; return None

def main():
    if not DKEY or not VKEY:
        print("DART_KEY, VWORLD_KEY 필요"); return
    ccache = json.load(open("dart_corp_cache.json")) if os.path.exists("dart_corp_cache.json") else {}
    gcache = json.load(open("dart_geo_cache.json")) if os.path.exists("dart_geo_cache.json") else {}
    discs = list_disc("I", "신규시설투자") + list_disc("B", "유형자산양수")
    # corp_code+report_nm 중복 제거(최신 rcept 유지)
    uniq = {}
    for x in discs:
        uniq[(x["corp_code"], x["report_nm"])] = x
    discs = list(uniq.values())
    print(f"수집 공시 {len(discs)}건 → 수도권 필터·처리")
    sigs = []; processed = 0
    for x in discs:
        if processed >= CAP: break
        cc = x["corp_code"]
        sd, sg, adr = company_region(cc, ccache)
        if not sd: continue   # 수도권 회사만
        processed += 1
        eok = invest_amount(x["rcept_no"]); time.sleep(0.1)
        lvl = level_of(eok)
        if lvl is None: continue   # 300억 미만(파싱실패 0 포함) = 제외
        c = geocode(adr, gcache); time.sleep(0.12)
        if not c: continue
        rn = x["rcept_no"]
        sigs.append({
            "title": f"{x['corp_name']} {x['report_nm'].replace('주요사항보고서','').strip('()')}",
            "cat": "일자리", "lvl": lvl, "amount_eok": round(eok),
            "sd": sd, "sg": sg, "dong": None, "confirmed": False,
            "current_stage": "공시", "phase": "계획", "status": "활성",
            "rcept_dt": x.get("rcept_dt"), "corp": x["corp_name"],
            # 마일스톤(DATA_STANDARD §3b): 공시일 = 완료 사실. 투자기간은 서식 불안정으로 미추출(추정 금지)
            "milestones": ([{"phase": "계획", "stage": "신규시설투자 공시", "date": _d8(x.get("rcept_dt")),
                             "state": "current", "src": "DART 전자공시"}] if _d8(x.get("rcept_dt")) else []),
            "src_tier": "official_report", "pt_type": "facility", "geo_prec": "parcel",   # 표준 필드
            "src": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rn}",
            "geometry": {"coordinates": c}
        })
    json.dump(ccache, open("dart_corp_cache.json", "w"), ensure_ascii=False)
    json.dump(gcache, open("dart_geo_cache.json", "w"), ensure_ascii=False)
    json.dump({"_note": "DART 신규시설투자·유형자산양수 → 일자리 후보(본사 소재지 기준 근사, 투자금액→레벨). 승격 전 후보층.",
               "signals": sigs}, open("dart_signals.json", "w"), ensure_ascii=False, separators=(",", ":"))
    from collections import Counter
    print(f"일자리 후보 {len(sigs)}건 → dart_signals.json | 레벨:{dict(Counter(s['lvl'] for s in sigs))} | 지역:{dict(Counter(s['sd'] for s in sigs))}")
    for s in sigs[:12]:
        print(f"  [{s['lvl']}] {s['sd']} {s['sg']} · {s['title'][:34]} · {s['amount_eok']}억")

if __name__ == "__main__":
    main()
