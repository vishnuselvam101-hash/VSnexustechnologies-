#!/usr/bin/env bash
# Run the test suite in an offline network namespace.
# If unshare (network namespace) is unavailable, exit with code 2 and a clear message.

set -euo pipefail

# Try to run a trivial command in a new network namespace.
if ! unshare --net --map-root-user true 2>/dev/null; then
    echo "Error: Network namespaces are unavailable; offline-only check cannot be performed." >&2
    exit 2
fi

# Run the full test suite in the offline namespace.
# We use pytest with the configured PYTHONPATH from the environment.
PYTHONPATH="${PYTHONPATH:-}" unshare --net --map-root-user pytest -q
