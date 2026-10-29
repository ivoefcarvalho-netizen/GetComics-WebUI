#!/bin/bash
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")" && pwd)"
SOURCE="${1:-$HOME/getcomics-downloader}"
for file in getinfo.py download.py menu.py pixeldrain_support.py; do
  if [[ ! -f "$SOURCE/$file" ]]; then echo "Em falta: $SOURCE/$file" >&2; exit 1; fi
  cp "$SOURCE/$file" "$PROJECT/$file"
done
printf 'Módulos copiados da tua instalação.\n'
if grep -El '97055431-4efe-40f7-a339-fae646bf73d2' "$PROJECT"/*.py >/dev/null; then
  echo 'ATENÇÃO: foi encontrada uma chave API no código. NÃO publiques os ficheiros.' >&2
  exit 1
fi
printf 'Confirma que não existem outras chaves/segredos nos ficheiros antes de os publicares.\n'
