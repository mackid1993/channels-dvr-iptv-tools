#!/bin/sh
# Build the pasteable Dispatcharr stream profiles from the readable sources and put
# them into README.md.
#
#   stream_profiles/hlsloop.py  ->  README block between <!-- BEGIN hlsloop --> / <!-- END hlsloop -->
#   stream_profiles/tsloop.py   ->  README block between <!-- BEGIN tsloop -->  / <!-- END tsloop -->
#
# Usage: scripts/build-profiles.sh          rewrite README.md
#        scripts/build-profiles.sh --check  exit 1 if README.md is out of date
set -eu
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

for name in hlsloop tsloop; do
  "$PY" scripts/minify_profile.py "stream_profiles/$name.py" > "$tmp/$name.txt"
done

"$PY" - "$tmp" <<'EOF'
import sys
tmp = sys.argv[1]
readme = open("README.md", encoding="utf-8").read()
for name in ("hlsloop", "tsloop"):
    block = open("%s/%s.txt" % (tmp, name), encoding="utf-8").read()
    begin, end = "<!-- BEGIN %s -->" % name, "<!-- END %s -->" % name
    if readme.count(begin) != 1 or readme.count(end) != 1:
        sys.exit("README.md needs exactly one %s ... %s pair" % (begin, end))
    head, rest = readme.split(begin)
    _, tail = rest.split(end)
    readme = head + begin + "\n```text\n" + block + "```\n" + end + tail
open("%s/README.md" % tmp, "w", encoding="utf-8").write(readme)
EOF

if [ "${1:-}" = "--check" ]; then
  if cmp -s README.md "$tmp/README.md"; then
    echo "README.md is up to date"
  else
    echo "README.md is out of date; run scripts/build-profiles.sh" >&2
    exit 1
  fi
else
  cp "$tmp/README.md" README.md
  echo "README.md updated"
fi
