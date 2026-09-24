#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_notebooks.py
=================
全8ステップの講義ノートブックを生成する。ノートブックを手で編集すると出力セルや
実行カウントが混ざって差分が読めなくなるため，**内容はこのファイルで管理し，
ノートブックは生成物として扱う**。

    python3 scripts/make_notebooks.py --out notebooks
"""
from __future__ import annotations

import argparse
import json
import os
import re

# --------------------------------------------------------------------------
# 課題の提出先（年度ごとに書き換える）。md セルの {ZULIP_ORG} / {ZULIP_CHANNEL}
# は build() で置き換える。手順書 docs/00_setup_students.md §5.3 と揃えること。
# --------------------------------------------------------------------------
ZULIP_ORG = 'dh-uosaka.zulipchat.com'
ZULIP_CHANNEL = '2026年度テクスト分析論B'

# --------------------------------------------------------------------------
# 共通のセル
# --------------------------------------------------------------------------
PREAMBLE = r'''# ---- 共通の準備（毎回このセルから実行する）----------------------------
import os, sys, csv, json, math, random, shutil, subprocess, warnings
import importlib.util
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.colors import LinearSegmentedColormap

warnings.filterwarnings('ignore', category=FutureWarning)

# リポジトリのルートを自動で探す（my_work/notebooks/ でも notebooks/ でも，上へたどる）
ROOT = Path.cwd()
while not (ROOT / 'config' / 'pipeline.yaml').exists() and ROOT != ROOT.parent:
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / 'scripts'))
print('ROOT =', ROOT)
if Path.cwd().resolve() == (ROOT / 'notebooks').resolve():
    print('[注意] 配布版（notebooks/）を直接開いている。実行すると次の git pull が止まる。\n'
          '       python scripts/copy_notebooks.py でコピーを作り，my_work/notebooks/ の方を開くこと。')

# 日本語フォント（□ にならないように）
for cand in ['Hiragino Sans', 'Yu Gothic', 'Meiryo',
             'Noto Sans CJK JP', 'IPAexGothic', 'MS Gothic']:
    if cand in {f.name for f in font_manager.fontManager.ttflist}:
        plt.rcParams['font.family'] = cand
        break
else:
    print('[!] 日本語フォントが見つかりません。docs/00_setup_students.md §1.7 を参照。')
plt.rcParams['axes.unicode_minus'] = False
plt.rcParams['figure.dpi'] = 120

# ---- 図はすべて SVG（ベクタ）で保存する ---------------------------------
# 論文・スライドに載せる図は拡大しても劣化してはならない。PNG は解像度が
# 固定されるので，投影や印刷で文字が潰れる。SVG なら任意の倍率で鮮明で，
# Illustrator / Inkscape で軸ラベルだけを直すこともできる。
FIG_EXT   = 'svg'
RASTER_DPI = 200          # rasterized=True の要素にだけ効く
plt.rcParams['svg.fonttype']       = 'path'   # 文字をアウトライン化して環境非依存に
plt.rcParams['savefig.transparent'] = False
# 画面へのインライン表示は既定（PNG）のままにする。
# InlineBackend.figure_formats を 'svg' に変えると，JupyterLab や
# VS Code の版によっては図がまったく表示されなくなることがある。
# **保存されるファイルは SVG** なので，論文・スライドに使うほうは
# ベクタで手元に残る。画面で拡大して見たいときは save_fig が表示する
# パスの .svg をブラウザで開くこと。

def need(path, hint=''):
    """必要な入力があるか確かめる。無ければ**理由を表示して** False を返す。

    セルを `if p.exists():` で囲むと，入力が無いときに何も起きない。
    学生には「壊れている」と「まだ前の工程を走らせていない」の区別が
    つかず，図が出ないという相談の大半がこれである。必ず理由を出す。
    """
    p = Path(path)
    try:
        ok = p.is_file() or (p.is_dir() and any(p.iterdir()))
    except OSError:
        ok = False
    if not ok:
        print(f'[未実行] {p} がありません。')
        if hint:
            print(f'         {hint}')
        print('         この Step の前のセルを上から順に実行すること。'
              '\n         それでも出ない場合は，前の Step のノートブックが'
              '最後まで通っているか確認する。')
    return ok


# 旧名 → 新名。**中身は 0–1 の割合なので per cent は誤称**であった。
# 2026-09-24 に改名。古い出力を持っている人のために読み替えだけは残す。
LEGACY_COLS = {'df_all_pct': 'df_all_prop', 'df_in_pct': 'df_in_prop'}


def read_table(path, **kw):
    """CSV を読み，**古い列名があれば新しい名前に読み替える**。

    列名の約束：割合（0–1）は ``_prop`` / ``_ratio`` / ``_share``，
    百分率（0–100）だけを ``_pct`` と綴る。``df_all_prop`` が 0.1584 なら
    15.84 % の意である。読み替えたときは黙らずに知らせる — 黙って直すと，
    手元の CSV と教材の列名が食い違っていることに気づけないため。
    """
    d = pd.read_csv(path, **kw)
    old = {k: v for k, v in LEGACY_COLS.items()
           if k in d.columns and v not in d.columns}
    if old:
        d = d.rename(columns=old)
        print('[note] 古い列名を読み替えた: '
              + '，'.join(f'{k}→{v}' for k, v in old.items())
              + '\n       07_descriptive_stats.py を走らせ直すと'
                '新しい名前で書き出される。')
    return d


def load_meta(path=None, analysis_only=True):
    """メタデータを読む。既定では**分析に使う行だけ**を返す。

    落とすのは2種類。書誌としては残すが，集計に足してはいけない行である。
      superseded … v1 の合本。増補で分冊ごとに取り直したので，足すと
                   同じ作品を二重に数える
      merged     … 分冊。03b で canonical の巻に本文を統合したので，
                   この行はもう本文を持たない（『夜明け前』『家』）
      too_short  … 1チャンクにも満たず，チャンク単位の分析に乗らない

    生の表がほしいときは ``analysis_only=False``。
    """
    df = pd.read_csv(path or META)
    if analysis_only and 'completeness' in df.columns:
        drop = df['completeness'].isin(['superseded', 'merged', 'too_short'])
        if drop.any():
            names = '，'.join(df.loc[drop, 'title_aozora'].astype(str))
            print(f'[meta] 分析から除外 {int(drop.sum())} 行: {names}')
        df = df[~drop].reset_index(drop=True)
    return df


def w_ljust(text, width):
    """全角を2桁と数えて左詰めする。

    ``f'{s:<26}'`` は**文字数**で詰めるので，日本語の作品名を並べると
    桁が揃わない（全角は2桁ぶんの幅を占める）。表として読ませるなら
    表示幅で詰めること。

    **なお，一覧を出すなら ``show()`` で表にするほうがよい**（下記）。
    この関数は，表にしにくいもの（KWIC の前後文脈など）を print で
    並べるときに使う。
    """
    import unicodedata
    text = str(text)
    w = sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)
    return text + ' ' * max(0, width - w)


# ---------------------------------------------------------------------------
# 分析結果の表示
# ---------------------------------------------------------------------------
# **一覧は print ではなく表で出す。**
#   * print は桁が揃わない（全角の幅）。数字の比較がしにくい
#   * 列に名前が付かないので，あとで見返したときに何の数字か分からない
#   * 並べ替えも絞り込みもできない
# 表にすると，列名がそのまま「何を測ったか」の記録になる。
# **ただし何でも表にするのではない。** 単発の数値・警告・KWIC の前後文脈は
# 文のほうが読みやすい。目安は「2列以上あるか」「行が並ぶか」。
TABLE_STYLES = [
    {'selector': 'caption',
     'props': [('caption-side', 'top'), ('text-align', 'left'),
               ('font-weight', '600'), ('padding', '0 0 .4em 0'),
               ('color', '#33322e'), ('font-size', '95%')]},
    {'selector': 'th',
     'props': [('background', '#f2f2ef'), ('text-align', 'left'),
               ('font-weight', '600'), ('padding', '.26em .7em'),
               ('border-bottom', '1px solid #c6c5bd'), ('white-space', 'nowrap')]},
    {'selector': 'td',
     'props': [('padding', '.22em .7em'), ('border-bottom', '1px solid #ecebe6')]},
    {'selector': 'tbody tr:hover td', 'props': [('background', '#f7f7f4')]},
]


def show(df, caption='', fmt=None, index=False, header=True, na='—', align=None):
    """DataFrame を表として表示する。Jupyter 以外でも落ちない。

    ``fmt`` は pandas の ``Styler.format`` に渡す辞書
    （例 ``{'一致率': '{:.1%}', 'G²': '{:.0f}'}``）。
    数値の列は自動で右寄せにする。``align`` で列ごとに寄せを指定できる。
    KWIC の左文脈を ``align={'左文脈': 'right'}`` にすると，
    **キーワードが縦に揃う**（等幅フォントに頼らずに揃う）。
    返り値は ``df`` なので ``t = show(df)`` として続けて使える。
    """
    if isinstance(df, pd.Series):
        df = df.to_frame()
    try:
        from IPython.display import display as _display
        st = df.style.format(fmt, na_rep=na) if fmt else df.style.format(na_rep=na)
        st = st.set_table_styles(TABLE_STYLES)
        num = list(df.select_dtypes('number').columns)
        if num:
            st = st.set_properties(subset=num, **{'text-align': 'right'})
        for col, side in (align or {}).items():
            if col in df.columns:
                st = st.set_properties(subset=[col],
                                       **{'text-align': side,
                                          'white-space': 'pre'})
        if caption:
            st = st.set_caption(caption)
        if not index:
            st = st.hide(axis='index')
        if not header:
            st = st.hide(axis='columns')
        _display(st)
    except Exception:                                   # noqa: BLE001
        # ノートブックの外（スクリプトから import したとき）でも読める形
        if caption:
            print(caption)
        print(df.to_string(index=index, header=header))
    return df


def grid(items, ncol=8, caption=''):
    """語の並びを ``ncol`` 列の表にして表示する。

    40 語を1行に流すと折り返しで読めない。列に切ると目で追える。
    順位が要るなら ``show()`` に順位列を付けた表を渡すこと。
    """
    items = [str(x) for x in items]
    rows = [items[i:i + ncol] for i in range(0, len(items), ncol)]
    rows = [r + [''] * (ncol - len(r)) for r in rows]
    t = pd.DataFrame(rows, columns=[f'_{i}' for i in range(ncol)])
    return show(t, caption=caption, header=False)


def work_rows(meta_df=None):
    """``work_stem`` からメタデータの行を引く辞書を作る。

    ``meta_df`` を省くと**分析対象外の行も含めた全件**から作る。
    表示用の名前は，分析から外した作品についても引けるほうがよい。

    **キーの綴りに注意。** 青空文庫の作品 ID は索引では 0 埋めされていない
    （``1743``）が，本パイプラインのファイル名は6桁に 0 埋めしてある
    （``000119_001743``）。素朴に連結すると ``000119_1743`` となり，
    **1件も一致しない**。辞書は空振りしても例外を出さないので，
    誰の何だか分からないまま最後まで通ってしまう。両方の綴りを登録する。

    ``file_v1`` は増補 45 点では空である。``os.path.splitext(nan)`` は
    例外になるので，文字列であることを確かめてから使う。
    """
    if meta_df is None:
        meta_df = load_meta(analysis_only=False)
    d = {}
    for _, r in meta_df.iterrows():
        fv = r.get('file_v1')
        if isinstance(fv, str) and fv.strip():
            d[os.path.splitext(fv)[0]] = r
        pid = str(r.get('aozora_person_id') or '').strip()
        wid = str(r.get('aozora_work_id') or '').strip()
        if pid and wid and pid.lower() != 'nan' and wid.lower() != 'nan':
            for k in (f'{pid.zfill(6)}_{wid.zfill(6)}',
                      f'{pid}_{wid}', f'{pid.zfill(6)}_{wid}'):
                d[k] = r
    return d


def work_labels(meta_df=None, maxlen=12, with_year=False):
    """``work_stem`` → ``作者『作品』`` の対応表を返す。

    ``000119_001743`` と出されても誰の何だか分からない。距離の近い
    ペアを見るときに**どの作家のどの作品か**が分からなければ，
    「作家効果か時代効果か」という問いにそもそも答えられない。
    表示するときは必ずこれを通すこと。
    """
    out = {}
    for k, r in work_rows(meta_df).items():
        t = str(r.get('title_aozora') or '')
        lab = f"{r.get('author_ja', '?')}『{t[:maxlen]}』"
        if with_year and str(r.get('year_first') or '').strip():
            lab += f"({r['year_first']})"
        out[k] = lab
    return out


def attach_meta(df, cols, stem_col='work_stem', meta_df=None, quiet=False,
                fill_blank=True):
    """``df`` に足りないメタデータの列を，``work_stem`` から引いて補う。

    ``fill_blank=True``（既定）なら，**列はあるのに値が空**のセルも補う。
    列が無いより，列があって半分が空のほうが危ない。列が無ければ
    ``AttributeError`` で止まるが，値が空だと**図がそのまま描けてしまう**。
    2026-09-22 に 07 の突合が外れ，101 点のうち 62 点の ``year_first`` が
    空になった。図は描けたが，62 点が「初出年不明」の灰色で並んだ。
    値の空きも数えて報告し，ここで補えるものは補う。

    分析スクリプトの出力は，その分析に要る列しか書かない。
    ``09_doc2vec.py`` の ``work_vectors.csv`` に ``genre_main`` が無いのは
    その一例である。ノートブックで ``wv.genre_main`` と書けば
    ``AttributeError: 'DataFrame' object has no attribute 'genre_main'``
    になるが，**足りないのは列であって情報ではない**。
    メタデータ表には必ずあるのだから，ここで引いて補えばよい。

    出力 CSV の列構成に図の描画が依存するのは弱い。分析スクリプトを
    書き換えるたびに図が落ちる。図の側で「要る列を宣言して取りに行く」
    ほうが，どちらを先に走らせても通る。

    引けなかった列は空のまま残し ``[warn]`` を出す。図が落ちるより，
    「この軸は色分けできなかった」と分かったうえで出るほうがよい。
    """
    df = df.copy()
    if stem_col not in df.columns:
        if not quiet:
            print(f'[warn] {stem_col} 列が無いので補完できない: {list(cols)}')
        for c in cols:
            if c not in df.columns:
                df[c] = ''
        return df

    rows = work_rows(meta_df)

    # **まずキーが合っているかを見る。** 合っていなければ何も補えない。
    # 「1件も合わない」のはたいてい 0 埋めの綴り違いで，黙って通すと
    # 全部の軸が空のまま図になる。
    stems = df[stem_col].astype(str)
    found = stems.map(lambda s: s in rows)
    if not quiet and not found.all():
        n_miss = int((~found).sum())
        lv = 'FATAL' if found.sum() == 0 else 'warn '
        print(f'[{lv}] {stem_col} がメタデータと突合できない行が '
              f'{n_miss}/{len(df)} 件ある: '
              + '，'.join(stems[~found].head(4)))
        if found.sum() == 0:
            print('        **1件も合っていない。** 作品 ID の 0 埋めの'
                  '綴り違いを疑うこと（例 000119_1743 と 000119_001743）。')
            print('        この表を作ったスクリプトのキーの作り方を直すこと。')

    def _blank(v):
        return v is None or str(v).strip().lower() in ('', 'nan', 'none')

    for c in cols:
        if c not in df.columns:
            vals = [(lambda r: '' if r is None or _blank(r.get(c))
                     else r.get(c))(rows.get(s)) for s in stems]
            df[c] = vals
            n = int(sum(1 for v in vals if str(v).strip()))
            if not quiet:
                mark = 'ok  ' if n == len(df) else 'warn'
                print(f'[{mark}] {c} をメタデータから補完: {n}/{len(df)} 件')
            continue

        if not fill_blank:
            continue
        # 列はある。空のセルだけを埋める。
        blank = df[c].map(_blank)
        if not blank.any():
            continue
        filled = 0
        vals = df[c].tolist()
        for i, (s, is_blank) in enumerate(zip(stems, blank)):
            if not is_blank:
                continue
            r = rows.get(s)
            if r is not None and not _blank(r.get(c)):
                vals[i] = r.get(c)
                filled += 1
        df[c] = vals
        if not quiet:
            left = int(sum(1 for v in vals if _blank(v)))
            mark = 'fix ' if left == 0 else 'warn'
            print(f'[{mark}] {c} は {int(blank.sum())}/{len(df)} 件が空だった'
                  f' → {filled} 件をメタデータから補完'
                  + ('' if left == 0 else f'（なお {left} 件が空）'))
            if left:
                print('        **その列で色分けする図・集計は，この件数を'
                      '報告に書くこと。**')
    return df


def label_points(ax, xs, ys, texts, fontsize=8, color='#333333', pad=4,
                 leader='line', leader_min=13, crowd_r=26,
                 leader_color='#8a8a83'):
    """散布図の注記を，重ならない位置だけに置き，遠いものは引き出し線で結ぶ。

    素朴に ``ax.annotate(t, (x, y))`` と書くと，**注目すべき点ほど一箇所に
    固まる**ので注記が必ず重なって読めなくなる。文語標識の上位は
    どれも口語標識がほぼ 0 で，対数軸の右下隅に密集するのが典型である。

    そこで点の周囲を順に試し，他の注記とも他の点とも重ならず，かつ軸の
    内側に収まる位置があればそこに置く。どこにも置けない注記は**置かずに
    数だけ報告する**。読めない字を重ねるより，図の外（下の表）で番号から
    引くほうがよい。

    **離れた位置に置いた注記は，引き出し線で点と結ぶ。** 避けた結果として
    注記は点から離れるので，線が無いとどの点の名前なのか分からなくなる
    ——密集した領域では隣の点の名前だと読まれる。線があれば，遠くへ逃がす
    ことに副作用が無くなるので，**近くに空きが無い注記も置ける**ようになる
    （候補の輪を 24・30 ポイントまで広げてあるのはそのため）。

    ``leader``
        ``'line'``（既定）… 矢じりの無い細線で結ぶ。図版の慣例はこちら。
        6.5pt の文字に矢じりを付けると，マーカーそのものを覆って点が読めなくなる
        ``'arrow'`` … 小さな矢じりを付ける
        ``'none'`` … 結ばない（従来どおり）
    ``leader_min``
        この距離（ポイント）より遠くに置いた注記を結ぶ。既定は 0，
        つまり**すべて結ぶ**。注記は必ず点から離れた位置に置かれるので，
        離れている以上「どの点の名前か」は線でしか確定しない。
        線を省くと，隣の点の名前だと読まれる余地が残る。
    ``crowd_r``
        注記の近くに**自分以外の点**がこの半径（ピクセル）内にあるかを
        見る。``leader_min`` を上げて線を減らしたときでも，
        紛れる相手が居る注記だけは必ず結ぶための保険である。

    表示座標で矩形の重なりを見るので，**軸の位置が確定してから**呼ぶ。
    ``fig.tight_layout()`` はこの関数より**前**に呼ぶこと（後で呼ぶと軸が
    動き，せっかく避けた位置がずれる）。戻り値は置けた注記の数。
    """
    from matplotlib.transforms import Bbox
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    texts = list(texts)
    if len(texts) == 0:
        return 0
    # **長さが違えば黙って切り詰めずに止める。** zip は短いほうに合わせるので，
    # 座標だけを絞り込んで名前を絞り忘れると，先頭から順に**別の作品の名前**が
    # 貼られた図が，何の警告も出さずに出来上がる。これがいちばん重い事故である。
    if not (len(xs) == len(ys) == len(texts)):
        raise ValueError(
            f'label_points: 長さが違う（x={len(xs)}, y={len(ys)}, '
            f'名前={len(texts)}）。座標と名前を同じ添字で絞り込むこと。'
            'たとえば P[pick,0] と組むのは names[pick] であって names ではない。')
    fig = ax.figure
    fig.canvas.draw()
    ren = fig.canvas.get_renderer()
    axbb = ax.get_window_extent(renderer=ren)

    def pad_box(b, w=2.0, h=1.5):
        """注記の矩形に**絶対量の余白**を足す。

        倍率（``expanded(1.08, …)``）では足りない。1桁の数字は幅 8px ほど
        なので 8% は 0.6px にしかならず，隣り合う注記が触れるほど近くても
        「重なっていない」と判定される。**``21`` が「21」と読める**のは
        これが原因である。文字の大小によらず一定の余白を確保する。
        """
        return Bbox.from_extents(b.x0 - w, b.y0 - h, b.x1 + w, b.y1 + h)

    def bb_of(ann):
        # Annotation 自身の get_window_extent を使うこと。
        # Text.get_window_extent(ann, ...) を呼ぶと xy の位置が無視され，
        # xytext のオフセットを絶対座標と見た矩形が返って判定が壊れる。
        return ann.get_window_extent(renderer=ren)

    blocked = []
    for coll in ax.collections:
        try:
            for p in coll.get_offsets():
                px, py = ax.transData.transform(p)
                blocked.append(Bbox.from_bounds(px - pad, py - pad,
                                                2 * pad, 2 * pad))
        except Exception:                                    # noqa: BLE001
            pass

    # **まっすぐ真上・真下を先に試す。** 点の直上に中央揃えで置ければ，
    # それがいちばん素直で，引き出し線も要らない。横へずらすのは，
    # 直上が塞がっていたときの次善である。
    #
    # 横へずらす輪は 12 ポイントから始める。**線が線として見える長さを
    # 確保する**ため。8 ポイントに置くと引き出し線が3ピクセルの点にしか
    # ならず，汚れと区別がつかない。
    # **真上に置けなければ，まず真上へ逃がす。** 横へ逃がすと注記の左右の
    # 順序が点の順序と入れ替わり，引き出し線も交差する。真上に段を重ねる
    # 限り，x は動かないので順序は必ず保たれる。横へずらすのは最後。
    # **横のずらし幅は小さく取る。** 横へ 30 ポイントも動かすと，注記が
    # 隣の点の真上に乗り，引き出し線で結んでも読みにくい。真上に段を
    # 重ねるほうが先で（x が動かないので順序が保たれる），横は 8→18
    # ポイントの範囲に収める。
    CAND = [(0, 9), (0, -11), (0, 20), (0, -22), (0, 31), (0, -33),
            (8, 5), (-8, 5), (8, -12), (-8, -12),
            (11, 0), (-11, 0),
            (13, 9), (-13, 9), (13, -16), (-13, -16),
            (18, 0), (-18, 0), (18, 14), (-18, 14),
            (0, 42), (0, -44)]

    def _ha(dx):
        # dx が 0 なら**中央揃え**。ここを 'left' にすると，真上に置いた
        # つもりの注記が文字幅の半分だけ右にずれ，隣の点の上に乗る。
        return 'center' if dx == 0 else ('left' if dx > 0 else 'right')
    placed, chosen, skipped = [], [], 0
    for x, y, t in zip(xs, ys, texts):
        # **自分が指している点は避けない。** 除かないと，注記は必ず
        # 自分の点の隣に来るので全部「重なる」と判定され，1つも置けない。
        ox, oy = ax.transData.transform((x, y))
        near = [b for b in blocked
                if not (abs((b.x0 + b.x1) / 2 - ox) < 1
                        and abs((b.y0 + b.y1) / 2 - oy) < 1)]
        for dx, dy in CAND:
            ann = ax.annotate(str(t), (x, y), textcoords='offset points',
                              xytext=(dx, dy), fontsize=fontsize, color=color,
                              ha=_ha(dx),
                              va='bottom' if dy >= 0 else 'top', zorder=6)
            bb = pad_box(bb_of(ann))
            inside = (bb.x0 >= axbb.x0 and bb.x1 <= axbb.x1
                      and bb.y0 >= axbb.y0 and bb.y1 <= axbb.y1)
            if inside and not any(bb.overlaps(b) for b in placed + near):
                placed.append(bb)
                chosen.append((ann, x, y, str(t), dx, dy))
                break
            ann.remove()
        else:
            skipped += 1

    # ---- 交差をほどく ----------------------------------------------------
    # **引き出し線が交差すると，注記の左右の順序が点の順序と入れ替わる。**
    # 文語標識の上位のように順位そのものが意味を持つ図では，2 と 3 が
    # 入れ替わって並ぶだけで読み違えられる。交差している2件を見つけ，
    # **位置を入れ替えて交差が解ければ入れ替える**（2-opt）。
    def _cross(p, q, r, s):
        def o(a, b, c):
            return ((b[0] - a[0]) * (c[1] - a[1])
                    - (b[1] - a[1]) * (c[0] - a[0]))
        return (((o(r, s, p) > 0) != (o(r, s, q) > 0))
                and ((o(p, q, r) > 0) != (o(p, q, s) > 0)))

    kpt = fig.dpi / 72.0

    def _seg(i):
        ann, x, y, t, dx, dy = chosen[i]
        ox, oy = ax.transData.transform((x, y))
        return (ox, oy), (ox + dx * kpt, oy + dy * kpt)

    def _set_off(i, dx, dy):
        ann, x, y, t, _, _ = chosen[i]
        ann.set_position((dx, dy))
        ann.set_ha(_ha(dx))
        ann.set_va('bottom' if dy >= 0 else 'top')
        chosen[i] = (ann, x, y, t, dx, dy)

    def _fits(i, bb):
        ox, oy = ax.transData.transform((chosen[i][1], chosen[i][2]))
        if not (bb.x0 >= axbb.x0 and bb.x1 <= axbb.x1
                and bb.y0 >= axbb.y0 and bb.y1 <= axbb.y1):
            return False
        return not any(bb.overlaps(b) for b in blocked
                       if abs((b.x0 + b.x1) / 2 - ox) > 1
                       or abs((b.y0 + b.y1) / 2 - oy) > 1)

    swaps = 0
    for _ in range(3):
        improved = False
        for i in range(len(chosen)):
            for j in range(i + 1, len(chosen)):
                if not _cross(*_seg(i), *_seg(j)):
                    continue
                di, dj = chosen[i][4:6], chosen[j][4:6]
                _set_off(i, *dj)
                _set_off(j, *di)
                bi = pad_box(bb_of(chosen[i][0]))
                bj = pad_box(bb_of(chosen[j][0]))
                others = [b for k2, b in enumerate(placed) if k2 not in (i, j)]
                good = (not bi.overlaps(bj)
                        and not any(bi.overlaps(b) or bj.overlaps(b)
                                    for b in others)
                        and _fits(i, bi) and _fits(j, bj)
                        and not _cross(*_seg(i), *_seg(j)))
                if good:
                    placed[i], placed[j] = bi, bj
                    swaps += 1
                    improved = True
                    continue
                _set_off(i, *di)
                _set_off(j, *dj)

                # 入れ替えが収まらないときは，**片方を別の候補位置へ動かす**。
                # 入れ替えは2つの箱の大きさが違うと失敗しやすい（数字1桁と
                # 作者名では幅が違う）。動かすほうは箱の大きさが変わらない。
                moved = False
                for who, other in ((i, j), (j, i)):
                    d0 = chosen[who][4:6]
                    for cx, cy in CAND:
                        if (cx, cy) == tuple(d0):
                            continue
                        _set_off(who, cx, cy)
                        bw = pad_box(bb_of(chosen[who][0]))
                        rest = [b for k2, b in enumerate(placed) if k2 != who]
                        if (_fits(who, bw)
                                and not any(bw.overlaps(b) for b in rest)
                                and not _cross(*_seg(who), *_seg(other))
                                and not any(_cross(*_seg(who), *_seg(k2))
                                            for k2 in range(len(chosen))
                                            if k2 != who)):
                            placed[who] = bw
                            swaps += 1
                            moved = improved = True
                            break
                        _set_off(who, *d0)
                    if moved:
                        break
        if not improved:
            break

    # ---- 引き出し線 ------------------------------------------------------
    # **線は配置が全部決まってから付ける。** arrowprops を付けた
    # Annotation の get_window_extent は「文字＋線」の外接矩形を返すので，
    # 配置の判定に使うと自分の点と必ず重なり，1件も置けなくなる。
    n_leader = 0
    if leader in ('line', 'arrow'):
        style = '-' if leader == 'line' else '-|>'
        for (ann, x, y, t, dx, dy), bb in zip(chosen, placed):
            ox, oy = ax.transData.transform((x, y))

            def dist_to_box(px, py, b=bb):
                # 文字の矩形から点までの距離。矩形の中なら 0。
                ddx = max(b.x0 - px, 0, px - b.x1)
                ddy = max(b.y0 - py, 0, py - b.y1)
                return (ddx * ddx + ddy * ddy) ** .5

            # **素直に置けたものには線を引かない。** 点の直上（または直下）に
            # 中央揃えで載っていて，しかもその注記にいちばん近い点が自分の
            # 点であれば，どの点の名前かは見れば分かる。線はかえって邪魔
            # である。横へ逃がしたものだけを結ぶ。
            if dx == 0 and abs(dy) <= 12:
                continue            # 点の真上・真下の一段目 → 線は要らない
            # それ以外は結ぶ。**段を上げたものも結ぶ。** 一段上げた注記の
            # 真下には別の点の注記が入るので，どちらの点のものか分からなく
            # なる。横へずらしたものは言うまでもない。
            d_other = min(
                (dist_to_box((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2)
                 for b in blocked
                 if abs((b.x0 + b.x1) / 2 - ox) > 1
                 or abs((b.y0 + b.y1) / 2 - oy) > 1),
                default=float('inf'))
            if (dx * dx + dy * dy) ** .5 < leader_min and d_other >= crowd_r:
                continue
            ann.remove()
            ax.annotate(t, (x, y), textcoords='offset points',
                        xytext=(dx, dy), fontsize=fontsize, color=color,
                        ha=_ha(dx),
                        va='bottom' if dy >= 0 else 'top', zorder=6,
                        arrowprops=dict(arrowstyle=style, linewidth=.55,
                                        color=leader_color, alpha=.9,
                                        shrinkA=1.5, shrinkB=2.5,
                                        mutation_scale=7))
            n_leader += 1

    # ---- 誤読の自己点検 --------------------------------------------------
    # **注記の最寄りの点が自分の点でないものを数える。** これが
    # 「ラベルとデータ点がずれて見える」の正体である。引き出し線を
    # 引いてあれば誤読にはならないが，線を切った設定では危険なので，
    # そのときだけ警告を出す。
    risky = []
    for (ann, x, y, t, dx, dy), bb in zip(chosen, placed):
        ox, oy = ax.transData.transform((x, y))
        cx, cy = (bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2
        d_own = ((cx - ox) ** 2 + (cy - oy) ** 2) ** .5
        d_other = min(
            (((b.x0 + b.x1) / 2 - cx) ** 2 + ((b.y0 + b.y1) / 2 - cy) ** 2) ** .5
            for b in blocked
            if abs((b.x0 + b.x1) / 2 - ox) > 1 or abs((b.y0 + b.y1) / 2 - oy) > 1
        ) if len(blocked) > 1 else float('inf')
        if d_other < d_own:
            risky.append(t)
    left = sum(1 for i in range(len(chosen)) for j in range(i + 1, len(chosen))
               if _cross(*_seg(i), *_seg(j)))
    if skipped:
        print(f'[fig] 重なるため {skipped} 件の注記を省いた（表で引くこと）')
    if left:
        print(f'[warn] 引き出し線の交差が {left} 件ほどけなかった。'
              '注記の左右の順序が点の順序と食い違う。'
              '注記を短くするか，件数を減らすこと。')
    if risky:
        head = '，'.join(str(r) for r in risky[:6])
        more = f' ほか{len(risky) - 6}件' if len(risky) > 6 else ''
        if leader in ('line', 'arrow'):
            print(f'[fig] {len(risky)} 件の注記は別の点のほうが近い'
                  f'（{head}{more}）。引き出し線で結んであるので読み違えない。')
        else:
            print(f'[warn] {len(risky)} 件の注記は**別の点のほうが近い**'
                  f'（{head}{more}）。leader="none" では読み違えが起きる。')
    return len(placed)
def _proj_versions():
    """射影に関わる版を並べる（うまくいかないときの手がかり）。"""
    import importlib
    out = []
    for nm in ['numpy', 'numba', 'llvmlite', 'pynndescent', 'sklearn']:
        try:
            out.append(f'{nm} ' + str(getattr(importlib.import_module(nm),
                                              '__version__', '?')))
        except Exception:                                    # noqa: BLE001
            out.append(f'{nm} ×')
    return '／'.join(out)

def umap_diagnosis(e):
    """UMAP が使えないときに，**何をすればよいか**を出す。"""
    import sys
    print(f'[NG  ] UMAP が使えない: {type(e).__name__}: {e}')
    print(f'       このカーネルの Python = {sys.executable}')
    print(f'       {_proj_versions()}')
    if isinstance(e, ModuleNotFoundError):
        # **入れた先とカーネルの環境が違う**のが圧倒的に多い。
        # uv add は「プロジェクト（pyproject.toml のある場所）」単位なので，
        # dh_project/pyproject.toml が無い，または dh_project の外に clone
        # した場合は，uv は別のプロジェクトに入れる。カーネルの .venv には入らない。
        print('       **この環境には入っていない。** 入れた先が違う可能性が高い')
        print('       （uv add はプロジェクト単位。~/Documents/dh_project に')
        print('        pyproject.toml が無いと，別のプロジェクトに入る）。')
        print('       この環境を名指しして入れるのが確実:')
        import platform as _pf
        if sys.platform == 'darwin' and _pf.machine() == 'x86_64':
            # **Intel Mac は版を固定する。** llvmlite の x86_64 wheel は
            # 0.45.1 が最後で，0.46 以降は arm64 のみ。固定しないと
            # ソースからのビルドに落ち，Homebrew の LLVM と版が合わずに
            # 失敗する（llvmlite 0.49 は LLVM 22 を要求）。
            print('       （Intel Mac なので**版を固定する**。'
                  'llvmlite の x86_64 wheel は 0.45.1 が最後）')
            print(f'         uv pip install --python "{sys.executable}" \\')
            print('             --only-binary :all: \\')
            print('             "numba==0.62.1" "llvmlite==0.45.1" '
                  '"numpy<2.4" umap-learn')
        else:
            print(f'         uv pip install --python "{sys.executable}" umap-learn')
        print('       入れたら**カーネルを再起動**して，このセルから実行し直す。')
    else:
        print('       import は通るが使えない型の失敗である'
              '（別パッケージの umap／numba と numpy の版違い／'
              'numba のキャッシュ）。')
    print('       切り分けの全項目:')
    print('         import sys, subprocess; print(subprocess.run('
          '[sys.executable,')
    print("             str(ROOT/'scripts'/'check_umap.py')], "
          'capture_output=True,')
    print('             text=True).stdout)')

def project(Xn, how='umap', seed=20260920, n_neighbors=15, min_dist=0.12,
            perplexity=30):
    """高次元の行列を2次元に落とす。**どの方法で落としたかを必ず返す。**

    ``how`` は ``'umap'``／``'tsne'``／``'auto'``。既定の ``'umap'`` は，
    使えなければ**止まって理由を出す**。``'auto'`` のときだけ t-SNE に落ちる。
    **黙って別の方法に替えないのが肝心である**（図は出るが塊の見え方は
    変わるので，環境の問題を分析結果と読み違える）。

    Step 5 の §3（主成分分析との比較）と §4（ギャラクシー）が共有する。
    """
    import importlib
    Xn = np.asarray(Xn, dtype=np.float32)
    if how in ('auto', 'umap'):
        try:
            m = importlib.import_module('umap')
            if not hasattr(m, 'UMAP'):
                # PyPI には umap（別物）と umap-learn（本物）がある。
                # pip install umap をしていると import umap はそちらを拾う。
                raise ImportError(
                    f'umap に UMAP クラスが無い（{getattr(m, "__file__", "?")}）。'
                    '別パッケージの umap が入っている。'
                    'umap を外して umap-learn を入れること')
            P = m.UMAP(n_neighbors=n_neighbors, min_dist=min_dist,
                       metric='cosine', random_state=seed).fit_transform(Xn)
            return (np.asarray(P, dtype=np.float32),
                    f'UMAP {getattr(m, "__version__", "")}'
                    f' (n_neighbors={n_neighbors}, min_dist={min_dist}, cosine)')
        except Exception as e:                               # noqa: BLE001
            umap_diagnosis(e)
            if how == 'umap':
                # **黙って別の方法に替えない。** どうしても t-SNE で
                # 進めたいときは 'tsne' と明示し，報告にもそう書くこと。
                raise
            print('[warn] how="auto" なので t-SNE に切り替える。'
                  '**図と報告に t-SNE と書くこと。**')
    from sklearn.manifold import TSNE
    P = TSNE(n_components=2, perplexity=perplexity, metric='cosine',
             init='pca', random_state=seed).fit_transform(Xn)
    return (np.asarray(P, dtype=np.float32),
            f't-SNE (perplexity={perplexity}, cosine)')


def proj_quality(Xn, P, k=10):
    """射影がどれだけ嘘をついているかを3つの数で返す。

    ``trust``  … 2次元で近く見える点が原空間でも近いか（局所・1が最良）
    ``keep``   … 原空間の上位 k 近傍のうち画面でも上位 k に入る語数
    ``rho``    … 原空間の距離と画面の距離の順位相関（**大域**の保存）

    局所（trust・keep）と大域（rho）は別物である。**UMAP は局所に強く，
    主成分分析は大域に強い**——これを目で見ずに数で確かめるための関数。
    """
    from scipy.spatial.distance import pdist
    from scipy.stats import spearmanr
    from sklearn.manifold import trustworthiness
    Xn, P = np.asarray(Xn, np.float32), np.asarray(P, np.float32)
    S = Xn @ Xn.T
    np.fill_diagonal(S, -np.inf)
    nn_t = np.argsort(-S, axis=1)[:, :k]
    d2 = ((P[:, None, :] - P[None, :, :]) ** 2).sum(-1)
    np.fill_diagonal(d2, np.inf)
    nn_p = np.argsort(d2, axis=1)[:, :k]
    keep = np.array([len(set(a) & set(b)) for a, b in zip(nn_t, nn_p)])
    trust = float(trustworthiness(Xn, P, n_neighbors=k, metric='cosine'))
    rho = float(spearmanr(pdist(Xn, 'cosine'), pdist(P))[0])
    return {'trust': trust, 'keep': keep, 'rho': rho}


def reserve_right(fig, frac=0.80):
    """面の外に凡例を置いた図で，**右に余白を確保する**。

    ``tight_layout()`` は面の外に置いた凡例を数えないので，そのままだと
    凡例が図の枠から出る。静止版は ``bbox_inches='tight'`` で救われるが，
    **HTML に埋め込む版は切り取らない**（切り取ると点の位置の割合が
    ずれる）ので，凡例が切れて読めなくなる。実際に切れた。

    ``tight_layout()`` の**後**，注記（``label_points``）の**前**に呼ぶ。
    """
    fig.subplots_adjust(right=frac)


def save_fig(fig, stem, out=None):
    """図を SVG で保存してパスを表示する。

    stem は拡張子なしの名前（例 'Step1_period_balance'）。
    点が数千個ある散布図は，散布図だけ rasterized=True にしておくと
    軸と文字はベクタのままファイルが軽くなる。
    """
    d = Path(out) if out else OUT
    d.mkdir(parents=True, exist_ok=True)
    path = d / f'{stem}.{FIG_EXT}'
    fig.savefig(path, format=FIG_EXT, dpi=RASTER_DPI, bbox_inches='tight')
    print(f'[fig] {path}  ({path.stat().st_size/1024:,.0f} KB)')
    return path


# ---------------------------------------------------------------------------
# 対話的な図（SVG はそのまま残す）
# ---------------------------------------------------------------------------
# 散布図の点が何百個あると，注記を付けられるのはごく一部である。残りの点は
# 「どの語か」が分からないまま眺めることになる。かといって全点に名前を
# 付ければ図は読めない。
#
# そこで**同じ図から2つ出す**。
#   * ``<stem>.svg``  … 論文・配布用。これまでどおり。加筆も拡大も自由
#   * ``<stem>.html`` … 授業・探索用。SVG をそのまま埋め込み，
#                       その上に当たり判定を重ねて，指した点の語を出す
#
# **HTML は SVG を作り直さない。同じ SVG を中に入れる。** 別に描き直すと
# 図が2種類できて，どちらが正かが分からなくなる。注記（bursty な語の
# ラベル）も SVG の中にあるのでそのまま残る。
#
# 外部の JS ライブラリは使わない。CDN が塞がれたマシンでも開けるようにする。
INTERACTIVE_CSS = """
:root { --ink:#1f1e1b; --ink2:#5a5a55; --line:#d8d7d0; --surface:#ffffff;
        --wash:#f7f7f4; --accent:#184f95; }
* { box-sizing:border-box; }
body { margin:0; padding:24px 16px 48px; background:var(--wash);
       color:var(--ink); font-family:"Hiragino Sans","Noto Sans JP",
       "Yu Gothic",system-ui,sans-serif; line-height:1.6; }
.wrap { max-width:1100px; margin:0 auto; }
h1 { font-size:1.15rem; margin:0 0 .2em; font-weight:650; }
.sub { color:var(--ink2); font-size:.86rem; margin:0 0 1.1em; }
.card { background:var(--surface); border:1px solid var(--line);
        border-radius:10px; padding:14px; }
.figbox { position:relative; }
.figbox svg { width:100%; height:auto; display:block; }
#hit { position:absolute; inset:0; cursor:crosshair; }
#ring { position:absolute; width:22px; height:22px; margin:-11px 0 0 -11px;
        border:2px solid var(--accent); border-radius:50%;
        pointer-events:none; opacity:0; transition:opacity .08s; }
#tip { position:absolute; z-index:5; min-width:190px; max-width:290px;
       background:var(--surface); border:1px solid var(--line);
       border-radius:8px; box-shadow:0 6px 20px rgba(0,0,0,.13);
       padding:9px 11px; font-size:.8rem; pointer-events:none; opacity:0;
       transition:opacity .08s; }
#tip .term { font-size:1.05rem; font-weight:650; letter-spacing:.02em;
             margin-bottom:.35em; word-break:break-all; }
#tip dl { display:grid; grid-template-columns:auto 1fr; gap:1px 10px;
          margin:0; }
#tip dt { color:var(--ink2); font-size:.74rem; white-space:nowrap; }
#tip dd { margin:0; text-align:right; font-variant-numeric:tabular-nums;
          font-weight:600; }
.bar { display:flex; gap:10px; align-items:center; flex-wrap:wrap;
       margin:14px 0 0; font-size:.82rem; color:var(--ink2); }
.bar input { font:inherit; padding:5px 9px; border:1px solid var(--line);
             border-radius:6px; min-width:190px; background:var(--surface); }
.bar a { color:var(--accent); }
table { border-collapse:collapse; width:100%; font-size:.78rem;
        margin-top:10px; }
th,td { padding:4px 8px; border-bottom:1px solid #ecebe6; text-align:left;
        white-space:nowrap; }
th { background:var(--wash); position:sticky; top:0; font-weight:650; }
td.num { text-align:right; font-variant-numeric:tabular-nums; }
tbody tr:hover td { background:var(--wash); }
tbody tr.on td { background:#eaf1fb; }
.scroll { max-height:340px; overflow:auto; border:1px solid var(--line);
          border-radius:8px; margin-top:10px; }
.hint { font-size:.78rem; color:var(--ink2); margin:.6em 0 0; }
#links { position:absolute; inset:0; pointer-events:none; overflow:visible; }
#links line { stroke:var(--accent); stroke-width:1.1; opacity:.55; }
#links circle { fill:none; stroke:var(--accent); stroke-width:1.4; opacity:.8; }
#marks { position:absolute; inset:0; pointer-events:none; overflow:visible; }
#marks circle { fill:none; stroke:#d55e00; stroke-width:1.6; opacity:.9; }
#tip .notes { margin:.45em 0 0; font-size:.76rem; color:var(--ink);
              border-top:1px solid var(--line); padding-top:.4em;
              line-height:1.5; word-break:break-all; }
#tip .notes b { color:var(--ink2); font-weight:600; }
.prov { font-size:.72rem; color:var(--ink2); margin:.9em 0 0;
        border-top:1px solid var(--line); padding-top:.6em;
        font-variant-numeric:tabular-nums; }
.danger { background:#fdf0ea; border:1px solid #e8a37c; border-radius:8px;
          padding:9px 12px; font-size:.85rem; color:#8a3b10;
          margin:0 0 12px; }
"""

INTERACTIVE_JS = r"""
// 点は data-* ではなく JSON で渡す。語はコーパス由来の任意の文字列なので，
// **HTML に文字列連結で差し込まない**（textContent で入れる）。
// 見出し（keys・nhead）は全点で同じなら1回だけ入っている。点が1万個ある
// 図では，これで HTML が 1 MB 以上軽くなる。古い形（配列だけ）も読む。
const RAW = JSON.parse(document.getElementById('pts-data').textContent);
const PTS = Array.isArray(RAW) ? RAW : RAW.pts;
const KEYS = (RAW && RAW.keys) || [];
const NHEAD = (RAW && RAW.nhead) || '';
const LINKNOTES = !!(RAW && RAW.linknotes);
function pairsOf(p) {
  if (p.fields) return p.fields;
  if (p.v) return p.v.map((x, i) => [KEYS[i] || '', x]);
  return [];
}
function notesOf(p) {
  if (p.notes) return p.notes;
  if (p.n) return [NHEAD, p.n];
  // 本文が無く linknotes が立っているときは，線で結ぶ先の語を並べる
  if (LINKNOTES && p.links && p.links.length) {
    return [NHEAD, p.links.map(j => (PTS[j] || {}).term || '').join(' ')];
  }
  return null;
}
const box = document.getElementById('hit');
const tip = document.getElementById('tip');
const ring = document.getElementById('ring');
const rows = Array.from(document.querySelectorAll('tbody tr'));
const links = document.getElementById('links');
const marks = document.getElementById('marks');

// **最も近い点を拾う。** 点の直径は数ピクセルしかないので，
// 「真上に置く」ことを要求すると誰も当てられない（dataviz の規則）。
// カーソルに最も近い点を選び，遠すぎるときだけ何も出さない。
function nearest(px, py, w, h) {
  let best = null, bd = 1e9;
  for (const p of PTS) {
    const dx = p.x * w - px, dy = p.y * h - py;
    const d = dx * dx + dy * dy;
    if (d < bd) { bd = d; best = p; }
  }
  return Math.sqrt(bd) <= 34 ? best : null;   // 34px より遠ければ出さない
}

function fill(p) {
  tip.textContent = '';
  const h = document.createElement('div');
  h.className = 'term';
  h.textContent = p.term;                     // ← 連結しない
  tip.appendChild(h);
  const dl = document.createElement('dl');
  for (const [k, v] of pairsOf(p)) {
    const dt = document.createElement('dt'); dt.textContent = k;
    const dd = document.createElement('dd'); dd.textContent = v;
    dl.appendChild(dt); dl.appendChild(dd);
  }
  tip.appendChild(dl);
  const nt = notesOf(p);
  if (nt) {                                   // 近傍語など，横に長い情報
    const n = document.createElement('p');
    n.className = 'notes';
    const b = document.createElement('b');
    b.textContent = nt[0] + ' ';
    n.appendChild(b);
    n.appendChild(document.createTextNode(nt[1]));
    tip.appendChild(n);
  }
}

// **原空間での近傍を線で結ぶ。** 画面の近さは射影の結果にすぎない。
// 線が遠くへ伸びるなら，その点の近傍関係は2次元に収まっていない。
// これを見せるのが，この図でいちばん大事なところである。
function drawLinks(p, w, h) {
  if (!links) return;
  while (links.firstChild) links.removeChild(links.firstChild);
  if (!p.links || !p.links.length) return;
  const NS = 'http://www.w3.org/2000/svg';
  for (const j of p.links) {
    const q = PTS[j];
    if (!q) continue;
    const ln = document.createElementNS(NS, 'line');
    ln.setAttribute('x1', p.x * w); ln.setAttribute('y1', p.y * h);
    ln.setAttribute('x2', q.x * w); ln.setAttribute('y2', q.y * h);
    links.appendChild(ln);
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('cx', q.x * w); c.setAttribute('cy', q.y * h);
    c.setAttribute('r', 5);
    links.appendChild(c);
  }
}

let cur = null;
function show(p, px, py) {
  const w = box.clientWidth, h = box.clientHeight;
  if (p !== cur) { fill(p); drawLinks(p, w, h); cur = p; }
  ring.style.left = (p.x * w) + 'px';
  ring.style.top = (p.y * h) + 'px';
  ring.style.opacity = 1;
  tip.style.opacity = 1;
  // はみ出さないように寄せる
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let lx = px + 16, ly = py + 14;
  if (lx + tw > w) lx = px - tw - 16;
  if (ly + th > h) ly = py - th - 14;
  tip.style.left = Math.max(0, lx) + 'px';
  tip.style.top = Math.max(0, ly) + 'px';
  rows.forEach(r => r.classList.toggle('on', r.dataset.i === String(p.r)));
}
function hide() {
  tip.style.opacity = 0; ring.style.opacity = 0; cur = null;
  if (links) while (links.firstChild) links.removeChild(links.firstChild);
  rows.forEach(r => r.classList.remove('on'));
}

box.addEventListener('pointermove', e => {
  const r = box.getBoundingClientRect();
  const p = nearest(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
  if (p) show(p, e.clientX - r.left, e.clientY - r.top); else hide();
});
box.addEventListener('pointerleave', hide);

// 表の行にカーソルを乗せても，図の上の点が光る（逆引き）。
// **カーソルが使えない人にも同じ情報が届くように**，表を必ず添える
// （点が数千を超える図だけは表を絞る。絞ったことは図の下に明記する）。
rows.forEach(r => {
  r.addEventListener('mouseenter', () => {
    // 表の行は論理点。図の上では**先頭の面**の点を光らせる
    const p = PTS[Number(r.dataset.i)];
    if (!p) return;
    const w = box.clientWidth, h = box.clientHeight;
    show(p, p.x * w, p.y * h);
  });
  r.addEventListener('mouseleave', hide);
});

// 絞り込み。語・作品・時代のどれでも当たる
const q = document.getElementById('q');
if (q) q.addEventListener('input', () => {
  const s = q.value.trim();
  let n = 0;
  rows.forEach(r => {
    const hit = !s || r.textContent.includes(s);
    r.style.display = hit ? '' : 'none';
    if (hit) n++;
  });
  // 表を絞った図では，**表に無い語も図の上では当たる**。
  // 表の件数だけを出すと「無い」と誤解されるので両方を出す。
  const nlog = Number(document.body.dataset.nlog || rows.length);
  let extra = '';
  if (s && rows.length < nlog) {
    const seen = new Set();
    for (const p of PTS) if (p.term.includes(s)) seen.add(p.r);
    extra = '（図の上 ' + seen.size + ' 件）';
  }
  document.getElementById('count').textContent = n + ' 件' + extra;
  // **図の上にもマーカーを付ける。** 表だけ絞っても「どこにあるか」は分からない。
  if (!marks) return;
  while (marks.firstChild) marks.removeChild(marks.firstChild);
  if (!s) return;
  const NS = 'http://www.w3.org/2000/svg';
  const w = box.clientWidth, h = box.clientHeight;
  let drawn = 0;
  for (const p of PTS) {
    if (!p.term.includes(s)) continue;
    const c = document.createElementNS(NS, 'circle');
    c.setAttribute('cx', p.x * w); c.setAttribute('cy', p.y * h);
    c.setAttribute('r', 7);
    marks.appendChild(c);
    if (++drawn > 400) break;          // マーカーが多すぎると図が読めない
  }
});
"""


def save_interactive(fig, ax, stem, xs, ys, tips, out=None, title='',
                     note='', table_cols=None, source=None, id_col='語',
                     hint='', table_idx=None, coords=None):
    """SVG を保存し，**同じ SVG を埋め込んだ対話的な HTML** も書く。

    ``xs`` ``ys`` はデータ座標，``tips`` は点ごとの情報
    （``{'term': 語, 'fields': [(見出し, 値), …]}`` の並び）。
    3つの長さは一致していなければならない。ずれたまま描くと，
    **指した点と出る語が食い違う**（注記の添字ずれと同じ事故）。

    位置は「図全体に対する割合」で書き出す。SVG を ``width:100%`` で
    伸縮させても割合は変わらないので，どんな幅でも点と当たり判定が
    合う。座標は matplotlib の変換を通して得るので，**図と HTML で
    座標の計算が二重にならない**。

    ``source`` に入力ファイルのパスを渡すこと。**どの表から描いた図かを
    HTML の末尾に刻む。** これが無いと，試験用の作りかけのデータから
    描いた図と，本番のデータから描いた図が見分けられない。
    入力がプロジェクトの外（``/tmp`` など）にあるときは
    「試験用」と赤字で出し，配布してはいけないことを図自身に言わせる。

    ``id_col`` は表の第1列の見出し（既定「語」。作品を点にする図では
    「作品」などに変える）。

    ``ax`` には**面の並び**も渡せる（``[axes[0], axes[1]]``）。同じ点を
    別の色分けで2面に描いた図では，どちらの面を指しても同じ情報が出る。
    表の行は点ごとに1行だけ作る（面の数だけ重複させない）。

    ``table_idx`` は**表に載せる点の添字**（既定は全点）。点が数千を超える
    図では表を全件出すと HTML が数 MB になり，読む側にも役に立たない。
    そのときは載せる点を選ぶ。**ただし図の当たり判定と検索は全点に効く**
    ので，表に無い語も指せるし検索で図にマーカーが付く。表を絞ったときは，
    何件のうち何件を載せたかを HTML に明記する（黙って捨てないこと）。

    ``coords`` は**面ごとの座標**（``[(x1, y1), (x2, y2)]``）。同じ点を
    **違う座標系**で2面に描いた図（主成分分析と UMAP の比較など）で使う。
    渡さなければ全部の面で ``xs`` ``ys`` を使う。
    ⚠ 面ごとに座標が違うのに ``coords`` を渡さないと，2面めの当たり判定が
    1面めの座標で置かれる。**図は出るが，指した点と出る語が食い違う。**
    """
    import json as _json
    if not (len(xs) == len(ys) == len(tips)):
        raise ValueError(
            f'save_interactive: 長さが違う（x={len(xs)}, y={len(ys)}, '
            f'情報={len(tips)}）。座標と情報を同じ添字で絞り込むこと。')

    svg_path = save_fig(fig, stem, out=out)
    outdir = svg_path.parent

    # ---- 埋め込む SVG は**切り取らずに**保存する ------------------------
    # save_fig は bbox_inches='tight' で余白を詰めるため，図全体に対する
    # 割合と，ファイルの座標系がずれる。埋め込み用は詰めずに出す。
    import io
    buf = io.StringIO()
    fig.savefig(buf, format='svg', dpi=RASTER_DPI)
    svg = buf.getvalue()
    svg = svg[svg.index('<svg'):]          # XML 宣言と DOCTYPE を落とす

    # ---- 点の位置を図全体に対する割合で得る -----------------------------
    axes_list = list(ax) if isinstance(ax, (list, tuple, np.ndarray)) else [ax]
    W, H = fig.bbox.width, fig.bbox.height
    n_pts = len(tips)

    # **見出しは点ごとに書かない。** 点が1万個ある図では，
    # 「品詞」「頻度」…という見出しを1万回繰り返すだけで HTML が
    # 1 MB 以上ふくらむ。全点で見出しが同じなら1回だけ書き，
    # 値の並びだけを点に持たせる（JS 側で組み直す）。
    keys = [str(k) for k, _ in tips[0].get('fields', [])] if tips else []
    same_keys = bool(keys) and all(
        [str(k) for k, _ in t.get('fields', [])] == keys for t in tips)
    nheads = {str(t['notes'][0]) for t in tips if t.get('notes')}
    nhead = next(iter(nheads)) if len(nheads) == 1 else ''
    # 本文を渡さず ``notes=(見出し, None)`` としたときは，
    # ``links`` の先の語を JS 側で並べる
    linknotes = bool(nhead) and any(
        t.get('notes') and t['notes'][1] is None and t.get('links')
        for t in tips)

    pts = []
    if coords is not None and len(coords) != len(axes_list):
        raise ValueError(
            f'save_interactive: coords の数が面の数と違う'
            f'（面 {len(axes_list)} / coords {len(coords)}）')
    for k, axk in enumerate(axes_list):
      xk, yk = (coords[k] if coords is not None else (xs, ys))
      if not (len(xk) == len(yk) == n_pts):
          raise ValueError(
              f'save_interactive: 面 {k} の座標の数が情報の数と違う'
              f'（x={len(xk)}, y={len(yk)}, 情報={n_pts}）')
      pxy = axk.transData.transform(np.column_stack([np.asarray(xk, float),
                                                     np.asarray(yk, float)]))
      for j, (t, (px, py)) in enumerate(zip(tips, pxy)):
        i = k * n_pts + j
        # **変数名に注意。** ここを d と書くと，上で取った出力先 d
        # （svg_path.parent）を上書きして，最後に d / '....html' が
        # 「dict ÷ str」になる。実際に踏んだ。名前は使い回さない。
        # 添字 i は JS では使わない（行は r で引く）。点が1万個ある図では
        # 使わない値も 100 KB 単位で効くので書かない。
        rec = {'r': j, 'term': str(t.get('term', '')),
               'x': round(float(px) / W, 6),
               'y': round(1 - float(py) / H, 6)}        # SVG は上が 0
        if same_keys:
            rec['v'] = [str(b) for _, b in t.get('fields', [])]
        else:
            rec['fields'] = [[str(a), str(b)] for a, b in t.get('fields', [])]
        if t.get('notes'):
            # ('見出し', '本文') の2つ組。横に長い情報（近傍語など）。
            # 本文を None にすると，**線で結ぶ先の語を JS が並べる**
            # （同じ語の列を点ごとに書かずに済む。1万点で 1 MB 近く効く）
            if t['notes'][1] is None:
                pass
            elif nhead:
                rec['n'] = str(t['notes'][1])
            else:
                rec['notes'] = [str(t['notes'][0]), str(t['notes'][1])]
        if t.get('links'):
            # 原空間での近傍の添字。**同じ面の中で**線を結ぶ
            rec['links'] = [k * n_pts + int(q) for q in t['links']]
        pts.append(rec)

    cols = table_cols or keys
    head = f'<tr><th>{_esc(id_col)}</th>' + ''.join(
        f'<th>{_esc(c)}</th>' for c in cols) + '</tr>'
    # 数字の列だけ右寄せにする。時代名や作品 ID を右寄せにすると読みにくい。
    def _numish(v):
        t = str(v).strip().replace('%', '').replace(',', '')
        t = t.lstrip('+-')
        return bool(t) and t.replace('.', '', 1).isdigit()

    # 表に載せる点。**面の数だけ重複させない**（論理点1つに1行）
    if table_idx is None:
        order = list(range(n_pts))
    else:
        seen, order = set(), []
        for i in [int(q) for q in table_idx]:   # 重複を除きつつ順序は保つ
            if 0 <= i < n_pts and i not in seen:
                seen.add(i); order.append(i)

    # 表は **tips から作る**（点の JSON は見出しを省いてあるので）
    body = []
    for j in order:
        fv = {str(a): str(b) for a, b in tips[j].get('fields', [])}
        tds = ''
        for c in cols:
            v = fv.get(c, '')
            cls = ' class="num"' if _numish(v) else ''
            tds += f'<td{cls}>{_esc(v)}</td>'
        body.append(f'<tr data-i="{j}">'
                    f'<td>{_esc(tips[j].get("term", ""))}</td>{tds}</tr>')

    # ---- 由来を図自身に刻む -------------------------------------------
    # **どの表から描いた図かが分からないと，試験用のデータで描いた図が
    # 本物として配られる。** 実際に起きた（2026-09-22）。
    import datetime as _dt
    stamp = _dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')
    src = Path(source) if source else None
    prov = f'点 {len(pts)} 個／作図 {stamp}'
    warn = ''
    if src is not None:
        try:
            mt = _dt.datetime.fromtimestamp(src.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
        except OSError:
            mt = '不明'
        prov = f'入力 {src.name}（更新 {mt}）／' + prov
        # プロジェクトの外（/tmp など）から描いた図は試験用である
        try:
            outside = not str(src.resolve()).startswith(str(ROOT.resolve()))
        except Exception:                               # noqa: BLE001
            outside = True
        if outside or '/tmp/' in str(src):
            warn = ('<p class="danger">⚠ <b>試験用の入力から作った図である。'
                    f'配布してはいけない。</b>（入力 {_esc(str(src))}）</p>')
            prov = f'入力 {_esc(str(src))}／' + f'点 {len(pts)} 個／作図 {stamp}'

    html = f"""<!DOCTYPE html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title or stem)}</title>
<style>{INTERACTIVE_CSS}</style></head>
<body data-nlog="{n_pts}" data-ntable="{len(body)}"><div class="wrap">
<h1>{_esc(title or stem)}</h1>
<p class="sub">{_rich(note)}</p>
{warn}
<div class="card">
  <div class="figbox">
    {svg}
    <svg id="links"></svg><svg id="marks"></svg>
    <div id="hit"></div><div id="ring"></div><div id="tip"></div>
  </div>
  <p class="hint">{_rich(hint or '点にカーソルを近づけると語が出る（最も近い点を拾うので，真上に置かなくてよい）。図の中の注記は静止版と同じものである。')}</p>
  <div class="bar">
    <input id="q" type="search" placeholder="語・作品・時代で絞り込む">
    <span id="count">{len(body)} 件</span>
    <span>·</span>
    {(f'<span>表は {len(body)} 件（図の点は {n_pts} 件。'
      '表に無い語も図の上で指せる。検索は図のマーカーにも効く）</span><span>·</span>')
     if len(body) < n_pts else ''}
    <a href="{_esc(svg_path.name)}" download>SVG を保存</a>
    <span>（この HTML の中の図はその SVG そのもの）</span>
  </div>
  <div class="scroll"><table><thead>{head}</thead>
    <tbody>{''.join(body)}</tbody></table></div>
  <p class="prov">{prov}</p>
</div>
<script type="application/json" id="pts-data">{_json.dumps(
    {'keys': keys if same_keys else [], 'nhead': nhead,
     'linknotes': linknotes, 'pts': pts},
    ensure_ascii=False, separators=(',', ':'))}</script>
<script>{INTERACTIVE_JS}</script>
</div></body></html>
"""
    path = outdir / f'{stem}.html'
    path.write_text(html, encoding='utf-8')
    print(f'[fig] {path}  ({path.stat().st_size/1024:,.0f} KB・対話版／'
          f'点 {len(pts)} 個)')
    return svg_path, path


def _esc(s):
    """HTML の特殊文字を落とす。**語はコーパス由来なので必ず通す。**"""
    return (str(s).replace('&', '&amp;').replace('<', '&lt;')
            .replace('>', '&gt;').replace('"', '&quot;'))


def _rich(s):
    """注記の ``**…**`` だけを太字にする。

    説明文をノートブックと同じ書き方（Markdown 風）で書けるようにする。
    **先に必ず _esc を通す**ので，タグを書き込まれる余地は無い。
    ``**`` のままだと HTML では記号がそのまま出て読みにくい。
    """
    import re as _re
    return _re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', _esc(s))


PALETTE = ['#0072B2', '#E69F00', '#009E73', '#CC79A7',
           '#56B4E9', '#D55E00', '#F0E442', '#666666']

# --- 順序のあるものを色分けするための1色相のランプ -----------------------------
# **時代・年次・段階のように順序のあるものを，上の8色で色分けしてはいけない。**
# 明治中期が青で明治後期が黄なら，隣り合う時代が隣り合う色にならず，
# 「時代が下るとどちらへ動くか」という肝心のことが読めなくなる。
# 1色相の濃淡にすれば，近いもの同士が近い色になり，勾配がそのまま見える。
# 散布図の点は白地の上に置くので，いちばん明るい段は 100 ではなく
# 250（背景との対比 2:1）から始める。100 は面を色分けするとき（ヒートマップ）用。
SEQ_BLUE_STEPS = ['#86b6ef', '#5598e7', '#3987e5',
                  '#256abf', '#184f95', '#0d366b']
SEQ_BLUE = LinearSegmentedColormap.from_list('jlit_blue', SEQ_BLUE_STEPS)

# 離散の順序（4区分など）を色分けするときはこちら。隣の段と明度差が十分あり，
# いちばん明るい段も背景から浮く（対比 2:1 以上）ことを確かめてある。
SEQ_BLUE_5 = ['#86b6ef', '#3987e5', '#256abf', '#184f95', '#0d366b']

# 大分類の2色。散布図ではどの2点も隣り合いうるので**全ペアが
# 見分けられる必要**があり，使える色数は多くない。2色に絞って，
# 下位の区別はマーカーの形に持たせる。
GENRE_C = {'Fiction': '#2a78d6', 'Nonfiction': '#eb6834'}

# --- 初出年の5段 ---------------------------------------------------------
# 切れ目は period と同じ 1900／1912／1926／1945。
# **6段にはできない。** 1色相の濃淡で順序を見せるには，隣り合う段の明度差が
# 0.06 以上要る。この青系ランプは 250→700 で明度差にして 0.30 ほどしか幅が
# 無いので，段を6つ取るとどこかが 0.05 台に落ち，隣が見分けられなくなる。
# そこで作品数3点の明治前期（〜1886）を明治中期にまとめて5段とする。
YEAR_EDGES = [1900, 1912, 1926, 1945]
YEAR_LABELS = ['〜1899 明治前・中期', '1900-1911 明治後期', '1912-1925 大正',
               '1926-1944 昭和戦前', '1945- 昭和戦後']


def year_bands(years):
    """初出年を5段に畳み，``(段番号, ラベル, 色)`` を返す。

    段番号は 0〜4。**初出年が読めないものは -1** にする。0 に落とすと
    年の分からない作品が全部いちばん古い段に入り，通時の議論が崩れる。

    時代で色分けする図はすべてこれを通すこと。同じ色が全ステップで同じ時代を
    指すようになり，Step 1 の図と Step 7 の図を並べて読める。
    """
    y = pd.to_numeric(pd.Series(list(years)), errors='coerce')
    code = np.full(len(y), -1, dtype=int)
    ok = y.notna().values
    if ok.any():
        code[ok] = np.digitize(y[ok].values, YEAR_EDGES)
    return code, YEAR_LABELS, SEQ_BLUE_5


def run_script(script, *args, tail=4000):
    """scripts/ のスクリプトを実行し，標準出力・標準エラー・終了コードを必ず表示する。

    print(r.stdout or r.stderr) では，標準出力が空でないときに
    エラーの内容が隠れてしまう。学習用には両方見えるほうがよい。
    """
    cmd = [sys.executable, str(ROOT / 'scripts' / script)] + [str(a) for a in args]
    print('$ python', ' '.join(cmd[1:]))
    r = subprocess.run(cmd, capture_output=True, text=True)

    def _tail(s):
        # 末尾 tail 文字だけを出す。行の途中で切らないよう，切ったときは
        # 次の改行から始め，前を省いたことを明示する
        if len(s) <= tail:
            return s
        s = s[-tail:]
        return '（…前略）\n' + s[s.find('\n') + 1:]

    if r.stdout:
        print(_tail(r.stdout))
    if r.stderr.strip():
        # 標準エラーには**エラー以外**も出る。MALLET は学習の進み具合
        # （<10> LL/token: …）と途中のトピック上位語をここに書く。
        # 判断は [exit 0] かどうかで行う
        print('--- stderr（進行ログを含む。エラーとは限らない）---')
        print(_tail(r.stderr))
    print(f'[exit {r.returncode}]' + ('' if r.returncode == 0 else '  ← 0 でなければ失敗'))
    return r


# 使うメタデータ。増補分（45点）を含む v3 があればそちらを優先する。
# v2 は v1 の 64 点しか無いので，増補後のコーパスで v2 を使うと
# 突合が外れて period も genre も空になる（Step 3 で v3 を作る）。
# Step 3 で自分が作った v3 は *_local.csv に書かれる（配布版は上書きしない。
# 上書きすると git pull のたびに衝突する）。自分の版 > 配布版 v3 > v2 の順。
for _m in ('corpus_metadata_v3_local.csv', 'corpus_metadata_v3.csv',
           'corpus_metadata_v2.csv'):
    META = ROOT / 'metadata' / _m
    if META.exists():
        break

# 図・表の書き出し先は自分の作業フォルダ my_work/results/。my_work/ は
# コースのリポジトリの外扱い（.gitignore）で，自分の GitHub にバックアップを取る。
OUT = ROOT / 'my_work' / 'results'
OUT.mkdir(parents=True, exist_ok=True)
print('OUT  =', OUT)
'''

# --------------------------------------------------------------------------
# 各ステップの定義。cells は ('md', text) / ('code', text) の列。
# --------------------------------------------------------------------------
LESSONS: list[dict] = []


def L(n, title, cells):
    LESSONS.append({'n': n, 'title': title, 'cells': cells})


# ==========================================================================
L(1, 'コーパスとは何か — 設計・代表性・v1の診断', [
 ('md', r'''# Step 1 コーパスとは何か — 設計・代表性・v1 の診断

> **「回」ではなく「Step」と呼ぶ理由**
>
> 本授業は8つの Step からなるが，これは8コマという意味ではない。環境構築・
> スクリプトの不具合・データの取り直しで必ず遅れが出るので，実質 12 コマ
> 程度を見込んでいる。Step は**カレンダー上の回ではなく到達点**である。
> 前の Step の成果物（XML，正規化テクスト，トークン列）ができていなければ
> 次へは進めない。逆に，できていれば何コマかかっても構わない。各 Step の
> 冒頭に到達目標を置いたのはそのためで，そこに書かれたことができているか
> どうかが，先へ進んでよいかどうかの判断基準になる。

## このステップの到達目標

1. 環境構築を完了し，`00_env_check.py` が `ALL OK` を返す
2. 「コーパス＝母集団からの標本」という見方を身につける
3. 既存コーパス（v1, 64点）の**偏り**を自分の手で数え，図にする
4. 3つの重大な欠陥（重複・外字欠落・奥付混入）を自分で再発見する

## 導入：なぜ「代表性」から始めるのか

近代日本文学の文体変化を量的に記述したい，とする。このとき私たちが本当に
測りたいのは **母集団**（1868–1960年に日本語で書かれた文学テクストの全体）の
性質である。しかし手元にあるのは **標本**（青空文庫にあり，著作権が切れ，
誰かが入力した64点）にすぎない。

標本が母集団を歪んだ形で代表していると，どんなに精緻なモデルを当てても
**その歪みを測ることになる**。

> 「昭和期の小説は語彙が平易になった」という結論が出たとする。
> しかしコーパスの昭和期7点が児童向け読物だったら？

本授業ではこの問いを最初に置く。パイプラインの前半 4 ステップはすべて，
**分析に耐える標本を作る**ための工程である。

## 参考

- Biber, D. (1993) Representativeness in corpus design. *LLC* 8(4).
- 田野村忠温 (2011)「コーパスとコーパス言語学」『日本語学』
- 前川喜久雄 (2013)『コーパス入門』（講座日本語コーパス1）朝倉書店
'''),
 ('code', PREAMBLE),
 ('md', r'''## 1. 環境チェック

まず全員が次のセルを実行する。`ALL OK` が出るまで先に進まない。
足りないものがあれば，表示された指示のとおりに導入する
（詳細は `docs/00_setup_students.md`）。

macOS なら，たいていの不足は次の1行で解決する。**`sudo` は要らない。**

```bash
bash scripts/00_bootstrap_mac.sh             # DH Lab 共用 iMac
bash scripts/00_bootstrap_mac.sh --personal  # 自分の Mac
```

リポジトリは **`~/Documents/dh_project/JLit_Corpus_2026`** に clone し，
仮想環境は **`~/Documents/dh_project/.venv`** に置く（スクリプトが作る）。
下のセルの出力で「リポジトリ構成」と「仮想環境」がこの場所を指しているか，
カーネルが **Python (JLit)** かを確かめること。別の場所（古いコピーなど）を
指していたら，Jupyter をリポジトリで起動し直す。

### 共用 iMac を使う人へ — 使用したマシンの番号（例：2021-09）を控えておくこと

DH Lab の iMac は XCreds 認証で，**ホームはマシンごとに別々**である。
ログインしたマシンのホームはそのまま保持されるが，マシンどうしでは共有されない。
火曜に 2021-03 番ラベルのマシンで作った仮想環境と成果物は，木曜に
2021-03 番なら残っているが，2021-05 番にログインしても無い。
故障ではなく，そういう仕組みである。

- 本体のラベル番号（例 2021-03）と，出力の先頭に出る**マシン名をレポートに控える**
- 自分の作業（`my_work/`）は毎回，自分の GitHub に push して持ち運ぶ（`docs/00_setup_students.md` §5）
- マシンを移ったら上のスクリプトを再実行する。JDK・UniDic などは
  `/Users/Shared/jlit` に残っているので，そのマシンで誰かが済ませていれば
  仮想環境を作るだけで終わる'''),
 ('code', r'''r = run_script('00_env_check.py')'''),
 ('md', r'''## 2. メタデータを読む

`metadata/corpus_metadata_v2.csv` は，v1 の `コーパスdescription.xlsx` を
青空文庫の図書カードに突き合わせて作り直したものである。

**v1 の何が問題だったか**（`changes_from_v1` シートに全件）:

| 列 | 問題 | 例 |
|---|---|---|
| `year` | 底本の刊年を初出年にしていた | 小栗虫太郎『潜航艇「鷹の城」』1977 → 実は1935 |
| `ndc` | NDC 番号ではなくラベル文字列 | 「小説、物語」→ 913 |
| `genre` | 英語・日本語・別題・誤綴の混在 | "Histrical novel", 「（二十世紀鉄仮面）」 |
| `comments` | 語り・形態・文体が無統制に混在 | 「現代、独白」「Collection」「Colloquial」 |
| `brow` | high/low の二値 | 児童書とノンフィクションが押し込まれていた |

**データを作る人は，列の意味を1行で説明できなければならない。**
説明できない列は，必ずあとで誤用される。'''),
 ('code', r'''# load_meta() は分析に使わない行（superseded / too_short）を落として読む。
# 生の表がほしいときは load_meta(analysis_only=False)。
meta = load_meta()
print('分析対象:', meta.shape)
meta[['id','author_ja','title_aozora','year_first','period','ndc',
      'genre_sub','narration','style_class','tokens']].head(12)'''),
 ('md', r'''## 3. 演習 1 — 偏りを数える

次のセルを実行し，**どの軸がいちばん偏っているか**を自分の言葉で述べよ。
`value_counts()` は比率も出せる（`normalize=True`）。'''),
 ('code', r'''COLS = ['period','style_class','kana_orthography','ndc',
        'genre_main','audience','register_level','narration','author_sex']

# まず**偏りの一覧**を1枚で見る。区分がいくつあり，最大の区分が
# 何割を占めるか。ここが 0.5 を超える項目は，その項目で群を比べると
# 片方がほとんど無い状態で比べることになる。
summ = []
for col in COLS:
    vc = meta[col].value_counts()
    summ.append({'項目': col, '区分数': len(vc),
                 '最大の区分': str(vc.index[0]) if len(vc) else '',
                 '最大の件数': int(vc.iloc[0]) if len(vc) else 0,
                 '最大の割合': float(vc.iloc[0]/vc.sum()) if len(vc) else np.nan,
                 '欠損': int(meta[col].isna().sum())})
show(pd.DataFrame(summ).sort_values('最大の割合', ascending=False),
     caption=f'メタデータの偏り（分析対象 {len(meta)} 点）',
     fmt={'最大の割合': '{:.1%}'})

# 続いて内訳。項目名は各ブロックの先頭行だけに出す（表として読みやすい）
rows = []
for col in COLS:
    vc = meta[col].value_counts()
    rt = meta[col].value_counts(normalize=True)
    for i, k in enumerate(vc.index):
        rows.append({'項目': col if i == 0 else '', '区分': str(k),
                     '作品数': int(vc[k]), '割合': float(rt[k])})
show(pd.DataFrame(rows), caption='各項目の内訳', fmt={'割合': '{:.1%}'})'''),
 ('code', r'''# 語数ベースでも見る。作品数と語数で印象が変わる軸はどれか。
g = (meta.groupby('period')
        .agg(works=('id','count'), tokens=('tokens','sum'))
        .assign(work_prop=lambda d: d.works/d.works.sum(),
                token_prop=lambda d: d.tokens/d.tokens.sum()))
show(g.reset_index().rename(columns={'period':'時代','works':'作品数',
                                      'tokens':'語数','work_prop':'作品数の割合',
                                      'token_prop':'語数の割合'}),
     caption='時代の構成 — 作品数で見るか語数で見るか',
     fmt={'語数':'{:,.0f}','作品数の割合':'{:.1%}','語数の割合':'{:.1%}'})

fig, ax = plt.subplots(figsize=(9,4))
x = np.arange(len(g))
ax.bar(x-0.2, g.work_prop, .38, label='作品数の比率', color=PALETTE[0])
ax.bar(x+0.2, g.token_prop, .38, label='語数の比率', color=PALETTE[1])
ax.set_xticks(x); ax.set_xticklabels([i.split('_',1)[1] for i in g.index],
                                      rotation=20, ha='right')
ax.set_ylabel('比率'); ax.legend(frameon=False)
ax.set_title('時代区分の代表性：作品数 vs 語数')
ax.spines[['top','right']].set_visible(False); ax.grid(axis='y', alpha=.25)
fig.tight_layout(); save_fig(fig, 'Step1_period_balance'); plt.show()'''),
 ('md', r'''### 図は SVG で保存する

`save_fig()` は図を **SVG（ベクタ形式）**で `my_work/results/` に書き出す。
PNG ではない。理由は3つ。

1. **拡大しても劣化しない。** PNG は画素の並びなので，スライドで投影したり
   論文に載せたりすると文字が潰れる。SVG は輪郭の記述なので何倍にしても鮮明
2. **あとから直せる。** Illustrator や Inkscape で開いて，軸ラベルの位置や
   凡例の文字だけを直せる。図を作り直す必要がない
3. **査読・投稿に通る。** 多くの学術誌がベクタ形式を要求する

既定では `svg.fonttype='path'`，つまり**文字をアウトライン（図形）に変換**する。
日本語フォントの入っていない環境で開いても崩れないためである。
編集しやすさを優先するなら `plt.rcParams['svg.fonttype'] = 'none'` にすると
文字が `<text>` 要素のまま残るが，閲覧側に同じフォントが必要になる。

点が数千個ある散布図では，**散布図だけ** `rasterized=True` を指定する。
点はラスタ化されてファイルが軽くなり，軸と文字はベクタのまま残る。

> **画面に出る図と，保存される図は別物である。**
> ノートブックの中に表示される図は PNG（Jupyter や VS Code の版によっては
> SVG がそのまま表示されないことがあるため）。**保存されるファイルは SVG**
> なので，レポートに貼るほうはベクタである。拡大して確かめたいときは，
> `save_fig` が表示するパスの `.svg` をブラウザで開くこと。

### セルを実行しても何も出ないとき

ノートブックのセルは，必要な入力が無ければ `need()` が理由を表示して
何もしない。たとえば：

```
[未実行] data/datasets/chunks_index.csv がありません。
         先に 06_build_datasets.py のセルを実行すること
```

これは**壊れているのではなく，前の工程がまだ走っていない**という意味である。
表示された指示に従い，上のセルから順に実行し直すこと。'''),
 ('md', r'''## 4. 演習 2 — 文語と口語の連続体

`bungo_per10k`（なり・けり・べし・ごとし…）と `kogo_per10k`（です・ます・である…）を
散布図にする。**言文一致運動の前後**を1枚で見せる図になるはずだが……

### 時代は「カテゴリ」ではなく「順序」である

`period` を凡例に取って8色で色分けするのが素朴なやり方だが，それでは
**明治中期が青，明治後期が黄，大正が赤**…となって，隣り合う時代が
隣り合う色にならない。この図で見たいのは個々の時代の位置ではなく
**時代が下るにつれて点がどちらへ動くか**だから，それでは肝心のものが
見えない。順序のあるものは**1色相の濃淡**に割り当てる。

段数は**5段**とする。6段にはできない。1色相の濃淡で順序を見せるには
隣り合う段の明度差が 0.06 以上要るが，白地の散布図で使える青の幅
（背景から浮く 250 から最も濃い 700 まで）は明度差にして 0.30 ほどしか
ない。6段取るとどこかが 0.05 台に落ち，隣の段と見分けられなくなる。
そこで作品数3点の**明治前期（〜1886）を明治中期にまとめて**5段にする。

色だけでは「どちらへ動いたか」は意外と読み取れないので，
**各段の中央値を結んだ軌跡**を重ねる。平均ではなく中央値にするのは，
文語標識が桁で外れる作品（『たけくらべ』など）に平均が引きずられるため。

> ### ⚠ この軸の値は「辞書に依存する実測値」である
>
> `bungo_per10k` / `kogo_per10k` は，**トークン列から数えた実測値**である
> （メタデータに人が書き入れた値ではない）。文語助動詞を1語と切るか
> 2語に割るかは辞書によって違うので，**辞書を替えるとこの図は動く**。
> `style_class`（A_文語体 / B_過渡 / C_口語体）は `bungo_per10k` の閾値で
> 決めているから，**作品の所属が変わることもある**。
>
> 本コーパスの辞書は `unidic-novel`（2026-09-22 決定）。辞書を替えたら
> `00_extend_metadata.py --remeasure-all` で測り直し，**この図も描き直す**
> こと。→ Step 3 §3，`docs/dictionary_comparison.md` §8'''),
 ('code', r'''TOP_N = 10
top = meta.nlargest(TOP_N, 'bungo_per10k').reset_index(drop=True)

fig, ax = plt.subplots(figsize=(9.6,6.2))

# ------------------------------------------------------------------
# **時代は順序のあるものである。** period をカテゴリの8色で色分けすると，
# 明治中期が青で明治後期が黄…となり，隣り合う時代が隣り合う色にならない。
# そうすると「時代が下るにつれて点がどちらへ動くか」という，この図で
# 見たい当のものが読めなくなる。初出年を5段に畳み，1色相の濃淡に割り当てる
# （淡いほど古く，濃いほど新しい）。年の値から作るので，period が空でも
# 年さえあれば色分けできる。
# ------------------------------------------------------------------
code, blabels, bcols = year_bands(meta.year_first)
if (code < 0).any():
    m = (code < 0)
    ax.scatter(meta.bungo_per10k[m], meta.kogo_per10k[m], s=46, alpha=.85,
               color='#c3c2b7', edgecolor='white', linewidth=.8,
               label=f'初出年不明（{int(m.sum())}点）', zorder=3)
for i, lab in enumerate(blabels):
    m = (code == i)
    if not m.any():
        continue
    # 濃淡5段を見分けさせるので，点は少し大きめにする。小さい点では
    # 面積が足りず，隣り合う段の明度差が目に入らない。
    ax.scatter(meta.bungo_per10k[m], meta.kogo_per10k[m], s=54, alpha=.9,
               color=bcols[i], edgecolor='white', linewidth=.8,
               label=f'{lab}（{int(m.sum())}点）', zorder=3)

# 各段の中央値をつなぐ。点の雲だけでは「どちらへ動いたか」は意外と
# 読み取れない。中央値の軌跡を1本引くと，時代による移動が線として出る。
# 中央値にするのは，文語標識が桁で外れる作品に平均が引きずられるため。
mx = [meta.bungo_per10k[code == i].median() for i in range(len(blabels))]
my = [meta.kogo_per10k[code == i].median() for i in range(len(blabels))]
ax.plot(mx, my, color='#55554f', linewidth=1.4, alpha=.85, zorder=4,
        label='各段の中央値（古→新）')
ax.scatter(mx, my, s=135, marker='D', c=bcols, edgecolor='#55554f',
           linewidth=1.2, zorder=5)
# 進む向きを矢印で示す。凡例を読まないと古新が分からない図は不親切である。
ax.annotate('', xy=(mx[-1], my[-1]), xytext=(mx[-2], my[-2]), zorder=6,
            arrowprops=dict(arrowstyle='-|>', color='#55554f', lw=1.4,
                            shrinkA=9, shrinkB=9))

ax.set_xlabel('文語助動詞標識（/万語・対数目盛）')
ax.set_ylabel('口語助動詞標識（/万語）')
ax.set_title('文語 ⇄ 口語（1点＝1作品／色＝初出年・濃いほど新しい）')
# linthresh を指定しないと目盛が 1 未満まで刻まれて右端が潰れる
ax.set_xscale('symlog', linthresh=10)
ax.grid(alpha=.25, linewidth=.6, zorder=0)
# 凡例は**順序どおり**に並べる。matplotlib は描いた順に並べるので，
# 淡→濃の順で描いておけば凡例もそのまま時代順になる。
ax.legend(frameon=False, fontsize=8.5, loc='upper left',
          bbox_to_anchor=(1.01, 1.0), borderaxespad=0)
ax.spines[['top','right']].set_visible(False)

# **注記は tight_layout の後に置く。** 先に置くと軸が動いて位置がずれる。
fig.tight_layout()
reserve_right(fig, 0.82)      # 凡例を面の外に置いてあるため
# 作者名＋作品名をそのまま置くと，上位はどれも右下隅に固まっているので
# 6件中5件が重なって読めない。番号だけを打ち，名前は下の表で引く。
# 番号は 8pt。密集帯の点の間隔は最小 9px で，9pt の数字（幅 8.4px を
# 1.08 倍に見込む）では直上に載らない。8pt なら載る。
label_points(ax, top.bungo_per10k, top.kogo_per10k,
             [str(i+1) for i in range(len(top))], fontsize=8)

# 番号が付くのは上位10点だけである。残りの91点は「どの作品か」が
# 分からないまま眺めることになる。**対話版では全点を指して引ける。**
# 表と同じ情報を持たせるので，番号と表を往復する必要もなくなる。
# 図の番号は，語幹の突合ではなく**同じ基準で順位を振り直して**求める。
# top は reset_index してあるので語幹で引こうとすると空振りする
# （空振りしても例外は出ないので，全件の番号が黙って空になる）。
order = meta.bungo_per10k.rank(ascending=False, method='first')
tips = []
for pos, (_, r) in enumerate(meta.iterrows()):
    n = int(order.iloc[pos])
    tips.append({'term': f'{r.author_ja}『{r.title_aozora}』',
                 'fields': [('初出', f'{r.year_first:.0f}'
                                     if pd.notna(r.year_first) else '不明'),
                            ('時代', str(r.period)),
                            ('文体区分', str(r.style_class)),
                            ('文語標識', f'{r.bungo_per10k:.1f}'),
                            ('口語標識', f'{r.kogo_per10k:.1f}'),
                            ('正書法', str(r.kana_orthography)),
                            ('語数', f'{r.tokens:,.0f}'
                                     if pd.notna(r.tokens) else '—'),
                            ('図の番号', str(n) if n <= len(top) else '')]})
save_interactive(fig, ax, 'Step1_bungo_kogo',
                 meta.bungo_per10k, meta.kogo_per10k, tips,
                 source=META, id_col='作品',
                 title='文語 ⇄ 口語（1点＝1作品）',
                 note=('色＝初出年の5段（濃いほど新しい）／菱形と矢印は'
                       '各段の中央値／番号は文語標識の上位10点。'
                       '横軸は symlog（10 未満は線形）。'),
                 table_cols=['初出', '時代', '文体区分', '文語標識',
                             '口語標識', '正書法', '語数', '図の番号'])
plt.show()

tbl = top[['author_ja','title_aozora','year_first',
           'bungo_per10k','kogo_per10k','style_class']].copy()
tbl.insert(0, '順位', range(1, len(tbl)+1))
show(tbl.rename(columns={'author_ja':'作家','title_aozora':'作品',
                         'year_first':'初出','bungo_per10k':'文語標識',
                         'kogo_per10k':'口語標識','style_class':'文体区分'}),
     caption=f'文語標識の上位{TOP_N}件（図中の番号に対応・万語あたり）',
     fmt={'文語標識':'{:.1f}','口語標識':'{:.1f}','初出':'{:.0f}'})'''),
 ('md', r'''### 考えてみよう

- 図の左下（文語も口語も少ない）に何があるか。それは何を意味するか。
- **中央値の軌跡はどちらへ向かっているか。**単調か，途中で折り返すか。
  折り返すとしたら，それは文体の変化か，それとも各段に入っている作品の
  顔ぶれ（ジャンル・作家）の違いか。
- `style_class` が `A_文語体` の作品は何点か。それで「言文一致以前」を代表できるか。
- **このコーパスで「言文一致による文体変化」を論じられるか。論じられないとしたら何が足りないか。**

`metadata/expansion_candidates.csv` に，この空白を埋めるための候補を挙げてある。'''),
 ('code', r'''cand = pd.read_csv(ROOT/'metadata'/'expansion_candidates.csv')

# priority は 1〜5 の整数のはずだが，手で編集される表なので
# 数値でない値が紛れることがある。そのまま cand.priority<=2 と書くと
# 列全体が文字列として読まれ TypeError で落ちる。数値化してから比べる。
cand['priority'] = pd.to_numeric(cand['priority'], errors='coerce')
bad = cand['priority'].isna().sum()
if bad:
    print(f'[warn] priority が数値でない行が {bad} 件ある（絞り込みから外れる）')

cols = ['priority','gap','author_ja','title','year_target','style_expect','rationale']
show(cand[cand['priority'] <= 2][cols].sort_values('priority').head(20),
     caption='増補の候補（優先度1・2のみ／上位20件）')'''),
 ('md', r'''## 5. 演習 3 — 欠陥を自分で見つける

メタデータではなく**テクスト本体**を見る。検証スクリプトを走らせる前に，
まず素朴な方法で重複を探してみよう。

### 手がかり：統計量が近すぎるファイルはないか'''),
 ('code', r'''# v1 コーパス（64点の .txt）の場所。指定の仕方は2通りある。
#
#  (a) **このセルに直接書く** … 手っ取り早い。下の V1_PATH を埋める
#  (b) **環境変数で渡す**     … マシンを移っても効く。Jupyter を起動する
#      **前に**シェルで次を実行しておく
#          export JLIT_CORPUS_V1=~/Dropbox/Corpus/DH_text_analytics_2025/corpus
#
# **`os.environ.get()` の中に `export …` と書いてはいけない。** そこに入るのは
# 環境変数の**名前**であって，シェルのコマンドではない。書いても例外は出ず，
# 「そんな名前の変数は無い」と判定されて既定値に落ちるだけなので気づきにくい。
# 直接書きたいときは V1_PATH のほうを使うこと。
V1_PATH = ''        # 例: '~/Dropbox/Corpus/DH_text_analytics_2025/corpus'

# expanduser を通すこと。'~/…' は Path が展開しないので，そのままでは
# 「存在しない」と判定される。
CORPUS_V1 = Path(os.path.expanduser(
    V1_PATH or os.environ.get('JLIT_CORPUS_V1') or str(ROOT/'data'/'corpus_v1')))
print(f'v1 コーパス: {CORPUS_V1}')
print('  →', '見つかった' if CORPUS_V1.exists() else '**見つからない**')
if not CORPUS_V1.exists():
    print('  本文そのものを読む検査（次のセルと 99_validate）は飛ばされる。')
    print('  場所が分かっているなら，このセルの V1_PATH に書いて再実行すること。')

# ---- ここから下は診断表だけで動く。v1 コーパスが無くても実行できる -------
# 本文を読まずに重複を疑う，というのがこの演習の要点である。
d = pd.read_csv(ROOT/'metadata'/'diagnostics_v1.csv')
# 語数・異なり語数・漢字率が極端に近いペアを探す
cols = ['tokens','types','kanji_ratio']
X = d[cols].values.astype(float)
Xn = (X - X.mean(0)) / X.std(0)
D = np.linalg.norm(Xn[:,None,:]-Xn[None,:,:], axis=2)
np.fill_diagonal(D, np.inf)
i,j = np.unravel_index(np.argmin(D), D.shape)
show(d.iloc[[i,j]][['file']+cols],
     caption=f'最も統計量の近いペア（標準化距離 {D[i,j]:.5f}）',
     fmt={'tokens':'{:,.0f}','types':'{:,.0f}','kanji_ratio':'{:.3f}'})
print('**距離がほぼ 0 なら同一本文の疑い。** 次のセルで n-gram で確かめる。')'''),
 ('code', r'''# 8-gram シングルによる重複検出（99_validate.py の中核）
def shingles(text, n=8, step=3):
    t = text.split()
    return {tuple(t[i:i+n]) for i in range(0, max(0,len(t)-n), step)}

if CORPUS_V1.exists():
    a = (CORPUS_V1/d.file[i]).read_text(encoding='utf-8')
    b = (CORPUS_V1/d.file[j]).read_text(encoding='utf-8')
    A,B = shingles(a), shingles(b)
    print(f'8-gram 包含率 = {len(A&B)/min(len(A),len(B)):.1%}')
    print('\n--- 冒頭120字 ---')
    print('A:', a[:120].replace(chr(10),'/'))
    print('B:', b[:120].replace(chr(10),'/'))'''),
 ('md', r'''### 何が起きていたか

`乱歩_灰色の巨人.txt` の本文は **『魔法博士』と同一**である。章題まで一致する
（動く映画館／悪魔の国／人造人間／黄金怪人／井戸の中から／奇々怪々…）。
つまり実効サンプル数は 64 ではなく **63**。

さらに 2 種類の欠陥がある。

1. **外字の喪失** — 青空文庫の外字注記 `※［＃「…」、第4水準2-81-40］` から
   `［＃…］` だけを削った結果，`※` が本文に残り，文字が失われている。
   全体で **592箇所**。『不如帰』の「合※の式」はもと「合巹の式」である。
2. **奥付の混入** — `海野十三_敗戦日記.txt` の末尾に
   「入力：青空文庫／校正：伊藤時也／ファイル作成：野口英司」以下がそのまま残っている。

いずれも「正規表現で要らないものを削る」という方針の副作用である。
Step 2 からは **削らずにタグで分離する** 方針に切り替える。'''),
 ('code', r'''# 全件検査。FATAL が出るのが正しい（これが Step 2–3 で直す対象）
#
# **ここで渡すメタデータは v2 である。** v3 は Step 3 で作り直した
# 108 点のコーパスを記述する表で，v1 の欠陥（本文の取り違え・不完全収録）
# はすでに解消済みとして書いてある。v3 を渡すと「FATAL が出るのが正しい」
# はずの検査が何も出さず，何を直したのかが分からなくなる。
META_V1 = ROOT/'metadata'/'corpus_metadata_v2.csv'
if CORPUS_V1.exists():
    run_script('99_validate.py', '--corpus', CORPUS_V1,
               '--meta', META_V1,
               '--out', OUT/'Step1_validation.csv')
else:
    need(CORPUS_V1, 'v1 コーパスの場所を環境変数 JLIT_CORPUS_V1 で指定すること')'''),
 ('md', r'''## 6. このステップの課題

次の設問への答えを，テンプレート `my_work/results/Step1_report.md` に書いて提出する（**全体で600–1000字程度**。図表と「再現のための情報」は字数に含めない）。

- **提出先**：Zulip（{ZULIP_ORG}）の非公開チャネル **{ZULIP_CHANNEL}** ＞ トピック **Step 1**
- テンプレートの中身をメッセージに貼り付け，図（SVG）・表（CSV）は**同じメッセージに添付**する（1人1通）
- 図は番号で言及し（図1），**図を見なくても論旨が追えるように**書く（SVG は Zulip で表示されないことがある）
- 再提出は元の投稿を直さず，同じトピックに新しく投稿する（手順書 §5.3）

1. このコーパスで**最も深刻な偏り**はどれか。作品数と語数の両方を根拠に述べること。
2. その偏りは，どんな研究上の問いを**不可能にする**か。具体的に1つ挙げること。
3. `expansion_candidates.csv` の **`in_corpus` が `未収録` の行**から3点選び，
   なぜその3点かを説明すること。11件のうち**4件は `保護期間中`**（著作権存続）である。
   「入れたい作品」と「入れられる作品」が一致しないことが何を意味するか，
   1 で述べた偏りと結びつけて論じること。
4. 図を最低1枚（自分で作ったもの）添付すること。**SVG で提出すること。**

### このステップの到達点（次へ進む条件）

- `00_env_check.py` が `ALL OK` を返す（Python・UniDic・MALLET・日本語フォント）
- 表示された**マシン名を控えた**（共用 iMac の場合）
- `save_fig()` で SVG を書き出し，ブラウザで開いて日本語が読めることを確認した
- その図を含めて `my_work/` を自分の GitHub に push できた（`setup_my_work.sh`）
- v1 の偏りを示す図を自分で1枚作った
- 3つの重大な欠陥を自分の手で再発見した
- `docs/representativeness_report.md` を通読した
'''),
])

# ==========================================================================
L(2, '青空文庫からの再構築(1) — 書誌の典拠とXMLマークアップ', [
 ('md', r'''# Step 2 青空文庫からの再構築(1) — 書誌の典拠と XML マークアップ

## このステップの到達目標

1. 青空文庫の書誌索引から，**典拠のある**メタデータを自動生成できる
2. プレーンテクスト版と XHTML 版の違いを説明できる
3. 外字（JIS X 0213）を面区点から Unicode に復元する仕組みを理解する
4. ルビ・注記・会話を XML でタグ付けした版を作る

## 導入：なぜ「削る」のをやめるのか

Step 1 で見たとおり，v1 の欠陥はすべて**削除**に由来する。

```
※［＃「广＋(炎/鳥)」、第4水準2-81-40］     ← 青空文庫の原文
※                                        ← ［＃…］ だけ削った結果（文字が消える）
㽷                                        ← 正しくは面区点から復元できる
```

削除は不可逆である。いったん削ったものは戻らない。したがって

> **本文とメタ情報を「分離」し，何を数えるかは後段の設定で決める**

という設計に変える。これは TEI（Text Encoding Initiative）の基本思想でもある。

## 青空文庫の2つの配布形式

| | プレーンテクスト版 (.txt) | XHTML 版 (.html) |
|---|---|---|
| 文字コード | Shift_JIS | UTF-8 |
| ルビ | `渋江《しぶえ》` | `<ruby><rb>渋江</rb><rt>しぶえ</rt></ruby>` |
| 注記 | `［＃…］` | `<span class="notes">［＃…］</span>` |
| 外字 | `※［＃…］` | `<img class="gaiji" alt="※(噓, 1-84-7)">` |
| 奥付 | 本文と同じ平文 | `<div class="bibliographical_information">` |

**XHTML を使う。** 境界が要素で決まるので，正規表現の当て推量が要らない。
'''),
 ('code', PREAMBLE),
 ('md', r'''## 1. 書誌索引 — 一次資料としての CSV

青空文庫は公開中の全作品（約 2万点）について，
作品ID・作品名・**初出**・**分類番号(NDC)**・文字遣い種別・底本・親本・
テキスト/XHTML の URL を1つの CSV で配布している。

`list_person_all_extended_utf8.zip`

**v1 の `year` 列の誤りは，この索引を使っていれば起きなかった種類の誤りである。**
図書カードを1件ずつ読む必要もない。'''),
 ('code', r'''# 索引の取得（最初の一度だけ。約 20 MB）
DATA = ROOT/'data'/'aozora'
run_script('02_fetch_aozora.py', 'index', '--out', DATA)'''),
 ('code', r'''idx = pd.read_csv(DATA/'list_person_all_extended_utf8.csv',
                  encoding='utf-8-sig', low_memory=False)
print(idx.shape)
print([c for c in idx.columns][:20])
# 森鴎外の作品を覗いてみる
m = idx[(idx['姓']=='森') & (idx['名']=='鴎外')]
show(m[['作品ID','作品名','初出','分類番号','文字遣い種別']].head(15),
     caption='青空文庫索引から引いた森鴎外の作品（先頭15件）')'''),
 ('md', r'''### 演習 1 — v1 の誤りを索引で確かめる

小栗虫太郎『潜航艇「鷹の城」』を索引から引き，`初出` 欄と `底本初版発行年1` を
比べよ。v1 が記録していた 1977 はどちらか。'''),
 ('code', r'''q = idx[idx['作品名'].astype(str).str.contains('鷹の城', na=False)]
cols = [c for c in idx.columns if c in
        ['作品ID','作品名','初出','分類番号','文字遣い種別',
         '底本名1','底本出版社名1','底本初版発行年1']]
show(q[cols].T.reset_index().rename(columns={'index':'項目'}),
     caption='『潜航艇「鷹の城」』の索引の記述（列を縦に倒した）')'''),
 ('md', r'''## 2. マニフェストによる取得

取りたい作品は `config/corpus_manifest.tsv` に **作者名＋作品名**で書く。
作品IDが分かっていれば書いてもよい。スクリプトが索引と突き合わせて
ID・URL・書誌を解決する。'''),
 ('code', r'''man = pd.read_csv(ROOT/'config'/'corpus_manifest.tsv', sep='\t', comment='#')
show(man['set'].value_counts().rename_axis('set').reset_index(name='件数'),
     caption='マニフェストの内訳')
show(man.head(8), caption='マニフェストの先頭8行')'''),
 ('code', r'''# まず解決だけ試す（ダウンロードしない）
run_script('02_fetch_aozora.py', 'resolve',
           '--manifest', ROOT/'config'/'corpus_manifest.tsv', '--out', DATA)'''),
 ('code', r'''# 未解決の一覧を表で確認する（resolve を実行すると作られる）
p = DATA/'unresolved.csv'
if need(p):
    un = pd.read_csv(p)
    show(un.cause.value_counts().rename_axis('原因').reset_index(name='件数'),
         caption='未解決の内訳')
    # 原因ごとにまとめ，原因名はブロックの先頭行だけに出す。
    # 候補は長いので 160 字で切る（全文は unresolved.csv にある）。
    rows = []
    for cause in ['title_mismatch', 'author_differs', 'author_not_found']:
        d = un[un.cause == cause]
        for i, (_, r) in enumerate(d.iterrows()):
            cand = r.candidates if isinstance(r.candidates, str) else ''
            rows.append({'原因': cause if i == 0 else '', 'set': r['set'],
                         '作家': r.author_ja, '作品': r.title,
                         '候補': cand[:160]})
    if rows:
        show(pd.DataFrame(rows), caption='未解決の一覧（原因ごと）')
        print('**「候補」が出ているものは綴りの違いである。** '
              'マニフェストの表記を索引に合わせるか，作品IDを直接書く。')
else:
    print('unresolved.csv がまだありません。上の resolve セルを実行してください。')'''),
 ('md', r'''**未解決行は原因つきで表示される。** 原因は3つに分かれ，対処が違う。

| 原因 | 意味 | 対処 |
|---|---|---|
| `title_mismatch` | 著者は登録あり，作品名が一致しない | 候補を見てマニフェストの `title` を直す |
| `author_differs` | 同名作品が別の著者名の下にある | **翻訳作品**は原著者の下に入る（『即興詩人』→アンデルセン，『小公子』→バーネット）。`author_ja` を直し，訳者は `note` へ |
| `author_not_found` | 著者そのものが青空文庫にない | **著作権保護期間中（没後70年）**。作品名を直しても解決しない。マニフェストから外す |

`title_mismatch` の典型は，作品名の表記ゆれ（『不如帰』vs『小説 不如帰』），
副題つき（『半七捕物帳 69 白蝶怪』），分冊（『夜明け前 01 第一部上』），
新字版・旧字版の併存（啄木『鳥影』）である。

一覧は `data/aozora/unresolved.csv` にも落ちる。
**core の行が混じっていたら最優先で直す**こと。prio1〜4 は増補候補なので，
当面は次のセルの `--set core` で現行64点だけ先に取得してよい。

**この突き合わせ作業そのものがコーパス構築の実務である。**'''),
 ('code', r'''# 本文（XHTML）の取得。1秒/件の間隔を空けるので，100件で約2分かかる。
# 各自，自分のマシンで実行する。取得した XHTML はこのマシンの共有キャッシュ
# （/Users/Shared/jlit/aozora-cache）にも置かれ，同じマシンでの2回目以降は
# 青空文庫に取りに行かない（マシンどうしでは共有されない）。
RUN_FETCH = False        # ← 実行するときだけ True に
ONLY_CORE = True         # True なら現行64点（set=core）だけ取得する

if RUN_FETCH:
    opts = ['--set', 'core'] if ONLY_CORE else []
    run_script('02_fetch_aozora.py', 'works',
               '--manifest', ROOT/'config'/'corpus_manifest.tsv',
               '--out', DATA, *opts)
else:
    print('RUN_FETCH = False のままです。取得するときは True にしてください。')'''),
 ('md', r'''## 3. 外字の復元 — 面区点から Unicode へ

青空文庫の外字注記は JIS X 0213 の **面-区-点** を与えている。

```
※［＃「广＋(炎/鳥)」、第4水準2-81-40］
                      └面┘└区┘└点┘
```

Python の `euc_jis_2004` コーデックを使うと，面区点はバイト列に直して
そのまま復号できる。

| 面 | バイト列 |
|---|---|
| 面1 | `0xA0+区, 0xA0+点` |
| 面2 | `0x8F, 0xA0+区, 0xA0+点` |

`scripts/lib/aozora.py` の `menkuten_to_char()` がこれを実装している。

### 注記は3つの形式がある

面区点を与えてくれるのは**いちばん親切な形式**にすぎない。実際には3つある。

| 形式 | 例 | 解決法 |
|---|---|---|
| 面区点 | `※［＃「广＋(炎/鳥)」、第4水準2-81-40］` | `euc_jis_2004` で復号 |
| Unicode | `※［＃「女＋（而／大）」、U+5A86、7巻-16-下-14］` | `chr(0x5A86)` |
| **字の名前だけ** | `※［＃小書き片仮名ヲ、160-9］` | 対応表を引く。無ければ欠字 |

3つめが曲者である。JIS X 0213 に無い字なので青空文庫は番号を書けない。
末尾の `160-9` は**底本のページ-行**であって面区点ではない。これを面区点と
読み違えたり，注記の中身に「水準」という語が無いことを理由に外字扱いを
しなかったりすると，`※` が本文に残る。`※` は UniDic では記号1字として
数えられるので，**文字が失われたことが誰にも気づかれない**。

さらに，`※` が**ルビの基底文字の中**に入ることもある。

```html
<ruby rt="どんさうじゆけい">※相寿桂</ruby><span class="notes">
［＃「女＋（而／大）」、U+5A86、7巻-16-下-14］</span>
```

注記の直前が `</ruby>` なので「直前の文字が `※` か」だけを見ていると
取り逃がす。森鴎外『伊沢蘭軒』だけで 74 箇所がこの形である。'''),
 ('code', r'''from lib.aozora import menkuten_to_char, resolve_gaiji

tests = [(1,15,25),(2,78,35),(2,81,40),(1,84,7),(2,3,72),(1,14,22)]
show(pd.DataFrame([{'面': m, '区': k, '点': t,
                    '復元した字': menkuten_to_char(m,k,t)}
                   for m,k,t in tests]),
     caption='面区点 → 文字（JIS X 0213 の面区点番号を Unicode に直す）')

descs = ['「广＋(炎/鳥)」、第4水準2-81-40',            # 面区点
         '「女＋（而／大）」、U+5A86、7巻-16-下-14',    # Unicode
         '「にんべん＋弖」、第3水準1-14-22',
         '小書き片仮名ヲ、160-9',                      # 字名のみ（解決できる）
         '「弋＋頁」、239-6',                          # 字名のみ（解決できない）
         ]
rows = []
for desc in descs:
    ch, kind = resolve_gaiji(desc)
    rows.append({'入力者注の記述': desc, '復元': ch if ch else '（不能）',
                 '手がかり': kind})
show(pd.DataFrame(rows), caption='注の記述からの復元（どの手がかりで解けたか）')
print('「手がかり」の列が復元の根拠である。**同じ字が別の記述で書かれること'
      'があるので，根拠を残さないと再現できない。**')'''),
 ('md', r'''### 演習 2 — 失われた文字を数え，どこで失われたかを言う

`03_aozora2xml.py` の変換レポートには `gaiji_resolved` と
`gaiji_unresolved` が出る。両者の意味は**まったく違う**。

- `gaiji_resolved` … 実体を復元して `<g ref="…">侔</g>` に入れた
- `gaiji_unresolved` … 実体が分からないので `<g ref="unresolved" n="注記">`
  として残した。正規化後は `〓` になる

そして，正規化後のテクストに残る `※` は**第3のもの**である。

1. 正規化後テクスト（`data/plain/full/`）の `※` と `〓` をそれぞれ数えよ。
2. `※` が残っている作品を1つ選び，青空文庫の原文を見て，それが
   **(a) 外字マーカの消し残し** なのか **(b) 底本そのものが使う記号**
   （編者注の印，伏字）なのかを判定せよ。
3. (b) だった場合，それは欠陥か。なぜそう言えるか。

**ヒント**: 海野十三『敗戦日記』には `（※マリアナ基地からの…）` という
補注が 100 箇所あまりある。これは底本の編者（橋本哲男）が付けたもので，
**本文の一部**である。外字マーカの消し残しではない。
なお，このファイルは `<div class="main_text">` を持たない旧形式なので，
`03_aozora2xml.py` は `<hr>` を手掛かりに奥付を落としている。奥付にも外字注記の `※` があるため，**奥付を落とさないと (a) と (b) が
混ざって数えられない**。'''),
 ('md', r'''## 4. XML への変換

`scripts/03_aozora2xml.py` が XHTML を TEI 風 XML に変換する。

```xml
<p>池田氏、名は<g ref="1-14-22">侔</g>、字は河澄。</p>
<p><said>「わたくしは<ruby rt="こ">此</ruby>に記述したい。」</said>と云つた。</p>
<p><quote>『武鑑』</quote>に接続する<note type="textual">「○」は底本では「●」</note>。</p>
<p><quote type="embedded" part="I">「……私はこの夏あなたから手紙を受け取りました。</p>
<p><quote type="embedded" part="M">妻は<said>「もう寝ましょう」</said>と云った。</p>
```

要点は5つ。

- `<g>` に外字の**実体**が入る（`ref` に面区点を残すので検証できる）
- `<ruby rt="...">` で読みを保持したまま基底文字を本文に残す
- `<said>` で**会話と地の文を分離**する。語りの人称を測るとき必須になる
- `@part="I|M|F"` は段落をまたぐ会話の断片。XML の入れ子を段落構造と
  交差させられないので，段落末で閉じ，次の段落で開き直す
- `<quote type="embedded">` は8段落以上にわたる引用，つまり書簡・演説・
  手記といった**埋め込まれたテクスト**。その内側の対話は `<said>` になる

最後の2つは，底本の組版慣習に合わせるために必要になった。
`「` で始まり `」` で閉じる，という素朴な前提は日本語の底本では成り立たない。'''),
 ('code', r'''XHTML = DATA/'xhtml'
XML   = ROOT/'data'/'xml'
if need(XHTML, '上の RUN_FETCH を True にして青空文庫から取得すること'):
    run_script('03_aozora2xml.py', '--in', XHTML,
               '--log', DATA/'fetch_log.csv', '--out', XML)
else:
    print('XHTML がまだありません。上の RUN_FETCH を True にして取得してください。')'''),
 ('md', r'''### 分冊を1作品にまとめる

青空文庫は長篇を分冊ごとに別カードで公開する。『夜明け前』は4カード，
『家』は上下2カードである。**そのまま進めると，分析の単位が「作品」では
なく「冊」になる。**

- Delta や doc2vec の最近傍が，同じ作品の別の巻になる。
  Step 4 の「最近傍が同じ作家である割合」は自明に上がり，指標として死ぬ
- 1作家あたりの作品数が水増しされる（島崎藤村は分冊のせいだけで9点あった）
- 語数・TTR・文体指標が巻ごとにばらけ，他の作品と比べられない

TEI 的にも，分冊は1つの `<text>` の中の `<div type="volume">` であって
別の文書ではない。**XML の段階でまとめる**のが正しい。ここでまとめておけば，
04・05・06・99 はすべて「1作品1ファイル」の前提のまま動く。

まとめる組合せは `config/merge_volumes.tsv` に書いてある。もとの巻別 XML は
`data/xml/_volumes/` に退避されるので，巻ごとの比較をしたくなれば戻せる。'''),
 ('code', r'''if need(XML, 'Step 2 の 03_aozora2xml.py を先に走らせること'):
    # まず確認だけ（--dry-run）。何が何にまとまるかを読んでから本番を走らせる
    run_script('03b_merge_volumes.py', '--xml', XML,
               '--config', ROOT/'config'/'merge_volumes.tsv', '--dry-run')'''),
 ('code', r'''if need(XML, 'Step 2 の 03_aozora2xml.py を先に走らせること'):
    run_script('03b_merge_volumes.py', '--xml', XML,
               '--config', ROOT/'config'/'merge_volumes.tsv')'''),
 ('code', r'''# 変換レポート：外字がどれだけ復元できたか
p = XML/'conversion_report.csv'
if need(p, 'Step 2 の 03_aozora2xml.py を先に走らせること'):
    rep = pd.read_csv(p)
    print('外字 復元合計 =', rep.gaiji_resolved.sum(),
          '／ 未解決 =', rep.gaiji_unresolved.sum())
    show(rep.nlargest(12,'gaiji_resolved')[
            ['author','title','gaiji_resolved','gaiji_unresolved','ruby','said']]
         .rename(columns={'author':'作家','title':'作品',
                          'gaiji_resolved':'外字 復元','gaiji_unresolved':'未解決',
                          'ruby':'ルビ','said':'会話'}),
         caption='外字の多い作品（復元数の上位12件）')'''),
 ('md', r'''### 会話標示の診断を読む

変換レポートには会話の標示に関する列が並ぶ。素朴な実装では出ない列である。

| 列 | 意味 |
|---|---|
| `speech_mark` | その作品の**第一階層の会話符**。`「」` か `『』` |
| `said` | 会話の数（段落をまたぐものも1と数える） |
| `said_parts` | `<said>` 要素の数。断片化したぶん `said` より多い |
| `said_density` | 1万字あたりの会話数 |
| `said_cont` | 継続引用符で接続した回数 |
| `quote_embedded` | 埋め込みテクストの数 |
| `said_unclosed` | 閉じ括弧のない開き括弧の数 |
| `speech_markup` | 標示が信用できるか。`full` / `partial` / `none` |

`speech_mark` が `『』` になる作品があることに注意せよ。島崎藤村『破戒』は
`「」` が 47 に対し `『』` が 1,574 で，会話のほぼ全部が `『』` である。
一律に `「」` を会話と見ると，この作品の会話が**丸ごと 0 件**になる。'''),
 ('code', r'''if need(p):
    cols = ['author','title','speech_mark','speech_markup','said',
            'said_parts','said_density','said_cont','quote_embedded',
            'said_unclosed']
    cols = [c for c in cols if c in rep.columns]
    show(rep[rep.speech_mark == '『』'][cols],
         caption='第一階層の会話符が『』の作品（「」で数えると会話が 0 件になる）')
    show(rep.nlargest(8, 'said_cont')[cols],
         caption='継続引用符・埋め込みテクストが多い作品')
    show(rep.nlargest(6, 'said_unclosed')[cols],
         caption='閉じ括弧のない開き括弧が多い作品（標示の信頼性が落ちる）')'''),
 ('md', r'''### 演習 3 — 会話文比率を出す。そして「0」と「測れない」を区別する

`<said>` があるので，作品ごとの会話文比率が計算できる。これは
**ジャンルを最もよく分ける文体指標の一つ**である（戯曲＞小説＞随筆＞論説）。

ただし注意すべきことがある。**比率が 0 であることと，比率が測れないことは違う。**
底本が会話符を使わない作品がある。樋口一葉『たけくらべ』『にごりえ』は
会話に括弧を用いず地の文に溶け込ませる。岡本綺堂『修禅寺物語』は戯曲で，
会話は話者名と字下げで示される。福沢諭吉『福翁自伝』は口述筆記で，
`「` で話を起こして「と云う」で閉じ `」` を置かない（本文中 `「` 330 に対し
`」` 38）。これらを「会話文比率 0」として分析に入れると，
**文体指標も話法の通時変化も系統的に歪む**。

`03_aozora2xml.py` は `teiHeader` の `<catRef scheme="speech_markup">` に
`full` / `partial` / `none` を記録している。`full` 以外は欠測として扱うこと。'''),
 ('code', r'''import xml.etree.ElementTree as ET

def speech_markup(root):
    for cr in root.iter('catRef'):
        if cr.get('scheme') == 'speech_markup':
            return cr.get('target')
    return 'full'

rows = []
for f in sorted(XML.glob('*.xml')):
    root = ET.parse(f).getroot()
    body = root.find('.//body')
    all_text  = ''.join(body.itertext())
    said_text = ''.join(''.join(s.itertext()) for s in body.iter('said'))
    grade = speech_markup(root)
    rows.append({'file': f.name,
                 # 作者も出す。『武蔵野』は山田美妙と国木田独歩の2点あり，
                 # 作品名だけでは区別できない
                 'author': root.findtext('.//author',''),
                 'title': root.findtext('.//title',''),
                 'chars': len(all_text),
                 'markup': grade,
                 # full 以外は NaN にする。0 で埋めてはいけない
                 'speech_ratio': (len(said_text)/max(1,len(all_text))
                                  if grade == 'full' else float('nan'))})
if rows:
    df = pd.DataFrame(rows)
    show(df.markup.value_counts().rename_axis('標示の程度')
           .reset_index(name='作品数'), caption='会話標示の内訳')
    show(df[df.markup != 'full'][['author','title','markup','chars']]
         .rename(columns={'author':'作家','title':'作品',
                          'markup':'標示の程度','chars':'字数'}),
         caption='会話文比率を**欠測**にした作品（0 で埋めてはいけない）',
         fmt={'字数':'{:,.0f}'})
    d = df.dropna(subset=['speech_ratio']).sort_values('speech_ratio', ascending=False)
    ends = pd.concat([d.head(8), d.tail(8)])[
        ['author','title','markup','speech_ratio']].rename(
        columns={'author':'作家','title':'作品','markup':'標示の程度',
                 'speech_ratio':'会話文比率'})
    show(ends, caption='会話文比率の上位8件と下位8件',
         fmt={'会話文比率':'{:.1%}'})'''),
 ('md', r'''## 5. このステップの課題

次の設問への答えを，テンプレート `my_work/results/Step2_report.md` に書いて提出する（**全体で600–1000字程度**。図表と「再現のための情報」は字数に含めない）。

- **提出先**：Zulip（{ZULIP_ORG}）の非公開チャネル **{ZULIP_CHANNEL}** ＞ トピック **Step 2**
- テンプレートの中身をメッセージに貼り付け，図（SVG）・表（CSV）は**同じメッセージに添付**する（1人1通）
- 図は番号で言及し（図1），**図を見なくても論旨が追えるように**書く（SVG は Zulip で表示されないことがある）
- 再提出は元の投稿を直さず，同じトピックに新しく投稿する（手順書 §5.3）

1. `config/corpus_manifest.tsv` の未解決行を最低3つ解決し，直した行を報告すること。
2. v1 で `※` になっていた箇所を **3つ**選び，青空文庫の原文から復元した文字と，
   その語の意味を示すこと（辞書を引くこと）。3つのうち少なくとも1つは
   **面区点を持たない注記**（`※［＃小書き片仮名ヲ、160-9］` の形）から選ぶこと。
3. XML から会話文比率を計算し，ジャンル別に比較した図を1枚作ること。
   ただし `speech_markup` が `full` でない作品は**図から除き，その旨を図の
   キャプションに書く**こと。0 で埋めて描いた図は誤りである。
4. `scripts/find_unclosed_quotes.py` を `data/xml` にかけ，残った
   `unmarked_paragraph` を1つ選んで青空文庫の原文を確認し，
   **底本の組版のどの慣習に由来するか**を説明すること。

### このステップの到達点（次へ進む条件）

- `data/xml/` に全件の XML があり，`conversion_report.csv` が出ている
- `gaiji_unresolved` の合計と，その内訳（どの作品のどの字か）を言える
- `speech_markup` が `full` でない作品を列挙でき，理由を説明できる
- `docs/encoding_guidelines.md` を読み終えている
'''),
])

# ==========================================================================
L(3, '再構築(2) — 踊り字の正規化・UniDic解析・データセット構築', [
 ('md', r'''# Step 3 再構築(2) — 踊り字の正規化・UniDic 解析・データセット構築

## このステップの到達目標

1. 踊り字（`ゝゞヽヾ` とくの字点）を規則的に展開できる／`々` を展開しない理由を言える
2. UniDic の語形（surface / orthBase / lemma / lForm）の違いを説明できる
3. **解析辞書の選び方を，未知語率と平均語長の両方から論じられる**
4. 未知語率を指標に，前処理の良し悪しを判定できる／
   **その指標が何を測っているかが途中で変わりうることを説明できる**
5. 長さの偏りを吸収するチャンク分割ができる

## 導入：踊り字はなぜ問題か

v1 コーパスの仮名踊り字の分布を見よ。

| ファイル | ゝ | ゞ | ヽ | ヾ |
|---|---:|---:|---:|---:|
| 藤村『破戒』 | **218** | 0 | 0 | 0 |
| 岡本かの子『生々流転』 | 8 | **183** | 0 | 0 |
| 石川啄木『鳥影』 | 2 | 5 | **66** | 2 |
| 藤村『夜明け前』『家』『新生』『千曲川』 | **0** | 0 | 0 | 0 |

同じ藤村でも『破戒』だけ 218 箇所。これは作家の文体差ではなく，
**底本の正書法の差**である（『破戒』は新字旧仮名）。

正規化しないと，UniDic は `たゞ` を未知語として切り出す。すると

- `ただ`（副詞）が消え，`た` `ゞ` という無意味な列ができる
- 「藤村の文体は他と違う」という結論が，実は底本の違いを測っている

## `々` を展開してはいけない理由

`々` は UniDic に**語彙素として登録されている**（人々・時々・我々）。
これを `人人` に展開すると，かえって未知語になる。
**踊り字だからといって一律に扱わない。** 辞書の挙動を確かめてから決める。
'''),
 ('code', PREAMBLE),
 ('code', r'''from lib.aozora import normalise_iteration_marks, expand_kana_odoriji, expand_kunoji

samples = ['たゞ一人','つゞき','ホホホヽヽ','ドヾドン','はゝゝゝ','あゝ',
           'いろ／＼','とき／″＼','しみ〴〵','人々','時々','我々']
for s in samples:
    out, st = normalise_iteration_marks(s)
    print(f'{s:<10} → {out:<10}  {st}')'''),
 ('md', r'''### 規則

| 記号 | 規則 | 例 |
|---|---|---|
| `ゝ` | 直前の平仮名を反復（直前が濁音なら清音化） | `はゝ` → `はは` |
| `ゞ` | 直前の平仮名を濁音化して反復 | `たゞ` → `ただ` |
| `ヽ` `ヾ` | 片仮名版 | `ドヾ` → `ドド` |
| `／＼` `〳〵` | 直前の仮名連続（2–4字）を反復 | `いろ／＼` → `いろいろ` |
| `／″＼` `〴〵` | 同上・濁音化 | `しみ〴〵` → `しみじみ` |
| `々` | **展開しない** | `人々` → `人々` |

くの字点は「直前の何字を繰り返すか」が原理的に曖昧なので，
**機械推定であることを記録に残す**（`@resp="auto"`）。'''),
 ('md', r'''## 1. 正規化の実行

`04_normalise.py` は XML から4つのストリームを作る。

| ストリーム | 内容 | 使いどころ |
|---|---|---|
| `full` | 地の文＋会話 | 既定の分析対象 |
| `narration` | 地の文のみ | 語りの人称を測るとき |
| `speech` | 会話のみ | 会話文比率・役割語の分析 |
| `embedded` | 書簡・手記など埋め込みテクスト | 枠構造をもつ作品の内部対照 |

いずれも `<note>` は除外，ルビの読みは除外，`<g>` は復元文字に置換される。

`embedded` は `<quote type="embedded">`，すなわち8段落以上にわたる引用である。
『こころ』下「先生と遺書」（本文の 51%），森鴎外『即興詩人』（30%），
夢野久作『ドグラ・マグラ』（6%）などが該当する。これらを地の文と
一緒くたにすると，枠物語の内と外の文体差が見えなくなる。

レポートの `speech_ratio` は，会話標示が `full` でない作品では**空欄**になる。
該当が何点あるかは `conversion_report.csv` の `speech_markup` 列を
数えること（増補で変わる）。
`fillna(0)` をしないこと。'''),
 ('code', r'''XML   = ROOT/'data'/'xml'
PLAIN = ROOT/'data'/'plain'
if need(XML, 'Step 2 のノートブックを最後まで実行し，data/xml/ に XML を作ること'):
    run_script('04_normalise.py', '--in', XML, '--out', PLAIN,
               '--config', ROOT/'config'/'pipeline.yaml')
else:
    print('XML がありません。Step 2 のノートブックを先に実行してください。')'''),
 ('code', r'''p = PLAIN/'normalise_report.csv'
if need(p, '04_normalise.py のセルを先に実行すること'):
    nr = pd.read_csv(p)
    print('踊り字の展開合計:',
          int(nr.kana_odoriji_expanded.sum()), '(仮名)',
          int(nr.kunoji_expanded.sum()), '(くの字)')
    print('未解決外字:', int(nr.unresolved_gaiji.sum()))
    LAB = work_labels()
    nr2 = nr.nlargest(10,'kana_odoriji_expanded').copy()
    nr2.insert(1, '作品',
               nr2['file'].str.replace(r'\.(xml|txt)$','',regex=True).map(LAB))
    show(nr2[['作品','kana_odoriji_expanded','kunoji_expanded',
              'unresolved_gaiji','speech_ratio']]
         .rename(columns={'kana_odoriji_expanded':'仮名踊り字',
                          'kunoji_expanded':'くの字点',
                          'unresolved_gaiji':'未解決外字',
                          'speech_ratio':'会話文比率'}),
         caption='踊り字の展開が多かった作品（上位10件）',
         fmt={'会話文比率':'{:.1%}'})
    print('**偏りが極端なら，文体差ではなく底本の正書法差である。**'
          '同じ作家の他の作品と比べること。')'''),
 ('md', r'''### 演習 — 枠の内と外で文体は違うか

『こころ』は上・中が「私」の語り，下が先生の遺書である。遺書は
`<quote type="embedded">` として切り出されているので，同じ作品の**内部で**
文体を対照できる。作家も時代も同じなのだから，差が出ればそれは
**枠構造そのものの効果**である。

`narration`（地の文）と `embedded`（埋め込みテクスト）を比べてみよう。
ここでは指標として，一人称代名詞の頻度と平均文長を使う。'''),
 ('code', r'''import re as _re

def profile(path):
    if not path.exists():
        return None
    t = path.read_text(encoding='utf-8')
    sents = [s for s in _re.split(r'[。！？]', t) if s.strip()]
    return {'chars': len(t),
            '私/万字': t.count('私') / max(1, len(t)) * 10000,
            '自分/万字': t.count('自分') / max(1, len(t)) * 10000,
            '平均文長': (sum(len(s) for s in sents) / len(sents)) if sents else 0}

ROWS = work_rows()          # 000148_000773 → メタデータの行（全件から）
LAB  = work_labels()        # 000148_000773 → 夏目漱石『こころ』

rows, miss = [], []
for f in sorted((PLAIN/'embedded').glob('*.txt')) if (PLAIN/'embedded').exists() else []:
    emb = profile(f)
    if not emb or emb['chars'] < 3000:        # 短すぎる切片は比較に耐えない
        continue
    nar = profile(PLAIN/'narration'/f.name)
    if not nar:
        continue
    stem = f.stem
    r = ROWS.get(stem)
    if r is None:
        miss.append(stem)
    # work_rows() が返すのは pandas の Series である。
    # **`r or {}` と書いてはいけない。** Series は真偽値に変換できず
    # ValueError になる（空でないかどうかが一意に決まらないため）。
    def col(name, default=''):
        return default if r is None else r.get(name, default)
    # ファイル名だけでは「誰の何の枠物語か」が分からず，
    # 「差は枠構造の効果か，埋め込みテクストのジャンルの効果か」という
    # 問いに答えようがない。作者・作品名・初出年を出す。
    rows.append({'作者': col('author_ja', '—'),
                 '作品': col('title_aozora', stem),
                 '初出': col('year_first'),
                 'embedded字数': emb['chars'], 'narration字数': nar['chars'],
                 '埋込比': round(emb['chars'] / max(1, emb['chars'] + nar['chars']), 3),
                 '私(emb)': round(emb['私/万字'],1), '私(nar)': round(nar['私/万字'],1),
                 '文長(emb)': round(emb['平均文長'],1),
                 '文長(nar)': round(nar['平均文長'],1),
                 'work_stem': stem})
if miss:
    print(f'[warn] メタデータに無い作品 {len(miss)} 件（語幹のまま表示）: '
          + '，'.join(miss[:5]))
if rows:
    show(pd.DataFrame(rows).sort_values('embedded字数', ascending=False)
           .drop(columns=['work_stem']),
         caption='枠の内（embedded）と外（narration）の比較',
         fmt={'embedded字数':'{:,.0f}','narration字数':'{:,.0f}',
              '埋込比':'{:.1%}','初出':'{:.0f}'})
    print('問い: 差が出た作品はどれか。その差は枠構造の効果と言えるか，')
    print('      それとも埋め込みテクストの**ジャンル**（書簡・手記）の効果か。')
else:
    print('embedded ストリームがありません。config/pipeline.yaml の streams を確認。')'''),
 ('md', r'''## 2. UniDic の語形 — どれを分析単位にするか

これは**結果を左右する決定**である。

| 素性 | 「噓を」 | 「渋江」 | 「云つた」 |
|---|---|---|---|
| surface（表層形） | 噓 | 渋江 | 云つ |
| orthBase（書字形基本形） | 噓 | 渋江 | 云う |
| **lemma（語彙素）** | 嘘 | **シブエ** | 言う |
| lForm（語彙素読み） | ウソ | シブエ | イウ |

`lemma` は異表記（噓／嘘，云う／言う）を統合するので**通時比較に向く**。
ただし**固有名詞では片仮名の読みになる**という UniDic の仕様がある。

本授業の既定（`--lemma-policy mixed`）:

```
固有名詞     → orthBase（なければ表層形）
それ以外     → lemma（なければ orthBase → 表層形）
```

加えて `私-代名詞` のような同形異義接尾辞は既定で落とす。'''),
 ('code', r'''import fugashi
# 辞書は自分で探さず，05_tokenise_unidic.py と**同じ規則**で解決する。
# ここで別の辞書を掴むと，このノートブックの表と data/tokens の中身が
# 違う辞書のものになる。（ファイル名が数字で始まるので import できない）
spec = importlib.util.spec_from_file_location(
    'tok', ROOT/'scripts'/'05_tokenise_unidic.py')
tok = importlib.util.module_from_spec(spec); spec.loader.exec_module(tok)

DIC, SRC = tok.resolve_dicdir(None)
NAME, VER = tok.identify_dict(DIC) if DIC else ('（未検出）', '')
tagger = fugashi.Tagger(f'-d {DIC}' if DIC else '')
print(f'辞書: {NAME} {VER}   ← {SRC}\n      {DIC}')
if NAME != tok.PROD_DICT:
    print(f'\n[warn] **本番の辞書ではない。** 本番は {tok.PROD_DICT} '
          f'{tok.PROD_VERSION}（docs/dictionary_comparison.md §10）。\n'
          f'       この辞書で出した数値は他の人の結果と比べられない。\n'
          f'       docs/00_setup_students.md §1.4 を見て入れ直すこと。')

s = 'ただ一人、しみじみと噓をついた。渋江抽斎の述志の詩である。'
rows = [{'surface':w.surface, 'pos1':w.feature.pos1, 'pos2':w.feature.pos2,
         'lemma':w.feature.lemma, 'orthBase':w.feature.orthBase,
         'lForm':w.feature.lForm} for w in tagger(s)]
show(pd.DataFrame(rows), caption=f'「{s}」の解析結果（{NAME} {VER}）')'''),
 ('md', r'''### 演習 1 — 方針を変えると結果がどう変わるか

同じテクストを4つの `lemma-policy` で解析し，異なり語数を比べよ。
**どれが最も語をまとめるか。まとめすぎて困るのはどんな場合か。**'''),
 ('code', r'''# tok は上のセルで読み込んである（05_tokenise_unidic.py）
demo = ('その男は云つた。「私は噓をつかない」と。渋江抽斎は述志の詩を作つた。'
        'ただしみじみと思ふ。人々は時々さう言ふ。')
for pol in ['surface','orthBase','lemma','mixed']:
    keys = [tok.lemma_key(w, pol) for w in tagger(demo)]
    print(f'{pol:<10} 異なり{len(set(keys)):>3}  {" ".join(keys[:22])}')'''),
 ('md', r'''## 3. どの辞書で解析するか — これは研究設計の決定である

**解析器（MeCab）と辞書（UniDic）は別物**で，辞書を替えれば結果は変わる。
国語研は変種ごとに別の辞書を配っている。本コーパスは 1872–1959 年の
口語小説が大半なので，**現代語の辞書が最善とはかぎらない**。

そこで 2026-09-22 に 4 辞書を 111 点で測り比べた。

| 辞書 | 未知語率 | 平均語長 | 助動詞率 |
|---|---:|---:|---:|
| **`unidic-novel`（近現代口語小説）← 本番** | **0.17%** | 1.575 | 10.9% |
| `unidic-qkana`（旧仮名口語） | 0.19% | 1.577 | 10.9% |
| `unidic-kindai-bungo`（近代文語）← 検算用 | 0.25% | 1.582 | **11.2%** |
| `unidic-cwj`（現代書き言葉） | 0.66% | 1.560 | 10.6% |

### ⚠ ここが勘所 — 未知語率だけを見てはいけない

辞書は**細かく刻めば未知語率を下げられる**。「知らない語」を
「知っている短い語2つ」に割れば，未知語は消えて見える。
だから未知語率と**平均語長**を必ず並べて見る。

上の表では，未知語率が最小の `novel` が cwj **より語が長い**
（1.575 > 1.560）。細かく分割して稼いだのではない，と言える。この確認をせずに
「未知語率が低いから良い辞書だ」と書くのは誤りである。

⚠ **判断の規則は測る前に決めておく。** 測ってから決めると，出た数字に
都合の良い規則を選ぶことになる。→ `docs/dictionary_comparison.md` §4

> **辞書は作品ごとに替えてはいけない。** 文語作品だけ近代文語 UniDic で
> 解析すれば，その作品の未知語率は下がる。しかし語彙素の体系が変わるので，
> **他の作品と混ぜて頻度比較できなくなる**。辞書はコーパス全体で統一し，
> 近代文語 UniDic は A_文語体の助動詞率の**検算**にのみ使う。

## 4. 形態素解析の実行'''),
 ('code', r'''TOK = ROOT/'data'/'tokens'
if need(PLAIN/'full', 'このステップの 04_normalise.py のセルを先に実行すること'):
    run_script('05_tokenise_unidic.py', '--in', PLAIN/'full', '--out', TOK,
               '--dicdir', DIC, '--lemma-policy', 'mixed')'''),
 ('md', r'''### 演習 2 — 未知語率で前処理を評価する

未知語率は**前処理の良し悪しを測る最良の単一指標**である。
高いファイルには理由がある。旧仮名か，外字が残っているか，漢文脈か。'''),
 ('code', r'''p = TOK/'tokenise_report.csv'
if need(p, '05_tokenise_unidic.py のセルを先に実行すること'):
    tr = pd.read_csv(p)
    meta = load_meta()
    LAB = work_labels()
    print('未知語率 中央値 = {:.2%}'.format(tr.unknown_rate.median()))
    top_unk = tr.nlargest(12,'unknown_rate')[['file','tokens','unknown_rate']].copy()
    # ファイル名のままでは「なぜ未知語が多いのか」を考えられない
    top_unk.insert(1, '作品',
                   top_unk['file'].str.replace(r'\.txt$','',regex=True).map(LAB))
    show(top_unk.drop(columns=['file']).rename(columns={'tokens':'語数',
                                                        'unknown_rate':'未知語率'}),
         caption='未知語率の高い作品（上位12件）',
         fmt={'語数':'{:,.0f}','未知語率':'{:.2%}'})

    # 未知語は「何が何回」だけでなく**上位が全体の何割を占めるか**を見る。
    # 上位20語で大半を占めるなら，直すべき対象は少数に絞れる。
    unk = pd.read_csv(TOK/'unknown_words.csv')
    u = unk.head(20).copy()
    u.insert(0, '順位', range(1, len(u)+1))
    u['累積割合'] = u.freq.cumsum() / unk.freq.sum()
    show(u.rename(columns={'surface':'語', 'freq':'頻度'}),
         caption=f'未知語 上位20（異なり {len(unk):,} 語・延べ {unk.freq.sum():,}）',
         fmt={'頻度':'{:,.0f}', '累積割合':'{:.1%}'})
    grid(unk.surface.head(60).astype(str), ncol=10,
         caption='未知語 上位60（種類を眺めるため）')'''),
 ('md', r'''**読み方。** 未知語の上位に来るものは5種類に分かれ，
**どれに当たるかで対処が違う。**

| 来るもの | 意味 | 対処 |
|---|---|---|
| **カタカナ外来語** | **正常**。外来語は開いた類で，辞書に全部は入らない | 何もしない |
| 人名・地名 | **正常**。固有名詞は辞書に限界がある | 何もしない |
| `ゝ` `ゞ` を含む列 | **正規化漏れ** | `04` の設定を見直す |
| `※` `〓` | **外字が復元できていない** | `03` の変換を見直す |
| 文語の活用形（`けれ` `ざり` `べかり`） | **辞書の被覆の問題** | 辞書の選定で解く（上の §3） |

### ⚠ 種類が変わると，未知語率の**意味**も変わる

前処理の失敗（踊り字・外字）が消えて**外来語だけが残った状態**では，
未知語率はもう「前処理の質」を測っていない。**外来語の密度**を
測っている。

そして外来語密度は**時代とジャンルの変数**である。昭和戦前の探偵小説・
SF（海野十三）は多く，明治の文語論説（福沢諭吉）はほぼ無い。

> **この転換点を見落とすと，海野十三の未知語率の高さを
> 「解析の失敗」と報告に書いてしまう。**

自分の未知語リストがどちらの段階にあるかは，目で見るのではなく数える。

```
python3 scripts/14_unknown_profile.py --unknown data/tokens/unknown_words.csv --report data/tokens/tokenise_report.csv
```

種類ごとの割合と，**カタカナ表記の揺れ**（`ラムプ`／`ランプ`，
`ヰスキー`／`ウイスキー`）の組も出る。揺れは同じ語を別の型として
数えるので異なり語数を膨らませるが，**まとめるかどうかは研究の問いに
よる。表記の揺れ自体が正書法の近代化の資料でもある。**'''),
 ('md', r'''## 5. データセットの構築 — 長さの偏りを吸収する

v1 の作品長は 30,555 語（鴎外『大塩平八郎』）から 502,937 語（藤村『夜明け前』）まで
**16.5倍**の開きがある。作品を1文書としてトピックモデルにかけると，
長篇1作が数トピックを独占する。

対策は2段構え。

1. 一定語数（既定 2,000 語）のチャンクに分割
2. 1作品あたりのチャンク数に上限を設ける（`--max-chunks`）'''),
 ('md', r'''### その前に — メタデータを増補分まで広げる

`metadata/corpus_metadata_v2.csv` は **v1 の 64 点**しか載っていない。
増補後のコーパスはそれより多いので，差分の作品には行が無い。行が無いと
次のチャンク分割でメタデータの突合が外れ，**`period` も `genre` も空の
チャンク索引**ができあがる。しかもエラーは出ない。そのまま進むと
時代別 keyness も doc2vec のカテゴリ効果もトピックの通時変化も，
**すべて空振りしたまま最後まで通ってしまう。**

`00_extend_metadata.py` が，青空文庫の索引（`fetch_log.csv`）・増補候補表・
**トークン列からの実測**を典拠ごとに分けて v3 を作る。書き出し先は
**`metadata/corpus_metadata_v3_local.csv`**（自分の版。git には入らない）で，
配布版の `corpus_metadata_v3.csv` は書き換えない（書き換えると `git pull` の
たびに衝突する）。以後のセルは自分の版を優先して読む。
実測に渡すのは `data/tokens/tokens_surface/` であって `data/plain/full/` ではない。
後者は分かち書きされていないので，`str.split()` が段落を語として数えてしまう。
**機械で決められない列（`narration`, `register_level`, `audience`, `form`）は
`TBD` のまま残る。** これは手抜きではなく，語りの視点や読者層は本文を読まないと
決まらないからである。`*_needs_review.csv` に一覧が出るので，
自分の分析に使う列だけでも埋めてから先へ進むこと。'''),
 ('code', r'''# --tokens には **分かち書きされた** トークン列を渡すこと。
# data/plain/full を渡すと，空白で区切られていないので段落数を語数として
# 数えてしまい，TTR が 1000 近くになる（スクリプトが検査して止める）。
# --remeasure-all は既存64点も同じ方法で測り直す。v1 のテクストには
# 外字欠落と奥付混入があるので，本来はこちらが正しい。
run_script('00_extend_metadata.py',
           '--meta', ROOT/'metadata'/'corpus_metadata_v2.csv',
           '--fetch-log', ROOT/'data'/'aozora'/'fetch_log.csv',
           '--candidates', ROOT/'metadata'/'expansion_candidates.csv',
           '--tokens', TOK/'tokens_surface',
           '--remeasure-all',
           '--out', ROOT/'metadata'/'corpus_metadata_v3_local.csv')
# 自分の v3。配布版 corpus_metadata_v3.csv は書き換えない（git pull で衝突しない）
META = ROOT/'metadata'/'corpus_metadata_v3_local.csv'   # 以後はこちらを使う'''),
 ('code', r'''DS = ROOT/'data'/'datasets'
if need(TOK/'tokens_content', 'このステップの 05_tokenise_unidic.py のセルを先に実行すること'):
    # --max-chunks は長篇の支配を防ぐための上限。結合後の『夜明け前』は
    # 約107チャンクあり，上限を外すと1作品でトピックを数本占有する。
    # 上限を超えた作品からは**層化無作為で**抜き，--seed で再現する。
    run_script('06_build_datasets.py', '--tokens', TOK/'tokens_content',
               '--meta', META, '--out', DS,
               '--chunk', 2000, '--max-chunks', 40,
               '--sample', 'stratified', '--seed', 20260920)'''),
 ('md', r'''### 抜き出しの再現性

上限を超えた作品からどのチャンクを残すかは，**結果に効く決定**である。
論文には最低でも `--chunk` `--max-chunks` `--seed` の3つを書くこと。

乱数シードは `f'{seed}:{作品の語幹}'` として**作品ごとに**作っている。
`random.seed()` を最初に一度だけ呼ぶ実装だと，各作品の標本は
「それまでに何回 random を呼んだか」に依存する。**作品を1点足しただけで，
後ろに並ぶ全作品の標本が入れ替わり，前回の結果と比べられなくなる。**
作品ごとにシードを作れば，コーパスの構成が変わっても同じ作品からは同じ
チャンクが選ばれる。

`--sample` には4つある。

| 値 | 何をするか | いつ使うか |
|---|---|---|
| `stratified` | 作品を40等分し，各区画から1つ無作為に抜く（既定） | 標準。冒頭・中盤・結末が必ず入る |
| `random` | 作品全体から無作為に抜く | 層化の必要がないとき |
| `spread` | 等間隔に抜く | 乱数を使いたくないとき |
| `head` | 先頭から | 使わない。冒頭に偏る |

既定を `stratified` にしたのは被覆のためである。単純無作為で107から40を
引くと，たまたま連続した区間が抜け落ちる（この設定では最大8チャンク＝
約16,000語）。物語の位置と主題が相関する長篇では，その区間の話題が
標本から丸ごと落ちる。層化すれば区間の抜けは最大5チャンクに収まり，
冒頭・中盤・結末が必ず標本に入る。

**層化しても無作為性は失われない**。区画の中でどのチャンクを引くかは
乱数が決めており，`spread`（等間隔＝決定論的）とは別物である。'''),
 ('code', r'''p = DS/'chunks_index.csv'
if need(p, '先に 06_build_datasets.py のセルを実行すること'):
    ci = pd.read_csv(p)
    print('チャンク総数:', len(ci))
    fig, axes = plt.subplots(1,2, figsize=(12,4))
    for ax, col in zip(axes, ['period','genre_sub']):
        vc = ci[col].value_counts().sort_index() if col=='period' else \
             ci[col].value_counts().head(12)
        ax.barh([str(i)[:22] for i in vc.index][::-1], vc.values[::-1],
                color=PALETTE[0])
        ax.set_title(f'チャンク数: {col}'); ax.spines[['top','right']].set_visible(False)
    fig.tight_layout(); save_fig(fig, 'Step3_chunks'); plt.show()'''),
 ('md', r"""## 5.5 KWIC コンコーダンサ — **テクストに戻る道具** ★★

数えたあとに本文へ戻れなければ，数えたことに意味は無い。**分析の最後の砦は
"back to texts" である。** 頻度表や word embedding は問いを作る道具で，答えは本文にある。

そこで，いま作った解析結果（`data/tokens/tsv`）から**索引**を作り，
ブラウザで使える KWIC コンコーダンサを起動する。

| できること | 中身 |
|---|---|
| **語彙素と表層形の切り替え** | 同じ解析結果の別の列。「語彙素で検索して表層形で読む」ができる |
| 品詞・活用形での指定 | `/動詞`・`言う/動詞`。`L:`／`S:` で項ごとに列を混ぜられる |
| 語の連なり | `汽車 に 乗る`。**文境界は越えない** |
| ワイルドカードと正規表現 | `乗*`・`*車`・`re:^汽.$` |
| **出典の表示** | 時代区分・著者・作品名（＋初出年・文体）。用例に出典が付く |
| 絞り込み | 時代区分・著者・文体・作品 |
| 並べ替え | 左1語・左1→2語・右1語・初出年・著者（本式のコンコーダンサと同じ） |
| 分布 | 作品別・時代区分別（**1万語あたりで正規化**） |
| 共起語 | LogDice・MI・t |
| **本文へ広げる** | 用例の行を押すと前後の文が開く。語にカーソルを当てると語彙素・品詞・活用形・語種が出る |
| 書き出し | CSV と，そのまま配れる HTML |

⚠ **索引は 05 を走らせ直したら作り直す。** 辞書を替えれば切り方が変わるので，
古い索引のままでは用例が本文と食い違う。索引には辞書名と版が刻まれていて，
画面の下に常に出る。**どの辞書で切った本文を読んでいるか分からない用例は，
証拠にならない。**"""),
 ('code', r'''# ---- 索引を作る（05 を走らせ直したら，これも作り直す）----------------
KW = ROOT/'data'/'kwic'
if need(TOK/'tsv', 'このステップの 05_tokenise_unidic.py のセルを先に実行すること'):
    run_script('15_kwic_index.py', '--tsv', TOK/'tsv', '--meta', META,
               '--out', KW, tail=2500)'''),
 ('md', r"""### 画面を起動する

次のセルは**別のプロセス**としてサーバを起動する（ノートブックは止まらない）。
出てくるリンクをクリックすると，ブラウザで開く。

サーバはカーネルを再起動しても止まらない。使い終わったら下の「止める」の
セルで `STOP_KWIC = True` にして実行する（**既定では止めない**。上から順に
実行したとき，起動した直後に止めてしまわないようにしてある）。

⚠ サーバは **127.0.0.1 でだけ待ち受ける**（このマシンからしか見えない）。
認証の無い簡易サーバなので，学内のネットワークに公開しないための仕様である。127.0.0.1 は
マシンごとに別のものなので，DH ラボの iMac で隣の人と同じ `PORT`（8765）を
使っても衝突しない。ただし，前にそのマシンを使った人がログアウトせずに離れる
（ファストユーザスイッチ）と，その人のサーバが 8765 に残っていることがある。
下のセルは**自分の索引のサーバかどうかを確かめ**，違えば 8766, 8767, … の
空いているポートを自動で使う。`PORT` を手で変える必要はない。"""),
 ('code', r'''# ---- 画面を起動する ----------------------------------------------------
# **ポートが開くまで待ってからリンクを出す。** 待たずにリンクを出すと，
# 索引を読んでいる最中にクリックして「サーバに接続できません」になる
# （実際にそうなった）。起動しなかったときはログをその場に出す。
import socket, subprocess, sys, time, urllib.request
from IPython.display import display, HTML
import getpass, json, os
PORT0 = 8765         # 既定のポート番号。使われていれば 8766, 8767, … の空きを自動で使う
KWIC_LOG = OUT/'kwic_server.log'
KWIC_PID = OUT/'kwic_server.pid'

def port_open(port, host='127.0.0.1', timeout=0.4):
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False

def mine(port):
    """そのポートのサーバが「自分の，この索引の」KWIC かどうか。

    127.0.0.1 はマシンの中の全ユーザーに共通なので，前の人のサーバ
    （ログアウトせずに離れた人のもの）が残っていることがある。"""
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/whoami', timeout=2) as r:
            w = json.loads(r.read().decode('utf-8'))
        return (w.get('user') == getpass.getuser()
                and w.get('index') == os.path.realpath(KW))
    except Exception:                                        # noqa: BLE001
        return False

# 自分のサーバが既に起動していればそれを使い，無ければ最初の空いているポートを使う
PORTS = range(PORT0, PORT0 + 20)
PORT = next((p for p in PORTS if port_open(p) and mine(p)), None)
REUSE = PORT is not None
if PORT is None:
    PORT = next((p for p in PORTS if not port_open(p)), None)

def show_link(port):
    display(HTML(f'<p style="font-size:1.05em">'
                 f'<a href="http://127.0.0.1:{port}/" target="_blank">'
                 f'KWIC コンコーダンサを開く（127.0.0.1:{port}）</a></p>'))
    print('リンクが開かないときは，ブラウザに '
          f'http://127.0.0.1:{port}/ を直接入れること。')

if REUSE:
    # 前に起動した自分のもの（カーネル再起動の前のものを含む）をそのまま使う
    print(f'[info ] すでに起動している（ポート番号 {PORT}）。止めるには下のセル。')
    show_link(PORT)
elif not (KW/'kwic_index.json').exists():
    print(f'[NG  ] 索引が無い: {KW}')
    print('       上の「索引を作る」セルを先に実行すること。')
elif PORT is None:
    print(f'[NG  ] ポート番号 {PORTS.start}–{PORTS.stop - 1} がすべて使われている。')
    print('       前の人のサーバが残っている可能性がある。いったんログアウトして'
          'ログインし直すか，TA に相談すること。')
else:
    if PORT != PORT0:
        print(f'[info ] ポート番号 {PORT0} は別のもの（前の人のサーバなど）が使っているので，'
              f'ポート番号 {PORT} を使う。')
    # -u: 出力を溜めずにログへ書く（溜めると動いていてもログが空に見える）
    # start_new_session: カーネルの中断・再起動に巻き込まれないようにする
    with open(KWIC_LOG, 'w', encoding='utf-8') as _log:
        KWIC_PROC = subprocess.Popen(
            [sys.executable, '-u', str(ROOT/'scripts'/'16_kwic_server.py'),
             '--index', str(KW), '--port', str(PORT)],
            stdout=_log, stderr=subprocess.STDOUT, text=True,
            start_new_session=True)
    KWIC_PID.write_text(f'{KWIC_PROC.pid} {PORT}\n', encoding='utf-8')
    t0 = time.time()
    ok = False
    while time.time() - t0 < 90:
        if KWIC_PROC.poll() is not None:
            break                       # 落ちた
        if port_open(PORT):
            ok = True
            break
        time.sleep(0.5)
        if int(time.time() - t0) and int(time.time() - t0) % 10 == 0:
            print(f'       …索引を読んでいる（{time.time() - t0:.0f} 秒）')
    if not ok:
        print('[NG  ] サーバが起動しなかった。ログの末尾:')
        print('-' * 60)
        print(KWIC_LOG.read_text(encoding='utf-8')[-2000:] or '（ログが空）')
        print('-' * 60)
        print('よくある原因: 索引が無い／ポートが使われている／'
              'Python が別の環境（sys.executable を確かめる）。')
        print(f'       sys.executable = {sys.executable}')
    else:
        print(f'[ok  ] 起動した（PID {KWIC_PROC.pid}・{time.time() - t0:.1f} 秒）'
              f'／ログ {KWIC_LOG}')
        show_link(PORT)'''),
 ('code', r'''# ---- 止める（使い終わったら STOP_KWIC = True にして実行する）----------
# 既定は False。上から順にセルを実行したとき，起動した直後に止めないため。
STOP_KWIC = False

import os, signal
KWIC_PID = OUT/'kwic_server.pid'
if not STOP_KWIC:
    print('[info ] 止めない（STOP_KWIC = False）。画面は起動したまま使える。')
    print('       使い終わったら STOP_KWIC = True にしてこのセルを実行する。')
else:
    pid = None
    proc = globals().get('KWIC_PROC')
    if proc is not None and proc.poll() is None:
        pid = proc.pid
    elif KWIC_PID.exists():                 # カーネルを再起動したあと
        pid = int(KWIC_PID.read_text().split()[0])
    if pid is None:
        print('[info ] 起動していない')
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f'[ok  ] 止めた（PID {pid}）')
        except ProcessLookupError:
            print('[info ] すでに止まっていた')
        KWIC_PID.unlink(missing_ok=True)'''),
 ('md', r"""### ノートブックの中でも引ける

画面を使わずに，**表として**用例を出すこともできる（レポートに貼るとき・
図と並べて見たいとき）。`show()` に渡せる表が返る。"""),
 ('code', r'''# ---- ノートブックの中で KWIC を引く ----------------------------------
from kwic_core import KwicIndex, QueryError
kwi = KwicIndex(KW)
p = kwi.prov
print(f'索引: 辞書 {p["dictionary"]} {p["dictionary_version"]}／'
      f'{p["works"]} 作品・{p["tokens"]:,} 形態素／作成 {p["built_at"]}')

QUERY, STREAM = '汽車', 'lemma'     # 'lemma'（語彙素）か 'surface'（表層形）
try:
    res = kwi.search(QUERY, stream=STREAM, context=7, limit=15, sort='year')
    show(kwi.to_frame(res),
         caption=f'「{QUERY}」の用例（{"語彙素" if STREAM == "lemma" else "表層形"}・'
                 f'全 {res["total"]:,} 件のうち {res["shown"]} 件・初出年順）',
         align={'左文脈': 'right', 'キーワード': 'center'})
    show(pd.DataFrame(res['by_band']).rename(columns={
            'label': '時代区分', 'hits': 'ヒット', 'tokens': '形態素',
            'per_10k': '1万語あたり'})[['時代区分', 'ヒット', '形態素', '1万語あたり']],
         caption='時代区分ごとの分布（**生のヒット数で時代を比べてはいけない**）',
         fmt={'ヒット': '{:,.0f}', '形態素': '{:,.0f}', '1万語あたり': '{:.3f}'})
except QueryError as e:
    # **黙って0件にしない。** 語彙に無い語形は理由が出る
    print(f'[検索式] {e}')'''),
 ('md', r"""### 演習 — 文語と口語の助動詞を**用例で**確かめる

Step 1 で `bungo_per10k`（文語助動詞の率）から文体を分類した。あれは数である。
**本当に文語の助動詞なのかを，用例で確かめる。**

1. 語彙素で `なり|けり|べし|ず` を引き，時代区分ごとの分布を見よ。
   数の上で減っているとして，**残っている用例はどういう文脈か**。
2. 同じ語を**表層形**で引き直し，何が変わるかを述べよ
   （「なり」は「なる」「なれ」と別の語になる。どちらの数字で議論すべきか）。
3. `L:なり /名詞` のように**列を混ぜた連なり**を作り，
   「なり」が助動詞ではなく名詞「成り」と解析されている例を探せ。
   **辞書の誤解析は，用例を読まなければ見つからない。**
4. 自分が Step 5 以降で追う語を5つ選び，それぞれ用例を10件読んでから
   仮説を書け。**数える前に読む**のが順序である。"""),
 ('md', r'''## 6. 検証 — 何が消え，何が残るのが正しいか

Step 1 で見た3つの重大な欠陥（重複・外字欠落・奥付混入）は消えているはずである。
ただし **FATAL 0 を目標にしてはいけない。** 検証スクリプトが出す警告には，
直すべきものと，直してはいけないものがある。

| 検査 | 出たら | 対処 |
|---|---|---|
| `duplicate` | 同一本文のファイルがある | **直す**。マニフェストを見直して取り直す |
| `gaiji_unresolved` (`〓`) | 実体が分からない外字がある | 注記を読んで対応字を探す。無ければそのまま。**件数と内訳を報告に書く** |
| `gaiji_marker` (`※`) | ※ が残っている | **判定する**。底本の記号（編者注・伏字）なら本文であって欠陥ではない |
| `colophon` | 奥付が本文に混入 | **直す**。`03` の本文抽出を見直す |
| `odoriji` | 踊り字が展開されていない | **直す**。`04` の設定を見直す |
| `length_skew` | 作品長の開きが大きい | 直さない。`--max-chunks` で吸収する（次節） |
| `too_short` | 本文が 3,000 字未満 | **確認する**。取得の失敗（本文抽出の打ち切り）か，本当に短い作品か |
| `length_skew_tokens` | 語数の開きが大きい | チャンク分割の判断はこちら。文字数ではなく語数で見る |

`gaiji_marker` を機械的に「欠陥」と数えると，海野十三の日記体作品の
編者注 `（※マリアナ基地からの…）` 100 箇所が誤って欠陥に数えられる。
**検証結果は読むものであって，0 にするものではない。**'''),
 ('code', r'''if need(PLAIN/'full', 'このステップの 04_normalise.py のセルを先に実行すること'):
    run_script('99_validate.py', '--corpus', PLAIN/'full',
               '--meta', META,
               '--tokenise-report', TOK/'tokenise_report.csv',
               '--out', OUT/'Step3_validation.csv')'''),
 ('md', r'''## 7. このステップの課題

次の設問への答えを，テンプレート `my_work/results/Step3_report.md` に書いて提出する（**全体で600–1000字程度**。図表と「再現のための情報」は字数に含めない）。

- **提出先**：Zulip（{ZULIP_ORG}）の非公開チャネル **{ZULIP_CHANNEL}** ＞ トピック **Step 3**
- テンプレートの中身をメッセージに貼り付け，図（SVG）・表（CSV）は**同じメッセージに添付**する（1人1通）
- 図は番号で言及し（図1），**図を見なくても論旨が追えるように**書く（SVG は Zulip で表示されないことがある）
- 再提出は元の投稿を直さず，同じトピックに新しく投稿する（手順書 §5.3）

1. 未知語率が高い上位3ファイルについて，**理由を特定**し，対処案を書くこと。
2. `lemma-policy` を `surface` と `mixed` で切り替え，
   同一作品の異なり語数がどれだけ変わるかを報告すること。
3. `--chunk` を 1000 / 2000 / 5000 と変え，チャンク数の分布がどう変わるか図示すこと。
   トピックモデルにとってどれが望ましいか，理由とともに述べること。
   **`--min-chunk` の既定は `--chunk` の半分**なので，`--chunk` を上げると
   1チャンクに満たない作品が増えて**コーパスから落ちる**。
   `works_too_short.csv` を見て，条件ごとに何点落ちたかも報告すること。
4. 検証ログを読み，上の表にしたがって**各警告を「直す／直さない」に分類**し，
   直さないものについてはその理由を1行で書くこと。FATAL 0 は目標ではない。
5. `narration_excludes_embedded` を `true` にして `04` を再実行し，
   『こころ』の `narration` の字数がどれだけ減るかを示すこと。
   どちらの設定が自分の問いに適しているかを述べること。
6. **（発展）辞書を2つ用意して `12_dict_compare.py` を回し**，
   未知語率・平均語長・境界一致率を層別に出すこと。
   `dict_disagreements.csv` から食い違いを**3例選んで原文と照らし**，
   どちらの切り方が自分の問いに適しているかを述べること。
   **未知語率が低いほうを無条件に選んではいけない**理由も書くこと。

### このステップの到達点（次へ進む条件）

- `data/plain/{full,narration,speech,embedded}/` がそろっている
- `data/tokens_*/` と `tokenise_report.csv` が出ており，未知語率を言える
- **`tokenise_provenance.json` に本番の辞書（`unidic-novel`）が記録されている**
- `data/datasets/chunks_index.csv` があり，時代別チャンク数の偏りを把握している
- 検証の警告を一つずつ読み，直すもの・直さないものを仕分けてある
'''),
])

# ==========================================================================
L(4, '記述統計と文体計量 — MFW・Delta・PCA・特徴語', [
 ('md', r'''# Step 4 記述統計と文体計量 — MFW・Delta・PCA・特徴語

## このステップの到達目標

1. 最頻語（MFW）による文体計量の考え方を説明できる
2. Burrows's Delta を自分で実装・解釈できる
3. 対数尤度比による特徴語抽出ができ，その限界を言える
4. **word embedding に進む前に，頻度で何が見えるかを確定させる**

## 導入：なぜ最頻語なのか

文体計量（stylometry）の中心的な発見は逆説的である。

> 作者を最もよく識別するのは，**内容と無関係な機能語**の頻度である。

「の」「は」「て」「が」の使用比率は，作家ごとに驚くほど安定し，
主題が変わっても変わらない。だから作者推定に使える。
逆に，**時代やジャンルを見たいときは，機能語が作家効果を持ち込む**。

本コーパスは**同じ作家の作品を複数含む**（森鴎外7点，岡本綺堂6点，
坂口安吾・海野十三・島崎藤村が各5点…）。作家効果が強く出る構造であり，
時代やジャンルの効果を見たいときはこれが交絡する。
なお島崎藤村が9点に見えていたのは『夜明け前』4冊・『家』2冊を
別作品として数えていたからで，Step 2 の分冊結合で解消してある。
このステップで「何が作家由来で，何が時代由来か」を切り分ける感覚を作る。

## 参考
- Burrows, J. (2002) 'Delta': a measure of stylistic difference. *LLC* 17(3).
- Evert, S. et al. (2017) Understanding and explaining Delta measures. *DSH* 32.
- 金明哲 (2021)『テキストアナリティクス』共立出版
'''),
 ('code', PREAMBLE),
 ('code', r'''TOK = ROOT/'data'/'tokens'
SRC = TOK/'tokens_lemma'
if not SRC.exists():
    print('Step 3 の出力がありません。')
else:
    run_script('07_descriptive_stats.py', '--tokens', SRC, '--tsv', TOK/'tsv',
               '--meta', META,
               '--out', OUT/'descriptive', '--mfw', 300)'''),
 ('md', r'''## 1. 語彙の豊かさ — TTR の罠

Type-Token Ratio（異なり語数 ÷ 延べ語数）は直感的だが，
**標本サイズに強く依存する**。長い作品ほど TTR は必ず下がる。

v1 の実測がその典型である。

| 作品 | 語数 | TTR×1000 |
|---|---:|---:|
| 海野十三『敗戦日記』 | 50,131 | 169.5 |
| 藤村『夜明け前』 | 502,937 | **45.2** |

『夜明け前』の語彙が貧しいのではない。長いだけである。
標本サイズに頑健な指標を使う。

- **Guiraud's R** = V / √N
- **Yule's K**（頻度分布の集中度。長さにほぼ依存しない）
- **エントロピー**（bit）'''),
 ('code', r'''p = OUT/'descriptive'/'work_profile.csv'
if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    wp = pd.read_csv(p)
    fig, axes = plt.subplots(1,3, figsize=(14,4))
    for ax, y, lab in zip(axes, ['ttr','guiraud_R','yule_K'],
                          ['TTR','Guiraud R','Yule K']):
        ax.scatter(wp.tokens, wp[y], s=30, color=PALETTE[0], alpha=.8)
        ax.set_xscale('log'); ax.set_xlabel('延べ語数（対数）'); ax.set_ylabel(lab)
        rho = np.corrcoef(np.log(wp.tokens), wp[y])[0,1]
        ax.set_title(f'{lab}  (r={rho:+.2f})')
        ax.spines[['top','right']].set_visible(False)
    fig.tight_layout(); save_fig(fig, 'Step4_lexical_richness'); plt.show()
    print('→ 相関の絶対値が小さい指標ほど，長さに頑健である。')'''),
 ('md', r'''## 2. Burrows's Delta を手で計算する

Delta の手順は三つしかない。

1. 最頻語 N 語の**相対頻度**を求める（ここでは 1 万語あたり）
2. 語ごとに **z スコア**にする（頻度の大小によらず各語を等しく扱う）
3. 2 つのテクストの z の**差の絶対値を，語について平均**する——これが距離

単純さが強みで，少ない訓練データでもよく効く。ただしノートブックでは
`np.abs(Z[:,None]-Z[None]).mean(2)` の1行になってしまい，**何をしているかが
見えない**。そこで二段で進める。

| | どこで | 規模 | 目的 |
|---|---|---|---|
| **2.1** | Excel | 最頻語 50 語 × 9 作品 | 1 手順＝1 シート。セルと数式で計算の中身を見る |
| **2.2** | ノートブック | 同じ 50 語 × 9 作品 | 同じ数値を pandas で出し，**Excel と一致する**ことを確かめる |
| **2.3** | — | — | 作品X の正体と，結果の読み方 |
| **2.4** | ノートブック | 300 語 × 全作品 | Excel では扱えない規模に広げる |

作家を伏せた「作品X」1 点と，既知の 4 作家（各 2 点）を比べる。
既知の作家には，作品X と同時代の作家と，時代も読者層も離れた作家とが
混ざっている。**遠い理由が作家の違いだけとは限らない**ことにも注意する。
**作品X が誰の作品かは 2.3 まで見ないこと。**'''),
 ('md', r'''### 2.1 Excel で手計算する

次のセルが `my_work/results/Step4_delta_manual.xlsx` を作る。Excel（Numbers・
LibreOffice でもよい）で開き，`説明` シートから順に見ていく。

- **最頻語・平均・標準偏差は既知の作品だけから決める。** 作品X に物差しを
  動かさせないためである（Burrows 2002）
- 標準偏差は**母標準偏差**（Excel の `STDEVP`，numpy の `ddof=0`）。
  `07_descriptive_stats.py` も同じ
- シートを**並べ替えないこと**（数式の参照が崩れる）'''),
 ('code', r'''# ---- 手計算用のブックを作る ------------------------------------------
# 作品を変えるときは '--questioned 作家:題' '--known 作家:題,作家:題,…' を足す
DX = OUT/'Step4_delta_manual.xlsx'
run_script('17_delta_workbook.py', '--tokens', SRC, '--meta', META,
           '--mfw', 50, '--out', DX)'''),
 ('md', r'''#### 演習 0 — Excel の上で（15 分）

1. `6_Delta` で，作品X に**いちばん近い作品**と**いちばん近い作家プロファイル**を
   書き留める。両者は一致するか
2. `5_z差` で色の濃い（差の大きい）語を 3 つ挙げる。それは作家の癖か，
   **語りの人称や登場人物名**など別の要因か
3. `7_語数Nを変える` の黄色のセルに 10・20・50 を入れ，順位の変わり方を見る
4. `1_度数` の数字を 1 つ書き換え，影響がどのシートまで伝わるかを追う
   （試したら元に戻すか，上のセルでブックを作り直す）'''),
 ('md', r'''### 2.2 同じ計算をノートブックで

Excel の `1_度数` から**入力値だけ**を読み，同じ手順を pandas で1行ずつ書く。
変数名はシート名に合わせてある。最後に Excel の結果と突き合わせる。'''),
 ('code', r'''# ---- Excel と同じ計算を pandas で ------------------------------------
import json, openpyxl
if need(DX, '上のセルでブックを作ること'):
    ws = openpyxl.load_workbook(DX)['1_度数']
    R_AU, R_TI, R_N, R0 = 3, 4, 5, 6          # ブックの配置（17_delta_workbook.py と同じ）
    ncol = ws.max_column
    names   = [ws.cell(R_TI, c).value for c in range(3, ncol + 1)]
    authors = [ws.cell(R_AU, c).value for c in range(3, ncol + 1)]
    words, rows = [], []
    r = R0
    while ws.cell(r, 2).value is not None:
        words.append(ws.cell(r, 2).value)
        rows.append([ws.cell(r, c).value for c in range(3, ncol + 1)])
        r += 1
    counts = pd.DataFrame(rows, index=words, columns=names)            # 1_度数
    totals = pd.Series([ws.cell(R_N, c).value for c in range(3, ncol + 1)], index=names)
    X = names[-1]                                                      # 作品X
    known = names[:-1]
    author_of = dict(zip(known, authors[:-1]))

    rel  = counts / totals * 10000                                     # 2_相対頻度
    mu   = rel[known].mean(axis=1)                                     # 3_平均と標準偏差
    sd   = rel[known].std(axis=1, ddof=0)                              #   STDEVP と同じ
    z    = rel.sub(mu, axis=0).div(sd, axis=0)                         # 4_zスコア
    diff = z[known].rsub(z[X], axis=0).abs()                           # 5_z差
    delta = diff.mean()                                                # 6_Delta

    prof = z[known].T.groupby(author_of).mean().T                      # 作家プロファイル
    delta_a = prof.rsub(z[X], axis=0).abs().mean()

    t = pd.DataFrame({'作家': [author_of[k] for k in known], '作品': known,
                      'Delta': delta.values}).sort_values('Delta')
    t.insert(0, '順位', range(1, len(t) + 1))
    show(t, caption=f'{X} との Delta（作品ごと・{len(words)} 語）', fmt={'Delta': '{:.4f}'})
    ta = delta_a.sort_values().rename('Delta').reset_index().rename(columns={'index': '作家'})
    ta.insert(0, '順位', range(1, len(ta) + 1))
    show(ta, caption=f'{X} との Delta（作家プロファイル）', fmt={'Delta': '{:.4f}'})

    # ---- Excel と突き合わせる ---------------------------------------------
    # Excel で開いて「保存」すると計算結果がファイルに残るので，それを読む。
    # 保存していなければ値が無い（数式しか入っていない）。
    ws6 = openpyxl.load_workbook(DX, data_only=True)['6_Delta']
    xl = {ws6.cell(r, 3).value: ws6.cell(r, 4).value
          for r in range(5, 5 + len(known)) if ws6.cell(r, 1).value == '作品'}
    if all(v is None for v in xl.values()):
        print('[info ] ブックに計算結果が保存されていない。Excel で開いて保存してから'
              'このセルを実行し直すと，自動で突き合わせる。')
        print('        それまでは 6_Delta の D 列と上の表を目で比べること。')
    else:
        gap = max(abs(xl[k] - delta[k]) for k in known if xl.get(k) is not None)
        print(f'[{"ok  " if gap < 1e-6 else "NG  "}] Excel との差の最大 = {gap:.2e}'
              + ('（一致）' if gap < 1e-6 else ' — 1_度数 を書き換えたままではないか'))'''),
 ('md', r'''### 2.3 作品X の正体と，結果の読み方

次のセルで作品X を明かす。**演習 0 の答えを書き留めてから実行すること。**'''),
 ('code', r'''exp = json.loads((DX.parent / (DX.stem + '_expected.json')).read_text(encoding='utf-8'))
qx = exp['questioned']
print(f"作品X ＝ {qx['author']}『{qx['title']}』")
best_w = min(exp['delta_work'], key=exp['delta_work'].get)
best_a = min(exp['delta_author'], key=exp['delta_author'].get)
print(f'いちばん近い作品          : {best_w}')
print(f'いちばん近い作家プロファイル: {best_a}'
      + ('  ← 正解' if best_a == qx['author'] else '  ← 外れ'))'''),
 ('md', r'''**読み方の要点**

- **作品単位と作家単位で答えが変わりうる。** 同じ作家でも作品ごとに
  語りの人称（一人称の「私」）や文体が違う。1 点ずつ比べると，たまたま
  似た**別の作家の作品**が割り込む。作家の2作品の z を平均した**プロファイル**
  は，作品ごとの揺れをならす
- **近さの理由を必ず語で確かめる。** `5_z差` で差を作っている語が，
  登場人物名（最頻語に固有名詞が紛れ込む）や「私」のような人称なら，
  Delta は作家ではなく**語りの形式**を測っている
- **N を変えると順位が動く。** 10 語では少数の語に振り回される。
  報告には必ず N を書き，いくつかの N で結論が変わらないことを示す
- **候補に真の作者がいなくても，いちばん近い誰かは必ず出る。**
  Delta は「この中で誰に近いか」しか答えない'''),
 ('md', r'''### 2.4 全作品・300 語に広げる

ここからは Excel では扱えない規模である。手順は 2.2 とまったく同じで，
作品 × 作品のすべての組について Delta を出す（全作品で z を取るので，
2.1–2.2 のような「既知／問題」の区別はない）。'''),
 ('code', r'''p = OUT/'descriptive'/'freq_matrix_mfw.csv'
if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    F = pd.read_csv(p, index_col=0)
    # ddof=0（母標準偏差）。07_descriptive_stats.py・Excel の STDEVP と揃える
    Z = (F - F.mean()) / F.std(ddof=0).replace(0, 1e-12)
    D = pd.DataFrame(
        np.abs(Z.values[:,None,:] - Z.values[None,:,:]).mean(2),
        index=F.index, columns=F.index)
    meta = load_meta()
    ROWS = work_rows(meta)           # 000119_001743 → メタデータの行
    LAB  = work_labels()             # 000119_001743 → 中島敦『光と風と夢』
                                     # ラベルは分析対象外の作品も引けるよう全件から

    miss = [s for s in D.index if s not in LAB]
    if miss:
        print(f'[warn] メタデータに無い作品 {len(miss)} 件（ファイル名のまま表示）: '
              + '，'.join(miss[:5]))

    def au(s):
        r = ROWS.get(s)
        return None if r is None else r.get('author_ja')

    pairs = [(a,b,D.loc[a,b]) for i,a in enumerate(D.index) for b in D.index[i+1:]]
    near = pd.DataFrame(
        [{'順位': i+1, 'Delta 距離': d,
          '作品 A': LAB.get(a,a), '作品 B': LAB.get(b,b),
          '同一作家': '●' if au(a) is not None and au(a)==au(b) else ''}
         for i,(a,b,d) in enumerate(sorted(pairs, key=lambda t:t[2])[:12])])
    show(near, caption='最も近い作品ペア（Delta 距離の小さい順・上位12）',
         fmt={'Delta 距離': '{:.4f}'})
    n_same = (near['同一作家'] == '●').sum()
    print(f'上位12ペアのうち同一作家が {n_same} 組。'
          'ここが多いほど，Delta は作家を測っている。')'''),
 ('md', r'''### 演習 1 — 作家効果 vs 時代効果

最近傍が**同じ作家**である割合と，**同じ時代**である割合を比べよ。
どちらが大きいか。それは何を意味するか。'''),
 ('code', r'''if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    # キーの 0 埋めと file_v1 の欠損は work_rows() が面倒を見る。
    # ここで自前に辞書を作ると，増補45点の file_v1 が空なので落ちるか，
    # 0 埋めの違いで**1件も一致しないまま割合だけ出る**。
    labs = work_rows(meta)
    have = [f for f in D.index if f in labs]
    if len(have) < len(D.index):
        print(f'[warn] メタデータに無い作品 {len(D.index)-len(have)} 件を除いて集計')
    if not have:
        print('[FATAL] 1件も突合できていない。割合を読んではいけない')
    else:
        # 最近傍は作品ごとに1回求めれば済む（観点ごとに引き直さない）
        nn = {f: D.loc[f, [x for x in have if x != f]].idxmin() for f in have}
        FIELD_JA = {'author_ja':'作家', 'period':'時代', 'genre_sub':'ジャンル',
                    'register_level':'文体の階層', 'narration':'語り'}
        rows = []
        for field, ja in FIELD_JA.items():
            hit = sum(labs[nn[f]][field] == labs[f][field] for f in have)
            rows.append({'観点': ja, '列名': field,
                         '一致': hit, '作品数': len(have),
                         '一致率': hit/len(have)})
        t = show(pd.DataFrame(rows).sort_values('一致率', ascending=False),
                 caption='最近傍が同じ属性を持つ割合（Delta・1近傍）',
                 fmt={'一致率': '{:.1%}'})
        top = t.iloc[0]
        print(f'最も一致しやすいのは「{top["観点"]}」（{top["一致率"]:.1%}）。'
              'これがコーパスで最も強い信号である。')'''),
 ('md', r'''## 3. 主成分分析 — 何が第1主成分か

MFW の z 行列を主成分分析すると，第1・第2主成分に何が現れるか。
**時代か，ジャンルか，作家か，文語/口語か。**

散布図を4通りの色分けで描き，どの軸が最も分離しているかを見る。

### 色分けは，変数の種類で変える

4面のうち **`period` の面だけ1色相の濃淡**（薄い水色 → 濃紺の5段）で色分けする。
理由は Step 1 の散布図と同じである。

- 時代は**順序のある変数**である。色相環の8色で色分けすると，明治中期が青・
  明治後期が黄のように隣り合う時代が隣り合う色にならず，
  **「時代が下るとどちらへ動くか」という肝心のことが読めない**
- 濃淡なら近い時代が近い色になり，勾配がそのまま目に入る
- 段数は**5段**。1色相の濃淡で順序を見せるには隣の段の明度差が 0.06 以上
  要り，白地の散布図で使える青の幅では6段取るとどこかが見分けられなくなる
  （`year_bands()` の説明を見よ）

色の濃淡だけでは動きの向きが読みにくいので，**各段の中央値を結んだ矢印**を
重ねる。

⚠ **この5段の色は全 Step で同じ時代を指す。** `year_bands()` を通すのは
そのためで，Step 1 の `Step1_bungo_kogo.svg` と並べて読める。

**濃淡を使うのは `period` の面だけである。** `style_class` も順序のある
変数だが，青の濃淡は「時代」を指す約束にしてあるので，別の変数に流用すると
図をまたいだ読みが壊れる。残りの3面は色相で色分けする。'''),
 ('code', r'''p = OUT/'descriptive'/'pca_coordinates.csv'
if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    pc = pd.read_csv(p)

    # **色分けする軸は，図の側で宣言して取りに行く。**
    # 07 の突合が外れると year_first も period も空のまま CSV に入る。
    # それを黙って色分けすると，62 点が「初出年不明」の灰色で並ぶ（実際に起きた）。
    # attach_meta は空のセルもメタデータから補い，補えなかった件数を出す。
    pc = attach_meta(pc, ['year_first','period','style_class',
                          'genre_sub','author_ja'], stem_col='work')

    # 補ってもなお空が残るなら，メタデータ側の空きと突き合わせて言い切る。
    # **凡例の「初出年不明」は，作品の性質ではなく突合の失敗**であることが
    # 多い。両者を混同しないために，ここで数える。
    _yr = pd.to_numeric(pc.year_first, errors='coerce')
    n_nan = int(_yr.isna().sum())
    if n_nan:
        _m = load_meta()
        n_meta_blank = int(pd.to_numeric(_m.year_first, errors='coerce')
                           .isna().sum())
        print(f'[warn] 初出年が読めない作品が {n_nan}/{len(pc)} 件ある。'
              f'メタデータ側で空なのは {n_meta_blank} 件。')
        if n_nan > n_meta_blank:
            print('       **差は突合の失敗である。** メタデータには年がある。')
            print('       07_descriptive_stats.py を今の版で走らせ直すこと'
                  '（キーの 0 埋めの綴り違いを直してある）。')
            print('       それでも残るなら '
                  'results/descriptive/meta_unmatched.csv を見る。')

    fig, axes = plt.subplots(2,2, figsize=(13,10))

    # ---- 第1面：時代は「順序」なので1色相の濃淡で色分けする ---------------------
    ax = axes[0,0]
    code, blabels, bcolors = year_bands(pc.year_first)
    # 初出年が読めないものは -1。**いちばん古い段に混ぜない**（灰で別扱い）
    unk = code < 0
    if unk.any():
        ax.scatter(pc.PC1[unk], pc.PC2[unk], s=22, color='#cccccc',
                   edgecolor='white', linewidth=.5,
                   label=f'初出年不明（{int(unk.sum())}）')
    for b, (lab, col) in enumerate(zip(blabels, bcolors)):
        m = code == b
        if not m.any():
            continue
        ax.scatter(pc.PC1[m], pc.PC2[m], s=42, alpha=.9, color=col,
                   edgecolor='white', linewidth=.5,
                   label=f'{lab}（{int(m.sum())}）')
    # 平均ではなく中央値。外れ値1点で軌跡の向きが変わるのを防ぐ。
    mx = [pc.PC1[code==b].median() for b in range(len(blabels))]
    my = [pc.PC2[code==b].median() for b in range(len(blabels))]
    pts = [(x,y) for x,y in zip(mx,my) if np.isfinite(x) and np.isfinite(y)]
    for (x0,y0),(x1,y1) in zip(pts, pts[1:]):
        ax.annotate('', xy=(x1,y1), xytext=(x0,y0),
                    arrowprops=dict(arrowstyle='-|>', color='#5a5a55', lw=1.2,
                                    shrinkA=7, shrinkB=7, alpha=.9))
    # 面の名は「period」ではなく「初出年の5段」。period 列は6区分だが，
    # 作品数3点の明治前期を明治中期に畳んで5段にしてある（year_bands）。
    ax.set_title('period → 初出年の5段（濃いほど新しい／矢印は段の中央値）')
    # 凡例は点の上に重なりうるので，白の半透明を敷いて文字を読めるようにする
    ax.legend(fontsize=6.5, ncol=1, loc='best', frameon=True,
              framealpha=.85, edgecolor='none')

    # ---- 残り3面：順序の無い変数なので色相で色分けする --------------------------
    for ax, field in zip(axes.ravel()[1:],
                         ['style_class','genre_sub','author_ja']):
        keys = pc[field].value_counts().index[:8]
        for i,k in enumerate(keys):
            d = pc[pc[field]==k]
            ax.scatter(d.PC1, d.PC2, s=38, alpha=.85,
                       color=PALETTE[i%len(PALETTE)], label=str(k)[:18],
                       edgecolor='white', linewidth=.5)
        rest = pc[~pc[field].isin(keys)]
        if len(rest): ax.scatter(rest.PC1, rest.PC2, s=18, color='#cccccc', label='その他')
        ax.set_title(field); ax.legend(frameon=False, fontsize=7, ncol=2)

    for ax in axes.ravel():
        ax.axhline(0,color='#ddd',lw=.8); ax.axvline(0,color='#ddd',lw=.8)
        ax.set_xlabel('PC1'); ax.set_ylabel('PC2')
        ax.spines[['top','right']].set_visible(False)
    fig.suptitle('MFW-PCA：どの軸が第1・第2主成分を説明するか', y=1.01)
    fig.tight_layout(); save_fig(fig, 'Step4_pca_four'); plt.show()

    # 目で見た勾配を数字で裏づける。**図だけで「時代の軸だ」と言わない。**
    def mono(vals):
        v = [x for x in vals if np.isfinite(x)]
        up = all(a <= b for a,b in zip(v, v[1:]))
        dn = all(a >= b for a,b in zip(v, v[1:]))
        return '単調' if (up or dn) else '単調でない'

    yr = pd.to_numeric(pc.year_first, errors='coerce')
    show(pd.DataFrame([
            {'主成分':'PC1', '初出年との ρ': pc.PC1.corr(yr, method='spearman'),
             '段の中央値': mono(mx)},
            {'主成分':'PC2', '初出年との ρ': pc.PC2.corr(yr, method='spearman'),
             '段の中央値': mono(my)}]),
         caption='主成分と初出年の関係（Spearman の順位相関）',
         fmt={'初出年との ρ':'{:+.3f}'})
    print('|ρ| が大きい主成分が「時代の軸」である。段の中央値が'
          '**単調でない**なら，濃淡の勾配は見かけだけかもしれない。')
    print('**いずれにせよ作家効果と交絡している。** 演習 1 の一致率と併せて読む。')'''),
 ('md', r'''## 4. 特徴語 — 対数尤度比 G²

2つのコーパスで語の頻度を比べる標準的な方法。

$$G^2 = 2\left(a\ln\frac{a}{E_1} + b\ln\frac{b}{E_2}\right)$$

**注意点が3つある。**

1. G² は**標本サイズに比例して大きくなる**。大きいコーパスでは些細な差も有意になる。
   必ず**効果量**（log ratio）を併記する。
2. 1作家に偏った時代では，**その作家の固有名詞**が上位に来る。
   「時代の特徴語」ではなく「特定作家の語彙」を見ていないか確認する。
3. **G² は「どこで多いか」を見ない。** 頻度の合計しか見ないので，
   1作品に固まって出る語と，全作品に薄く広がる語を区別できない。
   これは 4.1 で扱う。'''),
 ('code', r'''p = OUT/'descriptive'/'keyness_by_period.csv'
if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    ky = read_table(p)
    # 散らばりの列は 07 が書く（df_all_prop / dp_in / top_work_share）。
    # 古い CSV には無いので，無ければ 4.1 以降が動かないことを先に言う。
    DISP = ['df_all_prop', 'df_in_prop', 'dp_in', 'top_work_share', 'top_work']
    lack = [c for c in DISP if c not in ky.columns]
    if lack:
        print('[warn] 散らばり（ディスパーション）の列が無い: ' + '，'.join(lack))
        print('       古い 07_descriptive_stats.py で作った CSV である。')
        print('       上の「07 を走らせるセル」を実行し直すこと。'
              '（下の G² の表は列が無くても出る）\n')

    # **時代を列にした表にする。** 時代ごとに行を流すと，同じ語が
    # どの時代にも出ていることに気づけない。列に並べれば横に読める。
    TOPK0 = 15
    cols = {}
    for per in sorted(ky.period.unique()):
        d = ky[(ky.period==per)&(ky.G2>0)].nlargest(TOPK0,'G2')
        cols[per] = [f'{r.term} {r.G2:.0f}' for _,r in d.iterrows()] \
                    + [''] * (TOPK0 - len(d))
    kt = pd.DataFrame(cols, index=[f'{i+1}' for i in range(TOPK0)])
    kt.index.name = '順位'
    # 有意語が 15 に満たない時代があると空行が並ぶので，全列が空の行は落とす
    kt = kt[(kt != '').any(axis=1)]
    show(kt.reset_index(), caption=f'時代ごとの特徴語 上位{TOPK0}（語と G²）')

    # 語だけの表は「どの時代にも出る語」を見落としやすいので，
    # 複数の時代で上位に入った語を名指しする。
    shared = Counter(x.split(' ')[0] for v in cols.values() for x in v if x)
    dup = {w: n for w, n in shared.items() if n > 1}
    if dup:
        print('2つ以上の時代で上位に入った語: '
              + '，'.join(f'{w}({n}時代)' for w, n in
                          sorted(dup.items(), key=lambda x: -x[1])))
        print('**同じ語が複数の時代で「特徴語」になるのは，比較の相手が'
              '「残り全部」だからである。**隣の時代とだけ比べれば消えることがある。')'''),
 ('md', r'''### 4.1 culling — G² が見ていないもの

上のリストは「その時代に頻度が偏っている語」である。しかし
**偏りには2種類ある。**

次の2語を考える。どちらも明治後期の全体で 160 回，他の時代では 0 回とする。

| 語 | 出かた |
|---|---|
| **A** | 明治後期の 8 作品すべてに 20 回ずつ |
| **B** | 明治後期の **1 作品だけ**に 160 回。残りの 7 作品には 0 回 |

G² は総頻度と総語数だけから計算されるので，**この2語の G² は完全に同じ
値になる**（実際に人工データで確かめると，どちらも G²=221.01，
log ratio=18.92 になる）。しかし意味はまったく違う。

- **A は evenly distributed** — 「明治後期の書き方」と呼べる
- **B は bursty** — 「その1作品の語彙」であって時代の特徴ではない

**G² はこの区別をしない。** だから G² のリストを見るだけでは，
時代の特徴を見ているのか，数点の作品を見ているのか分からない。

#### culling とは

そこで**語を絞る**。文体計量でいう *culling*（Eder らの用語）は，
**一定割合の文書に現れない語を特徴量から外す**操作である。

> **culling at 10%** = コーパス全体の文書頻度（df）が 10% 未満の語を弾く

本コーパスは 101 点なので，**11 点未満に出る語を捨てる**ことになる。

⚠ **culling は G² の値を変えない。** 語を1つ落としても，他の語の
`a, b, c, d` は変わらないからである（`c, d` は総語数で，特徴量の集合とは
無関係）。変わるのは**リストの中身と順位**だけである。
「culling したら G² が動いた」なら，語ではなく**トークンを消してしまって
いる**（総語数が変わっている）。その2つは別の操作である。

#### 何で bursty さを測るか

`07_descriptive_stats.py` は次の列を書き出す。**df は粗い指標**
（出たか出ないかの 0/1 しか見ない）なので，併せて読む。

| 列 | 何を測るか | bursty なら |
|---|---|---|
| `df_all_prop` | 全 101 点のうち何割に出るか | **小さい** |
| `df_in_prop` | その時代の作品のうち何割に出るか | 小さい |
| `dp_in` | **その時代の中での** Gries の DP（偏差の総和の半分） | **1 に近い** |
| `top_work_share` | 総頻度のうち最多の1作品が占める割合 | 1 に近い |
| `top_work` | その1作品はどれか | — |

**割合の列はすべて 0–1 である**（`df_all_prop` = 0.1584 は 15.84 % の意）。
このパイプラインでは，0–1 の割合を `_prop` / `_ratio` / `_share`，
0–100 の百分率だけを `_pct` と綴り分ける。名前を見れば尺度が決まる。
表計算ソフトで開いて「0.15」と「15」を取り違えないための約束である。

⚠ `dp_gries`（コーパス全体の DP）を bursty 判定に使わないこと。
**ある時代だけに出る語は，その時代の全作品に均等に出ていても
全体では「偏った」語**になり，DP が 0.5 前後になる。しかしその偏りは
時代の特徴語として**望ましい**偏りである。全体の DP は
「時代に偏る」と「1作品に偏る」を区別できない。分母をその時代に限った
`dp_in` を使う。（上の A は `dp_in`=0.004，B は 0.871 になる。
`dp_gries` はどちらも 0.50 前後で，区別できない。）'''),
 ('code', r'''# ---- culling at df < 10% と，しない場合を並べる ------------------------
CULL = 0.10      # コーパス全体の df がこの割合未満の語を弾く
TOPK = 15

p = OUT/'descriptive'/'keyness_by_period.csv'
if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    ky = read_table(p)
    if 'df_all_prop' not in ky.columns:
        print('[warn] df_all_prop 列が無い（旧名 df_all_pct も無い）。'
              '07 を走らせるセルを実行し直すこと。')
    else:
        # **語幹（000160_003368）では誰の何だか分からない。**
        # 「この語はこの作品のものだ」と言えて初めて bursty の話が通じる。
        LAB4 = work_labels()          # 語幹 → 作家『作品』
        def _w4(x):
            x = str(x)
            return LAB4.get(x, x)     # 引けないときは語幹のまま出す
        for per in sorted(ky.period.unique()):
            a = ky[(ky.period==per)&(ky.G2>0)]
            b = a[a.df_all_prop >= CULL]
            A = list(a.nlargest(TOPK,'G2').term)
            B = list(b.nlargest(TOPK,'G2').term)
            rank = {t:i+1 for i,t in enumerate(a.nlargest(500,'G2').term)}
            sa, sb = set(A), set(B)

            # **左右に並べた表にする。** 2本のリストを print で上下に
            # 並べると，何番目が入れ替わったのかを目で数えることになる。
            rows = []
            for i in range(min(TOPK, max(len(A), len(B)))):
                t_a, t_b = (A[i] if i < len(A) else ''), (B[i] if i < len(B) else '')
                mark = ''
                if t_a and t_a not in sb:
                    r = a[a.term == t_a].iloc[0]
                    mark = (f'← 落ちた（df {r.df_all_prop:.0%}／'
                            f'dp_in {r.dp_in:.2f}／最多 {_w4(r.top_work)} '
                            f'{r.top_work_share:.0%}）')
                elif t_b and t_b not in sa:
                    mark = f'→ 入った（元 {rank.get(t_b, "500+")} 位）'
                rows.append({'順位': i+1, 'そのまま': t_a,
                             'culling 後': t_b, '入れ替わり': mark})
            show(pd.DataFrame(rows),
                 caption=(f'{per}　有意語 {len(a):,} → culling 後 {len(b):,}'
                          f'（{len(b)/max(1,len(a)):.0%} 残る）'
                          f'／閾値 df < {CULL:.0%} を除外'))'''),
 ('md', r'''### 4.2 どれだけ変わったか — 数えて比べる

目で見た印象では「だいぶ変わった」とも「あまり変わらない」とも言える。
**数えること。** 指標は3種類あればよい。

| 指標 | 定義 | 読み |
|---|---|---|
| **残存率** | culling 後に残る有意語の割合 | 小さい＝その時代の特徴語は薄く分布する語が少ない |
| **重なり@k** | そのままの上位 k 語のうち，culling 後も上位 k 語に残る割合 | 1 に近い＝culling してもリストはほぼ同じ |
| **入れ替わり** | culling 後の上位 k 語のうち，元は上位 k 語に無かった語数 | 大きい＝頻度順では埋もれていた語が浮上した |

⚠ **順位相関（Spearman ρ）を「そのまま vs culling 後」で計算しても
意味がない。** culling は語を落とすだけで残った語の順序を変えないので，
**ρ は必ず 1 になる**。使うなら「G² の順位」と「`dp_in`」の相関を見て，
*このコーパスでは高頻度の特徴語ほど bursty なのか*を問うほうがよい。'''),
 ('code', r'''p = OUT/'descriptive'/'keyness_by_period.csv'
if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    ky = read_table(p)
    if 'dp_in' not in ky.columns:
        print('[warn] dp_in 列が無い。07 を走らせるセルを実行し直すこと。')
    else:
        rows = []
        for per in sorted(ky.period.unique()):
            a = ky[(ky.period==per)&(ky.G2>0)]
            b = a[a.df_all_prop >= CULL]
            rec = {'period': per, '有意語': len(a), 'culling後': len(b),
                   '残存率': len(b)/max(1,len(a))}
            for k in (15, 50):
                A = set(a.nlargest(k,'G2').term)
                B = set(b.nlargest(k,'G2').term)
                # **分母は k ではなく min(k, 実際の語数)。** 有意語が k に
                # 満たない時代（作品数の少ない時代）で，k で割ると
                # 重なりが不当に低く出て「culling で激変した」と読める。
                rec[f'重なり@{k}'] = len(A & B)/max(1, min(k, len(a)))
                rec[f'入替@{k}'] = len(B - A)
            t50 = a.nlargest(50,'G2')
            keep = t50[t50.df_all_prop >= CULL]
            drop = t50[t50.df_all_prop <  CULL]
            rec['残る語のdp_in中位'] = keep.dp_in.median() if len(keep) else np.nan
            rec['落ちる語のdp_in中位'] = drop.dp_in.median() if len(drop) else np.nan
            # G² の順位と bursty さの関係。正なら「上位ほど一点に固まる」
            top = a.nlargest(100,'G2')
            rec['ρ(G²,dp_in)'] = (top.G2.corr(top.dp_in, method='spearman')
                                  if len(top) > 5 else np.nan)
            rows.append(rec)
        mt = pd.DataFrame(rows).rename(columns={'period':'時代'})
        show(mt, caption=f'culling（df < {CULL:.0%} を除外）の効き方',
             fmt={'残存率':'{:.1%}', '重なり@15':'{:.0%}', '重なり@50':'{:.0%}',
                  '残る語のdp_in中位':'{:.3f}', '落ちる語のdp_in中位':'{:.3f}',
                  'ρ(G²,dp_in)':'{:+.2f}'})
        print('重なり@15 が 1.00 に近い時代は，culling の有無で結論が変わらない。')
        print('小さい時代は，**そのままの上位語が数点の作品に依存している**。')'''),
 ('md', r'''### 4.3 culling の閾値は bursty さの代理にすぎない

`df < 10%` という規則は**文書頻度**を見ているだけで，bursty さを直接
測ってはいない。だから2種類の取りこぼしが起きる。

- **弾かれないが bursty** … 多くの作品に1回ずつ出るうえで，1作品に
  大量に出る語（df は大きいのに `dp_in` も大きい）
- **弾かれるが均等** … その時代の作品数が少ないために df が 10% に
  届かないだけで，その時代の中では均等に出る語

下の図は横軸に `df_all_prop`（culling の規則），縦軸に `dp_in`
（bursty さ）を取る。**規則が捉え損なう語は左上と右下に現れる。**
その語に名前が付いているので，自分の目で確かめられる。

### 図は2つ出る — 静止版（SVG）と対話版（HTML）

名前を付けられるのは数語だけである。300 点に全部名前を付ければ図は読めない。
かといって名前が無ければ，「左上が bursty」と言われても**どの語なのかを
確かめようがない**。そこで同じ図から2つ書き出す。

| ファイル | 用途 |
|---|---|
| `Step4_keyness_dispersion.svg` | 論文・配布用。従来どおり。拡大も加筆も自由 |
| `Step4_keyness_dispersion.html` | 探索用。**点にカーソルを近づけると語が出る** |

HTML は**その SVG をそのまま埋め込んでいる**（描き直していない）ので，
注記も軸も静止版と同一である。加えて

- 最も近い点を拾うので，小さな点の真上に置かなくてよい
- 語・作品・時代で絞り込める検索欄がある
- 全点の表が下に付く（カーソルが使えなくても同じ情報に届く）。
  **表の行にカーソルを乗せると，図の上のその点が光る**
- 「SVG を保存」から静止版を取り出せる

外部の JavaScript ライブラリは使っていない。**ネットワークが塞がれたマシンでも
ブラウザで開ける**（`open my_work/results/Step4_keyness_dispersion.html`）。'''),
 ('code', r'''p = OUT/'descriptive'/'keyness_by_period.csv'
if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    ky = read_table(p)
    if 'dp_in' not in ky.columns:
        print('[warn] dp_in 列が無い。07 を走らせるセルを実行し直すこと。')
    else:
        sub = pd.concat([ky[(ky.period==per)&(ky.G2>0)].nlargest(50,'G2')
                         for per in sorted(ky.period.unique())])
        keep = (sub.df_all_prop >= CULL).values
        # このセル単独でも走るように，語幹→作家『作品』の表はここでも作る
        LAB4 = work_labels()
        def _w4(x):
            x = str(x)
            return LAB4.get(x, x)

        fig, ax = plt.subplots(figsize=(9.4,6.2))
        ax.axvspan(0, CULL, color='#f4f4f1', zorder=0)   # 弾かれる領域
        # 色だけでなくマーカーの形も変える（色覚の多様性と白黒印刷のため）
        ax.scatter(sub.df_all_prop[keep], sub.dp_in[keep], s=26, alpha=.80,
                   color=PALETTE[0], linewidth=0, label=f'残る（df≧{CULL:.0%}）')
        ax.scatter(sub.df_all_prop[~keep], sub.dp_in[~keep], s=30, alpha=.85,
                   color=PALETTE[1], marker='^', linewidth=0,
                   label=f'弾かれる（df<{CULL:.0%}）')
        ax.axvline(CULL, color='#8a8a83', lw=1, ls='--')
        ax.axhline(0.5, color='#8a8a83', lw=1, ls=':')

        ax.set_xlabel('df_all_prop — 全作品のうち何割に出るか（culling の規則）')
        ax.set_ylabel('dp_in — その時代の中での偏り（1 に近いほど bursty）')
        ax.set_title('culling の閾値と bursty さは一致しない（各時代の上位50語）')
        ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.03, 1.03)
        ax.legend(frameon=False, loc='upper right')
        for sp in ('top','right'): ax.spines[sp].set_visible(False)
        # **注記より先に tight_layout を呼ぶ。** あとで呼ぶと軸が動き，
        # 置いたラベルが点からずれる（check_scatter_labels.py が検出する）。
        fig.tight_layout()

        # **規則が取りこぼした語にだけ**名前を付ける。全点に付けると読めず，
        # 無条件に上位6語を拾うと「取りこぼしが無い」ときにも何か名前が
        # 付いてしまい，規則が外れている証拠のように見える。
        # 閾値で切って，外れていなければ何も付けない。
        k1 = sub[keep]
        miss = k1[k1.dp_in > 0.5].nlargest(6,'dp_in')      # 残るのに bursty
        k0 = sub[~keep]
        over = k0[k0.dp_in < 0.3].nsmallest(6,'dp_in')     # 弾かれるのに均等
        lab = pd.concat([miss, over])
        if len(lab):
            label_points(ax, lab.df_all_prop, lab.dp_in, lab.term, fontsize=8)

        # SVG（静止版）と HTML（対話版）を同じ図から出す。
        # **注記は SVG の中にあるので対話版にもそのまま残る。**
        # 残りの点は，指せば語が出る。どの点が何の語かを言えないまま
        # 「左上が bursty」と説明しても，受講生には確かめようがない。
        tips = [{'term': r.term,
                 'fields': [('時代', r.period),
                            ('G²', f'{r.G2:.0f}'),
                            ('df（全作品）', f'{r.df_all_prop:.0%}'),
                            ('df（その時代）', f'{r.df_in_prop:.0%}'),
                            ('dp_in', f'{r.dp_in:.3f}'),
                            ('最多作品の占有', f'{r.top_work_share:.0%}'),
                            ('最多作品', _w4(r.top_work)),
                            ('culling', '残る' if r.df_all_prop >= CULL
                                        else '弾かれる')]}
                for _, r in sub.iterrows()]
        save_interactive(
            fig, ax, 'Step4_keyness_dispersion',
            sub.df_all_prop, sub.dp_in, tips, source=p,
            title='culling の閾値と bursty さは一致しない',
            hint=('点にカーソルを近づけると語が出る（最も近い点を拾うので，'
                  '真上に置かなくてよい）。図の中の注記は静止版と同じもので，'
                  '取りこぼした語だけに付いている。'),
            note=(f'各時代の G² 上位50語／縦線は df {CULL:.0%} の閾値，'
                  f'横の点線は dp_in 0.5。左上＝弾かれないが bursty，'
                  f'右下＝弾かれるが均等。'),
            table_cols=['時代', 'G²', 'df（全作品）', 'dp_in',
                        '最多作品の占有', '最多作品', 'culling'])
        plt.show()

        # 規則が外れた語を表にする（どの作品のせいかまで載せる）
        bad = pd.concat([
            miss.assign(位置='左上：弾かれないが bursty'),
            over.assign(位置='右下：弾かれるが均等')])
        if len(bad):
            bad = bad.assign(top_work=bad.top_work.map(_w4))
            show(bad[['位置','period','term','df_all_prop','dp_in',
                      'top_work_share','top_work','G2']]
                 .rename(columns={'period':'時代','term':'語',
                                  'df_all_prop':'df','dp_in':'dp_in',
                                  'top_work_share':'最多作品の占有',
                                  'top_work':'最多作品','G2':'G²'}),
                 caption='df の規則が取りこぼした語',
                 fmt={'df':'{:.0%}', 'dp_in':'{:.2f}',
                      '最多作品の占有':'{:.0%}', 'G²':'{:.0f}'})
            print('**この語については規則が外れている。** 閾値を変えるか，'
                  'dp_in で直接絞ることを検討する。')
        else:
            print(f'取りこぼしなし（df≧{CULL:.0%} の語はどれも dp_in≦0.5，'
                  '弾かれる語はどれも dp_in≧0.3）。')
            print('このコーパスでは df の規則が bursty さの代理として働いている。')'''),
 ('md', r'''### 演習 2 — bursty か evenly-distributed か

`CULL` を `0.05` / `0.10` / `0.25` と変え，時代ごとに次を答えよ。

1. **重なり@15 が最も小さい時代**はどれか。その時代の
   「落ちた語」の `top_work` を見て，**どの作品が効いていたか**を言え。
2. 「入った語」（culling で浮上した語）を5語選び，それが
   **時代の特徴として説明できるか**を述べよ。説明できないなら，
   何を拾ってしまっているのか。
3. 自分の研究上の問いに対して，**culling すべきか，すべきでないか**。
   *その時代に何が書かれたか*を問うなら bursty な語も証拠になるが，
   *その時代の書き方*を問うなら邪魔になる。**問いによって答えが変わる**
   ことを，具体的な語を挙げて論じよ。
4. ⚠ 閾値は**測る前に決めておく**こと。3通り試してから「いちばん
   きれいな」閾値を選ぶのは，結果に合わせて規則を選ぶことである。
   試すのは**感度分析**として行い，本分析の閾値は先に宣言して報告に書く。'''),
 ('md', r'''### 演習 3 — 固有名詞を除くとどう変わるか

`data/tokens/tsv/` には品詞情報がある。固有名詞を除いて特徴語を再計算し，
上のリストと比べよ。**消えた語と残った語の違いは何か。**'''),
 ('code', r'''# 固有名詞を除いたトークン列を作る（演習用）
def tokens_without_propn(tsv_path):
    out = []
    # 05 は BOM 付きで書く（Excel 対策）ので utf-8-sig で読む
    with open(tsv_path, encoding='utf-8-sig') as fh:
        next(fh); next(fh)           # コメント行とヘッダ
        for line in fh:
            f = line.rstrip('\n').split('\t')
            if len(f) < 13 or f[6] in ('EOS','補助記号','空白'):
                continue
            if f[6]=='名詞' and f[7]=='固有名詞':
                continue
            out.append(f[2])
    return out

tsvs = sorted((TOK/'tsv').glob('*.tsv'))[:3]
for t in tsvs:
    toks = tokens_without_propn(t)
    print(f'{t.name:<24} 固有名詞を除いた語数 {len(toks):,}')
print('\n→ 全件で作り直して 07_descriptive_stats.py にかけ直すのが課題。')'''),
 ('md', r'''## 5. このステップの課題

次の設問への答えを，テンプレート `my_work/results/Step4_report.md` に書いて提出する（**全体で600–1000字程度**。図表と「再現のための情報」は字数に含めない）。

- **提出先**：Zulip（{ZULIP_ORG}）の非公開チャネル **{ZULIP_CHANNEL}** ＞ トピック **Step 4**
- テンプレートの中身をメッセージに貼り付け，図（SVG）・表（CSV）は**同じメッセージに添付**する（1人1通）
- 図は番号で言及し（図1），**図を見なくても論旨が追えるように**書く（SVG は Zulip で表示されないことがある）
- 再提出は元の投稿を直さず，同じトピックに新しく投稿する（手順書 §5.3）

1. TTR・Guiraud R・Yule K・エントロピーを作品長に対してプロットし，
   **通時比較に使うならどれか**を根拠とともに選ぶこと。
2. Delta の最近傍一致率（作家 / 時代 / ジャンル）を報告し，
   このコーパスで通時的主張をするときの注意点を述べること。
3. 固有名詞を除いた特徴語リストを作り，除く前と比較すること。
4. MFW の語数（100 / 300 / 1000）を変えると PCA の布置がどう変わるか確かめること。
5. `speech_markup` が `full` でない作品を除いた場合と含めた場合とで，
   会話文比率と第1主成分の相関がどう変わるかを示すこと。**該当作品は
   `conversion_report.csv` から自分で数えること**（コーパスの増補で
   件数は変わる）。
6. **culling（4.1–4.3）を報告の形に書くこと。** 閾値を1つ宣言し，
   時代ごとに「残存率・重なり@15・入替@15」の表を出し，
   *culling の有無で結論が変わる時代*があるかを述べること。
   変わる時代があれば，**その原因になっている作品を名指しする**
   （`top_work` 列）。加えて，自分の問いに対してどちらを本分析に
   採るかを1段落で正当化すること。
### このステップの到達点（次へ進む条件）

- MFW 行列・Delta 行列・PCA 図が `results/descriptive/` に出ている
- Delta の最近傍が作家・時代・ジャンルのどれと一致しやすいかを言える
- 特徴語リストの上位語について，**それが何を測っているか**を説明できる
- **G² の順位と bursty さが別物であることを，自分のデータの語で示せる**
  （`Step4_keyness_dispersion.svg` の左上・右下の語）
'''),
])

# ==========================================================================
L(5, 'word2vec の原理 — 分布仮説から word embedding へ', [
 ('md', r'''# Step 5 word2vec の原理 — 分布仮説から word embedding へ

## このステップの到達目標

1. 分布仮説と共起行列から word2vec までの流れを説明できる
2. ハイパーパラメータ（window / dim / min_count / sg）の意味と影響を言える
3. 近傍語・類推・クラスタリングで word embedding を点検できる
4. **word embedding が不安定になる条件**を知り，結果を過信しない

## 導入：分布仮説

> "You shall know a word by the company it keeps." — J. R. Firth (1957)

語の意味は，その語が現れる文脈の分布で近似できる。これが分布仮説である。
word2vec はこの仮説を，**文脈語を予測する浅いニューラルネットの重み**として
実装したものにすぎない。学習が終わったあとに残る重み行列が word embedding である。

### 2つの学習方式

| | 入力 → 出力 | 特徴 |
|---|---|---|
| **CBOW** | 文脈語 → 中心語 | 速い。高頻度語に強い |
| **Skip-gram (sg=1)** | 中心語 → 文脈語 | 遅い。**低頻度語に強い** |

文学コーパスは総語数が数百万語と小さく，関心のある語（「恋」「自由」「機械」）も
必ずしも高頻度ではない。したがって **skip-gram を既定にする**。

## 参考
- Mikolov et al. (2013) Efficient estimation of word representations. *ICLR Workshop*.
- Levy & Goldberg (2014) Neural word embedding as implicit matrix factorization. *NIPS*.
- Antoniak & Mimno (2018) Evaluating the stability of embedding-based word similarities. *TACL* 6.
'''),
 ('code', PREAMBLE),
 ('md', r'''## 1. まず共起行列を作ってみる

word2vec に入る前に，**素朴な共起行列＋PPMI＋SVD** で同じことをやる。
Levy & Goldberg (2014) が示したとおり，両者は数学的に近い関係にある。
手で作ると，word embedding が魔法ではないことが分かる。'''),
 ('code', r'''DS  = ROOT/'data'/'datasets'
TOK = ROOT/'data'/'tokens'
docs = [f.read_text(encoding='utf-8').split()
        for f in sorted((TOK/'tokens_content').glob('*.txt'))]
print(f'{len(docs)} 文書 / {sum(len(d) for d in docs):,} 語')

WINDOW = 5
vocab = Counter(w for d in docs for w in d)
V = [w for w,c in vocab.most_common(3000)]
vi = {w:i for i,w in enumerate(V)}
C = np.zeros((len(V), len(V)), dtype=np.float32)
for d in docs:
    ids = [vi.get(w,-1) for w in d]
    for k,a in enumerate(ids):
        if a < 0: continue
        for b in ids[max(0,k-WINDOW):k+WINDOW+1]:
            if b >= 0 and b != a:
                C[a,b] += 1
print('共起行列:', C.shape, '非ゼロ率 {:.1%}'.format((C>0).mean()))'''),
 ('code', r'''# PPMI（正の相互情報量）→ SVD
tot = C.sum(); pw = C.sum(1)/tot; pc = C.sum(0)/tot
with np.errstate(divide='ignore', invalid='ignore'):
    PMI = np.log((C/tot) / (pw[:,None]*pc[None,:]))
PPMI = np.nan_to_num(np.maximum(PMI, 0))
U,S,Vt = np.linalg.svd(PPMI, full_matrices=False)
E = U[:,:100]*S[:100]
En = E/ (np.linalg.norm(E,axis=1,keepdims=True)+1e-12)

def nbr(w, k=10, M=En):
    if w not in vi: return f'{w} は語彙にない'
    s = M @ M[vi[w]]
    return ' '.join(V[i] for i in np.argsort(-s)[1:k+1])

for w in ['女','心','国','汽車','恋','自由','戦争']:
    print(f'{w:<6} → {nbr(w)}')'''),
 ('md', r'''## 2. word2vec を学習する'''),
 ('code', r'''from gensim.models import Word2Vec
sents = [f.read_text(encoding='utf-8').split()
         for f in sorted((DS/'chunks').glob('*.txt'))] or docs
# 既定値は config/pipeline.yaml の word2vec: に揃えてある（dim 300／window 3）。
# **ウィンドウを狭くしてある**のは，語の統語的な振る舞い（品詞・共起の型）を拾いたい
# からである。ウィンドウを広げると主題の近さが優勢になる（演習 1 で確かめる）。
W2V_DIM, W2V_WIN = 300, 3
SEEDS = [11, 22, 33, 44, 55, 66, 77, 88, 99, 111]   # 報告にはこの並びをそのまま書く
w2v = Word2Vec(sents, vector_size=W2V_DIM, window=W2V_WIN, min_count=20,
               sg=1, workers=4, epochs=20, seed=SEEDS[0])
print(f'語彙: {len(w2v.wv)}'
      f'（dim={W2V_DIM} window={W2V_WIN} seed={SEEDS[0]}）')
for w in ['女','心','国','汽車','恋','自由','戦争','機械','神']:
    if w in w2v.wv:
        print(f'{w:<6} → ' + ' '.join(x for x,_ in w2v.wv.most_similar(w, topn=10)))'''),
 ('md', r'''### 演習 1 — ハイパーパラメータの影響

`window` を 2 / 3 / 10 と変えて，同じ語の近傍がどう変わるかを見よ
（3 が本パイプラインの既定値である）。

一般に
- **小さい window（2–3）** → 統語的・構文的な類似（品詞が揃う）
- **大きい window（10–15）** → 主題的・連想的な類似（同じ話題の語）

文学の主題分析には大きめ，文体分析には小さめが向く。**既定を 3 にしてある
のは，この授業の主眼が文体にあるからである**（§4 のギャラクシーで
「品詞のまとまり」と「意味のまとまり」の両方が見えるのも，このウィンドウ幅のため）。'''),
 ('code', r'''probe = ['女','汽車','心']
# 語を行・window を列にすると，**同じ語がウィンドウ幅でどう動くか**を横に読める。
cols = {}
for win in [2, 3, 10]:
    m = Word2Vec(sents, vector_size=W2V_DIM, window=win, min_count=20,
                 sg=1, workers=4, epochs=15, seed=SEEDS[0])
    cols[f'window={win}'] = [
        ' '.join(x for x,_ in m.wv.most_similar(w, topn=8)) if w in m.wv
        else '（min_count 未満）' for w in probe]
t = pd.DataFrame(cols, index=probe)
t.index.name = '語'
show(t.reset_index(), caption='ウィンドウ幅を変えると近傍語がどう変わるか（上位8語）')
print('狭いウィンドウは統語的に置き換えられる語（品詞が同じ語）を，'
      '広いウィンドウは主題の近い語を集めやすい。')'''),
 ('md', r'''### 演習 2 — 安定性の検査（重要）

**乱数シードを変えると近傍語は変わる。** Antoniak & Mimno (2018) は，
小規模コーパスでは word embedding の近傍が実験ごとに大きく揺れることを示した。

シードを **10 回**（`SEEDS` = 11, 22, …, 111）変えて学習し，近傍の**一致率**を
測る。一致率が低い語について「意味が変化した」と論じてはいけない。

⚠ 10 回の学習は時間がかかる（この規模で数分）。**時間が無いときは
`SEEDS[:5]` に減らしてよいが，報告には何回で測ったかを必ず書くこと。**'''),
 ('code', r'''def topn(model, w, k=10):
    return [x for x,_ in model.wv.most_similar(w, topn=k)] if w in model.wv else []

# 学習の設定は本番と同じ（dim 300／window 3）。epochs だけ 15 に落としてある。
runs = [Word2Vec(sents, vector_size=W2V_DIM, window=W2V_WIN, min_count=20,
                 sg=1, workers=4, epochs=15, seed=s) for s in SEEDS]
print(f'{len(runs)} 回学習した（シード: ' + ', '.join(map(str, SEEDS)) + '）')
rows=[]
for w in ['女','心','国','汽車','恋','自由','戦争','機械','神','自然']:
    sets = [set(topn(m,w)) for m in runs]
    sets = [s for s in sets if s]
    if len(sets) < 2: continue
    jac = np.mean([len(a&b)/len(a|b)
                   for i,a in enumerate(sets) for b in sets[i+1:]])
    rows.append({'term':w, 'freq':vocab[w], 'jaccard':round(jac,3)})
stab = pd.DataFrame(rows).sort_values('jaccard')
show(stab.rename(columns={'term':'語','freq':'頻度','jaccard':'Jaccard 平均'}),
     caption=f'乱数シードを変えたときの近傍上位10語の一致'
             f'（{len(runs)}回の全ペア平均）',
     fmt={'頻度':'{:,.0f}','Jaccard 平均':'{:.3f}'})
weak = stab[stab.jaccard < 0.3]
note = ('：' + '，'.join(weak.term)) if len(weak) else ''
print(f'Jaccard が 0.3 を下回る語は {len(weak)} 語{note}。'
      '**この語の近傍は解釈に耐えない。**')
print('min_count を上げる／コーパスを増やす／複数回の平均を取る。')'''),
 ('code', r'''fig, ax = plt.subplots(figsize=(7,5))
ax.scatter(stab.freq, stab.jaccard, s=60, color=PALETTE[0], zorder=3)
ax.set_xscale('log'); ax.set_xlabel('コーパス頻度（対数）')
ax.set_ylabel(f'近傍の一致率（Jaccard, {len(runs)}試行）')
ax.axhline(.3, color=PALETTE[5], ls='--', lw=1)
ax.set_title('頻度が低い語ほど word embedding は不安定になる')
ax.spines[['top','right']].set_visible(False)
fig.tight_layout()
label_points(ax, stab.freq, stab.jaccard, stab.term, fontsize=9)
save_fig(fig, 'Step5_stability'); plt.show()'''),
 ('md', r"""## 3. 語彙空間を眺める — **主成分分析と UMAP を並べて比べる** ★

2次元に落として語のまとまりを見る。ただし**落とし方は1つではない**。
同じ300語を**2つの方法**で落として並べる。

| | 何を守ろうとするか | 何を捨てるか |
|---|---|---|
| **主成分分析（PCA）** | **大域**の構造（分散の大きい向き）。線形 | 局所の細かい近さ。第1・2主成分に乗らない違い |
| **UMAP** | **局所**の近さ（各点の近傍） | 大域の配置・塊どうしの距離。軸の意味 |

見どころは「どちらが正しいか」ではない。**同じ空間なのに絵が違う**こと，
そして**違い方が方法の性質どおりか**である。

- PCA の第1主成分は，たいてい**頻度**か**品詞**の軸になる（語彙素の列なら
  助詞・助動詞が一方の端に寄る）。軸に解釈を与えられるのが PCA の強み。
- UMAP は塊をはっきり見せるが，**塊どうしの距離と軸には意味が無い**。
  締まった塊が出たからクラスタがあるとは言えない。

目で見るだけでは水掛け論になるので，**3つの数**を並べる。

| 指標 | 何を測るか | 強いのは |
|---|---|---|
| 信頼度（trustworthiness） | 画面で近い点が原空間でも近いか（局所） | ふつう UMAP |
| 近傍保存 | 原空間の上位10近傍のうち画面でも上位10に入る語数 | ふつう UMAP |
| 順位相関 ρ | 原空間の距離と画面の距離の順位の一致（**大域**） | ふつう PCA |

**この表が「方法を選ぶ」ということの中身である。** 図の見た目ではなく，
何を守りたいかで選び，選んだ理由を報告に書く。"""),
 ('code', r'''# ---- 同じ300語を2つの方法で落とす ------------------------------------
WORD_PROJ = 'umap'      # 'umap' / 'tsne' / 'auto'（auto のときだけ t-SNE に落ちる）
WORD_SEED = 20260920

sel = [w for w,c in vocab.most_common(400) if w in w2v.wv][:300]
X  = np.vstack([w2v.wv[w] for w in sel]).astype(np.float32)
Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-12)

# (1) 主成分分析（線形・大域）。中心化してから特異値分解する
Xc = X - X.mean(0)
U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
P_pca = U[:, :2] * S[:2]
var = (S ** 2 / (S ** 2).sum())[:2]

# (2) UMAP（非線形・局所）。使えなければ**止まって理由を出す**
P_umap, METHOD_W = project(Xn, WORD_PROJ, WORD_SEED)

q_pca  = proj_quality(Xn, P_pca)
q_umap = proj_quality(Xn, P_umap)
show(pd.DataFrame([
        {'射影': f'主成分分析（第1・2主成分で {var.sum():.1%}）',
         '信頼度': q_pca['trust'], '近傍保存（/10）': q_pca['keep'].mean(),
         '順位相関 ρ（大域）': q_pca['rho']},
        {'射影': METHOD_W.split(' (')[0],
         '信頼度': q_umap['trust'], '近傍保存（/10）': q_umap['keep'].mean(),
         '順位相関 ρ（大域）': q_umap['rho']}]),
     caption=f'同じ {len(sel)} 語を2つの方法で2次元に落とした結果',
     fmt={'信頼度': '{:.3f}', '近傍保存（/10）': '{:.1f}',
          '順位相関 ρ（大域）': '{:+.3f}'})
print('局所（信頼度・近傍保存）と大域（ρ）は別物である。')
print('**どちらが上でも「良い射影」ではない。** 何を守りたいかで選ぶ。')'''),
 ('code', r'''# ---- 2面に並べて描く（静止版 SVG ＋ 対話版 HTML）----------------------
fig, axes = plt.subplots(1, 2, figsize=(14, 7))
panels = [('主成分分析', P_pca,
           f'第1・2主成分（分散の {var.sum():.1%}）／'
           f'ρ {q_pca["rho"]:+.2f}・信頼度 {q_pca["trust"]:.2f}'),
          (METHOD_W.split(' (')[0], P_umap,
           f'{METHOD_W}／ρ {q_umap["rho"]:+.2f}・'
           f'信頼度 {q_umap["trust"]:.2f}')]
# **名前を打つ語は2面で同じにする。** 違えると見比べられない。
# 300語ぜんぶに打つと真っ黒になるので，頻度上位 40 語に絞る
# （残りの語は対話版で指せる。label_points が省いた件数を報告する）。
mark = set(sel[:40])
for ax, (name, P, sub) in zip(axes, panels):
    ax.scatter(P[:, 0], P[:, 1], s=12, color=PALETTE[0], alpha=.45,
               linewidth=0, rasterized=True)
    ax.set_title(f'{name}\n{sub}', fontsize=10)
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)
axes[0].set_xlabel('軸に意味がある（分散の大きい向き）', fontsize=9)
axes[1].set_xlabel('※ 軸にも塊どうしの距離にも意味は無い', fontsize=9)
fig.suptitle(f'word2vec 空間（{X.shape[1]}次元・高頻度{len(sel)}語）を'
             f'2つの方法で2次元に落とす', fontsize=12)
fig.tight_layout()
# ⚠ 注記は tight_layout の**後**（軸が動くと位置がずれる）
# ⚠ 座標と名前は**同じ添字で**絞る。綴りも揃えておく（片方だけ絞る事故を
#   check_scatter_labels.py が見つけられるようにするため）
sel_arr = np.array(sel)
idx = np.array([i for i, w in enumerate(sel) if w in mark])
for ax, (name, P, sub) in zip(axes, panels):
    label_points(ax, P[idx, 0], P[idx, 1], sel_arr[idx], fontsize=7)

tips = [{'term': w,
         'fields': [('頻度', f'{vocab[w]:,}'),
                    ('近傍保存 PCA', f'{int(q_pca["keep"][i])}/10'),
                    ('近傍保存 UMAP', f'{int(q_umap["keep"][i])}/10')]}
        for i, w in enumerate(sel)]
# **面ごとに座標が違う**ので coords で渡す（渡さないと2面めの当たり判定が
# 1面めの座標で置かれ，指した点と出る語が食い違う）
save_interactive(fig, list(axes), 'Step5_wordspace',
                 P_pca[:, 0], P_pca[:, 1], tips,
                 coords=[(P_pca[:, 0], P_pca[:, 1]),
                         (P_umap[:, 0], P_umap[:, 1])],
                 source=DS/'chunks', id_col='語',
                 title='語彙空間 — 主成分分析と UMAP の比較',
                 note=(f'word2vec {X.shape[1]}次元・高頻度{len(sel)}語／'
                       f'左 主成分分析（分散 {var.sum():.1%}）／右 {METHOD_W}／'
                       f'シード {WORD_SEED}'),
                 hint=('**同じ語が左右のどこに来るか**を見る。点にカーソルを'
                       '近づけると語と，それぞれの射影での近傍保存が出る。'
                       '検索すると両方の面にマーカーが付く。'),
                 table_cols=['頻度', '近傍保存 PCA', '近傍保存 UMAP'])
plt.show()'''),
 ('md', r"""### 演習 — 2つの絵の違いを言葉にする

1. **同じ語**（たとえば「私」「汽車」「美しい」）が左右のどこに来るかを，
   対話版で追え。両方で端にある語，片方だけで端にある語を書き出す。
2. PCA の第1主成分の両端に来る語を10語ずつ並べ，**その軸が何か**を
   述べよ（頻度か，品詞か，語種か）。`vocab` の頻度と見比べる。
3. 上の表で ρ と信頼度がどちらに振れたかを確かめ，
   **「UMAP の絵で塊が2つに見えた」から言えること／言えないこと**を書け。
4. `WORD_SEED` を変えて UMAP だけ描き直し，塊の位置がどれだけ動くかを見よ。
   **PCA はシードに依存しない**（同じ行列からは同じ答えが出る）。ここが
   「軸に意味がある」ことの実際的な意味である。"""),

 ('md', r"""## 4. 語彙のギャラクシー — 300次元を2次元に落として眺める

近傍語の一覧は「1語ずつ」の確認である。**空間の全体像**を見たい。
300 次元は目で見られないので2次元に落とす。ここでは **UMAP** を使う
（`GAL_PROJ` で切り替える。どちらで描いたかは図と HTML に記録される）。

### ⚠ UMAP で描けているか確かめる

`GAL_PROJ = 'umap'` が既定である。**使えないときは黙って t-SNE に落ちず，
理由を出して止まる。** 図は出るのに方法だけが替わっているのが，いちばん
気づきにくい（塊の見え方が変わるので，設定の問題を分析結果と読み違える）。

入れたはずなのに使えないときの原因は，ほぼ次の4つである。

| 原因 | 見分け方 |
|---|---|
| **カーネルが作業フォルダの環境でない**（最多） | 下のセルの `sys.executable` が `dh_project/.venv/bin/python` でない |
| `umap` という**別パッケージ**が入っている | `umap.UMAP` が無いというエラー |
| numba / llvmlite が numpy の版と合わない | `import umap` 自体が例外 |
| numba がキャッシュを書けない | 初回の射影で permission のエラー |

切り分けと対処は診断スクリプトが全部やる。**ノートブックと同じ
カーネルで**走らせること。

```python
import sys, subprocess
print(sys.executable)
print(subprocess.run([sys.executable, str(ROOT/'scripts'/'check_umap.py')],
                     capture_output=True, text=True).stdout)
```

カーネルが違っていたときは，JupyterLab の右上でカーネルを
「Python (JLit)」に切り替え，**再起動する**。無ければ
`bash scripts/00_bootstrap_mac.sh`（自分の Mac は `--personal`）を実行し直す。

入っていないパッケージは，リポジトリの中で `uv add umap-learn` のように入れる。
`~/Documents/dh_project/pyproject.toml` があるので，カーネルと同じ `.venv` に入る
（**`uv sync` は使わない**）。

### この図が見せようとしていること — 文法と意味の二重構造

word embedding は「意味の似た語が近くに来る」と説明されがちだが，実際に学習して
いるのは**文脈の似た語が近くに来る**ことである。文脈が似ていれば

- **文法的にも似る**（同じ位置に立てる語＝品詞が揃う）
- **意味的にも似る**（同じ話題に出る語が集まる）

この2つが同時に起きる。そこでこの図では

- **色 = 品詞**（名詞・動詞・形容詞・副詞）… 文法的なまとまり
- **マーカーの形 = 意味／機能のカテゴリ**（`ANCHORS`）… 意味的なまとまり

と**別の手がかりに分けて**示してある。見どころは，
**同じ色の中に別の形の塊がいくつもできている**ところである。
形容詞（緑）は全体として一帯に寄りながら，その中で「色の形容詞」と
「大小の形容詞」に分かれる。これが「文法的な関係と意味的な関係の両方が
モデル化されている」ということの，目に見える形である。

### ⚠ この図でいちばん大事な注意

**軸に意味は無い。** 上下左右は「第1主成分」のような解釈可能な軸ではなく，
**近さだけ**が意味を持つ。しかもその近さは
**300 次元の近さを2次元に押し込んだ結果**であって、元の近さそのものではない。

そこでこの図は，**射影がどれだけ嘘をついているか**も一緒に出す。

- `trustworthiness`（信頼度）… 2次元で近いと見える点が，原空間でも本当に
  近いか。1 に近ければ嘘が少ない
- 点ごとの**近傍保存**… その語の原空間での上位10近傍のうち，画面上でも
  上位10近傍に入っているのは何語か
- 対話版で点を指すと，**原空間での近傍10語へ線が伸びる**。
  線が遠くまで伸びる点は，その語の近傍関係が2次元に収まっていない

**線が長い語について「図で遠いから意味が遠い」と言ってはいけない。**
これがこの図の使い方であり，同時に限界の示し方である。

### 4.0 どの語の列で学習するか — 表層形・語彙素・内容語 ★

`05_tokenise_unidic.py` は**3つの列**を書き出している。どれを使うかは
好みではなく，**何を分析の対象とするか**の宣言である。

| 列 | 中身 | 何が起きるか |
|---|---|---|
| `tokens_surface` | 表層形そのまま | 「言う／言った／言ふ／云う」が**別の語**になる。異なり語数が膨らみ，1語あたりの頻度が下がる |
| `tokens_lemma` | 語彙素（見出し語）・**全品詞** | 活用と表記のゆれを畳む。助詞・助動詞・代名詞も**空間に入る** |
| `tokens_content` | 語彙素のうち名詞・動詞・形容詞・副詞 | 機能語を落とす。主題的な近さが出やすい |

**「表層形でないと空間が歪む」という直感には，半分だけ理由がある。**
分けて考える必要がある。

1. **機能語を落とすこと**（content の問題）。ウィンドウ 3 で
   「彼 は 汽車 に 乗っ た」を見るとき，助詞を落とすと隣接関係が
   「彼 汽車 乗る」に変わる。つまり**ウィンドウの意味が変わり，実効的に広くなる**。
   しかも「が／を／に」対「て／た」という**品詞を見分ける最強の手がかり**
   が消える。§4 で「文法的な関係もモデル化されている」ことを見せるなら，
   機能語を落とすのは自分の首を絞めている。**この点はご指摘のとおり。**
2. **語彙素に畳むこと**（lemma の問題）。こちらは畳んだほうがよい。
   1872–1959 のコーパスでは，同じ語が旧字旧仮名・文語活用で
   **時代ごとに違う表層形**になる。表層形のままだと「時代が下ると語が
   入れ替わる」という見かけの変化が生じ，通時比較（Step 6）が壊れる。
   低頻度の異形が大量にできて，ベクトルも不安定になる。

つまり**歪むのは「表層形でないから」ではなく「機能語を落としたから」**
であり，対処は `tokens_surface` ではなく **`tokens_lemma`（全品詞・語彙素）**
である——というのが，次のセルの数字で確かめる仮説である。

⚠ 表層形が要る分析もある。**文体の指標**（旧仮名の比率・活用形の分布・
「ぬ／ず」の使い分け）は表層形でなければ測れない。`00_extend_metadata.py`
が `bungo_per10k` を測るのに `tokens_surface` を使っているのはそのためである。
**目的で選ぶ。どれかが一般に正しいのではない。**

### 凡例に埋め込む語（あらかじめ決めておく）

「どこに何があるか」の手がかりとして，カテゴリの典型語を**マーカーの形**で
目立たせる。名詞の意味カテゴリ（身体・親族・自然・器物・抽象語）に加えて，
**動詞・形容詞・副詞のカテゴリ**を入れ，さらに `tokens_lemma` で学習した
ときは**文語の助動詞・口語の助動詞・人称代名詞**の3群が加わる。
カテゴリは `ANCHORS` に書いてあるので，**自分の問いに合わせて書き換えること**
（それがこのステップの課題でもある）。

⚠ `tokens_content` で学習したときは，**助動詞・助詞・代名詞はそもそも
空間に無い。** 「なり」「です」「私」を探しても見つからないのは，
モデルの失敗ではなく**入力の定義**である。上の3群が図から消えるので，
どちらの列で学習したかは図を見れば分かる。

⚠ 副詞の凡例語には注意が要る。「非常に」のような語は UniDic では
**「非常」＋「に」に切られる**ので，1語としては空間に無い。図の下に
「語彙に無い凡例語」として名指しされるので，そこで**辞書の切り方**を
確かめること（これも分析の一部である）。"""),
 ('code', r"""# ---- 4.0 3つの列を実測で比べる ----------------------------------------
# **結論を書く前に数える。** 小さいモデル（dim 100・10 epochs）を3つ作り，
#   * 異なり語数（表層形はどれだけ割れるか）
#   * 低頻度語の割合（割れるとベクトルが不安定になる）
#   * 機能語が語彙に入っているか
#   * 品詞の凝集度（＝文法的な構造が入っているか）
#   * 意味カテゴリの凝集度（＝意味的な構造が入っているか）
# を並べる。**本番の設定（dim 300）ではない**ので，数字の絶対値ではなく
# 列どうしの差を見ること。
CMP_RUN = True          # 時間が無いときは False（下の表は出ない）
CMP_DIM, CMP_EPOCHS, CMP_MIN = 100, 10, 20
CAP = 80000             # 1作品あたりの上限。06 の --max-chunks 40 ×
                        # --chunk 2000 と同じ量に揃える（長篇に空間を
                        # 支配させない）。**先頭から切るので，06 の
                        # 層化無作為とは抜き方が違う**（比較用と割り切る）
STREAMS = ['tokens_lemma', 'tokens_content', 'tokens_surface']

class StreamCorpus:
    # 作品ごとに読んで返す。**全体を記憶に載せない**（表層形の列は
    # 1万語どころではないので，素朴に list にすると数百 MB になる）。
    # gensim は2回以上反復するので，__iter__ で毎回読み直す。
    def __init__(self, d, cap=CAP, piece=10000):
        self.files = sorted(Path(d).glob('*.txt'))
        self.cap, self.piece = cap, piece
    def __iter__(self):
        for f in self.files:
            toks = f.read_text(encoding='utf-8').split()[:self.cap]
            for i in range(0, len(toks), self.piece):
                yield toks[i:i + self.piece]

def pos_of_stream(stream, need, limit=60000):
    # 語 → 品詞（大分類）。**表層形の列は TSV の surface 列で引く。**
    # 語彙素の列と同じ列で引くと1件も当たらない（0 埋めの事故と同型）。
    col = 1 if stream.endswith('surface') else 2
    out, need = {}, set(need)
    d = TOK/'tsv'
    if not d.exists():
        return out
    for f in sorted(d.glob('*.tsv')):
        if not need:
            break
        with open(f, encoding='utf-8-sig') as fh:
            next(fh, None); next(fh, None)
            for i, line in enumerate(fh):
                if i > limit:
                    break
                c = line.rstrip('\n').split('\t')
                if len(c) > 6 and c[col] in need:
                    out[c[col]] = c[6].split('-')[0]; need.discard(c[col])
    return out

def coh(idx, Xn):
    # 群内・群外の平均コサイン類似度を**厳密に**返す（行列を作らない）
    n, Nall = len(idx), len(Xn)
    if n < 2 or n >= Nall:
        return None
    sm = Xn[idx].sum(0); T = Xn.sum(0)
    return ((float(sm @ sm) - n) / (n * n - n),
            float(sm @ (T - sm)) / (n * (Nall - n)))

FUNC_PROBE = ('の に を は が と で も から まで '
              'だ です ます た ない れる せる たい '
              'なり けり ぬ つ たり べし ごとし き '
              '私 僕 君 彼 彼女 我 汝 あなた').split()
CMP_CATS = {   # 意味の凝集度を測る名詞3群（どの列にもある語で公平に）
    '親族': '父 母 兄 姉 弟 妹 妻 夫 娘 息子',
    '自然': '春 夏 秋 冬 雨 雪 風 花 月 山 海 空',
    '器物': '汽車 電車 写真 新聞 雑誌 時計 電報 郵便 銀行 洋服',
}
CMP_POS = ['名詞', '動詞', '形容詞', '副詞', '助動詞', '代名詞', '助詞']

cmp_rows, cmp_types = [], {}
if CMP_RUN:
    for st in STREAMS:
        d = TOK/st
        if not d.exists():
            print(f'[warn] {d} が無い（05 を --lemma-policy のまま走らせると'
                  '3つとも出る）。飛ばす。')
            continue
        corpus = StreamCorpus(d)
        cnt = Counter(w for piece in corpus for w in piece)
        m = Word2Vec(corpus, vector_size=CMP_DIM, window=W2V_WIN,
                     min_count=CMP_MIN, sg=1, workers=4,
                     epochs=CMP_EPOCHS, seed=SEEDS[0])
        V = list(m.wv.index_to_key)
        Xc = np.vstack([m.wv[w] for w in V]).astype(np.float32)
        Xc /= np.linalg.norm(Xc, axis=1, keepdims=True) + 1e-12
        idx_of = {w: i for i, w in enumerate(V)}
        pm = pos_of_stream(st, V)
        p1 = np.array([pm.get(w, '') for w in V])

        gaps = []
        for pos in CMP_POS:
            r = coh(np.where(p1 == pos)[0], Xc)
            if r:
                gaps.append(r[0] - r[1])
        sgaps = []
        for k, ws in CMP_CATS.items():
            ii = [idx_of[w] for w in ws.split() if w in idx_of]
            r = coh(np.array(ii), Xc) if len(ii) > 1 else None
            if r:
                sgaps.append(r[0] - r[1])
        low = float(np.mean([cnt[w] < 100 for w in V]))
        nf = sum(1 for w in FUNC_PROBE if w in idx_of)
        cmp_types[st] = len(V)
        cmp_rows.append({
            '語の列': st.replace('tokens_', ''),
            '延べ語数': sum(cnt.values()),
            'モデル語彙': len(V),
            '低頻度語（<100）': low,
            f'機能語（/{len(FUNC_PROBE)}）': nf,
            '品詞の凝集度（中央値）': float(np.median(gaps)) if gaps else np.nan,
            '意味の凝集度（中央値）': float(np.median(sgaps)) if sgaps else np.nan,
            '品詞を引けた語': float((p1 != '').mean())})

    if cmp_rows:
        show(pd.DataFrame(cmp_rows),
             caption=f'語の列を変えるとどうなるか'
                     f'（dim {CMP_DIM}・window {W2V_WIN}・'
                     f'min_count {CMP_MIN}・1作品 {CAP:,} 語まで）',
             fmt={'延べ語数': '{:,.0f}', 'モデル語彙': '{:,.0f}',
                  '低頻度語（<100）': '{:.1%}',
                  f'機能語（/{len(FUNC_PROBE)}）': '{:.0f}',
                  '品詞の凝集度（中央値）': '{:+.3f}',
                  '意味の凝集度（中央値）': '{:+.3f}',
                  '品詞を引けた語': '{:.0%}'})
        if 'tokens_surface' in cmp_types and 'tokens_lemma' in cmp_types:
            r = cmp_types['tokens_surface'] / max(1, cmp_types['tokens_lemma'])
            print(f'表層形の異なり語数は語彙素の {r:.2f} 倍。'
                  'これが**活用形と旧仮名で割れた分**である。')
        print('読み方: 「品詞の凝集度」が高い列は**文法的な構造**を，'
              '「意味の凝集度」が高い列は**意味的な構造**をよく写している。')
        print('機能語の数が 0 の列（content）では，'
              '**助動詞・助詞・代名詞の分析はできない**。')"""),
 ('code', r"""# ---- 4.0b ギャラクシーに使うモデルを決める ----------------------------
# 既定は **tokens_lemma**（語彙素・全品詞）。理由は上の表のとおり，
#   * 機能語が空間に入る → 文語/口語の助動詞や代名詞を図に置ける
#   * 活用と旧仮名を畳む → 低頻度の異形が減り，ベクトルが安定する
#   * 通時比較（Step 6）と同じ単位で語を数えられる
# 表層形で見たいときは 'tokens_surface' に書き換えてよい。**そのときは
# 図の副題に列名が入るので，どちらで描いたかは残る。**
GAL_STREAM = 'tokens_lemma'
if not (TOK/GAL_STREAM).exists():
    print(f'[warn] {TOK/GAL_STREAM} が無いので tokens_content で描く')
    GAL_STREAM = 'tokens_content'

# 学習し直すのは時間がかかるので，**一度作ったら保存して使い回す**。
# 設定を変えたらファイル名が変わるので，古いモデルを拾うことはない。
gal_path = OUT/f'w2v_gal_{GAL_STREAM}_d{W2V_DIM}_w{W2V_WIN}_s{SEEDS[0]}.model'
if gal_path.exists():
    GAL_W2V = Word2Vec.load(str(gal_path))
    print(f'[load] {gal_path.name}（語彙 {len(GAL_W2V.wv):,}）')
    print('       学習し直したいときはこのファイルを消すこと。')
else:
    print(f'[fit ] {GAL_STREAM} で学習する'
          f'（dim {W2V_DIM}・window {W2V_WIN}・数分かかる）')
    GAL_W2V = Word2Vec(StreamCorpus(TOK/GAL_STREAM), vector_size=W2V_DIM,
                       window=W2V_WIN, min_count=20, sg=1, workers=4,
                       epochs=20, seed=SEEDS[0])
    GAL_W2V.save(str(gal_path))
    print(f'[save] {gal_path.name}（語彙 {len(GAL_W2V.wv):,}）')"""),
 ('code', r"""# ---- 語彙のギャラクシー ----------------------------------------------
GAL_N   = 10000         # 表示する語数（モデル内の頻度上位）
GAL_K   = 10            # 近傍の数（原空間・画面ともに）
GAL_SEED = 20260920
TW_SAMPLE = 2000        # 信頼度を測る標本数（全点で測ると O(N²) になる）

# カテゴリの典型語。**マーカーの形**で示す。名詞の意味カテゴリだけでなく，
# 動詞・形容詞・副詞のカテゴリも入れる。**形は多くても11まで**にして，
# 名前は図に直接書く（形だけで見分けさせない）。
ANCHORS = {
    '身体・知覚〈名〉':   '顔 目 手 足 胸 声 髪 唇 肩 涙 頭 腕',
    '親族・人〈名〉':     '父 母 兄 姉 弟 妹 妻 夫 娘 息子 祖母 子供',
    '自然・季節〈名〉':   '春 夏 秋 冬 雨 雪 風 花 月 山 海 空 星 桜',
    '近代の器物〈名〉':   '汽車 電車 写真 新聞 雑誌 時計 電報 郵便 銀行 洋服',
    '近代の抽象語〈名〉': '自由 権利 社会 国家 文明 科学 精神 恋愛 個人 思想 芸術',
    '感情の動詞〈動〉':   '泣く 笑う 驚く 怒る 喜ぶ 悲しむ 苦しむ 恐れる 愛する 憎む',
    '属性の形容詞〈形〉': '大きい 小さい 白い 赤い 黒い 青い 長い 短い 高い 低い 深い 美しい',
    '程度・時の副詞〈副〉': '少し やがて しばらく 突然 ふと いつも しきりに ようやく まるで すでに',
}
# **機能語の3群は，機能語が空間にある列（tokens_lemma / tokens_surface）
# でだけ足す。** tokens_content で足すと「語彙に無い凡例語」が並ぶだけで，
# 「このカテゴリは空間に無い」という誤解を生む。
ANCHORS_FUNC = {
    '文語の助動詞〈助動〉': 'なり けり ぬ つ たり べし ごとし き む らむ ず',
    '口語の助動詞〈助動〉': 'だ です ます た ない れる せる たい らしい そうだ',
    '人称代名詞〈代〉':   '私 僕 俺 君 彼 彼女 我 汝 あなた おまえ わたくし',
}
if globals().get('GAL_STREAM', 'tokens_content') != 'tokens_content':
    ANCHORS.update(ANCHORS_FUNC)
CAT_M = ['o', 's', '^', 'D', 'v', 'P', 'X', '*', '<', '>', 'h']

# 色は**品詞**に割り当てる。6色とも dataviz の検証器を全ペアで通してある
#   node scripts/validate_palette.js \
#     "#0072B2,#D55E00,#009E73,#CC79A7,#E69F00,#56B4E9" \
#     --mode light --pairs all      → ALL CHECKS PASS
# 形容詞と副詞の対は色覚差が 7.6（6–8 の下限帯）なので，**色だけに
# 頼らせない**。マーカーの形・図に直接置くカテゴリ名・表の3つで二重に示す。
# **助詞は灰色にまとめる**（7色は散布図で見分けられない）。凡例で
# 「助詞・その他」と名指しするので，灰色の意味は曖昧にならない。
POS_C = {'名詞': '#0072B2', '動詞': '#D55E00',
         '形容詞': '#009E73', '副詞': '#CC79A7',
         '助動詞': '#E69F00', '代名詞': '#56B4E9'}
POS_GREY = '#c9c8c1'

# §4.0b で決めたモデル（既定は tokens_lemma）。無ければ §2 のモデル
wv = globals().get('GAL_W2V', w2v).wv
GAL_STREAM = globals().get('GAL_STREAM', 'tokens_content')
print(f'ギャラクシーの入力: {GAL_STREAM}（語彙 {len(wv):,}）')
def _count(w):
    try:    return int(wv.get_vecattr(w, 'count'))
    except Exception:  return int(vocab.get(w, 0))

gal = list(wv.index_to_key[:GAL_N])
acat = {}
for k, ws in ANCHORS.items():
    for w in ws.split():
        if w in wv:
            acat[w] = k
# **凡例の語は頻度で切らずに必ず入れる。** 入っていない語が凡例にあると，
# 「このカテゴリは空間に無い」と誤解される。落ちた語は下で名指しする。
extra = [w for w in acat if w not in set(gal)]
gal += extra
miss = [w for k, ws in ANCHORS.items() for w in ws.split() if w not in wv]
print(f'表示 {len(gal)} 語（頻度上位 {min(GAL_N, len(wv))} '
      f'＋ 凡例の追加 {len(extra)} 語）')
if extra:
    print('  頻度順では入らないが凡例なので追加:', ' '.join(extra))
if miss:
    print(f'  [warn] モデルの語彙に無い凡例語 {len(miss)} 語: ' + ' '.join(miss))
    print(f'         min_count 未満／{GAL_STREAM} に無い品詞'
          '／**辞書が複合語を切っている**（「非常に」→「非常」＋「に」）'
          'のいずれか。どれなのかは Step 3 の TSV で確かめられる。')
    if GAL_STREAM == 'tokens_content':
        print('         いまは内容語の列なので，**助動詞・助詞・代名詞は'
              'そもそも空間に無い**（入力の定義による）。')

X  = np.vstack([wv[w] for w in gal]).astype(np.float32)
Xn = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-12)
print(f'原空間: {X.shape[0]} 語 × {X.shape[1]} 次元')

# ---- 2次元に落とす ----------------------------------------------------
# GAL_PROJ = 'umap'  … UMAP で描く。**使えなければ止まって理由を出す**
#            'tsne'  … t-SNE で描く
#            'auto'  … UMAP を試し，駄目なら t-SNE に落ちる（理由は出す）
#
# ⚠ 既定を 'umap' にしてある。**黙って t-SNE に落ちるのがいちばん悪い。**
# 図は出るが塊の見え方が変わるので，設定の問題を分析結果と読み違える。
# 原因の切り分けは  python3 scripts/check_umap.py  が全部やってくれる。
GAL_PROJ = 'umap'
import time as _time

# 1万語だと UMAP で1–3分，t-SNE ではもっとかかる。**待つこと。**
# 初回は numba の JIT に十数秒余分にかかる。
_t0 = _time.time()
P, METHOD = project(Xn, GAL_PROJ, GAL_SEED)
print(f'射影: {METHOD}（{_time.time()-_t0:.0f} 秒）')

# ---- 原空間の近傍と，射影がどれだけ嘘をつくか ------------------------
# ⚠ 1万語の全対行列は 10000×10000 で 400 MB になる。**作らない。**
# 行を 512 語ずつに切って上位 K だけ残す（結果は全対と同じ）。
N = len(gal)
nn_true = np.empty((N, GAL_K), dtype=np.int32)
for a in range(0, N, 512):
    b = min(a + 512, N)
    s = Xn[a:b] @ Xn.T                      # 512×N。これなら 20 MB 程度
    s[np.arange(b - a), np.arange(a, b)] = -np.inf   # 自分を外す
    idx = np.argpartition(-s, GAL_K, axis=1)[:, :GAL_K]
    ord_ = np.argsort(-np.take_along_axis(s, idx, 1), axis=1)
    nn_true[a:b] = np.take_along_axis(idx, ord_, 1)
    del s

# 画面（2次元）の近傍は k-d 木で引く。全対距離を作る必要はない
from scipy.spatial import cKDTree
_, nn_proj = cKDTree(P).query(P, k=GAL_K + 1)
nn_proj = nn_proj[:, 1:]                    # 先頭は自分自身
keep = np.array([len(set(a) & set(b)) for a, b in zip(nn_true, nn_proj)])

# 信頼度は O(N²) なので**無作為標本**で測る。標本で測ったことを明記する
from sklearn.manifold import trustworthiness
rng = np.random.default_rng(GAL_SEED)
sub = (np.arange(N) if N <= TW_SAMPLE
       else np.sort(rng.choice(N, TW_SAMPLE, replace=False)))
TW = trustworthiness(Xn[sub], P[sub], n_neighbors=GAL_K, metric='cosine')
TW_NOTE = ('全点' if len(sub) == N else f'{len(sub)} 語の無作為標本')
print(f'信頼度 trustworthiness = {TW:.3f}（{TW_NOTE}で測定）'
      f'／近傍保存 平均 {keep.mean():.1f}/{GAL_K} 語')
print('**平均で半分も残らないのが普通である。** 2次元の距離を'
      '「意味の距離」として読んではいけない。')

# ---- 品詞 -------------------------------------------------------------
# 色を品詞に使うので，ここは**図の主役**である。語彙素→品詞の対応を
# Step 3 の TSV から拾う。1万語ぶん要るので，各ファイルの先頭 60000 行
# までを見て，必要な語が揃った時点で止める。
# ⚠ **表層形の列で学習したときは TSV の surface 列で引く。**
# 語彙素の列で引くと1件も当たらず，全部が灰色になる（0 埋めの事故と同型）。
POS_COL = 1 if GAL_STREAM.endswith('surface') else 2
posmap, need = {}, set(gal)
tsvdir = TOK/'tsv'
if tsvdir.exists():
    for f in sorted(tsvdir.glob('*.tsv')):
        if not need: break
        with open(f, encoding='utf-8-sig') as fh:
            next(fh, None); next(fh, None)
            for i, line in enumerate(fh):
                if i > 60000: break
                c = line.rstrip('\n').split('\t')
                if len(c) > 6 and c[POS_COL] in need:
                    posmap[c[POS_COL]] = c[6]; need.discard(c[POS_COL])
else:
    print('[info ] data/tokens/tsv が無い')

# 残った語は1語ずつ辞書に当てる（本文の文脈は無いので推定になる）。
# **文脈なしの解析なので，多義の語では取りこぼす。** それでも
# 色分けできないより良い。何語をこの方法で埋めたかは下に出す。
n_iso = 0
if need:
    try:
        import fugashi, yaml
        _dd = (yaml.safe_load(open(ROOT/'config'/'pipeline.yaml',
                                   encoding='utf-8'))
               .get('tokenise', {}).get('dicdir'))
        tg = fugashi.GenericTagger(f'-d {_dd}') if _dd else fugashi.Tagger()
        for w in list(need):
            ws = tg(w)
            if len(ws) == 1:
                p = str(ws[0].feature[0])
                if p:
                    posmap[w] = p; need.discard(w); n_iso += 1
    except Exception as e:                            # noqa: BLE001
        print(f'[info ] 単語単位の品詞付与は使えない（{type(e).__name__}）')

pos1 = np.array([str(posmap.get(w, '')).split('-')[0] for w in gal])
cov = float(np.isin(pos1, list(POS_C)).mean())
print(f'品詞を引けた語: {len(posmap)}/{len(gal)}'
      + (f'（うち {n_iso} 語は単語単位で推定）' if n_iso else '')
      + f'／4品詞に収まった語 {cov:.1%}')
# **覆えていないのに色分けすると，灰色の意味が「不明」から「その他の品詞」へ
# 静かにすり替わる。** 6割を下回るときは色分けをやめ，そのことを図に書く。
POS_OK = cov >= 0.60
if not POS_OK:
    print('[warn] 品詞の取得率が低いので**色分けはしない**。'
          'Step 3 を走らせて data/tokens/tsv を作ると色が付く。')

cat = np.array([acat.get(w, '') for w in gal])
print(f'凡例のカテゴリに入る語: {int((cat != "").sum())} 語／'
      f'その他 {int((cat == "").sum())} 語')
""" ),
 ('code', r"""# ---- 図（静止版 SVG ＋ 対話版 HTML）----------------------------------
# **色 = 品詞（文法）／マーカーの形 = カテゴリ（意味）** の二重の色分け。
# 凡例も2つ出す（ax.add_artist で重ねる）。
fig, ax = plt.subplots(figsize=(13.5, 9.6))
other = cat == ''

# 地の1万語。**品詞ごとに分けて描く**ことで，凡例の色が実体と結びつく。
# 1万点はラスタ化しないと SVG が数十 MB になる
if POS_OK:
    for p, c in POS_C.items():
        mk = other & (pos1 == p)
        if mk.any():
            ax.scatter(P[mk, 0], P[mk, 1], s=4.5, color=c, alpha=.40,
                       linewidth=0, rasterized=True)
    mk = other & ~np.isin(pos1, list(POS_C))
    if mk.any():
        ax.scatter(P[mk, 0], P[mk, 1], s=4.5, color=POS_GREY, alpha=.45,
                   linewidth=0, rasterized=True)
else:
    ax.scatter(P[other, 0], P[other, 1], s=4.5, color=POS_GREY, alpha=.5,
               linewidth=0, rasterized=True)

# 凡例の語。形はカテゴリ，中の色は品詞（＝その語が実際に何と解析されたか）
cx, cy, cl = [], [], []
for ci, k in enumerate(ANCHORS):
    mk = cat == k
    if not mk.any():
        continue
    fc = [POS_C.get(p, '#6f6d66') if POS_OK else '#33322e' for p in pos1[mk]]
    ax.scatter(P[mk, 0], P[mk, 1], s=(100 if CAT_M[ci] == '*' else 62),
               c=fc, marker=CAT_M[ci], edgecolor='white', linewidth=.8,
               zorder=3)
    # 位置は平均ではなく中央値。外れた1語で札の位置が飛ばないように。
    cx.append(float(np.median(P[mk, 0]))); cy.append(float(np.median(P[mk, 1])))
    cl.append(k)

ax.set_title(
    f'語彙のギャラクシー（{GAL_STREAM.replace("tokens_", "")}・'
    f'word2vec {X.shape[1]}次元 → {METHOD.split()[0]} 2次元）'
    f'／{len(gal):,} 語・信頼度 {TW:.2f}・近傍保存 {keep.mean():.1f}/{GAL_K}')
ax.set_xlabel('※ 軸に意味は無い。近さだけが意味を持つ（しかも近さも射影の結果である）'
              '／色＝品詞（文法的なまとまり）・マーカーの形＝カテゴリ（意味的なまとまり）')
ax.set_xticks([]); ax.set_yticks([])
for sp in ax.spines.values():
    sp.set_visible(False)

from matplotlib.lines import Line2D
h_pos = []
if POS_OK:
    for p, c in POS_C.items():
        n = int((pos1 == p).sum())
        h_pos.append(Line2D([], [], marker='o', ls='', ms=7, color=c,
                            label=f'{p}（{n:,}）'))
    n = int((~np.isin(pos1, list(POS_C))).sum())
    if n:
        # **灰色が何なのかを名指しする。** 「その他」だけだと，
        # 引けなかった語と助詞の区別がつかない。
        n_jo = int((pos1 == '助詞').sum())
        lab = (f'助詞（{n_jo:,}）・その他・不明（{n - n_jo:,}）'
               if n_jo else f'その他・不明（{n:,}）')
        h_pos.append(Line2D([], [], marker='o', ls='', ms=7, color=POS_GREY,
                            label=lab))
h_cat = [Line2D([], [], marker=CAT_M[ci], ls='', ms=(11 if CAT_M[ci] == '*' else 8),
                color='#55534d', markeredgecolor='white',
                label=f'{k}（{int((cat == k).sum())}）')
         for ci, k in enumerate(ANCHORS) if (cat == k).any()]
if h_pos:
    lg1 = ax.legend(handles=h_pos, title='色 ＝ 品詞', frameon=False,
                    fontsize=8.5, title_fontsize=9, loc='upper left',
                    bbox_to_anchor=(1.01, 1.0), borderaxespad=0)
    ax.add_artist(lg1)
ax.legend(handles=h_cat, title='マーカーの形 ＝ カテゴリ', frameon=False,
          fontsize=8.5, title_fontsize=9, loc='upper left',
          bbox_to_anchor=(1.01, 0.70 if h_pos else 1.0), borderaxespad=0)
fig.tight_layout()
# 凡例2つを面の外に置いてあるので，**右の余白を確保する**。
# しないと HTML に埋め込む版（切り取らない版）で凡例が切れる。
reserve_right(fig, 0.78)
# カテゴリ名は図に直接置く（色と形だけに頼らせない）。
# ⚠ label_points は tight_layout の**後**に呼ぶこと（座標が動く）
label_points(ax, cx, cy, cl, fontsize=10, color='#33322e')

tips = []
for i, w in enumerate(gal):
    tips.append({
        'term': w,
        'fields': [('品詞', posmap.get(w, '—')),
                   ('頻度', f'{_count(w):,}'),
                   ('カテゴリ', acat.get(w, '（その他）')),
                   ('近傍保存', f'{int(keep[i])}/{GAL_K}')],
        # 原空間での近傍。**画面の近さではない**ので必ず併記する。
        # 本文は None にして links から並べさせる（HTML が 1 MB 近く軽くなる）
        'notes': ('原空間の近傍:', None),
        'links': [int(j) for j in nn_true[i]],
    })

# 表は全1万件を載せると HTML が数 MB になり，読む側にも役に立たない。
# **凡例の語を先に置き**，あとは頻度上位から埋める（点はすべて指せる）。
TABLE_MAX = 600
t_idx = [i for i, w in enumerate(gal) if w in acat]
t_idx += [i for i in range(len(gal)) if i not in set(t_idx)][:max(
    0, TABLE_MAX - len(t_idx))]

save_interactive(fig, ax, 'Step5_galaxy', P[:, 0], P[:, 1], tips,
                 source=DS/'chunks', id_col='語',
                 title=f'語彙のギャラクシー（{METHOD}）',
                 note=(f'語の列 {GAL_STREAM}／'
                       f'word2vec {X.shape[1]}次元（window {W2V_WIN}）を'
                       f'2次元に射影／{len(gal):,} 語／'
                       f'信頼度 {TW:.3f}（{TW_NOTE}）／'
                       f'近傍保存 平均 {keep.mean():.1f}/{GAL_K}／'
                       f'シード {GAL_SEED}／色＝品詞・マーカーの形＝カテゴリ'),
                 hint=('点にカーソルを近づけると語が出て，**原空間での近傍10語'
                       'へ線が伸びる**。線が遠くまで伸びる点は，その語の近傍が'
                       '2次元に収まっていない。検索すると図の上にもマーカーが付く'
                       '（表に無い語も図の上では指せる）。'),
                 table_cols=['品詞', '頻度', 'カテゴリ', '近傍保存'],
                 table_idx=t_idx)
plt.show()"""),
 ('md', r"""### まとまっているのは本当か — 目で見ずに数える

「親族語が固まって見える」のは，**こちらがそう並べたから**かもしれない。
射影の癖でそう見えているだけかもしれない。原空間（300次元）で測り直す。
群内の平均コサイン類似度が，群外との平均よりどれだけ高いかを見る。

⚠ 1万語の全対類似度行列は作らない（400 MB になる）。平均は
和のノルムから**厳密に**出せる。群の和を s，群の語数を n とすると

    Σ_{i,j∈群} x_i·x_j = ‖s‖²  ⇒  群内の平均 = (‖s‖² − n) / (n² − n)

（対角の n 個は自分自身との類似度 1 なので引く。全対を並べるのと同じ値で，
記憶は使わない。）まず**品詞**（文法），次に**カテゴリ**（意味）で測る。"""),
 ('code', r"""def coh(idx, Xn):
    # 群内・群外の平均コサイン類似度を**厳密に**返す（行列を作らない）
    n, Nall = len(idx), len(Xn)
    if n < 2 or n >= Nall:
        return None
    s = Xn[idx].sum(0)
    T = Xn.sum(0)
    intra = (float(s @ s) - n) / (n * n - n)
    inter = float(s @ (T - s)) / (n * (Nall - n))
    return intra, inter

T_all = Xn.sum(0)
allmean = (float(T_all @ T_all) - len(Xn)) / (len(Xn) ** 2 - len(Xn))

rows = []
if POS_OK:
    for p in POS_C:
        idx = np.where(pos1 == p)[0]
        r = coh(idx, Xn)
        if r is None: continue
        rows.append({'品詞': p, '語数': len(idx), '品詞内': r[0],
                     '品詞外': r[1], '差': r[0] - r[1],
                     '近傍保存の中央値': float(np.median(keep[idx]))})
    show(pd.DataFrame(rows).sort_values('差', ascending=False),
         caption=f'**文法的なまとまり**：品詞の凝集度'
                 f'（原空間のコサイン類似度／全体平均 {allmean:+.3f}）',
         fmt={'品詞内': '{:+.3f}', '品詞外': '{:+.3f}', '差': '{:+.3f}',
              '語数': '{:,.0f}', '近傍保存の中央値': '{:.1f}'})
    print('差が正なら，**同じ品詞の語は互いに近い**。')
    print('window を 3 に狭めてあるので，この差は大きく出るはずである'
          '（ウィンドウを 10 に広げて測り直すと縮む。演習 1 の続きとしてやってみよ）。')
else:
    print('[info ] 品詞が引けていないので品詞の凝集度は測れない。')"""),
 ('code', r"""rows = []
for k in ANCHORS:
    idx = np.where(cat == k)[0]
    r = coh(idx, Xn)
    if r is None:
        continue
    # 画面上での散らばり（中央値からの距離の中央値）も出す
    spread = float(np.median(np.hypot(P[idx, 0] - np.median(P[idx, 0]),
                                      P[idx, 1] - np.median(P[idx, 1]))))
    rows.append({'カテゴリ': k, '語数': len(idx),
                 'カテゴリ内の平均類似度': r[0],
                 'カテゴリ外との平均類似度': r[1],
                 '差': r[0] - r[1],
                 '画面上の散らばり': spread,
                 '近傍保存の中央値': float(np.median(keep[idx]))})
show(pd.DataFrame(rows).sort_values('差', ascending=False),
     caption=f'**意味的なまとまり**：カテゴリの凝集度'
             f'（原空間のコサイン類似度／全体平均 {allmean:+.3f}）',
     fmt={'カテゴリ内の平均類似度': '{:+.3f}', 'カテゴリ外との平均類似度': '{:+.3f}',
          '差': '{:+.3f}', '画面上の散らばり': '{:.2f}',
          '近傍保存の中央値': '{:.1f}'})
print('「差」が大きいカテゴリは，**空間の中で実際にまとまっている**。')
print('小さいカテゴリは，こちらが名前でまとめただけで，モデルはそう見ていない。')
print('画面上の散らばりが小さいのに差も小さい場合は，**射影がまとめて見せている**'
      'だけの可能性がある。原空間の数字のほうを信じること。')
print()
print('**2つの表を並べて読むこと。** 品詞でも差が出て，同じ品詞の中の'
      '意味カテゴリでも差が出る——これが「文法的な関係と意味的な関係の'
      '両方がモデル化されている」ということの中身である。')"""),
 ('md', r"""### 演習 3 — ギャラクシーを自分の問いで色分けし直す

1. `ANCHORS` を**自分の問いのカテゴリ**に書き換えよ（8つまで＝マーカーの形の数）。
   例: 視覚語／聴覚語・和語／漢語・自然主義の語彙／プロレタリア文学の語彙。
   **品詞をまたぐカテゴリ**（例「戦争を語る語」＝名詞＋動詞＋形容詞）を
   立てると，色がばらけて形だけが揃う。これは
   「意味では揃うが文法では揃わない」カテゴリの見え方である。
   書き換えたら**凝集度の表**を見て，そのカテゴリが空間でまとまっているかを
   確かめる。まとまっていなければ，カテゴリの立て方を疑う。
2. **近傍保存が低い語**（対話版で線が遠くまで伸びる語）を3語選び，
   なぜ2次元に収まらないのかを考えよ。多義語か，頻度が低いか，
   複数の文脈にまたがる語か。
3. `GAL_N` を 1000 / 3000 / 10000 と変え，**信頼度がどう変わるか**を記録せよ。
   語を増やすと図は賑やかになるが，射影の嘘は増えるか減るか。
   （`W2V_WIN` を 10 にして学習し直し，**品詞の凝集度がどう動くか**も見よ。
   狭いウィンドウが文法を拾っているという説明が正しければ，差は縮むはずである。）
4. `min_dist` を 0.0 / 0.12 / 0.5 と変えると，塊の締まり方が変わる。
   **これは分析結果ではなく作図の設定である。** 締まった図を見て
   「クラスタがある」と言ってよいか，理由とともに述べよ。
5. ⚠ UMAP / t-SNE の座標は**シードに依存する**。`GAL_SEED` を変えて2回描き，
   同じ結論が言えるかを確かめよ。言えないなら，その結論は図の癖である。"""),
 ('md', r'''## 5. このステップの課題

次の設問への答えを，テンプレート `my_work/results/Step5_report.md` に書いて提出する（**全体で600–1000字程度**。図表と「再現のための情報」は字数に含めない）。

- **提出先**：Zulip（{ZULIP_ORG}）の非公開チャネル **{ZULIP_CHANNEL}** ＞ トピック **Step 5**
- テンプレートの中身をメッセージに貼り付け，図（SVG）・表（CSV）は**同じメッセージに添付**する（1人1通）
- 図は番号で言及し（図1），**図を見なくても論旨が追えるように**書く（SVG は Zulip で表示されないことがある）
- 再提出は元の投稿を直さず，同じトピックに新しく投稿する（手順書 §5.3）

1. 共起行列＋PPMI＋SVD と word2vec の近傍を5語について比較し，
   違いを記述すること。**どちらが良いかではなく，何が違うか**を書く。
2. `window` と `min_count` を変えた3条件で，同じ語の近傍表を作ること。
3. 安定性検査を自分の関心のある語10語で行い，
   **解釈に耐える語／耐えない語**を分類すること。
4. 次の Step のために，自分が通時的に追いたい語を **5語**選び，
   選んだ理由（文学史的な仮説）を書いてくること。

### このステップの到達点（次へ進む条件）

- コーパス全体で word2vec を学習でき，近傍語を取り出せる
- シードを変えた複数回の学習で，近傍の一致率（Jaccard）を測れる
- **解釈に耐える語と耐えない語**を区別でき，その基準を言える
- 通時的に追う5語を選び，仮説を文章にしてある
'''),
])

# ==========================================================================
L(6, '通時的 word2vec — 時代スライスと Procrustes アラインメント', [
 ('md', r'''# Step 6 通時的 word2vec — 時代スライスと Procrustes アラインメント

## このステップの到達目標

1. 独立に学習した word embeddings が**そのままでは比較できない**理由を説明できる
2. 直交 Procrustes 変換によるアラインメントを実装・実行できる
3. 意味変化の指標を計算し，交絡（語彙量・作家）を統制できる
4. 検出された「変化」が本物かを検証する手順を持つ

## 導入：なぜそのまま比べられないのか

word2vec の目的関数は**回転に対して不変**である。
すべてのベクトルを同じ直交行列で回しても，内積（＝コサイン類似度）は変わらない。
したがって学習のたびに空間全体の向きが変わる。

> 明治期モデルの「恋」と昭和期モデルの「恋」のコサイン類似度を直接計算しても，
> **それは意味の違いではなく，座標系の違いを測っている。**

解決は Hamilton, Leskovec & Jurafsky (2016) の方法である。

1. 両モデルに共通する語彙を取る
2. その部分行列どうしを最もよく重ねる**直交行列 R** を求める
   （$R = UV^\top$ where $U\Sigma V^\top = \mathrm{SVD}(B^\top A)$）
3. 回転だけを許すので，**語どうしの距離構造は保たれる**

## 参考
- Hamilton, Leskovec & Jurafsky (2016) Diachronic word embeddings reveal statistical laws of semantic change. *ACL*.
- Kim et al. (2014) Temporal analysis of language through neural language models. *ACL Workshop*.
- Dubossarsky et al. (2017) Outta control: laws of semantic change and inherent biases. *EMNLP*.
'''),
 ('code', PREAMBLE),
 ('md', r'''## 1. Procrustes アラインメントを手で実装する

まず小さな例で，回転してもコサインが変わらないことを確かめる。'''),
 ('code', r'''rng = np.random.default_rng(0)
A = rng.normal(size=(200, 50))
Q,_ = np.linalg.qr(rng.normal(size=(50,50)))       # ランダムな直交行列
B = A @ Q                                          # A を回転しただけ

def cos(a,b): return float(a@b/(np.linalg.norm(a)*np.linalg.norm(b)))
print('同じ2語の内部的なコサイン（A空間）:', round(cos(A[0],A[1]),4))
print('同じ2語の内部的なコサイン（B空間）:', round(cos(B[0],B[1]),4), '← 不変')
print('A と B の「同じ語」のコサイン          :', round(cos(A[0],B[0]),4), '← 無意味')

def procrustes(base, other):
    U,_,Vt = np.linalg.svd(other.T @ base)
    return U @ Vt
R = procrustes(A, B)
print('アラインメント後の「同じ語」のコサイン :', round(cos(A[0], (B@R)[0]),4), '← 回復')'''),
 ('md', r'''## 2. 時代スライスの設計

スライスの切り方は**結果を決める**。本コーパスの制約を思い出す。

| 時代区分 | 語数 | 判断 |
|---|---:|---|
| 明治前期（〜1886） | 54,035 | 単独では学習不可 |
| 明治中期（1887–1899） | 222,610 | 単独では不安定 |
| 明治後期（1900–1911） | 682,188 | ぎりぎり |
| 大正（1912–1925） | 1,168,853 | 可 |
| 昭和戦前（1926–1944） | 2,556,493 | 可 |
| 昭和戦後（1945–） | 1,366,057 | 可 |

**明治期3区分を合わせても 96万語**。そのままでは 6 スライスに切れない。
現実的な選択肢は

- (a) 明治（〜1911）／大正（1912–25）／昭和戦前／昭和戦後 の **4スライス**
- (b) 戦前（〜1944）／戦後 の **2スライス**（最も頑健）
- (c) 増補を待って 6 スライス

本授業では (a) を既定とし，(b) で結果を確認する。'''),
 ('code', r'''DS = ROOT/'data'/'datasets'
ci = pd.read_csv(DS/'chunks_index.csv')

# period が空のチャンクを先に始末する。**ここが通時分析の分かれ目である。**
# 06 の突合が外れると period は NaN になる。NaN を「その他」に落とす
# 書き方（`return 'D_昭和戦後'` のような最後の return）だと，
# **素性の知れないチャンクが昭和戦後に混ざったまま**通時変化の図ができる。
# 時代が分からないものは，時代の分析から外すのが正しい。
bad = ci.period.isna() | (ci.period.astype(str).str.strip() == '')
if bad.any():
    LAB = work_labels()
    works = sorted({str(w) for w in ci.loc[bad, 'work_stem']})
    print(f'[warn] **period が空のチャンクが {int(bad.sum()):,} / {len(ci):,} ある**')
    print(f'       作品数にして {len(works)} 点。時代スライスから外す。')
    # 作品名だけを流すのではなく，**何チャンク落ちるか**まで出す。
    # 落ちる量が分からないと，無視してよい漏れかどうかが判断できない。
    cnt = ci.loc[bad, 'work_stem'].astype(str).value_counts()
    show(pd.DataFrame({'作品': [LAB.get(w, w) for w in cnt.index],
                       'work_stem': cnt.index,
                       '落ちるチャンク数': cnt.values}),
         caption='時代スライスから外れる作品（period が空）')
    print('       原因は 06_build_datasets.py の突合漏れである。')
    print('       Step 3 で 00_extend_metadata.py → 06 を走らせ直し，')
    print('       meta_unmatched.csv が空になってから戻ってくること。')

def coarse(p):
    p = str(p)
    if p.startswith(('1_','2_','3_')): return 'A_明治(〜1911)'
    if p.startswith('4_'):             return 'B_大正(1912-25)'
    if p.startswith('5_'):             return 'C_昭和戦前(1926-44)'
    if p.startswith('6_'):             return 'D_昭和戦後(1945-)'
    # ここに来るのは period の綴りが想定外のとき。黙って昭和戦後に
    # 入れてはいけない。NaN にして後段の dropna で落とす。
    return None

ci['slice4'] = ci.period.map(coarse)
ci['slice2'] = np.where(
    ci.period.astype(str).str.startswith(('1_','2_','3_','4_','5_')),
    'PRE_戦前', 'POST_戦後')

ci.loc[bad, 'slice2'] = None          # 時代不明は戦前/戦後の別も付けない

unknown = ci.slice4.isna()
if unknown.any() and not bad.all():
    odd = sorted({str(x) for x in ci.loc[unknown & ~bad, 'period']})
    if odd:
        print(f'[warn] period の綴りが想定外のチャンクがある: ' + '，'.join(odd[:5]))

# **索引そのものは削らずに書き戻す。** ここで `ci = ci[~bad]` としてから
# 保存すると，突合の外れたチャンクが索引から消え，あとから
# 「何件落ちたのか」を確かめられなくなる。列を足すだけにして，
# 分析には下の `sl` を使う。
ci.to_csv(DS/'chunks_index.csv', index=False, encoding='utf-8-sig')

sl = ci.dropna(subset=['slice4'])      # ← 以後のスライス学習はこれを使う
print(sl.slice4.value_counts().sort_index().to_string())
print()
print(sl.slice2.value_counts().to_string())
print(f'\nスライスに使うチャンク {len(sl):,} / {len(ci):,} 件'
      f'（作品 {sl.work_stem.nunique()} 点）')'''),
 ('md', r'''### 統制すべき交絡（Dubossarsky et al. 2017 の警告）

彼らは，**語をランダムにシャッフルした「偽の時系列」でも
意味変化の法則（頻度が高い語ほど変化しない等）が再現されてしまう**ことを示した。
つまり，観測された「法則」の多くは word embedding の統計的性質の産物である。

したがって最低限，次を統制する。

1. **スライスごとの語数を揃える**（`--balance`）
2. **作家の偏りを抑える**（`--max-per-author`）
3. **乱数を変えて複数回**学習し，安定した変化だけを報告する
4. **コントロール実験**：時代ラベルをシャッフルして同じ手順を回し，
   本物の変化量が偽の変化量を上回るか確かめる'''),
 ('code', r'''W2V = OUT/'w2v_slice4'
# --runs は**シードを変えて何回学習するか**。既定は 10（config/pipeline.yaml）。
# 授業では時間の都合で 3 回にしてある。**報告には回数を必ず書くこと**
# （10 回で測り直すなら --runs を外すか 10 を渡す）。
# スライス別モデルを runs 回ぶん学習するので，時間はおよそ runs 倍かかる。
RUNS = 3
run_script('08_word2vec_diachronic.py', '--chunks', DS/'chunks',
           '--index', DS/'chunks_index.csv', '--out', W2V, '--slice', 'slice4',
           '--dim', 300, '--window', 3, '--min-count', 20, '--balance',
           '--runs', RUNS)
print(f'※ {RUNS} 回の平均で測った。semantic_change.csv の drift_sd が'
      'ばらつきである。**ばらつきの大きい語で意味変化を論じてはいけない。**')'''),
 ('code', r'''p = W2V/'semantic_change.csv'
if need(p, '08_word2vec_diachronic.py を先に走らせること'):
    sc = pd.read_csv(p)
    # **平均だけを見せない。** 試行間のばらつき（drift_sd）と変動係数
    # （drift_cv = sd/平均）を必ず並べる。変化量が大きくても
    # ばらつきが大きい語は「乱数で動いただけ」かもしれない。
    cols = {'term': '語', 'drift_first_last': '変化量（平均）',
            'drift_sd': 'ばらつき（SD）', 'drift_cv': '変動係数',
            'drift_min': '最小', 'drift_max': '最大',
            'max_step_at': '最大変化の時代', 'runs': '試行'}
    have = [c for c in cols if c in sc.columns]
    t = sc[have].head(25).rename(columns=cols)
    if '変動係数' in t:
        # ばらつきの大きい語に印を付ける（色だけに頼らず文字で示す）
        t['判定'] = np.where(pd.to_numeric(t['変動係数'],
                                          errors='coerce') > 0.25,
                             '⚠ 解釈に耐えない', '')
    show(t, caption=f'意味変化量の大きい語（上位25・'
                    f'{int(sc.runs.iloc[0]) if "runs" in sc else 1} 回の平均）',
         fmt={'変化量（平均）': '{:.4f}', 'ばらつき（SD）': '{:.4f}',
              '変動係数': '{:.3f}', '最小': '{:.4f}', '最大': '{:.4f}',
              '試行': '{:.0f}'})
    if 'drift_cv' in sc.columns:
        cv = pd.to_numeric(sc.drift_cv, errors='coerce')
        n_bad = int((cv.head(100) > 0.25).sum())
        print(f'上位100語のうち，試行間のばらつきが大きい語は {n_bad} 語。'
              '**この語で意味変化を論じてはいけない。**')

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6))
    axes[0].hist(sc.drift_first_last, bins=50, color=PALETTE[0], alpha=.85)
    axes[0].set_xlabel('明治→昭和戦後 のコサイン距離（平均）')
    axes[0].set_ylabel('語数'); axes[0].set_title('意味変化量の分布')
    if 'drift_sd' in sc.columns and sc.drift_sd.max() > 0:
        # **変化量とばらつきを同じ図で見る。** 右下（大きく・安定）の語が
        # 解釈に値する。対角線より上の語は，変化量よりも揺れのほうが大きい。
        axes[1].scatter(sc.drift_first_last, sc.drift_sd, s=8,
                        color=PALETTE[0], alpha=.45, rasterized=True)
        lim = float(max(sc.drift_first_last.max(), sc.drift_sd.max()))
        axes[1].plot([0, lim], [0, lim * 0.25], color=PALETTE[5], lw=1,
                     ls='--', label='変動係数 0.25')
        axes[1].set_xlabel('変化量（平均）'); axes[1].set_ylabel('ばらつき（SD）')
        axes[1].set_title('破線より上は「乱数で動いた」語')
        axes[1].legend(frameon=False, fontsize=9)
    else:
        axes[1].text(.5, .5, '1回しか学習していないので\nばらつきは測れない',
                     ha='center', va='center', fontsize=11)
        axes[1].set_axis_off()
    for a_ in axes:
        a_.spines[['top','right']].set_visible(False)
    fig.tight_layout(); save_fig(fig, 'Step6_drift_hist'); plt.show()'''),
 ('md', r'''## 3. コントロール実験 — 「偽の時代」と比べる

時代ラベルをシャッフルして同じ手順を回す。**本物の変化量が
偽の変化量とほとんど変わらなければ，観測された変化は時代効果ではない。**

これは本授業で最も重要な手続きである。'''),
 ('code', r'''# 時代ラベルをシャッフルした対照条件
#
# **ラベルの付いた行だけを入れ替える。** 索引ぜんぶを permutation にかけると，
# 時代不明（slice4 が空）の行のぶんだけ空ラベルが混ざり込み，
# 本物の条件と対照条件でチャンク数が変わってしまう。比較にならない。
ci_shuf = sl.copy()
rs = np.random.default_rng(20260920)
ci_shuf['slice4'] = rs.permutation(ci_shuf['slice4'].values)
shuf_path = DS/'chunks_index_shuffled.csv'
ci_shuf.to_csv(shuf_path, index=False, encoding='utf-8-sig')
print(f'対照条件のチャンク {len(ci_shuf):,} 件（本物の条件と同数であること）')

W2V_S = OUT/'w2v_shuffled'
run_script('08_word2vec_diachronic.py', '--chunks', DS/'chunks',
           '--index', shuf_path, '--out', W2V_S, '--slice', 'slice4',
           '--dim', 300, '--window', 3, '--min-count', 20, '--balance',
           '--runs', RUNS, tail=1500)
# **回数も本物の条件と同じにする。** 片方だけ10回にすると，
# 平均の安定度が違うぶんだけ分布の幅が変わり，比較にならない。'''),
 ('code', r'''a = W2V/'semantic_change.csv'; b = W2V_S/'semantic_change.csv'
if a.exists() and b.exists():
    real = pd.read_csv(a); fake = pd.read_csv(b)
    fig, ax = plt.subplots(figsize=(8,5))
    ax.hist(real.drift_first_last, bins=50, alpha=.7,
            color=PALETTE[0], label='本物の時代ラベル', density=True)
    ax.hist(fake.drift_first_last, bins=50, alpha=.7,
            color=PALETTE[5], label='シャッフルした時代ラベル', density=True)
    ax.set_xlabel('コサイン距離'); ax.set_ylabel('密度')
    ax.set_title('意味変化量：本物 vs 対照条件')
    ax.legend(frameon=False); ax.spines[['top','right']].set_visible(False)
    fig.tight_layout(); save_fig(fig, 'Step6_control'); plt.show()
    q = lambda s: {'語数': len(s), '中央値': s.median(),
                   '第1四分位': s.quantile(.25), '第3四分位': s.quantile(.75),
                   '最大': s.max()}
    show(pd.DataFrame([{'条件': '本物の時代ラベル', **q(real.drift_first_last)},
                       {'条件': 'シャッフルした対照', **q(fake.drift_first_last)}]),
         caption='意味変化量（最初のスライスと最後のスライスのコサイン距離）',
         fmt={'中央値':'{:.4f}','第1四分位':'{:.4f}',
              '第3四分位':'{:.4f}','最大':'{:.4f}'})
    d = real.drift_first_last.median() - fake.drift_first_last.median()
    print(f'中央値の差 {d:+.4f}。**2つの分布が重なるほど，'
          f'「時代差」の主張は弱くなる。**')'''),
 ('md', r'''## 4. 近傍語の変遷を読む

数値の大小より，**近傍語がどう入れ替わったか**のほうが解釈に堪える。'''),
 ('code', r'''p = W2V/'probe_neighbours.csv'
if need(p, '08_word2vec_diachronic.py を先に走らせること'):
    nb = pd.read_csv(p)
    # 語ごとにブロックを作り，語名は先頭行だけに出す。
    # print で流すと，同じスライスの行が縦に揃わず比べにくい。
    rows = []
    for term in nb.term.unique()[:8]:
        g = nb[nb.term==term]
        for i,(_,r) in enumerate(g.iterrows()):
            rows.append({'語': term if i==0 else '',
                         'スライス': r['slice'], '近傍語': r.neighbours})
    show(pd.DataFrame(rows), caption='時代スライスごとの近傍語')
    print('**縦に読むのではなく，同じ語の行を横に見比べること。**'
          '入れ替わった語が意味変化の候補である。')'''),
 ('md', r'''### 演習 — 自分の5語を追う

Step 5 で選んだ5語について
1. 変化量（drift）はどの程度か
2. 近傍語はどう入れ替わったか
3. 対照条件と比べて，その変化は意味があるか
4. **原文で確かめる。** KWIC で実際の用例を読む。数値だけで論じない。'''),
 ('code', r'''# 簡易 KWIC。数値で見つけた変化を，必ず原文で確認する。
PLAIN = ROOT/'data'/'plain'/'full'
meta = load_meta()
# キーは work_rows() に作らせる。自前で f'{person_id}_{work_id}' と書くと
# **0 埋めの違いで1件も一致せず**，period_prefix での絞り込みが黙って
# 効かなくなる（全作品が対象になる）。ラベルも出なくなる。
ROWS = work_rows(meta)
LAB  = work_labels(with_year=True)

miss = [f.stem for f in sorted(PLAIN.glob('*.txt')) if f.stem not in ROWS]
if miss:
    print(f'[warn] メタデータに無い本文 {len(miss)} 件: ' + '，'.join(miss[:5]))

def kwic(word, width=28, limit=12, period_prefix=None):
    """簡易 KWIC。**表にして出す。**

    左文脈を右寄せにすると，キーワードが縦に揃って並ぶ。
    print で流すと，全角の幅のせいで揃わない。
    """
    rows = []
    for f in sorted(PLAIN.glob('*.txt')):
        r = ROWS.get(f.stem)
        if period_prefix:
            if r is None:                      # 年が分からないものは外す
                continue
            if not str(r.get('year_first')).startswith(period_prefix):
                continue
        t = f.read_text(encoding='utf-8').replace('\n','　')
        lab = LAB.get(f.stem, f.stem)
        for i in range(len(t)):
            if t.startswith(word, i):
                rows.append({'作品': lab,
                             '左文脈': '…' + t[max(0,i-width):i],
                             '語': word,
                             '右文脈': t[i+len(word):i+len(word)+width] + '…'})
                if len(rows) >= limit:
                    break
        if len(rows) >= limit:
            break
    if not rows:
        print(f'「{word}」は見つからなかった'
              + (f'（{period_prefix} 台に限定）' if period_prefix else ''))
        return
    cap = f'KWIC：「{word}」' + (f'（{period_prefix} 台）' if period_prefix else '')
    show(pd.DataFrame(rows), caption=f'{cap}　{len(rows)} 例',
         align={'左文脈': 'right', '語': 'center', '右文脈': 'left'})

kwic('自由')'''),
 ('md', r'''## 5. このステップの課題

次の設問への答えを，テンプレート `my_work/results/Step6_report.md` に書いて提出する（**全体で600–1000字程度**。図表と「再現のための情報」は字数に含めない）。

- **提出先**：Zulip（{ZULIP_ORG}）の非公開チャネル **{ZULIP_CHANNEL}** ＞ トピック **Step 6**
- テンプレートの中身をメッセージに貼り付け，図（SVG）・表（CSV）は**同じメッセージに添付**する（1人1通）
- 図は番号で言及し（図1），**図を見なくても論旨が追えるように**書く（SVG は Zulip で表示されないことがある）
- 再提出は元の投稿を直さず，同じトピックに新しく投稿する（手順書 §5.3）

1. 4スライスと2スライスの両方で実行し，結果の頑健性を比較すること。
2. **対照条件（シャッフル）の図を必ず添付**し，そこから言えることを述べること。
3. 自分の5語について，drift・近傍語・KWIC の3点セットで報告すること。
4. 「この語は意味が変化した」と言えるための条件を，自分の言葉で3つ挙げること。

### このステップの到達点（次へ進む条件）

- 時代スライスごとのモデルを学習し，Procrustes でアラインメントできた
- **シャッフル対照**を実行し，本物の変化量と比較した図がある
- 変化が大きいと出た語について，KWIC で原文を確認した
- スライス間の語数の偏りを `--balance` で吸収した
'''),
])

# ==========================================================================
L(7, 'doc2vec — 作品の表現と交絡の分離', [
 ('md', r'''# Step 7 doc2vec — 作品の表現と，作家効果・時代効果の分離

## このステップの到達目標

1. Paragraph Vector（PV-DM / PV-DBOW）の考え方を説明できる
2. 作品の document vectors を作り，メタデータとの関係を定量化できる
3. **作家効果と時代効果を分離する**手続きを実行できる
4. document vectors とメタデータの関係を分類・回帰で評価できる

## 導入：文書をベクトルにする3つの道

| 方法 | 長所 | 短所 |
|---|---|---|
| 語頻度ベクトル（BoW/tf-idf） | 解釈しやすい。安定 | 語順・文脈を捨てる |
| 語ベクトルの平均 | 簡単。頑健 | 文書固有の情報が薄まる |
| **doc2vec (PV)** | 文書固有のベクトルを学習 | 不安定。ハイパラ依存 |

Le & Mikolov (2014) の Paragraph Vector は，文書 ID を追加の「語」として
学習に参加させる。PV-DBOW（`dm=0`）は skip-gram の文書版で，
小規模コーパスではこちらが安定しやすい。

## 本コーパス固有の問題

**チャンク単位で学習し，作品ベクトルはその平均とする。**
作品を1文書として学習すると，長篇ほど学習量が多くなり，
ベクトルの質が作品長と相関してしまう（『夜明け前』は『大塩平八郎』の16.5倍）。

## 参考
- Le & Mikolov (2014) Distributed representations of sentences and documents. *ICML*.
- Lau & Baldwin (2016) An empirical evaluation of doc2vec. *Rep4NLP*.
'''),
 ('code', PREAMBLE),
 ('code', r'''DS = ROOT/'data'/'datasets'
D2V = OUT/'d2v'
run_script('09_doc2vec.py', '--chunks', DS/'chunks',
           '--index', DS/'chunks_index.csv', '--out', D2V,
           '--dim', 300, '--window', 3, '--epochs', 40, '--dm', 0)'''),
 ('md', r'''## 1. 何が捉えられているか — カテゴリ効果の比較

`category_effects.csv` は，**同一カテゴリ内の平均類似度**と
**異カテゴリ間の平均類似度**の差を示す。差が大きい軸ほど，
document vector がその軸を強く捉えている。

**作家（`author_ja`）が最大なら要注意。** その document vector が測っているのは
主として「誰が書いたか」であり，時代の主張には統制が要る。'''),
 ('code', r'''p = D2V/'category_effects.csv'
if need(p, '09_doc2vec.py を先に走らせること'):
    ce = pd.read_csv(p)
    show(ce, caption='カテゴリ効果（同一カテゴリ内 − 異カテゴリ間 の平均類似度）',
         fmt={c: '{:.4f}' for c in ce.columns if ce[c].dtype.kind == 'f'})
    fig, ax = plt.subplots(figsize=(8,4.5))
    ax.barh(ce.field[::-1], ce.gap[::-1], color=PALETTE[0])
    ax.set_xlabel('同一カテゴリ内 − 異カテゴリ間 の平均類似度')
    ax.set_title('doc2vec 空間は何を捉えているか')
    ax.spines[['top','right']].set_visible(False); ax.grid(axis='x', alpha=.25)
    fig.tight_layout(); save_fig(fig, 'Step7_category_effects'); plt.show()'''),
 ('md', r'''## 2. 作家効果を「取り除く」— 3つの方法

### (a) 作家ごとの平均を引く（centering）
各作家の作品ベクトルから，その作家の重心を引く。残差が
「その作家にとって，この作品がどう外れているか」を表す。

### (b) 作家を層化して抽出する
各作家から同数の作品だけを使う。情報は減るが交絡は減る。

### (c) 混合効果モデルで作家をランダム効果に入れる
統計的に最も正統。`statsmodels` の `MixedLM` を使う。

ここでは (a) を実装し，作家効果を除いたあとに時代効果が残るかを見る。'''),
 ('code', r'''p = D2V/'work_vectors.csv'
if need(p, '09_doc2vec.py を先に走らせること'):
    wv = pd.read_csv(p)
    # work_vectors.csv には 09 が書いた列しか無い。図で使う軸は
    # ここで宣言し，足りないぶんはメタデータから引いて補う。
    # こうしておけば 09 の出力列が変わっても図は落ちない。
    wv = attach_meta(wv, ['author_ja', 'title', 'year_first', 'period',
                          'genre_main', 'genre_sub', 'narration',
                          'register_level'])
    dcols = [c for c in wv.columns if c.startswith('d')and c[1:].isdigit()]
    X = wv[dcols].values
    Xn = X/ (np.linalg.norm(X,axis=1,keepdims=True)+1e-12)

    # (a) 作家ごとに中心化
    Xc = X.copy()
    for a, g in wv.groupby('author_ja'):
        Xc[g.index] -= X[g.index].mean(0)
    Xcn = Xc/(np.linalg.norm(Xc,axis=1,keepdims=True)+1e-12)

    def gap(M, field):
        S = M@M.T; lab = wv[field].values
        win=[];bet=[]
        for i in range(len(wv)):
            for j in range(i+1,len(wv)):
                (win if lab[i]==lab[j] else bet).append(S[i,j])
        return (np.mean(win)-np.mean(bet)) if win and bet else np.nan

    FIELD_JA = {'author_ja':'作家', 'period':'時代', 'genre_sub':'ジャンル',
                'narration':'語り', 'register_level':'文体の階層'}
    rows = []
    for f, ja in FIELD_JA.items():
        g0, g1 = gap(Xn, f), gap(Xcn, f)
        rows.append({'観点': ja, '列名': f, '元の空間': g0,
                     '作家中心化後': g1, '減り': g0-g1,
                     '残存率': (g1/g0 if g0 else np.nan)})
    show(pd.DataFrame(rows),
         caption='同じ属性どうしの類似度が，違う属性どうしより'
                 'どれだけ高いか（差。大きいほどその属性を測っている）',
         fmt={'元の空間':'{:.4f}','作家中心化後':'{:.4f}',
              '減り':'{:+.4f}','残存率':'{:.0%}'})
    print('作家中心化で「作家」の差はほぼ 0 になる（定義上）。')
    print('**そのとき「時代」の残存率が高ければ，時代効果は作家効果と'
          '独立に存在する。** 0 近くまで落ちるなら，見ていたのは作家である。')'''),
 ('md', r'''## 3. 分類による評価 — 交差検証

「document vector は時代を予測できるか」を，**作家を跨いだ交差検証**で測る。

重要なのは **GroupKFold で作家をグループにする**ことである。
同じ作家の作品が訓練とテストに分かれていると，
モデルは時代ではなく作家を覚えて高い精度を出してしまう。'''),
 ('code', r'''from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold, cross_val_score, StratifiedKFold
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline

if need(p, '09_doc2vec.py を先に走らせること'):
    y = wv.period.values
    groups = wv.author_ja.values
    pipe = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, C=1.0))

    ok = pd.Series(y).value_counts()
    keep = pd.Series(y).isin(ok[ok>=3].index).values
    Xk, yk, gk = Xn[keep], y[keep], groups[keep]

    s1 = cross_val_score(pipe, Xk, yk, cv=StratifiedKFold(5, shuffle=True,
                                                          random_state=0))
    s2 = cross_val_score(pipe, Xk, yk, groups=gk,
                         cv=GroupKFold(n_splits=min(5, len(set(gk)))))
    base = ok.max()/ok.sum()
    show(pd.DataFrame([
            {'条件':'多数派ベースライン', '正解率':base, 'ばらつき':np.nan,
             '分割数':np.nan, '備考':'最も多い時代を常に答える'},
            {'条件':'通常の5分割交差検証', '正解率':s1.mean(), 'ばらつき':s1.std(),
             '分割数':len(s1), '備考':'同じ作家が訓練と検証の両方に入る'},
            {'条件':'作家グループ交差検証', '正解率':s2.mean(), 'ばらつき':s2.std(),
             '分割数':len(s2), '備考':'**こちらが正しい**'}]),
         caption=f'時代の予測精度（{keep.sum()} 点・3 件以上ある時代のみ）',
         fmt={'正解率':'{:.1%}', 'ばらつき':'{:.1%}', '分割数':'{:.0f}'})
    print(f'差 {s1.mean()-s2.mean():+.1%}。**この差が大きいほど，'
          '「時代を当てている」のではなく「作家を当てている」度合いが高い。**')'''),
 ('md', r'''## 4. 作品空間を見る'''),
 ('md', r'''### 色の選び方

**順序のあるものを，カテゴリ用の8色で色分けしてはいけない。**
明治中期が青で明治後期が黄なら，隣り合う時代が隣り合う色にならず，
「時代が下るにつれて布置がどちらへ動くか」という肝心のことが読めない。

| データの性質 | 例 | 使う色 |
|---|---|---|
| 順序がある | 初出年・時代・段階 | **1色相の濃淡**。近い値が近い色になる |
| 順序が無い | ジャンル・作家 | 色相で分ける。ただし**散布図では色数に上限**がある |
| 正負がある | 増減・差分 | 2色相＋中央は灰 |

散布図には固有の制約がある。棒グラフや折れ線なら隣り合う系列だけが
接するので，隣どうしが見分けられれば足りる。**散布図はどの2点も
隣り合いうる**ので，全ペアが見分けられなければならない。この条件は厳しく，
色覚の多様性を考慮した既定のパレットでも**3色が上限**である。

そこで下の図では

- 左：**初出年を連続量として**1色相の濃淡に割り当てる（濃いほど新しい）。
  目盛を時代の境目に打って，連続の色と既存の区分を橋渡しする
- 右：色相は `genre_main` の**2つだけ**にし，下位ジャンルは**マーカーの形**で
  区別する。形は色とは別の通路なので，色数の上限に縛られない

作者名は外れた作品にだけ打つ。全点に打つと文字が面を覆い，
色の勾配が見えなくなる。'''),
 ('code', r'''if need(p):
    Xz = Xn - Xn.mean(0)
    U,S,Vt = np.linalg.svd(Xz, full_matrices=False)
    P = U[:,:2]*S[:2]
    fig, axes = plt.subplots(1,2, figsize=(14.5,6))

    # ------------------------------------------------------------------
    # 左：時代。**順序のあるものを categorical の8色で色分けしてはいけない。**
    # 明治中期が青，明治後期が黄，大正が赤…では，隣り合う時代が隣り合う色に
    # ならず，「時代が下るにつれて布置がどちらへ動くか」が読めない。
    # 初出年そのものを連続量として1色相の濃淡に割り当てる。
    # 近い年は近い色になり，勾配がそのまま通時変化として見える。
    # ------------------------------------------------------------------
    yr = pd.to_numeric(wv.year_first, errors='coerce')
    ok = yr.notna().values
    ax = axes[0]
    if (~ok).any():
        ax.scatter(P[~ok,0], P[~ok,1], s=40, color='#c3c2b7', alpha=.7,
                   edgecolor='white', linewidth=.6, label='初出年不明', zorder=2)
        ax.legend(frameon=False, fontsize=7, loc='lower right')
    # vmin/vmax は**データの範囲に合わせる**。既定のままだと，データより
    # 外側に打った目盛のぶんだけ色帯に白い余白ができ，「そこに色が無い」
    # ように見える。
    vmin, vmax = float(np.nanmin(yr)), float(np.nanmax(yr))
    sc = ax.scatter(P[ok,0], P[ok,1], c=yr[ok], cmap=SEQ_BLUE, s=48, alpha=.9,
                    vmin=vmin, vmax=vmax,
                    edgecolor='white', linewidth=.6, zorder=3)
    cb = fig.colorbar(sc, ax=ax, pad=.02)
    cb.set_label('初出年', fontsize=8)
    # 時代の境目に目盛を打ち，連続の色と既存の区分を橋渡しする。
    # 範囲の外に出る境目は落とし，両端は実際の最小・最大を示す。
    ticks = [t for t in (1887, 1900, 1912, 1926, 1945) if vmin + 3 <= t <= vmax - 3]
    cb.set_ticks([vmin] + ticks + [vmax])
    cb.set_ticklabels([f'{vmin:.0f}'] + [str(t) for t in ticks] + [f'{vmax:.0f}'])
    cb.ax.tick_params(labelsize=7)
    ax.set_title('doc2vec 作品空間（初出年で着色・濃いほど新しい）')

    # ------------------------------------------------------------------
    # 右：ジャンル。こちらは順序が無いので色相で分ける。ただし
    # **散布図で8色は使えない**（どの2点も隣り合いうるので，全ペアが
    # 見分けられる必要があり，既定パレットでも3色が上限）。
    # そこで色相は genre_main の2つだけにして，下位ジャンルは**マーカーの形**で
    # 区別する。同じ大分類の作品が同系色に集まるので，
    # 「Fiction と Nonfiction が分かれているか」が一目で分かる。
    # ------------------------------------------------------------------
    ax = axes[1]
    MARKS = ['o','s','^','D','v','P','X','*']
    # 空欄・欠損を1つの値にまとめてから使う。NaN は == でも isin でも
    # 偽になるので，まとめておかないと**どの系列にも入らない点**が出る。
    gmain = (wv.genre_main.fillna('').astype(str).str.strip()
             .replace('', '（不明）'))
    gsub  = (wv.genre_sub.fillna('').astype(str).str.strip()
             .replace('', '（不明）'))
    subs = list(gsub.value_counts().index[:7])
    shape = {k: MARKS[i % len(MARKS)] for i, k in enumerate(subs)}
    # 色相は既定の2つを先に，それ以外（不明など）は灰色で後ろに置く
    mains = [g for g in ('Fiction', 'Nonfiction') if (gmain == g).any()]
    mains += [g for g in gmain.unique() if g not in mains]
    for gm in mains:
        colour = GENRE_C.get(gm, '#8f8e85')
        for k in subs + ['（その他）']:
            other = (k == '（その他）')
            if other:
                m = ((gmain == gm) & ~gsub.isin(subs)).values
                mk = 'o'
            else:
                m = ((gmain == gm) & (gsub == k)).values
                mk = shape[k]
            if not m.any():
                continue
            # 上位7つに入らない下位ジャンルは**白抜きの丸**にする。
            # 小さな点にすると，ただ目立たないだけで区別がつかない。
            ax.scatter(P[m,0], P[m,1], s=52, alpha=.9, marker=mk,
                       facecolor=('none' if other else colour),
                       edgecolor=(colour if other else 'white'),
                       linewidth=(1.1 if other else .6),
                       label=f'{gm}／{str(k)[:12]}', zorder=3)
    ax.set_title('doc2vec 作品空間（色＝大分類／形＝下位ジャンル）')
    ax.legend(frameon=False, fontsize=6.5, ncol=2, loc='best')

    for ax in axes:
        ax.spines[['top','right']].set_visible(False)
        ax.grid(alpha=.18, linewidth=.6, zorder=0)
    fig.tight_layout()
    # 作者名は**外れた作品だけ**に打つ。全点に打つと文字が面を覆い，
    # 色の勾配という肝心のものが見えなくなる。重心から遠い順に12点。
    d_c = np.hypot(P[:,0] - P[:,0].mean(), P[:,1] - P[:,1].mean())
    pick = np.argsort(-d_c)[:12]
    names = wv.author_ja.fillna('').astype(str).str.strip()
    names = names.where(names != '', wv.work_stem.astype(str))
    for ax in axes:
        label_points(ax, P[pick,0], P[pick,1],
                     names.values[pick], fontsize=6.5)

    # ---- 対話版：どちらの面を指しても同じ作品の情報が出る -------------
    # **作品名が出ないと，この図は色の模様にすぎない。**
    # 併せて doc2vec 空間での近傍5作品へ線を引く。線の多くが同じ作家に
    # 向かうなら，この空間が測っているのは主として「誰が書いたか」である
    # （Step 7 の主題そのもの）。
    Sw = Xn @ Xn.T
    np.fill_diagonal(Sw, -np.inf)
    nn5 = np.argsort(-Sw, axis=1)[:, :5]
    lab7 = [f'{a}『{t}』' if str(a).strip() else str(st)
            for a, t, st in zip(wv.author_ja.fillna(''), wv.title.fillna(''),
                                wv.work_stem)]
    same = float(np.mean([wv.author_ja.iloc[i] == wv.author_ja.iloc[nn5[i, 0]]
                          for i in range(len(wv))]))
    print(f'最近傍が同じ作家である割合: {same:.1%}'
          '（高いほど，この空間は作家を測っている）')
    tips7 = []
    for i in range(len(wv)):
        r = wv.iloc[i]
        tips7.append({
            'term': lab7[i],
            'fields': [('初出', f'{pd.to_numeric(r.year_first, errors="coerce"):.0f}'
                                if pd.notna(pd.to_numeric(r.year_first, errors='coerce'))
                                else '不明'),
                       ('時代', str(r.period)),
                       ('ジャンル', f'{r.genre_main}／{r.genre_sub}'),
                       ('語り', str(r.narration)),
                       ('文体の階層', str(r.register_level)),
                       ('最近傍の作家', str(wv.author_ja.iloc[nn5[i, 0]])),
                       ('同じ作家か', '●' if wv.author_ja.iloc[i] ==
                                      wv.author_ja.iloc[nn5[i, 0]] else '—')],
            'notes': ('doc2vec 空間の近傍:', '／'.join(lab7[j] for j in nn5[i])),
            'links': [int(j) for j in nn5[i]],
        })
    save_interactive(fig, list(axes), 'Step7_work_space', P[:, 0], P[:, 1], tips7,
                     source=D2V/'work_vectors.csv', id_col='作品',
                     title='doc2vec 作品空間（1点＝1作品）',
                     note=(f'左＝初出年で着色／右＝ジャンル。{len(wv)} 作品。'
                           f'最近傍が同じ作家である割合 {same:.1%}。'
                           '軸は主成分で，向きに実質的な意味は無い。'),
                     hint=('点にカーソルを近づけると作品が出て，**doc2vec 空間の'
                           '近傍5作品へ線が伸びる**。線が同じ作家に集まるなら，'
                           'この空間は作家を測っている。左右どちらの面でも同じ。'),
                     table_cols=['初出', '時代', 'ジャンル', '語り',
                                 '文体の階層', '最近傍の作家', '同じ作家か'])
    plt.show()'''),
 ('md', r'''## 5. このステップの課題

次の設問への答えを，テンプレート `my_work/results/Step7_report.md` に書いて提出する（**全体で600–1000字程度**。図表と「再現のための情報」は字数に含めない）。

- **提出先**：Zulip（{ZULIP_ORG}）の非公開チャネル **{ZULIP_CHANNEL}** ＞ トピック **Step 7**
- テンプレートの中身をメッセージに貼り付け，図（SVG）・表（CSV）は**同じメッセージに添付**する（1人1通）
- 図は番号で言及し（図1），**図を見なくても論旨が追えるように**書く（SVG は Zulip で表示されないことがある）
- 再提出は元の投稿を直さず，同じトピックに新しく投稿する（手順書 §5.3）

1. `dm=0`（PV-DBOW）と `dm=1`（PV-DM）で学習し，カテゴリ効果を比較すること。
2. 作家中心化の前後で `period` の効果がどう変わるか報告すること。
3. **通常の交差検証と作家グループ交差検証の差**を報告し，
   その差が何を意味するかを説明すること。
4. Step 4 の Delta 空間と Step 7 の doc2vec 空間で，
   **近傍が一致する作品／一致しない作品**を探し，理由を考察すること。

### このステップの到達点（次へ進む条件）

- チャンク単位で doc2vec を学習し，作品ベクトルを得た
- `category_effects.csv` を読み，どのカテゴリが最も効いているか言える
- **作家グループ交差検証**を実行し，通常の交差検証との差を説明できる
- 作家中心化の前後で時代効果がどう変わるかを示した
'''),
])

# ==========================================================================
L(8, 'トピックモデル(MALLET) — 主題の多様化と総合', [
 ('md', r'''# Step 8 トピックモデル（MALLET） — 主題の多様化と総合

## このステップの到達目標

1. LDA の生成過程を説明し，ハイパーパラメータの役割を言える
2. MALLET を実行し，トピック数を診断指標で選べる
3. トピック分布をメタデータと結合し，通時的な主題変化を記述できる
4. 4 種の分析（頻度・word2vec・doc2vec・LDA）を統合して議論できる

## 導入：LDA の生成過程

LDA は文書がこう作られたと仮定する。

1. 各トピック $k$ に語の分布 $\phi_k \sim \mathrm{Dir}(\beta)$ がある
2. 各文書 $d$ にトピックの分布 $\theta_d \sim \mathrm{Dir}(\alpha)$ がある
3. 文書の各語について，まず $\theta_d$ からトピック $z$ を選び，
   次に $\phi_z$ から語を選ぶ

観測できるのは語だけなので，$\theta$ と $\phi$ を**逆推定**する。
MALLET はこれを Gibbs サンプリングで行う。

### ハイパーパラメータ

| | 意味 | 効果 |
|---|---|---|
| `--num-topics` | トピック数 K | 大きいほど細かい。過大だと解釈不能 |
| `--optimize-interval` | α の最適化 | **必ず有効にする**。トピックの大小を自動調整 |
| `--num-iterations` | 反復回数 | 1000 以上。2000 が安全 |

## 本コーパス固有の注意

Step 3 でチャンク分割した理由がここで効く。作品を1文書とすると，
50万語の『夜明け前』が複数トピックを占有する。**2,000語チャンクで揃える。**

## 参考
- Blei, Ng & Jordan (2003) Latent Dirichlet Allocation. *JMLR* 3.
- Mimno et al. (2011) Optimizing semantic coherence in topic models. *EMNLP*.
- Underwood, T. (2019) *Distant Horizons*. Chicago UP.
'''),
 ('code', PREAMBLE),
 ('md', r'''## 1. ストップリストの設計

トピックモデルの成否の半分はストップリストで決まる。

`config/stopwords_ja.txt` は次を除く。

- 形式名詞（事・物・者・訳・筈）
- 指示語・代名詞
- 汎用動詞（為る・有る・居る・成る）
- 汎用形容詞・副詞
- **書誌由来の語**（底本・入力・校正・青空・文庫…）← v1 の混入対策

**ただし研究の問いによる。** 語りを分析するときは人称代名詞を除いてはいけない。
`config/stopwords_ja.txt` のブロックをコメントアウトして使い分ける。'''),
 ('code', r'''sw = (ROOT/'config'/'stopwords_ja.txt').read_text(encoding='utf-8')
words = [l for l in sw.splitlines() if l and not l.startswith('#')]
print(f'ストップリスト {len(words)} 語')
print(' '.join(words[:60]), '...')

# 自動候補（文書頻度による）も確認する
DS = ROOT/'data'/'datasets'
p = DS/'stopwords_auto.txt'
if need(p, '先に 06_build_datasets.py のセルを実行すること'):
    auto = p.read_text(encoding='utf-8').split()
    print(f'\n自動候補 {len(auto)} 語（df が低すぎ/高すぎ）')
    vs = pd.read_csv(DS/'vocab_stats.csv')
    print('df 上位20:', ' '.join(vs.term.head(20).astype(str)))'''),
 ('md', r'''## 2. トピック数を選ぶ

`K` は先験的には決まらない。診断指標で当たりをつける。

- **coherence**（Mimno et al. 2011）: トピック上位語が実際に共起するか。
  負の値で，0 に近いほど良い。
- **exclusivity**: トピック固有の語をどれだけ持つか。高いほど良い。

両者はトレードオフになる。K を増やすと exclusivity は上がるが
coherence は悪化する。**肘（elbow）と解釈可能性の両方で決める。**'''),
 ('code', r'''MALLET = os.environ.get('MALLET') or shutil.which('mallet')
print('MALLET =', MALLET)
if not MALLET:
    print('環境変数 MALLET を設定してください（docs/00_setup_students.md §1.6 / §2.5）')

RUN_SWEEP = False   # 時間がかかる（K ごとに数分）。実行するとき True に
if MALLET and RUN_SWEEP:
    run_script('10_mallet.py', 'sweep', '--datasets', DS,
               '--out', OUT/'mallet_sweep', '--iterations', 1000,
               '--stoplist', ROOT/'config'/'stopwords_ja.txt',
               '--topic-list', 20, 30, 40, 50, 60, 80)
elif MALLET:
    print('[skip] RUN_SWEEP = False なので，トピック数の比較（sweep）は実行していない。')
    print('       見たいときは RUN_SWEEP = True にしてこのセルを実行する'
          '（6 通りの K を学習するので 20–40 分かかる）。')
    print('       飛ばしても，下の「3. 本番の学習」（K=50）は実行できる。')'''),
 ('code', r'''p = OUT/'mallet_sweep'/'sweep.csv'
if not p.exists():
    # 上のセルが RUN_SWEEP = False（既定）のときは sweep.csv が無いのが正常
    print(f'[skip] {p.name} が無い。上のセルで RUN_SWEEP = True にして sweep を実行したときだけ図が出る。')
    print('       飛ばして先へ進んでよい。')
else:
    sw_ = pd.read_csv(p)
    fig, ax1 = plt.subplots(figsize=(8,5))
    ax1.plot(sw_.topics, sw_.mean_coherence, 'o-', color=PALETTE[0], label='coherence')
    ax1.set_xlabel('トピック数 K'); ax1.set_ylabel('平均 coherence', color=PALETTE[0])
    ax2 = ax1.twinx()
    ax2.plot(sw_.topics, sw_.mean_exclusivity, 's--', color=PALETTE[1],
             label='exclusivity')
    ax2.set_ylabel('平均 exclusivity', color=PALETTE[1])
    ax1.set_title('トピック数の選択')
    fig.tight_layout(); save_fig(fig, 'Step8_sweep'); plt.show()'''),
 ('md', r'''## 3. 本番の学習'''),
 ('code', r'''ML = OUT/'mallet'
if MALLET:
    run_script('10_mallet.py', 'all', '--datasets', DS, '--out', ML,
               '--topics', 50, '--iterations', 2000,
               '--stoplist', ROOT/'config'/'stopwords_ja.txt', tail=5000)'''),
 ('code', r'''p = ML/'topics_summary.csv'
if need(p, '10_mallet.py を先に走らせること'):
    ts = pd.read_csv(p)
    pd.set_option('display.max_colwidth', 110)
    show(ts.nlargest(20,'mean_prob')[['topic','mean_prob','top_words']]
           .rename(columns={'topic':'トピック','mean_prob':'平均確率',
                            'top_words':'高頻度語'}),
         caption='平均確率の高いトピック（上位20）', fmt={'平均確率':'{:.4f}'})'''),
 ('md', r'''### トピックを読むときの規律

1. **上位語を見るだけで名づけない。** そのトピックが高い文書を実際に読む。
2. **固有名詞の束はトピックではない。** 特定作家・特定作品の指紋である。
   `exclusivity` が異常に高いトピックはたいていこれ。
3. **「ゴミトピック」を認める。** どのモデルにも解釈不能なトピックが1–2割出る。
   無理に解釈しない。'''),
 ('code', r'''# 各トピックの代表チャンクを読む
p = ML/'doc-topics.txt'
if need(p, '10_mallet.py を先に走らせること'):
    spec = importlib.util.spec_from_file_location('ml', ROOT/'scripts'/'10_mallet.py')
    mlmod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mlmod)
    ids, M = mlmod.read_doc_topics(str(p))
    keys = mlmod.read_topic_keys(str(ML/'topic-keys.txt'))
    ci = pd.read_csv(DS/'chunks_index.csv').set_index('chunk_id')

    TOPIC = 0      # ← 読みたいトピック番号に変える
    order = np.argsort(-M[:,TOPIC])[:5]
    # **何番目のチャンクかも出す。** 長篇は40チャンクに間引いてあるので，
    # 「『こころ』が代表」だけでは原文に戻れない。
    rows = []
    for k in order:
        cid = ids[k]
        r = ci.loc[cid] if cid in ci.index else None
        rows.append({'p': float(M[k,TOPIC]),
                     '作家': (r.author_ja if r is not None else ''),
                     '作品': (r.title if r is not None else cid),
                     '初出': (r.year_first if r is not None else ''),
                     'チャンク': (f'第{r.chunk_no}' if r is not None else ''),
                     'chunk_id': cid})
    show(pd.DataFrame(rows),
         caption=f'T{TOPIC:02d} の代表チャンク上位5 ／ 高頻度語: '
                 + keys.get(TOPIC,(0,''))[1][:80],
         fmt={'p':'{:.3f}'})

    # 本文は表に入れず，読むための形で出す（折り返して読む対象だから）
    print()
    for _, r in pd.DataFrame(rows).iterrows():
        f = DS/'chunks'/(r.chunk_id+'.txt')
        if f.exists():
            print(f'■ p={r.p:.3f} {r.作家}『{r.作品}』{r.チャンク}')
            print('   ', ' '.join(f.read_text(encoding='utf-8').split()[:45]), '…')'''),
 ('md', r'''## 3.5 語を選び直して学習する — 品詞・集中度・散らばり（ディスパーション）

上の本番モデルは 05 の `tokens_content`（名詞・動詞・形容詞・副詞をすべて）で
学習した。そこには二種類の困りものが入る。

| 困りもの | 例 | トピックで起きること |
|---|---|---|
| **固有名詞**（登場人物・地名） | 三吉・岸本・半蔵・法水 | 1作品にしか出ないので，トピックが**作品の目印**になる |
| **意味の薄い高頻度語** | 為る・居る・成る・もう・そう | どのトピックの上位にも出て，区別に役立たない |

`data/tokens/tsv/` には語ごとの品詞（UniDic の pos1–pos3）がある。
`18_pos_select.py` はそれを使って語を選び直す。

- **品詞**：既定（`nva`）は 普通名詞・自立動詞・自立形容詞。固有名詞・数詞・
  代名詞・非自立の動詞（為る・居る）・副詞を除く
- **1作品への集中度**：品詞解析は辞書に無い人名を普通名詞と誤ることがある
  （「純一」「山嵐」「法水」）。度数の 8 割以上が1作品に集中する語を除く
- **1作家への集中度**：同じ作家の複数作品に出る人物名（宮本百合子の「素子」）
  は作品に集中しないので，作家への集中で除く
- **散らばり dp_in**：Gries (2008) の DP を，その語が**いちばん濃い時代の中で**
  測ったもの（Step 4 の `dp_in` と同じ考え方）。少数の作品に固まる bursty な語を除く。
  全体の DP ではなく dp_in を使うのは，**時代への偏りは主題として残したい**からである

⚠ どの閾値も分析の決定である。**報告に必ず書くこと。**'''),
 ('code', r'''# ---- 語を選び直す（閾値は自分で決めてよい。報告に書くこと）----------
TOK = ROOT/'data'/'tokens'
SEL = dict(name='nva_clean', profile='nva',
           max_work_share=0.8,     # 1作品に 80% 超が集中する語を除く
           max_author_share=0.9,   # 1作家に 90% 超が集中する語を除く
           max_dp_in=0.9)          # いちばん濃い時代の中で DP > 0.9 の語を除く
if need(TOK/'tsv', 'Step 3 の 05_tokenise_unidic.py を先に実行すること'):
    run_script('18_pos_select.py', '--tsv', TOK/'tsv', '--meta', META,
               '--profile', SEL['profile'],
               '--max-work-share', SEL['max_work_share'],
               '--max-author-share', SEL['max_author_share'],
               '--max-dp-in', SEL['max_dp_in'],
               '--name', SEL['name'])'''),
 ('code', r'''# ---- 選んだ語でチャンクを作り直し，同じ条件で学習する -----------------
# チャンク長・上限・乱数シードは本番モデル（Step 3）と同じにする。変えると比べられない
TOK_SEL = TOK/f"tokens_{SEL['name']}"
DS_SEL = ROOT/'data'/f"datasets_{SEL['name']}"
ML_SEL = OUT/f"mallet_{SEL['name']}"
if need(TOK_SEL, '上のセルを先に実行すること'):
    run_script('06_build_datasets.py', '--tokens', TOK_SEL, '--meta', META, '--out', DS_SEL,
               '--chunk', 2000, '--max-chunks', 40, '--sample', 'stratified', '--seed', 20260920,
               tail=1500)
    run_script('10_mallet.py', 'all', '--datasets', DS_SEL, '--out', ML_SEL,
               '--topics', 50, '--iterations', 2000,
               '--stoplist', ROOT/'config'/'stopwords_ja.txt', tail=3000)'''),
 ('md', r'''### トピックビューア

2つのモデル（語を選び直したもの・本番）を1枚の HTML に入れる。ブラウザで開き，
左の欄で絞り込む。**サーバは要らない。**

- **品詞・頻度帯・集中度・dp_in** で上位語を絞る
- **relevance λ** を下げると，そのトピックに特有の語が上に来る（0.6 前後が目安）
- トピックを押すと，**時代別の割合・割合の大きい作品と作家**が出る
- 語で探すと，その語を上位に持つトピックだけが濃く残る

⚠ **ビューアで語を隠すことと，その語を除いて学習し直すことは違う。**
隠した語もトピックの形成には効いている。本番モデルで固有名詞を隠しても，
固有名詞が作ったトピックは「その作品のトピック」のままである。
カードの「残存」が低いトピックは，隠した語でできている。'''),
 ('code', r'''# ---- トピックビューアを作る -----------------------------------------
TV = OUT/'topic_viewer.html'
models = []
if (ML_SEL/'doc-topics.txt').exists():
    models += ['--model', f"語を選び直したもの（{SEL['name']}）={ML_SEL}"]
if (ML/'doc-topics.txt').exists():
    models += ['--model', f'本番（内容語すべて）={ML}']
if not models:
    print('[未実行] MALLET の結果が無い。上のセルを先に実行すること')
else:
    run_script('19_topic_viewer.py', *models, '--meta', META,
               '--lexicon', TOK/'lexicon.tsv', '--out', TV)
    print(f'ブラウザで開く: {TV}')
    if sys.platform == 'darwin':
        print('（macOS なら次のセルで開ける）')'''),
 ('code', r'''# macOS：既定のブラウザで開く
import subprocess
if sys.platform == 'darwin' and TV.exists():
    subprocess.run(['open', str(TV)])'''),
 ('md', r'''### 演習 4 — 選び直す前と後

1. 本番モデルで，**1作品・1作家に偏ったトピック**（ビューアの ⚠）をいくつか挙げる。
   選び直したモデルでは，それに当たるトピックはどうなったか
2. 本番モデルのまま，ビューアで「固有名詞」を外し「1作品への集中度」を 60% に
   下げる。カードの「残存」が低く残るトピックはどれか。**隠しても消えない理由**を説明せよ
3. `SEL` の閾値を1つだけ変えて学習し直し，トピックの顔ぶれがどう変わるかを見る。
   どの閾値を採るかを，自分の問いに照らして1段落で正当化せよ
4. 「汽車」「戦争」「工場」を検索し，それを上位に持つトピックの**時代別の割合**を比べる'''),
 ('md', r'''## 4. 主題の通時変化と多様化

問いは2つある。

1. **どの主題が増え，どの主題が減ったか** → トピック × 時代の lift
2. **主題は多様化したか** → 文書ごとのトピック分布の**エントロピー**の推移

(2) は「近代日本文学におけるトピックの多様化」という本研究の中心的な問いに
直接対応する。エントロピーが高いほど，1つの文書が複数の主題にまたがる。'''),
 ('code', r'''if need(p):
    ci2 = pd.read_csv(DS/'chunks_index.csv').set_index('chunk_id')
    ent = -(M*np.log(M+1e-12)).sum(1)
    df = pd.DataFrame({'chunk_id':ids, 'entropy':ent}).set_index('chunk_id')
    df = df.join(ci2[['period','year_first','author_ja','genre_sub','title']], how='inner')

    # **g は下の図でも使うので，列名は英語のまま保つ。**
    # 表示用の名前は別のフレームに付ける（図と表の食い違いを防ぐ）。
    g = df.groupby('period').entropy.agg(['mean','std','count'])
    show(g.reset_index().rename(columns={'period':'時代','mean':'平均',
                                         'std':'標準偏差','count':'チャンク数'}),
         caption='トピック分布のエントロピー（チャンク単位・nats）',
         fmt={'平均':'{:.3f}','標準偏差':'{:.3f}'})

    # 時代の平均だけでは「どういう作品が多様なのか」が見えない。
    # 両端を作品名で出す。エントロピーが低い＝ひとつの主題に凝り固まった
    # チャンク，高い＝主題が散っているチャンクである。
    w = (df.groupby(['author_ja','title','period']).entropy
           .agg(['mean','count']).reset_index()
           .query('count >= 3').sort_values('mean'))
    if len(w):
        COLS_W = {'author_ja':'作家','title':'作品','period':'時代',
                  'mean':'エントロピー平均','count':'チャンク数'}
        w2 = w[list(COLS_W)].rename(columns=COLS_W)
        show(w2.head(6), caption='主題が最も偏っている作品（エントロピー小）',
             fmt={'エントロピー平均':'{:.3f}'})
        show(w2.tail(6).iloc[::-1],
             caption='主題が最も散っている作品（エントロピー大）',
             fmt={'エントロピー平均':'{:.3f}'})
        print('※ チャンク3個未満の作品は除いた。平均が1〜2点で決まってしまう')

    fig, axes = plt.subplots(1,2, figsize=(13,4.6))
    axes[0].bar([i.split('_',1)[1] for i in g.index], g['mean'],
                yerr=g['std']/np.sqrt(g['count']), color=PALETTE[0], capsize=4)
    axes[0].set_ylabel('トピック分布のエントロピー（nats）')
    axes[0].set_title('主題の多様さの推移（チャンク単位）')
    axes[0].tick_params(axis='x', rotation=20)

    d2 = df.dropna(subset=['year_first'])
    axes[1].scatter(d2.year_first, d2.entropy, s=6, alpha=.25, color=PALETTE[0],
                    rasterized=True)   # 点が数千個。ここだけラスタ化して軽くする
    yy = d2.groupby(d2.year_first//5*5).entropy.mean()
    axes[1].plot(yy.index, yy.values, color=PALETTE[5], lw=2, label='5年移動平均')
    axes[1].set_xlabel('初出年'); axes[1].set_ylabel('エントロピー')
    axes[1].legend(frameon=False); axes[1].set_title('初出年 × 主題の多様さ')
    for a in axes: a.spines[['top','right']].set_visible(False)
    fig.tight_layout(); save_fig(fig, 'Step8_entropy'); plt.show()'''),
 ('md', r'''### 解釈上の警告

エントロピーの上昇を「主題の多様化」と読む前に，次を確かめる。

- **作家構成が時代で変わっていないか。** 昭和期の児童書7点は語彙が平易で
  トピックが散りやすい可能性がある。
- **ジャンル構成が変わっていないか。** 探偵小説8点は昭和戦前に集中している。
- **チャンク数が時代で違わないか。** Step 6 と同様，バランスを取って再計算する。

下のセルで `audience` と `genre_main` を統制した再集計を行う。'''),
 ('code', r'''if need(p):
    sub = df.join(ci2[['audience','register_level']], how='left') \
            if 'audience' in ci2.columns else df
    if 'audience' in sub.columns:
        gg = (sub[sub.audience=='general'].groupby('period').entropy
                 .agg(['mean','count']).reset_index()
                 .rename(columns={'period':'時代','mean':'エントロピー平均',
                                  'count':'チャンク数'}))
        show(gg, caption='一般向けのみに限定した場合（児童書を除く）',
             fmt={'エントロピー平均':'{:.3f}'})
        print('児童書を除いても傾向が残るか。残らなければ，')
        print('「多様化」は読者層構成の変化を測っていたことになる。')'''),
 ('code', r'''# トピック × 時代 のヒートマップ
p2 = ML/'topic_by_period.csv'
if need(p2, '10_mallet.py を先に走らせること'):
    tp = pd.read_csv(p2)
    tcols = [c for c in tp.columns if c.startswith('T')]
    Mp = tp[tcols].values
    var = Mp.std(0); sel = np.argsort(-var)[:22]
    sel = sel[np.argsort([np.argmax(Mp[:,t]) for t in sel])]
    ts = pd.read_csv(ML/'topics_summary.csv').set_index('topic')
    labels = [f"T{int(tcols[t][1:]):02d} " +
              ' '.join(str(ts.loc[int(tcols[t][1:]),'top_words']).split()[:5])
              for t in sel]
    fig, ax = plt.subplots(figsize=(11, max(5,len(sel)*.34)))
    im = ax.imshow(Mp[:,sel].T, aspect='auto', cmap='magma')
    ax.set_xticks(range(len(tp))); ax.set_xticklabels(
        [s.split('_',1)[-1] for s in tp.period], rotation=25, ha='right', fontsize=9)
    ax.set_yticks(range(len(sel))); ax.set_yticklabels(labels, fontsize=7.5)
    ax.set_title('時代 × トピック')
    fig.colorbar(im, ax=ax, shrink=.8, label='平均トピック確率')
    fig.tight_layout(); save_fig(fig, 'Step8_heatmap'); plt.show()'''),
 ('md', r'''## 5. 総合 — 4つの方法は同じことを言っているか

| Step | 方法 | 何を測るか |
|---|---|---|
| 4 | MFW + Delta + PCA | 機能語の使用パターン（＝文体） |
| 6 | 通時 word2vec | 語の意味（文脈分布）の変化 |
| 7 | doc2vec | 作品全体の表現 |
| 8 | LDA | 主題の構成 |

**4つが一致する所見は強い。一致しない所見は，なぜ違うかを考える材料になる。**

たとえば「大正期が文体的に過渡的」という所見が Delta でも doc2vec でも出るなら，
それは方法に依存しない。片方でしか出ないなら，方法の性質（機能語 vs 内容語）
に由来する可能性がある。'''),
 ('code', r'''# 4空間の比較：作品ペアの距離順位がどれだけ一致するか
from scipy.stats import spearmanr

spaces = {}
p = OUT/'descriptive'/'delta_matrix.csv'
if need(p, 'この分析のスクリプトを走らせるセルを先に実行すること'):
    Dm = pd.read_csv(p, index_col=0); spaces['Delta(MFW)'] = Dm
p = D2V/'work_similarity.csv' if (D2V:=OUT/'d2v').exists() else None
if p and p.exists():
    Sm = pd.read_csv(p, index_col=0); spaces['doc2vec'] = 1-Sm
p = ML/'topic_by_work.csv'
if need(p, '10_mallet.py を先に走らせること'):
    tw = pd.read_csv(p)
    tcols = [c for c in tw.columns if c.startswith('T')]
    T = tw[tcols].values
    JS = np.zeros((len(T),len(T)))
    for i in range(len(T)):
        for j in range(len(T)):
            m = (T[i]+T[j])/2
            kl = lambda a,b: np.sum(a*np.log((a+1e-12)/(b+1e-12)))
            JS[i,j] = 0.5*kl(T[i],m)+0.5*kl(T[j],m)
    spaces['LDA(JSD)'] = pd.DataFrame(JS, index=tw.work_stem, columns=tw.work_stem)

names = list(spaces)
if len(names) >= 2:
    common = set(spaces[names[0]].index)
    for n in names[1:]: common &= set(spaces[n].index)
    common = sorted(common)
    # 空間ごとに作品名の綴りが違うと，共通集合が小さくなったまま
    # **ρ だけがもっともらしく出る**。件数を必ず確かめること。
    n_min = min(len(spaces[n].index) for n in names)
    print(f'共通作品 {len(common)} 件で比較'
          f'（各空間の作品数の最小は {n_min}）')
    if len(common) < n_min * 0.8:
        print(f'  [warn] **共通が {len(common)}/{n_min} しかない。**'
              '語幹の綴りが空間ごとに違う疑いがある。')
        print('         ρ を読む前に，各空間の index を突き合わせること。')

    pair_rows = []
    for i,a in enumerate(names):
        for b in names[i+1:]:
            A = spaces[a].loc[common,common].values
            B = spaces[b].loc[common,common].values
            iu = np.triu_indices(len(common),1)
            pair_rows.append({'空間 A': a, '空間 B': b,
                              'Spearman ρ': spearmanr(A[iu], B[iu]).correlation,
                              'ペア数': len(iu[0])})
    show(pd.DataFrame(pair_rows).sort_values('Spearman ρ', ascending=False),
         caption=f'作品ペアの距離順位がどれだけ一致するか（共通 {len(common)} 点）',
         fmt={'Spearman ρ':'{:+.3f}'})

    # 全体の ρ は「どの作品で食い違うか」を教えてくれない。
    # Step 7 の課題4（近傍が一致する作品／しない作品を探す）に答えるには，
    # **作品ごと**に順位相関を取る必要がある。
    if len(names) >= 2 and len(common) >= 5:
        LAB = work_labels()
        a, b = names[0], names[1]
        rows_d = []
        for wstem in common:
            ra = spaces[a].loc[wstem, common].drop(wstem)
            rb = spaces[b].loc[wstem, common].drop(wstem)
            rows_d.append({'作品': LAB.get(wstem, wstem),
                           'ρ': round(spearmanr(ra.values, rb.values).correlation, 3),
                           f'{a}の最近傍': LAB.get(ra.idxmin(), ra.idxmin()),
                           f'{b}の最近傍': LAB.get(rb.idxmin(), rb.idxmin())})
        dd = pd.DataFrame(rows_d).sort_values('ρ')
        show(dd.head(8), caption=f'{a} と {b} で近傍の並びが最も食い違う作品',
             fmt={'ρ':'{:+.3f}'})
        show(dd.tail(5).iloc[::-1],
             caption=f'{a} と {b} で最もよく一致する作品', fmt={'ρ':'{:+.3f}'})
        print('食い違う作品は，**どちらの空間が正しいかではなく，'
              '2つの空間が別のものを測っている**ことを示す。原文で確かめること。')'''),
 ('md', r'''## 6. 最終課題（レポート）

テンプレート `my_work/results/final_report.md` の構成で書き，**PDF にして**提出する
（**全体で4000–6000字程度**。図表と「再現のための情報」は字数に含めない）。
PDF を作る道具は問わない（Word・Pages・Google ドキュメントなど）。

- **提出先**：Zulip（{ZULIP_ORG}）の非公開チャネル **{ZULIP_CHANNEL}** ＞ トピック **最終リポート**
- 要旨（3〜5行）をメッセージに書き，PDF を**同じメッセージに添付**する（1人1通）
- 再提出は元の投稿を直さず，同じトピックに新しく投稿する（手順書 §5.3）

構成は次のとおり。

### 1. 問いの設定
近代日本文学における言語変化・文体変化・主題の多様化について，
**このコーパスで答えられる問い**を1つ立てる。
Step 1 の代表性診断をふまえ，**答えられない問い**も明示すること。

### 2. データ
使用したコーパスの構成（時代・ジャンル・作家・語数）を表と図で示す。
前処理の決定（踊り字・外字・語彙素方針・チャンク長・ストップリスト）を
すべて記し，**なぜそう決めたか**を書く。

### 3. 方法
4つの方法のうち最低2つを使う。ハイパーパラメータと乱数シードを明記する。

### 4. 結果
- 図表は自分で作ったものを使う
- **対照条件・交差検証・安定性検査を必ず1つ以上含める**

### 5. 考察
- 作家効果と時代効果をどう切り分けたか
- 結果がコーパスの偏りに由来する可能性をどう排除したか
- 増補すべきテクストは何か（`expansion_candidates.csv` を参照）

### 6. 再現のための情報
教材の版（`git rev-parse --short HEAD`）・辞書・乱数シード・変えた設定を書き，
使用した `config/pipeline.yaml` を添付する。

---

## 付録：この授業で身につけたこと

1. **コーパスは与えられるものではなく，作るものである。**
   メタデータの1列1列に典拠がある状態を作れる。
2. **前処理の決定はすべて分析結果に影響する。**
   何をどう数えたかを記録に残せる。
3. **数値は必ず原文に戻して確かめる。** KWIC を引く習慣。
4. **「変化を検出した」と言うには対照条件が要る。**
   シャッフル検定・グループ交差検証・安定性検査。
'''),
])


# --------------------------------------------------------------------------
def build(lesson: dict) -> dict:
    cells = []
    for i, (kind, src) in enumerate(lesson['cells']):
        # 各ノートブックの冒頭に用語集への導線を1行入れる。予習は
        # 「キーワードを見て自分で説明してみる」，復習は「確認問題を解く」
        # という使い方を想定しているので，入口はステップの先頭に要る。
        if i == 0 and kind == 'md' and src.startswith('# Step'):
            head, _, rest = src.partition('\n')
            src = (head + '\n\n> **用語の予習・復習**: `docs/glossary.md` の '
                   f'Step {lesson["n"]} を参照。'
                   'キーワードを見て自分で説明してみてから読むこと。\n' + rest)
        if kind == 'md':
            src = (src.replace('{ZULIP_ORG}', ZULIP_ORG)
                      .replace('{ZULIP_CHANNEL}', ZULIP_CHANNEL))
        lines = src.splitlines(keepends=True)
        if kind == 'md':
            cells.append({'cell_type': 'markdown', 'metadata': {}, 'source': lines})
        else:
            cells.append({'cell_type': 'code', 'metadata': {},
                          'execution_count': None, 'outputs': [], 'source': lines})
    return {
        'cells': cells,
        'metadata': {
            'kernelspec': {'display_name': 'Python (JLit)', 'language': 'python',
                           'name': 'jlit'},
            'language_info': {'name': 'python', 'version': '3.12'},
            'jlit': {'lesson': lesson['n'], 'title': lesson['title']},
        },
        'nbformat': 4, 'nbformat_minor': 5,
    }


SLUGS = {1: 'corpus_design', 2: 'rebuild_xml', 3: 'normalise_tokenise',
         4: 'descriptive_stylometry', 5: 'word2vec_basics',
         6: 'diachronic_word2vec', 7: 'doc2vec', 8: 'topic_modelling'}


# --------------------------------------------------------------------------
# 課題のテンプレート。各 Step の「このステップの課題」の設問から作る。
# copy_notebooks.py が my_work/results/ にコピーする。
# --------------------------------------------------------------------------
REPRO = """## 再現のための情報

- 教材の版：（`git rev-parse --short HEAD` の出力）
- 辞書：unidic-novel-v202512
- マシン：（ラベル番号。自分の Mac なら機種）
- 変えた設定・乱数シード：
"""


def _questions(md: str) -> list[str]:
    """課題の節から番号付きの設問を取り出す（続きの行は字下げで判定）。"""
    qs, cur = [], None
    for line in md.splitlines():
        m = re.match(r'^(\d+)\. (.*)', line)
        if m:
            if cur is not None:
                qs.append(cur)
            cur = m.group(2)
        elif cur is not None and line.startswith('   ') and line.strip():
            cur += line.strip()
        elif cur is not None and not line.strip():
            qs.append(cur); cur = None
        elif line.startswith('#'):
            if cur is not None:
                qs.append(cur); cur = None
    if cur is not None:
        qs.append(cur)
    return qs


def write_templates(outdir: str) -> int:
    os.makedirs(outdir, exist_ok=True)
    n_out = 0
    for les in LESSONS:
        for kind, src in les['cells']:
            if kind != 'md':
                continue
            if 'このステップの課題' in src.split('\n', 1)[0]:
                qs = _questions(src.split('### このステップの到達点')[0])
                body = [f"# Step {les['n']} 課題 — （氏名）", '']
                for i, q in enumerate(qs, 1):
                    if q.startswith('図を') and '添付' in q:
                        continue                  # 添付の指示は「図表」の節で受ける
                    body += [f'## {i}.', '', f'> {q.replace("**", "")}', '', '（ここに書く）', '']
                body += ['## 図表', '',
                         '- 図1 `（ファイル名）.svg` — 何を，どの設定で示したか',
                         '- 表1 `（ファイル名）.csv` — 何を示したか', '', REPRO]
                path = os.path.join(outdir, f"Step{les['n']}_report.md")
            elif '最終課題' in src.split('\n', 1)[0]:
                heads = re.findall(r'^### (\d\. .+)$', src, re.M)
                body = ['# 最終リポート — （氏名）', '', '## 要旨', '',
                        '（3〜5行。Zulip のメッセージにもこれを書く）', '']
                for h in heads:
                    if h.startswith('6.'):
                        body += [REPRO.replace('## 再現のための情報', f'## {h}').rstrip(),
                                 '- 添付：config/pipeline.yaml', '']
                    else:
                        body += [f'## {h}', '', '（ここに書く）', '']
                body += ['## 参考文献', '', '- ', '']
                path = os.path.join(outdir, 'final_report.md')
            else:
                continue
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('\n'.join(body).rstrip() + '\n')
            n_out += 1
    return n_out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='notebooks')
    ap.add_argument('--templates', default='templates',
                    help='課題のテンプレート（StepN_report.md・final_report.md）の書き出し先')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for les in LESSONS:
        nb = build(les)
        name = f"{les['n']:02d}_{SLUGS[les['n']]}.ipynb"
        with open(os.path.join(args.out, name), 'w', encoding='utf-8') as fh:
            json.dump(nb, fh, ensure_ascii=False, indent=1)
        n_md = sum(1 for c in nb['cells'] if c['cell_type'] == 'markdown')
        n_cd = sum(1 for c in nb['cells'] if c['cell_type'] == 'code')
        print(f'  [ok  ] {name:<34} 解説{n_md:>3}セル / コード{n_cd:>3}セル  '
              f'— {les["title"]}')
    print(f'\n[ok  ] {len(LESSONS)} 件のノートブック → {args.out}')
    k = write_templates(args.templates)
    print(f'[ok  ] {k} 件の課題のテンプレート → {args.templates}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
