"""Season aggregates derived only from imported official vote records."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict


def normalized_name(value):
    value = str(value or "").casefold().translate(str.maketrans({
        "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe", "ß": "ss",
    }))
    value = unicodedata.normalize("NFKD", value)
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _stat_candidates(directory_row, statistics):
    team = normalized_name(directory_row.get("team"))
    full = normalized_name(directory_row.get("name"))
    result = []
    for stat in statistics:
        if normalized_name(stat.get("team")) != team:
            continue
        short = normalized_name(stat.get("name"))
        if short == full:
            result.append(stat)
            continue
        parts = short.split()
        if len(parts) == 1 and (full == short or full.endswith(" " + short)):
            result.append(stat)
            continue
        if len(parts) >= 2 and len(parts[-1]) == 1:
            surname = " ".join(parts[:-1])
            full_parts = full.split()
            if full == surname or (full.endswith(" " + surname) and full_parts[0].startswith(parts[-1])):
                result.append(stat)
    return result


def merge_player_catalog(directory, statistics):
    """Merge the directory and vote feeds without discarding either source."""
    directory, statistics = [dict(row) for row in directory], [dict(row) for row in statistics]
    if not directory:
        return sorted(({**row, "directory_provider": ""} for row in statistics),
                      key=lambda row: (normalized_name(row.get("team")), row.get("role", ""), normalized_name(row.get("name"))))
    result = []
    candidate_lists = [_stat_candidates(player, statistics) for player in directory]
    candidate_counts = defaultdict(int)
    referenced_statistics = set()
    for matches in candidate_lists:
        for stat in matches:
            candidate_counts[id(stat)] += 1
            referenced_statistics.add(id(stat))
    for player, matches in zip(directory, candidate_lists):
        row = {**player, "directory_provider": player.get("provider", "Diretta.it")}
        if len(matches) == 1 and candidate_counts[id(matches[0])] == 1:
            stat = matches[0]
            # The complete name, role and current club remain authoritative from Diretta.
            row.update({key: value for key, value in stat.items() if key not in {"name", "role", "team"}})
        else:
            row.update({"vote_provider": "", "provider_player_id": "", "appearances": 0,
                        "days_present": 0, "average_vote": None, "average_fantavote": None,
                        "provider_average_fantavote": None, "recent_average_fantavote": None,
                        "trend": 0, "goals": 0, "assists": 0, "yellow_cards": 0, "red_cards": 0})
        result.append(row)
    # A newly transferred player can appear in the vote feed before the public
    # directory is updated. Keep that stable provider identity available rather
    # than making the player disappear from auction and market screens.
    result.extend({**stat, "directory_provider": ""} for stat in statistics
                  if id(stat) not in referenced_statistics)
    return sorted(result, key=lambda row: (normalized_name(row.get("team")), row.get("role", ""), normalized_name(row.get("name"))))


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
