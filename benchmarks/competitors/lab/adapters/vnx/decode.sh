#!/bin/bash
# VNX-DNA decode adapter for the dt4dds-benchmark codec wrapper.
#   decode.sh READS_FILE OUTPUT_FILE PROFILE [SHUFFLE_SEED] [DECODER]
# READS_FILE: one read per line, plain A/C/G/T/N. The harness may have clustered or abundance-sorted them first; for the
# raw-read configuration (no clusterer) they arrive in strand order, so the adapter shuffles them with a fixed seed so that
# the order carries no cluster information. VNX-DNA groups reads by its own in-strand address and builds its own consensus.
# DECODER: "default" (the CLI decoder, default) or a preset name of decode_preset.py (e.g. "F": read clustering
# fallback with the outer-code list decoding preset; opt-in, EXPERIMENTAL).
# Exit status is the VNX-DNA exit status (0 = verified recovery; anything else, including 9 = PARTIAL, writes no output).
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
IN="$1"; OUT="$2"; PROFILE="${3:-dense}"; SEED="${4:-1}"; DECODER="${5:-default}"
CFG="$VNX_ADAPTER_DIR/profiles/$PROFILE.json"
[ -f "$CFG" ] || { echo "adapter: unknown profile $PROFILE" >&2; exit 98; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
# deterministic shuffle, then FASTA
shuf --random-source=<(yes "$SEED") "$IN" | awk 'NF {print ">r" NR; print $1}' > "$TMP/reads.fasta"
mkdir "$TMP/extract"
if [ "$DECODER" = default ]; then
  "${VNX_CLI[@]}" decode "$TMP/reads.fasta" -o "$TMP/out.vnx" --extract "$TMP/extract" -c "$CFG" -w 1 > "$TMP/decode.json"
else
  "$VNX_PY" "$VNX_ADAPTER_DIR/decode_preset.py" "$TMP/reads.fasta" "$TMP/extract" "$CFG" "$DECODER" > "$TMP/decode.txt" || { rc=$?; cat "$TMP/decode.txt" >&2; exit $rc; }
  cat "$TMP/decode.txt" >&2
fi
n=$(find "$TMP/extract" -type f | wc -l)
[ "$n" -eq 1 ] || { echo "adapter: expected 1 extracted file, got $n" >&2; exit 99; }
cp "$(find "$TMP/extract" -type f)" "$OUT"
