#!/bin/bash
# =============================================================================
# scripts/update.sh — 教材を最新にする（受講生用の「安全な git pull」）
#
#     cd ~/Documents/dh_project/JLit_Corpus_2026
#     bash scripts/update.sh            # 更新する
#     bash scripts/update.sh --dry-run  # 何が起きるかだけ表示
#
# 素の `git pull` は，配布ファイル（notebooks/*.ipynb など）を手元で実行・編集
# していると「Your local changes ... would be overwritten」で止まる。このスクリプトは
#   0. コースのリポジトリへの push を無効にする（受講生は教材に一切書き込まない）
#   1. 手元で変わった配布ファイルを my_work/_backup/<日時>/ に退避し，配布版に戻す
#   2. pull で新しく届くファイルと同名の「管理外ファイル」があれば，同じ所へ退避する
#   3. GitHub の最新版に進める（fast-forward のみ。履歴は書き換えない）
#   4. my_work/ を自分の GitHub のバックアップと揃え，copy_notebooks.py で
#      my_work/notebooks/ に新しいノートブックを揃える
# を順に行う。**何も消さない**（退避先に必ず残る）。
#
# 教員のマスター（Dropbox 内）では使わない。
#
# 注意（bash 3.2）：変数の直後に日本語を続けるときは必ず ${VAR} と書く。
# =============================================================================
set -u

DRY=0
case "${1:-}" in
  --dry-run|-n) DRY=1 ;;
  "") ;;
  -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
  *) echo "[ERR ] 不明な引数: ${1}（--dry-run のみ）"; exit 2 ;;
esac

cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd -P)"

case "${ROOT}" in
  *CloudStorage/Dropbox*|*/Dropbox/*)
    echo "[ERR ] ここは Dropbox 内（教員のマスター）: ${ROOT}"
    echo "       update.sh は受講生の clone（~/Documents/dh_project/JLit_Corpus_2026）で使う"
    exit 1 ;;
esac
command -v git >/dev/null 2>&1 || { echo "[ERR ] git が無い（xcode-select --install）"; exit 1; }
[ -d .git ] || { echo "[ERR ] ${ROOT} は git の clone ではない"; exit 1; }

G() { git -c core.quotepath=off "$@"; }   # 日本語のファイル名をそのまま出す

echo "== 教材の更新: ${ROOT}"
if ! G fetch --quiet origin; then
  echo "[ERR ] GitHub に接続できない（ネットワークを確かめて再実行）"
  exit 1
fi
UP="$(G rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)"
[ -n "${UP}" ] || UP="origin/main"

BEHIND="$(G rev-list --count "HEAD..${UP}")"
AHEAD="$(G rev-list --count "${UP}..HEAD")"
if [ "${AHEAD}" != "0" ]; then
  echo "[WARN] 手元に GitHub に無いコミットが ${AHEAD} 件ある（この clone で commit した）"
fi

# 0. コースのリポジトリへは push しない。push 先を無効な名前にしておくと，
#    うっかり git push しても手元で止まり，GitHub には何も送られない。
if [ "$(G remote get-url --push origin 2>/dev/null)" != "DISABLED" ]; then
  if [ "${DRY}" = "1" ]; then
    echo "-- (--dry-run) コースのリポジトリへの push を無効にする予定"
  else
    G remote set-url --push origin DISABLED
    echo "-- コースのリポジトリへの push を無効にした（自分の作業は my_work/ から自分の GitHub へ）"
  fi
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
BK="my_work/_backup/${STAMP}"
NBK=0

backup() {   # $1 = 退避するファイル（リポジトリからの相対パス），$2 = mv|cp
  local f="$1" how="$2"
  NBK=$((NBK + 1))
  if [ "${DRY}" = "1" ]; then
    echo "  退避予定: ${f}"
    return
  fi
  mkdir -p "${BK}/$(dirname "${f}")"
  if [ "${how}" = "mv" ]; then mv "${f}" "${BK}/${f}"; else cp -p "${f}" "${BK}/${f}"; fi
  echo "  退避: ${f}  →  ${BK}/${f}"
}

# 1. 手元で変わった配布ファイル（作業ツリー・ステージの両方）
CHANGED="$( { G diff --name-only; G diff --name-only --cached; } | sort -u )"
if [ -n "${CHANGED}" ]; then
  echo "-- 手元で変更された配布ファイル（退避してから配布版に戻す）"
  while IFS= read -r f; do
    [ -n "${f}" ] || continue
    if [ -e "${f}" ]; then backup "${f}" cp; fi
    if [ "${DRY}" = "0" ]; then
      G checkout HEAD -- "${f}" 2>/dev/null || G reset -q -- "${f}"
    fi
  done <<EOF
${CHANGED}
EOF
fi

# 2. これから届くファイルと同名の管理外ファイル（pull を止める原因になる）
if [ "${BEHIND}" != "0" ]; then
  INCOMING="$(G diff --name-only --diff-filter=A HEAD "${UP}")"
  FIRST=1
  while IFS= read -r f; do
    [ -n "${f}" ] || continue
    if [ -e "${f}" ] && ! G ls-files --error-unmatch -- "${f}" >/dev/null 2>&1; then
      if [ "${FIRST}" = "1" ]; then
        echo "-- 新しく配られるファイルと同名の手元ファイル（退避する）"; FIRST=0
      fi
      backup "${f}" mv
    fi
  done <<EOF
${INCOMING}
EOF
fi

# 3. 最新版へ進める
if [ "${DRY}" = "1" ]; then
  echo "-- (--dry-run) 更新は ${BEHIND} コミット分。退避予定 ${NBK} 件。何も変更していない"
  exit 0
fi
if [ "${BEHIND}" = "0" ]; then
  echo "-- すでに最新（${UP}）"
else
  if G merge --ff-only --quiet "${UP}"; then
    echo "-- 更新した（${BEHIND} コミット分）: $(G log -1 --format='%h %s')"
  else
    echo "[ERR ] 自動では進められない（この clone に独自のコミットがあり，GitHub と分岐している）"
    echo "       git log --oneline -5 の出力を添えて教員に相談すること"
    exit 1
  fi
fi

# 3b. 自分のバックアップ（my_work/）を GitHub から取ってくる（別のマシンで作業した分）
if [ -d my_work/.git ] && git -C my_work remote get-url origin >/dev/null 2>&1; then
  if git -C my_work diff --quiet && git -C my_work diff --cached --quiet; then
    if git -C my_work pull -q --ff-only 2>/dev/null; then
      echo "-- my_work/ を自分の GitHub のバックアップと揃えた"
    else
      echo "[warn] my_work/ をバックアップと揃えられなかった（ネットワーク・認証，または別のマシンの変更と食い違い）"
      echo "       cd my_work && git pull で理由を見る"
    fi
  else
    echo "[warn] my_work/ にバックアップしていない変更があるので，バックアップからの取り込みは飛ばした"
  fi
fi

# 4. 自分用ノートブックを揃える
PY=""
for c in "${ROOT}/../.venv/bin/python" "${ROOT}/../.venv/Scripts/python.exe" python3 python; do
  if command -v "${c}" >/dev/null 2>&1; then PY="${c}"; break; fi
done
if [ -n "${PY}" ]; then
  echo "-- my_work/notebooks/ を揃える"
  "${PY}" scripts/copy_notebooks.py
else
  echo "[WARN] python が見つからない。あとで python scripts/copy_notebooks.py を実行すること"
fi

if [ "${NBK}" != "0" ]; then
  echo "[NOTE] ${NBK} 件を ${BK}/ に退避した（消していない）。必要なら中身を確かめること"
fi
# 5. 自分のバックアップ（my_work/）の状態を知らせる
if [ -d my_work/.git ]; then
  NCH="$(git -C my_work status --porcelain | wc -l | tr -d ' ')"
  if [ "${NCH}" != "0" ]; then
    echo "[NOTE] my_work/ に，まだバックアップしていない変更が ${NCH} 件ある。作業の終わりに："
    echo "         cd my_work && git add -A && git commit -m \"Step N の作業\" && git push"
  fi
else
  echo "[NOTE] my_work/ のバックアップがまだ無い → bash scripts/setup_my_work.sh <GitHubのユーザ名>（手順書 §5）"
fi
echo "[ OK ] 完了。Jupyter では my_work/notebooks/ のノートブックを開くこと"
