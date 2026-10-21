/*
 * shim.js — Web 版の KWIC 画面で，サーバへの問い合わせを engine.js に回す
 * ======================================================================
 * 画面（kwic_app.html）はサーバ（16_kwic_server.py）の /api/... に問い合わせる。
 * Web 版にはサーバが無いので，その問い合わせをここで受け取り，Web Worker で
 * 動く engine.js に渡して，返ってきた結果をサーバの応答と同じ形で画面に返す。
 * 画面の側は手を加えずに使える（20_build_site.py が読み込みの1行を足すだけ）。
 *
 * 初めに索引（約 30 MB）を読み込む。読み込みが終わるまでは進み具合を表示する。
 */
(function () {
  'use strict';
  const worker = new Worker('engine.js');
  const pending = new Map();
  let seq = 0, readyResolve, readyReject;
  const ready = new Promise((res, rej) => { readyResolve = res; readyReject = rej; });

  // ---- 読み込み中の表示 -------------------------------------------------------
  const box = document.createElement('div');
  box.id = 'webload';
  box.setAttribute('role', 'status');
  box.innerHTML = '<div class="wl-card"><div class="wl-t">コーパスを読み込んでいる</div>'
    + '<div class="wl-bar"><i></i></div><div class="wl-n"></div>'
    + '<div class="wl-s">初回だけ約 30 MB を読み込む（2 回目からはブラウザに残ったものを使う）。'
    + '検索はすべてこのブラウザの中で行い，検索語はどこにも送らない。</div></div>';
  const css = document.createElement('style');
  css.textContent = '#webload{position:fixed;inset:0;z-index:999;background:rgba(247,247,244,.92);'
    + 'display:grid;place-items:center;font-family:inherit}'
    + '#webload .wl-card{background:#fff;border:1px solid #d8d7d0;border-radius:10px;padding:18px 22px;'
    + 'width:min(440px,calc(100vw - 32px));box-shadow:0 6px 20px rgba(0,0,0,.08)}'
    + '#webload .wl-t{font-weight:650;margin-bottom:10px}'
    + '#webload .wl-bar{height:8px;background:#eceae3;border-radius:4px;overflow:hidden}'
    + '#webload .wl-bar i{display:block;height:100%;width:0;background:#0072B2;transition:width .2s}'
    + '#webload .wl-n{font-size:12px;color:#5a5a55;margin-top:6px;font-variant-numeric:tabular-nums}'
    + '#webload .wl-s{font-size:12px;color:#5a5a55;margin-top:10px;line-height:1.6}'
    + '#webload.err .wl-t{color:#8a3b12}';
  const mount = () => { document.head.appendChild(css); document.body.appendChild(box); };
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);

  worker.onmessage = ev => {
    const m = ev.data;
    if (m.type === 'progress') {
      const p = m.total ? Math.min(100, m.got / m.total * 100) : 0;
      box.querySelector('i').style.width = p.toFixed(1) + '%';
      box.querySelector('.wl-n').textContent =
        `${(m.got / 1048576).toFixed(1)} / ${(m.total / 1048576).toFixed(1)} MB`;
    } else if (m.type === 'ready') {
      box.remove();
      readyResolve();
    } else if (m.type === 'fatal') {
      box.classList.add('err');
      box.querySelector('.wl-t').textContent = 'コーパスを読み込めなかった';
      box.querySelector('.wl-n').textContent = m.error;
      readyReject(new Error(m.error));
    } else if (m.type === 'reply') {
      const f = pending.get(m.id);
      if (f) { pending.delete(m.id); f(m); }
    }
  };
  worker.onerror = e => {
    box.classList.add('err');
    box.querySelector('.wl-t').textContent = '検索エンジンを起動できなかった';
    box.querySelector('.wl-n').textContent = String(e.message || e);
  };
  worker.postMessage({ type: 'boot', base: new URL('data/', location.href).href });

  function call(path, query, body) {
    return ready.then(() => new Promise(res => {
      const id = ++seq;
      pending.set(id, res);
      worker.postMessage({ type: 'call', id, path, query, body });
    }));
  }

  function parse(url) {
    const s = String(url);
    const i = s.indexOf('?');
    const path = i < 0 ? s : s.slice(0, i);
    const query = {};
    if (i >= 0) new URLSearchParams(s.slice(i + 1)).forEach((v, k) => { query[k] = v; });
    return { path, query };
  }

  // ---- fetch('/api/...') を受け取る ------------------------------------------
  const origFetch = window.fetch.bind(window);
  window.fetch = async function (url, opt) {
    if (typeof url !== 'string' || !url.startsWith('/api/')) return origFetch(url, opt);
    const { path, query } = parse(url);
    let body = null;
    if (opt && opt.body) { try { body = JSON.parse(opt.body); } catch (e) { body = {}; } }
    const r = await call(path, query, body);
    return new Response(JSON.stringify(r.json), {
      status: r.status, headers: { 'Content-Type': 'application/json; charset=utf-8' } });
  };

  // ---- CSV の書き出し（サーバ版の /api/export.csv の代わり）---------------------
  window.jlitExportCsv = async function (q) {
    const r = await call('/api/export.csv', { q: decodeURIComponent(q) }, null);
    if (r.status !== 200) { (window.showErr || console.error)((r.json && r.json.error) || '書き出せなかった'); return; }
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([r.csv], { type: 'text/csv;charset=utf-8' }));
    a.download = r.name.replace(/[\\/:*?"<>|]/g, '_');
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  };
})();
