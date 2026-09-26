def throughput_mb_per_second(byte_count: int, seconds: float) -> float:
    return (byte_count / 1_000_000) / seconds if seconds > 0 else 0.0
