"""PWA (ana ekrana eklenebilir web uygulamasi) parcalari.

Uygulama, telefondan `products.yaml`'a dokunmaz. Bunun yerine GitHub
Actions is akisini girdilerle tetikler; ekleme/silme/tarama islerini zaten
dogru calisan Python kodu yapar. Iki faydasi var:

1. YAML'i tarayicida elle duzenlemek kirilgandir; bu yoldan hic gerekmiyor.
2. Telefondaki anahtarin yetkisi cok daha dar kalabiliyor (sadece Actions),
   depo icerigine yazma yetkisi istemiyoruz.
"""

from __future__ import annotations

import json
from pathlib import Path

APP_NAME = "Fiyat Takip"
THEME_COLOR = "#2a78d6"

MANIFEST = {
    "name": "Fiyat Takip",
    "short_name": "Fiyat",
    "description": "Turk pazaryerlerinde fiyat takibi",
    "lang": "tr",
    "dir": "ltr",
    # Goreli yollar: depo adi ne olursa olsun GitHub Pages altinda calisir
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#f9f9f7",
    "theme_color": THEME_COLOR,
    "categories": ["shopping", "utilities"],
    "icons": [
        {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "maskable"},
    ],
}

# Servis calisani: rapor icin "once ag, olmazsa onbellek".
# Boylece cevrimdisiyken son tarama sonucu yine de acilir.
SW_JS = r"""
const CACHE = 'fiyat-takip-v1';
const SHELL = ['./', './app.js', './manifest.webmanifest',
               './icons/icon-192.png', './icons/apple-touch-icon.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  // GitHub API istekleri asla onbelleklenmez
  if (!req.url.startsWith(self.registration.scope)) return;

  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match('./')))
  );
});
"""

# Uygulama mantigi: ayarlar, tarama tetikleme, urun ekleme/silme, durum takibi
APP_JS = r"""
(() => {
  'use strict';

  const API = 'https://api.github.com';
  const KEY = { repo: 'ft.repo', token: 'ft.token', wf: 'ft.workflow', branch: 'ft.branch' };
  const WF_DEFAULT = 'scan.yml';

  // localStorage gizli pencerede veya site verisi kapaliyken patlayabilir
  const store = {
    get(k, d = '') { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
    del(k) { try { localStorage.removeItem(k); } catch {} },
  };

  const cfg = () => ({
    repo: store.get(KEY.repo).trim(),
    token: store.get(KEY.token).trim(),
    wf: store.get(KEY.wf).trim() || WF_DEFAULT,
    branch: store.get(KEY.branch).trim(),
  });
  const ready = () => { const c = cfg(); return c.repo.includes('/') && c.token.length > 10; };

  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  // ---------------------------------------------------------------- GitHub API
  async function gh(path, opts = {}) {
    const c = cfg();
    const res = await fetch(API + path, {
      ...opts,
      headers: {
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Authorization': 'Bearer ' + c.token,
        ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
        ...(opts.headers || {}),
      },
    });
    if (res.status === 204) return null;
    let body = null;
    try { body = await res.json(); } catch {}
    if (!res.ok) throw new ApiError(res.status, body);
    return body;
  }

  class ApiError extends Error {
    constructor(status, body) {
      super((body && body.message) || ('HTTP ' + status));
      this.status = status;
    }
    get turkish() {
      switch (this.status) {
        case 401: return 'Anahtar gecersiz veya suresi dolmus. Ayarlardan yenile.';
        case 403: return 'Anahtarin bu depoda Actions yazma yetkisi yok.';
        case 404: return 'Depo ya da is akisi bulunamadi. Depo adini ve scan.yml dosyasini kontrol et.';
        case 422: return 'Is akisi tetiklenemedi. scan.yml varsayilan dala gonderilmis mi?';
        default: return this.message;
      }
    }
  }

  async function defaultBranch() {
    const c = cfg();
    if (c.branch) return c.branch;
    const repo = await gh(`/repos/${c.repo}`);
    store.set(KEY.branch, repo.default_branch);
    return repo.default_branch;
  }

  async function latestRunId() {
    const c = cfg();
    try {
      const d = await gh(`/repos/${c.repo}/actions/workflows/${c.wf}/runs?per_page=1`);
      return d.workflow_runs && d.workflow_runs[0] ? d.workflow_runs[0].id : 0;
    } catch { return 0; }
  }

  async function dispatch(inputs) {
    const c = cfg();
    const ref = await defaultBranch();
    await gh(`/repos/${c.repo}/actions/workflows/${c.wf}/dispatches`, {
      method: 'POST',
      body: JSON.stringify({ ref, inputs }),
    });
  }

  // ------------------------------------------------------------ durum seridi
  const bar = () => $('#ft-status');

  function status(text, kind = 'busy') {
    const b = bar();
    if (!b) return;
    b.className = 'ft-status ' + kind;
    b.textContent = text;
    b.hidden = false;
  }
  function clearStatus(delay = 0) {
    setTimeout(() => { const b = bar(); if (b) b.hidden = true; }, delay);
  }

  // Tetikledikten sonra yeni kosunun bitmesini bekler
  async function waitForRun(sinceId, onTick) {
    const c = cfg();
    const deadline = Date.now() + 12 * 60 * 1000;   // en fazla 12 dakika
    let runId = 0;

    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 4000));
      try {
        const d = await gh(`/repos/${c.repo}/actions/workflows/${c.wf}/runs?per_page=5`);
        const runs = d.workflow_runs || [];
        if (!runId) {
          const fresh = runs.find((r) => r.id > sinceId);
          if (fresh) runId = fresh.id;
          else { onTick('Tarama kuyruga alindi...'); continue; }
        }
        const run = runs.find((r) => r.id === runId) || await gh(`/repos/${c.repo}/actions/runs/${runId}`);
        if (run.status === 'completed') return run.conclusion;
        onTick(run.status === 'queued' ? 'Kuyrukta bekliyor...' : 'Fiyatlar taraniyor...');
      } catch (e) {
        onTick('Durum alinamadi, tekrar deneniyor...');
      }
    }
    return 'timed_out';
  }

  // GitHub Pages yayini commit'ten sonra dakikalar surebiliyor. Sayfayi erken
  // yenilersek kullanici eski veriyi gorur; bu yuzden yeni raporun gercekten
  // yayina girdigini damgayi karsilastirarak dogrularız.
  async function waitForPublish(maxMs = 5 * 60 * 1000) {
    const meta = document.querySelector('meta[name="ft-generated"]');
    const current = meta ? meta.content : '';
    if (!current) { await new Promise((r) => setTimeout(r, 20000)); return; }

    const started = Date.now();
    const deadline = started + maxMs;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 8000));
      try {
        const res = await fetch(location.pathname + '?p=' + Date.now(), { cache: 'no-store' });
        const html = await res.text();
        const m = html.match(/<meta name="ft-generated" content="([^"]*)"/);
        if (m && m[1] && m[1] !== current) return;
      } catch {}
      // Gecen sureyi gosteririz; kalan sureyi gostermek "bu kadar beklemelisin"
      // gibi okunuyordu
      const waited = Math.round((Date.now() - started) / 1000);
      status(`GitHub Pages yayini bekleniyor... (${waited} sn)`, 'ok');
    }
  }

  async function runWorkflow(inputs, busyText) {
    if (!ready()) { openSheet('settings'); status('Once depo ve anahtari gir.', 'warn'); clearStatus(4000); return; }
    document.body.classList.add('ft-busy');
    status(busyText);
    try {
      const since = await latestRunId();
      await dispatch(inputs);
      const result = await waitForRun(since, (t) => status(t));
      if (result === 'success') {
        status('Tarama bitti, yeni rapor bekleniyor...', 'ok');
        await waitForPublish();
        status('Guncel rapor yukleniyor...', 'ok');
        location.replace(location.pathname + '?t=' + Date.now());
      } else if (result === 'timed_out') {
        status('Tarama uzun surdu. Actions sekmesinden takip edebilirsin.', 'warn');
        clearStatus(8000);
      } else {
        status('Tarama basarisiz bitti (' + result + '). Actions gunlugune bak.', 'warn');
        clearStatus(8000);
      }
    } catch (e) {
      status(e instanceof ApiError ? e.turkish : ('Hata: ' + e.message), 'warn');
      clearStatus(8000);
    } finally {
      document.body.classList.remove('ft-busy');
    }
  }

  // ------------------------------------------------------------------ panolar
  function openSheet(name) {
    const s = $('#ft-sheet-' + name);
    if (!s) return;
    document.querySelectorAll('.ft-sheet').forEach((x) => (x.hidden = true));
    $('#ft-scrim').hidden = false;
    s.hidden = false;
    const first = s.querySelector('input, select');
    if (first) setTimeout(() => first.focus(), 120);
  }
  function closeSheets() {
    document.querySelectorAll('.ft-sheet').forEach((x) => (x.hidden = true));
    $('#ft-scrim').hidden = true;
  }

  // ------------------------------------------------------------------- kurulum
  function wire() {
    // Ayarlar panosunu mevcut degerlerle doldur
    const c = cfg();
    $('#ft-repo').value = c.repo;
    $('#ft-token').value = c.token;
    $('#ft-wf').value = c.wf;

    $('#ft-scrim').addEventListener('click', closeSheets);
    document.querySelectorAll('[data-ft-close]').forEach((b) =>
      b.addEventListener('click', closeSheets));

    $('#ft-open-settings').addEventListener('click', () => openSheet('settings'));
    $('#ft-open-add').addEventListener('click', () => {
      if (!ready()) { openSheet('settings'); return; }
      openSheet('add');
    });

    $('#ft-scan').addEventListener('click', () =>
      runWorkflow({ notify: 'true' }, 'Tarama baslatiliyor...'));

    // Ayarlari kaydet
    $('#ft-settings-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const repo = $('#ft-repo').value.trim().replace(/^https?:\/\/github\.com\//i, '').replace(/\.git$/, '').replace(/\/$/, '');
      store.set(KEY.repo, repo);
      store.set(KEY.token, $('#ft-token').value.trim());
      store.set(KEY.wf, $('#ft-wf').value.trim() || WF_DEFAULT);
      store.del(KEY.branch);                     // depo degismis olabilir

      const out = $('#ft-settings-msg');
      out.textContent = 'Baglanti deneniyor...';
      out.className = 'ft-msg';
      try {
        const branch = await defaultBranch();
        await gh(`/repos/${cfg().repo}/actions/workflows/${cfg().wf}`);
        out.textContent = `Baglandi. Varsayilan dal: ${branch}`;
        out.className = 'ft-msg ok';
        setTimeout(closeSheets, 1200);
        document.body.classList.add('ft-configured');
      } catch (err) {
        out.textContent = err instanceof ApiError ? err.turkish : err.message;
        out.className = 'ft-msg warn';
      }
    });

    $('#ft-forget').addEventListener('click', () => {
      [KEY.repo, KEY.token, KEY.wf, KEY.branch].forEach(store.del);
      $('#ft-repo').value = ''; $('#ft-token').value = ''; $('#ft-wf').value = WF_DEFAULT;
      $('#ft-settings-msg').textContent = 'Bu cihazdaki bilgiler silindi.';
      $('#ft-settings-msg').className = 'ft-msg';
      document.body.classList.remove('ft-configured');
    });

    // Urun ekle
    $('#ft-add-form').addEventListener('submit', (e) => {
      e.preventDefault();
      const url = $('#ft-add-url').value.trim();
      if (!url) return;
      // Ayni alan hem urun adini hem adresi kabul ediyor; hangisi oldugunu
      // sunucu tarafi (tracker add) kendisi anliyor.
      const inputs = { add_url: url, notify: 'true' };
      const name = $('#ft-add-name').value.trim();
      const target = $('#ft-add-target').value.trim();
      const exclude = $('#ft-add-exclude').value.trim();
      if (name) inputs.add_name = name;
      if (target) inputs.add_target = target;
      if (exclude) inputs.add_exclude = exclude;
      if ($('#ft-add-browser').checked) inputs.add_mode = 'browser';
      closeSheets();
      $('#ft-add-form').reset();
      runWorkflow(inputs, 'Urun ekleniyor ve taraniyor...');
    });

    // Urun silme dugmeleri (rapor tarafindan basiliyor)
    document.querySelectorAll('[data-ft-remove]').forEach((b) =>
      b.addEventListener('click', () => {
        const id = b.getAttribute('data-ft-remove');
        const name = b.getAttribute('data-ft-name') || id;
        if (!confirm(`"${name}" takipten cikarilsin mi?\n(Gecmis verisi silinmez.)`)) return;
        runWorkflow({ remove_id: id, notify: 'false' }, 'Urun cikariliyor...');
      }));

    if (ready()) document.body.classList.add('ft-configured');

    // iOS Safari'de aciksa "ana ekrana ekle" ipucunu goster
    const standalone = window.navigator.standalone === true ||
                       window.matchMedia('(display-mode: standalone)').matches;
    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);
    const hint = $('#ft-install');
    if (hint && isIOS && !standalone && store.get('ft.hint') !== 'off') {
      hint.hidden = false;
      $('#ft-install-close').addEventListener('click', () => {
        hint.hidden = true;
        store.set('ft.hint', 'off');
      });
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    wire();
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('./sw.js').catch(() => {});
    }
  });
})();
"""

# Uygulama kabugunun stilleri (rapor CSS'inin sonuna eklenir)
APP_CSS = """
/* --- uygulama kabugu --- */
body{padding:0 0 calc(56px + env(safe-area-inset-bottom))}
.wrap{padding:0 16px}
.ft-bar{position:sticky;top:0;z-index:20;
 padding:calc(10px + env(safe-area-inset-top)) 16px 10px;
 background:var(--surface-1);border-bottom:1px solid var(--border);
 backdrop-filter:saturate(180%) blur(12px)}
.ft-bar-inner{display:flex;align-items:center;gap:8px;max-width:980px;margin:0 auto}
.ft-bar .ft-title{font-weight:650;font-size:1rem;flex:1;letter-spacing:-.01em}
.ft-sub{margin:16px 0 0;color:var(--text-secondary);font-size:.86rem}
.ft-status,.ft-install{margin:14px 0 0}
.ft-btn{appearance:none;border:1px solid var(--border);background:var(--plane);
 color:var(--text-primary);border-radius:10px;min-width:40px;height:36px;padding:0 10px;
 font-size:.95rem;font-weight:600;cursor:pointer;display:inline-flex;align-items:center;
 justify-content:center;gap:6px;-webkit-tap-highlight-color:transparent}
.ft-btn:active{transform:scale(.97)}
.ft-btn.primary{background:var(--series-1);border-color:var(--series-1);color:#fff}
.ft-btn[disabled],body.ft-busy .ft-btn{opacity:.55;pointer-events:none}
.ft-status{padding:9px 12px;border-radius:10px;font-size:.85rem;
 background:var(--series-1-soft);color:var(--text-primary);border:1px solid var(--border)}
.ft-status.ok{background:var(--good-bg);color:var(--good)}
.ft-status.warn{background:var(--bad-bg);color:var(--bad)}
.ft-install{padding:10px 12px;border-radius:10px;font-size:.83rem;
 background:var(--series-1-soft);border:1px solid var(--border);display:flex;gap:10px;align-items:flex-start}
.ft-install button{margin-left:auto;background:none;border:none;color:var(--text-secondary);
 font-size:1.1rem;cursor:pointer;line-height:1;padding:0 2px}
#ft-scrim{position:fixed;inset:0;background:rgba(0,0,0,.42);z-index:40}
.ft-sheet{position:fixed;left:0;right:0;bottom:0;z-index:50;background:var(--surface-1);
 border-top-left-radius:18px;border-top-right-radius:18px;padding:18px 18px calc(22px + env(safe-area-inset-bottom));
 box-shadow:0 -8px 30px rgba(0,0,0,.2);max-height:88vh;overflow-y:auto;
 animation:ft-up .22s cubic-bezier(.22,.9,.3,1)}
@keyframes ft-up{from{transform:translateY(100%)}to{transform:translateY(0)}}
@media(min-width:620px){.ft-sheet{left:50%;right:auto;bottom:auto;top:50%;width:460px;
 transform:translate(-50%,-50%);border-radius:16px;animation:none}}
.ft-sheet h3{margin:0 0 4px;font-size:1.05rem}
.ft-sheet p.hint{margin:0 0 14px;font-size:.82rem;color:var(--text-secondary);line-height:1.45}
.ft-field{display:block;margin-bottom:12px}
.ft-field span{display:block;font-size:.8rem;color:var(--text-secondary);margin-bottom:5px}
.ft-field input[type=text],.ft-field input[type=url],.ft-field input[type=password],
.ft-field input[type=number]{width:100%;padding:11px 12px;font-size:16px;
 border:1px solid var(--border);border-radius:10px;background:var(--plane);
 color:var(--text-primary);font-family:inherit}
.ft-check{display:flex;align-items:center;gap:8px;font-size:.85rem;margin-bottom:14px}
.ft-actions{display:flex;gap:8px;margin-top:6px}
.ft-actions .ft-btn{flex:1;height:44px}
.ft-msg{font-size:.82rem;margin:10px 0 0;color:var(--text-secondary)}
.ft-msg.ok{color:var(--good)}
.ft-msg.warn{color:var(--bad)}
.ft-link{color:var(--series-1);text-decoration:none}
.ft-remove{appearance:none;border:none;background:none;color:var(--text-secondary);
 font-size:.78rem;cursor:pointer;padding:2px 6px;border-radius:6px;display:none}
body.ft-configured .ft-remove{display:inline-block}
.ft-remove:hover{background:var(--bad-bg);color:var(--bad)}
@media(max-width:520px){
 .tiles{grid-template-columns:repeat(2,1fr);gap:8px;margin:16px 0}
 .tile{padding:10px 12px}
 .tile .k{font-size:.72rem;margin-bottom:2px}
 .tile .v{font-size:1.25rem}
 .ft-sub{margin-top:12px;font-size:.82rem}
}
"""


def head_tags(base_title: str = APP_NAME) -> str:
    """index.html'in <head> bolumune giren PWA etiketleri."""
    return (
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        '<link rel="manifest" href="manifest.webmanifest">\n'
        f'<meta name="theme-color" content="{THEME_COLOR}">\n'
        '<meta name="mobile-web-app-capable" content="yes">\n'
        # iOS'ta tam ekran (Safari cubugu olmadan) acilmasini saglar
        '<meta name="apple-mobile-web-app-capable" content="yes">\n'
        '<meta name="apple-mobile-web-app-status-bar-style" content="default">\n'
        f'<meta name="apple-mobile-web-app-title" content="{base_title}">\n'
        '<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">\n'
        '<link rel="icon" href="icons/icon-192.png" sizes="192x192" type="image/png">\n'
    )


def app_bar() -> str:
    """Yapiskan ust cubuk (tam genislik)."""
    return f"""
<div class="ft-bar">
  <div class="ft-bar-inner">
    <span class="ft-title">{APP_NAME}</span>
    <button class="ft-btn" id="ft-open-add" title="Urun ekle" aria-label="Urun ekle">＋</button>
    <button class="ft-btn primary" id="ft-scan" title="Simdi tara">⟳ Tara</button>
    <button class="ft-btn" id="ft-open-settings" title="Ayarlar" aria-label="Ayarlar">⚙</button>
  </div>
</div>
"""


def app_strips(subtitle: str) -> str:
    """Durum seridi, kurulum ipucu ve alt baslik (icerik genisliginde)."""
    return f"""
<div class="ft-status" id="ft-status" hidden></div>
<div class="ft-install" id="ft-install" hidden>
  <span>Ana ekrana eklemek icin: Paylas <strong>&#8593;</strong> &rarr; <strong>Ana Ekrana Ekle</strong>.
  Uygulama gibi tam ekran acilir.</span>
  <button id="ft-install-close" aria-label="Kapat">&times;</button>
</div>
<p class="ft-sub">{subtitle}</p>
"""


def sheets() -> str:
    """Ayarlar ve urun ekleme panolari."""
    return """
<div id="ft-scrim" hidden></div>

<section class="ft-sheet" id="ft-sheet-settings" hidden role="dialog" aria-label="Ayarlar">
  <h3>Ayarlar</h3>
  <p class="hint">Tarama GitHub Actions'ta calisir. Telefondan baslatabilmek icin
  deponun adi ve bir erisim anahtari gerekiyor. Anahtar yalnizca bu telefonda saklanir,
  sayfanin kodunda yer almaz.</p>
  <form id="ft-settings-form">
    <label class="ft-field"><span>Depo (kullanici/repo)</span>
      <input type="text" id="ft-repo" placeholder="kullanici/fiyat-takip"
             autocapitalize="off" autocorrect="off" spellcheck="false" required></label>
    <label class="ft-field"><span>Erisim anahtari (fine-grained PAT)</span>
      <input type="password" id="ft-token" placeholder="github_pat_..."
             autocapitalize="off" autocorrect="off" spellcheck="false" required></label>
    <label class="ft-field"><span>Is akisi dosyasi</span>
      <input type="text" id="ft-wf" placeholder="scan.yml"
             autocapitalize="off" autocorrect="off" spellcheck="false"></label>
    <p class="hint">Anahtari <a class="ft-link" target="_blank" rel="noopener"
      href="https://github.com/settings/personal-access-tokens/new">github.com/settings/personal-access-tokens</a>
      adresinden olustur. Sadece bu depoyu sec ve tek yetki ver:
      <strong>Actions: Read and write</strong>.</p>
    <div class="ft-actions">
      <button type="button" class="ft-btn" data-ft-close>Vazgec</button>
      <button type="submit" class="ft-btn primary">Kaydet ve dene</button>
    </div>
    <p class="ft-msg" id="ft-settings-msg"></p>
    <div class="ft-actions"><button type="button" class="ft-btn" id="ft-forget">Bu cihazdaki bilgileri sil</button></div>
  </form>
</section>

<section class="ft-sheet" id="ft-sheet-add" hidden role="dialog" aria-label="Urun ekle">
  <h3>Urun ekle</h3>
  <p class="hint">Urun <strong>adini</strong> yaz &mdash; pazaryerlerinde aranir ve en ucuz
  teklif takip edilir. Belirli bir saticiyi izlemek istersen onun yerine
  <strong>adresini</strong> yapistir.</p>
  <form id="ft-add-form">
    <label class="ft-field"><span>Urun adi veya adresi</span>
      <input type="text" id="ft-add-url" placeholder="BeSafe iZi Turn B i-Size"
             autocapitalize="off" autocorrect="off" spellcheck="false" required></label>
    <label class="ft-field"><span>Listede gorunecek isim (istege bagli)</span>
      <input type="text" id="ft-add-name" placeholder="Oto koltugu"></label>
    <label class="ft-field"><span>Hedef fiyat (istege bagli)</span>
      <input type="number" id="ft-add-target" inputmode="decimal" step="0.01" placeholder="45000"></label>
    <label class="ft-field"><span>Haric tut (virgulle ayir, istege bagli)</span>
      <input type="text" id="ft-add-exclude" placeholder="kilif, aksesuar, yenilenmis"></label>
    <label class="ft-check"><input type="checkbox" id="ft-add-browser">
      Tarayici modu (yalnizca adres verdiysen, Amazon gibi zor siteler icin)</label>
    <div class="ft-actions">
      <button type="button" class="ft-btn" data-ft-close>Vazgec</button>
      <button type="submit" class="ft-btn primary">Ekle ve tara</button>
    </div>
  </form>
</section>
"""


def write_assets(docs_dir: Path) -> None:
    """manifest.webmanifest, sw.js ve app.js dosyalarini yazar.

    Ikonlar `tools/make_icons.py` ile bir kez uretilip depoya konur; burada
    yeniden uretilmez.
    """
    docs_dir = Path(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "manifest.webmanifest").write_text(
        json.dumps(MANIFEST, ensure_ascii=False, indent=2), encoding="utf-8")
    (docs_dir / "sw.js").write_text(SW_JS.strip() + "\n", encoding="utf-8")
    (docs_dir / "app.js").write_text(APP_JS.strip() + "\n", encoding="utf-8")
    # GitHub Pages varsayilan olarak Jekyll calistirir; alt cizgili/ozel
    # dosyalarin dokunulmadan yayinlanmasi icin bunu kapatiyoruz.
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
