"""Season aggregates derived only from imported official vote records."""
from __future__ import annotations

from collections import defaultdict


def season_statistics(records):
    groups = defaultdict(list)
    for row in records:
        identity = ("id", row.get("source_name", ""), row["provider_player_id"]) if row.get("provider_player_id") else (
            "name", str(row.get("name", "")).casefold(), str(row.get("team", "")).casefold()
        )
        groups[identity].append(dict(row))
    result = []
    for rows in groups.values():
        rows.sort(key=lambda row: int(row.get("matchday", 0)))
        sample = rows[-1]
        voted = [row for row in rows if row.get("official_vote") is not None]
        fantasy = [row for row in rows if row.get("fantavote") is not None]
        provider_fantasy = [row for row in rows if row.get("provider_fantavote") is not None]
        average = round(sum(float(row["fantavote"]) for row in fantasy) / len(fantasy), 2) if fantasy else None
        recent = fantasy[-3:]
        recent_average = round(sum(float(row["fantavote"]) for row in recent) / len(recent), 2) if recent else None
        trend = 0 if average in (None, 0) or len(fantasy) <= 3 else round((recent_average - average) / abs(average) * 100)
        result.append({
            "name": sample.get("name", ""), "role": sample.get("role", ""), "team": sample.get("team", ""),
            "vote_provider": sample.get("source_name", ""), "provider_player_id": sample.get("provider_player_id", ""),
            "appearances": len(voted), "days_present": len(rows),
            "average_vote": round(sum(float(row["official_vote"]) for row in voted) / len(voted), 2) if voted else None,
            "average_fantavote": average,
            "provider_average_fantavote": round(sum(float(row["provider_fantavote"]) for row in provider_fantasy) / len(provider_fantasy), 2) if provider_fantasy else None,
            "recent_average_fantavote": recent_average, "trend": trend,
            "goals": sum(int(row.get("goals", 0) or 0) for row in rows),
            "assists": sum(int(row.get("assists", 0) or 0) for row in rows),
            "yellow_cards": sum(int(row.get("yellow_cards", 0) or 0) for row in rows),
            "red_cards": sum(int(row.get("red_cards", 0) or 0) for row in rows),
        })
    return sorted(result, key=lambda row: (row["average_fantavote"] is not None, row["average_fantavote"] or 0, row["appearances"]), reverse=True)
