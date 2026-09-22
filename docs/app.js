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
