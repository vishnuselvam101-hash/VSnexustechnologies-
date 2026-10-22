//! F6: the sealed volume's binding marker must compare the key check in constant time.
//!
//! The marker of a sealed volume carries the key's check value, and the comparison decided whether the volume may be
//! opened at all. A plain comparison stops at the first byte that differs, which tells an observer how much of the
//! check value matched; the crate already has the constant-time comparison (`authz::ct_eq`) for exactly this, and the
//! marker check now goes through it. A marker written with another key is still refused.

use std::fs;
use std::sync::Arc;

use vnxd::addr::{Generation, GenerationRef};
use vnxd::error::{ErrorCode, Result};
use vnxd::media::{MediaAdapter, MemoryMedia};
use vnxd::seal::{SealedMedia, Sealer};
use vnxd::volume::MARKER_EXTENT;

/// A sealer that only answers the key check: the binding marker never seals or opens an object.
struct FakeSealer {
    kcv: [u8; 32],
}

impl Sealer for FakeSealer {
    fn seal(&self, _device_id: &[u8; 16], _at: GenerationRef, plain: &[u8]) -> Result<Vec<u8>> {
        Ok(plain.to_vec())
    }

    fn open(&self, _device_id: &[u8; 16], _at: GenerationRef, sealed: &[u8]) -> Result<Vec<u8>> {
        Ok(sealed.to_vec())
    }

    fn key_check(&self, _device_id: &[u8; 16]) -> Result<[u8; 32]> {
        Ok(self.kcv)
    }
}

fn marker() -> GenerationRef {
    GenerationRef { extent: MARKER_EXTENT, generation: Generation::FIRST }
}

fn sealed(kcv: [u8; 32], inner: Arc<dyn MediaAdapter>) -> SealedMedia {
    SealedMedia::new(inner, Arc::new(FakeSealer { kcv }), [0x42u8; 16], 4096)
}

#[test]
fn a_marker_written_with_another_key_is_refused() {
    let raw = Arc::new(MemoryMedia::new());
    sealed([7u8; 32], raw.clone()).put(marker(), b"binding marker").unwrap();
    // the key that wrote the marker reads it back
    assert_eq!(sealed([7u8; 32], raw.clone()).get(marker()).unwrap(), b"binding marker");
    // another key is refused, before anything is read
    let e = sealed([9u8; 32], raw.clone()).get(marker()).unwrap_err();
    assert_eq!(e.code, ErrorCode::Unauthorized, "{e}");
}

#[test]
fn the_key_check_is_compared_in_constant_time() {
    let source = fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/src/seal.rs")).expect("the crate's seal.rs");
    assert!(source.contains("ct_eq("), "the key check must go through the constant-time comparison of the crate");
    for (n, line) in source.lines().enumerate() {
        let line = line.trim();
        if line.contains("kcv") && (line.contains("!=") || line.contains("==")) {
            panic!("the key check is compared with a plain equality on line {}: {line}", n + 1);
        }
    }
}
