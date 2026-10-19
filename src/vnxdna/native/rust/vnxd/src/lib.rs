//! `vnxd`: the VNX storage service of V14 (docs/V14_ARCHITECTURE.md).
//!
//! Phase 1 contents: the storage contracts every later module builds on. Block addresses, extents and generations
//! ([`addr`]), write states ([`state`]), typed errors ([`error`]), integrity primitives ([`digest`]), the persisted
//! record format ([`record`]), strict canonical JSON ([`json`]), the management and codec protocols ([`proto`]) and
//! the media-adapter contract with its conformance suite ([`media`]). Standard library only; no `unsafe` code in Phase 1.
//! The byte layouts and schemas are specified in docs/V14_CONTRACTS.md and mirrored by `vnxdna.device.contracts`.
//!
//! Phase 7 adds the media adapters behind the unchanged contract (docs/V14_MEDIA_ADAPTERS.md): the electronic image
//! backend ([`image`]), the DNA software simulator ([`dnasim`]) over the codec boundary ([`codec`], worker pool
//! [`worker`]), the extended fault-injection backend ([`fault`]) and the future physical-DNA lab interface with its
//! simulator ([`lab`]).

#![deny(unsafe_code)] // a later module that needs an OS interface allows it locally and documents the boundary

pub mod addr;
pub mod audit;
pub mod authz;
pub mod balance;
pub mod cache;
pub mod codec;
pub mod digest;
pub mod dirmedia;
pub mod dnasim;
pub mod error;
pub mod fallback;
pub mod fault;
pub mod image;
pub mod io;
pub mod journal;
pub mod json;
pub mod lab;
pub mod media;
pub mod nbd;
pub mod nbdctl;
pub mod nbdserve;
pub mod nbdsys; // the only module with `unsafe` (the kernel ioctl and signal boundary)
pub mod proto;
pub mod ram;
pub mod record;
pub mod seal;
pub mod state;
pub mod volume;
pub mod worker;

pub const NAME: &str = "vnxd";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
