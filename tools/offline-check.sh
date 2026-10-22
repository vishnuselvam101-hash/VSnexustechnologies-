#!/usr/bin/env bash
# AP-17 offline-only check: run the test suite inside a network namespace that
# has no route off the machine, so a passing run proves the suite needs no
# network.
#
# Usage: tools/offline-check.sh [pytest args]
#   With no arguments the core suite runs: every test under tests/ except
#   tests/v14/test_device_nbd.py, which needs root block devices.
#
# Exit codes:
#   0  the suite passed inside the network namespace
#   1  the suite failed inside the network namespace
#   2  network namespaces are unavailable (or the namespace is not isolated),
#      so no offline run was possible; the reason is printed
#
# Environment:
#   VNX_UNSHARE          unshare command to use (default: unshare)
#   VNX_PYTHON           Python interpreter to use (default: python3)
#   VNX_PYTEST_WORKERS   pytest-xdist workers (default: 8)
set -uo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd) || exit 2
cd "$repo_root" || exit 2

UNSHARE="${VNX_UNSHARE:-unshare}"
PY="${VNX_PYTHON:-python3}"
WORKERS="${VNX_PYTEST_WORKERS:-8}"

if ! command -v "$UNSHARE" >/dev/null 2>&1; then
    echo "offline-check: '$UNSHARE' was not found; network namespaces are unavailable, so the offline check cannot run." >&2
    echo "offline-check: install util-linux (unshare) and allow unprivileged user namespaces." >&2
    exit 2
fi
if ! "$UNSHARE" --net --map-root-user true >/dev/null 2>&1; then
    echo "offline-check: '$UNSHARE --net --map-root-user' failed; network namespaces are unavailable, so the offline check cannot run." >&2
    echo "offline-check: the host must allow unprivileged user namespaces." >&2
    exit 2
fi
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "offline-check: the Python interpreter '$PY' was not found (set VNX_PYTHON)." >&2
    exit 2
fi

if [ "$#" -eq 0 ]; then
    set -- tests --ignore=tests/v14/test_device_nbd.py
fi

# Runs inside the namespace: the only interface may be the loopback, then the
# suite runs with the repository's pytest addopts neutralised.
runner='
set -u
ip link set lo up 2>/dev/null || true
if command -v ip >/dev/null 2>&1; then
    names=$(ip -o link show 2>/dev/null | sed -e "s/^[0-9]*: //" -e "s/@.*//" | cut -d: -f1)
else
    names=$(ls -1 /sys/class/net 2>/dev/null)
fi
extra=$(printf "%s\n" "$names" | grep -v "^lo$" | grep -v "^[[:space:]]*$" || true)
if [ -n "$extra" ]; then
    echo "offline-check: the network namespace still exposes interfaces: $extra" >&2
    exit 2
fi
exec env PYTHONPATH=src OPENBLAS_NUM_THREADS=1 PYBIN -m pytest -o addopts="" -q -n WORKERS -p no:cacheprovider "$@"
'
runner=${runner//PYBIN/$PY}
runner=${runner//WORKERS/$WORKERS}

"$UNSHARE" --net --map-root-user -- bash -c "$runner" offline-check "$@"
rc=$?

if [ "$rc" -eq 2 ]; then
    echo "offline-check: the network namespace was not isolated; the offline check could not be performed." >&2
    exit 2
fi
if [ "$rc" -ne 0 ]; then
    echo "offline-check: the suite failed inside the network namespace (pytest exit $rc)." >&2
    exit 1
fi
exit 0
