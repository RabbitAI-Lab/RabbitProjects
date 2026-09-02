DEFAULT_GAP = 65535.0
REBALANCE_THRESHOLD = 1e-6


def calculate_sort_order(*, prev_order: float | None, next_order: float | None) -> float:
    if prev_order is None and next_order is None:
        return DEFAULT_GAP
    if prev_order is None:
        return next_order / 2  # type: ignore[operator]
    if next_order is None:
        return prev_order + DEFAULT_GAP
    return (prev_order + next_order) / 2


def needs_rebalance(prev_order: float, next_order: float) -> bool:
    return abs(next_order - prev_order) < REBALANCE_THRESHOLD
