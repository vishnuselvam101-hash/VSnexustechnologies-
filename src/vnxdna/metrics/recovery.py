def recovery_percent(successes: int, total: int) -> float:
    return 100 * successes / total if total else 0.0
