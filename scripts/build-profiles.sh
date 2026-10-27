#!/bin/sh
# Build the pasteable Dispatcharr stream profiles from the readable sources and put
# them into README.md.
#
# Each block holds two copy-paste parts: the settings (pasted once) and the code
# (replaced on every update). Together they are exactly the whole profile.
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
  "$PY" scripts/minify_profile.py "stream_profiles/$name.py" --settings > "$tmp/$name.settings.txt"
  "$PY" scripts/minify_profile.py "stream_profiles/$name.py" --code > "$tmp/$name.code.txt"
  "$PY" scripts/minify_profile.py "stream_profiles/$name.py" > "$tmp/$name.whole.txt"
done

"$PY" - "$tmp" <<'EOF'
import sys
tmp = sys.argv[1]
readme = open("README.md", encoding="utf-8").read()
for name in ("hlsloop", "tsloop"):
    st = open("%s/%s.settings.txt" % (tmp, name), encoding="utf-8").read()
    cd = open("%s/%s.code.txt" % (tmp, name), encoding="utf-8").read()
    if st + cd != open("%s/%s.whole.txt" % (tmp, name), encoding="utf-8").read():
        sys.exit("%s: settings + code parts do not add up to the whole profile" % name)
    block = ("\n**Part 1: settings.** Paste this first and fill it in. You only do this once.\n\n"
             "```text\n" + st + "```\n\n"
             "**Part 2: code.** Paste this directly below part 1, in the same Parameters box. "
             "To update the profile later, delete everything below the `END OF SETTINGS` line "
             "and paste the new part 2 there.\n\n"
             "```text\n" + cd + "```\n")
    begin, end = "<!-- BEGIN %s -->" % name, "<!-- END %s -->" % name
    if readme.count(begin) != 1 or readme.count(end) != 1:
        sys.exit("README.md needs exactly one %s ... %s pair" % (begin, end))
    head, rest = readme.split(begin)
    _, tail = rest.split(end)
    readme = head + begin + "\n" + block + end + tail
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
