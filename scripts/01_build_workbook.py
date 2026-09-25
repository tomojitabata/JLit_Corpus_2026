#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
01_build_workbook.py
====================
metadata/corpus_metadata_v2.csv 等から，配布用の Excel ブックを組み立てる。

シート構成
    凡例                 このブックの読み方と修正方針
    metadata_v2          作品ごとの改訂版メタデータ（64行）
    changes_from_v1      v1 からの変更点一覧（typ / 旧値 / 新値 / 典拠）
    diagnostics          テクスト実測値と検出された問題
    representativeness   代表性の集計（COUNTIFS による動的集計）
    expansion_candidates 増補候補
    codebook             列定義と統制語彙

実行:
    python3 01_build_workbook.py --meta metadata --out metadata/corpus_metadata_v2.xlsx
"""
from __future__ import annotations

import argparse
import csv
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

FONT = "Arial"
H_FILL = PatternFill("solid", fgColor="1F3864")
H_FONT = Font(name=FONT, bold=True, color="FFFFFF", size=10)
BODY = Font(name=FONT, size=10)
WARN = PatternFill("solid", fgColor="FFF2CC")
BAD = PatternFill("solid", fgColor="FBD5D5")
NOTE_FONT = Font(name=FONT, size=10, italic=True, color="7F7F7F")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


# --------------------------------------------------------------------------
# v1 → v2 変更点。典拠を必ず添える。
# (id, 列, v1の値, v2の値, 変更の種類, 典拠)
# --------------------------------------------------------------------------
CHANGES = [
 ("JLIT028","year","1977","1935","誤り（底本刊年を初出年としていた）","青空文庫 図書カード card43656：初出「新青年」博文館 1935年4〜5月号"),
 ("JLIT039","year","1971","1944–1945","誤り（初刊年を初出年としていた）","日記本体の執筆年。1971年は講談社版の刊年"),
 ("JLIT025","year","1927","1924–1926","誤り（単行本年を初出年としていた）","青空文庫 図書カード card2015：「改造」1924年9月〜1926年9月号"),
 ("JLIT056","year","1935","1929–1935","不正確（完結年のみ）","「中央公論」連載期間"),
 ("JLIT058","year","1919","1918–1919","不正確（完結年のみ）","青空文庫 図書カード card843：「東京朝日新聞」1918年5月1日〜1919年10月24日"),
 ("JLIT064","year","1910","1910–1911","不正確（開始年のみ）","青空文庫 図書カード card2522：「昴」1910年3月〜1911年8月"),
 ("JLIT052","year","1913","1912–1913","不正確（完結年のみ）","青空文庫 図書カード card49682：「やまと新聞」1912年11月13日〜1913年1月21日"),
 ("JLIT049","year","1917","1936（要確認）","誤り（シリーズ開始年を個別話の年としていた）","1917年は半七捕物帳シリーズ全体の開始年。第69話「白蝶怪」は後期作"),
 ("JLIT034","year","1898","1898–1899","不正確","「國民新聞」連載期間"),
 ("JLIT026","year","1947","1946–1947","不正確","「新日本文学」創刊号（1946年3月）が初出"),
 ("JLIT009","year","1944","1947（典拠なし）","典拠不明","青空文庫カードは九州書房版1947年1月30日。v1の1944の根拠が不明"),
 ("JLIT036","year","1930","1928–1930（初出）／1949（本文の版）","概念の混同","初出年と底本テクストの成立年を分離した"),
 ("ALL","ndc","「小説、物語」等のラベル文字列","913 / K913 / 914 / 915 / 289 / 370 / 180","データ型の誤り","青空文庫の分類欄は NDC 番号を持つ。ラベルは ndc_label 列に分離"),
 ("JLIT031","ndc","「自己啓発？」","180（仏教）","分類の誤り","青空文庫 図書カード card52342：NDC 180"),
 ("JLIT046","ndc","「教育」","370（教育）","ラベル→番号","青空文庫 図書カード card47061：NDC 370"),
 ("JLIT047","ndc","「個人伝記」","289（個人伝記）","ラベル→番号","青空文庫 図書カード card1864：NDC 289"),
 ("JLIT054","ndc","「評論、エッセイ、随筆」","914","ラベル→番号","青空文庫 図書カード card26：NDC 914"),
 ("JLIT055","ndc","「評論、エッセイ、随筆」","914","ラベル→番号","青空文庫 図書カード card1503：NDC 914"),
 ("JLIT039","ndc","「日記、書簡、紀行」","915","ラベル→番号","青空文庫 図書カード card1255：NDC 915"),
 ("児童書7件","ndc","「小説、物語 (児童書)」","K913","ラベル→番号","青空文庫は児童書に K 接頭辞付き NDC を与える"),
 ("ALL","genre","英語ラベル・日本語ラベル・別題・誤綴の混在","genre_main / genre_sub の2層統制語彙","語彙統制","'Histrical novel'（誤綴）, '（二十世紀鉄仮面）'（別題）, '怪奇'（日本語）等を整理"),
 ("JLIT011","genre","Experimental?","Fiction / Modern（form=cycle）","分類の誤り","「金銭無情」「失恋難」「夜の王様」「王様失脚」4篇の連作。実験小説ではない"),
 ("JLIT023","genre","超現実主義的","Fiction / Experimental（narration=dialogue）","記述の精密化","全篇対話体である点を narration 列で表現"),
 ("JLIT028","genre","（二十世紀鉄仮面）","Fiction / Detective","別題の混入","「二十世紀鉄仮面」は改題。genre 列に入るべきものではない"),
 ("JLIT013","genre","小説、物語","Fiction / Modern","ndc ラベルの混入","genre 列に NDC ラベルが入っていた"),
 ("JLIT061/62/63","genre","Histrical novel","Fiction|Nonfiction / Historical(-Biography)","誤綴の訂正と再分類","『伊沢蘭軒』『渋江抽斎』は史伝＝Nonfiction、『大塩平八郎』は歴史小説"),
 ("ALL","comments","語り・叢書形態・文体注記の混在","narration / form / style_class / note に分解","構造化","「現代、独白」→narration=first、「Collection」→form=collection、「Colloquial」→style_class"),
 ("ALL","brow","high / low の二値","register_level: canonical / middlebrow / popular / documentary ＋ audience: general / juvenile","尺度の再設計","児童書とノンフィクションが high/low に押し込まれていた"),
 ("JLIT004","completeness","—","DUPLICATE","【重大】本文の取り違え","乱歩_灰色の巨人.txt の本文は『魔法博士』と同一（8-gram 一致率45.3%、冒頭完全一致）"),
 ("JLIT004","completeness","DUPLICATE","complete","【解消】カード56676から取り直し","全29章（「志摩の女王」〜「怪人のさいご」）。56679『魔法博士』との文字12-gram 一致率 0.0009"),
 ("JLIT057","completeness","—","PARTIAL","【重大】不完全収録","『家』は下巻のみ。上巻（card1509）が欠落"),
 ("JLIT057","completeness","PARTIAL","part","【解消】上巻を増補","card1509 を取得。上下そろったので欠落ではなく分冊"),
 ("JLIT056","completeness","complete","superseded","二重計上の回避","v1 の合本。増補で 001504〜001507 を分冊ごとに取り直したので集計から外す"),
]

CODEBOOK = [
 ("id","作品の一意識別子。JLIT001–064。以後この ID で参照する",""),
 ("file_v1","v1 コーパスにおけるファイル名（対応確認用）",""),
 ("author_ja / author_reading / author_sex / author_birth / author_death","著者情報。sex は F / M",""),
 ("title_aozora","青空文庫の作品名欄どおりの表記。v1 の通称ではなくこちらを正とする",""),
 ("aozora_person_id / aozora_work_id / aozora_card_url","青空文庫の人物 ID・作品 ID・図書カード URL。再取得と検証のキー",""),
 ("year_first / year_first_end","初出の開始年と終了年。単発なら同値",""),
 ("year_source","card＝青空文庫図書カードの記載どおり／editor＝カードに記載がなく編者が補った。editor 行は要確認","card / editor"),
 ("period","初出年の文学史的区分（6区分）","1_明治前期 / 2_明治中期 / 3_明治後期 / 4_大正 / 5_昭和戦前 / 6_昭和戦後"),
 ("ndc / ndc_label","NDC 番号とその日本語ラベル。K 接頭辞は児童書","913 / K913 / 914 / 915 / 289 / 370 / 180"),
 ("genre_main","第1層ジャンル","Fiction / Nonfiction"),
 ("genre_sub","第2層ジャンル（統制語彙）","Modern, Historical, Detective, Ghost-Story, SF, Juvenile-SF, Juvenile-Mystery, Proletarian, Naturalist, Bildungsroman, Melodrama, Aesthetic, Experimental, Detective-Edo, Historical-Fantasy, Historical-Biography, Autobiography, Autobiographical-Diary, Diary, Criticism, Sketch-Essay, Enlightenment-Didactic, Religious-Didactic"),
 ("form","テクストの形態","novel / novella / short-story / collection / cycle / essay / treatise / biography / autobiography / diary / diary-novel"),
 ("audience","想定読者","general / juvenile"),
 ("register_level","文化的位置づけ。v1 の brow を置き換える","canonical / middlebrow / popular / documentary"),
 ("narration","語りの視点","first（一人称）/ third（三人称）/ mixed（作品内で交替・枠物語）/ dialogue（対話体）/ none（非物語）"),
 ("first_medium","初出媒体","newspaper / magazine / book"),
 ("kana_orthography","青空文庫の文字遣い種別","新字新仮名 / 新字旧仮名 / 旧字旧仮名"),
 ("completeness","テクストの完全性","complete / incomplete（原作が未完）/ part（分冊の一冊）/ PARTIAL（一部のみ収録）/ revised（改稿版）/ superseded（v1 の合本。分冊で取り直したので集計から外す）/ DUPLICATE（本文の取り違え）"),
 ("style_class","文語／口語の経験的3分類。bungo_ratio から自動判定（0.60以上=A / 0.20以上または文語40以上=B / 他=C）","A_文語体 / B_過渡的文体 / C_口語体"),
 ("bungo_ratio","文語標識が文語・口語標識全体に占める割合。0（純口語）〜1（純文語）","style_class の判定根拠。閾値を変えるならこの列で確かめる"),
 ("tokens / types / ttr_x1000","v1 の分かち書きテクストにおける延べ語数・異なり語数・TTR×1000。※再構築後に更新すること",""),
 ("kanji_ratio / katakana_ratio","非空白文字に占める漢字・片仮名の比率",""),
 ("bungo_per10k / kogo_per10k","文語助動詞標識・口語助動詞標識の頻度。**1万語あたり**（10000×該当語数÷総語数）であって per million ではない。複合形（である＝で＋ある）も連結して数える",""),
 ("p1_per10k / p3_per10k","一人称代名詞・三人称代名詞の**1万語あたり**頻度（narration の裏付け）",""),
 ("gaiji_lost","本文に残存する ※ の数＝失われた外字の数。0 でないものは要再構築",""),
 ("kana_odoriji / kanji_odoriji / ditto_mark","仮名踊り字（ゝゞヽヾ）・漢字踊り字（々）・同上記号（〃）の数",""),
 ("latin_chars / arabic_digits","ラテン文字数・算用数字数。表記正規化の対象量",""),
 ("note","作品固有の注記。【重大】【修正】で始まるものは対応必須",""),
]


def read_csv(path):
    with open(path, encoding='utf-8-sig') as fh:
        return list(csv.DictReader(fh))


def style_header(ws, ncol, row=1):
    for c in range(1, ncol + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = H_FILL
        cell.font = H_FONT
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    ws.row_dimensions[row].height = 30
    ws.freeze_panes = ws.cell(row=row + 1, column=1)


def autosize(ws, maxw=48, minw=8):
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        width = max((len(str(c.value)) if c.value is not None else 0) for c in col[:200])
        ws.column_dimensions[letter].width = max(minw, min(maxw, width * 1.15 + 2))


# 数値に見えても文字列のまま保たねばならない列（先頭ゼロ・分類記号など）
TEXT_COLS = {'aozora_person_id', 'aozora_work_id', 'ndc', 'ndc_expect', 'id'}


def coerce(v, header=None):
    """CSV 由来の文字列を，数値として書ける場合は数値に直す（SUMIF が効くように）。"""
    if header in TEXT_COLS:
        return v
    if not isinstance(v, str) or v == '':
        return v
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def write_table(ws, rows, headers=None, name=None):
    headers = headers or list(rows[0].keys())
    ws.append(headers)
    for r in rows:
        ws.append([coerce(r.get(h, ""), h) for h in headers])
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = BODY
            c.alignment = Alignment(vertical='top', wrap_text=False)
    style_header(ws, len(headers))
    if name:
        ref = f"A1:{get_column_letter(len(headers))}{len(rows) + 1}"
        t = Table(displayName=name, ref=ref)
        t.tableStyleInfo = TableStyleInfo(name="TableStyleLight9", showRowStripes=True)
        ws.add_table(t)
    return headers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--meta', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    meta = read_csv(os.path.join(args.meta, 'corpus_metadata_v2.csv'))
    cand = read_csv(os.path.join(args.meta, 'expansion_candidates.csv'))
    diag = read_csv(os.path.join(args.meta, 'diagnostics_v1.csv'))

    wb = Workbook()

    # ---- 凡例 -------------------------------------------------------------
    ws = wb.active
    ws.title = "凡例"
    lines = [
        ("近代日本文学コーパス（JLit）メタデータ v2", True),
        ("", False),
        (f"作成日: 2026-09-20 ／ 対象: DH_text_analytics_2025/corpus の全64ファイル", False),
        ("", False),
        ("■ このブックの目的", True),
        ("v1（コーパスdescription.xlsx）の year・ndc・genre・comments・brow の5列を，典拠にもとづいて全面的に", False),
        ("作り直したもの。あわせて，テクスト本体の診断で見つかった問題を記録する。", False),
        ("", False),
        ("■ 最重要の3点", True),
        ("(1) 乱歩_灰色の巨人.txt の本文は『魔法博士』と同一である。8-gram 一致率45.3%，冒頭は完全一致，", False),
        ("    章題も同一（動く映画館／悪魔の国／人造人間／黄金怪人／…）。『灰色の巨人』は事実上未収録であり，", False),
        ("    実効サンプル数は 64 ではなく 63 である。", False),
        ("(2) 本文中に ※ が計592箇所残存している。これは青空文庫の外字注記 ※［＃…］ から［＃…］だけを", False),
        ("    削除した結果であり，文字そのものが失われている（例：不如帰「合※の式」← 合巹の式）。", False),
        ("    最多は鴎外『伊沢蘭軒』166箇所，海野十三『敗戦日記』110箇所，宮本百合子『道標』86箇所。", False),
        ("(3) 海野十三_敗戦日記.txt には青空文庫の奥付・入力者注・外字一覧が本文末尾に残っている。", False),
        ("    「入力：青空文庫／校正：伊藤時也／ファイル作成：野口英司」以下が語数に算入されている。", False),
        ("", False),
        ("■ シートの読み方", True),
        ("metadata_v2          : 1作品1行。year_source 列が editor の19行は，青空文庫カードに初出欄がなく", False),
        ("                       編者が補った値であり，確認を要する。", False),
        ("changes_from_v1      : v1 のどの値をなぜ変えたか。典拠列に青空文庫の図書カード番号を記す。", False),
        ("diagnostics          : テクスト実測値。gaiji_lost > 0 の行は再構築が必要。", False),
        ("representativeness   : 代表性の集計。COUNTIFS で metadata_v2 を参照するため，metadata_v2 を", False),
        ("                       編集すれば自動で更新される。", False),
        ("expansion_candidates : 増補候補41件。priority 1=明治前期，2=女性作家，3=ジャンル，4=作家平準化，5=欠落補完。", False),
        ("codebook             : 列定義と統制語彙の一覧。", False),
        ("", False),
        ("■ 注意", True),
        ("tokens / types / ttr 等の実測値は，v1 の分かち書きテクスト（外字欠落・注記混入を含む）に基づく。", False),
        ("再構築後は scripts/00_build_metadata_v2.py を再実行して更新すること。", False),
    ]
    for i, (text, bold) in enumerate(lines, 1):
        c = ws.cell(row=i, column=1, value=text)
        c.font = Font(name=FONT, size=11, bold=bold)
    ws.column_dimensions['A'].width = 115

    # ---- metadata_v2 ------------------------------------------------------
    ws = wb.create_sheet("metadata_v2")
    headers = write_table(ws, meta, name="MetaV2")
    ci = {h: i + 1 for i, h in enumerate(headers)}
    for r in range(2, len(meta) + 2):
        comp = ws.cell(row=r, column=ci['completeness']).value
        if comp in ('DUPLICATE', 'PARTIAL'):
            for c in range(1, len(headers) + 1):
                ws.cell(row=r, column=c).fill = BAD
        elif ws.cell(row=r, column=ci['year_source']).value == 'editor':
            ws.cell(row=r, column=ci['year_first']).fill = WARN
            ws.cell(row=r, column=ci['year_source']).fill = WARN
        try:
            if int(ws.cell(row=r, column=ci['gaiji_lost']).value or 0) > 0:
                ws.cell(row=r, column=ci['gaiji_lost']).fill = WARN
        except (TypeError, ValueError):
            pass
    autosize(ws)
    ws.column_dimensions[get_column_letter(ci['note'])].width = 60
    ws.column_dimensions[get_column_letter(ci['aozora_card_url'])].width = 46

    # ---- changes_from_v1 --------------------------------------------------
    ws = wb.create_sheet("changes_from_v1")
    rows = [dict(zip(("id", "列", "v1の値", "v2の値", "変更の種類", "典拠"), c)) for c in CHANGES]
    write_table(ws, rows, name="Changes")
    autosize(ws)
    for col, w in (('C', 34), ('D', 40), ('E', 26), ('F', 62)):
        ws.column_dimensions[col].width = w
    for r in range(2, len(rows) + 2):
        for c in range(1, 7):
            ws.cell(row=r, column=c).alignment = Alignment(vertical='top', wrap_text=True)
        if str(ws.cell(row=r, column=5).value).startswith('【重大】'):
            for c in range(1, 7):
                ws.cell(row=r, column=c).fill = BAD

    # ---- diagnostics ------------------------------------------------------
    ws = wb.create_sheet("diagnostics")
    write_table(ws, diag, name="Diag")
    autosize(ws)

    # ---- representativeness ----------------------------------------------
    ws = wb.create_sheet("representativeness")
    n = len(meta)
    last = n + 1
    def col_of(h):
        return get_column_letter(headers.index(h) + 1)

    blocks = [
        ("時代区分（period）", 'period',
         ["1_明治前期(〜1886)", "2_明治中期(1887-1899)", "3_明治後期(1900-1911)",
          "4_大正(1912-1925)", "5_昭和戦前(1926-1944)", "6_昭和戦後(1945-)"],
         "1930年以前の明治期3区分で12点＝全体の19%にすぎない。昭和戦前が26点＝41%を占める。"),
        ("文体（style_class）", 'style_class', ["A_文語体", "B_過渡的文体", "C_口語体"],
         "文語体が1点（『学問のすすめ』）のみ。言文一致以前／過渡期の実態を記述できる水準にない。"),
        ("仮名遣い（kana_orthography）", 'kana_orthography', ["新字新仮名", "新字旧仮名", "旧字旧仮名"],
         "旧字旧仮名が0点。青空文庫は同一作品に新字／旧字の両版を持つことがあり，選択を統一すべき。"),
        ("NDC", 'ndc', ["913", "K913", "914", "915", "289", "370", "180"],
         "913 が51点＝80%。随筆・評論(914)は2点，戯曲(912)・韻文(911)はゼロ。"),
        ("ジャンル第1層（genre_main）", 'genre_main', ["Fiction", "Nonfiction"], ""),
        ("読者層（audience）", 'audience', ["general", "juvenile"], "児童書7点はすべて乱歩・海野十三に由来する。"),
        ("文化的位置（register_level）", 'register_level',
         ["canonical", "middlebrow", "popular", "documentary"], ""),
        ("語りの視点（narration）", 'narration', ["first", "third", "mixed", "dialogue", "none"],
         "一人称28点／三人称30点でほぼ均衡しており，この軸に限れば偏りは小さい。"),
        ("著者の性別（author_sex）", 'author_sex', ["F", "M"],
         "女性作家は4名・11点（作品ベースで17%）。うち4点は宮本百合子1名に集中する。"),
        ("初出媒体（first_medium）", 'first_medium', ["newspaper", "magazine", "book"], ""),
        ("完全性（completeness）", 'completeness',
         ["complete", "incomplete", "PARTIAL", "revised", "DUPLICATE"], ""),
    ]

    r = 1
    ws.cell(row=r, column=1, value="代表性の集計（metadata_v2 を COUNTIFS で参照。metadata_v2 を編集すると自動更新される）")\
      .font = Font(name=FONT, bold=True, size=12)
    r += 2
    for title, field, cats, comment in blocks:
        ws.cell(row=r, column=1, value=title).font = Font(name=FONT, bold=True, size=11)
        r += 1
        for h, lab in (("A", "区分"), ("B", "作品数"), ("C", "構成比"), ("D", "延べ語数"), ("E", "語数構成比")):
            c = ws.cell(row=r, column="ABCDE".index(h) + 1, value=lab)
            c.fill = H_FILL
            c.font = H_FONT
        r += 1
        start = r
        cl = col_of(field)
        tk = col_of('tokens')
        for cat in cats:
            ws.cell(row=r, column=1, value=cat).font = BODY
            ws.cell(row=r, column=2,
                    value=f'=COUNTIF(metadata_v2!${cl}$2:${cl}${last},A{r})').font = BODY
            ws.cell(row=r, column=3,
                    value=f'=IF($B${start + len(cats)}=0,0,B{r}/$B${start + len(cats)})').font = BODY
            ws.cell(row=r, column=3).number_format = '0.0%'
            ws.cell(row=r, column=4,
                    value=f'=SUMIF(metadata_v2!${cl}$2:${cl}${last},A{r},metadata_v2!${tk}$2:${tk}${last})').font = BODY
            ws.cell(row=r, column=4).number_format = '#,##0'
            ws.cell(row=r, column=5,
                    value=f'=IF($D${start + len(cats)}=0,0,D{r}/$D${start + len(cats)})').font = BODY
            ws.cell(row=r, column=5).number_format = '0.0%'
            r += 1
        ws.cell(row=r, column=1, value="合計").font = Font(name=FONT, bold=True, size=10)
        ws.cell(row=r, column=2, value=f'=SUM(B{start}:B{r - 1})').font = Font(name=FONT, bold=True, size=10)
        ws.cell(row=r, column=4, value=f'=SUM(D{start}:D{r - 1})').font = Font(name=FONT, bold=True, size=10)
        ws.cell(row=r, column=4).number_format = '#,##0'
        if comment:
            ws.cell(row=r, column=7, value=comment).font = NOTE_FONT
        r += 2

    # 著者別
    ws.cell(row=r, column=1, value="著者別（作品数の多い順）").font = Font(name=FONT, bold=True, size=11)
    r += 1
    for h, lab in (("A", "著者"), ("B", "作品数"), ("C", "延べ語数")):
        c = ws.cell(row=r, column="ABC".index(h) + 1, value=lab)
        c.fill = H_FILL
        c.font = H_FONT
    r += 1
    au_col = col_of('author_ja')
    tk = col_of('tokens')
    seen, order = set(), []
    for m in meta:
        if m['author_ja'] not in seen:
            seen.add(m['author_ja'])
            order.append(m['author_ja'])
    counts = {a: sum(1 for m in meta if m['author_ja'] == a) for a in order}
    for a in sorted(order, key=lambda x: (-counts[x], x)):
        ws.cell(row=r, column=1, value=a).font = BODY
        ws.cell(row=r, column=2,
                value=f'=COUNTIF(metadata_v2!${au_col}$2:${au_col}${last},A{r})').font = BODY
        ws.cell(row=r, column=3,
                value=f'=SUMIF(metadata_v2!${au_col}$2:${au_col}${last},A{r},metadata_v2!${tk}$2:${tk}${last})').font = BODY
        ws.cell(row=r, column=3).number_format = '#,##0'
        r += 1
    for col, w in (('A', 26), ('B', 10), ('C', 12), ('D', 14), ('E', 12), ('F', 3), ('G', 90)):
        ws.column_dimensions[col].width = w

    # ---- expansion_candidates --------------------------------------------
    ws = wb.create_sheet("expansion_candidates")
    write_table(ws, cand, name="Candidates")
    autosize(ws)
    ws.column_dimensions[get_column_letter(len(cand[0]))].width = 70
    for row in ws.iter_rows(min_row=2, min_col=len(cand[0]), max_col=len(cand[0])):
        for c in row:
            c.alignment = Alignment(vertical='top', wrap_text=True)

    # ---- codebook ---------------------------------------------------------
    ws = wb.create_sheet("codebook")
    rows = [dict(zip(("列名", "説明", "統制語彙"), c)) for c in CODEBOOK]
    write_table(ws, rows, name="Codebook")
    ws.column_dimensions['A'].width = 42
    ws.column_dimensions['B'].width = 70
    ws.column_dimensions['C'].width = 80
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical='top', wrap_text=True)

    wb.save(args.out)
    print("wrote", args.out)


if __name__ == '__main__':
    main()
