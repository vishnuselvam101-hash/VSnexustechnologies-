"""Layer and import-cycle rules of the V6 package structure (V6_ARCHITECTURE §3), checked statically with :mod:`ast`.

Every module under ``src/vnxdna`` is assigned to a package node:

* a **layer package** (``vnxdna.core`` … ``vnxdna.commands``, table ``LAYERS``);
* a module that has not moved yet goes to its **target** package (table ``PENDING``; it only shrinks as the
  migration of V6_ARCHITECTURE §7.1 proceeds);
* ``legacy``: frozen V0.1–V3 code that serves only ``vnx-dna`` (``LEGACY``);
* a **shim**: an old module path that is now an alias of the moved module (``alias_module`` in ``vnxdna.core._alias``)
  or a façade of a split module (``__facade_of__``). Shims are not nodes; imports of them are resolved to their target.

Module-level and function-level imports both count (the audit's cycles hid in function-level imports). The rules:

R1  downward only: a layer package imports only its own package or lower layers.
R2  codec purity: core … pipeline never import simulation, providers, physical, benchmark, sdk, commands or legacy.
R3  the package-level import graph is acyclic.
R4  no layer package imports legacy code; legacy code imports only core, native, archive, codec and dnaenc.
R5  only ``vnxdna.native`` uses ``ctypes``.
R6  ``vnxdna.commands`` imports only ``vnxdna.sdk`` and ``vnxdna.core``.
R7  layer packages import moved code by its new path, never through a shim.

Known exceptions are listed in ``layer_allowlist.json`` with a reason. The allow-list may only shrink: an entry that no
longer matches a real import fails this test, so it has to be deleted when the code is fixed.
"""
from __future__ import annotations

import ast
import json
from functools import lru_cache
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
ALLOWLIST = Path(__file__).with_name("layer_allowlist.json")

LAYERS = {
    "core": 0, "native": 1, "archive": 2, "codec": 2, "dnaenc": 3, "sync": 3, "recovery": 4,
    "pipeline": 5, "simulation": 5, "physical": 5, "providers": 6, "benchmark": 6, "sdk": 7, "conformance": 7,
    "commands": 8,
}
#: R2: packages that must stay free of simulation/application code
PURE = ("core", "native", "archive", "codec", "dnaenc", "sync", "recovery", "pipeline")
PURE_FORBIDDEN = ("simulation", "providers", "physical", "benchmark", "sdk", "commands", "legacy")
#: R4: what legacy code may use (through the old module paths)
LEGACY_MAY_USE = ("core", "native", "archive", "codec", "dnaenc", "legacy")

#: top-level modules that belong to a layer without living in its package
TOP_LEVEL = {"vnxdna._version": "core", "vnxdna._native_build": "native"}

#: modules not yet moved -> the package they move to (V6_ARCHITECTURE §2 table). Shrinks with every M-step.
PENDING = {
    "vnxdna.errors": "core", "vnxdna.provenance": "core", "vnxdna.v4.errors": "core", "vnxdna.v4.version": "core",
    "vnxdna.v4.util": "core", "vnxdna.v2.crc": "core", "vnxdna.v6.observe": "core", "vnxdna.v6.errors": "core",
    "vnxdna.native": "native", "vnxdna.v5.native_alignment": "native", "vnxdna.v6.native_reads": "native",
    "vnxdna.v6.native_rs": "native",
    "vnxdna.v4.container": "archive", "vnxdna.v4.archive": "archive", "vnxdna.v4.crypto": "archive",
    "vnxdna.v4.merkle": "archive", "vnxdna.container.compression": "archive",
    "vnxdna.ecc.gf256": "codec", "vnxdna.ecc.cauchy": "codec", "vnxdna.ecc.rs_batch": "codec", "vnxdna.v4.codecs": "codec",
    "vnxdna.v4.rs_fast": "codec", "vnxdna.v6.outer": "codec", "vnxdna.v6.profiles": "codec",
    "vnxdna.v4.frame": "dnaenc", "vnxdna.v4.constraints": "dnaenc", "vnxdna.v4.reads": "dnaenc", "vnxdna.v2.strandio": "dnaenc",
    "vnxdna.v4.sync": "sync", "vnxdna.v5.indel": "sync",
    "vnxdna.v5.soft": "recovery", "vnxdna.v6.decode": "recovery", "vnxdna.v6.recovery": "recovery",
    "vnxdna.v4.decoder": "pipeline", "vnxdna.v4.encoder": "pipeline", "vnxdna.v6.encoder": "pipeline",
    "vnxdna.v4.channel": "simulation", "vnxdna.v6.loss": "simulation",
    "vnxdna.v4.bench": "benchmark", "vnxdna.v4.experiment": "benchmark", "vnxdna.v4.sweep": "benchmark",
    "vnxdna.v4.datagen": "benchmark", "vnxdna.v4.compare": "benchmark",
    "vnxdna.v4.config": "sdk", "vnxdna.v4.cli": "commands",
}
#: the version packages' own __init__ files only re-export; they are neither layer code nor legacy
VERSION_PACKAGES = ("vnxdna.v4", "vnxdna.v5", "vnxdna.v5.indel", "vnxdna.v5.soft", "vnxdna.v6")
LEGACY = ("vnxdna.v2", "vnxdna.v3", "vnxdna.api", "vnxdna.cli", "vnxdna.cli_v1", "vnxdna.bench", "vnxdna.channel",
          "vnxdna.container", "vnxdna.dna", "vnxdna.storage", "vnxdna.sync.indel", "vnxdna.legacy", "vnxdna._entry",
          "vnxdna.__main__", "vnxdna.ecc")
ROOT = "vnxdna"      # the package __init__: re-exports __version__ and native_status; not a node


def _name(path: Path) -> str:
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@lru_cache(maxsize=1)
def modules() -> dict[str, tuple[Path, ast.Module]]:
    out = {}
    for p in sorted((SRC / "vnxdna").rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        out[_name(p)] = (p, ast.parse(p.read_text(encoding="utf-8"), filename=str(p)))
    return out


def _is_package(mod: str) -> bool:
    return modules()[mod][0].name == "__init__.py"


def shim_target(mod: str) -> str | tuple | None:
    """The alias target of a shim module, ``("facade", ...)`` for a façade, or None for real code."""
    tree = modules()[mod][1]
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if (getattr(f, "id", None) or getattr(f, "attr", None)) == "alias_module":
                return node.value.args[1].value
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "__facade_of__" for t in node.targets):
            return ("facade",) + tuple(ast.literal_eval(node.value))
    return None


def resolve(mod: str) -> str:
    """Follow shim aliases to the module that holds the code."""
    seen = set()
    while mod in modules() and isinstance(t := shim_target(mod), str) and mod not in seen:
        seen.add(mod)
        mod = t
    return mod


def node_of(mod: str) -> str | None:
    """Package node of a real module (None: root, version-package __init__, or shim)."""
    if mod == ROOT or mod in VERSION_PACKAGES or shim_target(mod) is not None:
        return None
    if mod in TOP_LEVEL:
        return TOP_LEVEL[mod]
    for prefix in sorted(PENDING, key=len, reverse=True):
        if mod == prefix or mod.startswith(prefix + "."):
            return PENDING[prefix]
    for prefix in LEGACY:                    # before the layer packages: vnxdna.sync.indel is the V1 module
        if mod == prefix or mod.startswith(prefix + "."):
            return "legacy"
    parts = mod.split(".")
    if len(parts) > 1 and parts[1] in LAYERS:
        return parts[1]
    raise AssertionError(f"module {mod} is not classified: add it to a layer package, PENDING or LEGACY")


def _target_modules(mod: str, node: ast.AST) -> list[str]:
    """Modules an import statement in ``mod`` refers to (``from p import x`` → ``p.x`` if that is a module)."""
    known = modules()
    out = []
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name.startswith("vnxdna"):
                out.append(a.name)
        return out
    assert isinstance(node, ast.ImportFrom)
    if node.level:
        base = mod.split(".") if _is_package(mod) else mod.split(".")[:-1]
        base = base[: len(base) - (node.level - 1)]
        pkg = ".".join(base + ([node.module] if node.module else []))
    else:
        pkg = node.module or ""
    if not pkg.startswith("vnxdna"):
        return out
    for a in node.names:
        sub = f"{pkg}.{a.name}"
        out.append(sub if sub in known else pkg)
    return out


@lru_cache(maxsize=1)
def imports() -> list[tuple[str, str, int]]:
    """(importing module, imported module as written, line) for every vnxdna import in src, at any nesting level."""
    out = []
    for mod, (_, tree) in modules().items():
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for t in _target_modules(mod, node):
                    if t in modules():
                        out.append((mod, t, node.lineno))
    return out


def violations() -> tuple[set, dict]:
    """(rule violations as "R<n> src -> dst" strings, package edges -> example module edges)."""
    found: set = set()
    edges: dict = {}
    for src, written, _ in imports():
        s = node_of(src)
        if s is None:
            continue
        dst_mod = resolve(written)
        d = node_of(dst_mod) if dst_mod in modules() else None
        if s != "legacy" and (written != dst_mod or isinstance(shim_target(written), tuple)):
            found.add(f"R7 {src} -> {written}")
        if d is None or d == s:
            continue
        edges.setdefault((s, d), set()).add(f"{src} -> {dst_mod}")
        if s == "legacy":
            if d not in LEGACY_MAY_USE:
                found.add(f"R4 {src} -> {dst_mod}")
            continue
        if d == "legacy":
            found.add(f"R4 {src} -> {dst_mod}")
            continue
        if LAYERS[d] > LAYERS[s]:
            found.add(f"R1 {src} -> {dst_mod}")
        if s in PURE and d in PURE_FORBIDDEN:
            found.add(f"R2 {src} -> {dst_mod}")
        if s == "commands" and d not in ("sdk", "core"):
            found.add(f"R6 {src} -> {dst_mod}")
    for mod, (_, tree) in modules().items():
        if node_of(mod) in (None, "native"):
            continue
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and not node.level else []
            if any(n and (n == "ctypes" or n.startswith("ctypes.")) for n in names):
                found.add(f"R5 {mod} -> ctypes")
    return found, edges


def find_cycle(edges: set) -> list | None:
    graph: dict = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
    state: dict = {}
    stack: list = []

    def visit(n):
        state[n] = 1
        stack.append(n)
        for m in sorted(graph.get(n, ())):
            if state.get(m) == 1:
                return stack[stack.index(m):] + [m]
            if m not in state and (c := visit(m)):
                return c
        stack.pop()
        state[n] = 2
        return None

    for n in sorted(graph):
        if n not in state and (c := visit(n)):
            return c
    return None


def allowlist() -> dict:
    return json.loads(ALLOWLIST.read_text())


# ----------------------------------------------------------------------------------------------------------------- tests
def test_every_module_is_classified():
    for mod in modules():
        node_of(mod)


def test_shims_point_at_real_modules():
    for mod in modules():
        t = shim_target(mod)
        if isinstance(t, str):
            assert t in modules(), f"{mod} aliases the unknown module {t}"
            assert shim_target(t) is None, f"{mod} aliases another shim {t}"


def test_layer_rules_hold_except_the_allowlist():
    found, _ = violations()
    allowed = {e["violation"] for e in allowlist()["violations"]}
    new = sorted(found - allowed)
    assert not new, "layer rule violations (fix them; the allow-list may only shrink):\n" + "\n".join(new)


def test_allowlist_has_no_stale_entries():
    """The allow-list may only shrink: an entry that no longer occurs must be deleted."""
    found, edges = violations()
    stale = sorted({e["violation"] for e in allowlist()["violations"]} - found)
    assert not stale, "stale allow-list entries (delete them):\n" + "\n".join(stale)
    pkg_edges = set(edges)
    stale_c = [e for e in allowlist()["cycle_edges"] if tuple(e["edge"]) not in pkg_edges]
    assert not stale_c, f"stale cycle-edge entries (delete them): {stale_c}"
    for e in allowlist()["violations"] + allowlist()["cycle_edges"]:
        assert e.get("why"), f"allow-list entry without a reason: {e}"


def test_package_graph_is_acyclic():
    _, edges = violations()
    tolerated = {tuple(e["edge"]) for e in allowlist()["cycle_edges"]}
    cycle = find_cycle(set(edges) - tolerated)
    assert cycle is None, f"import cycle between packages: {' -> '.join(cycle)}; e.g. " + \
        "; ".join(sorted(next(iter(edges[(a, b)])) for a, b in zip(cycle, cycle[1:])))


def test_cycle_detector_is_not_vacuous():
    assert find_cycle({("a", "b"), ("b", "c"), ("c", "a")}) is not None
    assert find_cycle({("a", "b"), ("b", "c"), ("a", "c")}) is None
