//! Error contract (docs/V14_CONTRACTS.md §5).
//!
//! Every failure of the service is a [`VnxError`]: a stable code, the class that decides whether the operation may be
//! retried, the errno the host block interface reports, and a human-readable message. The code strings are part of
//! the management and codec protocols and are mirrored by `vnxdna.device.contracts.ERROR_CODES`.

use std::fmt;

/// Whether an operation that failed with this code may succeed if repeated unchanged.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorClass {
    Retryable,
    Permanent,
}

impl ErrorClass {
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorClass::Retryable => "retryable",
            ErrorClass::Permanent => "permanent",
        }
    }
}

// errno values the Linux NBD driver passes to the block layer (include/uapi/asm-generic/errno*.h)
pub const EPERM: u32 = 1;
pub const EIO: u32 = 5;
pub const ENOMEM: u32 = 12;
pub const EINVAL: u32 = 22;
pub const ENOSPC: u32 = 28;
pub const EOVERFLOW: u32 = 75;
pub const ENOTSUP: u32 = 95;
pub const ESHUTDOWN: u32 = 108;

macro_rules! codes {
    ($($variant:ident => $name:literal, $class:ident, $errno:ident;)*) => {
        /// Stable error codes.
        #[derive(Clone, Copy, Debug, PartialEq, Eq)]
        pub enum ErrorCode { $($variant,)* }

        impl ErrorCode {
            pub const ALL: &'static [ErrorCode] = &[$(ErrorCode::$variant,)*];

            pub fn as_str(self) -> &'static str {
                match self { $(ErrorCode::$variant => $name,)* }
            }

            pub fn class(self) -> ErrorClass {
                match self { $(ErrorCode::$variant => ErrorClass::$class,)* }
            }

            /// The errno reported to the host block layer for a request that failed with this code.
            pub fn errno(self) -> u32 {
                match self { $(ErrorCode::$variant => $errno,)* }
            }

            pub fn parse(s: &str) -> Option<ErrorCode> {
                match s { $($name => Some(ErrorCode::$variant),)* _ => None }
            }
        }
    };
}

codes! {
    InvalidArgument => "INVALID_ARGUMENT", Permanent, EINVAL;
    OutOfRange => "OUT_OF_RANGE", Permanent, EINVAL;
    NoSpace => "NO_SPACE", Permanent, ENOSPC;
    Unauthorized => "UNAUTHORIZED", Permanent, EPERM;
    ReadOnly => "READ_ONLY", Permanent, EPERM;
    NotFound => "NOT_FOUND", Permanent, EIO;
    Corruption => "CORRUPTION", Permanent, EIO;
    BackendUnavailable => "BACKEND_UNAVAILABLE", Retryable, EIO;
    BackendBusy => "BACKEND_BUSY", Retryable, EIO;
    Timeout => "TIMEOUT", Retryable, EIO;
    ResourceExhausted => "RESOURCE_EXHAUSTED", Retryable, ENOMEM;
    Cancelled => "CANCELLED", Permanent, ESHUTDOWN;
    ShuttingDown => "SHUTTING_DOWN", Permanent, ESHUTDOWN;
    Protocol => "PROTOCOL", Permanent, EINVAL;
    VersionMismatch => "VERSION_MISMATCH", Permanent, ENOTSUP;
    Overflow => "OVERFLOW", Permanent, EOVERFLOW;
    Internal => "INTERNAL", Permanent, EIO;
    Retained => "RETAINED", Permanent, EPERM;
}

/// A typed failure with its diagnostic message.
#[derive(Clone, Debug, PartialEq, Eq)]
#[derive(Debug)]
pub struct VnxError {
    pub code: ErrorCode,
    pub message: String,
}

impl VnxError {
    pub fn new(code: ErrorCode, message: impl Into<String>) -> VnxError {
        VnxError { code, message: message.into() }
    }

    pub fn class(&self) -> ErrorClass {
        self.code.class()
    }

    pub fn is_retryable(&self) -> bool {
        self.class() == ErrorClass::Retryable
    }
}

impl fmt::Display for VnxError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code.as_str(), self.message)
    }
}

impl std::error::Error for VnxError {}

pub type Result<T> = std::result::Result<T, VnxError>;

/// Shorthand used throughout the crate.
pub fn err<T>(code: ErrorCode, message: impl Into<String>) -> Result<T> {
    Err(VnxError::new(code, message))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_round_trip_and_are_unique() {
        let mut seen = std::collections::BTreeSet::new();
        for &c in ErrorCode::ALL {
            assert!(seen.insert(c.as_str()));
            assert_eq!(ErrorCode::parse(c.as_str()), Some(c));
        }
        assert_eq!(ErrorCode::parse("nope"), None);
    }

    #[test]
    fn retryable_codes_are_exactly_the_transient_ones() {
        let retry: Vec<&str> =
            ErrorCode::ALL.iter().filter(|c| c.class() == ErrorClass::Retryable).map(|c| c.as_str()).collect();
        assert_eq!(retry, ["BACKEND_UNAVAILABLE", "BACKEND_BUSY", "TIMEOUT", "RESOURCE_EXHAUSTED"]);
    }

    #[test]
    fn display_names_the_code() {
        let e = VnxError::new(ErrorCode::NoSpace, "volume full");
        assert_eq!(e.to_string(), "NO_SPACE: volume full");
        assert!(!e.is_retryable());
        assert_eq!(e.code.errno(), ENOSPC);
    }
}
