def complete_roster():
    counts = {"POR": 2, "DIF": 6, "CEN": 7, "ATT": 5}
    result = []
    for role, count in counts.items():
        for index in range(count):
            result.append({
                "name": f"{role} {index}", "role": role, "team": f"Team {index}",
                "expected": 6 + index / 10, "start_probability": 80 + index,
                "risk": "Basso", "price": 5 + index, "tier": "C", "trend": index,
                "purchase_cost": 2,
            })
    return result
