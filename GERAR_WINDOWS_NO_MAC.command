#!/bin/zsh
# Compilar Windows na nuvem a partir de um Mac. Usa uma branch separada: nao altera main.
set -euo pipefail
cd "$(dirname "$0")"
ORIGEM="$PWD"
REPO="ivoefcarvalho-netizen/GetComics-WebUI"
BRANCH="windows-portable"
if ! command -v git >/dev/null || ! command -v gh >/dev/null; then
  echo "Precisas de git e GitHub CLI (gh) instalados no Mac para este metodo."
  exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "Inicia primeiro sessao com: gh auth login"
  exit 1
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "A preparar uma branch Windows separada no GitHub…"
git clone --quiet "https://github.com/${REPO}.git" "$TMP/repo"
cd "$TMP/repo"
git fetch --quiet origin "$BRANCH" 2>/dev/null || true
if git show-ref --verify --quiet "refs/remotes/origin/${BRANCH}"; then
  git switch --quiet --track -c "$BRANCH" "origin/$BRANCH"
else
  git switch --quiet -c "$BRANCH"
fi
rsync -a --exclude='.env' --exclude='*.key' --exclude='pixeldrain.dat' \
  --exclude='.venv*' --exclude='__pycache__' --exclude='dist' --exclude='build' \
  "$ORIGEM/" "$TMP/repo/"
git add -A
if ! git diff --cached --quiet; then
  git commit -qm 'Preparar Windows portátil autónomo com configurações individuais'
else
  git commit -qm 'Nova compilação Windows' --allow-empty
fi
git push --quiet -u origin "$BRANCH"
SHA=$(git rev-parse HEAD)
echo "Código enviado para a branch $BRANCH, sem tocar na main."
echo "A procurar a compilação Windows deste commit…"
RUN=""
for i in {1..25}; do
  RUN=$(gh run list --repo "$REPO" --branch "$BRANCH" --limit 30 \
      --json databaseId,headSha,workflowName \
      --jq ".[] | select(.headSha == \"$SHA\" and .workflowName == \"GetComics Windows portátil\") | .databaseId" \
      | head -n 1 || true)
  if [[ -n "$RUN" ]]; then break; fi
  sleep 4
done
if [[ -z "$RUN" ]]; then
  echo "A execução não apareceu. Consulta https://github.com/${REPO}/actions"
  exit 1
fi
echo "Execução $RUN. A acompanhar o resultado…"
gh run watch "$RUN" --repo "$REPO" --exit-status
OUT="$HOME/Downloads/GetComics-Windows-Distribuicao"
mkdir -p "$OUT"
gh run download "$RUN" --repo "$REPO" -n GetComics-Windows-Portable --dir "$OUT"
echo "Concluído. O ZIP Windows está em: $OUT"
open "$OUT"
