#!/bin/bash
# =============================================================================
# scripts/setup_my_work.sh — 自分の作業フォルダ my_work/ を用意し，
#                            自分の GitHub（private リポジトリ）に控えを取る
#
#     cd ~/Documents/dh_project/JLit_Corpus_2026
#     bash scripts/setup_my_work.sh <GitHubのユーザ名> [リポジトリ名]
#
# リポジトリ名を省くと jlit-work。**先に GitHub の画面で，同じ名前の空の
# private リポジトリを作っておくこと**（README などは付けない。手順書 §5.2）。
#
# このスクリプトがすること
#   1. コースのリポジトリへの push を無効にする（教材には一切書き込まない）
#   2. my_work/ を独立した git リポジトリにする。push 先は
#      https://github.com/<ユーザ名>/<リポジトリ名>.git。そこに別の機体で作った
#      控えがあれば，それを取ってくる（2台目以降の機体でも同じ1行でよい）
#   3. my_work/notebooks/ にノートブックを揃える
#   4. .gitignore と，50 MB を超えるファイルを止める見張りを置く
#   5. コミットして push する
#
# 何度実行してもよい（済んでいる手順は飛ばす）。
# 注意（bash 3.2）：変数の直後に日本語を続けるときは必ず ${VAR} と書く。
# =============================================================================
set -u

GHUSER="${1:-}"
REPO="${2:-jlit-work}"
if [ -z "${GHUSER}" ] || [ "${GHUSER}" = "-h" ] || [ "${GHUSER}" = "--help" ]; then
  sed -n '2,20p' "$0"; exit 2
fi
case "${GHUSER}" in
  *[!A-Za-z0-9-]*) echo "[ERR ] GitHub のユーザ名に使えない文字がある: ${GHUSER}"; exit 2 ;;
esac

cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd -P)"
case "${ROOT}" in
  *CloudStorage/Dropbox*|*/Dropbox/*)
    echo "[ERR ] ここは Dropbox 内（教員のマスター）: ${ROOT}"; exit 1 ;;
esac
command -v git >/dev/null 2>&1 || { echo "[ERR ] git が無い（xcode-select --install）"; exit 1; }
[ -d .git ] || { echo "[ERR ] ${ROOT} は git の clone ではない"; exit 1; }

# ---- 0. 名前とメールアドレス（コミットに記録される） ------------------------
if [ -z "$(git config --global user.name)" ] || [ -z "$(git config --global user.email)" ]; then
  echo "[ERR ] git に名前とメールアドレスが設定されていない。次の2行を実行してから，もう一度："
  echo '         git config --global user.name  "<氏名またはローマ字>"'
  echo '         git config --global user.email "<GitHub に登録したメールアドレス>"'
  exit 1
fi

# ---- 1. コースのリポジトリへの push を無効にする -----------------------------
if [ "$(git remote get-url --push origin 2>/dev/null)" != "DISABLED" ]; then
  git remote set-url --push origin DISABLED
  echo "-- コースのリポジトリへの push を無効にした"
fi

# ---- 2. my_work/ を git リポジトリにする（GitHub に控えがあれば取ってくる） ----
URL="https://github.com/${GHUSER}/${REPO}.git"
mkdir -p my_work
cd my_work || exit 1
if [ ! -d .git ]; then
  git init -q
  git symbolic-ref HEAD refs/heads/main
  git remote add origin "${URL}"
  echo "-- my_work/ を git リポジトリにした。GitHub の控えを確かめる（ユーザ名とトークンを聞かれることがある）"
  if git fetch -q origin 2>/dev/null && git rev-parse -q --verify origin/main >/dev/null; then
    # 別の機体で作った控えがある → それを取ってくる（こちらの同名ファイルは控えの版になる）
    git checkout -q -f -B main origin/main
    git branch -q --set-upstream-to=origin/main main
    echo "-- GitHub の控え（${URL}）を取ってきた"
  fi
fi
CUR="$(git config --get remote.origin.url)"
if [ -n "${CUR}" ]; then
  if [ "${CUR}" != "${URL}" ]; then
    echo "[warn] my_work/ の push 先はすでに ${CUR} になっている（変えていない）"
    URL="${CUR}"
  fi
else
  git remote add origin "${URL}"
fi
mkdir -p notebooks results
cd "${ROOT}" || exit 1

# ---- 3. ノートブックを揃える ---------------------------------------------------
# 試行版の置き場からの引っ越し（2026-09 の試行版を使った人だけ）
if [ -d my_notebooks ]; then
  for f in my_notebooks/* my_notebooks/.copied_from.json; do
    [ -e "${f}" ] || continue
    b="$(basename "${f}")"
    case "${b}" in _backup_*) continue ;; esac
    [ -e "my_work/notebooks/${b}" ] || mv "${f}" "my_work/notebooks/${b}"
  done
  echo "-- my_notebooks/ の中身を my_work/notebooks/ に移した（残ったものは my_notebooks/ にある）"
fi
PY=""
for c in "${ROOT}/../.venv/bin/python" "${ROOT}/../.venv/Scripts/python.exe" python3 python; do
  if command -v "${c}" >/dev/null 2>&1; then PY="${c}"; break; fi
done
[ -n "${PY}" ] && "${PY}" scripts/copy_notebooks.py | grep -v '^\[NOTE\]'

# ---- 4. 控えの設定（.gitignore・README・大きいファイルの見張り） ------------
cd my_work || exit 1
if [ ! -f .gitignore ]; then
  cat > .gitignore <<'EOF'
# 大きいもの・作り直せるものは控えに入れない（GitHub は 1 ファイル 100 MB まで）
*.model
*.npy
*.mallet
topic-state.gz
topic-word-weights.txt
_backup/
.ipynb_checkpoints/
.DS_Store
EOF
fi
if [ ! -f README.md ]; then
  cat > README.md <<EOF
# ${REPO}

JLit_Corpus_2026（テクスト分析論）の自分の作業の控え。

- \`notebooks/\` … 実行したノートブック（配布版のコピー）
- \`results/\`   … 図（SVG）・表・レポート原稿

教材そのものは https://github.com/tomojitabata/JLit_Corpus_2026 にある（ここには入れない）。
EOF
fi
# 50 MB を超えるファイルをコミットしようとしたら止める（GitHub の上限は 100 MB）
HOOK=.git/hooks/pre-commit
if [ ! -f "${HOOK}" ]; then
  cat > "${HOOK}" <<'EOF'
#!/bin/bash
# setup_my_work.sh が置いた見張り：50 MB を超えるファイルのコミットを止める
fail=0
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue
  s=$(wc -c < "$f" | tr -d ' ')
  if [ "$s" -gt 52428800 ]; then
    [ "$fail" = 0 ] && echo "[ERR ] 50 MB を超えるファイルは控えに入れない（GitHub の上限は 100 MB）:"
    echo "         $f ($((s / 1048576)) MB)"
    fail=1
  fi
done < <(git diff --cached --name-only --diff-filter=AM -z)
if [ "$fail" = 1 ]; then
  echo "       git restore --staged <ファイル> で外し，必要なら my_work/.gitignore に書き足す"
  exit 1
fi
EOF
  chmod +x "${HOOK}"
fi

git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "作業フォルダの控え（$(date +%Y-%m-%d)）" && echo "-- コミットした"
fi

# ---- 5. 自分の GitHub へ push する -------------------------------------------
echo "-- ${URL} へ push する（初回はユーザ名とトークンを聞かれる。§5.2）"
if git push -u origin main; then
  echo "[ OK ] 控えができた: ${URL}"
  echo "       作業の終わりに：cd my_work && git add -A && git commit -m \"Step N の作業\" && git push"
  echo "       別の機体では，教材を clone してから同じ1行：bash scripts/setup_my_work.sh ${GHUSER} ${REPO}"
else
  echo "[ERR ] push できなかった。よくある原因："
  echo "       1) GitHub に ${REPO} という空の private リポジトリを作っていない"
  echo "       2) パスワードの欄に GitHub のパスワードを入れた（トークンを入れる。§5.2）"
  echo "       3) ユーザ名の綴りが違う（${GHUSER}）"
  echo "       直したら，もう一度 bash scripts/setup_my_work.sh ${GHUSER} ${REPO}"
  exit 1
fi
