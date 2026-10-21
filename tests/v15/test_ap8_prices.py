"""Tests for vendor prices (V15 Task E part 2a)."""
import os
import tempfile
from decimal import Decimal

import pytest

from vnxdna.pipeline.plan import VendorPrice, load_prices, plan_encode
from vnxdna.core.errors import VNXConfigurationError


def test_load_prices_valid_file_returns_vendorprice_objects_with_decimal():
    """Test (1): load_prices on a valid 'vnx.vendor-prices/1' JSON file returns VendorPrice objects with Decimal money."""
    prices_data = {
        "format": "vnx.vendor-prices/1",
        "vendors": [
            {
                "vendor": "vendor-a",
                "currency": "USD",
                "per_base": "0.01",
                "per_strand": "0.50",
                "per_order": "10.00",
                "max_strand_nt": 200,
                "min_strands": 10
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        import json
        json.dump(prices_data, tmp)
        tmp_path = tmp.name

    try:
        prices = load_prices(tmp_path)

        assert len(prices) == 1
        vp = prices[0]
        assert isinstance(vp, VendorPrice)
        assert vp.vendor == "vendor-a"
        assert vp.currency == "USD"
        assert isinstance(vp.per_base, Decimal)
        assert vp.per_base == Decimal("0.01")
        assert isinstance(vp.per_strand, Decimal)
        assert vp.per_strand == Decimal("0.50")
        assert isinstance(vp.per_order, Decimal)
        assert vp.per_order == Decimal("10.00")
        assert vp.max_strand_nt == 200
        assert vp.min_strands == 10
    finally:
        os.unlink(tmp_path)


def test_plan_encode_costs_calculation_exact_decimal():
    """Test (2): plan_encode(4096, prices=[...]).costs gives per vendor total = per_order + strands*per_strand + bases*per_base exactly as a Decimal string."""
    prices = [
        VendorPrice(
            vendor="vendor-a",
            currency="USD",
            per_base=Decimal("0.01"),
            per_strand=Decimal("0.50"),
            per_order=Decimal("10.00"),
        )
    ]

    plan = plan_encode(4096, prices=prices)

    assert len(plan.costs) == 1
    cost = plan.costs[0]

    # Calculate expected cost manually
    total_strands = plan.strands
    total_bases = plan.bases
    expected_total = Decimal("10.00") + Decimal("0.50") * Decimal(str(total_strands)) + Decimal("0.01") * Decimal(str(total_bases))

    assert cost["vendor"] == "vendor-a"
    assert cost["currency"] == "USD"
    assert cost["total"] == str(expected_total)
    assert cost["fits"] is True
    assert cost["reason"] is None


def test_fits_false_max_strand_nt_exceeded():
    """Test (3a): fits is False with a reason naming max_strand_nt when the strand is longer than the vendor maximum."""
    # Create a vendor price with max_strand_nt lower than typical strand_nt
    prices = [
        VendorPrice(
            vendor="vendor-limited",
            currency="USD",
            per_base=Decimal("0.01"),
            per_strand=Decimal("0.50"),
            per_order=Decimal("10.00"),
            max_strand_nt=100,  # Typical strand_nt is larger than 100
        )
    ]

    plan = plan_encode(1000, prices=prices)

    assert len(plan.costs) == 1
    cost = plan.costs[0]
    assert cost["fits"] is False
    assert cost["reason"] is not None
    assert "max_strand_nt" in cost["reason"] or "maximum" in cost["reason"]


def test_fits_false_min_strands_not_met():
    """Test (3b): fits is False with a reason naming min_strands when there are too few strands."""
    # Create a vendor price with min_strands higher than what we'd get for small input
    prices = [
        VendorPrice(
            vendor="vendor-high-min",
            currency="USD",
            per_base=Decimal("0.01"),
            per_strand=Decimal("0.50"),
            per_order=Decimal("10.00"),
            min_strands=1000,  # Very high minimum
        )
    ]

    plan = plan_encode(100, prices=prices)  # Small input -> few strands

    assert len(plan.costs) == 1
    cost = plan.costs[0]
    assert cost["fits"] is False
    assert cost["reason"] is not None
    assert "min_strands" in cost["reason"]


def test_load_prices_unknown_top_level_key():
    """Test (4a): VNXConfigurationError for unknown top-level key."""
    prices_data = {
        "format": "vnx.vendor-prices/1",
        "vendors": [],
        "unknown_key": "value"
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        import json
        json.dump(prices_data, tmp)
        tmp_path = tmp.name

    try:
        with pytest.raises(VNXConfigurationError):
            load_prices(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_load_prices_unknown_vendor_key():
    """Test (4b): VNXConfigurationError for unknown vendor key."""
    prices_data = {
        "format": "vnx.vendor-prices/1",
        "vendors": [
            {
                "vendor": "test",
                "currency": "USD",
                "unknown_vendor_key": "value"
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        import json
        json.dump(prices_data, tmp)
        tmp_path = tmp.name

    try:
        with pytest.raises(VNXConfigurationError):
            load_prices(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_load_prices_negative_price():
    """Test (4c): VNXConfigurationError for negative price."""
    prices_data = {
        "format": "vnx.vendor-prices/1",
        "vendors": [
            {
                "vendor": "test",
                "currency": "USD",
                "per_base": "-0.01"
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        import json
        json.dump(prices_data, tmp)
        tmp_path = tmp.name

    try:
        with pytest.raises(VNXConfigurationError):
            load_prices(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_load_prices_missing_currency():
    """Test (4d): VNXConfigurationError for missing currency."""
    prices_data = {
        "format": "vnx.vendor-prices/1",
        "vendors": [
            {
                "vendor": "test",
                "per_base": "0.01"
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        import json
        json.dump(prices_data, tmp)
        tmp_path = tmp.name

    try:
        with pytest.raises(VNXConfigurationError):
            load_prices(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_load_prices_lowercase_currency():
    """Test (4e): VNXConfigurationError for lowercase currency (must be ISO 4217 uppercase)."""
    prices_data = {
        "format": "vnx.vendor-prices/1",
        "vendors": [
            {
                "vendor": "test",
                "currency": "usd",  # lowercase - invalid
                "per_base": "0.01"
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        import json
        json.dump(prices_data, tmp)
        tmp_path = tmp.name

    try:
        with pytest.raises(VNXConfigurationError):
            load_prices(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_load_prices_wrong_format_string():
    """Test (4f): VNXConfigurationError for wrong format string."""
    prices_data = {
        "format": "wrong.format/1",
        "vendors": [
            {
                "vendor": "test",
                "currency": "USD",
                "per_base": "0.01"
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        import json
        json.dump(prices_data, tmp)
        tmp_path = tmp.name

    try:
        with pytest.raises(VNXConfigurationError):
            load_prices(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_load_prices_float_price():
    """Test (4g): VNXConfigurationError for a float price (must be string)."""
    prices_data = {
        "format": "vnx.vendor-prices/1",
        "vendors": [
            {
                "vendor": "test",
                "currency": "USD",
                "per_base": 0.01  # float instead of string
            }
        ]
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        import json
        json.dump(prices_data, tmp)
        tmp_path = tmp.name

    try:
        with pytest.raises(VNXConfigurationError):
            load_prices(tmp_path)
    finally:
        os.unlink(tmp_path)


def test_no_prices_costs_empty():
    """Test (5): no prices -> costs == []."""
    plan = plan_encode(4096, prices=None)

    assert plan.costs == []

    # Also test with empty list
    plan2 = plan_encode(4096, prices=[])
    assert plan2.costs == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])