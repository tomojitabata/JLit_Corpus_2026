# JLit — 近代日本文学コーパスの再構築と計量分析

青空文庫由来の近代日本文学コーパス（1872–1959）を，典拠のあるメタデータと
再現可能な前処理のうえに作り直し，word2vec・doc2vec・MALLET で
言語変化・文体変化・主題の多様化を記述するためのリポジトリ。
全8ステップの講義教材を兼ねる。

**作成**: 2026-09-20 ／ **対象**: `DH_text_analytics_2025/corpus` の64ファイル

---

## まず読むもの

| 目的 | ファイル |
|---|---|
| 現行コーパスの何が問題か | [`docs/representativeness_report.md`](docs/representativeness_report.md) |
| 修正済みメタデータ | [`metadata/corpus_metadata_v2.xlsx`](metadata/corpus_metadata_v2.xlsx) |
| 受講生の環境構築（Step 1） | [`docs/00_setup_students.md`](docs/00_setup_students.md) |
| 講義計画（教員用） | [`docs/syllabus_8_lectures.md`](docs/syllabus_8_lectures.md) |
| 符号化の決定事項 | [`docs/encoding_guidelines.md`](docs/encoding_guidelines.md) |

---

## 診断の要点（v1 コーパス）

テクスト本体と書誌の全数検査で，次が判明した。

### 致命的な3点

| # | 問題 | 規模 |
|---|---|---|
| 1 | `乱歩_灰色の巨人.txt` の本文が『魔法博士』と同一（8-gram 一致率45.3%，冒頭完全一致，章題も同一） | 実効標本は 64 → **63** |
| 2 | 外字が `※` のまま失われている（`※［＃…］` から括弧部分だけ削った結果） | **592箇所 / 22ファイル** |
| 3 | `海野十三_敗戦日記.txt` に青空文庫の奥付・入力者注・外字一覧が残存 | 約300語 |

加えて `藤村_家.txt` は下巻のみで作品として不完全，
`林芙美子_放浪記初出.txt` には校訂注（`「裏」はママ］`）が漏れている。

### メタデータの誤り（主なもの）

| 作品 | v1 | v2 | 典拠 |
|---|---|---|---|
| 小栗虫太郎『潜航艇「鷹の城」』 | 1977 | **1935** | 青空文庫 card43656（初出「新青年」1935年4〜5月号）。1977は底本刊年 |
| 海野十三『敗戦日記』 | 1971 | **1944–45** | 1971は講談社版の刊年 |
| 宮本百合子『伸子』 | 1927 | **1924–26** | card2015（「改造」1924年9月〜1926年9月） |
| 岡本かの子『仏教人生読本』 | 「自己啓発？」 | **NDC 180** | card52342 |
| 森鴎外『伊沢蘭軒』ほか | "Histrical novel" | Nonfiction / Historical-Biography | 誤綴の訂正と再分類 |

`ndc` 列は NDC 番号ではなくラベル文字列（「小説、物語」）だった。
`genre` は英語・日本語・別題・誤綴の混在。`brow` は high/low の二値で，
児童書とノンフィクションが押し込まれていた。
全変更は Excel の `changes_from_v1` シートに典拠つきで記録してある。

### 代表性

| 軸 | 実態 |
|---|---|
| 時代 | 昭和戦前が26点・42.3%。1899年以前は3点・4.6% |
| 文体 | **文語体が1点のみ**（『学問のすすめ』）。言文一致以前を代表できない |
| ジャンル | 小説（913+K913）が90.6%。**戯曲・韻文はゼロ** |
| 性別 | 女性作家4名・11点（17%）。**1926年以前の女性作家はゼロ** |
| 作家 | 上位5名で39%。1点のみの作家が4名。作家効果と時代効果が分離できない |
| 語り | 一人称28点・三人称30点で**ほぼ均衡**（この軸の偏りは小さい） |

---

## ディレクトリ

```
JLit_Corpus_2026/
├── README.md
├── requirements.txt
├── config/
│   ├── pipeline.yaml            全工程の設定。これが「何をどう数えたか」の記録
│   ├── corpus_manifest.tsv      取得する作品（現行64点＋増補候補39点）
│   └── stopwords_ja.txt         292語。書誌由来の語（底本・入力・校正…）を含む
├── docs/
│   ├── 00_setup_students.md     受講生の環境構築（macOS 27 / Windows 11）
│   ├── syllabus_8_lectures.md   講義計画（教員用）
│   ├── representativeness_report.md   代表性診断レポート
│   └── encoding_guidelines.md   符号化と正規化の決定事項
├── metadata/
│   ├── corpus_metadata_v2.csv   64作品 × 43列
│   ├── corpus_metadata_v2.xlsx  7シート（凡例／メタデータ／変更点／診断／代表性／増補候補／コードブック）
│   ├── expansion_candidates.csv 増補候補41件（優先度つき）
│   └── diagnostics_v1.csv       v1 全64ファイルの実測値
├── notebooks/                   全8ステップの講義ノートブック
├── scripts/
│   ├── 00_bootstrap_mac.sh      macOS セットアップ（共用 iMac／自分の Mac）
│   ├── 00_env_check.py          環境チェック
│   ├── 00_build_metadata_v2.py  メタデータ生成（v1 の64点）
│   ├── 00_extend_metadata.py    増補分まで広げて v3 を作る
│   ├── 01_build_workbook.py     Excel ブック生成
│   ├── 02_fetch_aozora.py       青空文庫の索引と XHTML の取得
│   ├── 03_aozora2xml.py         XHTML → TEI 風 XML（外字復元・ルビ保持・会話標示）
│   ├── 04_normalise.py          XML → 解析用テクスト（踊り字展開・4ストリーム）
│   ├── 05_tokenise_unidic.py    UniDic 短単位解析
│   ├── 06_build_datasets.py     チャンク分割・語彙統計
│   ├── 07_descriptive_stats.py  MFW・Delta・PCA・特徴語
│   ├── 08_word2vec_diachronic.py  通時埋め込み＋Procrustes 整列
│   ├── 09_doc2vec.py            作品埋め込み・交絡の分離
│   ├── 10_mallet.py             MALLET LDA
│   ├── 11_visualise.py          作図
│   ├── 15_kwic_index.py         KWIC の索引づくり（TSV → data/kwic）
│   ├── 16_kwic_server.py        KWIC コンコーダンサ（localhost の画面・書き出し）
│   ├── kwic_core.py             KWIC の中身（索引と検索。ノートブックからも使える）
│   ├── kwic_app.html            KWIC の画面（外部資源を使わない1枚）
│   ├── 99_validate.py           健全性検査（重複・外字・奥付・踊り字・メタデータ）
│   ├── make_notebooks.py        ノートブック生成
│   ├── find_unclosed_quotes.py  閉じ括弧のない「の検出
│   └── lib/aozora.py            外字復元・踊り字展開の中核
├── data/                        生成物（git 管理外）
├── results/                     分析結果（受講生ごとのサブフォルダ）
└── logs/                        実行記録
```

---

## 使い方

### 0. 環境

```bash
# macOS（DH Lab 共用 iMac）
bash scripts/00_bootstrap_mac.sh
source /Users/Shared/jlit/env.sh && source .venv/bin/activate

# macOS（自分の Mac）
bash scripts/00_bootstrap_mac.sh --personal
source ~/.jlit/env.sh && source .venv/bin/activate

# Windows
py -3.12 -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# 辞書は本番の unidic-novel-v202512 を取る（docs/00_setup_students.md §4.3）。
# pip install unidic で入るのは cwj であって本番の辞書ではない。

python scripts/00_env_check.py          # ALL OK を確認
```

`00_bootstrap_mac.sh` は **sudo を一度も使わない**（uv・Temurin の tar.gz・
MALLET の tar.gz を展開するだけ）。共用 iMac ではホームがマシンをまたがないため，
JDK・MALLET・UniDic・取得済みテクストを `/Users/Shared/jlit` に置き，
同じ機体なら次回も別のユーザでも使い回せるようにしている。

**解析辞書は `unidic-novel`（近現代口語小説UniDic v202512）に決めてある**
（2026-09-22。4 辞書を 111 点で比較。未知語率 0.17%＝cwj の 3.9 分の 1）。
判断の経緯と記録の場所は
[`docs/dictionary_comparison.md`](docs/dictionary_comparison.md) §10。

詳細は [`docs/00_setup_students.md`](docs/00_setup_students.md)。

### 1. 現行コーパスの診断

```bash
python scripts/99_validate.py \
  --corpus <v1 の corpus ディレクトリ> \
  --meta metadata/corpus_metadata_v2.csv \
  --out logs/validation_v1.csv
```

現時点では FATAL 16 件 / WARN 18 件が出る。これが再構築の対象である。

### 2. 再構築

```bash
# 書誌索引（約20MB）
python scripts/02_fetch_aozora.py index --out data/aozora

# 解決状況の確認（未解決行はマニフェストを直す）
python scripts/02_fetch_aozora.py resolve \
  --manifest config/corpus_manifest.tsv --out data/aozora

# XHTML の取得（1秒/件。104件で約2分）
python scripts/02_fetch_aozora.py works \
  --manifest config/corpus_manifest.tsv --out data/aozora

# XML 化（外字を面区点から復元，ルビ保持，会話を <said> に）
python scripts/03_aozora2xml.py \
  --in data/aozora/xhtml --log data/aozora/fetch_log.csv --out data/xml

# 正規化（踊り字展開，3ストリーム生成）
python scripts/04_normalise.py \
  --in data/xml --out data/plain --config config/pipeline.yaml

# 形態素解析（UniDic 短単位・語彙素。辞書は unidic-novel v202512）
python scripts/05_tokenise_unidic.py \
  --in data/plain/full --out data/tokens --lemma-policy mixed \
  --expect-dict unidic-novel

# データセット構築（2000語チャンク・作品あたり上限40）
python scripts/06_build_datasets.py \
  --tokens data/tokens/tokens_content \
  --meta metadata/corpus_metadata_v2.csv \
  --out data/datasets --chunk 2000 --max-chunks 40

# 再検証：FATAL 0 になるまで進まない
python scripts/99_validate.py --corpus data/plain/full \
  --meta metadata/corpus_metadata_v2.csv \
  --tokenise-report data/tokens/tokenise_report.csv
```

### 3. 分析

```bash
# 記述統計・文体計量
python scripts/07_descriptive_stats.py \
  --tokens data/tokens/tokens_lemma --tsv data/tokens/tsv \
  --meta metadata/corpus_metadata_v2.csv --out results/descriptive --mfw 300

# 通時 word2vec（スライスの語数を揃える）
python scripts/08_word2vec_diachronic.py \
  --chunks data/datasets/chunks --index data/datasets/chunks_index.csv \
  --out results/w2v --slice period --balance

# doc2vec（作家効果と時代効果の比較）
python scripts/09_doc2vec.py \
  --chunks data/datasets/chunks --index data/datasets/chunks_index.csv \
  --out results/d2v --dm 0

# MALLET
export MALLET=/path/to/mallet/bin/mallet
python scripts/10_mallet.py all --datasets data/datasets \
  --out results/mallet --topics 50 --iterations 2000

# 作図
python scripts/11_visualise.py --meta metadata/corpus_metadata_v2.csv \
  --descriptive results/descriptive --w2v results/w2v \
  --mallet results/mallet --out results/figures
```

### 4. テクストに戻る（KWIC コンコーダンサ）

数えたあとに本文へ戻れなければ，数えたことに意味は無い。
**05 の解析結果（TSV）から索引を作り，ブラウザで読む。**

```bash
# 索引（05 を走らせ直したら作り直す。辞書が変われば切り方が変わる）
python scripts/15_kwic_index.py \
  --tsv data/tokens/tsv --meta metadata/corpus_metadata_v3.csv --out data/kwic

# 画面（127.0.0.1 にしか結び付けない。この機体からだけ見える）
python scripts/16_kwic_server.py --open

# 結果をそのまま配れる HTML／CSV に落とす（サーバは要らない）
python scripts/16_kwic_server.py --query 汽車 --stream lemma \
  --export results/student/kwic_汽車.html --csv results/student/kwic_汽車.csv
```

**語彙素と表層形を切り替えて**検索でき（`L:`／`S:` で項ごとに混ぜられる），
品詞（`/動詞`）・連なり（`汽車 に 乗る`，文境界は越えない）・ワイルドカード・
正規表現が使える。用例には**時代区分・著者・作品名**が付き，行を押すと
前後の本文へ広がる。詳細は `docs/glossary.md` の「KWIC コンコーダンサ」。

---

## 設計上の3つの約束

### 1. 削らない。分離する。

v1 の欠陥はすべて「正規表現で要らないものを削る」方針の副作用である。
削除は不可逆で記録も残らない。v2 では青空文庫 XHTML のすべての構成要素を
XML に写像し，解析用テクストはそこから決定的に生成する。

### 2. 典拠のない数値を置かない。

メタデータの各行には `year_source` 列があり，`card`（青空文庫図書カードの記載どおり）と
`editor`（カードに記載がなく編者が補った）を区別する。
現在 19 件が `editor` で，Excel では黄色で塗ってある。**要確認**。

### 3. 「変化を検出した」と言うには対照条件が要る。

- 時代ラベルをシャッフルした対照実験（Step 6）
- 作家をグループとする交差検証（Step 7）
- 乱数種を変えた安定性検査（Step 5）
- 児童書を除いても傾向が残るかの確認（Step 8）

いずれもノートブックに実装済みである。

---

## 次に手を付けるべきこと

1. **`乱歩_灰色の巨人.txt` の取り直し**（card56676）
2. **`藤村_家` の上巻追加**（card1509）
3. 外字592箇所の復元（`03_aozora2xml.py` が自動で行う）
4. `metadata/corpus_metadata_v2.xlsx` の黄色セル（`year_source=editor` の19件）の確認
5. 増補候補 priority 1–2（明治前期10点・女性作家10点）の追加

目標とする時代別語数は
[`docs/representativeness_report.md`](docs/representativeness_report.md) §8 の表を参照。

---

## 典拠

書誌情報はすべて青空文庫の図書カードおよび
「公開中 作家別作品一覧拡張版」CSV に拠る。各作品のカード URL は
`metadata/corpus_metadata_v2.csv` の `aozora_card_url` 列にある。
