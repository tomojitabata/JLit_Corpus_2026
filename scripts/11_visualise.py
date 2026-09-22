#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
11_visualise.py
===============
結果の可視化。日本語フォントの設定を含む（ここでつまずく受講生が多い）。

作図するもの
------------
``corpus_balance.svg``      時代 × ジャンル × 語数のコーパス構成
``pca_works.svg``           最頻語 PCA 上の作品配置（時代で着色）
``delta_dendrogram.svg``    Burrows's Delta によるクラスタ樹形図
``semantic_change.svg``     意味変化の大きい語の推移
``topic_heatmap.svg``       時代 × トピックのヒートマップ
``topic_trends.svg``        主要トピックの通時推移

出力は **SVG（ベクタ）** である
-------------------------------
論文・スライドに載せる図は拡大しても劣化してはならない。PNG は
解像度が固定されるので，投影や印刷で文字が潰れる。SVG なら任意の倍率で
鮮明であり，Illustrator や Inkscape で軸ラベルだけを直すこともできる。

文字の扱いは ``--svg-text`` で選ぶ。

``path``（既定）
    文字をアウトライン（図形）に変換する。日本語フォントが入っていない
    環境で開いても**必ず正しく表示される**。配布・投稿用はこちら。
``none``
    文字を ``<text>`` 要素のまま残す。ファイルが小さく，検索・編集・
    翻訳ができる。ただし閲覧側に同じ日本語フォントが必要。

点の多い散布図は ``rasterized=True`` で点だけをラスタ化し，
軸と文字はベクタのまま保つ（ファイルの肥大を防ぐ）。

使い方
------
    python3 11_visualise.py --meta metadata/corpus_metadata_v2.csv \\
        --descriptive results/descriptive --w2v results/w2v \\
        --mallet results/mallet --out results/figures
"""
from __future__ import annotations

import argparse
import csv
import os

import numpy as np

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt                                  # noqa: E402
from matplotlib import font_manager                              # noqa: E402

# 色覚多様性に配慮した離散パレット（Okabe–Ito）
PALETTE = ['#0072B2', '#E69F00', '#009E73', '#CC79A7',
           '#56B4E9', '#D55E00', '#F0E442', '#666666']

FIG_EXT = 'svg'          # 図はすべてベクタで出す
RASTER_DPI = 200         # rasterized=True の要素だけに効く解像度


def setup_svg(fonttype: str = 'path') -> None:
    """SVG 出力の既定を設定する。

    ``svg.fonttype``
        ``path`` … 文字をアウトライン化。フォントの無い環境でも崩れない
        ``none`` … 文字を <text> のまま残す。軽く編集できるがフォント依存
    """
    plt.rcParams['svg.fonttype'] = fonttype
    plt.rcParams['savefig.bbox'] = 'tight'
    plt.rcParams['savefig.transparent'] = False


def save_fig(fig, out: str, stem: str) -> str:
    """図を SVG で保存し，パスを返す。"""
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f'{stem}.{FIG_EXT}')
    fig.savefig(path, format=FIG_EXT, dpi=RASTER_DPI)
    plt.close(fig)
    return path


def setup_japanese_font(preferred: str | None = None) -> str:
    """日本語が豆腐（□）にならないようフォントを設定する。

    macOS: Hiragino Sans / Hiragino Maru Gothic ProN
    Windows: Yu Gothic / MS Gothic / Meiryo
    Linux: Noto Sans CJK JP / IPAexGothic
    """
    candidates = [preferred] if preferred else []
    candidates += ['Hiragino Sans', 'Hiragino Maru Gothic ProN', 'Hiragino Kaku Gothic ProN',
                   'Yu Gothic', 'Meiryo', 'MS Gothic',
                   'Noto Sans CJK JP', 'Noto Sans JP', 'IPAexGothic', 'IPAPGothic',
                   'TakaoPGothic', 'VL PGothic']
    available = {f.name for f in font_manager.fontManager.ttflist}
    for c in candidates:
        if c and c in available:
            plt.rcParams['font.family'] = c
            plt.rcParams['axes.unicode_minus'] = False
            print(f'[font] 日本語フォント: {c}')
            return c
    print('[font] 日本語フォントが見つからない。図のラベルが □ になる可能性がある。')
    print('       macOS なら通常 Hiragino Sans が入っている。Linux は')
    print('       sudo apt install fonts-noto-cjk / Windows は Yu Gothic を使う。')
    return ''


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))


def fig_corpus_balance(meta, out):
    periods = sorted({m['period'] for m in meta})
    genres = sorted({m['genre_main'] for m in meta})
    data = np.zeros((len(genres), len(periods)))
    for m in meta:
        data[genres.index(m['genre_main']), periods.index(m['period'])] += int(m['tokens'] or 0)
    fig, ax = plt.subplots(figsize=(10, 5))
    bottom = np.zeros(len(periods))
    for i, g in enumerate(genres):
        ax.bar(range(len(periods)), data[i] / 1e6, bottom=bottom / 1e6,
               label=g, color=PALETTE[i % len(PALETTE)], width=0.62)
        bottom += data[i]
    ax.set_xticks(range(len(periods)))
    ax.set_xticklabels([p.split('_', 1)[1] for p in periods], rotation=20, ha='right')
    ax.set_ylabel('延べ語数（百万語）')
    ax.set_title('コーパス構成：時代区分 × ジャンル')
    ax.legend(frameon=False)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='y', alpha=.25)
    for i, p in enumerate(periods):
        n = sum(1 for m in meta if m['period'] == p)
        ax.text(i, bottom[i] / 1e6 + .03, f'{n}点', ha='center', fontsize=9, color='#444')
    fig.tight_layout()
    save_fig(fig, out, 'corpus_balance')


def fig_pca(rows, out):
    if not rows:
        return
    periods = sorted({r['period'] for r in rows})
    fig, ax = plt.subplots(figsize=(9, 7))
    for i, p in enumerate(periods):
        sel = [r for r in rows if r['period'] == p]
        ax.scatter([float(r['PC1']) for r in sel], [float(r['PC2']) for r in sel],
                   rasterized=True,
                   s=46, color=PALETTE[i % len(PALETTE)],
                   label=p.split('_', 1)[1], alpha=.85, edgecolor='white', linewidth=.6)
    for r in rows:
        ax.annotate(f"{r['author_ja']}", (float(r['PC1']), float(r['PC2'])),
                    fontsize=6.5, alpha=.65, xytext=(3, 3), textcoords='offset points')
    ax.set_xlabel('PC1')
    ax.set_ylabel('PC2')
    ax.set_title('最頻語相対頻度の主成分分析（作品単位）')
    ax.legend(frameon=False, fontsize=9)
    ax.axhline(0, color='#ccc', lw=.8)
    ax.axvline(0, color='#ccc', lw=.8)
    ax.spines[['top', 'right']].set_visible(False)
    fig.tight_layout()
    save_fig(fig, out, 'pca_works')


def fig_dendrogram(path, out):
    if not os.path.exists(path):
        return
    try:
        from scipy.cluster.hierarchy import dendrogram, linkage
        from scipy.spatial.distance import squareform
    except ImportError:
        print('[skip] scipy がないため樹形図は省略（pip install scipy）')
        return
    rows = list(csv.reader(open(path, encoding='utf-8-sig')))
    labels = rows[0][1:]
    D = np.array([[float(x) for x in r[1:]] for r in rows[1:]])
    D = (D + D.T) / 2
    np.fill_diagonal(D, 0)
    Z = linkage(squareform(D, checks=False), method='ward')
    fig, ax = plt.subplots(figsize=(9, max(6, len(labels) * 0.22)))
    dendrogram(Z, labels=labels, orientation='right', ax=ax,
               color_threshold=.7 * Z[:, 2].max(), leaf_font_size=7)
    ax.set_title("Burrows's Delta によるクラスタ（Ward 法）")
    ax.set_xlabel('距離')
    fig.tight_layout()
    save_fig(fig, out, 'delta_dendrogram')


def fig_semantic_change(w2v_dir, out, topn=12):
    path = os.path.join(w2v_dir, 'semantic_change.csv')
    rows = read_csv(path)
    if not rows:
        return
    step_cols = [c for c in rows[0] if c.startswith('step_')]
    if not step_cols:
        return
    top = rows[:topn]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    x = range(len(step_cols))
    for i, r in enumerate(top):
        ys = [float(r[c]) for c in step_cols]
        ax.plot(x, ys, marker='o', ms=4, lw=1.4,
                color=PALETTE[i % len(PALETTE)], alpha=.9, label=r['term'])
    ax.set_xticks(list(x))
    ax.set_xticklabels([c.replace('step_', '').replace('1_', '').replace('2_', '')
                        .replace('3_', '').replace('4_', '').replace('5_', '')
                        .replace('6_', '') for c in step_cols],
                       rotation=25, ha='right', fontsize=8)
    ax.set_ylabel('隣接スライス間のコサイン距離')
    ax.set_title('語義の変化量（Procrustes 整列後）')
    ax.legend(frameon=False, ncol=3, fontsize=8)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='y', alpha=.25)
    fig.tight_layout()
    save_fig(fig, out, 'semantic_change')


def fig_topics(mallet_dir, out, top_k=20):
    path = os.path.join(mallet_dir, 'topic_by_period.csv')
    rows = read_csv(path)
    if not rows:
        return
    summ = {int(r['topic']): r['top_words'] for r in read_csv(
        os.path.join(mallet_dir, 'topics_summary.csv'))}
    periods = [r['period'] for r in rows]
    tcols = [c for c in rows[0] if c.startswith('T')]
    M = np.array([[float(r[c]) for c in tcols] for r in rows])
    var = M.std(axis=0)
    sel = np.argsort(-var)[:top_k]
    sel = sel[np.argsort([np.argmax(M[:, t]) for t in sel])]

    fig, ax = plt.subplots(figsize=(11, max(5, len(sel) * 0.34)))
    im = ax.imshow(M[:, sel].T, aspect='auto', cmap='magma')
    ax.set_xticks(range(len(periods)))
    ax.set_xticklabels([p.split('_', 1)[-1] for p in periods], rotation=25,
                       ha='right', fontsize=9)
    labels = []
    for t in sel:
        words = (summ.get(int(tcols[t][1:]), '') or '').split()[:5]
        labels.append(f'T{int(tcols[t][1:]):02d} ' + ' '.join(words))
    ax.set_yticks(range(len(sel)))
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.set_title('時代 × トピック（時代内で変動の大きい上位トピック）')
    fig.colorbar(im, ax=ax, label='平均トピック確率', shrink=.8)
    fig.tight_layout()
    save_fig(fig, out, 'topic_heatmap')

    fig, ax = plt.subplots(figsize=(10, 5.5))
    for i, t in enumerate(sel[:8]):
        ax.plot(range(len(periods)), M[:, t], marker='o', ms=4,
                color=PALETTE[i % len(PALETTE)], lw=1.5, label=labels[i][:28])
    ax.set_xticks(range(len(periods)))
    ax.set_xticklabels([p.split('_', 1)[-1] for p in periods], rotation=25,
                       ha='right', fontsize=9)
    ax.set_ylabel('平均トピック確率')
    ax.set_title('主要トピックの通時推移')
    ax.legend(frameon=False, fontsize=7.5, ncol=2)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='y', alpha=.25)
    fig.tight_layout()
    save_fig(fig, out, 'topic_trends')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--meta', required=True)
    ap.add_argument('--descriptive', default=None)
    ap.add_argument('--w2v', default=None)
    ap.add_argument('--mallet', default=None)
    ap.add_argument('--out', required=True)
    ap.add_argument('--font', default=None)
    ap.add_argument('--svg-text', choices=('path', 'none'), default='path',
                    help='SVG 内の文字を path（アウトライン化・環境非依存）で '
                         '持つか none（<text> のまま・編集可）で持つか')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    setup_svg(args.svg_text)
    setup_japanese_font(args.font)

    meta = read_csv(args.meta)
    if meta:
        fig_corpus_balance(meta, args.out)
    if args.descriptive:
        fig_pca(read_csv(os.path.join(args.descriptive, 'pca_coordinates.csv')), args.out)
        fig_dendrogram(os.path.join(args.descriptive, 'delta_matrix.csv'), args.out)
    if args.w2v:
        fig_semantic_change(args.w2v, args.out)
    if args.mallet:
        fig_topics(args.mallet, args.out)
    print(f'[ok  ] 図（SVG）→ {args.out}')
    for f in sorted(os.listdir(args.out)):
        kb = os.path.getsize(os.path.join(args.out, f)) / 1024
        print(f'    {f:<28}{kb:>8,.0f} KB')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
