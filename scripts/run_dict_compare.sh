#!/usr/bin/env bash
# ============================================================================
# run_dict_compare.sh — 辞書比較をまとめて実行する
# ============================================================================
# なぜスクリプトにしたか
# ----------------------
# 辞書比較のコマンドは長く，行の継続（末尾の \）が何行も続く。これを
# 端末に貼ると失敗しやすい。
#
#   * 末尾が \\ になっていると，zsh は「バックスラッシュ1文字」という
#     引数として受け取る。**行はそこで終わる**ので，次の行が独立した
#     コマンドとして実行され  zsh: command not found: --plain  となる
#   * 行の後ろに # で注釈を書くと，対話的な zsh では注釈にならない
#     （interactive_comments が既定で無効になっている）
#
# どちらも「コマンドは正しいのに無関係なエラーが出る」種類の事故である。
# **貼らずに，このスクリプトを実行すること。**
#
# 使い方
# ------
#     bash scripts/run_dict_compare.sh              # 内的評価だけ（速い）
#     bash scripts/run_dict_compare.sh --full       # 外的評価も（重い）
#     bash scripts/run_dict_compare.sh --check      # 辞書の検査だけ
#
# 辞書の場所を変えるときは，下の DICTS を書き換える。
# ============================================================================
set -u

cd "$(dirname "$0")/.." || exit 1

# ---- 設定（ここだけ書き換える）--------------------------------------------
# 「名前=パス」を1行に1つ。名前は出力の列名になるので短くする。
# **版を揃えること。** pip の unidic（python -m unidic download で入るもの）と
# NINJAL 配布の v202512 を混ぜると，辞書の違いか版の違いか分からなくなる。
#
# 2026-09-22 の実行で **novel を本番の辞書に決定した**（§10）。
# このスクリプトは判定済みの比較を**再現・検算**するためのものである。
# 本番の解析そのものは 05_tokenise_unidic.py で行う（--out は data/tokens）。
# ここでの出力は results/dict_compare と data/dict_runs に分けて書かれ，
# **本番の data/tokens を上書きしない**。
DICTS=(
  "cwj=/Users/Shared/jlit/unidic-cwj-202512"
  "kindai=/Users/Shared/jlit/unidic-kindai-bungo-v202512"
  "novel=/Users/Shared/jlit/unidic-novel-v202512"
  "qkana=/Users/Shared/jlit/unidic-qkana-v202512"
)
PLAIN="data/plain/full"
# 自分で作った v3（_local）があればそちらを使う
META="metadata/corpus_metadata_v3.csv"
[ -f metadata/corpus_metadata_v3_local.csv ] && META="metadata/corpus_metadata_v3_local.csv"
OUT="results/dict_compare"
WORK="data/dict_runs"
TOPICS=40
# ---------------------------------------------------------------------------

MODE="${1:-}"

echo "== 辞書の確認 =="
MISSING=0
ARGS=()
for spec in "${DICTS[@]}"; do
  name="${spec%%=*}"
  path="${spec#*=}"
  if [ -d "$path" ]; then
    printf '  [ok  ] %-10s %s\n' "$name" "$path"
    ARGS+=(--dict "$spec")
  else
    printf '  [skip] %-10s %s （無い）\n' "$name" "$path"
    MISSING=$((MISSING + 1))
  fi
done

if [ "${#ARGS[@]}" -lt 4 ]; then
  echo
  echo "[FATAL] 使える辞書が 2 つ未満。比較できない。"
  echo "        展開できているかを確かめる:"
  echo "          python3 scripts/check_unidic_dir.py /Users/Shared/jlit/*"
  echo "        展開に失敗する場合は docs/dictionary_comparison.md §9 を見る。"
  exit 1
fi
if [ "$MISSING" -gt 0 ]; then
  echo
  echo "[info ] $MISSING 個の辞書を飛ばした。**報告にそのことを書くこと。**"
fi

echo
echo "== 辞書が本当に読めるかを検査 =="
for spec in "${DICTS[@]}"; do
  path="${spec#*=}"
  [ -d "$path" ] && python3 scripts/check_unidic_dir.py "$path" || true
done

if [ "$MODE" = "--check" ]; then
  echo
  echo "[ok  ] 検査のみで終了。"
  exit 0
fi

echo
echo "== 内的評価（12_dict_compare.py）=="
python3 scripts/12_dict_compare.py \
  --plain "$PLAIN" \
  "${ARGS[@]}" \
  --meta "$META" \
  --out "$OUT" || exit 1

if [ "$MODE" = "--full" ]; then
  echo
  echo "== 外的評価（13_pipeline_compare.py）=="
  echo "   辞書 1 つあたり 15–40 分かかる。途中で止めても --skip-existing で再開できる。"
  python3 scripts/13_pipeline_compare.py \
    --plain "$PLAIN" \
    "${ARGS[@]}" \
    --meta "$META" \
    --work "$WORK" \
    --out "$OUT" \
    --topics "$TOPICS" \
    --skip-existing || exit 1
else
  echo
  echo "[info ] 外的評価（Step 4・7・8 を辞書ごとに実行する）は --full で実行する:"
  echo "          bash scripts/run_dict_compare.sh --full"
fi

echo
echo "[ok  ] 出力 → $OUT"
echo "       判断の規則は docs/dictionary_comparison.md §4 を先に読むこと。"
echo "       dict_disagreements.csv は必ず目で見ること。"
