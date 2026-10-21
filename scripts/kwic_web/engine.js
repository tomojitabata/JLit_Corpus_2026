/*
 * engine.js — JLit KWIC コンコーダンサの Web 版の検索エンジン
 * ==========================================================
 * scripts/kwic_core.py の KwicIndex を JavaScript に移したもの。サーバの
 * 要らない Web 版（scripts/20_build_site.py が作る）で，ブラウザの Web Worker
 * として動く。画面（kwic_app.html）は手を加えずに使い，画面が /api/... に
 * 問い合わせるところを shim.js がこのエンジンに回す。
 *
 * ⚠ 検索の意味は kwic_core.py と同じでなければならない。kwic_core.py を
 *   直したら，ここも同じように直し，scripts/check_kwic_web.py で突き合わせる
 *   （同じ検索を両方に掛けて結果を比べる）。
 *
 * kwic_core.py と違うところ（意図したもの）
 *   - サンプリング（sample）と無作為な並べ替え（sort=random）の乱数は
 *     numpy と同じ列にならない。同じシードなら同じ結果になるが，Python 版と
 *     同じ用例が選ばれるわけではない
 *   - 正規表現（re:）は JavaScript の正規表現として解釈する
 *   - 頻度が同じ語の並び順（一致した語形の表）は Python 版と違うことがある
 *
 * Node.js からも読める（module.exports）。検査スクリプトが使う。
 */
'use strict';

const API_VERSION = 2;
const EOS = 'EOS';
const PUNCT_POS = new Set(['補助記号', '空白']);
const COLL_MEASURES = {
  logdice: 'LogDice', mi: 'MI', mi3: 'MI3', t: 't', g2: 'G²',
  dp_fwd: 'ΔP（検索語→共起語）', dp_bwd: 'ΔP（共起語→検索語）', co: '共起頻度',
};

class QueryError extends Error {}

// ---- 小道具 -----------------------------------------------------------------
// Python の文字列比較（符号位置の順）。JS の既定の比較は UTF-16 の単位で
// 比べるので，BMP の後半と補助面の字の順が Python と食い違う。
function cpCompare(a, b) {
  if (a === b) return 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    const x = a.charCodeAt(i), y = b.charCodeAt(i);
    if (x === y) continue;
    const xs = x >= 0xD800 && x <= 0xDFFF, ys = y >= 0xD800 && y <= 0xDFFF;
    if (xs === ys) return x - y;
    return xs ? 1 : -1;       // 補助面（サロゲート）は BMP のどの字より後
  }
  return a.length - b.length;
}

// Python の round(x, d) に近いもの（表示用）
function rnd(x, d) {
  if (!isFinite(x)) return x;
  const p = Math.pow(10, d);
  return Math.round(x * p) / p;
}

// fnmatch.fnmatchcase の書き方を正規表現にする
function fnmatchRe(pat) {
  let i = 0, out = '';
  while (i < pat.length) {
    const c = pat[i++];
    if (c === '*') out += '.*';
    else if (c === '?') out += '.';
    else if (c === '[') {
      let j = i;
      if (j < pat.length && pat[j] === '!') j++;
      if (j < pat.length && pat[j] === ']') j++;
      while (j < pat.length && pat[j] !== ']') j++;
      if (j >= pat.length) out += '\\[';
      else {
        let stuff = pat.slice(i, j).replace(/\\/g, '\\\\');
        i = j + 1;
        if (stuff[0] === '!') stuff = '^' + stuff.slice(1);
        else if (stuff[0] === '^') stuff = '\\' + stuff;
        out += '[' + stuff + ']';
      }
    } else out += c.replace(/[.*+?^${}()|[\]\\\/]/g, '\\$&');
  }
  return new RegExp('^(?:' + out + ')$', 'su');
}

// 決まった種から乱数を作る（mulberry32）
function prng(seed) {
  let a = (seed >>> 0) || 1;
  return () => {
    a = (a + 0x6D2B79F5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function counterMostCommon(items) {
  // collections.Counter(...).most_common()：多い順，同数は最初に現れた順
  const m = new Map();
  for (const x of items) m.set(x, (m.get(x) || 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

// ---- 索引 -------------------------------------------------------------------
class KwicIndex {
  /**
   * @param meta  kwic_index.json と同じ形（provenance, works, vocab, band_labels）
   * @param arr   {surf, lem, pos12, sent, work: Int32Array/Uint8Array, orth?, cfm?, gos?}
   */
  constructor(meta, arr) {
    this.prov = meta.provenance;
    this.works = meta.works;
    this.band_labels = meta.band_labels;
    this.v = meta.vocab;
    this.a = arr;
    this.n = arr.surf.length;
    // 品詞の大分類は中分類から引く（Web 版は中分類だけを配る）
    if (!arr.pos) {
      const pmap = new Uint8Array(this.v.pos12.length);
      const pid = new Map(this.v.pos.map((p, i) => [p, i]));
      this.v.pos12.forEach((p, i) => {
        const p1 = p.split('-')[0];
        if (!pid.has(p1)) throw new Error('品詞の大分類が見つからない: ' + p);
        pmap[i] = pid.get(p1);
      });
      const pos = new Uint8Array(this.n), q = arr.pos12;
      for (let i = 0; i < this.n; i++) pos[i] = pmap[q[i]];
      arr.pos = pos;
    }
    this.v2 = !!(arr.pos12 && this.v.orth && this.v.pos12);
    this._rev = {};
    for (const k of ['surf', 'lem']) {
      const m = new Map();
      this.v[k].forEach((w, i) => { if (!m.has(w)) m.set(w, i); });
      this._rev[k] = m;
    }
    this._pos_of = new Map(this.v.pos.map((p, i) => [p, i]));
    this._punct = this.v.pos.map((p, i) => PUNCT_POS.has(p) ? i : -1).filter(i => i >= 0);
    this._punctLut = new Uint8Array(this.v.pos.length + 1);
    for (const i of this._punct) this._punctLut[i] = 1;
    this._rank = {};
    this._cfreq = new Map();
    this._vtab = new Map();
  }

  stream_key(stream) {
    if (['lemma', 'lem', '語彙素'].includes(stream)) return 'lem';
    if (['surface', 'surf', '表層形'].includes(stream)) return 'surf';
    throw new QueryError(`列の名前が違う: ${stream}（lemma か surface）`);
  }

  rank(key) {
    if (!this._rank[key]) {
      const vocab = this.v[key];
      const order = vocab.map((_, i) => i).sort((a, b) => cpCompare(vocab[a], vocab[b]) || a - b);
      const r = new Int32Array(vocab.length);
      order.forEach((id, k) => { r[id] = k; });
      this._rank[key] = r;
    }
    return this._rank[key];
  }

  needOrth() {
    if (!this.a.orth) throw new QueryError('書字形基本形（異綴形）のデータをまだ読み込んでいる。少し待ってからもう一度検索すること。');
  }

  // -- 検索式 ------------------------------------------------------------------
  parse(query, stream = 'lemma') {
    const base = this.stream_key(stream);
    const q = (query || '').trim();
    if (!q) throw new QueryError('検索語が空である');
    const terms = [];
    for (const raw of q.split(/\s+/)) {
      let t = raw, key = base;
      if (t.slice(0, 2) === 'L:' || t.slice(0, 2) === 'l:') { t = t.slice(2); key = 'lem'; }
      else if (t.slice(0, 2) === 'S:' || t.slice(0, 2) === 's:') { t = t.slice(2); key = 'surf'; }
      let pos = '';
      const sl = t.indexOf('/');
      if (sl >= 0) { pos = t.slice(sl + 1); t = t.slice(0, sl); }
      if (!t && !pos) throw new QueryError(`項が空である: ${raw}`);
      if (t === '' || t === '*') { terms.push({ raw, stream: key, forms: null, pos, any: true }); continue; }
      const vocab = this.v[key], rev = this._rev[key];
      let ids;
      if (t.startsWith('re:')) {
        let rx;
        try { rx = new RegExp(t.slice(3), 'u'); }
        catch (e) { throw new QueryError(`正規表現が誤っている（${raw}）: ${e.message}`); }
        ids = []; vocab.forEach((w, i) => { if (rx.test(w)) ids.push(i); });
      } else if (/[*?[\]]/.test(t)) {
        const pats = t.split('|').map(fnmatchRe);
        ids = []; vocab.forEach((w, i) => { if (pats.some(p => p.test(w))) ids.push(i); });
      } else {
        ids = t.split('|').filter(w => rev.has(w)).map(w => rev.get(w));
      }
      if (!ids.length) {
        throw new QueryError(
          `「${t}」に当たる語形が${key === 'lem' ? '語彙素' : '表層形'}の語彙に無い。` +
          '列（語彙素／表層形）の選び違い，辞書によるテクスト分割' +
          '（「非常に」→「非常」＋「に」），旧仮名の表記を疑うこと。');
      }
      ids = [...new Set(ids)].sort((a, b) => a - b);
      terms.push({ raw, stream: key, forms: ids, pos, any: false });
    }
    return terms;
  }

  _lut(key, ids) {
    const lut = new Uint8Array(this.v[key].length + 1);
    for (const i of ids) lut[i] = 1;
    return lut;
  }

  // その項に当たるかを位置ごとに調べる関数を返す
  _termTest(t) {
    let fl = null, pl = null;
    if (!t.any) fl = this._lut(t.stream, t.forms);
    if (t.pos) {
      const pids = [];
      for (const [p, i] of this._pos_of) if (p.startsWith(t.pos)) pids.push(i);
      if (!pids.length) {
        throw new QueryError(`品詞「${t.pos}」は索引に無い。使えるのは: ${[...this._pos_of.keys()].sort(cpCompare).join('，')}`);
      }
      pl = this._lut('pos', pids);
    }
    const fa = t.any ? null : this.a[t.stream], pa = this.a.pos;
    if (fl && pl) return j => fl[fa[j]] === 1 && pl[pa[j]] === 1;
    if (fl) return j => fl[fa[j]] === 1;
    if (pl) return j => pl[pa[j]] === 1;
    return () => true;
  }

  // -- 本体 --------------------------------------------------------------------
  search(query, o = {}) {
    const t0 = Date.now();
    const stream = o.stream ?? 'lemma';
    let context = o.context ?? 7;
    const sort = o.sort ?? 'position', limit = o.limit ?? 200, offset = o.offset ?? 0;
    const sample = o.sample ?? 0, seed = o.seed ?? 20260920;
    const collocates = o.collocates ?? 0, coll_window = o.coll_window ?? 4;
    const coll_sort = o.coll_sort ?? 'logdice', coll_pos = o.coll_pos ?? null;
    const coll_min = o.coll_min ?? 2, coll_focus = o.coll_focus ?? null;
    const variants = o.variants ?? true;
    const terms = this.parse(query, stream);
    const base = this.stream_key(stream);
    const n = this.n, sent = this.a.sent;

    // 連なりの先頭位置。文境界を越えない
    const tests = terms.map(t => this._termTest(t));
    let hitsArr = [];
    const t0test = tests[0];
    for (let j = 0; j < n; j++) if (t0test(j)) hitsArr.push(j);
    let hits = Int32Array.from(hitsArr); hitsArr = null;
    for (let k = 1; k < terms.length; k++) {
      if (!hits.length) break;
      const tk = tests[k], out = [];
      for (const h of hits) {
        const x = h + k;
        if (x < n && sent[x] === sent[h] && tk(x)) out.push(h);
      }
      hits = Int32Array.from(out);
    }
    const span = terms.length;

    // 絞り込み
    if (hits.length) {
      const W = this.works.length;
      const keepW = new Uint8Array(W).fill(1);
      if (o.works && o.works.length) {
        const s = new Set(o.works.map(Number));
        for (let w = 0; w < W; w++) if (!s.has(w)) keepW[w] = 0;
      }
      if (o.bands && o.bands.length) {
        const s = new Set(o.bands.map(Number));
        for (let w = 0; w < W; w++) if (!s.has(this.works[w].band)) keepW[w] = 0;
      }
      for (const [field, want] of [['author', o.authors], ['style', o.styles], ['genre', o.genres]]) {
        if (want && want.length) {
          const s = new Set(want);
          for (let w = 0; w < W; w++) if (!s.has(this.works[w][field])) keepW[w] = 0;
        }
      }
      const wk = this.a.work, pa = this.a.pos, pl = this._punctLut;
      const out = [];
      for (const h of hits) {
        if (!keepW[wk[h]]) continue;
        if (o.exclude_punct && pl[pa[h]]) continue;
        out.push(h);
      }
      hits = Int32Array.from(out);
    }

    const total = hits.length;
    const rand = prng(seed);
    let sampled = false;
    if (sample && total > sample) {
      // 部分的な Fisher–Yates で sample 件を選び，位置の順に戻す
      const a = Array.from(hits);
      for (let i = 0; i < sample; i++) {
        const j = i + Math.floor(rand() * (a.length - i));
        const tmp = a[i]; a[i] = a[j]; a[j] = tmp;
      }
      hits = Int32Array.from(a.slice(0, sample)).sort();
      sampled = true;
    }

    // 集計（並べ替えと頁より先に）
    const wcnt = new Int32Array(this.works.length);
    for (const h of hits) wcnt[this.a.work[h]]++;
    const byWork = [];
    wcnt.forEach((c, i) => { if (c) byWork.push([i, c]); });
    byWork.sort((x, y) => y[1] - x[1]);       // Counter.most_common（同数は番号順）
    const byBand = new Map();
    for (const [w, c] of byWork) {
      const b = this.works[w].band;
      byBand.set(b, (byBand.get(b) || 0) + c);
    }
    const [types, types_capped] = this._type_tally(hits, span, base);
    let coll = [], coll_info = {};
    if (collocates) {
      [coll, coll_info] = this._collocates(hits, span, base, coll_window, collocates,
        { measure: coll_sort, pos_sel: coll_pos, min_co: coll_min });
    }
    const v = variants ? this._variants(hits, span) : null;

    // 共起語で用例を絞る（集計は変えない）
    let focus = null, listed = hits, mark = null;
    if (coll_focus) {
      const fid = this._rev[base].get(coll_focus);
      if (fid === undefined) {
        throw new QueryError(`共起語「${coll_focus}」が${base === 'lem' ? '語彙素' : '表層形'}の語彙に無い。`);
      }
      const cm = this._coll_mask(hits, span, base, coll_window, fid, coll_pos);
      const out = []; let co = 0;
      hits.forEach((h, r) => { const row = cm[r]; let any = false; for (const x of row) { if (x) { any = true; co++; } } if (any) out.push(h); });
      listed = Int32Array.from(out);
      mark = [fid, coll_window, coll_pos];
      context = Math.max(context, coll_window);
      focus = { form: coll_focus, window: coll_window, pos: [...(coll_pos || [])],
                hits: listed.length, co, of: total };
    }

    const order = this._order(listed, span, base, sort, rand);
    const shown = limit ? order.slice(offset, offset + limit) : order;
    let cpos = new Array(shown.length).fill(null);
    if (mark && shown.length) {
      const offs = KwicIndex._coll_offsets(span, mark[1]);
      const cm = this._coll_mask(Int32Array.from(shown), span, base, mark[1], mark[0], mark[2]);
      cpos = Array.from(shown, (i, r) => {
        const s = new Set(); offs.forEach((of, c) => { if (cm[r][c]) s.add(i + of); }); return s;
      });
    }
    const rows = Array.from(shown, (i, r) => this._row(i, span, context, base, cpos[r]));

    return {
      query, stream: base, span, terms: terms.map(t => t.raw),
      total, listed: listed.length, focus, sampled, sample: sample || 0,
      seed, shown: rows.length, offset, sort, context, rows,
      by_work: byWork.map(([k, c]) => {
        const w = this.works[k];
        return { work: k, stem: w.stem, author: w.author, title: w.title, band: w.band,
                 hits: c, tokens: w.tokens, per_10k: rnd(c / Math.max(1, w.tokens) * 1e4, 2) };
      }),
      by_band: this._band_table(byBand),
      types, types_capped, collocates: coll, coll_info, variants: v,
      per_million: rnd(total / Math.max(1, n) * 1e6, 2),
      elapsed_ms: Date.now() - t0,
      provenance: this.prov,
    };
  }

  // 一致した語形の集計。連なりは全体を1つの語形として数える
  _type_tally(hits, span, base, topn = 50) {
    if (!hits.length) return [[], false];
    const arr = this.a[base], vocab = this.v[base];
    const m = new Map();
    for (const h of hits) {
      let key;
      if (span === 1) key = arr[h];
      else { key = ''; for (let k = 0; k < span; k++) key += (k ? ',' : '') + arr[h + k]; }
      m.set(key, (m.get(key) || 0) + 1);
    }
    const ent = [...m.entries()];
    ent.sort((a, b) => b[1] - a[1]);
    const out = ent.slice(0, topn).map(([k, c]) => ({
      form: span === 1 ? vocab[k] : String(k).split(',').map(x => vocab[+x]).join(' '),
      hits: c,
    }));
    return [out, false];
  }

  _order(hits, span, base, sort, rand) {
    const h = Array.from(hits);
    if (!h.length || sort === 'position' || sort === '' || sort == null) return h;
    if (sort === 'random') {
      for (let i = h.length - 1; i > 0; i--) {
        const j = Math.floor(rand() * (i + 1)); const t = h[i]; h[i] = h[j]; h[j] = t;
      }
      return h;
    }
    const n = this.n, sent = this.a.sent, arr = this.a[base];
    let keyFns;
    if (['left1', 'left2', 'right1', 'right2'].includes(sort)) {
      const rk = this.rank(base);
      const offs = sort === 'left1' ? [-1] : sort === 'left2' ? [-1, -2]
                 : sort === 'right1' ? [span] : [span, span + 1];
      const ctx = (i, off) => {
        const j = i + off;
        if (j < 0 || j >= n || sent[j] !== sent[i]) return -1;
        return rk[arr[j]];
      };
      const keys = new Map(h.map(i => [i, offs.map(off => ctx(i, off))]));
      h.sort((a, b) => {
        const ka = keys.get(a), kb = keys.get(b);
        for (let k = 0; k < ka.length; k++) if (ka[k] !== kb[k]) return ka[k] - kb[k];
        return a - b;
      });
      return h;
    }
    if (['year', 'author', 'title'].includes(sort)) {
      let prim, sec;
      if (sort === 'year') {
        const yr = w => { const y = parseFloat(w.year); return isFinite(y) ? Math.trunc(y) : 9999; };
        prim = this.works.map(yr);
        const names = [...new Set(this.works.map(w => w.author))].sort(cpCompare);
        const nrk = new Map(names.map((x, i) => [x, i]));
        sec = this.works.map(w => nrk.get(w.author));
      } else {
        const val = w => w[sort] || '￿';
        const vals = [...new Set(this.works.map(val))].sort(cpCompare);
        const vrk = new Map(vals.map((x, i) => [x, i]));
        prim = this.works.map(w => vrk.get(val(w)));
        sec = this.works.map(() => 0);
      }
      const wk = this.a.work;
      h.sort((a, b) => (prim[wk[a]] - prim[wk[b]]) || (sec[wk[a]] - sec[wk[b]]) || (a - b));
      return h;
    }
    throw new QueryError(`並べ替えの指定が違う: ${sort}`);
  }

  _row(i, span, context, base, cpos) {
    const sent = this.a.sent, s = sent[i];
    const lo = Math.max(0, i - context), hi = Math.min(this.n, i + span + context);
    cpos = cpos || new Set();
    const seq = (a, b) => {
      const out = [];
      for (let j = a; j < b; j++) {
        const t = { surf: this.v.surf[this.a.surf[j]], lem: this.v.lem[this.a.lem[j]],
                    pos: this.v.pos[this.a.pos[j]], same_sent: sent[j] === s };
        if (cpos.has(j)) t.coll = true;
        out.push(t);
      }
      return out;
    };
    const w = this.works[this.a.work[i]];
    return {
      pos_i: i, work: w.i, stem: w.stem,
      author: w.author || '（メタデータ無し）', title: w.title || w.stem,
      year: w.year, band: w.band, period: w.period, style: w.style, has_meta: w.meta,
      left: seq(lo, i), key: seq(i, i + span), right: seq(i + span, hi),
      in_work: i - w.start,
    };
  }

  _band_table(byBand) {
    const tok = new Map();
    for (const w of this.works) tok.set(w.band, (tok.get(w.band) || 0) + w.tokens);
    const order = this.band_labels.map((_, i) => i);
    for (const b of [...byBand.keys()].sort((a, b) => a - b)) if (!order.includes(b)) order.push(b);
    return order.map(b => {
      const lab = (b >= 0 && b < this.band_labels.length) ? this.band_labels[b] : '初出年不明';
      const h = byBand.get(b) || 0, t = tok.get(b) || 0;
      return { band: b, label: lab, hits: h, tokens: t, per_10k: t ? rnd(h / t * 1e4, 3) : 0.0 };
    });
  }

  pos_lut(sel) {
    if (!sel) return null;
    sel = sel.filter(s => s);
    if (!sel.length) return null;
    let key, vocab;
    if (this.v2) { key = 'pos12'; vocab = this.v.pos12; }
    else {
      if (sel.some(s => s.includes('-'))) throw new QueryError('品詞の中分類で絞るには索引を作り直すこと（この索引は版 1。python3 scripts/15_kwic_index.py）。');
      key = 'pos'; vocab = this.v.pos;
    }
    const want = new Set(sel);
    const lut = new Uint8Array(vocab.length + 1);
    let anyT = false;
    vocab.forEach((p, i) => { if (want.has(p) || want.has(p.split('-')[0])) { lut[i] = 1; anyT = true; } });
    if (!anyT) throw new QueryError(`品詞「${sel.join('，')}」は索引に無い。`);
    return [key, lut];
  }

  _content_ok(pos_sel) {
    const pl = this.pos_lut(pos_sel), pa = this.a.pos, pu = this._punctLut;
    if (pl) { const [key, lut] = pl, ka = this.a[key]; return j => !pu[pa[j]] && lut[ka[j]] === 1; }
    return j => !pu[pa[j]];
  }

  _coll_freq(base, pos_sel) {
    const ck = base + '\u0000' + [...(pos_sel || [])].sort(cpCompare).join('|');
    if (!this._cfreq.has(ck)) {
      const ok = this._content_ok(pos_sel), arr = this.a[base];
      const f = new Float64Array(this.v[base].length);
      let m = 0;
      for (let j = 0; j < this.n; j++) if (ok(j)) { f[arr[j]]++; m++; }
      let N = m;
      if (pos_sel && pos_sel.length) {
        const all = this._content_ok(null); N = 0;
        for (let j = 0; j < this.n; j++) if (all(j)) N++;
      }
      this._cfreq.set(ck, [f, N]);
    }
    return this._cfreq.get(ck);
  }

  _collocates(hits, span, base, window, topn, { measure = 'logdice', pos_sel = null, min_co = 2 } = {}) {
    if (!(measure in COLL_MEASURES)) throw new QueryError(`共起語の指標の指定が違う: ${measure}（${Object.keys(COLL_MEASURES).join('，')}）`);
    const info = { measure, window, pos: [...(pos_sel || [])], min_co };
    if (!hits.length) return [[], info];
    const arr = this.a[base], sent = this.a.sent, pa = this.a.pos, pu = this._punctLut;
    const [fAll, N] = this._coll_freq(base, pos_sel);
    const V = this.v[base].length;
    const pl = this.pos_lut(pos_sel);
    const co = new Float64Array(V);
    let W = 0;
    const offs = KwicIndex._coll_offsets(span, window);
    for (const o of offs) {
      for (const h of hits) {
        const j = h + o;
        if (j < 0 || j >= this.n || sent[j] !== sent[h] || pu[pa[j]]) continue;
        W++;
        if (pl && pl[1][this.a[pl[0]][j]] !== 1) continue;
        co[arr[j]]++;
      }
    }
    const fNode = hits.length;
    Object.assign(info, { W, N, node: fNode });
    const idx = [];
    for (let i = 0; i < V; i++) if (co[i] >= Math.max(1, min_co) && fAll[i] >= 3) idx.push(i);
    if (!idx.length || W === 0) return [[], info];
    const xlx = (o_, e) => o_ > 0 ? o_ * Math.log(o_ / e) : 0;
    const rows = idx.map(tid => {
      const o11 = co[tid], fc = fAll[tid];
      const o12 = W - o11, o21 = fc - o11, o22 = N - W - o21;
      const e11 = W * fc / N, e12 = W * (N - fc) / N, e21 = (N - W) * fc / N, e22 = (N - W) * (N - fc) / N;
      let g2 = 2 * (xlx(o11, e11) + xlx(o12, e12) + xlx(o21, e21) + xlx(o22, e22));
      if (o11 < e11) g2 = -g2;
      const M = {
        co: o11,
        logdice: 14 + Math.log2(2 * o11 / (fNode + fc)),
        mi: Math.log2(o11 / e11),
        mi3: Math.log2(Math.pow(o11, 3) / e11),
        t: (o11 - e11) / Math.sqrt(o11),
        g2,
        dp_fwd: o11 / W - o21 / (N - W),
        dp_bwd: o11 / fc - o12 / (N - fc),
      };
      return { tid, o11, fc, e11, M };
    });
    // 指標の大きい順，同点は共起頻度の多い順（np.lexsort((-o11, -key)) と同じ）
    // NaN は numpy と同じく最後に回す
    const key = r => { const k = r.M[measure]; return isNaN(k) ? -Infinity : k; };
    const desc = (x, y) => (x === y ? 0 : (x < y ? 1 : -1));
    rows.sort((a, b) => desc(key(a), key(b)) || (b.o11 - a.o11) || (a.tid - b.tid));
    const out = rows.slice(0, topn).map(r => {
      const row = { form: this.v[base][r.tid], co: r.o11, freq: r.fc, exp: rnd(r.e11, 3) };
      for (const [m, val] of Object.entries(r.M)) {
        if (m === 'co') continue;
        row[m] = rnd(val, m.startsWith('dp') ? 4 : 2);
      }
      return row;
    });
    return [out, info];
  }

  static _coll_offsets(span, window) {
    const out = [];
    for (let k = 1; k <= window; k++) out.push(-k);
    for (let k = 0; k < window; k++) out.push(span + k);
    return out;
  }

  _coll_mask(hits, span, base, window, fid, pos_sel = null) {
    const offs = KwicIndex._coll_offsets(span, window);
    const sent = this.a.sent, pa = this.a.pos, pu = this._punctLut, arr = this.a[base];
    const pl = this.pos_lut(pos_sel);
    return Array.from(hits, h => offs.map(o => {
      const j = h + o;
      if (j < 0 || j >= this.n || sent[j] !== sent[h] || pu[pa[j]]) return false;
      if (pl && pl[1][this.a[pl[0]][j]] !== 1) return false;
      return arr[j] === fid;
    }));
  }

  _band_of_work() {
    const nb = this.band_labels.length;
    return this.works.map(w => (w.band >= 0 && w.band < nb) ? w.band : nb);
  }

  _variants(hits, span, top = 12) {
    if (!this.v2) return { available: false, reason: '異綴形を出すには索引を作り直すこと（この索引は版 1。python3 scripts/15_kwic_index.py）。' };
    if (!hits.length) return { available: true, total: 0, forms: [], other: null, by_band: [], by_author: [], by_work: [] };
    if (!this.a.orth) {
      // Web 版：書字形基本形はあとから読み込む。読み終わる前の検索では出さない
      return { available: false, reason: '書字形基本形のデータを読み込んでいるところである。読み込みが終わってからもう一度検索すると表示される。' };
    }
    const arr = this.a.orth, vo = this.v.orth;
    // 語形の組ごとにまとめる。並びは番号の組の辞書順（np.unique と同じ）
    const m = new Map();
    const inv = new Int32Array(hits.length);
    const codes = [];
    hits.forEach((h, r) => {
      const tup = []; for (let k = 0; k < span; k++) tup.push(arr[h + k]);
      const key = tup.join(',');
      if (!m.has(key)) { m.set(key, codes.length); codes.push(tup); }
      inv[r] = m.get(key);
    });
    const uorder = codes.map((_, i) => i).sort((a, b) => {
      const x = codes[a], y = codes[b];
      for (let k = 0; k < span; k++) if (x[k] !== y[k]) return x[k] - y[k];
      return 0;
    });
    const remap = new Int32Array(codes.length);
    uorder.forEach((u, k) => { remap[u] = k; });
    const names = uorder.map(u => codes[u].map(x => vo[x]).join(' '));
    for (let r = 0; r < inv.length; r++) inv[r] = remap[inv[r]];
    const cnt = new Array(names.length).fill(0);
    for (const x of inv) cnt[x]++;
    const order = names.map((_, i) => i).sort((a, b) => (cnt[b] - cnt[a]) || (a - b));
    const keep = order.slice(0, top);
    const col = new Int32Array(names.length).fill(keep.length);
    keep.forEach((u, k) => { col[u] = k; });
    const ncol = keep.length + (names.length > top ? 1 : 0);
    const total = hits.length;
    const forms = keep.map(i => ({ form: names[i], hits: cnt[i], share: rnd(cnt[i] / total, 4) }));
    let other = null;
    if (names.length > top) {
      const oh = order.slice(top).reduce((s, i) => s + cnt[i], 0);
      other = { types: names.length - top, hits: oh, share: rnd(oh / total, 4) };
    }
    const wid = Array.from(hits, h => this.a.work[h]);
    const table = (groupOf, labels, extra) => {
      const G = labels.length;
      const mat = Array.from({ length: G }, () => new Array(ncol).fill(0));
      wid.forEach((w, r) => { mat[groupOf(w)][col[inv[r]]]++; });
      const rows = [];
      for (let g = 0; g < G; g++) {
        const tot = mat[g].reduce((s, x) => s + x, 0);
        if (!tot) continue;
        const row = { label: labels[g], total: tot, counts: mat[g] };
        if (extra) Object.assign(row, extra(g));
        rows.push(row);
      }
      return rows;
    };
    const nb = this.band_labels.length;
    const bw = this._band_of_work();
    const band_rows = table(w => bw[w], [...this.band_labels, '初出年不明']);
    const authors = [...new Set(this.works.map(w => w.author || '（メタデータ無し）'))].sort(cpCompare);
    const aidx = new Map(authors.map((a, i) => [a, i]));
    const amap = this.works.map(w => aidx.get(w.author || '（メタデータ無し）'));
    const au_rows = table(w => amap[w], authors).sort((a, b) => b.total - a.total);
    const wlab = this.works.map(w => `${w.author || '（メタデータ無し）'}『${w.title || w.stem}』`);
    const wk_rows = table(w => w, wlab, g => ({ year: this.works[g].year, band: this.works[g].band }))
      .sort((a, b) => b.total - a.total);
    return { available: true, total, forms, other, types: names.length,
             by_band: band_rows, by_author: au_rows, by_work: wk_rows, n_bands: nb };
  }

  variant_table({ min_freq = 20, min_var = 2, min_types = 2, pos = null, sort = 'minor', limit = 300 } = {}) {
    if (!this.v2) throw new QueryError('異綴形の一覧には索引を作り直すこと（この索引は版 1。python3 scripts/15_kwic_index.py）。');
    if (!['minor', 'types', 'freq'].includes(sort)) throw new QueryError(`並べ方の指定が違う: ${sort}（minor・types・freq）`);
    this.needOrth();
    const ck = [...(pos || [])].sort(cpCompare).join('|');
    if (!this._vtab.has(ck)) {
      const ok = this._content_ok(pos);
      const lem = this.a.lem, orth = this.a.orth, pa = this.a.pos;
      const byLem = new Map();         // 語彙素 → Map(書字形 → 数)
      const posCnt = new Map();        // 語彙素 → Map(品詞 → 数)
      for (let j = 0; j < this.n; j++) {
        if (!ok(j)) continue;
        const l = lem[j];
        let mo = byLem.get(l); if (!mo) { mo = new Map(); byLem.set(l, mo); }
        mo.set(orth[j], (mo.get(orth[j]) || 0) + 1);
        let mp = posCnt.get(l); if (!mp) { mp = new Map(); posCnt.set(l, mp); }
        mp.set(pa[j], (mp.get(pa[j]) || 0) + 1);
      }
      const main_pos = new Map();
      for (const [l, mp] of posCnt) {
        let best = [0, 0];
        for (const p of [...mp.keys()].sort((a, b) => a - b)) if (mp.get(p) > best[0]) best = [mp.get(p), p];
        main_pos.set(l, best);
      }
      this._vtab.set(ck, { byLem, main_pos });
    }
    const d = this._vtab.get(ck);
    const rows = [];
    for (const l of [...d.byLem.keys()].sort((a, b) => a - b)) {
      const mo = d.byLem.get(l);
      const os = [...mo.keys()].sort((a, b) => a - b);
      const c = os.map(x => mo.get(x));
      const T = c.reduce((s, x) => s + x, 0);
      if (T < min_freq) continue;
      const nvalid = c.filter(x => x >= min_var).length;
      if (nvalid < min_types) continue;
      const order = c.map((_, i) => i).sort((a, b) => (c[b] - c[a]) || (a - b));
      const main = c[order[0]];
      rows.push({
        lemma: this.v.lem[l],
        pos: this.v.pos[(d.main_pos.get(l) || [0, 0])[1]],
        freq: T, types: nvalid,
        main: this.v.orth[os[order[0]]],
        minor_share: rnd(1 - main / T, 4),
        forms: order.filter(i => c[i] >= min_var).map(i => ({ form: this.v.orth[os[i]], hits: c[i] })).slice(0, 8),
      });
    }
    const keyf = { minor: (a, b) => (b.minor_share - a.minor_share) || (b.freq - a.freq),
                   types: (a, b) => (b.types - a.types) || (b.minor_share - a.minor_share) || (b.freq - a.freq),
                   freq: (a, b) => (b.freq - a.freq) || (b.minor_share - a.minor_share) }[sort];
    rows.sort(keyf);
    return { rows: rows.slice(0, limit), total: rows.length, min_freq, min_var, min_types,
             pos: [...(pos || [])], sort };
  }

  passage(pos_i, { before = 120, after = 120, span = 1 } = {}) {
    const i = Math.trunc(pos_i);
    if (!(i >= 0 && i < this.n)) throw new QueryError(`位置が索引の外である: ${pos_i}`);
    const w = this.works[this.a.work[i]], sent = this.a.sent;
    let lo = Math.max(w.start, i - before), hi = Math.min(w.end, i + span + after);
    while (lo > w.start && sent[lo - 1] === sent[lo]) lo--;
    while (hi < w.end && sent[hi - 1] === sent[hi]) hi++;
    const toks = [];
    for (let j = lo; j < hi; j++) {
      toks.push({
        surf: this.v.surf[this.a.surf[j]], lem: this.v.lem[this.a.lem[j]],
        pos: this.v.pos[this.a.pos[j]],
        cfm: this.a.cfm ? this.v.cfm[this.a.cfm[j]] : '',
        gos: this.a.gos ? this.v.gos[this.a.gos[j]] : '',
        key: i <= j && j < i + span,
        eos: j + 1 < this.n && sent[j + 1] !== sent[j],
      });
    }
    const wk = {}; for (const k of ['stem', 'author', 'title', 'year', 'period', 'style', 'band', 'meta']) wk[k] = w[k];
    return { pos_i: i, from: lo, to: hi, tokens: toks, work: wk,
             band_label: (w.band >= 0 && w.band < this.band_labels.length) ? this.band_labels[w.band] : '初出年不明',
             in_work: i - w.start, work_tokens: w.tokens };
  }

  pos_tree() {
    const key = this.v2 ? 'pos12' : 'pos', vocab = this.v[key], a = this.a[key];
    const cnt = new Float64Array(vocab.length);
    for (let j = 0; j < this.n; j++) cnt[a[j]]++;
    const tree = new Map();
    vocab.forEach((p, i) => {
      const k = p.indexOf('-');
      const p1 = k < 0 ? p : p.slice(0, k), p2 = k < 0 ? '' : p.slice(k + 1);
      if (!p1 || PUNCT_POS.has(p1) || !cnt[i]) return;
      if (!tree.has(p1)) tree.set(p1, { pos: p1, tokens: 0, children: [] });
      const node = tree.get(p1);
      node.tokens += cnt[i];
      if (p2) node.children.push({ pos: p, label: p2, tokens: cnt[i] });
    });
    const out = [...tree.values()].sort((x, y) => y.tokens - x.tokens);
    for (const x of out) x.children.sort((c, d) => d.tokens - c.tokens);
    return out;
  }

  facets() {
    const authors = counterMostCommon(this.works.map(w => w.author || '（メタデータ無し）'))
      .sort((a, b) => (b[1] - a[1]) || cpCompare(a[0], b[0]));
    return {
      authors: authors.map(([name, works]) => ({ name, works })),
      bands: this.band_labels.map((l, i) => ({ band: i, label: l, works: this.works.filter(w => w.band === i).length }))
        .concat([{ band: -1, label: '初出年不明', works: this.works.filter(w => w.band === -1).length }]),
      styles: counterMostCommon(this.works.filter(w => w.style).map(w => w.style)).map(([name, works]) => ({ name, works })),
      genres: counterMostCommon(this.works.filter(w => w.genre).map(w => w.genre)).map(([name, works]) => ({ name, works })),
      works: this.works.map(w => ({ work: w.i, stem: w.stem, author: w.author, title: w.title, year: w.year,
                                    band: w.band, tokens: w.tokens, meta: w.meta })),
      pos: this.v.pos.filter(p => p).sort(cpCompare),
      pos_tree: this.pos_tree(),
      coll_measures: COLL_MEASURES,
      index_v2: this.v2,
      api: API_VERSION,
      band_labels: this.band_labels,
      provenance: this.prov,
    };
  }
}

// ---- サーバと同じ受け口（16_kwic_server.py の Handler と同じ引数の扱い）--------
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const int = (x, d) => { const v = parseInt(x, 10); return isNaN(v) ? d : v; };

function searchFrom(kw, req) {
  return kw.search(req.query || '', {
    stream: req.stream || 'lemma',
    context: clamp(int(req.context ?? 7, 7), 1, 30),
    works: (req.works && req.works.length) ? req.works : null,
    bands: (req.bands && req.bands.length) ? req.bands : null,
    authors: (req.authors && req.authors.length) ? req.authors : null,
    styles: (req.styles && req.styles.length) ? req.styles : null,
    genres: (req.genres && req.genres.length) ? req.genres : null,
    exclude_punct: !!req.exclude_punct,
    sort: req.sort || 'position',
    limit: clamp(int(req.limit ?? 200, 200), 1, 2000),
    offset: Math.max(0, int(req.offset ?? 0, 0)),
    sample: Math.max(0, int(req.sample ?? 0, 0)),
    seed: int(req.seed ?? 20260920, 20260920),
    collocates: clamp(int(req.collocates ?? 0, 0), 0, 200),
    coll_window: clamp(int(req.coll_window ?? 4, 4), 1, 20),
    coll_sort: String(req.coll_sort || 'logdice'),
    coll_pos: (req.coll_pos && req.coll_pos.length) ? req.coll_pos.map(String) : null,
    coll_min: clamp(int(req.coll_min ?? 2, 2), 1, 1000),
    coll_focus: req.coll_focus ? String(req.coll_focus) : null,
    variants: req.variants === undefined ? true : !!req.variants,
  });
}

function csvCell(v) {
  const s = v == null ? '' : String(v);
  return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}

function exportCsv(kw, res) {
  const pv = res.provenance || {}, fo = res.focus || {};
  const lines = [];
  const row = a => lines.push(a.map(csvCell).join(','));
  row(['# query', res.query, 'stream', res.stream, 'total', res.total, 'shown', res.shown,
       'collocate', fo.form ?? '', 'window', fo.window ?? '', 'with_collocate', fo.hits ?? '',
       'dictionary', pv.dictionary || '', 'index_built', pv.built_at || '']);
  row(['時代区分', '著者', '作品', '初出年', '文体', '左文脈', 'キーワード', '右文脈',
       '語彙素', '品詞', '作品内位置', '作品のファイル名（拡張子なし）']);
  const bl = kw.band_labels;
  for (const r of res.rows) {
    row([(r.band >= 0 && r.band < bl.length) ? bl[r.band] : '初出年不明',
         r.author, r.title, r.year, r.style,
         r.left.map(t => t.surf).join(''), r.key.map(t => t.surf).join(''), r.right.map(t => t.surf).join(''),
         r.key.map(t => t.lem).join(' '), r.key.map(t => t.pos).join(' '), r.in_work, r.stem]);
  }
  return '﻿' + lines.join('\r\n') + '\r\n';
}

/** 画面からの問い合わせ（パス・クエリ・本文）を処理する。{status, json} か {status, csv, name} を返す */
function handle(kw, path, query, body) {
  const q = query || {};
  try {
    if (path === '/api/meta') return { status: 200, json: kw.facets() };
    if (path === '/api/passage') {
      return { status: 200, json: kw.passage(int(q.pos ?? 0, 0), {
        before: Math.min(400, int(q.before ?? 120, 120)), after: Math.min(400, int(q.after ?? 120, 120)),
        span: Math.max(1, int(q.span ?? 1, 1)) }) };
    }
    if (path === '/api/variants') {
      const pos = String(q.pos || '').split(',').filter(p => p);
      return { status: 200, json: kw.variant_table({
        min_freq: Math.max(1, int(q.min_freq ?? 20, 20)), min_var: Math.max(1, int(q.min_var ?? 2, 2)),
        min_types: Math.max(2, int(q.min_types ?? 2, 2)), pos: pos.length ? pos : null,
        sort: q.sort || 'minor', limit: clamp(int(q.limit ?? 300, 300), 1, 2000) }) };
    }
    if (path === '/api/search') return { status: 200, json: searchFrom(kw, body || {}) };
    if (path === '/api/export.csv') {
      const res = searchFrom(kw, JSON.parse(q.q || '{}'));
      return { status: 200, csv: exportCsv(kw, res), name: `kwic_${res.query}.csv` };
    }
    return { status: 404, json: { error: `存在しないパス: ${path}` } };
  } catch (e) {
    if (e instanceof QueryError) return { status: 400, json: { error: e.message, kind: 'query' } };
    return { status: 500, json: { error: `${e.name}: ${e.message}` } };
  }
}

// ---- 読み込み（ブラウザ）------------------------------------------------------
// 配列は gzip で圧縮して配る。中身は manifest.json に書いてある。
//   vb   : 可変長整数（7 ビットずつ，下位から）
//   u8   : 1 バイト整数
//   dvb  : 差分を可変長整数で（単調に増える文番号）
async function inflate(buf) {
  const u = new Uint8Array(buf);
  if (!(u[0] === 0x1f && u[1] === 0x8b)) return u;       // サーバが既に展開して渡した
  const ds = new DecompressionStream('gzip');
  const out = await new Response(new Blob([u]).stream().pipeThrough(ds)).arrayBuffer();
  return new Uint8Array(out);
}

function decode(kind, bytes, n) {
  if (kind === 'u8') return bytes;
  const out = new Int32Array(n);
  let i = 0, v = 0, shift = 0, acc = 0;
  for (let p = 0; p < bytes.length; p++) {
    const b = bytes[p];
    v |= (b & 0x7f) << shift;
    if (b & 0x80) { shift += 7; continue; }
    if (kind === 'dvb') { acc += v; out[i++] = acc; } else out[i++] = v;
    v = 0; shift = 0;
  }
  if (i !== n) throw new Error(`配列の長さが合わない（${i} / ${n}）`);
  return out;
}

async function fetchBytes(url, onBytes, init) {
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${url} を読めない（HTTP ${r.status}）`);
  if (!r.body || !onBytes) return inflate(await r.arrayBuffer());
  const reader = r.body.getReader(), chunks = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value); got += value.length; onBytes(value.length);
  }
  const all = new Uint8Array(got); let o = 0;
  for (const c of chunks) { all.set(c, o); o += c.length; }
  return inflate(all.buffer);
}

if (typeof module !== 'undefined') {
  module.exports = { KwicIndex, QueryError, handle, decode, cpCompare, fnmatchRe, API_VERSION };
}

// ---- Web Worker として動くとき ---------------------------------------------
if (typeof self !== 'undefined' && typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope) {
  let KW = null, ready = null, extra = null;
  const post = m => self.postMessage(m);
  async function boot(base) {
    // 目録は毎回サーバに確かめる。索引を作り直したとき，古い配列と新しい目録が
    // 混ざらないよう，配列は目録の版（build）を付けて読む
    const man = JSON.parse(new TextDecoder().decode(await fetchBytes(base + 'manifest.json', null, { cache: 'no-cache' })));
    const v = '?v=' + encodeURIComponent(man.build || '');
    const core = man.files.filter(f => f.stage === 'core');
    const total = core.reduce((s, f) => s + f.bytes, 0) + man.meta.bytes;
    let got = 0;
    const prog = b => { got += b; post({ type: 'progress', got, total }); };
    const meta = JSON.parse(new TextDecoder().decode(await fetchBytes(base + man.meta.file + v, prog)));

    const arr = {};
    // 語の列は頻度の順位で詰めてある。perm（順位 → 元の番号）で元に戻す
    const load = async (f, onBytes) => {
      const a = decode(f.kind, await fetchBytes(base + f.file + v, onBytes), man.n);
      if (f.perm) {
        const nv = meta.vocab[f.array].length;
        const p = decode('vb', await fetchBytes(base + f.perm + v), nv);
        for (let i = 0; i < a.length; i++) a[i] = p[a[i]];
      }
      return a;
    };
    await Promise.all(core.map(async f => { arr[f.array] = await load(f, prog); }));
    // 作品の番号は作品の始まりと終わりから作る
    const work = new Uint8Array(man.n);
    meta.works.forEach(w => work.fill(w.i, w.start, w.end));
    arr.work = work;
    KW = new KwicIndex(meta, arr);
    post({ type: 'ready', n: KW.n, works: KW.works.length });
    // 残り（書字形基本形・活用形・語種）は後から読む
    extra = Promise.all(man.files.filter(f => f.stage === 'later').map(async f => {
      KW.a[f.array] = await load(f, null);
    })).then(() => post({ type: 'complete' }));
  }
  self.onmessage = async ev => {
    const m = ev.data;
    if (m.type === 'boot') {
      ready = boot(m.base).catch(e => post({ type: 'fatal', error: String(e && e.message || e) }));
      return;
    }
    if (m.type === 'call') {
      await ready;
      if (!KW) { post({ type: 'reply', id: m.id, status: 503, json: { error: 'コーパスを読み込めなかった' } }); return; }
      // 書字形基本形などが要る問い合わせは，読み終わるまで待つ
      if (extra && (m.path === '/api/variants' || m.path === '/api/passage')) await extra;
      const r = handle(KW, m.path, m.query, m.body);
      post({ type: 'reply', id: m.id, ...r });
    }
  };
}
