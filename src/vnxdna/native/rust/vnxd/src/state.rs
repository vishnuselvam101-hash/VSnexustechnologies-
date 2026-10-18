//! Write states (master directive §5.1; docs/V14_CONTRACTS.md §2).
//!
//! A write moves forward only: RECEIVED -> STAGED -> BACKEND_COMMITTED -> VERIFIED. Any non-terminal state may move
//! to FAILED. The states are not interchangeable: only BACKEND_COMMITTED and VERIFIED may be reported to the host as
//! durable, and only within the guarantee the backend states (`media::Durability`). Bytes held in RAM are never
//! durable.

use crate::error::{err, ErrorCode, Result};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum WriteState {
    /// the request is in a service buffer
    Received,
    /// the extent bytes are in staging memory or the staging directory (volatile)
    Staged,
    /// the backend acknowledged its documented commit
    BackendCommitted,
    /// the readback/integrity check passed and the mapping record is durable
    Verified,
    /// the write failed; the previous mapping is unchanged
    Failed,
}

impl WriteState {
    pub const ALL: [WriteState; 5] =
        [WriteState::Received, WriteState::Staged, WriteState::BackendCommitted, WriteState::Verified, WriteState::Failed];

    pub fn as_str(self) -> &'static str {
        match self {
            WriteState::Received => "RECEIVED",
            WriteState::Staged => "STAGED",
            WriteState::BackendCommitted => "BACKEND_COMMITTED",
            WriteState::Verified => "VERIFIED",
            WriteState::Failed => "FAILED",
        }
    }

    pub fn parse(s: &str) -> Option<WriteState> {
        WriteState::ALL.iter().copied().find(|w| w.as_str() == s)
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, WriteState::Verified | WriteState::Failed)
    }

    /// May the host be told the write is durable (within the backend's stated guarantee)?
    pub fn is_durable(self) -> bool {
        matches!(self, WriteState::BackendCommitted | WriteState::Verified)
    }

    /// The only legal transitions; anything else is an internal error of the caller.
    pub fn advance(self, to: WriteState) -> Result<WriteState> {
        use WriteState::*;
        let ok = matches!(
            (self, to),
            (Received, Staged) | (Staged, BackendCommitted) | (BackendCommitted, Verified)
                | (Received, Failed) | (Staged, Failed) | (BackendCommitted, Failed)
        );
        if ok {
            Ok(to)
        } else {
            err(ErrorCode::Internal, format!("illegal write-state transition {} -> {}", self.as_str(), to.as_str()))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use WriteState::*;

    #[test]
    fn transition_table_is_exact() {
        let legal = [
            (Received, Staged),
            (Staged, BackendCommitted),
            (BackendCommitted, Verified),
            (Received, Failed),
            (Staged, Failed),
            (BackendCommitted, Failed),
        ];
        for a in WriteState::ALL {
            for b in WriteState::ALL {
                assert_eq!(a.advance(b).is_ok(), legal.contains(&(a, b)), "{a:?} -> {b:?}");
            }
        }
    }

    #[test]
    fn durability_and_names() {
        assert!(!Received.is_durable() && !Staged.is_durable() && !Failed.is_durable());
        assert!(BackendCommitted.is_durable() && Verified.is_durable());
        for s in WriteState::ALL {
            assert_eq!(WriteState::parse(s.as_str()), Some(s));
        }
        assert!(Verified.is_terminal() && Failed.is_terminal() && !Staged.is_terminal());
    }
}
