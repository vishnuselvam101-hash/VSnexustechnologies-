# Shared environment for the VNX-DNA adapter (sourced by encode.sh and decode.sh).
# The codec under test is the checkout that contains this file; the lab virtualenv's own editable install is never used.
VNX_ADAPTER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VNX_REPO="${VNX_REPO:-$(cd "$VNX_ADAPTER_DIR/../../../../.." && pwd)}"
export PYTHONPATH="$VNX_REPO/src"
VNX_PY="${VNX_PY:-/root/vnx-dna-lab/.venv/bin/python}"
VNX_CLI=("$VNX_PY" -m vnxdna.v4.cli)
# refuse to run against any other vnxdna than the one in this checkout
_where="$("$VNX_PY" -c 'import vnxdna;print(vnxdna.__file__)')"
case "$_where" in
  "$VNX_REPO"/src/*) ;;
  *) echo "adapter: vnxdna imported from $_where, expected under $VNX_REPO/src" >&2; exit 97 ;;
esac
