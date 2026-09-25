class VNXDNAError(Exception): """Base error for a recoverable VNX-DNA operation failure."""
class ConfigurationError(VNXDNAError): pass
class UnsupportedFormatError(VNXDNAError): pass
class MetadataError(VNXDNAError): pass
class InvalidDNAError(VNXDNAError): pass
class MissingStrandError(VNXDNAError): pass
class ECCRecoveryError(VNXDNAError): pass
class IntegrityError(VNXDNAError): pass
