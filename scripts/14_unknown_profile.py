#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
14_unknown_profile.py
=====================
未知語リストを**種類ごとに分類**し，「目で見てカタカナが多い」という印象を
数字に変える。あわせて**カタカナ表記の揺れ**をまとめて出す。

なぜ要るのか
------------
未知語率が下がっただけでは前処理が良くなったとは言えない。**残った未知語が
何であるか**で意味が変わる。

====================  ==========================================
残っているもの        意味
====================  ==========================================
カタカナ外来語        **正常**。外来語は開いた類で，辞書に全部は入らない
固有名詞らしい漢字列  **正常**。人名・地名は辞書に限界がある
``ゝ ゞ ヽ ヾ``        **正規化漏れ**。04 の設定を見直す
``※`` ``〓``          **外字が復元できていない**。03 の変換を見直す
文語の活用形          辞書の選択の問題。近代文語 UniDic の併用を検討
====================  ==========================================

**種類が変わると，未知語率という指標の意味も変わる。**
前処理の失敗が消えて外来語だけが残った状態では，未知語率は
「前処理の質」ではなく**外来語の密度**を測っている。外来語密度は
時代・ジャンルの変数（探偵小説・SF は多く，明治の文語論説はほぼ無い）
なので，**未知語率の高い作品を前処理の失敗と読んではいけなくなる。**
この転換点を見落とすと，海野十三の未知語率の高さを「解析の失敗」と
report に書いてしまう。

カタカナ表記の揺れ
------------------
戦前の外来語表記は揺れる。``ストツキング``／``ストッキング``，
``ヰスキー``／``ウイスキー``，``ハンケチ``／``ハンカチ``。
未知語のまま放置すると**同じ語が別の型として数えられ**，
異なり語数が膨らみ，word embedding もトピックも分散する。

本スクリプトは促音・拗音の小書き，``ヰヱヲ``，長音符を畳んだキーで
まとめ，2種類以上の綴りを持つ組を出す。

**ただし，まとめるかどうかは研究の問いによる。** 表記の揺れ自体が
正書法の近代化の資料でもある。**黙って正規化しないこと。**
まとめるなら ``config/pipeline.yaml`` に記録する。

使い方
------
    python3 14_unknown_profile.py --unknown data/tokens/unknown_words.csv
    python3 14_unknown_profile.py --unknown data/tokens/unknown_words.csv \\
        --report data/tokens/tokenise_report.csv \\
        --meta metadata/corpus_metadata_v3.csv --out results/unknown_profile
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict

KATAKANA = r'ァ-ヶーヽヾ'
KANJI = r'㐀-䶿一-鿿豈-﫿'
HIRAGANA = r'ぁ-ゖ'
ODORIJI = 'ゝゞヽヾ〳〵〴〵々'
GAIJI_MARK = '※〓'

RE_KATA = re.compile(f'^[{KATAKANA}]+$')
RE_KATA_MIX = re.compile(f'[{KATAKANA}]')
RE_KANJI = re.compile(f'^[{KANJI}]+$')
RE_HIRA = re.compile(f'^[{HIRAGANA}]+$')
RE_LATIN = re.compile(r'^[A-Za-zＡ-Ｚａ-ｚ]+$')
RE_DIGIT = re.compile(r'^[0-9０-９一二三四五六七八九十百千万億]+$')

# 文語の活用形・助動詞の語尾。網羅ではなく当たりを付けるための手掛かり。
BUNGO_HINT = re.compile(
    r'(けれ|けり|ざり|ざる|べかり|べけれ|たり|なり|めり|らむ|けむ|'
    r'つれ|ぬれ|しか|しき|かる|げれ)$')

CATEGORIES = ('カタカナ外来語', '固有名詞らしい漢字列', '踊り字の残り',
              '外字マーカの残り', '文語の活用形らしい', 'ラテン文字・数字',
              '平仮名列', 'その他')


def classify(s: str) -> str:
    """未知語1件を分類する。**判定の順序が意味を持つ。**

    踊り字と外字マーカを先に見る。これらは「前処理の失敗」であり，
    見落としてはいけないものなので，他の条件より優先して拾う。
    """
    if any(c in s for c in ODORIJI):
        return '踊り字の残り'
    if any(c in s for c in GAIJI_MARK):
        return '外字マーカの残り'
    if RE_KATA.match(s):
        return 'カタカナ外来語'
    if RE_LATIN.match(s) or RE_DIGIT.match(s):
        return 'ラテン文字・数字'
    if RE_KANJI.match(s):
        return '固有名詞らしい漢字列'
    if RE_HIRA.match(s):
        return '文語の活用形らしい' if BUNGO_HINT.search(s) else '平仮名列'
    if RE_KATA_MIX.search(s):
        return 'カタカナ外来語'
    if BUNGO_HINT.search(s):
        return '文語の活用形らしい'
    return 'その他'


def fold_katakana(s: str, loose: bool = False) -> str:
    """カタカナ表記の揺れを畳むキーを作る。

    **これは比較のためのキーであって，本文を書き換えるわけではない。**
    戦前の外来語表記に多い3つの慣習を畳む。

    ====================  ================================
    慣習                  例
    ====================  ================================
    小書きを使わない      ストツキング ⇄ ストッキング
    ``ヰヱヲヷ`` を使う   ヰスキー ⇄ ウイスキー
    唇音の前が ``ム``     ラムプ ⇄ ランプ
    長音符を使わない      サアベル ⇄ サーベル（``loose`` のとき）
    ====================  ================================

    ``loose`` は語頭以外の ``アイウエオ`` も落とす。長音を母音字で
    書いた表記（サアベル・コヒイ）を拾えるが，**別語を同じ組に
    まとめてしまうことがある**（ピアノとピノ）。組は人が見るための
    ものなので誤りが混じってよいが，既定では使わない。
    """
    t = s
    # 旧仮名のカタカナは，現代の2字表記に開いてから比べる。
    # ヰ→イ と畳むと「ヰスキー」と「ウイスキー」が別のキーになってしまう。
    for a, b in (('ヰ', 'ウイ'), ('ヱ', 'ウエ'), ('ヲ', 'ウオ'),
                 ('ヴ', 'ブ'), ('ヷ', 'バ'), ('ヸ', 'ビ'),
                 ('ヹ', 'ベ'), ('ヺ', 'ボ')):
        t = t.replace(a, b)
    # 小書き（促音・拗音）を大書きに畳む
    t = t.translate(str.maketrans('ァィゥェォッャュョヮ', 'アイウエオツヤユヨワ'))
    # 唇音の前の ム を ン に。ラムプ ⇄ ランプ，シムポジウム ⇄ シンポジウム
    t = re.sub('ム(?=[パピプペポバビブベボ])', 'ン', t)
    t = t.replace('ー', '')          # 長音符の有無
    if loose:
        t = t[:1] + re.sub('[アイウエオ]', '', t[1:])
    return t


def unknown_pos_profile(tsvdir: str) -> dict:
    """未知語に付いた**品詞の推定**を集める。

    ``unknown_words.csv`` は表層形と頻度しか持たないので，その語が
    固有名詞と推定されたのか普通名詞と推定されたのかは分からない。
    TSV には推定品詞が入っているので，そこから拾う。

    **なぜ見るのか。** カタカナの固有名詞（``ハルビン`` ``マリアナ``
    ``ドストエフスキー``）が未知語の上位を占めるのは，
    **解析が正しく働いている徴候**である。外国の人名・地名は
    二重に開いた類（固有名詞であり，かつ外来語である）なので，
    どの辞書にも入らない。そして**1語のまとまりとして未知語に
    立てられている**ことは，辞書が無理に既知語へ当てはめず，
    「知らない」と正しく申告したことを意味する。

    逆に，これらが未知語に出てこないのに未知語率が低い場合は，
    ``scan_forced_splits`` が拾う無理な当てはめを疑う。
    """
    if not tsvdir or not os.path.isdir(tsvdir):
        return {}
    pos = defaultdict(Counter)
    for fn in sorted(os.listdir(tsvdir)):
        if not fn.endswith('.tsv'):
            continue
        with open(os.path.join(tsvdir, fn), encoding='utf-8-sig') as fh:
            for line in fh:
                if line.startswith('#'):
                    continue
                f = line.rstrip('\n').split('\t')
                if len(f) < 8 or f[6] == 'EOS':
                    continue
                surface, lemma, pos1, pos2 = f[1], f[3], f[6], f[7]
                if surface and not lemma:          # 未知語
                    pos[surface][f'{pos1}-{pos2}' if pos2 else pos1] += 1
    return {s: c.most_common(1)[0][0] for s, c in pos.items()}


def scan_forced_splits(tsvdir: str, top: int = 30) -> list[dict]:
    """**辞書が「知っている」と誤って主張した箇所**を探す。

    未知語リストは片側しか映さない。そこに出るのは
    「辞書に無いと**正しく**判定されたもの」であり，逆の誤り——
    **辞書に無い語を，既知の語に無理に当てはめてしまったもの**——は
    定義上1件も出ない。

    未知語率はこの誤りで**下がる**。``ストツキング`` を
    ``ストツ`` ＋ ``キング`` と切れば，どちらも既知なので未知語は 0 件に
    なる。率だけを見れば「よく解析できた」ように見える。

    カタカナはこの誤りを機械的に拾える唯一の場所である。外来語は原則
    1語として書かれるので，**連続するカタカナが2つ以上のトークンに
    割れていたら**，それは複合語か，さもなければ無理な当てはめである。

    漢字の固有名詞（``渋江`` → ``渋`` ＋ ``江``）も同じ誤りを起こすが，
    正解データが無いと複合語と区別できない。**カタカナだけを見るのは，
    確実に判定できる範囲に限るためである。**

    戻り値は「割れたカタカナ列」の一覧。``コーヒーカップ`` のような
    正当な複合語も混じるので，**人が見て判断する材料**である。
    """
    if not tsvdir or not os.path.isdir(tsvdir):
        return []
    runs = Counter()
    parts_of: dict[str, str] = {}
    for fn in sorted(os.listdir(tsvdir)):
        if not fn.endswith('.tsv'):
            continue
        run: list[str] = []
        with open(os.path.join(tsvdir, fn), encoding='utf-8-sig') as fh:
            for line in fh:
                if line.startswith('#'):
                    continue
                f = line.rstrip('\n').split('\t')
                if len(f) < 7:
                    continue
                surface, lemma, pos1 = f[1], f[3], f[6]
                # 既知（lemma がある）かつカタカナのみのトークンを繋ぐ。
                # 未知語は既に unknown_words.csv に出ているので数えない。
                if surface and lemma and RE_KATA.match(surface):
                    run.append(surface)
                    continue
                if len(run) >= 2:
                    joined = ''.join(run)
                    runs[joined] += 1
                    parts_of[joined] = ' ＋ '.join(run)
                run = []
        if len(run) >= 2:
            joined = ''.join(run)
            runs[joined] += 1
            parts_of[joined] = ' ＋ '.join(run)
    return [{'joined': k, 'parts': parts_of[k], 'count': v}
            for k, v in runs.most_common(top)]


def read_rows(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))



def default_meta() -> str:
    """使うメタデータを決める。自分で作った v3（*_local.csv）> 配布版 v3 > v2。"""
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'metadata')
    for name in ('corpus_metadata_v3_local.csv', 'corpus_metadata_v3.csv',
                 'corpus_metadata_v2.csv'):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(base, 'corpus_metadata_v3.csv')

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--unknown', required=True,
                    help='05 が書いた unknown_words.csv')
    ap.add_argument('--report', default=None,
                    help='05 が書いた tokenise_report.csv（作品別の未知語率）')
    ap.add_argument('--meta', default=None,
                    help='既定: metadata/ の *_v3_local.csv > v3 > v2')
    ap.add_argument('--tsv', default=None,
                    help='05 の tsv ディレクトリ。**逆の誤り**'
                         '（辞書に無い語を既知の語に当てはめた箇所）を探す')
    ap.add_argument('--out', default=None, help='CSV を書き出す先')
    ap.add_argument('--top', type=int, default=12, help='各種類の例示件数')
    ap.add_argument('--loose', action='store_true',
                    help='長音を母音字で書いた表記（サアベル）も畳む。'
                         '別語を同じ組にまとめることがある')
    args = ap.parse_args()
    if args.meta is None:
        args.meta = default_meta()

    rows = read_rows(args.unknown)
    if not rows:
        sys.exit(f'未知語リストが読めない: {args.unknown}\n'
                 '  05_tokenise_unidic.py を先に走らせること。')
    col = 'surface' if 'surface' in rows[0] else list(rows[0])[0]
    fcol = next((c for c in ('freq', 'count', 'n') if c in rows[0]), None)

    items = []
    for r in rows:
        s = (r.get(col) or '').strip()
        if not s:
            continue
        n = int(float(r.get(fcol) or 1)) if fcol else 1
        items.append((s, n))

    # ---- 種類ごとの集計 ---------------------------------------------------
    by_cat_types = Counter()
    by_cat_tokens = Counter()
    examples = defaultdict(list)
    for s, n in items:
        c = classify(s)
        by_cat_types[c] += 1
        by_cat_tokens[c] += n
        if len(examples[c]) < args.top:
            examples[c].append(s)

    tt = sum(by_cat_types.values())
    tk = sum(by_cat_tokens.values()) or 1
    print(f'未知語 {tt:,} 種'
          + (f' / 延べ {tk:,} 語' if fcol else '（頻度列が無いので種数のみ）'))
    print(f'\n{"種類":<22}{"種数":>8}{"種%":>8}{"延べ":>10}{"延べ%":>8}')
    cat_rows = []
    for c in CATEGORIES:
        if not by_cat_types[c]:
            continue
        print(f'{c:<22}{by_cat_types[c]:>8,}{100*by_cat_types[c]/max(1,tt):>7.1f}%'
              f'{by_cat_tokens[c]:>10,}{100*by_cat_tokens[c]/tk:>7.1f}%')
        cat_rows.append({'category': c, 'types': by_cat_types[c],
                         'types_pct': round(100*by_cat_types[c]/max(1,tt), 2),
                         'tokens': by_cat_tokens[c],
                         'tokens_pct': round(100*by_cat_tokens[c]/tk, 2),
                         'examples': ' '.join(examples[c])})
    print()
    for c in CATEGORIES:
        if examples[c]:
            print(f'  {c}: {" ".join(examples[c])}')

    # ---- 前処理の失敗が残っていないか -------------------------------------
    bad = by_cat_types['踊り字の残り'] + by_cat_types['外字マーカの残り']
    print()
    if bad:
        print(f'[FATAL] 前処理の失敗が {bad} 種残っている。'
              '踊り字は 04，外字マーカは 03 を見直すこと。')
    else:
        print('[ok  ] 踊り字・外字マーカの残りは **0 種**。'
              '正規化と外字復元は効いている。')
    kata = 100 * by_cat_types['カタカナ外来語'] / max(1, tt)
    if kata >= 50:
        print(f'[info ] 未知語の {kata:.0f}% がカタカナ外来語である。')
        print('       **この段階で未知語率の意味が変わっている。**')
        print('       前処理の質ではなく**外来語の密度**を測る指標になった。')
        print('       外来語密度は時代・ジャンルの変数（探偵小説・SF は多く，')
        print('       明治の文語論説はほぼ無い）なので，未知語率の高い作品を')
        print('       「解析の失敗」と読まないこと。')

    # ---- 未知語の推定品詞（固有名詞かどうか）------------------------------
    if args.tsv:
        posmap = unknown_pos_profile(args.tsv)
        if posmap:
            kata_items = [(s, n) for s, n in items
                          if classify(s) == 'カタカナ外来語']
            prop = [(s, n) for s, n in kata_items
                    if '固有名詞' in posmap.get(s, '')]
            comm = [(s, n) for s, n in kata_items
                    if s in posmap and '固有名詞' not in posmap[s]]
            nk = sum(n for _, n in kata_items) or 1
            print('\nカタカナの未知語の内訳（推定品詞）')
            print(f'  固有名詞と推定  {len(prop):>5} 種 / 延べ '
                  f'{sum(n for _, n in prop):>7,}'
                  f'（{100*sum(n for _, n in prop)/nk:>5.1f}%）')
            print(f'  それ以外        {len(comm):>5} 種 / 延べ '
                  f'{sum(n for _, n in comm):>7,}'
                  f'（{100*sum(n for _, n in comm)/nk:>5.1f}%）')
            if prop:
                ex = ' '.join(s for s, _ in sorted(prop, key=lambda x: -x[1])[:12])
                print(f'  固有名詞の例: {ex}')
            print('  **カタカナの固有名詞が上位を占めるのは，解析が正しく')
            print('  働いている徴候である。** 外国の人名・地名は二重に開いた類')
            print('  （固有名詞かつ外来語）なので，どの辞書にも入らない。')
            print('  それが**1語のまとまりとして**未知語に立っていることは，')
            print('  辞書が無理に既知語へ当てはめず「知らない」と正しく')
            print('  申告したことを意味する。')

    # ---- 逆の誤り：辞書が「知っている」と誤って主張した箇所 ---------------
    split_rows = scan_forced_splits(args.tsv) if args.tsv else []
    if args.tsv:
        print('\n連続するカタカナが2トークン以上に割れた箇所'
              f'（上位 {len(split_rows)} 種）')
        print('  **未知語リストには出ない誤りである。** 無理な当てはめなら')
        print('  未知語率が下がってしまう（ストツキング → ストツ ＋ キング）。')
        if not split_rows:
            print('  該当なし。')
        for r in split_rows:
            print(f'  {r["count"]:>5}  {r["joined"]:<16} ← {r["parts"]}')
        if split_rows:
            print('\n  正当な複合語（コーヒーカップ）も混じる。**人が見て判断する。**')
            print('  無理な当てはめが多いなら，未知語率の低さは割り引いて読む。')

    # ---- カタカナ表記の揺れ -----------------------------------------------
    groups = defaultdict(list)
    for s, n in items:
        if classify(s) == 'カタカナ外来語':
            groups[fold_katakana(s, args.loose)].append((s, n))
    var = {k: v for k, v in groups.items() if len({s for s, _ in v}) > 1}
    print(f'\nカタカナ表記の揺れ: {len(var)} 組')
    var_rows = []
    for k, v in sorted(var.items(), key=lambda kv: -sum(n for _, n in kv[1]))[:20]:
        forms = '／'.join(f'{s}({n})' if fcol else s
                          for s, n in sorted(v, key=lambda x: -x[1]))
        print(f'  {forms}')
        var_rows.append({'fold_key': k, 'n_forms': len({s for s, _ in v}),
                         'total': sum(n for _, n in v), 'forms': forms})
    if var:
        print('\n  **まとめるかどうかは研究の問いによる。** 表記の揺れ自体が')
        print('  正書法の近代化の資料でもある。黙って正規化しないこと。')
        print('  まとめるなら config/pipeline.yaml に記録する。')

    # ---- 作品別の未知語率を，時代・ジャンルと並べる ------------------------
    if args.report:
        rep = read_rows(args.report)
        meta = {}
        for r in read_rows(args.meta):
            pid = str(r.get('aozora_person_id') or '').strip()
            wid = str(r.get('aozora_work_id') or '').strip()
            row = {'label': f"{r.get('author_ja','?')}『{str(r.get('title_aozora'))[:12]}』",
                   'period': r.get('period', ''), 'genre_sub': r.get('genre_sub', ''),
                   'orthography': r.get('kana_orthography', '')}
            if pid and wid:
                meta[f'{pid.zfill(6)}_{wid.zfill(6)}'] = row
        if rep:
            rep.sort(key=lambda r: -float(r.get('unknown_rate') or 0))
            print('\n未知語率の高い作品（外来語密度として読む）')
            print(f'{"未知語率":>9}  {"作品":<26}{"時代":<20}{"下位ジャンル"}')
            for r in rep[:12]:
                st = os.path.splitext(r.get('file', ''))[0]
                m = meta.get(st, {})
                lab = m.get('label', st)
                w = sum(2 if '　' <= ch else 1 for ch in lab)
                print(f'{float(r["unknown_rate"]):>8.2%}  {lab}{" " * max(0, 26 - w)}'
                      f'{m.get("period", ""):<20}{m.get("genre_sub", "")}')

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for fn, rs in (('unknown_categories.csv', cat_rows),
                       ('katakana_variants.csv', var_rows),
                       ('forced_splits.csv', split_rows)):
            if not rs:
                continue
            with open(os.path.join(args.out, fn), 'w', newline='',
                      encoding='utf-8-sig') as fh:
                w = csv.DictWriter(fh, fieldnames=list(rs[0].keys()))
                w.writeheader(); w.writerows(rs)
            print(f'[ok  ] {fn}')
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
