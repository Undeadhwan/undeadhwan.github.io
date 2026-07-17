#!/usr/bin/env python3
"""호재맵 마스터 파이프라인 — 백지에서 전체 데이터 재생성 (2026-07-17 신설).

**백지 재현 원칙(사용자 확정)**: "다른 웹사이트를 새로 만들어 백지에서 실행해도 제대로 채워져야 한다."
이 스크립트 하나로 수집 → 가공 → 표준 필드 → QA → (배포 준비)까지 간다.

  ┌ 공식 지식(git 보관, 조사로 확보한 사실) ┐      ┌ 자동 수집(API/OSM) ┐
  │ rail_official.json   철도 노선·역·일정  │      │ 건축HUB  arch_fetch │
  │ official_roads.json  도로 사업·단계     │  +   │ DART     dart_fetch │  →  신호 3층
  │ ic_names.json        IC 명단            │      │ 정비 API seoul_hh   │
  │ signals.json         확정 신호          │      │ OSM      build_road │
  └─────────────────────────────────────────┘      └─────────────────────┘

원칙(DATA_STANDARD):
  · 사실의 근거는 공식 소스만(src_tier). OSM은 선형(그림)만.
  · 공식 지식은 **스크립트가 아니라 데이터 파일**에 둔다 → 백지에서도 재현.
  · 모든 산출물은 표준 필드(src_tier/geo_prec/pt_type/milestones)를 갖는다.
  · qa_check.py 오류 0이어야 배포.

사용:
  python3 build_all.py            # 공식 지식 기반 재생성 (API 호출 없음, 빠름·오프라인)
  python3 build_all.py --fetch    # + 외부 API/OSM 재수집 (느림, 키 필요)
  python3 build_all.py --deploy   # + dist/dist-root 복사까지
"""
import json, os, subprocess, sys, time

FETCH = "--fetch" in sys.argv
DEPLOY = "--deploy" in sys.argv
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

def sh(cmd, env=None, optional=False):
    e = dict(os.environ); e.update(env or {})
    print(f"\n$ {cmd}")
    r = subprocess.run(cmd, shell=True, env=e)
    if r.returncode != 0:
        msg = f"  ⚠ 실패(코드 {r.returncode})"
        print(msg + (" — 선택 단계라 계속" if optional else " — 중단"))
        if not optional: sys.exit(1)
    return r.returncode == 0

def key(name):
    try:
        s = json.load(open("secrets.local.json"))
        v = s.get(name)
        return (v.get("key") if isinstance(v, dict) else v) or ""
    except Exception:
        return ""

def step(n, title):
    print("\n" + "═" * 72); print(f"  [{n}] {title}"); print("═" * 72)

def main():
    t0 = time.time()
    print("호재맵 마스터 파이프라인" + (" (--fetch: 외부 수집 포함)" if FETCH else " (공식 지식 기반 재생성)"))

    # ── 1. 철도: 공식 지식 → 신호 (항상 재현 가능) ──
    step(1, "철도 — rail_official.json(공식 SSOT) → rail_signals.json")
    sh(f"python3 build_rail_v2.py{' --osm' if FETCH else ''}")

    # ── 2. 도로: OSM 선형 + official_roads.json 오버레이 ──
    step(2, "도로 — road_osm_geom.json(선형 스냅샷) + official_roads.json + ic_names.json → road_signals.json")
    if FETCH:
        sh("python3 build_road.py", optional=True)          # OSM 재수집 → 스냅샷 갱신
        sh("python3 build_road_ic.py", optional=True)       # OSM 램프 클러스터(공식 명단 없는 노선 보조)
    sh("python3 build_road_v2.py")                          # 공식 SSOT + 스냅샷 병합 (항상 실행 — 백지 재현)

    # ── 3. 외부 API 수집 (키 필요) ──
    if FETCH:
        step(3, "외부 API 수집 — 정비/건축/DART")
        sh("python3 seoul_hh_fetch.py", optional=True)      # 정비 세대수+마일스톤(API 재생성)
        dk, vk, gk = key("dart_key") or os.environ.get("DART_KEY", ""), key("vworld"), key("data_go_kr_통합키")
        if dk and vk:
            sh("python3 dart_fetch.py", {"DART_KEY": dk, "VWORLD_KEY": vk}, optional=True)
        else:
            print("  ⚠ DART_KEY/VWORLD_KEY 없음 — DART 건너뜀")
        if gk and vk:
            for bdong, out in [("seoul_bdong.geojson", "arch_signals.json"),
                               ("jibang_bdong.geojson", "arch_signals_jibang.json"),
                               ("jnkj_bdong.geojson", "arch_signals_jnkj.json")]:
                if os.path.exists(bdong):
                    sh("python3 arch_fetch.py", {"DATA_KEY": gk, "VWORLD_KEY": vk,
                                                 "ARCH_BDONG": bdong, "ARCH_OUT": out}, optional=True)
        else:
            print("  ⚠ DATA_KEY/VWORLD_KEY 없음 — 건축인허가 건너뜀")
    else:
        step(3, "외부 API 수집 — 건너뜀 (--fetch 로 활성화)")

    # ── 4. 대장(참조 문서) 생성 ──
    step(4, "노선·역/도로·IC 대장 생성")
    sh("python3 gen_reference.py", optional=True)

    # ── 5. QA 게이트 (DATA_STANDARD §9) ──
    step(5, "QA 게이트 — 표준 검사 (오류 0이어야 배포)")
    ok = sh("python3 qa_check.py", optional=True)

    # ── 6. 배포 ──
    if DEPLOY:
        step(6, "배포 — dist / dist-root 복사")
        if not ok:
            print("  ❌ QA 오류 — 배포 중단 (DATA_STANDARD §8 절차)"); sys.exit(1)
        files = ["index.html", "methodology.html", "rail_reference.html", "signals.json",
                 "rail_signals.json", "rail_reference.json", "road_signals.json", "road_reference.json",
                 "dart_signals.json", "arch_signals.json", "arch_signals_jibang.json", "arch_signals_jnkj.json",
                 "signals_jeongbi_all.json", "regions.json", "sgg_all.geojson",
                 "official_roads.json", "ic_names.json", "rail_official.json", "DATA_STANDARD.md"]
        for d in ("dist", "dist-root"):
            if not os.path.isdir(d): print(f"  ⚠ {d} 없음 — 건너뜀"); continue
            for f in files:
                if os.path.exists(f): sh(f'cp "{f}" {d}/', optional=True)
        print("  → BUILD 상수 갱신 후 각 repo에서 commit/push 필요")
    else:
        step(6, "배포 — 건너뜀 (--deploy 로 활성화)")

    print("\n" + "═" * 72)
    print(f"  완료 ({time.time()-t0:.0f}s) — QA {'통과 ✅' if ok else '오류 ❌'}")
    print("═" * 72)

if __name__ == "__main__":
    main()
