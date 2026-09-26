#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lib/aozora.py
=============
青空文庫のテクストを扱うための共通関数。

本モジュールが引き受ける問題
----------------------------
1. **外字の復元** — 青空文庫は JIS X 0208 外の漢字を
   ``※［＃「广＋(炎/鳥)」、第4水準2-81-40］`` のように注記する。
   面区点は EUC-JIS-2004 コーデックを介して Unicode に確定的に変換できる。
   v1 コーパスでは ``［＃…］`` のみが削除され ``※`` だけが残った結果，
   592 箇所で文字が失われている。
2. **踊り字の展開** — ``ゝゞヽヾ`` および くの字点 ``／＼`` ``／″＼`` は
   UniDic に登録がなく未知語になる。直前の仮名を複製して展開する。
   ``々`` は UniDic が語彙素として扱うため **展開しない**。
3. **注記の分離** — ルビ・入力者注・組版指示・奥付を本文から分離し，
   XML の属性／要素として保持する。除去ではなく分離が要点である。
"""
from __future__ import annotations

import csv
import os
import re
import unicodedata

# --------------------------------------------------------------------------
# 外字（gaiji）
# --------------------------------------------------------------------------

# ※［＃「注記本文」、第N水準m-kk-tt］ / ※［＃「…」、U+XXXX、…］
GAIJI_RE = re.compile(
    r'※?［＃(?P<desc>[^］]*?)］'
)
MENKUTEN_RE = re.compile(r'第[34]水準(?P<men>[12])-(?P<ku>\d{1,2})-(?P<ten>\d{1,2})')
UPLUS_RE = re.compile(r'U\+(?P<hex>[0-9A-Fa-f]{4,6})')


def menkuten_to_char(men: int, ku: int, ten: int) -> str | None:
    """JIS X 0213 の 面-区-点 を Unicode 文字に変換する。

    EUC-JIS-2004 の符号化規則:
        面1: 0xA0+ku, 0xA0+ten
        面2: 0x8F, 0xA0+ku, 0xA0+ten
    Python 標準の ``euc_jis_2004`` コーデックが復号する。
    """
    try:
        if men == 1:
            raw = bytes([0xA0 + ku, 0xA0 + ten])
        elif men == 2:
            raw = bytes([0x8F, 0xA0 + ku, 0xA0 + ten])
        else:
            return None
        ch = raw.decode('euc_jis_2004')
        return ch if len(ch) >= 1 else None
    except (ValueError, UnicodeDecodeError):
        return None


# 面区点も U+ も持たず，字の**名前**だけが書かれる外字注記。
# 例: ※［＃小書き片仮名ヲ、160-9］
#     末尾の数字は底本のページ-行であって面区点ではない。JIS X 0213 に無い字
#     なので青空文庫は番号を書けない。Unicode に対応字のあるものだけを
#     ここで解決し，無いもの（「弋＋頁」のような合字の説明）は
#     <g ref="unresolved"> として残す。
NAMED_GAIJI = {
    '小書き片仮名ヰ': '\U0001B164',
    '小書き片仮名ヱ': '\U0001B165',
    '小書き片仮名ヲ': '\U0001B166',
    '小書き片仮名ン': '\U0001B167',
    '小書き平仮名ゐ': '\U0001B11F',
    '小書き平仮名ゑ': '\U0001B120',
    '小書き平仮名を': '\U0001B121',
    '白ゴマ点': '﹆',
    '黒ゴマ点': '﹅',
    '二の字点': '〻',
}
NAMED_RE = re.compile('|'.join(re.escape(k) for k in NAMED_GAIJI))


def load_gaiji_supplement(path: str) -> dict[str, dict]:
    """外字の補助対照表を読む（``config/gaiji_supplement.tsv``）。

    青空文庫は **JIS X 0213 に無い**文字を ※［＃「巾＋兌」、47-下段-24］ の
    形で示す。面区点が無いので機械には復元しようがないが，JIS X 0213 に
    無いだけで **Unicode には在る字が大半**である。編者が同定した対応を
    この表に書けば復元できる。

    ⚠ **面区点からの復元とは性質が違う。** 前者は EUC-JIS-2004 を介した
    確定的な変換だが，こちらは**字形の説明文からの推定**であり，編者の
    判断である。だから XML には ``resp="editor"`` を付けて区別する。

    ``kind`` が ``glyph`` の行は，**Unicode に符号点が無い**字
    （「まいらせそろ」の合略仮名など）。文字を当てるのではなく，
    TEI の ``<g ref="#id"/>`` として記録し，字形の定義を
    ``teiHeader/charDecl`` に置く。平文の派生物では私用領域の文字で表す。

    ``mapping`` 列（``kind=glyph`` のときだけ）は，**その字形にいちばん
    近い標準の字**。字形としての身許は ``<g ref="#id"/>`` と charDecl に残した
    まま，平文ではこの字を使う。TEI の ``<mapping type="standard">`` に対応
    する。屋号記号「丸にナ」を ㋤ で表しつつ，屋号記号であることを失わない，
    というような場合に使う。空なら平文は私用領域の文字になる。

    ``glyph_name`` 列は ``charDecl`` の ``<glyphName>`` に入る字の名。
    引き当ての鍵（``note``）は注記の断片でよいが，名は**あとから見て何の
    字か分かる**ものでなければならない。空なら鍵をそのまま使う。
    """
    out: dict[str, dict] = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding='utf-8-sig') as fh:
        rdr = csv.DictReader((l for l in fh if not l.startswith('#')),
                             delimiter='\t')
        for r in rdr:
            note = (r.get('note') or '').strip()
            ch = (r.get('char') or '').strip()
            if not note or not ch:
                continue
            want = (r.get('codepoint') or '').strip().upper()
            if want and want != f'U+{ord(ch):04X}':
                raise ValueError(
                    f'{path}: 「{note}」の char と codepoint が食い違う'
                    f'（{ch} は U+{ord(ch):04X}，表には {want}）')
            kind = (r.get('kind') or 'unicode').strip()
            gid = (r.get('glyph_id') or '').strip()
            mapping = (r.get('mapping') or '').strip()
            if kind == 'glyph':
                # 字形として記録する行は，平文で使う私用領域の符号と
                # xml:id の両方が要る。
                if not gid:
                    raise ValueError(f'{path}: 「{note}」は kind=glyph だが'
                                     ' glyph_id が無い')
                if not (0xE000 <= ord(ch) <= 0xF8FF):
                    raise ValueError(f'{path}: 「{note}」は kind=glyph だが'
                                     f' char が私用領域外（{ch} U+{ord(ch):04X}）')
            elif mapping:
                raise ValueError(f'{path}: 「{note}」は kind={kind} なので'
                                 ' mapping 列は使えない')
            out[note] = {'char': ch, 'kind': kind, 'glyph_id': gid,
                         'mapping': mapping,
                         'glyph_name': (r.get('glyph_name') or '').strip(),
                         'evidence': (r.get('evidence') or '').strip()}
    return out


def resolve_gaiji(desc: str,
                  supplement: dict[str, dict] | None = None
                  ) -> tuple[str | None, str]:
    """注記本文から外字の実体を求める。

    戻り値 ``(文字 or None, 種別)``。種別は ``menkuten`` / ``uplus`` /
    ``named`` / ``supplement`` / ``glyph`` / ``unresolved`` のいずれか。

    ``supplement`` は :func:`load_gaiji_supplement` の戻り値。**面区点より
    先に引く。**

    ⚠ 当初は「底本が番号を与えているならそちらが優先」と考えて面区点を先に
    引いたが，それでは誤る注記がある。

        ※［＃「※(第4水準2-13-74) 」の「斤」に代えて「りっとう」、132-中段-24］

    これは「2-13-74 の**斤を立刀に替えた字**」の意で，面区点は**別の字の
    説明の中に入れ子**になっている。先に面区点を引くと基の字（斲）が返り，
    **違う字が本文に入る**。例外は出ないので誰も気づかない。

    編者が対照表に鍵を書いた以上，その判断が機械的な変換に優先する。
    表に無ければ従来どおり面区点・U+・名前の順に引く。
    """
    if supplement:
        # 注記は ※［＃「巾＋兌」、47-下段-24］ の形。鍵は 「」 の中身だが，
        # 鉤括弧を使わない書き方（※［＃「穀」の「禾」に代えて「黄」…］）も
        # あるので，**表の鍵が注記に含まれるか**で引く。長い鍵から順に見る
        # （「口＋罅のつくり」が「口＋罅」に食われないように）。
        for key in sorted(supplement, key=len, reverse=True):
            if key in desc:
                e = supplement[key]
                return e['char'], ('glyph' if e['kind'] == 'glyph'
                                   else 'supplement')
    m = MENKUTEN_RE.search(desc)
    if m:
        ch = menkuten_to_char(int(m['men']), int(m['ku']), int(m['ten']))
        if ch:
            return ch, 'menkuten'
    m = UPLUS_RE.search(desc)
    if m:
        try:
            return chr(int(m['hex'], 16)), 'uplus'
        except ValueError:
            pass
    m = NAMED_RE.search(desc)
    if m:
        return NAMED_GAIJI[m.group(0)], 'named'
    return None, 'unresolved'


# --------------------------------------------------------------------------
# 踊り字（iteration marks）
# --------------------------------------------------------------------------

# 清音 → 濁音
VOICE = {
    'か': 'が', 'き': 'ぎ', 'く': 'ぐ', 'け': 'げ', 'こ': 'ご',
    'さ': 'ざ', 'し': 'じ', 'す': 'ず', 'せ': 'ぜ', 'そ': 'ぞ',
    'た': 'だ', 'ち': 'ぢ', 'つ': 'づ', 'て': 'で', 'と': 'ど',
    'は': 'ば', 'ひ': 'び', 'ふ': 'ぶ', 'へ': 'べ', 'ほ': 'ぼ',
    'う': 'ゔ',
    'カ': 'ガ', 'キ': 'ギ', 'ク': 'グ', 'ケ': 'ゲ', 'コ': 'ゴ',
    'サ': 'ザ', 'シ': 'ジ', 'ス': 'ズ', 'セ': 'ゼ', 'ソ': 'ゾ',
    'タ': 'ダ', 'チ': 'ヂ', 'ツ': 'ヅ', 'テ': 'デ', 'ト': 'ド',
    'ハ': 'バ', 'ヒ': 'ビ', 'フ': 'ブ', 'ヘ': 'ベ', 'ホ': 'ボ',
    'ウ': 'ヴ',
}
# 濁音 → 清音（ゝ が濁音の後に来た場合の清音化）
DEVOICE = {v: k for k, v in VOICE.items()}

KANA_RE = re.compile(r'[ぁ-ゖァ-ヺー]')


def expand_kana_odoriji(text: str) -> tuple[str, int]:
    """``ゝゞヽヾ`` を直前の仮名で展開する。展開数も返す。

    規則
        ゝ  直前の平仮名をそのまま（直前が濁音なら清音化）
        ゞ  直前の平仮名を濁音化
        ヽ  直前の片仮名をそのまま
        ヾ  直前の片仮名を濁音化
    連続する踊り字（例 ``ホホホヽヽ``）は，展開済みの文字を参照して左から順に処理する。
    """
    out: list[str] = []
    n = 0
    for ch in text:
        if ch in 'ゝゞヽヾ' and out:
            prev = out[-1]
            if ch in 'ゝヽ':
                rep = DEVOICE.get(prev, prev)
            else:  # ゞ ヾ
                rep = VOICE.get(prev, VOICE.get(DEVOICE.get(prev, prev), prev))
            if KANA_RE.match(rep):
                out.append(rep)
                n += 1
                continue
            # 直前が仮名でない（漢字等）場合は展開できないので原形を残す
            out.append(ch)
        else:
            out.append(ch)
    return ''.join(out), n


# くの字点。青空文庫のプレーンテクストでは ／＼ ／″＼、XHTML では 〳〵 〴〵
KUNOJI_RE = re.compile(r'(／＼|／″＼|〳〵|〴〵|〳|〴)')


def expand_kunoji(text: str, max_unit: int = 4) -> tuple[str, int]:
    """くの字点を直前の仮名連続で展開する。

    くの字点は「直前の 2 文字以上の語句をくりかえす」記号である。
    原理的には範囲が曖昧なので，**直前の仮名連続（最大 max_unit 文字）**を
    単位と見なすヒューリスティクスを用い，展開したものは XML 側で
    ``@resp="auto"`` を付して機械推定であることを残す。
    """
    n = 0

    def repl(m: re.Match) -> str:
        nonlocal n
        start = m.start()
        # 直前の仮名連続を拾う
        i = start
        unit = []
        while i > 0 and len(unit) < max_unit:
            c = text[i - 1]
            if KANA_RE.match(c):
                unit.insert(0, c)
                i -= 1
            else:
                break
        if len(unit) < 2:
            return m.group(0)
        n += 1
        rep = ''.join(unit)
        if '″' in m.group(0) or m.group(0).startswith('〴'):
            rep = (VOICE.get(rep[0], rep[0]) + rep[1:])
        return rep

    return KUNOJI_RE.sub(repl, text), n


def normalise_iteration_marks(text: str) -> tuple[str, dict]:
    """踊り字の一括処理。``々`` は意図的に残す。"""
    t, n_kuno = expand_kunoji(text)
    t, n_kana = expand_kana_odoriji(t)
    return t, {'kunoji_expanded': n_kuno, 'kana_odoriji_expanded': n_kana}


# --------------------------------------------------------------------------
# その他の正規化
# --------------------------------------------------------------------------

DITTO_RE = re.compile(r'〃')


def normalise_chars(text: str, nfkc_latin_digits: bool = True) -> str:
    """表記の正規化。

    - 全角ラテン文字・全角数字を半角へ（NFKC を文字クラス限定で適用）。
      日本語の仮名・記号には NFKC をかけない（「」が変形するため）。
    - 波ダッシュ・全角チルダ等の異体を統一。
    """
    if nfkc_latin_digits:
        out = []
        for ch in text:
            if '！' <= ch <= '～':      # 全角 ASCII
                out.append(unicodedata.normalize('NFKC', ch))
            else:
                out.append(ch)
        text = ''.join(out)
    text = text.replace('〜', '～')      # 波ダッシュ → 全角チルダ に統一
    text = text.replace('−', '-')      # 全角マイナス
    return text


# --------------------------------------------------------------------------
# 本文と奥付の分離
# --------------------------------------------------------------------------

COLOPHON_MARKERS = (
    '底本：', '底本:', '入力：', '入力:', '校正：', '校正:',
    '青空文庫作成ファイル', 'このファイルは、インターネットの図書館、青空文庫',
)


def strip_colophon(lines: list[str]) -> tuple[list[str], list[str]]:
    """プレーンテクスト版から奥付ブロックを切り離す。

    XHTML 版を用いる場合は ``div.bibliographical_information`` を見ればよいので
    この関数は不要だが，既存のテクストを点検する用途のために残す。
    """
    for i, ln in enumerate(lines):
        if any(ln.strip().startswith(m) for m in COLOPHON_MARKERS):
            return lines[:i], lines[i:]
    return lines, []


def looks_like_colophon(text: str) -> bool:
    """テクスト末尾に奥付が残っていないかの検査。"""
    tail = text[-2000:]
    hits = sum(1 for m in COLOPHON_MARKERS if m.replace('：', '').replace(':', '') in tail.replace(' ', ''))
    return hits >= 2
