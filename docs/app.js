/* Fiyat Takip — telefon uygulamasi.
 *
 * Veri: index.html'e gomulu paket (docs/data.json ile ayni). Tarama/arama
 * GitHub Actions'ta calisir; uygulama is akisini tetikler, kosuyu takip eder
 * ve bitince yeni veriyi dogrudan GitHub API'den okur (Pages yayinini
 * beklemeden).
 *
 * Islemler (ekle, hedef degistir, "bu urun degil"...) yerel bir kuyruga
 * yazilir; uygulama kapatilip acilsa da takip kaldigi yerden surer. Tarama
 * is akisi ayni anda tek kosu calistirdigi icin uygulama da kendi
 * degisikliklerini sirayla, birikenleri tek seferde gonderir.
 */
(() => {
  'use strict';

  const API = 'https://api.github.com';
  const SCAN_WF = 'scan.yml';
  const SEARCH_WF = 'ara.yml';
  const RESULT_BRANCH = 'ft-veri';
  const TZ = 'Europe/Istanbul';
  const LS = {
    token: 'ft.token', repo: 'ft.repo', branch: 'ft.branch', ops: 'ft.ops.v2',
    cache: 'ft.cache.v2', recent: 'ft.recent', prefs: 'ft.prefs', hint: 'ft.hint',
  };

  // ------------------------------------------------------------------ depolama
  // localStorage gizli pencerede ya da site verisi kapaliyken patlayabilir
  const store = {
    get(k, d = '') { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch {} },
    del(k) { try { localStorage.removeItem(k); } catch {} },
    json(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } },
    put(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
  };

  // ------------------------------------------------------------------ yardimci
  const $ = (sel, root = document) => root.querySelector(sel);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const nf2 = new Intl.NumberFormat('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const nf0 = new Intl.NumberFormat('tr-TR', { maximumFractionDigits: 0 });
  const nf1 = new Intl.NumberFormat('tr-TR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  const CUR = { TRY: 'TL', USD: '$', EUR: '€', GBP: '£' };

  function money(v, cur = 'TRY') {
    if (v == null || isNaN(v)) return '—';
    const whole = Math.abs(v - Math.round(v)) < 0.005;
    return (whole ? nf0.format(Math.round(v)) : nf2.format(v)) + ' ' + (CUR[cur] || cur);
  }
  function priceHtml(v, opts = {}) {
    if (v == null) return '<span class="price stale">—</span>';
    const whole = Math.abs(v - Math.round(v)) < 0.005;
    const num = whole ? nf0.format(Math.round(v)) : nf2.format(v);
    const cls = ['price', opts.tag ? 'tag' : '', opts.stale ? 'stale' : ''].filter(Boolean).join(' ');
    return `<span class="${cls}">${num}<span class="cur">${esc(CUR[opts.cur] || opts.cur || 'TL')}</span></span>`;
  }
  const pct = (p) => '%' + nf1.format(Math.abs(p));

  const dtf = (o) => new Intl.DateTimeFormat('tr-TR', { timeZone: TZ, ...o });
  const fDay = dtf({ day: 'numeric', month: 'short' });
  const fTime = dtf({ hour: '2-digit', minute: '2-digit' });
  const fYmd = dtf({ year: 'numeric', month: '2-digit', day: '2-digit' });
  function when(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000), now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60) return 'az önce';
    if (diff < 3600) return `${Math.floor(diff / 60)} dk önce`;
    const today = fYmd.format(now), day = fYmd.format(d);
    const y = new Date(now - 86400000);
    if (day === today) return 'bugün ' + fTime.format(d);
    if (day === fYmd.format(y)) return 'dün ' + fTime.format(d);
    return fDay.format(d) + ' ' + fTime.format(d);
  }
  const shortDate = (ts) => (ts ? fDay.format(new Date(ts * 1000)) : '');

  const SITE_LABEL = {
    'trendyol.com': 'Trendyol', 'hepsiburada.com': 'Hepsiburada', 'amazon.com.tr': 'Amazon',
    'n11.com': 'n11', 'pazarama.com': 'Pazarama', 'teknosa.com': 'Teknosa',
    'mediamarkt.com.tr': 'MediaMarkt', 'ciceksepeti.com': 'Çiçeksepeti',
  };
  const siteLabel = (s) => SITE_LABEL[(s || '').replace(/^www\./, '')] || (s || '');
  const looksLikeUrl = (t) => /^(https?:\/\/|www\.)/i.test((t || '').trim());

  const STATUS = {
    ok: '', suspicious: 'Şüpheli fiyat, doğrula', blocked: 'Site engelledi',
    parse_failed: 'Fiyat okunamadı', error: 'Okunamadı', no_match: 'Eşleşen ürün bulunamadı',
    pending: 'İlk tarama bekleniyor', disabled: 'Duraklatıldı',
  };

  const ICON = {
    list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z"/><circle cx="7.6" cy="7.6" r="1.4"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/></svg>',
    settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2.2"/><circle cx="10" cy="17" r="2.2"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
    ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" class="ok-ico"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
    bad: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" class="bad-ico"><path d="M12 7v6M12 17h.01"/></svg>',
    spinner: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" class="spin"><path d="M12 3a9 9 0 1 0 9 9"/></svg>',
    ext: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  };

  // ------------------------------------------------------------------ durum
  const S = {
    data: null,
    dataFrom: '',
    tab: 'list',
    filter: 'all',
    sort: store.json(LS.prefs, {}).sort || 'change',
    detail: null,
    range: '90',
    editing: false,
    search: { q: '', sites: null, exclude: '', min: '', max: '', state: 'idle', opId: null,
              result: null, off: new Set(), on: new Set(), name: '', target: '', error: '' },
    ops: store.json(LS.ops, []),
    conn: { state: 'unknown', msg: '' },
    refreshing: false,
    drafts: {},
  };

  const token = () => store.get(LS.token).trim();
  function repoName() {
    const saved = store.get(LS.repo).trim();
    if (saved) return saved;
    if (S.data && S.data.repo) return S.data.repo;
    const m = location.hostname.match(/^([^.]+)\.github\.io$/i);
    const seg = location.pathname.split('/').filter(Boolean)[0];
    return m && seg ? `${m[1]}/${seg}` : '';
  }
  const connected = () => repoName().includes('/') && token().length > 10;
  const products = () => (S.data && S.data.products) || [];
  const byId = (id) => products().find((p) => p.id === id);

  // ------------------------------------------------------------------ GitHub API
  class ApiError extends Error {
    constructor(status, message) { super(message || ('HTTP ' + status)); this.status = status; }
    get human() {
      switch (this.status) {
        case 401: return 'Erişim anahtarı geçersiz ya da süresi dolmuş. Ayarlar\'dan yenisini gir.';
        case 403: return 'Anahtarın bu depoda yetkisi yok. "Actions: Read and write" izni verilmeli.';
        case 404: return 'Depo ya da iş akışı bulunamadı. Depo adını kontrol et.';
        case 422: return 'GitHub isteği kabul etmedi: ' + this.message;
        default: return this.message;
      }
    }
  }

  async function gh(path, { method = 'GET', body, raw = false, auth = true } = {}) {
    const headers = { 'Accept': raw ? 'application/vnd.github.raw+json' : 'application/vnd.github+json' };
    // Anahtarsiz okumalar "basit istek" kalsin (on kontrol istegi gerekmesin)
    if (auth && token()) {
      headers.Authorization = 'Bearer ' + token();
      headers['X-GitHub-Api-Version'] = '2022-11-28';
    }
    if (body) headers['Content-Type'] = 'application/json';
    const res = await fetch(API + path, { method, headers, cache: 'no-store',
                                          body: body ? JSON.stringify(body) : undefined });
    if (res.status === 204) return null;
    const text = await res.text();
    if (!res.ok) {
      let msg = '';
      try { msg = JSON.parse(text).message; } catch {}
      throw new ApiError(res.status, msg || text.slice(0, 140));
    }
    if (raw) return text;
    try { return text ? JSON.parse(text) : null; } catch { return text; }
  }

  // Herkese acik depoda anahtar dar yetkiliyse icerik okumasi 403/404 donebilir;
  // o durumda anahtarsiz tekrar deneriz.
  async function readRepoFile(path, ref) {
    const url = `/repos/${repoName()}/contents/${path}?ref=${encodeURIComponent(ref)}`;
    try {
      return await gh(url, { raw: true });
    } catch (e) {
      if (token() && e instanceof ApiError && [401, 403, 404].includes(e.status)) {
        return await gh(url, { raw: true, auth: false });
      }
      throw e;
    }
  }

  async function defaultBranch() {
    const saved = store.get(LS.branch);
    if (saved) return saved;
    const info = await gh(`/repos/${repoName()}`);
    store.set(LS.branch, info.default_branch);
    return info.default_branch;
  }

  // ------------------------------------------------------------------ veri
  function readEmbedded() {
    try { return JSON.parse($('#ft-data').textContent); } catch { return null; }
  }

  function acceptData(data, from) {
    if (!data || !Array.isArray(data.products)) return false;
    if (S.data && (data.generated_at || 0) < (S.data.generated_at || 0)) return false;
    const changed = !S.data || data.generated_at !== S.data.generated_at;
    S.data = data;
    S.dataFrom = from;
    if (from !== 'cache') store.put(LS.cache, data);
    return changed;
  }

  // Tazeleme sirasi: once GitHub API (aninda guncel), olmazsa Pages kopyasi
  async function refreshData({ quiet = false } = {}) {
    if (S.refreshing) return;
    S.refreshing = true;
    if (!quiet) render();
    let changed = false;
    try {
      if (repoName()) {
        try {
          const branch = connected() ? await defaultBranch() : 'main';
          const text = await readRepoFile('docs/data.json', branch);
          changed = acceptData(JSON.parse(text), 'api');
        } catch (e) {
          const res = await fetch('data.json?t=' + Date.now(), { cache: 'no-store' });
          if (res.ok) changed = acceptData(await res.json(), 'pages');
        }
      } else {
        const res = await fetch('data.json?t=' + Date.now(), { cache: 'no-store' });
        if (res.ok) changed = acceptData(await res.json(), 'pages');
      }
      if (!quiet) toast(changed ? 'Veriler güncellendi' : 'Veriler zaten güncel');
    } catch (e) {
      if (!quiet) toast('Veriler alınamadı. Bağlantını kontrol et.');
    } finally {
      S.refreshing = false;
      render();
    }
  }

  // ------------------------------------------------------------------ islemler
  /* Islem: { id, kind: scan|apply|search, wf, inputs, ops?, label, state, runId, url,
              conclusion, error, created, dispatchedAt, retried }
     state: queued -> sending -> waiting -> running -> done | failed */
  const saveOps = () => store.put(LS.ops, S.ops.map((o) => ({ ...o })));
  const activeOps = () => S.ops.filter((o) => !['done', 'failed'].includes(o.state));

  function needConnection() {
    if (connected()) return false;
    S.tab = 'settings';
    S.detail = null;
    render();
    toast('Önce depoya bağlan: erişim anahtarını gir.');
    return true;
  }

  // Tarama is akisina gidecek degisiklik. Bekleyen (henuz gonderilmemis)
  // bir uygulama islemi varsa ona eklenir; hepsi tek kosuda yapilir.
  function queueChange(op, label) {
    if (needConnection()) return;
    let target = S.ops.find((o) => o.kind === 'apply' && o.state === 'queued');
    if (!target) {
      target = { id: uid(), kind: 'apply', wf: SCAN_WF, ops: [], labels: [], state: 'queued', created: Date.now() };
      S.ops.push(target);
    }
    target.ops.push(op);
    target.labels.push(label);
    target.label = target.labels.length > 1 ? `${target.labels.length} değişiklik` : label;
    saveOps();
    pump();
    render();
  }

  function queueScan(ids, label) {
    if (needConnection()) return;
    if (ids && ids.length) {
      queueChange({ op: 'scan', ids }, label || 'Tarama');
      return;
    }
    if (S.ops.some((o) => o.kind === 'scan' && !['done', 'failed'].includes(o.state))) {
      toast('Tarama zaten sırada.');
      return;
    }
    S.ops.push({ id: uid(), kind: 'scan', wf: SCAN_WF, label: 'Tüm ürünler taranıyor', state: 'queued', created: Date.now() });
    saveOps();
    pump();
    render();
  }

  function inputsFor(op) {
    if (op.kind === 'search') return op.inputs;
    if (op.kind === 'apply') return { istek: op.id, komut: JSON.stringify({ ops: op.ops }), notify: 'true' };
    return { istek: op.id, notify: 'true' };
  }

  let pumping = false;
  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      while (true) {
        const live = S.ops.filter((o) => ['sending', 'waiting', 'running'].includes(o.state));
        // Tarama is akisi ayni anda tek kosu calistirir; araya giren yeni
        // istek beklemedekini iptal ettirir. Bu yuzden sirayla gonderiyoruz.
        const scanBusy = live.some((o) => o.wf === SCAN_WF);
        const next = S.ops.find((o) => o.state === 'queued' && (o.wf !== SCAN_WF || !scanBusy));
        if (next) { await send(next); continue; }
        if (!live.length) break;
        await sleep(3500);
        await Promise.all(live.map(poll));
      }
    } finally {
      pumping = false;
    }
  }

  async function send(op) {
    op.state = 'sending';
    render();
    try {
      const ref = await defaultBranch();
      await gh(`/repos/${repoName()}/actions/workflows/${op.wf}/dispatches`,
               { method: 'POST', body: { ref, inputs: inputsFor(op) } });
      op.state = 'waiting';
      op.dispatchedAt = Date.now();
    } catch (e) {
      op.state = 'failed';
      op.error = e instanceof ApiError ? e.human : 'Gönderilemedi: ' + e.message;
      if (op.kind === 'search') { S.search.state = 'error'; S.search.error = op.error; }
    }
    saveOps();
    render();
  }

  async function poll(op) {
    const repo = repoName();
    try {
      if (!op.runId) {
        const d = await gh(`/repos/${repo}/actions/workflows/${op.wf}/runs?event=workflow_dispatch&per_page=15`);
        const run = (d.workflow_runs || []).find((r) => (r.display_title || '').includes(op.id));
        if (!run) {
          if (Date.now() - op.dispatchedAt > 3 * 60 * 1000) fail(op, 'İşlem GitHub\'da başlamadı. Actions sekmesine bak.');
          return;
        }
        op.runId = run.id;
        op.url = run.html_url;
        await settle(op, run);
      } else {
        await settle(op, await gh(`/repos/${repo}/actions/runs/${op.runId}`));
      }
    } catch (e) {
      op.lastError = e.message;
      if (e instanceof ApiError && e.status === 401) fail(op, e.human);
    }
    if (Date.now() - (op.dispatchedAt || op.created) > 15 * 60 * 1000 && !['done', 'failed'].includes(op.state)) {
      fail(op, 'Çok uzun sürdü. Ayrıntılar için GitHub Actions sayfasına bak.');
    }
    saveOps();
    render();
  }

  function fail(op, msg) {
    op.state = 'failed';
    op.error = msg;
    if (op.kind === 'search' && S.search.opId === op.id) { S.search.state = 'error'; S.search.error = msg; }
  }

  async function settle(op, run) {
    if (run.status !== 'completed') {
      op.state = run.status === 'in_progress' ? 'running' : 'waiting';
      return;
    }
    op.conclusion = run.conclusion;
    if (run.conclusion === 'success') {
      if (op.kind === 'search') await loadSearchResult(op);
      else await refreshData({ quiet: true });
      op.state = 'done';
      op.doneAt = Date.now();
      if (op.kind !== 'search') toast(op.kind === 'scan' ? 'Tarama bitti, fiyatlar güncel.' : 'Değişiklikler uygulandı.');
      setTimeout(() => { S.ops = S.ops.filter((o) => o !== op); saveOps(); render(); }, 6000);
    } else if (run.conclusion === 'cancelled' && !op.retried) {
      // Arada baslayan zamanli tarama bekleyen kosuyu iptal ettirmis olabilir
      op.retried = true;
      op.state = 'queued';
      op.runId = null;
    } else {
      fail(op, run.conclusion === 'cancelled' ? 'İşlem iptal edildi.' : 'İşlem hata verdi. Ayrıntılara bak.');
    }
  }

  async function loadSearchResult(op) {
    for (let i = 0; i < 4; i++) {
      try {
        const text = await readRepoFile(`ara/${op.id}.json`, RESULT_BRANCH);
        const data = JSON.parse(text);
        if (S.search.opId === op.id) showSearchResult(data);
        remember(data);
        return;
      } catch (e) {
        await sleep(1500);
      }
    }
    fail(op, 'Sonuç dosyası okunamadı.');
  }

  // ------------------------------------------------------------------ arama
  function remember(data) {
    const list = store.json(LS.recent, []).filter((r) => r.query !== data.query);
    list.unshift({ query: data.query, at: Date.now(), data });
    // Sonuclar buyuk olabilir; son 6 aramayi tutmak yeter
    store.put(LS.recent, list.slice(0, 6));
  }

  function startSearch() {
    const q = S.search.q.trim();
    if (!q) return;
    if (looksLikeUrl(q)) { S.search.state = 'url'; render(); return; }
    if (needConnection()) return;
    const opts = {};
    if (S.search.sites) opts.sites = [...S.search.sites];
    const ex = S.search.exclude.split(',').map((s) => s.trim()).filter(Boolean);
    if (ex.length) opts.exclude = ex;
    if (S.search.min) opts.min_price = Number(S.search.min);
    if (S.search.max) opts.max_price = Number(S.search.max);
    const id = uid();
    S.ops.push({ id, kind: 'search', wf: SEARCH_WF, label: `"${q}" aranıyor`, state: 'queued', created: Date.now(),
                 inputs: { sorgu: q, istek: id, ayar: JSON.stringify(opts) } });
    Object.assign(S.search, { state: 'running', opId: id, result: null, error: '' });
    saveOps();
    pump();
    render();
  }

  function showSearchResult(data) {
    const r = data.result || {};
    S.search.result = data;
    S.search.state = 'done';
    S.search.off = new Set();
    S.search.on = new Set();
    S.search.name = S.search.name || data.query;
    if (!S.search.q) S.search.q = data.query;
    render();
  }

  function follow() {
    const data = S.search.result;
    if (!data) return;
    const r = data.result || {};
    const opts = data.options || {};
    const same = products().find((p) => p.kind === 'search' && (p.query || '').toLocaleLowerCase('tr') === data.query.toLocaleLowerCase('tr'));
    if (same) {
      toast('Bu arama zaten takipte. Ayarlarını ürün ekranından değiştirebilirsin.');
      S.tab = 'list'; S.detail = same.id; render();
      return;
    }
    const add = { op: 'add', query: data.query, name: (S.search.name || data.query).trim() };
    if (num(S.search.target)) add.target = num(S.search.target);
    if (opts.sites && opts.sites.length) add.sites = opts.sites;
    if (opts.exclude && opts.exclude.length) add.exclude = opts.exclude;
    if (opts.min_price) add.min_price = opts.min_price;
    if (opts.max_price) add.max_price = opts.max_price;
    const offKeys = (r.offers || []).filter((o) => S.search.off.has(o.key)).map((o) => o.key);
    const onKeys = (r.rejected || []).filter((o) => S.search.on.has(o.key)).map((o) => o.key);
    if (offKeys.length) add.exclude_listings = offKeys;
    if (onKeys.length) add.include_listings = onKeys;
    queueChange(add, `"${add.name}" ekleniyor`);
    S.tab = 'list';
    Object.assign(S.search, { state: 'idle', result: null, q: '', name: '', target: '' });
    render();
    toast('Takibe ekleniyor, ilk fiyat bir dakika içinde gelir.');
  }

  function followUrl() {
    const url = S.search.q.trim();
    const add = { op: 'add', url: url.startsWith('http') ? url : 'https://' + url };
    if (S.search.name.trim()) add.name = S.search.name.trim();
    if (num(S.search.target)) add.target = num(S.search.target);
    queueChange(add, 'Adres ekleniyor');
    S.tab = 'list';
    Object.assign(S.search, { state: 'idle', q: '', name: '', target: '' });
    render();
  }

  // ------------------------------------------------------------------ grafik
  function sparkline(hist, target, cls) {
    const pts = (hist || []).slice(-60);
    if (pts.length < 2) return '<svg class="spark" viewBox="0 0 120 26" preserveAspectRatio="none"></svg>';
    let lo = Math.min(...pts.map((p) => p[1])), hi = Math.max(...pts.map((p) => p[1]));
    const span = hi - lo || hi * 0.02 || 1;
    lo -= span * 0.1; hi += span * 0.1;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
    const x = (t) => ((t - t0) / ((t1 - t0) || 1)) * 116 + 2;
    const y = (v) => 24 - ((v - lo) / (hi - lo)) * 22;
    const d = pts.map((p, i) => `${i ? 'L' : 'M'}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join('');
    const last = pts[pts.length - 1];
    let tl = '';
    if (target != null && target >= lo && target <= hi) tl = `<line class="t" x1="0" x2="120" y1="${y(target).toFixed(1)}" y2="${y(target).toFixed(1)}"/>`;
    return `<svg class="spark ${cls || ''}" viewBox="0 0 120 26" preserveAspectRatio="none" aria-hidden="true">${tl}<path class="l" d="${d}" vector-effect="non-scaling-stroke"/></svg>`;
  }

  // Gorunum kutusu ekrandaki gercek genislige esit tutulur; boylece eksen
  // yazilari telefonda kuculup okunmaz hale gelmez.
  const chartWidth = () => Math.round(window.innerWidth >= 760 ? 586 : Math.max(280, window.innerWidth - 54));
  const CH = 210, PL = 6, PR = 54, PT = 14, PB = 24;
  function chartSvg(p) {
    const CW = chartWidth();
    const now = Date.now() / 1000;
    const days = S.range === 'all' ? Infinity : Number(S.range);
    let pts = (p.history || []).filter((h) => now - h[0] <= days * 86400);
    if (pts.length < 2) pts = (p.history || []).slice(-2);
    if (pts.length < 2) {
      return `<p class="muted small" style="margin:18px 6px">Grafik ikinci ölçümden sonra çıkar.${p.price != null ? ' Şu anki fiyat ' + esc(money(p.price)) + '.' : ''}</p>`;
    }
    const vals = pts.map((h) => h[1]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (p.target != null) { lo = Math.min(lo, p.target); hi = Math.max(hi, p.target); }
    const span = hi - lo || hi * 0.03 || 1;
    lo -= span * 0.12; hi += span * 0.12;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    const w = CW - PL - PR, h = CH - PT - PB;
    const x = (t) => PL + ((t - t0) / ((t1 - t0) || 1)) * w;
    const y = (v) => PT + (1 - (v - lo) / (hi - lo)) * h;
    // Basamakli cizgi: fiyat bir sonraki olcume kadar ayni kalir
    let d = '';
    pts.forEach((pt, i) => {
      const X = x(pt[0]).toFixed(1), Y = y(pt[1]).toFixed(1);
      if (!i) d += `M${X},${Y}`;
      else d += `H${X}V${Y}`;
    });
    const area = `${d}V${PT + h}H${PL}Z`;
    const grid = [0, 0.5, 1].map((f) => {
      const v = hi - (hi - lo) * f, Y = (PT + h * f).toFixed(1);
      return `<line class="grid" x1="${PL}" x2="${PL + w}" y1="${Y}" y2="${Y}"/>` +
             `<text class="axis" x="${CW - 4}" y="${(+Y + 3.5).toFixed(1)}" text-anchor="end">${esc(nf0.format(v))}</text>`;
    }).join('');
    let target = '';
    if (p.target != null) {
      const Y = y(p.target).toFixed(1);
      target = `<line class="target" x1="${PL}" x2="${PL + w}" y1="${Y}" y2="${Y}"/>` +
               `<text class="target-l" x="${PL + 4}" y="${(+Y - 5).toFixed(1)}">hedef</text>`;
    }
    const iMin = vals.indexOf(Math.min(...vals));
    const low = `<circle class="low" r="4.5" cx="${x(pts[iMin][0]).toFixed(1)}" cy="${y(pts[iMin][1]).toFixed(1)}"/>`;
    const lastPt = pts[pts.length - 1];
    const lastDot = `<circle class="cdot" r="4.5" cx="${x(lastPt[0]).toFixed(1)}" cy="${y(lastPt[1]).toFixed(1)}"/>`;
    const axis = `<text class="axis" x="${PL}" y="${CH - 6}">${esc(shortDate(t0))}</text>` +
                 `<text class="axis" x="${PL + w}" y="${CH - 6}" text-anchor="end">${esc(shortDate(t1))}</text>`;
    const json = esc(JSON.stringify(pts.map((pt) => [pt[0], pt[1], +x(pt[0]).toFixed(1), +y(pt[1]).toFixed(1)])));
    return `<svg class="chart" viewBox="0 0 ${CW} ${CH}" role="img" aria-label="Fiyat geçmişi grafiği" data-pts="${json}">
      ${grid}${target}<path class="area" d="${area}"/><path class="line" d="${d}"/>${low}${lastDot}${axis}
      <line class="cursor" y1="${PT}" y2="${PT + h}" x1="-10" x2="-10"/><circle class="cdot hov" r="5" cx="-10" cy="-10"/>
    </svg>`;
  }

  function chartInteract(svg) {
    if (!svg || svg.dataset.wired) return;
    svg.dataset.wired = '1';
    const pts = JSON.parse(svg.dataset.pts || '[]');
    const CW = svg.viewBox.baseVal.width;
    const tip = svg.parentElement.querySelector('.chart-tip');
    const cur = svg.querySelector('.cursor'), dot = svg.querySelector('.hov');
    const show = (clientX) => {
      const r = svg.getBoundingClientRect();
      const vx = ((clientX - r.left) / r.width) * CW;
      // Basamakli cizgide imlecin oldugu andaki fiyat, ondan onceki son olcumdur
      let pick = pts[0];
      for (const p of pts) if (p[2] <= vx) pick = p;
      cur.setAttribute('x1', vx.toFixed(1)); cur.setAttribute('x2', vx.toFixed(1));
      dot.setAttribute('cx', pick[2]); dot.setAttribute('cy', pick[3]);
      if (tip) tip.innerHTML = `<strong>${esc(money(pick[1]))}</strong> &nbsp;${esc(when(pick[0]))}`;
    };
    svg.addEventListener('pointermove', (e) => show(e.clientX));
    svg.addEventListener('pointerdown', (e) => show(e.clientX));
    svg.addEventListener('touchmove', (e) => { if (e.touches[0]) show(e.touches[0].clientX); }, { passive: true });
  }

  // ------------------------------------------------------------------ turetilmis
  function deal(p) {
    const hitTarget = p.target != null && p.price != null && !p.price_is_stale && p.price <= p.target;
    const level = p.deal && p.deal.level;
    const lowest = p.stats && p.stats.is_lowest_ever;
    return { hitTarget, level, lowest, good: hitTarget || lowest || level === 'lowest' || level === 'good' };
  }
  const isProblem = (p) => p.enabled && !['ok', 'suspicious', 'pending'].includes(p.status);
  const dropped = (p) => p.stats && p.stats.change_percent != null && p.stats.change_percent < -0.01;

  function filtered() {
    let list = products().slice();
    if (S.filter === 'deals') list = list.filter((p) => deal(p).good || dropped(p));
    if (S.filter === 'problems') list = list.filter(isProblem);
    const num = (v, d) => (v == null || isNaN(v) ? d : v);
    const sorters = {
      change: (a, b) => num(a.stats.change_percent, 999) - num(b.stats.change_percent, 999),
      target: (a, b) => num(a.target ? (a.price - a.target) / a.target : null, 999) - num(b.target ? (b.price - b.target) / b.target : null, 999),
      price: (a, b) => num(a.price, 1e12) - num(b.price, 1e12),
      name: (a, b) => a.name.localeCompare(b.name, 'tr'),
      added: (a, b) => (b.added_at || '').localeCompare(a.added_at || ''),
    };
    return list.sort(sorters[S.sort] || sorters.change);
  }

  // ------------------------------------------------------------------ gorunumler
  function thumb(src, name, cls = 'thumb') {
    const letter = esc((name || '?').trim().charAt(0).toLocaleUpperCase('tr'));
    if (!src) return `<div class="${cls}" aria-hidden="true">${letter}</div>`;
    return `<img class="${cls}" src="${esc(src)}" alt="" loading="lazy" referrerpolicy="no-referrer" data-letter="${letter}">`;
  }

  function deltaHtml(p) {
    const c = p.stats && p.stats.change_percent;
    if (c == null) return p.stats && p.stats.points ? '<span class="delta flat">ilk ölçüm</span>' : '';
    if (Math.abs(c) < 0.01) return '<span class="delta flat">değişmedi</span>';
    return c < 0 ? `<span class="delta down">↓ ${pct(c)}</span>` : `<span class="delta up">↑ ${pct(c)}</span>`;
  }

  function dealPill(p) {
    const d = deal(p);
    if (!p.enabled) return '<span class="pill">Duraklatıldı</span>';
    if (d.hitTarget) return '<span class="pill deal">Hedefte</span>';
    if (d.lowest || d.level === 'lowest') return '<span class="pill deal">En düşük fiyat</span>';
    if (d.level === 'good') return '<span class="pill good">İyi fiyat</span>';
    if (p.target != null && p.price != null) return `<span class="pill">Hedefe ${esc(money(p.price - p.target))}</span>`;
    if (d.level === 'high') return '<span class="pill bad">Pahalı dönem</span>';
    return '';
  }

  function rowHtml(p) {
    const d = deal(p);
    const status = STATUS[p.status] || p.status;
    const offers = p.offers ? p.offers.length : 0;
    const meta = [];
    if (status && p.status !== 'ok') meta.push(`<span style="color:var(--warn)">${esc(status)}</span>`);
    if (p.best && p.best.site) meta.push(esc(siteLabel(p.best.site)));
    if (p.kind === 'search' && offers > 1) meta.push(`${offers} teklif`);
    if (p.duplicate_of) meta.push('aynı ilan başka üründe de var');
    const sparkCls = d.good ? 'good' : (p.deal && p.deal.level === 'high' ? 'bad' : '');
    return `<button class="row" data-act="open" data-id="${esc(p.id)}">
      ${thumb(p.image, p.name)}
      <div class="row-main">
        <p class="row-name">${esc(p.name)}</p>
        <div class="row-meta">${meta.map((m) => `<span>${m}</span>`).join('')}</div>
      </div>
      <div class="row-side">${priceHtml(p.price, { tag: d.good && !p.price_is_stale, stale: p.price_is_stale || !p.enabled })}${deltaHtml(p)}</div>
      <div class="row-foot">${sparkline(p.history, p.target, sparkCls)}${dealPill(p)}</div>
    </button>`;
  }

  function opsHtml() {
    const list = S.ops.filter((o) => o.kind !== 'search' || o.state === 'failed');
    if (!list.length) return '';
    const sub = (o) => ({
      queued: 'Sırada', sending: 'GitHub\'a gönderiliyor', waiting: 'Başlıyor',
      running: o.kind === 'apply' && o.ops && o.ops.some((x) => x.op === 'add') ? 'Ekleniyor ve taranıyor' : 'Çalışıyor',
      done: 'Tamamlandı', failed: o.error || 'Başarısız',
    }[o.state]);
    return `<div class="ops">${list.map((o) => {
      const icon = o.state === 'done' ? ICON.ok : o.state === 'failed' ? ICON.bad : ICON.spinner;
      const secs = o.dispatchedAt && !['done', 'failed'].includes(o.state) ? `, <span data-since="${o.dispatchedAt}">${Math.round((Date.now() - o.dispatchedAt) / 1000)}</span> sn` : '';
      const btns = o.state === 'failed'
        ? `${o.url ? `<a class="btn small" href="${esc(o.url)}" target="_blank" rel="noopener">Ayrıntılar</a>` : ''}
           <button class="btn small" data-act="op-retry" data-id="${esc(o.id)}">Tekrar dene</button>
           <button class="icon-btn" data-act="op-drop" data-id="${esc(o.id)}" aria-label="Kapat">${ICON.close}</button>`
        : '';
      return `<div class="op ${o.state}">${icon}<div class="op-text"><div class="op-title">${esc(o.label || 'İşlem')}</div>
        <div class="op-sub">${esc(sub(o))}${secs}</div></div>${btns}</div>`;
    }).join('')}</div>`;
  }

  function listView() {
    const all = products();
    const addingAny = activeOps().some((o) => o.kind === 'apply' && (o.ops || []).some((x) => x.op === 'add'));
    if (!all.length && !addingAny) {
      return `${opsHtml()}<div class="empty">
        <h2>Takip listesi boş</h2>
        <p>Bir ürünün adını yaz; Trendyol, Hepsiburada, Amazon ve diğer mağazalarda aranır, en ucuz teklif takip edilir.</p>
        <button class="btn primary" data-act="tab" data-tab="search">${ICON.search} Ürün ara</button>
      </div>`;
    }
    const counts = { all: all.length, deals: all.filter((p) => deal(p).good || dropped(p)).length, problems: all.filter(isProblem).length };
    const seg = (id, label) => counts[id] || id === 'all'
      ? `<button class="seg" data-act="filter" data-f="${id}" aria-pressed="${S.filter === id}">${label}<span class="n">${counts[id]}</span></button>` : '';
    const rows = filtered();
    const gen = S.data.generated_at;
    const adding = activeOps().filter((o) => o.kind === 'apply')
      .flatMap((o) => (o.ops || []).filter((x) => x.op === 'add').map((x) => x.name || x.query || x.url));
    const addingRows = adding.map((name) => `<div class="row pending-row" aria-busy="true">
        ${thumb('', name)}
        <div class="row-main"><p class="row-name">${esc(name)}</p><div class="row-meta"><span>Ekleniyor, ilk fiyat bekleniyor</span></div></div>
        <div class="row-side">${ICON.spinner}</div></div>`).join('');
    return `
      ${opsHtml()}
      <p class="lead">Son tarama ${esc(when(gen) || '—')}.${S.data.schedule ? ` Otomatik tarama: ${esc(S.data.schedule.charAt(0).toLocaleLowerCase('tr') + S.data.schedule.slice(1))}.` : ''}</p>
      <div class="toolbar" role="toolbar" aria-label="Süzgeç">
        ${seg('all', 'Tümü')}${seg('deals', 'Fırsatlar')}${seg('problems', 'Sorunlu')}
        <label class="sort"><span hidden>Sırala</span>
          <select data-act="sort" aria-label="Sırala">
            ${[['change', 'Son değişim'], ['target', 'Hedefe yakın'], ['price', 'Fiyat'], ['name', 'Ad'], ['added', 'Yeni eklenen']]
              .map(([v, l]) => `<option value="${v}" ${S.sort === v ? 'selected' : ''}>${l}</option>`).join('')}
          </select></label>
      </div>
      ${rows.length || addingRows ? `<div class="group">${addingRows}${rows.map(rowHtml).join('')}</div>`
                    : '<p class="muted" style="text-align:center;margin:30px 0">Bu süzgeçte ürün yok.</p>'}
    `;
  }

  function offerRow(o, { mode, checked, why, actions } = {}) {
    const check = mode === 'check'
      ? `<input type="checkbox" class="check" data-act="${esc(o._toggle)}" data-key="${esc(o.key)}" ${checked ? 'checked' : ''} aria-label="Bu teklifi dahil et">` : '';
    return `<div class="offer ${mode === 'check' ? '' : 'no-check'} ${mode === 'check' && !checked ? 'off' : ''}">
      ${check}${thumb(o.image, o.title)}
      <div class="offer-main">
        <div class="offer-title"><a href="${esc(o.url)}" target="_blank" rel="noopener">${esc(o.title)}</a></div>
        <div class="offer-meta"><span>${esc(siteLabel(o.site))}</span>${o._best ? '<span class="best-mark">En ucuz</span>' : ''}${why ? `<span class="why">${esc(why)}</span>` : ''}</div>
      </div>
      <div class="offer-price">${priceHtml(o.price)}</div>
      ${actions ? `<div class="offer-actions">${actions}</div>` : ''}
    </div>`;
  }

  const REASON = {
    tokens: 'adı tutmuyor', variant: 'farklı model olabilir', outlier: 'şüpheli derecede ucuz',
    default_exclude: 'aksesuar / ikinci el', exclude: 'hariç kelime', must_include: 'zorunlu kelime yok',
    price: 'fiyat aralığı dışında', user: 'sen çıkardın',
  };
  const reasonText = (r) => {
    if (r.code === 'variant') { const m = (r.reason || '').match(/sorguda yok: ([^)]+)/); return 'farklı model' + (m ? ` (${m[1]})` : ''); }
    if (r.code === 'tokens') { const m = (r.reason || '').match(/eksik: (.+)\)$/); return m ? `eksik kelime: ${m[1]}` : REASON.tokens; }
    return REASON[r.code] || r.reason || '';
  };

  function siteSummary(perSite) {
    const entries = Object.entries(perSite || {});
    if (!entries.length) return '';
    return `<div class="site-sum">${entries.map(([id, s]) => {
      const label = (S.data.sources || []).find((x) => x.id === id)?.label || id;
      if (!s.ok) return `<span class="pill bad" title="${esc(s.note || '')}">${esc(label)}: açılmadı</span>`;
      return `<span class="pill ${s.kept ? 'good' : ''}">${esc(label)}: ${s.kept}/${s.found}</span>`;
    }).join('')}</div>`;
  }

  function searchView() {
    const s = S.search;
    const sources = (S.data && S.data.sources) || [];
    const selected = s.sites || new Set(sources.filter((x) => x.default).map((x) => x.id));
    const form = `
      <form class="searchbox" data-form="search" role="search">
        <input class="field-input" id="q" type="search" enterkeyhint="search" autocomplete="off" autocorrect="off"
               placeholder="Ürün adı ya da ürün adresi" value="${esc(s.q)}" aria-label="Ürün adı ya da adresi">
        <button class="btn primary" type="submit" ${s.state === 'running' ? 'disabled' : ''}>Ara</button>
      </form>
      <details class="opts" data-k="search-opts">
        <summary>Arama seçenekleri</summary>
        <div class="opts-body">
          <div class="field"><span>Mağazalar</span><div class="chips">
            ${sources.map((x) => `<button type="button" class="chip" data-act="site" data-site="${esc(x.id)}" aria-pressed="${selected.has(x.id)}">${esc(x.label)}</button>`).join('')}
          </div></div>
          <label class="field"><span>Hariç tutulacak kelimeler</span>
            <input class="field-input" data-bind="exclude" value="${esc(s.exclude)}" placeholder="kılıf, aksesuar, set" autocomplete="off">
            <small>Virgülle ayır. Aksesuar ve yenilenmiş ürünler zaten elenir.</small></label>
          <div class="two">
            <label class="field"><span>En az fiyat</span><input class="field-input" data-bind="min" inputmode="decimal" value="${esc(s.min)}" placeholder="—"></label>
            <label class="field"><span>En çok fiyat</span><input class="field-input" data-bind="max" inputmode="decimal" value="${esc(s.max)}" placeholder="—"></label>
          </div>
        </div>
      </details>`;

    if (s.state === 'url') {
      return `${form}<div class="form-card">
        <strong>Bu adresteki ürünü takip et</strong>
        <p class="muted small" style="margin:0">Belirli bir satıcının sayfası izlenir. Fiyat düşünce haber verilir.</p>
        <label class="field"><span>Listede görünecek ad (boş bırakılırsa sayfadan alınır)</span>
          <input class="field-input" data-bind="name" value="${esc(s.name)}"></label>
        <label class="field"><span>Hedef fiyat (isteğe bağlı)</span>
          <input class="field-input" data-bind="target" inputmode="decimal" value="${esc(s.target)}" placeholder="Bu fiyatın altına inince haber ver"></label>
        <button class="btn primary block" data-act="follow-url">Takip listesine ekle</button>
      </div>`;
    }

    if (s.state === 'running') {
      const op = S.ops.find((o) => o.id === s.opId);
      const st = op ? op.state : 'queued';
      const order = ['queued', 'sending', 'waiting', 'running', 'done'];
      const at = order.indexOf(st);
      const step = (i, label) => `<li class="${at > i ? 'done' : at === i || (i === 1 && at === 0) ? 'on' : ''}"><span class="b"></span>${label}</li>`;
      const secs = op && op.dispatchedAt ? Math.round((Date.now() - op.dispatchedAt) / 1000) : 0;
      return `${form}<div class="group progress">
        <ul class="steps">
          ${step(1, 'İstek GitHub\'a gönderiliyor')}
          ${step(2, 'Arama sunucusu hazırlanıyor')}
          ${step(3, 'Mağazalar taranıyor')}
        </ul>
        <p class="muted small" style="margin:14px 0 0">Genelde 20-40 saniye sürer${op && op.dispatchedAt ? ` (<span data-since="${op.dispatchedAt}">${secs}</span> sn)` : ''}. Bu sekmeden çıkabilirsin, sonuç burada bekler.</p>
      </div>`;
    }

    if (s.state === 'error') {
      return `${form}<div class="notice"><strong>Arama tamamlanamadı.</strong> ${esc(s.error)}</div>
        <button class="btn" data-act="search-again">Tekrar dene</button>`;
    }

    if (s.state === 'done' && s.result) {
      const data = s.result, r = data.result || {};
      const offers = (r.offers || []).map((o) => ({ ...o, _toggle: 'toggle-off' }));
      const near = (r.rejected || []).filter((x) => x.near).map((o) => ({ ...o, _toggle: 'toggle-on' }));
      const firstOn = offers.find((o) => !s.off.has(o.key));
      if (firstOn) firstOn._best = true;
      const included = offers.filter((o) => !s.off.has(o.key)).length + near.filter((o) => s.on.has(o.key)).length;
      const best = [...offers.filter((o) => !s.off.has(o.key)), ...near.filter((o) => s.on.has(o.key))]
        .sort((a, b) => a.price - b.price)[0];
      const warn = (r.warnings || []).map((w) => `<div class="notice"><strong>Dikkat:</strong> ${esc(w)}</div>`).join('');
      const list = offers.length
        ? `<div class="group">${offers.map((o) => offerRow(o, { mode: 'check', checked: !s.off.has(o.key) })).join('')}</div>`
        : `<div class="notice info">Bu adla eşleşen teklif bulunamadı. Sorguyu kısaltmayı dene (marka + model genelde yeter) ya da aşağıdaki yakın sonuçlara bak.</div>`;
      const nearHtml = near.length ? `
        <details class="section" data-k="search-near" ${offers.length ? '' : 'open'}><summary class="group-title" style="cursor:pointer;list-style:none">Elenen yakın sonuçlar (${near.length})</summary>
        <p class="muted small" style="margin:0 4px 8px">Doğru ürünse işaretle; takipte dahil edilir.</p>
        <div class="group">${near.map((o) => offerRow(o, { mode: 'check', checked: s.on.has(o.key), why: reasonText(o) })).join('')}</div></details>` : '';
      const followBox = included ? `
        <div class="follow">
          <div class="follow-sum"><strong>${included} teklif seçili</strong>${best ? `<span>En ucuz ${esc(money(best.price))}, ${esc(siteLabel(best.site))}</span>` : ''}</div>
          <button class="btn primary" data-act="follow-open">Takip et</button>
        </div>` : '';
      return `${form}
        <div class="result-head"><h2>${offers.length} teklif</h2><span class="muted small">${esc(when(data.generated_at))}</span></div>
        ${siteSummary(r.per_site)}${warn}${list}${nearHtml}${followBox}`;
    }

    const recent = store.json(LS.recent, []);
    return `${form}
      <div class="notice info">Ürünün <strong>adını</strong> yaz: tüm mağazalarda aranır, sonuçları görüp doğru olanları seçersin. Belirli bir satıcıyı izlemek için ürün <strong>adresini</strong> yapıştır.</div>
      ${recent.length ? `<div class="recent"><p class="group-title">Son aramalar</p><div class="group">${recent.map((r, i) => `
        <button class="recent-item" data-act="recent" data-i="${i}"><span>${esc(r.query)}</span>
        <span class="muted small">${(r.data.result && r.data.result.count) || 0} teklif, ${esc(when(r.data.generated_at))}</span></button>`).join('')}</div></div>` : ''}`;
  }

  function followSheet() {
    const s = S.search, data = s.result, r = data.result || {};
    const chosen = [...(r.offers || []).filter((o) => !s.off.has(o.key)), ...(r.rejected || []).filter((o) => s.on.has(o.key))]
      .sort((a, b) => a.price - b.price);
    const best = chosen[0];
    const suggest = best ? Math.floor((best.price * 0.95) / 100) * 100 : null;
    return `<div class="sheet-scrim" data-act="follow-close"></div>
      <section class="sheet compact" role="dialog" aria-modal="true" aria-label="Takibe ekle">
        <span class="grabber" aria-hidden="true"></span>
        <div class="sheet-head"><h2>Takibe ekle</h2><button class="icon-btn" data-act="follow-close" aria-label="Kapat">${ICON.close}</button></div>
        <div class="sheet-body">
          <div class="opts-body" style="padding:0">
            <label class="field"><span>Listede görünecek ad</span>
              <input class="field-input" data-bind="name" value="${esc(s.name || data.query)}" autocomplete="off"></label>
            <label class="field"><span>Hedef fiyat (isteğe bağlı)</span>
              <input class="field-input" data-bind="target" inputmode="decimal" value="${esc(s.target)}" placeholder="${suggest ? 'ör. ' + esc(nf0.format(suggest)) : ''}">
              <small>Fiyat bunun altına inince Telegram'dan haber verilir.</small></label>
            <p class="small" style="margin:0">Şu an en ucuz: <strong>${best ? esc(money(best.price)) : '—'}</strong>${best ? `, ${esc(siteLabel(best.site))}` : ''}. Her taramada "${esc(data.query)}" yeniden aranır; yeni ve daha ucuz satıcılar kendiliğinden bulunur, işaretini kaldırdığın ${s.off.size ? s.off.size + ' ilan' : 'ilanlar'} hesaba katılmaz.</p>
            <button class="btn primary block" data-act="follow">Takibe ekle</button>
          </div>
        </div>
      </section>`;
  }

  function settingsView() {
    const repo = repoName();
    const owner = repo.split('/')[0] || '';
    const tokenUrl = 'https://github.com/settings/personal-access-tokens/new?' + new URLSearchParams({
      name: 'Fiyat Takip uygulaması', description: 'Telefondaki Fiyat Takip uygulaması: tarama ve aramayı başlatır.',
      ...(owner ? { target_name: owner } : {}), expires_in: '366', actions: 'write', contents: 'read',
    }).toString();
    const c = S.conn;
    const led = connected() ? (c.state === 'bad' ? 'bad' : c.state === 'ok' ? 'ok' : '') : 'bad';
    const ledText = !connected() ? 'Bağlı değil: tarama ve arama için anahtar gerekli'
      : c.state === 'ok' ? c.msg : c.state === 'bad' ? c.msg : 'Anahtar kayıtlı';
    const d = S.data || {};
    const standalone = navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
    return `
      <div class="section" style="margin-top:6px">
        <h3>GitHub bağlantısı</h3>
        <div class="form-card">
          <div class="status-line ${led}"><span class="led"></span><span>${esc(ledText)}</span></div>
          <ol class="steps-guide">
            <li><a href="${esc(tokenUrl)}" target="_blank" rel="noopener"><strong>Anahtar oluşturma sayfasını aç</strong></a>. İzinler hazır gelir.</li>
            <li><strong>Repository access</strong> altında <strong>Only select repositories</strong> seç, <strong>${esc(repo.split('/')[1] || 'fiyat-takip')}</strong> deposunu işaretle.</li>
            <li><strong>Generate token</strong>'a bas, çıkan anahtarı kopyala ve aşağıya yapıştır.</li>
          </ol>
          <form data-form="settings" class="opts-body" style="padding:0">
            <label class="field"><span>Erişim anahtarı</span>
              <input class="field-input" id="tok" type="password" autocomplete="off" autocapitalize="off" spellcheck="false"
                     placeholder="github_pat_…" value="${esc(token())}"></label>
            <label class="field"><span>Depo</span>
              <input class="field-input" id="repo" autocomplete="off" autocapitalize="off" spellcheck="false"
                     placeholder="kullanici/fiyat-takip" value="${esc(repo)}"></label>
            <button class="btn primary block" type="submit">Kaydet ve bağlantıyı dene</button>
          </form>
          <p class="muted small" style="margin:0">Anahtar yalnızca bu telefonda saklanır, sayfanın kodunda yer almaz. Yalnızca iş akışlarını başlatma yetkisi vardır.</p>
        </div>
      </div>
      <div class="section">
        <h3>Tarama</h3>
        <div class="kvlist">
          <div><span>Otomatik tarama</span><span class="muted">${esc(d.schedule || '—')}</span></div>
          <div><span>Son tarama</span><span class="muted">${esc(when(d.generated_at) || '—')}</span></div>
          <div><span>Takip edilen</span><span class="muted">${products().length} ürün</span></div>
          <div><span>Mağazalar</span><span class="muted">${esc(((d.sources || []).filter((x) => x.default).map((x) => x.label)).join(', '))}</span></div>
        </div>
        <p class="muted small" style="margin:8px 4px 0">Fiyat düşünce, hedefin altına inince ve en düşük fiyat görülünce Telegram'dan haber verilir (kurulumu README'de). Sıklığı <code>scan.yml</code> içindeki cron satırı belirler.</p>
      </div>
      ${!standalone && /iPhone|iPad/.test(navigator.userAgent) ? `<div class="section"><h3>Ana ekrana ekle</h3>
        <div class="notice info">Safari'de <strong>Paylaş</strong> → <strong>Ana Ekrana Ekle</strong>. Uygulama gibi tam ekran açılır.</div></div>` : ''}
      <div class="section">
        <h3>Bu cihaz</h3>
        <div class="actions">
          <button class="btn" data-act="refresh">${ICON.refresh} Verileri yenile</button>
          <button class="btn danger" data-act="forget">Bilgileri sil</button>
        </div>
        <p class="muted small" style="margin:8px 4px 0">Sürüm ${esc(window.FT_VERSION || '')}, veri kaynağı: ${esc({ api: 'GitHub API', pages: 'GitHub Pages', cache: 'önbellek', embedded: 'sayfa' }[S.dataFrom] || S.dataFrom)}</p>
      </div>`;
  }

  function detailView(p) {
    const d = deal(p);
    const st = p.stats || {};
    const status = STATUS[p.status] || '';
    const pendingFor = activeOps().filter((o) => o.kind === 'apply' && (o.ops || []).some((x) => x.id === p.id));
    const bestSite = p.best && p.best.site ? siteLabel(p.best.site) : '';
    const offers = (p.offers || []).slice();
    const rejected = p.rejected || [];
    const near = rejected.filter((r) => r.near || r.code === 'user');
    const pendingReject = new Set(pendingFor.flatMap((o) => o.ops.flatMap((x) => x.reject || [])));
    const pendingInclude = new Set(pendingFor.flatMap((o) => o.ops.flatMap((x) => x.include || [])));
    const pinned = new Set((p.rules && p.rules.include_listings) || []);
    const pendingTargets = pendingFor.flatMap((o) => o.ops.filter((x) => x.id === p.id && x.set && 'target' in x.set).map((x) => x.set.target));
    const shownTarget = S.drafts[`${p.id}:target`] ?? (pendingTargets.length ? (pendingTargets[pendingTargets.length - 1] ?? '') : (p.target ?? ''));

    let meter = '';
    if (st.lowest != null && st.highest != null && st.highest > st.lowest && p.price != null) {
      const span = st.highest - st.lowest;
      const pos = Math.min(1, Math.max(0, (p.price - st.lowest) / span));
      const tpos = p.target != null ? Math.min(1, Math.max(0, (p.target - st.lowest) / span)) : null;
      const cap = p.deal ? ({
        lowest: 'Son 90 günün en düşük fiyatında.',
        good: `İyi bir fiyat: son 90 günün ortalamasından ${pct(p.deal.vs_average_percent)} ${p.deal.vs_average_percent < 0 ? 'ucuz' : 'pahalı'}.`,
        normal: `Ortalama civarında (ortalama ${money(p.deal.average)}).`,
        high: `Pahalı dönem: ortalamanın ${pct(p.deal.vs_average_percent)} üstünde. Beklemek mantıklı olabilir.`,
      }[p.deal.level] || '') : '';
      meter = `<div class="meter" aria-label="Fiyatın geçmişteki yeri">
        <div class="meter-bar">${tpos != null ? `<span class="meter-target" style="left:${(tpos * 100).toFixed(1)}%" title="hedef"></span>` : ''}
          <span class="meter-dot" style="left:${(pos * 100).toFixed(1)}%"></span></div>
        <div class="meter-labels"><span>En düşük ${esc(money(st.lowest))}${st.lowest_at ? ' (' + esc(shortDate(st.lowest_at)) + ')' : ''}</span><span>En yüksek ${esc(money(st.highest))}</span></div>
        ${cap ? `<p class="meter-caption">${esc(cap)}</p>` : ''}
      </div>`;
    }

    const offerActions = (o) => {
      if (p.kind !== 'search' || !o.key) return '';
      if (pendingReject.has(o.key)) return '<span class="pill warn">Çıkarılıyor…</span>';
      return `${pinned.has(o.key) ? '<span class="pill">Onayladın</span>' : ''}
        <button class="btn small" data-act="reject" data-id="${esc(p.id)}" data-key="${esc(o.key)}">Bu ürün değil</button>`;
    };
    const nearActions = (o) => {
      if (!o.key) return '';
      if (pendingInclude.has(o.key)) return '<span class="pill warn">Ekleniyor…</span>';
      if (o.code === 'user') return `<button class="btn small" data-act="unreject" data-id="${esc(p.id)}" data-key="${esc(o.key)}">Geri al</button>`;
      return `<button class="btn small" data-act="include" data-id="${esc(p.id)}" data-key="${esc(o.key)}">Bu da doğru ürün</button>`;
    };
    offers.forEach((o, i) => { o._best = i === 0; });

    const sources = (S.data.sources || []);
    const ruleSites = ruleSiteDraft || new Set((p.rules && p.rules.sites && p.rules.sites.length ? p.rules.sites : sources.filter((x) => x.default).map((x) => x.id)));
    const dv = (name, fallback) => (S.drafts[`${p.id}:${name}`] ?? fallback);
    const editForm = p.kind === 'search' ? `
      <details class="section opts" data-k="detail-rules" ${S.editing ? 'open' : ''}>
        <summary>Eşleştirme ayarları</summary>
        <form class="form-card" data-form="rules" data-id="${esc(p.id)}" style="margin-top:8px">
          <label class="field"><span>Aranan ad</span><input class="field-input" name="query" data-draft="${esc(p.id)}:query" value="${esc(dv('query', p.query))}" autocomplete="off"></label>
          <label class="field"><span>Hariç kelimeler</span><input class="field-input" name="exclude" data-draft="${esc(p.id)}:exclude" value="${esc(dv('exclude', (p.rules.exclude || []).join(', ')))}" placeholder="kılıf, set" autocomplete="off"></label>
          <label class="field"><span>Mutlaka geçmeli</span><input class="field-input" name="must_include" data-draft="${esc(p.id)}:must_include" value="${esc(dv('must_include', (p.rules.must_include || []).join(', ')))}" placeholder="ör. anthracite" autocomplete="off"></label>
          <div class="two">
            <label class="field"><span>En az fiyat</span><input class="field-input" name="min_price" data-draft="${esc(p.id)}:min_price" inputmode="decimal" value="${esc(dv('min_price', p.rules.min_price ?? ''))}"></label>
            <label class="field"><span>En çok fiyat</span><input class="field-input" name="max_price" data-draft="${esc(p.id)}:max_price" inputmode="decimal" value="${esc(dv('max_price', p.rules.max_price ?? ''))}"></label>
          </div>
          <div class="field"><span>Mağazalar</span><div class="chips">
            ${sources.map((x) => `<button type="button" class="chip" data-act="rule-site" data-site="${esc(x.id)}" aria-pressed="${ruleSites.has(x.id)}">${esc(x.label)}</button>`).join('')}
          </div></div>
          <button class="btn primary block" type="submit">Kaydet ve yeniden tara</button>
        </form>
      </details>` : '';

    return `
      <div class="hero">
        <div>${priceHtml(p.price, { tag: d.good && !p.price_is_stale, stale: p.price_is_stale })}</div>
        <div class="hero-side">${deltaHtml(p)}${dealPill(p)}</div>
      </div>
      <p class="where">${p.price_is_stale ? 'Son bilinen fiyat. ' : ''}${bestSite ? `En ucuz teklif: <a href="${esc(p.best.url)}" target="_blank" rel="noopener">${esc(bestSite)}</a>. ` : ''}${p.scanned_at ? `Ölçüldü: ${esc(when(p.scanned_at))}.` : ''}</p>
      ${status && p.status !== 'ok' ? `<div class="notice" style="margin-top:12px"><strong>${esc(status)}.</strong> ${esc(p.note || '')}</div>` : ''}
      ${(p.warnings || []).map((w) => `<div class="notice" style="margin-top:12px"><strong>Dikkat:</strong> ${esc(w)}</div>`).join('')}
      ${p.duplicate_of ? `<div class="notice info" style="margin-top:12px">Bu ürün, "${esc((byId(p.duplicate_of) || {}).name || p.duplicate_of)}" ile aynı ilanı takip ediyor. Biri fazlaysa silebilirsin.</div>` : ''}
      ${pendingFor.length ? `<div class="notice info" style="margin-top:12px">Değişiklikler uygulanıyor; bitince bu ekran güncellenir.</div>` : ''}
      <div class="actions">
        ${p.best && p.best.url ? `<a class="btn primary" href="${esc(p.best.url)}" target="_blank" rel="noopener">${ICON.ext} Mağazada aç</a>` : ''}
        <button class="btn" data-act="scan-one" data-id="${esc(p.id)}">${ICON.refresh} Şimdi tara</button>
      </div>
      ${meter}
      <div class="section">
        <h3>Fiyat geçmişi <span class="n">${st.points || 0} ölçüm</span></h3>
        <div class="chart-wrap">
          <div class="ranges" role="group" aria-label="Zaman aralığı">
            ${[['7', '7 gün'], ['30', '30 gün'], ['90', '90 gün'], ['all', 'Tümü']].map(([v, l]) => `<button class="seg" data-act="range" data-r="${v}" aria-pressed="${S.range === v}">${l}</button>`).join('')}
          </div>
          ${chartSvg(p)}
          <div class="chart-tip" aria-live="polite">Grafiğe dokun, o anki fiyatı gör.</div>
        </div>
      </div>
      <div class="section">
        <h3>Hedef fiyat</h3>
        <form class="inline" data-form="target" data-id="${esc(p.id)}">
          <input class="field-input" name="target" data-draft="${esc(p.id)}:target" inputmode="decimal" value="${esc(shownTarget)}" placeholder="Bu fiyatın altına inince haber ver" aria-label="Hedef fiyat">
          <button class="btn primary" type="submit">Kaydet</button>
        </form>
        ${pendingTargets.length ? '<p class="muted small" style="margin:6px 4px 0">Kaydediliyor…</p>' : ''}
      </div>
      ${offers.length ? `<div class="section"><h3>Teklifler <span class="n">${offers.length}</span></h3>
        <div class="group">${offers.map((o) => offerRow(o, { actions: offerActions(o) })).join('')}</div>
        ${p.kind === 'search' ? '<p class="muted small" style="margin:8px 4px 0">Yanlış bir ürün listedeyse "Bu ürün değil" de; sonraki taramalarda o ilan hesaba katılmaz.</p>' : ''}</div>` : ''}
      ${near.length ? `<details class="section" data-k="detail-near" ${offers.length ? '' : 'open'}><summary class="group-title" style="cursor:pointer;list-style:none">Elenen yakın sonuçlar (${near.length})</summary>
        <div class="group">${near.map((o) => offerRow(o, { why: reasonText(o), actions: nearActions(o) })).join('')}</div></details>` : ''}
      ${editForm}
      <div class="danger-zone">
        <button class="btn" data-act="toggle-enabled" data-id="${esc(p.id)}">${p.enabled ? 'Takibi duraklat' : 'Takibe devam et'}</button>
        <button class="btn danger" data-act="remove" data-id="${esc(p.id)}">Takipten çıkar</button>
      </div>`;
  }

  // ------------------------------------------------------------------ cizim
  let renderQueued = false;
  function render() {
    if (renderQueued) return;
    renderQueued = true;
    requestAnimationFrame(() => { renderQueued = false; draw(); });
  }

  function draw() {
    const app = $('#app');
    const focus = document.activeElement;
    const focusKey = focus && (focus.id || focus.dataset.bind || focus.dataset.draft || focus.name);
    const caret = focus && 'selectionStart' in focus ? focus.selectionStart : null;
    const scrollBody = $('.sheet-body') ? $('.sheet-body').scrollTop : 0;
    const openDetails = [...document.querySelectorAll('details[data-k]')].filter((x) => x.open).map((x) => x.dataset.k);

    const titles = { list: 'Fiyat Takip', search: 'Ara', settings: 'Ayarlar' };
    const busyScan = activeOps().some((o) => o.wf === SCAN_WF);
    const right = S.tab === 'list'
      ? `<button class="icon-btn" data-act="refresh" aria-label="Verileri yenile">${S.refreshing ? ICON.spinner : ICON.refresh}</button>
         <button class="btn primary" data-act="scan-all" ${busyScan ? 'disabled' : ''}>${busyScan ? 'Taranıyor' : 'Tara'}</button>`
      : '';
    const body = !S.data ? '<p class="muted" style="margin:40px 0;text-align:center">Yükleniyor…</p>'
      : S.tab === 'search' ? searchView() : S.tab === 'settings' ? settingsView() : listView();
    const p = S.detail && byId(S.detail);
    const confirmHtml = S.tab === 'search' && S.search.confirm && S.search.result ? followSheet() : '';
    const tabs = [['list', 'Takip', ICON.list], ['search', 'Ara', ICON.search], ['settings', 'Ayarlar', ICON.settings]];
    app.innerHTML = `
      <header class="topbar"><div class="topbar-row"><h1>${titles[S.tab]}</h1>${right}</div></header>
      <main class="main">${body}</main>
      <nav class="tabbar" aria-label="Sekmeler"><div class="tabbar-inner">${tabs.map(([id, label, icon]) =>
        `<button class="tab" data-act="tab" data-tab="${id}" ${S.tab === id ? 'aria-current="page"' : ''}>${icon}<span>${label}</span></button>`).join('')}
      </div></nav>
      ${p ? `<div class="sheet-scrim" data-act="close"></div>
        <section class="sheet" role="dialog" aria-modal="true" aria-label="${esc(p.name)}">
          <span class="grabber" aria-hidden="true"></span>
          <div class="sheet-head"><h2>${esc(p.name)}</h2><button class="icon-btn" data-act="close" aria-label="Kapat">${ICON.close}</button></div>
          <div class="sheet-body">${detailView(p)}</div>
        </section>` : ''}
      ${confirmHtml}
      ${S.toastText ? `<div class="toast" role="status">${esc(S.toastText)}</div>` : ''}`;

    document.body.style.overflow = p || confirmHtml ? 'hidden' : '';
    if (p) {
      const sb = $('.sheet-body');
      if (sb) sb.scrollTop = scrollBody;
      chartInteract($('.sheet .chart'));
      wireSheetDrag();
    }
    openDetails.forEach((k) => { const el = document.querySelector(`details[data-k="${k}"]`); if (el) el.open = true; });
    if (focusKey) {
      const sel = (a) => document.querySelector(`[${a}="${CSS.escape(focusKey)}"]`);
      const el = document.getElementById(focusKey) || sel('data-bind') || sel('data-draft') || sel('name');
      if (el && el !== document.activeElement) {
        el.focus({ preventScroll: true });
        if (caret != null && el.setSelectionRange) try { el.setSelectionRange(caret, caret); } catch {}
      }
    }
  }

  let toastTimer = null;
  function toast(text) {
    S.toastText = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { S.toastText = ''; render(); }, 2600);
    render();
  }

  function wireSheetDrag() {
    const sheet = $('.sheet'), head = $('.sheet-head');
    if (!sheet || !head || head.dataset.wired) return;
    head.dataset.wired = '1';
    let y0 = null, dy = 0;
    head.addEventListener('touchstart', (e) => { y0 = e.touches[0].clientY; dy = 0; }, { passive: true });
    head.addEventListener('touchmove', (e) => {
      if (y0 == null) return;
      dy = Math.max(0, e.touches[0].clientY - y0);
      sheet.style.transform = `translateY(${dy}px)`;
    }, { passive: true });
    head.addEventListener('touchend', () => {
      if (dy > 90) { S.detail = null; S.editing = false; render(); }
      else sheet.style.transform = '';
      y0 = null;
    });
  }

  // ------------------------------------------------------------------ olaylar
  function num(v) {
    const t = String(v || '').trim().replace(/\s/g, '');
    if (!t) return null;
    // "45.000" (TR binlik) ve "45000,50" yazimlarini kabul et
    const n = Number(/,\d{1,2}$/.test(t) ? t.replace(/\./g, '').replace(',', '.') : t.replace(/\.(?=\d{3}(\D|$))/g, '').replace(',', '.'));
    return isFinite(n) && n > 0 ? n : null;
  }
  const words = (v) => String(v || '').split(',').map((s) => s.trim()).filter(Boolean);

  let ruleSiteDraft = null;

  function onClick(e) {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const act = el.dataset.act;
    const id = el.dataset.id;
    const p = id ? byId(id) : null;
    switch (act) {
      case 'tab':
        S.tab = el.dataset.tab; S.detail = null; window.scrollTo(0, 0); render(); break;
      case 'open':
        S.detail = id; S.editing = false; ruleSiteDraft = null; S.range = (p && p.history && p.history.length > 1 && (Date.now() / 1000 - p.history[0][0]) < 7 * 86400) ? 'all' : '90'; render(); break;
      case 'close':
        S.detail = null; S.editing = false; ruleSiteDraft = null; S.drafts = {}; render(); break;
      case 'filter':
        S.filter = el.dataset.f; render(); break;
      case 'range':
        S.range = el.dataset.r; render(); break;
      case 'refresh':
        refreshData(); break;
      case 'scan-all':
        queueScan(null); break;
      case 'scan-one':
        queueScan([id], `"${p.name}" taranıyor`); break;
      case 'reject':
        queueChange({ op: 'update', id, reject: [el.dataset.key] }, `"${p.name}": ilan çıkarılıyor`); break;
      case 'unreject':
        queueChange({ op: 'update', id, unreject: [el.dataset.key] }, `"${p.name}": ilan geri alınıyor`); break;
      case 'include':
        queueChange({ op: 'update', id, include: [el.dataset.key] }, `"${p.name}": ilan ekleniyor`); break;
      case 'toggle-enabled':
        queueChange({ op: 'update', id, set: { enabled: !p.enabled } }, `"${p.name}" ${p.enabled ? 'duraklatılıyor' : 'yeniden takipte'}`); break;
      case 'remove':
        if (confirm(`"${p.name}" takipten çıkarılsın mı?\nFiyat geçmişi silinmez.`)) {
          queueChange({ op: 'remove', id }, `"${p.name}" çıkarılıyor`);
          S.detail = null; render();
        }
        break;
      case 'site': {
        const sources = S.data.sources || [];
        const set = S.search.sites || new Set(sources.filter((x) => x.default).map((x) => x.id));
        const sid = el.dataset.site;
        set.has(sid) ? set.delete(sid) : set.add(sid);
        S.search.sites = set.size ? set : null;
        render(); break;
      }
      case 'rule-site': {
        if (!ruleSiteDraft) {
          ruleSiteDraft = new Set([...document.querySelectorAll('[data-act="rule-site"]')]
            .filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.dataset.site));
        }
        const sid = el.dataset.site;
        ruleSiteDraft.has(sid) ? ruleSiteDraft.delete(sid) : ruleSiteDraft.add(sid);
        render();
        break;
      }
      case 'toggle-off':
        el.checked ? S.search.off.delete(el.dataset.key) : S.search.off.add(el.dataset.key); render(); break;
      case 'toggle-on':
        el.checked ? S.search.on.add(el.dataset.key) : S.search.on.delete(el.dataset.key); render(); break;
      case 'follow-open':
        S.search.confirm = true; render(); break;
      case 'follow-close':
        S.search.confirm = false; render(); break;
      case 'follow':
        S.search.confirm = false; follow(); break;
      case 'follow-url':
        followUrl(); break;
      case 'search-again':
        startSearch(); break;
      case 'recent': {
        const r = store.json(LS.recent, [])[Number(el.dataset.i)];
        if (r) { S.search.q = r.query; S.search.name = r.query; showSearchResult(r.data); }
        break;
      }
      case 'op-retry': {
        const op = S.ops.find((o) => o.id === id);
        if (op) { Object.assign(op, { state: 'queued', runId: null, error: '', url: '' }); saveOps(); pump(); render(); }
        break;
      }
      case 'op-drop':
        S.ops = S.ops.filter((o) => o.id !== id); saveOps(); render(); break;
      case 'forget':
        if (confirm('Anahtar, depo ve son aramalar bu cihazdan silinsin mi?')) {
          Object.values(LS).forEach(store.del); S.ops = []; S.conn = { state: 'unknown', msg: '' };
          toast('Bu cihazdaki bilgiler silindi.');
        }
        break;
      default:
    }
  }

  function onSubmit(e) {
    const form = e.target.closest('form[data-form]');
    if (!form) return;
    e.preventDefault();
    const kind = form.dataset.form;
    if (kind === 'search') {
      S.search.q = $('#q').value;
      S.search.name = S.search.q.trim();
      $('#q').blur();
      startSearch();
    } else if (kind === 'settings') {
      saveSettings($('#tok').value, $('#repo').value);
    } else if (kind === 'target') {
      const p = byId(form.dataset.id);
      const value = num(form.elements.target.value);
      queueChange({ op: 'update', id: p.id, set: { target: value } },
                  value ? `"${p.name}": hedef ${money(value)}` : `"${p.name}": hedef kaldırılıyor`);
      delete S.drafts[`${p.id}:target`];
      toast('Hedef kaydediliyor.');
    } else if (kind === 'rules') {
      const p = byId(form.dataset.id);
      const f = form.elements;
      const sites = [...form.querySelectorAll('[data-act="rule-site"]')].filter((b) => b.getAttribute('aria-pressed') === 'true').map((b) => b.dataset.site);
      const defaults = (S.data.sources || []).filter((x) => x.default).map((x) => x.id);
      const same = sites.length === defaults.length && sites.every((s) => defaults.includes(s));
      const set = {
        query: f.query.value.trim() || p.query,
        exclude: words(f.exclude.value),
        must_include: words(f.must_include.value),
        min_price: num(f.min_price.value),
        max_price: num(f.max_price.value),
        sites: same ? [] : sites,
      };
      queueChange({ op: 'update', id: p.id, set }, `"${p.name}": ayarlar kaydediliyor`);
      S.editing = false;
      ruleSiteDraft = null;
      Object.keys(S.drafts).filter((k) => k.startsWith(p.id + ':')).forEach((k) => delete S.drafts[k]);
      toast('Kaydedildi, yeniden taranıyor.');
    }
  }

  function onInput(e) {
    const el = e.target;
    if (el.id === 'q') { S.search.q = el.value; if (S.search.state === 'url' && !looksLikeUrl(el.value)) { S.search.state = 'idle'; render(); } return; }
    const bind = el.dataset && el.dataset.bind;
    if (bind) S.search[bind] = el.value;
    const draft = el.dataset && el.dataset.draft;
    if (draft) S.drafts[draft] = el.value;
  }

  function onChange(e) {
    const el = e.target;
    if (el.dataset && el.dataset.act === 'sort') {
      S.sort = el.value;
      store.put(LS.prefs, { ...store.json(LS.prefs, {}), sort: S.sort });
      render();
    }
  }

  async function saveSettings(tok, repo) {
    repo = (repo || '').trim().replace(/^https?:\/\/github\.com\//i, '').replace(/\.git$/, '').replace(/\/+$/, '');
    store.set(LS.token, (tok || '').trim());
    store.set(LS.repo, repo);
    store.del(LS.branch);
    S.conn = { state: 'unknown', msg: 'Deneniyor…' };
    render();
    try {
      const branch = await defaultBranch();
      await gh(`/repos/${repoName()}/actions/workflows/${SCAN_WF}`);
      let searchOk = true;
      try { await gh(`/repos/${repoName()}/actions/workflows/${SEARCH_WF}`); } catch { searchOk = false; }
      S.conn = { state: 'ok', msg: `Bağlandı (${repoName()}, dal: ${branch})${searchOk ? '' : '. Arama iş akışı (ara.yml) bulunamadı.'}` };
      toast('Bağlantı tamam.');
      refreshData({ quiet: true });
    } catch (err) {
      S.conn = { state: 'bad', msg: err instanceof ApiError ? err.human : err.message };
    }
    render();
  }

  // ------------------------------------------------------------------ baslat
  function init() {
    acceptData(readEmbedded(), 'embedded');
    const cached = store.json(LS.cache, null);
    if (cached) acceptData(cached, 'cache');
    if (!S.data) S.data = { products: [], sources: [] };

    // Eski (bitmis) islemleri temizle, yarim kalanlari surdur
    S.ops = S.ops.filter((o) => !['done'].includes(o.state) && Date.now() - (o.created || 0) < 6 * 3600 * 1000);
    const pendingSearch = S.ops.find((o) => o.kind === 'search' && !['done', 'failed'].includes(o.state));
    if (pendingSearch) {
      Object.assign(S.search, { state: 'running', opId: pendingSearch.id, q: pendingSearch.inputs.sorgu, name: pendingSearch.inputs.sorgu });
    }
    S.ops = S.ops.filter((o) => o.kind !== 'search' || o === pendingSearch);
    saveOps();

    document.addEventListener('click', onClick);
    document.addEventListener('submit', onSubmit);
    document.addEventListener('input', onInput);
    document.addEventListener('change', onChange);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && S.detail) { S.detail = null; render(); } });
    // Resim yuklenemezse bas harfe dus
    document.addEventListener('error', (e) => {
      const img = e.target;
      if (img && img.tagName === 'IMG' && img.classList.contains('thumb')) {
        const div = document.createElement('div');
        div.className = img.className;
        div.textContent = img.dataset.letter || '?';
        img.replaceWith(div);
      }
    }, true);
    window.addEventListener('scroll', () => {
      const bar = $('.topbar');
      if (bar) bar.classList.toggle('scrolled', window.scrollY > 4);
    }, { passive: true });
    // Islem surerken saniye sayaci
    setInterval(() => {
      document.querySelectorAll('[data-since]').forEach((el) => {
        el.textContent = String(Math.max(0, Math.round((Date.now() - Number(el.dataset.since)) / 1000)));
      });
    }, 1000);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) { pump(); if (!activeOps().length) refreshData({ quiet: true }); }
    });

    render();
    if (navigator.onLine !== false) refreshData({ quiet: true });
    if (activeOps().length) pump();
    if ('serviceWorker' in navigator && location.protocol.startsWith('http')) {
      navigator.serviceWorker.register('./sw.js').catch(() => {});
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
