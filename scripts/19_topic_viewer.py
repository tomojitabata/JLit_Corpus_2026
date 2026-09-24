#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
19_topic_viewer.py
==================
**MALLET の結果を，品詞と頻度帯で絞り込みながら読むビューア**（Step 8）。

1 枚の HTML を書く。外部の資源は使わない（ネットに出ない。サーバも要らない）。
ブラウザで開くと，次ができる。

* **品詞**でトピックの上位語を絞る（普通名詞・動詞・形容詞・固有名詞…）
* **頻度帯**で絞る：全体の度数，出現作品の割合，1 作品への集中度
  （集中度が高い語＝その作品にしか出ない語。取りこぼした登場人物名が多い），
  散らばり dp_in（Gries の DP をいちばん濃い時代の中で測ったもの。bursty な語）
* **relevance λ**（Sievert & Shirley 2014）で並べ替える。λ=1 は p(w|t) の順，
  λ を下げるほど**そのトピックに特有の語**が上に来る
* トピックごとに **時代別の割合**・**多い作品と作家**を見る
* 語で検索して，その語を上位に持つトピックを探す
* 複数のモデル（例：内容語すべて／名詞・動詞・形容詞）を切り替えて比べる

⚠ **ビューアで語を隠すことと，その語を除いて学習し直すことは違う。**
隠した語もトピックの形成には効いている。固有名詞が作ったトピックは，
固有名詞を隠しても「その作品のトピック」のままである。本当に除くには
18_pos_select.py で語を選び直して学習し直し，ここで 2 つのモデルを並べる。

入力
----
* MALLET の出力ディレクトリ（10_mallet.py の ``--out``）。
  ``word-topic-counts.txt`` があればそれを，無ければ ``topic-state.gz`` を読む
* ``doc-topics.txt``（チャンク × トピック）。チャンク ID から作品を引く
* メタデータ（作家・題・初出年・時代）
* ``data/tokens/lexicon.tsv``（18_pos_select.py が書く。語の品詞と頻度帯）

使い方
------
    python3 scripts/19_topic_viewer.py \\
        --model 名詞・動詞・形容詞=my_work/results/mallet_nva \\
        --model 内容語すべて=my_work/results/mallet \\
        --out my_work/results/topic_viewer.html
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_meta() -> str:
    base = os.path.join(ROOT, 'metadata')
    for name in ('corpus_metadata_v3_local.csv', 'corpus_metadata_v3.csv',
                 'corpus_metadata_v2.csv'):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(base, 'corpus_metadata_v3.csv')


def load_meta(path: str) -> dict[str, dict]:
    """作品の語幹（000148_000794）→ メタデータの行。0 埋めの揺れを両方登録する。"""
    out = {}
    with open(path, encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh):
            pid = str(r.get('aozora_person_id') or '').strip()
            wid = str(r.get('aozora_work_id') or '').strip()
            if pid.isdigit() and wid.isdigit():
                out[f'{pid.zfill(6)}_{wid.zfill(6)}'] = r
                out.setdefault(f'{pid}_{wid}', r)
            fv = (r.get('file_v1') or '').strip()
            if fv:
                out.setdefault(os.path.splitext(fv)[0], r)
    return out


def load_lexicon(path: str) -> dict[str, tuple]:
    lex = {}
    if not path or not os.path.exists(path):
        return lex
    with open(path, encoding='utf-8-sig') as fh:
        for r in csv.DictReader(fh, delimiter='\t'):
            lex[r['lemma']] = (r['pos'], int(r['count']), float(r['doc_ratio']),
                               float(r.get('top_work_share') or 0), r.get('top_work', ''),
                               float(r.get('dp_in') or 0), float(r.get('top_author_share') or 0))
    return lex


def read_word_topic(mdir: str) -> tuple[int, dict[str, Counter]]:
    """語 → {トピック: 度数}。word-topic-counts.txt があればそれ，無ければ state。"""
    wt: dict[str, Counter] = defaultdict(Counter)
    k = 0
    p = os.path.join(mdir, 'word-topic-counts.txt')
    if os.path.exists(p):
        with open(p, encoding='utf-8') as fh:
            for line in fh:
                f = line.split()
                if len(f) < 3:
                    continue
                for tc in f[2:]:
                    t, c = tc.split(':')
                    t = int(t)
                    wt[f[1]][t] += int(c)
                    k = max(k, t + 1)
        return k, wt
    p = os.path.join(mdir, 'topic-state.gz')
    if not os.path.exists(p):
        sys.exit(f'{mdir} に word-topic-counts.txt も topic-state.gz も無い')
    with gzip.open(p, 'rt', encoding='utf-8') as fh:
        for line in fh:
            if line.startswith('#'):
                continue
            f = line.rstrip('\n').split(' ')
            if len(f) < 6:
                continue
            t = int(f[-1])
            wt[f[-2]][t] += 1
            k = max(k, t + 1)
    return k, wt


def read_doc_topics(path: str) -> tuple[list[str], list[list[float]]]:
    ids, rows = [], []
    with open(path, encoding='utf-8-sig') as fh:
        for line in fh:
            if line.startswith('#') or not line.strip():
                continue
            f = line.rstrip('\n').split('\t')
            name = os.path.splitext(os.path.basename(f[1]))[0]
            vals = [x for x in f[2:] if x != '']
            try:
                probs = [float(x) for x in vals]
            except ValueError:
                d = {int(vals[i]): float(vals[i + 1]) for i in range(0, len(vals) - 1, 2)}
                probs = [d.get(i, 0.0) for i in range(max(d) + 1)]
            ids.append(name)
            rows.append(probs)
    return ids, rows


def read_selection(mdir: str) -> str:
    """学習に使った語の選び方（18 の selection.json）を，分かる範囲で探す。"""
    for cand in (os.path.join(mdir, 'selection.json'),):
        if os.path.exists(cand):
            return json.load(open(cand, encoding='utf-8')).get('name', '')
    return ''


def build_model(label: str, mdir: str, meta: dict, lex: dict, top: int, min_count: int) -> dict:
    k, wt = read_word_topic(mdir)
    ids, rows = read_doc_topics(os.path.join(mdir, 'doc-topics.txt'))
    k = max(k, max(len(r) for r in rows))

    tot_t = [0] * k
    tot_w = {}
    for w, c in wt.items():
        s = sum(c.values())
        tot_w[w] = s
        for t, n in c.items():
            tot_t[t] += n
    N = sum(tot_t)

    # 各トピックの上位 top 語（度数順）。relevance で並べ替えても上位に来うる
    # 語を拾うため，度数の少ない語は min_count で切る
    per_topic = [[] for _ in range(k)]
    for w, c in wt.items():
        if tot_w[w] < min_count:
            continue
        for t, n in c.items():
            per_topic[t].append((n, w))
    words: dict[str, int] = {}
    tw = []
    for t in range(k):
        lst = sorted(per_topic[t], reverse=True)[:top]
        row = []
        for n, w in lst:
            if w not in words:
                words[w] = len(words)
            row.append([words[w], n])
        tw.append(row)
    vocab = []
    for w in words:
        pos, cnt, dr, ws, twk, dpi, aus = lex.get(w, ('不明', 0, 0.0, 0.0, '', 0.0, 0.0))
        vocab.append([w, pos, cnt, round(dr, 3), round(ws, 3), tot_w[w], round(dpi, 3), round(aus, 3)])

    # 文書（チャンク）→ 作品・時代
    chunk_work = [re.sub(r'__\d+$', '', i) for i in ids]
    works = sorted(set(chunk_work))
    widx = {w: i for i, w in enumerate(works)}
    wsum = [[0.0] * k for _ in works]
    wn = [0] * len(works)
    for cw, r in zip(chunk_work, rows):
        i = widx[cw]
        wn[i] += 1
        for t, p in enumerate(r):
            wsum[i][t] += p
    wmean = [[round(x / max(1, wn[i]), 4) for x in wsum[i]] for i in range(len(works))]
    winfo = []
    for s in works:
        m = meta.get(s, {})
        winfo.append([s, m.get('author_ja', '（メタデータ無し）'),
                      m.get('title_aozora', s), m.get('year_first', ''),
                      m.get('period', '') or '不明', wn[widx[s]]])
    periods = sorted({w[4] for w in winfo})
    psum = {p: [0.0] * k for p in periods}
    pn = Counter()
    for cw, r in zip(chunk_work, rows):
        p = winfo[widx[cw]][4]
        pn[p] += 1
        for t, x in enumerate(r):
            psum[p][t] += x
    pmean = [[round(x / max(1, pn[p]), 4) for x in psum[p]] for p in periods]
    prev = [round(sum(r[t] for r in rows) / len(rows), 5) for t in range(k)]

    keys = {}
    kp = os.path.join(mdir, 'topic-keys.txt')
    if os.path.exists(kp):
        for line in open(kp, encoding='utf-8-sig'):
            f = line.rstrip('\n').split('\t')
            if len(f) >= 3:
                keys[int(f[0])] = f[2]

    print(f'[mdl ] {label}: {k} トピック・{len(ids):,} チャンク・{len(works)} 作品・'
          f'語 {len(tot_w):,}（うち表示用 {len(vocab):,}）')
    return {'label': label, 'dir': os.path.abspath(mdir), 'K': k, 'N': N,
            'topicTotals': tot_t, 'vocab': vocab, 'tw': tw, 'prev': prev,
            'works': winfo, 'workTopic': wmean, 'periods': periods,
            'periodN': [pn[p] for p in periods], 'periodTopic': pmean,
            'keys': [keys.get(t, '') for t in range(k)]}


HTML = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JLit トピックビューア</title>
<style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#dcdcd5;--card:#fff;--acc:#2f6f9f;--hi:#fff3c4;
--c0:#2f6f9f;--c1:#c0612b;--c2:#3f8f5a;--c3:#8a5aa8;--c4:#b0902f;--c5:#9a9a93}
@media (prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#ecece6;--mut:#a4a49c;--line:#3a3a37;--card:#242422;--acc:#7fb3db;--hi:#4a4220;
--c0:#7fb3db;--c1:#e0925f;--c2:#79c08f;--c3:#b893d0;--c4:#d8bd63;--c5:#8d8d86}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "Hiragino Sans","Yu Gothic","Noto Sans CJK JP",sans-serif}
header{padding:12px 18px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:17px;margin:0}
header .note{color:var(--mut);font-size:12px;max-width:760px}
select,input[type=number],input[type=text]{font:inherit;padding:3px 6px;border:1px solid var(--line);border-radius:4px;background:var(--card);color:var(--fg)}
.wrap{display:grid;grid-template-columns:270px 1fr;min-height:calc(100vh - 60px)}
aside{border-right:1px solid var(--line);padding:12px 14px;overflow:auto;max-height:calc(100vh - 60px);position:sticky;top:0}
aside h2{font-size:13px;margin:14px 0 6px;color:var(--mut);font-weight:600}
aside label{display:block;font-size:13px}
.pos label{display:flex;gap:6px;align-items:center}
.pos .n{margin-left:auto;color:var(--mut);font-size:11px}
.rng{display:flex;align-items:center;gap:6px}
.rng input[type=range]{flex:1}
.rng output{min-width:42px;text-align:right;font-variant-numeric:tabular-nums}
.hint{color:var(--mut);font-size:11.5px;margin:2px 0 0}
main{padding:12px 16px;overflow:auto}
.bar{display:flex;gap:12px;align-items:center;margin-bottom:10px;flex-wrap:wrap;color:var(--mut);font-size:12.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:8px 10px;cursor:pointer}
.card:hover{border-color:var(--acc)}
.card.sel{outline:2px solid var(--acc)}
.card.dim{opacity:.35}
.card h3{font-size:13px;margin:0 0 4px;display:flex;justify-content:space-between}
.card h3 span{color:var(--mut);font-weight:400}
.prevbar{height:4px;background:var(--line);border-radius:2px;margin-bottom:6px}
.prevbar i{display:block;height:100%;background:var(--acc);border-radius:2px}
.words{font-size:13px;line-height:1.7}
.words b{font-weight:500}
.words .m{background:var(--hi)}
#detail{margin-top:16px;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px 14px}
#detail h2{margin:0 0 6px;font-size:15px}
.cols{display:grid;grid-template-columns:minmax(300px,1fr) minmax(300px,1fr);gap:18px}
@media (max-width:900px){.wrap{grid-template-columns:1fr}aside{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--line)}.cols{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-size:12.5px}
td,th{padding:3px 6px;border-bottom:1px solid var(--line);text-align:left}
th{color:var(--mut);font-weight:500}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--mut);margin:4px 0}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:3px;vertical-align:-1px}
svg text{fill:var(--fg);font-size:11px}
svg .mut{fill:var(--mut)}
.warn{color:var(--c1);font-size:12px}
</style>
</head>
<body>
<header>
  <h1>JLit トピックビューア</h1>
  <label>モデル <select id="model"></select></label>
  <div class="note">上位語を<b>品詞・頻度帯</b>で絞り，<b>λ</b>で並べ替えて読む。
  ⚠ ここで語を隠しても，その語は学習には効いている。除いて学習し直した結果と比べるには，
  モデルを切り替えること。</div>
</header>
<div class="wrap">
<aside>
  <h2>品詞</h2>
  <div class="pos" id="pos"></div>
  <div class="hint">品詞は UniDic の解析結果。人名が普通名詞と解析されることがある（→ 集中度）</div>

  <h2>頻度帯</h2>
  <label>全体の度数（最小）</label>
  <div class="rng"><input type="range" id="minc" min="0" max="4" step="0.1" value="0"><output id="mincO"></output></div>
  <label>出現作品の割合（最大）</label>
  <div class="rng"><input type="range" id="maxdr" min="0.05" max="1" step="0.05" value="1"><output id="maxdrO"></output></div>
  <div class="hint">多くの作品に出る語（言う・思う・顔）を除く</div>
  <label>1作品への集中度（最大）</label>
  <div class="rng"><input type="range" id="maxws" min="0.2" max="1" step="0.05" value="1"><output id="maxwsO"></output></div>
  <div class="hint">度数のうち1作品が占める割合。高い語はその作品にしか出ない（登場人物名など）</div>
  <label>1作家への集中度（最大）</label>
  <div class="rng"><input type="range" id="maxas" min="0.2" max="1" step="0.05" value="1"><output id="maxasO"></output></div>
  <div class="hint">同じ作家の複数作品に出る人物名（「素子」など）は，作品ではなく作家に集中する</div>
  <label>散らばり dp_in（最大）</label>
  <div class="rng"><input type="range" id="maxdp" min="0.2" max="1" step="0.05" value="1"><output id="maxdpO"></output></div>
  <div class="hint">Gries の DP を，その語がいちばん濃い時代の中で測ったもの。1 に近いほど少数の作品に固まる（bursty）。時代への偏りは罰しない</div>

  <h2>並べ方</h2>
  <label>relevance λ</label>
  <div class="rng"><input type="range" id="lam" min="0" max="1" step="0.05" value="1"><output id="lamO"></output></div>
  <div class="hint">1＝トピック内の確率順。下げるほどそのトピックに特有の語が上に来る（0.6 前後が目安）</div>
  <label>表示する語数 <input type="number" id="nw" min="5" max="50" value="12" style="width:60px"></label>

  <h2>語で探す</h2>
  <input type="text" id="q" placeholder="例：汽車" style="width:100%">
  <div class="hint">その語を上位（表示語数以内）に持つトピックだけを濃く表示</div>

  <h2>並べ替え</h2>
  <select id="sort"><option value="id">トピック番号</option><option value="prev">割合の大きい順</option>
  <option value="kept">絞り込み後に残る確率の大きい順</option></select>
  <p class="hint" id="modelinfo"></p>
</aside>
<main>
  <div class="bar" id="summary"></div>
  <div class="grid" id="grid"></div>
  <section id="detail" hidden></section>
</main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = id => document.getElementById(id);
const POSGROUPS = [
  ['普通名詞', l => l.startsWith('名詞-普通名詞')],
  ['固有名詞', l => l.startsWith('名詞-固有名詞')],
  ['動詞（自立）', l => l === '動詞-一般' || l.startsWith('動詞-一般')],
  ['動詞（非自立）', l => l.startsWith('動詞-非自立')],
  ['形容詞（自立）', l => l.startsWith('形容詞-一般')],
  ['形容詞（非自立）', l => l.startsWith('形容詞-非自立')],
  ['形状詞', l => l.startsWith('形状詞')],
  ['副詞', l => l.startsWith('副詞')],
  ['数詞', l => l.startsWith('名詞-数詞')],
  ['代名詞', l => l.startsWith('代名詞')],
  ['その他', l => true],
];
const COLORS = ['var(--c0)','var(--c5)','var(--c1)','var(--c1)','var(--c2)','var(--c2)','var(--c3)','var(--c4)','var(--c5)','var(--c5)','var(--c5)'];
const posGroup = l => POSGROUPS.findIndex(g => g[1](l));
let M = null, sel = null, gOf = [];
const st = {pos: new Set(POSGROUPS.map((_, i) => i)), minc: 1, maxdr: 1, maxws: 1, maxdp: 1, maxas: 1, lam: 1, nw: 12, q: '', sort: 'id'};

// UniDic は外来語の語彙素に原綴を付ける（テーブル-table）。表示は片仮名だけにし，
// 原綴はマウスを載せたときに出す（ロケット-locket のような解析の誤りを確かめるため）
const disp = w => String(w).replace(/-[A-Za-z][A-Za-z .'-]*$/, '');
function esc(s){return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

function initModel(i){
  M = D.models[i];
  gOf = M.vocab.map(v => posGroup(v[1]));
  const cnt = POSGROUPS.map(() => 0);
  M.vocab.forEach((v, j) => cnt[gOf[j]] += v[5]);
  $('pos').innerHTML = POSGROUPS.map((g, gi) => cnt[gi] ? `<label><input type="checkbox" data-g="${gi}" ${st.pos.has(gi)?'checked':''}>
    <i style="width:10px;height:10px;border-radius:2px;background:${COLORS[gi]}"></i>${g[0]}<span class="n">${(100*cnt[gi]/M.N).toFixed(1)}%</span></label>` : '').join('');
  $('pos').querySelectorAll('input').forEach(el => el.onchange = () => {
    const g = +el.dataset.g; el.checked ? st.pos.add(g) : st.pos.delete(g); render();});
  const maxc = Math.max(...M.vocab.map(v => v[2]||v[5]));
  $('minc').max = Math.log10(maxc).toFixed(1);
  $('modelinfo').innerHTML = `${esc(M.label)}：${M.K} トピック・${M.works.length} 作品・トークン ${M.N.toLocaleString()}<br>${esc(M.dir)}`;
  sel = null; $('detail').hidden = true;
  render();
}

function keep(j){
  const v = M.vocab[j];
  if (!st.pos.has(gOf[j])) return false;
  const c = v[2] || v[5];
  if (c < st.minc) return false;
  if (v[3] > st.maxdr + 1e-9) return false;
  if (v[4] > st.maxws + 1e-9) return false;
  if (v[6] > st.maxdp + 1e-9) return false;
  if (v[7] > st.maxas + 1e-9) return false;
  return true;
}

function ranked(t){
  // relevance = λ log p(w|t) + (1−λ) log(p(w|t)/p(w))
  const Tt = M.topicTotals[t], N = M.N, lam = st.lam;
  const out = [];
  let keptMass = 0, stored = 0;
  for (const [j, n] of M.tw[t]) {
    const pwt = n / Tt;
    stored += pwt;
    if (!keep(j)) continue;
    keptMass += pwt;
    const pw = M.vocab[j][5] / N;
    out.push({j, n, pwt, r: lam * Math.log(pwt) + (1 - lam) * Math.log(pwt / pw)});
  }
  out.sort((a, b) => b.r - a.r);
  // 残存＝ビューアが持っている上位語の確率のうち，絞り込み後に残った割合
  return {list: out, keptMass: stored ? keptMass / stored : 0};
}

function render(){
  $('mincO').textContent = st.minc.toLocaleString();
  $('maxdrO').textContent = Math.round(st.maxdr * 100) + '%';
  $('maxwsO').textContent = Math.round(st.maxws * 100) + '%';
  $('maxdpO').textContent = st.maxdp.toFixed(2);
  $('maxasO').textContent = Math.round(st.maxas * 100) + '%';
  $('lamO').textContent = st.lam.toFixed(2);
  const R = [];
  for (let t = 0; t < M.K; t++) R.push({t, ...ranked(t)});
  const q = st.q.trim();
  let order = R.slice();
  if (st.sort === 'prev') order.sort((a, b) => M.prev[b.t] - M.prev[a.t]);
  if (st.sort === 'kept') order.sort((a, b) => b.keptMass - a.keptMass);
  const maxPrev = Math.max(...M.prev);
  let hits = 0;
  $('grid').innerHTML = order.map(({t, list, keptMass}) => {
    const top = list.slice(0, st.nw);
    const has = q && top.some(x => M.vocab[x.j][0] === q || disp(M.vocab[x.j][0]) === q);
    if (has) hits++;
    const dim = q && !has ? ' dim' : '';
    const ws = top.map(x => {
      const v = M.vocab[x.j];
      const hit = q && (v[0] === q || disp(v[0]) === q);
      return `<b style="color:${COLORS[gOf[x.j]]}" class="${hit?'m':''}" title="${esc(v[0])}（${esc(v[1])}）">${esc(disp(v[0]))}</b>`;}).join(' ');
    return `<div class="card${dim}${sel===t?' sel':''}" data-t="${t}"><h3>T${String(t).padStart(2,'0')}
      <span title="全体に占める割合・絞り込み後に残った語の確率の割合">${(100*M.prev[t]).toFixed(1)}%・残存 ${(100*keptMass).toFixed(0)}%</span></h3>
      <div class="prevbar"><i style="width:${100*M.prev[t]/maxPrev}%"></i></div><div class="words">${ws || '<span class="warn">表示できる語が無い</span>'}</div></div>`;
  }).join('');
  $('grid').querySelectorAll('.card').forEach(el => el.onclick = () => {sel = +el.dataset.t; render(); detail(sel); $('detail').scrollIntoView({behavior:'smooth'});});
  const nk = M.vocab.filter((_, j) => keep(j)).length;
  $('summary').innerHTML = `表示対象の語 ${nk.toLocaleString()} / ${M.vocab.length.toLocaleString()}（各トピックの上位語から）` +
    (q ? `・「${esc(q)}」を上位 ${st.nw} 語に持つトピック ${hits}` : '') +
    `・「残存」＝そのトピックの上位語の確率のうち，絞り込み後に残った割合（低いトピックは隠した語でできている）`;
  if (sel !== null) detail(sel);
}

function barsSVG(items, w, labelW, fmt){
  const h = 16, H = items.length * h + 6, max = Math.max(...items.map(x => x.v), 1e-9);
  return `<svg width="100%" viewBox="0 0 ${w} ${H}" role="img">` + items.map((x, i) =>
    `<text x="${labelW-4}" y="${i*h+12}" text-anchor="end">${esc(x.show || x.label)}<title>${esc(x.label)}</title></text>
     <rect x="${labelW}" y="${i*h+3}" width="${Math.max(1,(w-labelW-60)*x.v/max)}" height="${h-5}" rx="2" fill="${x.c||'var(--acc)'}"><title>${esc(x.label)}：${fmt(x.v)}</title></rect>
     <text x="${labelW+(w-labelW-60)*x.v/max+4}" y="${i*h+12}" class="mut">${fmt(x.v)}</text>`).join('') + '</svg>';
}

function detail(t){
  const {list, keptMass} = ranked(t);
  const top = list.slice(0, 30);
  const words = barsSVG(top.map(x => ({label: M.vocab[x.j][0], show: disp(M.vocab[x.j][0]), v: x.pwt, c: COLORS[gOf[x.j]]})), 420, 90,
    v => (100*v).toFixed(2) + '%');
  const per = barsSVG(M.periods.map((p, i) => ({label: p.replace(/^\d_/, ''), v: M.periodTopic[i][t]})), 420, 150,
    v => (100*v).toFixed(1) + '%');
  // 2つの割合を区別する
  //   作品内の割合      P(t|作品)：その作品のチャンクでこのトピックが占める割合の平均
  //   トピックに占める割合 P(作品|t)：このトピックの重み（割合×チャンク数の総和）のうち，
  //                      その作品（作家）のチャンクから来ている割合
  const mass = M.works.map((w, i) => M.workTopic[i][t] * w[5]);
  const totalMass = mass.reduce((s, x) => s + x, 0) || 1;
  const ws = M.works.map((w, i) => ({w, v: M.workTopic[i][t], sh: mass[i] / totalMass}))
    .sort((a, b) => b.v - a.v).slice(0, 12);
  const byAuthor = {};
  M.works.forEach((w, i) => { const a = (byAuthor[w[1]] ||= {v: [], m: 0}); a.v.push(M.workTopic[i][t]); a.m += mass[i]; });
  const au = Object.entries(byAuthor).map(([a, o]) => ({a, v: o.v.reduce((s, x) => s + x, 0) / o.v.length, n: o.v.length, sh: o.m / totalMass}))
    .sort((a, b) => b.sh - a.sh).slice(0, 8);
  const pct = x => (100 * x).toFixed(1) + '%';
  const wtab = `<table><tr><th>作品</th><th>作家</th><th>初出</th>
      <th class="num" title="その作品のチャンクで，このトピックが占める割合の平均">作品内の割合</th>
      <th class="num" title="このトピックの重み全体のうち，その作品から来ている割合">トピックに占める割合</th></tr>` +
    ws.map(x => `<tr><td>${esc(x.w[2])}</td><td>${esc(x.w[1])}</td><td>${esc(x.w[3])}</td><td class="num">${pct(x.v)}</td><td class="num">${pct(x.sh)}</td></tr>`).join('') + '</table>';
  const atab = `<table><tr><th>作家</th><th class="num">作品数</th>
      <th class="num" title="その作家の各作品での「作品内の割合」の平均">作品内の割合（平均）</th>
      <th class="num" title="このトピックの重み全体のうち，その作家の作品から来ている割合">トピックに占める割合</th></tr>` +
    au.map(x => `<tr><td>${esc(x.a)}</td><td class="num">${x.n}</td><td class="num">${pct(x.v)}</td><td class="num">${pct(x.sh)}</td></tr>`).join('') + '</table>';
  const [topA, topAm] = Object.entries(byAuthor).map(([a, o]) => [a, o.m]).sort((a, b) => b[1] - a[1])[0] || ['', 0];
  const topW = Math.max(...mass) / totalMass, topWi = mass.indexOf(Math.max(...mass));
  let warn = '';
  if (topW > 0.5) warn = `<p class="warn">⚠ このトピックの重みの ${(100*topW).toFixed(0)}% が1作品（${esc(M.works[topWi][2])}）から来ている（トピックに占める割合）。主題ではなく作品の目印である可能性が高い。</p>`;
  else if (topAm / totalMass > 0.5) warn = `<p class="warn">⚠ このトピックの重みの ${(100*topAm/totalMass).toFixed(0)}% が1作家（${esc(topA)}）の作品から来ている（トピックに占める割合）。主題ではなく作家の目印である可能性がある。</p>`;
  const note = `<p class="hint">「作品内の割合」＝その作品の中でこのトピックが占める割合（P(トピック｜作品)）。
    「トピックに占める割合」＝このトピックの重みのうちその作品から来る割合（P(作品｜トピック)）。
    向きが逆なので値は一致しない。どちらも語の絞り込みでは変わらない。</p>`;
  $('detail').hidden = false;
  $('detail').innerHTML = `<h2>T${String(t).padStart(2,'0')}　全体の ${(100*M.prev[t]).toFixed(1)}%・表示の残存 ${(100*keptMass).toFixed(0)}%</h2>
    <div class="legend">${POSGROUPS.map((g, gi) => `<span><i style="background:${COLORS[gi]}"></i>${g[0]}</span>`).slice(0, 8).join('')}</div>
    ${warn}
    <div class="cols"><div><h3 style="font-size:13px">上位語（λ=${st.lam.toFixed(2)} の順・棒は p(w|t)）</h3>${words}
      <p class="hint">MALLET の上位語（絞り込み前）：${esc(M.keys[t] || '')}</p>
      <p class="hint">外来語は片仮名だけを表示している。語にマウスを載せると UniDic の語彙素（原綴つき）と品詞が出る。</p></div>
    <div><h3 style="font-size:13px">時代別の割合（チャンク平均）</h3>${per}
      <h3 style="font-size:13px">このトピックが多い作品（作品内の割合の順）</h3>${wtab}
      <h3 style="font-size:13px">このトピックを担う作家（トピックに占める割合の順）</h3>${atab}${note}</div></div>`;
}

$('model').innerHTML = D.models.map((m, i) => `<option value="${i}">${esc(m.label)}</option>`).join('');
$('model').onchange = e => initModel(+e.target.value);
const logInput = el => Math.round(Math.pow(10, +el.value));
$('minc').oninput = e => { st.minc = +e.target.value === 0 ? 1 : logInput(e.target); render(); };
$('maxdr').oninput = e => { st.maxdr = +e.target.value; render(); };
$('maxws').oninput = e => { st.maxws = +e.target.value; render(); };
$('maxdp').oninput = e => { st.maxdp = +e.target.value; render(); };
$('maxas').oninput = e => { st.maxas = +e.target.value; render(); };
$('lam').oninput = e => { st.lam = +e.target.value; render(); };
$('nw').oninput = e => { st.nw = Math.max(5, Math.min(50, +e.target.value || 12)); render(); };
$('q').oninput = e => { st.q = e.target.value; render(); };
$('sort').onchange = e => { st.sort = e.target.value; render(); };
initModel(0);
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--model', action='append', required=True, metavar='名前=ディレクトリ',
                    help='MALLET の出力ディレクトリ。複数与えると切り替えて比べられる')
    ap.add_argument('--meta', default=None, help='既定: *_v3_local.csv > v3 > v2')
    ap.add_argument('--lexicon', default=os.path.join(ROOT, 'data', 'tokens', 'lexicon.tsv'),
                    help='18_pos_select.py が書く語の品詞・頻度帯の表')
    ap.add_argument('--top', type=int, default=400,
                    help='各トピックから持っておく語の数（多いほど λ を下げたときに正確）')
    ap.add_argument('--min-count', type=int, default=3,
                    help='モデル内の度数がこれ未満の語はビューアに入れない')
    ap.add_argument('--out', default=os.path.join(ROOT, 'my_work', 'results', 'topic_viewer.html'))
    args = ap.parse_args()

    meta = load_meta(args.meta or default_meta())
    lex = load_lexicon(args.lexicon)
    if not lex:
        print(f'[warn] 語の品詞表が無い: {args.lexicon}\n'
              '       品詞と頻度帯の絞り込みが効かない。18_pos_select.py を先に走らせること。')
    models = []
    for spec in args.model:
        label, _, d = spec.partition('=')
        if not d:
            label, d = os.path.basename(os.path.normpath(spec)), spec
        if not os.path.isdir(d):
            sys.exit(f'MALLET の出力が無い: {d}')
        models.append(build_model(label, d, meta, lex, args.top, args.min_count))
    unknown = sum(1 for m in models for v in m['vocab'] if v[1] == '不明')
    if lex and unknown:
        print(f'[warn] 品詞表に無い語が {unknown:,} ある（別の辞書・別の 05 の出力で学習した？）')

    data = json.dumps({'models': models}, ensure_ascii=False, separators=(',', ':'))
    data = data.replace('</', '<\\/')
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        fh.write(HTML.replace('__DATA__', data))
    print(f'[ok  ] {args.out}（{os.path.getsize(args.out) / 1e6:.1f} MB）')
    print('       ブラウザで開く。サーバは要らない。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
