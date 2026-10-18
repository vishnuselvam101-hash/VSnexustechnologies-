"""``vnxdna.device`` (V14, layer 6): the Python side of the VNX storage service ``vnxd``.

Phase 1 provides the shared contracts, mirrored from the Rust crate ``src/vnxdna/native/rust/vnxd`` and checked
against the same golden files: error codes and the write-state machine (:mod:`.contracts`), the persisted record
format (:mod:`.records`) and the management / codec protocols (:mod:`.protocol`). The management client and the
codec worker that use them are added in later phases (docs/V14_MASTER_PLAN.md).
"""
from .contracts import ERROR_CODES, DeviceError, ExtentSpan, Geometry, WriteState

__all__ = ["ERROR_CODES", "DeviceError", "ExtentSpan", "Geometry", "WriteState"]
