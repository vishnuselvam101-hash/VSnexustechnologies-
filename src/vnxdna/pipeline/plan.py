"""AP-8 cost planner: estimate strands, bases and costs before synthesis.

Before synthesis, report strands, bases and an estimated cost per vendor price profile that the operator
supplies. No hard-coded prices.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from vnxdna.core.errors import VNXConfigurationError
from vnxdna.codec.codecs import make_outer
from vnxdna.dnaenc.superblock import Superblock, group_k
from vnxdna.pipeline.encode import DNAOptions
from vnxdna.pipeline.encode_v6 import resolve_geometry

if TYPE_CHECKING:
    pass


PRICE_FORMAT = "vnx.vendor-prices/1"


@dataclass(frozen=True)
class VendorPrice:
    vendor: str
    currency: str              # ISO 4217 code, e.g. "USD"
    per_base: Decimal | None = None         # price per synthesised base
    per_strand: Decimal | None = None       # price per oligo
    per_order: Decimal = Decimal(0)         # fixed fee per order
    max_strand_nt: int | None = None        # refuse a plan whose strand_nt exceeds this
    min_strands: int = 0


def load_prices(path: str | os.PathLike) -> list[VendorPrice]:
    """Load vendor prices from a JSON file."""
    import json
    
    path = Path(path)
    with open(path, "r") as f:
        data = json.load(f)
    
    if not isinstance(data, dict):
        raise VNXConfigurationError("Vendor prices must be a JSON object")
    
    # Validate top-level keys (only "format" and "vendors" allowed)
    allowed_top_level = {"format", "vendors"}
    for key in data.keys():
        if key not in allowed_top_level:
            raise VNXConfigurationError(f"Unknown top-level key: {key}")
    
    if "format" not in data or data["format"] != PRICE_FORMAT:
        raise VNXConfigurationError(f"Unsupported vendor price format {data.get('format', 'unknown')}; expected {PRICE_FORMAT}")
        
    if "vendors" not in data:
        raise VNXConfigurationError("Vendor prices must contain a 'vendors' array")
    
    vendors = []
    for item in data["vendors"]:
        if not isinstance(item, dict):
            raise VNXConfigurationError("Each vendor must be a JSON object")
            
        # Validate vendor keys (only known keys allowed)
        allowed_vendor_keys = {"vendor", "currency", "per_base", "per_strand", "per_order", "max_strand_nt", "min_strands"}
        for key in item.keys():
            if key not in allowed_vendor_keys:
                raise VNXConfigurationError(f"Unknown vendor key: {key}")
            
        # Validate required fields
        if "vendor" not in item:
            raise VNXConfigurationError("Each vendor must have a 'vendor' field")
        if "currency" not in item:
            raise VNXConfigurationError("Each vendor must have a 'currency' field")
            
        # Validate currency is uppercase ISO 4217 (3 uppercase letters)
        currency = item["currency"]
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isupper() or not currency.isalpha():
            raise VNXConfigurationError(f"Currency must be a 3-letter uppercase ISO 4217 code: {currency}")
            
        # Validate price fields are strings (not floats) and convert to Decimal
        for field_name in ["per_base", "per_strand", "per_order"]:
            if field_name in item:
                value = item[field_name]
                # Must be a string to preserve precision
                if not isinstance(value, str):
                    raise VNXConfigurationError(f"Price {field_name} must be a string, not {type(value).__name__}")
                try:
                    item[field_name] = Decimal(value)
                    if item[field_name] < 0:
                        raise VNXConfigurationError(f"Price {field_name} cannot be negative")
                except Exception:
                    raise VNXConfigurationError(f"Invalid price value for {field_name}: {value}")
                    
        # Validate max_strand_nt
        if "max_strand_nt" in item:
            try:
                item["max_strand_nt"] = int(item["max_strand_nt"])
                if item["max_strand_nt"] < 0:
                    raise VNXConfigurationError("max_strand_nt cannot be negative")
            except Exception:
                raise VNXConfigurationError(f"Invalid max_strand_nt value: {item.get('max_strand_nt')}")
                
        # Validate min_strands
        if "min_strands" in item:
            try:
                item["min_strands"] = int(item["min_strands"])
                if item["min_strands"] < 0:
                    raise VNXConfigurationError("min_strands cannot be negative")
            except Exception:
                raise VNXConfigurationError(f"Invalid min_strands value: {item.get('min_strands')}")
                
        # Extract only the allowed fields for VendorPrice
        vendor_kwargs = {k: v for k, v in item.items() if k in allowed_vendor_keys}
        vendors.append(VendorPrice(**vendor_kwargs))
    
    return vendors


@dataclass(frozen=True)
class Plan:
    profile: str
    input_bytes: int
    groups: int
    strands: int
    bases: int
    strand_nt: int
    data_strands: int
    parity_strands: int
    column_parity_strands: int
    metadata_strands: int
    costs: list[dict] = field(default_factory=list)
    evidence: str = "THEORETICAL (encoder count)"

    def to_dict(self) -> dict:
        """Convert the plan to a dictionary."""
        return {
            "profile": self.profile,
            "input_bytes": self.input_bytes,
            "groups": self.groups,
            "strands": self.strands,
            "bases": self.bases,
            "strand_nt": self.strand_nt,
            "data_strands": self.data_strands,
            "parity_strands": self.parity_strands,
            "column_parity_strands": self.column_parity_strands,
            "metadata_strands": self.metadata_strands,
            "costs": self.costs,
            "evidence": self.evidence
        }


def _count_v4(size: int, lay, K: int, M: int, options: DNAOptions) -> tuple[int, int, int, int, int]:
    """Strand counts of the V4 encoder for a container of ``size`` bytes.

    Returns ``(groups, data_strands, parity_strands, column_parity_strands, metadata_strands)``.

    The counts come from the encoder's own primitives, not from a re-derivation: ``group_k`` gives the number of
    source symbols of each row (the last data row may be short), the outer codec's ``symbols_for`` gives the number
    of transmitted symbols of a row, and ``Superblock.symbols`` gives the superblock strand count. Re-deriving these
    here is what made the first version of this planner wrong.
    """
    P = lay.payload_bytes
    groups = -(-size // (K * P))
    if groups == 0:
        return 0, 0, 0, 0, 0

    codec = make_outer(options.outer_code, K, M, options.lt_seed, options.lt_distribution)
    data_strands = 0
    parity_strands = 0
    for g in range(groups):
        k = group_k(size, K, P, g)
        n = codec.symbols_for(k)
        data_strands += k
        parity_strands += n - k

    ks, ms = Superblock.symbols(P)
    return groups, data_strands, parity_strands, 0, ks + ms


def _count_v6(size: int, lay, K: int, M: int, options: DNAOptions) -> tuple[int, int, int, int, int]:
    """Strand counts of the V6 encoder for a container of ``size`` bytes.

    Returns ``(groups, data_strands, parity_strands, column_parity_strands, metadata_strands)``.
    """
    P = lay.payload_bytes
    geo, _ = resolve_geometry(options, lay, K, M, size)
    ks, ms = Superblock.symbols(P)
    return (geo.G, geo.data_strands(), geo.G * geo.M, geo.parity_groups * geo.n, ks + ms)


def plan_encode(input_bytes: int, options: DNAOptions | None = None, prices: list[VendorPrice] | None = None) -> Plan:
    """Plan an encoding operation for a given input size."""
    if options is None:
        options = DNAOptions()
    if type(input_bytes) is not int or input_bytes < 0:
        raise VNXConfigurationError(f"input_bytes must be a non-negative integer, got {input_bytes!r}")

    # Get the layout profile
    lay, K, M = options.resolve()

    # The counts come from the encoder itself: the V6 geometry when the V6 outer options are on, the V4 row counts
    # otherwise. The superblock strands are counted in both.
    if options.v6:
        groups, data_strands, parity_strands, column_parity_strands, metadata_strands = _count_v6(
            input_bytes, lay, K, M, options)
    else:
        groups, data_strands, parity_strands, column_parity_strands, metadata_strands = _count_v4(
            input_bytes, lay, K, M, options)

    # Total strands and bases
    total_strands = data_strands + parity_strands + column_parity_strands + metadata_strands
    total_bases = total_strands * lay.strand_nt

    # Create the plan
    plan = Plan(
        profile=options.profile,
        input_bytes=input_bytes,
        groups=groups,
        strands=total_strands,
        bases=total_bases,
        strand_nt=lay.strand_nt,
        data_strands=data_strands,
        parity_strands=parity_strands,
        column_parity_strands=column_parity_strands,
        metadata_strands=metadata_strands
    )

    # Calculate costs if prices are provided
    if prices is not None:
        costs = []
        for vendor_price in prices:
            cost_dict = {
                "vendor": vendor_price.vendor,
                "currency": vendor_price.currency,
                "total": "0",
                "fits": True,
                "reason": None
            }
            
            # Check max_strand_nt constraint
            if vendor_price.max_strand_nt is not None and lay.strand_nt > vendor_price.max_strand_nt:
                cost_dict["fits"] = False
                cost_dict["reason"] = f"strand_nt {lay.strand_nt} exceeds maximum {vendor_price.max_strand_nt}"
            # Check min_strands constraint
            elif vendor_price.min_strands > 0 and total_strands < vendor_price.min_strands:
                cost_dict["fits"] = False
                cost_dict["reason"] = f"min_strands {vendor_price.min_strands} not met (have {total_strands})"
            else:
                # Calculate total cost
                total_cost = vendor_price.per_order
                
                if vendor_price.per_strand is not None:
                    total_cost += vendor_price.per_strand * Decimal(str(total_strands))
                
                if vendor_price.per_base is not None:
                    total_cost += vendor_price.per_base * Decimal(str(total_bases))
                
                cost_dict["total"] = str(total_cost)
            
            costs.append(cost_dict)
        
        plan = Plan(
            profile=options.profile,
            input_bytes=input_bytes,
            groups=groups,
            strands=total_strands,
            bases=total_bases,
            strand_nt=lay.strand_nt,
            data_strands=data_strands,
            parity_strands=parity_strands,
            column_parity_strands=column_parity_strands,
            metadata_strands=metadata_strands,
            costs=costs
        )
    
    return plan


def plan_file(path, options=None, prices=None) -> Plan:
    """Plan an encoding operation for a file."""
    # Get the file size
    input_bytes = Path(path).stat().st_size
    
    # Plan the encoding
    return plan_encode(input_bytes, options, prices)