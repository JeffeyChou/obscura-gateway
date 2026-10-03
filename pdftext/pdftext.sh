#!/bin/sh
# usage: pdftext <url> [first_page] [last_page(0=end)]
# stdout: text (form feed between pages); stderr: "PAGES n" or "ERROR ..."
set -u
url=$1; first=${2:-1}; last=${3:-0}
f=/tmp/doc.pdf
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
curl -fsSL --proto '=http,https' --proto-redir '=http,https' --max-redirs 8 \
     --connect-timeout 15 --max-time 90 --max-filesize 104857600 \
     -A "$UA" -H 'Accept: application/pdf,*/*;q=0.8' -o "$f" "$url"
rc=$?
if [ $rc -ne 0 ]; then
  case $rc in
    22) echo "ERROR download failed: HTTP error (blocked, 403/404, or not public)" >&2;;
    28) echo "ERROR download timed out" >&2;;
    63) echo "ERROR file larger than 100 MB" >&2;;
    6|7) echo "ERROR could not connect (host unreachable or blocked by egress policy)" >&2;;
    *) echo "ERROR download failed (curl exit $rc)" >&2;;
  esac
  exit 2
fi
if [ "$(head -c 5 "$f")" != "%PDF-" ]; then
  echo "ERROR the URL did not return a PDF (got $(head -c 60 "$f" | tr -dc '[:print:]' | head -c 60)...)" >&2
  exit 3
fi
pages=$(pdfinfo "$f" 2>/dev/null | awk '/^Pages:/{print $2}')
echo "PAGES ${pages:-?}" >&2
if [ "$last" -gt 0 ] 2>/dev/null; then
  pdftotext -layout -enc UTF-8 -f "$first" -l "$last" "$f" - 2>/dev/null
else
  pdftotext -layout -enc UTF-8 -f "$first" "$f" - 2>/dev/null
fi
rc=$?
[ $rc -eq 0 ] || { echo "ERROR pdftotext failed (exit $rc; encrypted or damaged PDF?)" >&2; exit 4; }
