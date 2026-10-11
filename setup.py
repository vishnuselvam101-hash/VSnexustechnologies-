"""Build hook for the optional native kernels (all other metadata is in pyproject.toml).

C kernels: plain C shared libraries loaded with ctypes (no Python C API): the V5 marker aligner, the V6 read parser,
the V6 inner Reed-Solomon decoder (runtime SIMD dispatch, no -march), the V7 read-clustering kernels and the strand
builder. C++ kernels (``_native_build.CXX_KERNELS``, e.g. outer-code list decoding): the same C ABI contract, compiled
as C++20 optional extensions. Rust kernels (``_native_build.RUST_KERNELS``, e.g. the integrity kernel): cdylib crates with the same C ABI
contract, built with cargo (offline, standard library only) when a Rust toolchain is present.
Every kernel is *optional*: without a compiler (or if one kernel fails to build) installation still succeeds and
VNX-DNA uses the bit-identical reference for that kernel. ``python -m vnxdna.native`` reports which backend every
kernel uses. Flags and sources come from src/vnxdna/_native_build.py, shared with the explicit ``python -m … build``
commands. ``CARGO`` selects the cargo executable (``CARGO=/bin/false`` skips the Rust kernels).
"""
import importlib.util
import os
import shutil
import subprocess
import sysconfig
import tempfile
from pathlib import Path

from setuptools import Extension, setup

try:
    from setuptools.command.build_ext import build_ext
except ImportError:                    # a stand-in setuptools (the packaging-contract test): no build command
    build_ext = None

_spec = importlib.util.spec_from_file_location("_vnx_native_build", Path(__file__).parent / "src" / "vnxdna" / "_native_build.py")
_nb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_nb)


class build_native(build_ext or object):
    """build_ext plus the optional Rust kernels (copied next to the C extensions as ``<module><EXT_SUFFIX>``)."""

    def run(self):
        super().run()
        for name, crate in getattr(_nb, "RUST_KERNELS", {}).items():
            try:
                self._build_rust(name, Path(crate))
            except Exception as error:  # noqa: BLE001 - optional, like the C extensions
                print(f"WARNING: optional Rust kernel {name} not built ({error}); the reference implementation is used")

    def _build_rust(self, name: str, crate: Path) -> None:
        cargo = os.environ.get("CARGO") or shutil.which("cargo")
        if not cargo:
            raise RuntimeError("no cargo found")
        lib = f"libvnx_{crate.name}.so"
        with tempfile.TemporaryDirectory(prefix="vnx-cargo-") as target:
            subprocess.run([cargo, "build", "--release", "--offline", "--locked", "--manifest-path",
                            str(crate / "Cargo.toml"), "--target-dir", target], check=True)
            dest = Path(self.build_lib) / Path(*name.split("."))
            dest = dest.with_name(dest.name + (sysconfig.get_config_var("EXT_SUFFIX") or ".so"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(target) / "release" / lib, dest)
            if self.inplace:
                shutil.copyfile(dest, Path("src") / dest.relative_to(self.build_lib))


setup(ext_modules=[Extension(name, [source], extra_compile_args=list(_nb.COMPILE_FLAGS), optional=True,
                             depends=sorted(str(f) for f in Path(source).parent.glob("*.inc")))   # e.g. align_dp.inc
                   for name, source in _nb.KERNELS.items()]
                  + [Extension(name, [source], extra_compile_args=list(_nb.CXX_COMPILE_FLAGS), language="c++",
                               optional=True) for name, source in getattr(_nb, "CXX_KERNELS", {}).items()],
      cmdclass={"build_ext": build_native} if build_ext else {})
