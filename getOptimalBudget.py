def get_optimal_compute_budget(attempt):
    """Return optimal compute budget based on attempt number"""
    base_price = 50000
    return {
        "computeUnitPriceMicroLamports": base_price * (2 ** attempt),
        "computeUnitsLimit": 400000
    }