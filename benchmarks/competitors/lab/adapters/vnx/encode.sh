#!/bin/bash
# VNX-DNA encode adapter for the dt4dds-benchmark codec wrapper.
#   encode.sh INPUT_FILE SEQUENCE_FILE PROFILE
# Writes one strand per line (plain A/C/G/T, no FASTA header, no primers) to SEQUENCE_FILE.
# PROFILE names profiles/PROFILE.json (a VNX-DNA configuration file). No side information is produced.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/env.sh"
IN="$1"; OUT="$2"; PROFILE="${3:-dense}"
CFG="$VNX_ADAPTER_DIR/profiles/$PROFILE.json"
[ -f "$CFG" ] || { echo "adapter: unknown profile $PROFILE" >&2; exit 98; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
"${VNX_CLI[@]}" encode "$IN" "$TMP/strands.fasta" -c "$CFG" -w 1 > "$TMP/encode.json"
grep -v '^>' "$TMP/strands.fasta" > "$OUT"
