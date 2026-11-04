"""Street changes and stress futures, as simulator parameters.

Effect sizes are labelled assumptions (see EFFECTS); the simulator, not these
numbers, decides the outcome, because each change acts through timing and
geometry (who is in the crosswalk when the turning car arrives).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, replace
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EFFECTS = {
    "lpi": "walkers start crossing lpi_s seconds before turning cars are released; drivers see "
           "walkers already in the crosswalk more easily (+4 pts detection)",
    "hardened_centerline": "rubber curbs and posts at the centre line force a tighter, slower "
                           "left turn: turning speed x0.75, +3 pts detection (assumption)",
    "daylighting": "removing parking next to the crosswalk opens the sightline: +5 pts detection "
                   "for turning drivers, +2 pts for through drivers (assumption)",
    "turn_ban": "banned avenue turns move to the next downstream street that runs the same way; "
                "drivers comply at ban_compliance (default 90%, assumption); each diverted car "
                "adds ~40 s of detour (assumption)",
    "split_phase": "turning cars get their own interval after the parallel walk and flashing "
                   "DON'T WALK end, so turns never share the walk",
    "retime": "cycle length and avenue share of green change waits for cars and walkers; "
              "longer walker waits raise crossing against the signal",
}

CHANGE_LABELS = {
    "lpi_s": "Walk head start (LPI)",
    "turn_ban": "Turn ban",
    "hardened_centerline": "Slower turns (hardened centre line)",
    "daylighting": "Clear the corner (daylighting)",
    "split_phase": "Separate turn phase",
    "cycle_s": "Signal retiming",
    "ave_share": "Signal retiming",
}


class Changes(BaseModel):
    """One corner's changes. Fields left as None keep today's setting."""
    model_config = {"extra": "forbid"}

    lpi_s: float | None = Field(None, ge=0, le=12, description="walk head start, seconds")
    turn_ban: Literal["none", "left", "right", "both"] | None = None
    hardened_centerline: bool | None = None
    daylighting: bool | None = None
    split_phase: bool | None = None
    cycle_s: float | None = Field(None, ge=60, le=120)
    ave_share: float | None = Field(None, ge=0.45, le=0.72)

    @field_validator("lpi_s")
    @classmethod
    def _round(cls, v):
        return None if v is None else round(v, 1)

    def is_empty(self) -> bool:
        return all(v is None or v is False or v == "none" for v in self.model_dump().values())

    def describe(self) -> list[str]:
        d = []
        if self.lpi_s is not None:
            d.append(f"{CHANGE_LABELS['lpi_s']} {self.lpi_s:g} s")
        if self.turn_ban and self.turn_ban != "none":
            d.append(f"Ban {self.turn_ban} turns from 7 Av" if self.turn_ban != "both" else "Ban all turns from 7 Av")
        if self.hardened_centerline:
            d.append(CHANGE_LABELS["hardened_centerline"])
        if self.daylighting:
            d.append(CHANGE_LABELS["daylighting"])
        if self.split_phase:
            d.append(CHANGE_LABELS["split_phase"])
        if self.cycle_s is not None or self.ave_share is not None:
            parts = []
            if self.cycle_s is not None:
                parts.append(f"{self.cycle_s:g} s cycle")
            if self.ave_share is not None:
                parts.append(f"{round(self.ave_share * 100)}% green to 7 Av")
            d.append("Retime signal: " + ", ".join(parts))
        return d


@dataclass(frozen=True)
class Future:
    id: str
    label: str
    why: str
    ped_mult: float = 1.0
    veh_mult: float = 1.0
    ped_mult_nodes: tuple = ()          # ((node_id, mult), ...) overrides ped_mult
    detect_delta: float = 0.0
    walk_speed_mult: float = 1.0
    noncompliance_mult: float = 1.0
    turn_speed_mult: float = 1.0
    ban_compliance: float | None = None
    parking_compliance: float = 1.0     # share of cleared corners kept clear of parked cars
    turn_headway_add_s: float = 0.0
    through_lanes_delta: int = 0

    def ped_mult_for(self, node_id: str) -> float:
        for n, m in self.ped_mult_nodes:
            if n == node_id:
                return m
        return self.ped_mult


TODAY = Future("today", "Today (weekday 5-6 pm)", "the baseline every option is compared against")

THEATRE_NODES = tuple((f"w{n}", 2.6) for n in range(43, 51))
FUTURES = {
    f.id: f for f in [
        TODAY,
        Future("holiday", "Holiday crowds", "December shopping and tourist peak",
               ped_mult=1.7, veh_mult=1.1),
        Future("show_out", "Broadway shows let out", "theatre district empties onto 7 Av around 10:30 pm",
               ped_mult=1.15, ped_mult_nodes=THEATRE_NODES, veh_mult=0.9),
        Future("rain_night", "Rain at night", "wet glare, umbrellas, hurried walkers",
               detect_delta=-0.12, walk_speed_mult=1.1, noncompliance_mult=1.35, turn_speed_mult=0.9),
        Future("bus_lane", "New 7 Av bus lane", "one general lane becomes a bus lane; turning cars "
               "cross it and buses block sightlines",
               through_lanes_delta=-1, turn_headway_add_s=0.6, detect_delta=-0.03),
        Future("ban_ignored", "30% ignore the new rules", "weak enforcement: drivers still turn where "
               "turns are banned and park in cleared corners",
               ban_compliance=0.7, parking_compliance=0.7),
    ]
}


def future_from_dict(d: dict) -> Future:
    d = dict(d)
    d["ped_mult_nodes"] = tuple(tuple(x) for x in d.get("ped_mult_nodes", ()))
    return Future(**d)


def future_to_dict(f: Future) -> dict:
    d = asdict(f)
    d["ped_mult_nodes"] = [list(x) for x in f.ped_mult_nodes]
    return d
