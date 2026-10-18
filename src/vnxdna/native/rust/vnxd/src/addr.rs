//! Block addresses, extents and generations (docs/V14_CONTRACTS.md §1).
//!
//! The device is a sequence of `capacity_blocks` blocks of `block_size` bytes. Blocks are grouped into extents of
//! `blocks_per_extent` blocks (the last extent may be shorter); an extent is the unit the volume maps, the cache
//! stages and the media adapter stores. Every stored version of an extent is a *generation*: generations are
//! immutable, numbered from 1 per extent and strictly increasing; an overwrite writes a new generation and remaps.
//! All arithmetic is checked; a request that does not fit the device is an error, never a wrap.

use crate::error::{err, ErrorCode, Result};
use std::num::NonZeroU64;

pub const MIN_BLOCK_SIZE: u32 = 512;
pub const MAX_BLOCK_SIZE: u32 = 1 << 20;
/// Upper bound on one extent (it is staged in memory and handed to the codec as one unit).
pub const MAX_EXTENT_BYTES: u64 = 64 << 20;

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct ExtentId(pub u64);

/// A generation number: never 0 (0 encodes "none" in persisted records).
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct Generation(NonZeroU64);

impl Generation {
    pub const FIRST: Generation = Generation(match NonZeroU64::new(1) {
        Some(v) => v,
        None => unreachable!(),
    });

    pub fn new(n: u64) -> Result<Generation> {
        match NonZeroU64::new(n) {
            Some(v) => Ok(Generation(v)),
            None => err(ErrorCode::InvalidArgument, "generation 0 is reserved for 'none'"),
        }
    }

    pub fn get(self) -> u64 {
        self.0.get()
    }

    pub fn next(self) -> Result<Generation> {
        match self.0.get().checked_add(1) {
            Some(n) => Generation::new(n),
            None => err(ErrorCode::Overflow, "generation counter exhausted"),
        }
    }
}

/// One stored version of one extent: the key of the media adapter.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct GenerationRef {
    pub extent: ExtentId,
    pub generation: Generation,
}

/// The part of one extent touched by a byte-range request.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ExtentSpan {
    pub extent: ExtentId,
    /// byte offset inside the extent
    pub offset: u64,
    pub len: u64,
    /// byte offset inside the request buffer
    pub buf_offset: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Geometry {
    block_size: u32,
    blocks_per_extent: u32,
    capacity_blocks: u64,
}

impl Geometry {
    pub fn new(block_size: u32, blocks_per_extent: u32, capacity_blocks: u64) -> Result<Geometry> {
        if !block_size.is_power_of_two() || !(MIN_BLOCK_SIZE..=MAX_BLOCK_SIZE).contains(&block_size) {
            return err(
                ErrorCode::InvalidArgument,
                format!("block size {block_size} must be a power of two in [{MIN_BLOCK_SIZE}, {MAX_BLOCK_SIZE}]"),
            );
        }
        if blocks_per_extent == 0 || (block_size as u64) * (blocks_per_extent as u64) > MAX_EXTENT_BYTES {
            return err(
                ErrorCode::InvalidArgument,
                format!("blocks per extent {blocks_per_extent} must be >= 1 with extent size <= {MAX_EXTENT_BYTES} bytes"),
            );
        }
        if capacity_blocks == 0 {
            return err(ErrorCode::InvalidArgument, "capacity must be at least one block");
        }
        // capacity in bytes must fit u64 (and therefore every byte offset inside the device)
        if capacity_blocks.checked_mul(block_size as u64).is_none() {
            return err(ErrorCode::Overflow, "capacity in bytes does not fit 64 bits");
        }
        Ok(Geometry { block_size, blocks_per_extent, capacity_blocks })
    }

    pub fn block_size(&self) -> u32 {
        self.block_size
    }

    pub fn blocks_per_extent(&self) -> u32 {
        self.blocks_per_extent
    }

    pub fn capacity_blocks(&self) -> u64 {
        self.capacity_blocks
    }

    pub fn capacity_bytes(&self) -> u64 {
        self.capacity_blocks * self.block_size as u64 // checked in new()
    }

    pub fn extent_bytes(&self) -> u64 {
        self.block_size as u64 * self.blocks_per_extent as u64
    }

    pub fn extent_count(&self) -> u64 {
        let per = self.blocks_per_extent as u64;
        self.capacity_blocks / per + u64::from(self.capacity_blocks % per != 0)
    }

    /// Byte length of one extent (the last one may be shorter).
    pub fn extent_len(&self, extent: ExtentId) -> Result<u64> {
        if extent.0 >= self.extent_count() {
            return err(ErrorCode::OutOfRange, format!("extent {} beyond {} extents", extent.0, self.extent_count()));
        }
        let start = extent.0 * self.extent_bytes();
        Ok(self.extent_bytes().min(self.capacity_bytes() - start))
    }

    /// Validate a byte-range request against the device (zero-length requests are invalid).
    pub fn check_range(&self, offset: u64, len: u64) -> Result<()> {
        if len == 0 {
            return err(ErrorCode::InvalidArgument, "zero-length request");
        }
        match offset.checked_add(len) {
            Some(end) if end <= self.capacity_bytes() => Ok(()),
            _ => err(
                ErrorCode::OutOfRange,
                format!("request [{offset}, +{len}) beyond the device capacity {}", self.capacity_bytes()),
            ),
        }
    }

    /// Split a byte-range request into per-extent spans, in address order.
    pub fn spans(&self, offset: u64, len: u64) -> Result<Vec<ExtentSpan>> {
        self.check_range(offset, len)?;
        let eb = self.extent_bytes();
        let mut out = Vec::new();
        let mut pos = offset;
        let end = offset + len;
        while pos < end {
            let extent = pos / eb;
            let inner = pos % eb;
            let take = (eb - inner).min(end - pos);
            out.push(ExtentSpan { extent: ExtentId(extent), offset: inner, len: take, buf_offset: pos - offset });
            pos += take;
        }
        Ok(out)
    }

    /// True when `[offset, offset + len)` starts and ends on block boundaries.
    pub fn is_block_aligned(&self, offset: u64, len: u64) -> bool {
        let bs = self.block_size as u64;
        offset % bs == 0 && len % bs == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn g() -> Geometry {
        // 4 KiB blocks, 4 blocks per extent (16 KiB), 10 blocks: extents of 16, 16 and 8 KiB
        Geometry::new(4096, 4, 10).unwrap()
    }

    #[test]
    fn geometry_validation() {
        assert_eq!(Geometry::new(1000, 4, 10).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(Geometry::new(256, 4, 10).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(Geometry::new(2 << 20, 1, 10).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(Geometry::new(4096, 0, 10).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(Geometry::new(4096, 1 << 15, 10).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(Geometry::new(4096, 4, 0).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(Geometry::new(4096, 4, u64::MAX).unwrap_err().code, ErrorCode::Overflow);
    }

    #[test]
    fn extent_lengths_and_count() {
        let g = g();
        assert_eq!((g.capacity_bytes(), g.extent_bytes(), g.extent_count()), (40960, 16384, 3));
        assert_eq!(g.extent_len(ExtentId(0)).unwrap(), 16384);
        assert_eq!(g.extent_len(ExtentId(2)).unwrap(), 8192);
        assert_eq!(g.extent_len(ExtentId(3)).unwrap_err().code, ErrorCode::OutOfRange);
    }

    #[test]
    fn spans_cover_the_request_exactly() {
        let g = g();
        let s = g.spans(16000, 20000).unwrap();
        assert_eq!(
            s,
            vec![
                ExtentSpan { extent: ExtentId(0), offset: 16000, len: 384, buf_offset: 0 },
                ExtentSpan { extent: ExtentId(1), offset: 0, len: 16384, buf_offset: 384 },
                ExtentSpan { extent: ExtentId(2), offset: 0, len: 3232, buf_offset: 16768 },
            ]
        );
        // exhaustive small check: spans are contiguous and sum to len
        for off in (0..40960).step_by(509) {
            for len in [1u64, 511, 4096, 16384, 20000] {
                if off + len > 40960 {
                    assert_eq!(g.spans(off, len).unwrap_err().code, ErrorCode::OutOfRange);
                    continue;
                }
                let s = g.spans(off, len).unwrap();
                let mut pos = 0;
                for sp in &s {
                    assert_eq!(sp.buf_offset, pos);
                    assert_eq!(sp.extent.0 * 16384 + sp.offset, off + pos);
                    assert!(sp.offset + sp.len <= 16384);
                    pos += sp.len;
                }
                assert_eq!(pos, len);
            }
        }
    }

    #[test]
    fn range_checks_never_wrap() {
        let g = g();
        assert_eq!(g.check_range(0, 0).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(g.check_range(u64::MAX, 2).unwrap_err().code, ErrorCode::OutOfRange);
        assert!(g.check_range(40959, 1).is_ok());
        assert!(g.is_block_aligned(8192, 4096) && !g.is_block_aligned(8192, 100));
    }

    #[test]
    fn generations() {
        assert_eq!(Generation::new(0).unwrap_err().code, ErrorCode::InvalidArgument);
        assert_eq!(Generation::FIRST.get(), 1);
        assert_eq!(Generation::FIRST.next().unwrap().get(), 2);
        assert_eq!(Generation::new(u64::MAX).unwrap().next().unwrap_err().code, ErrorCode::Overflow);
    }
}
