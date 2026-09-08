/* 호재맵 서비스워커 — 푸시 알림 전용 (2026-09-08 세션107 · 알렉스 「단지 알림 구독까지 만들어」).
   ⚠ fetch 핸들러가 **없다** — 캐시·오프라인을 건드리지 않는다(사이트의 로딩 문법은 index.html이 정본 · 이 파일은 알림만).
   구조 = 빈 푸시 + 여기서 메시지를 가져간다: 접속 기록 워커가 `push_pending`에 메시지를 적고 VAPID 서명만 붙인 빈 푸시를 보내면,
   `push` 이벤트에서 내 구독 endpoint로 `/what`을 물어 제목·본문·링크를 받아 알림을 띄운다(페이로드 암호화 없음 · endpoint가 곧 열쇠).
   정본 = visitlog/README.md 「웹 푸시」 · 게이트 = html_check.push_check(등록 · 이 파일의 push/notificationclick 핸들러). */
const W = "https://hojaemap-visits.obey82.workers.dev";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("push", e => {
  e.waitUntil((async () => {
    let m = null;
    try {
      const sub = await self.registration.pushManager.getSubscription();
      if (!sub) return;
      const r = await fetch(W + "/what?e=" + encodeURIComponent(sub.endpoint), { cache: "no-store" });
      if (r.ok) m = await r.json();
    } catch (err) { /* 못 받으면 아래 일반 알림 */ }
    if (!m || !m.title) m = { title: "호재맵", body: "지켜보는 곳에 새 신호가 있습니다.", url: "https://hojaemap.kr/" };
    await self.registration.showNotification(m.title, {
      body: m.body || "", icon: "icon-192.png", badge: "icon-192.png", tag: "hojae-" + (m.tag || "push"), renotify: true,
      data: { url: m.url || "https://hojaemap.kr/" } });
  })());
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || "https://hojaemap.kr/";
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const same = all.find(c => c.url && c.url.startsWith("https://hojaemap.kr"));
    if (same) { try { await same.navigate(url); await same.focus(); return; } catch (err) { /* 새 창으로 */ } }
    await self.clients.openWindow(url);
  })());
});
