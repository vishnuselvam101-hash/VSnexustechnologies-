from statistics import mean, median, stdev
def summarize(values: list[float]) -> dict[str, float]:
    if not values: return {"count": 0}
    return {"count": len(values), "mean": mean(values), "median": median(values), "stddev": stdev(values) if len(values) > 1 else 0.0, "min": min(values), "max": max(values)}
