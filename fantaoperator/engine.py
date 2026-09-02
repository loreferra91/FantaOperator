from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ScoringRules:
    goal: float = 3.0
    assist: float = 1.0
    yellow_card: float = -0.5
    red_card: float = -1.0
    own_goal: float = -2.0
    goal_conceded: float = -1.0
    penalty_saved: float = 3.0
    penalty_scored: float = 3.0
    penalty_missed: float = -3.0
    clean_sheet: float = 0.0

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None) -> "ScoringRules":
        values = values or {}
        rules = cls(**{
            field: float(values.get(field, getattr(cls(), field)))
            for field in cls.__dataclass_fields__
        })
        if not all(math.isfinite(value) for value in rules.as_dict().values()):
            raise ValueError("Bonus e malus devono essere valori finiti")
        return rules

    def as_dict(self) -> dict[str, float]:
        return {field: float(getattr(self, field)) for field in self.__dataclass_fields__}


def calculate_fantavote(
    official_vote: float | None,
    *,
    goals: int = 0,
    assists: int = 0,
    yellow_cards: int = 0,
    red_cards: int = 0,
    own_goals: int = 0,
    goals_conceded: int = 0,
    penalties_saved: int = 0,
    penalties_scored: int = 0,
    penalties_missed: int = 0,
    clean_sheet: bool = False,
    custom_bonus: float = 0.0,
    custom_malus: float = 0.0,
    rules: ScoringRules | None = None,
) -> float | None:
    # S.V. is not zero: league-specific substitutions/administrative votes are not inferred.
    if official_vote is None:
        return None
    rules = rules or ScoringRules()
    result = (
        float(official_vote)
        + (goals - penalties_scored) * rules.goal
        + penalties_scored * rules.penalty_scored
        + penalties_missed * rules.penalty_missed
        + assists * rules.assist
        + yellow_cards * rules.yellow_card
        + red_cards * rules.red_card
        + own_goals * rules.own_goal
        + goals_conceded * rules.goal_conceded
        + penalties_saved * rules.penalty_saved
        + (rules.clean_sheet if clean_sheet else 0.0)
        + float(custom_bonus)
        - abs(float(custom_malus))
    )
    return round(result, 2)


FORMATION_LIMITS: dict[str, dict[str, int]] = {
    "3-4-3": {"POR": 1, "DIF": 3, "CEN": 4, "ATT": 3},
    "3-5-2": {"POR": 1, "DIF": 3, "CEN": 5, "ATT": 2},
    "4-3-3": {"POR": 1, "DIF": 4, "CEN": 3, "ATT": 3},
    "4-4-2": {"POR": 1, "DIF": 4, "CEN": 4, "ATT": 2},
    "4-5-1": {"POR": 1, "DIF": 4, "CEN": 5, "ATT": 1},
    "5-3-2": {"POR": 1, "DIF": 5, "CEN": 3, "ATT": 2},
    "5-4-1": {"POR": 1, "DIF": 5, "CEN": 4, "ATT": 1},
}


def player_score(player: Mapping[str, object], strategy: str = "Equilibrato") -> float:
    expected = float(player.get("expected", 6.0) or 6.0)
    start_probability = float(player.get("start_probability", 75) or 75) / 100
    trend = float(player.get("trend", 0) or 0)
    risk = str(player.get("risk", "Medio"))
    risk_penalty = {"Basso": 0.0, "Medio": 0.18, "Alto": 0.42}.get(risk, 0.18)
    if strategy == "Difendi il vantaggio":
        return expected * start_probability - risk_penalty * 1.5 + trend * 0.002
    if strategy == "Cerca il bonus":
        return expected * (0.75 + start_probability * 0.25) - risk_penalty * 0.4 + trend * 0.012
    return expected * start_probability - risk_penalty + trend * 0.006


def optimize_lineup(
    players: Iterable[Mapping[str, object]], strategy: str = "Equilibrato"
) -> tuple[str, list[dict[str, object]], float]:
    pool = [dict(player) for player in players]
    best: tuple[float, str, list[dict[str, object]]] | None = None
    for formation, limits in FORMATION_LIMITS.items():
        selected: list[dict[str, object]] = []
        valid = True
        for role, count in limits.items():
            candidates = sorted(
                (p for p in pool if str(p.get("role")) == role),
                key=lambda p: player_score(p, strategy),
                reverse=True,
            )
            if len(candidates) < count:
                valid = False
                break
            selected.extend(candidates[:count])
        if not valid:
            continue
        score = sum(player_score(p, strategy) for p in selected)
        if best is None or score > best[0]:
            best = (score, formation, selected)
    if best is None:
        return "N/D", [], 0.0
    return best[1], best[2], round(sum(float(p.get("expected", 0) or 0) for p in best[2]), 2)


def compare_players(left: Mapping[str, object], right: Mapping[str, object]) -> dict[str, object]:
    left_score = player_score(left)
    right_score = player_score(right)
    delta = round(right_score - left_score, 2)
    verdict = "ACCETTA" if delta > 0.25 else "RIFIUTA" if delta < -0.25 else "NEGOZIA"
    confidence = min(94, 55 + round(abs(delta) * 16))
    return {"delta": delta, "verdict": verdict, "confidence": confidence}
