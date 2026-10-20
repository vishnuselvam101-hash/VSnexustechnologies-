import os
import pytest

from vnxdna.native.cluster import threads
from vnxdna.core.errors import VNXConfigurationError


def test_threads_invalid_values():
    """Test that invalid VNXDNA_CLUSTER_THREADS values raise VNXConfigurationError."""
    
    # Test with monkeypatch.setenv for various invalid values
    with pytest.MonkeyPatch().context() as mp:
        # Test non-integer value "abc"
        mp.setenv("VNXDNA_CLUSTER_THREADS", "abc")
        with pytest.raises(VNXConfigurationError, match="VNXDNA_CLUSTER_THREADS"):
            threads()
        
        # Test zero value
        mp.setenv("VNXDNA_CLUSTER_THREADS", "0")
        with pytest.raises(VNXConfigurationError, match="VNXDNA_CLUSTER_THREADS"):
            threads()
            
        # Test negative value -5
        mp.setenv("VNXDNA_CLUSTER_THREADS", "-5")
        with pytest.raises(VNXConfigurationError, match="VNXDNA_CLUSTER_THREADS"):
            threads()
            
        # Test float value 3.5
        mp.setenv("VNXDNA_CLUSTER_THREADS", "3.5")
        with pytest.raises(VNXConfigurationError, match="VNXDNA_CLUSTER_THREADS"):
            threads()


def test_threads_valid_values():
    """Test that valid VNXDNA_CLUSTER_THREADS values work correctly."""
    
    with pytest.MonkeyPatch().context() as mp:
        # Test value 1 (should return 1)
        mp.setenv("VNXDNA_CLUSTER_THREADS", "1")
        assert threads() == 1
        
        # Test value 8 (should return 8)
        mp.setenv("VNXDNA_CLUSTER_THREADS", "8")
        assert threads() == 8
        
        # Test value 200 (should clamp to 64)
        mp.setenv("VNXDNA_CLUSTER_THREADS", "200")
        assert threads() == 64


def test_threads_unset():
    """Test that unset VNXDNA_CLUSTER_THREADS uses thread_scope."""
    
    with pytest.MonkeyPatch().context() as mp:
        # Unset the environment variable
        mp.delenv("VNXDNA_CLUSTER_THREADS", raising=False)
        
        # Should return 1 when thread scope is None (default)
        assert threads() == 1


def test_threads_empty():
    """Test that empty VNXDNA_CLUSTER_THREADS uses thread_scope."""
    
    with pytest.MonkeyPatch().context() as mp:
        # Set empty value
        mp.setenv("VNXDNA_CLUSTER_THREADS", "")
        
        # Should return 1 when thread scope is None (default)
        assert threads() == 1