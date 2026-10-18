//! `vnxd`: the VNX storage service of V14 (docs/V14_ARCHITECTURE.md).
//!
//! Phase 1 contents: the storage contracts every later module builds on. Block addresses, extents and generations
//! ([`addr`]), write states ([`state`]), typed errors ([`error`]), integrity primitives ([`digest`]), the persisted
//! record format ([`record`]), strict canonical JSON ([`json`]), the management and codec protocols ([`proto`]) and
//! the media-adapter contract with its conformance suite ([`media`]). Standard library only; no `unsafe` code in Phase 1.
//! The byte layouts and schemas are specified in docs/V14_CONTRACTS.md and mirrored by `vnxdna.device.contracts`.

#![deny(unsafe_code)] // a later module that needs an OS interface allows it locally and documents the boundary

pub mod addr;
pub mod digest;
pub mod error;
pub mod io;
pub mod journal;
pub mod json;
pub mod media;
pub mod proto;
pub mod record;
pub mod state;
pub mod volume;

pub const NAME: &str = "vnxd";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
