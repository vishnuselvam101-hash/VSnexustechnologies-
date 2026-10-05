#!/bin/bash
# VNX-DNA decode adapter for the dt4dds-benchmark codec wrapper.
#   decode.sh READS_FILE OUTPUT_FILE PROFILE [SHUFFLE_SEED]
# READS_FILE: one read per line, plain A/C/G/T/N. The harness may have clustered or abundance-sorted them first; for the
# raw-read configuration (no clusterer) they arrive in strand order, so the adapter shuffles them with a fixed seed so that
# the order carries no cluster information. VNX-DNA groups reads by its own in-strand address and builds its own consensus.
# Exit status is the VNX-DNA exit status (0 = verified recovery; anything else, including 9 = PARTIAL, writes no output).
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
IN="$1"; OUT="$2"; PROFILE="${3:-dense}"; SEED="${4:-1}"
CFG="$VNX_ADAPTER_DIR/profiles/$PROFILE.json"
[ -f "$CFG" ] || { echo "adapter: unknown profile $PROFILE" >&2; exit 98; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
# deterministic shuffle, then FASTA
shuf --random-source=<(yes "$SEED") "$IN" | awk 'NF {print ">r" NR; print $1}' > "$TMP/reads.fasta"
mkdir "$TMP/extract"
"${VNX_CLI[@]}" decode "$TMP/reads.fasta" -o "$TMP/out.vnx" --extract "$TMP/extract" -c "$CFG" -w 1 > "$TMP/decode.json"
n=$(find "$TMP/extract" -type f | wc -l)
[ "$n" -eq 1 ] || { echo "adapter: expected 1 extracted file, got $n" >&2; exit 99; }
cp "$(find "$TMP/extract" -type f)" "$OUT"
