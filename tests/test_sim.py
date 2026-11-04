import numpy as np
import pytest

from cornercase.sim import conflicts as CF
from cornercase.sim import corridor as SIM
from cornercase.sim import signals as S
from cornercase.sim.inputs import build_inputs
from cornercase.sim.interventions import FUTURES, Changes


@pytest.fixture(scope="module")
def inputs():
    return build_inputs(use_measured=False)


def node(inputs, nid):
    return next(n for n in inputs if n.id == nid)


# ---------------------------------------------------------------- determinism / CRN
def test_deterministic(inputs):
    SIM.clear_cache()
    a = SIM.simulate(inputs)
    SIM.clear_cache()
    b = SIM.simulate(inputs)
    assert a == b


def test_trace_deterministic(inputs):
    assert SIM.trace_node(inputs, "w45", seconds=60) == SIM.trace_node(inputs, "w45", seconds=60)


def test_common_random_numbers_isolate_corners(inputs):
    """A change at one corner (no turn ban) must not move any other corner's numbers."""
    base = SIM.simulate(inputs)
    plan = {"w42": Changes(lpi_s=7, daylighting=True)}
    r = SIM.simulate(inputs, plan)
    for nid, v in r["nodes"].items():
        if nid != "w42":
            assert v == base["nodes"][nid], nid
    assert r["nodes"]["w42"] != base["nodes"]["w42"]


# ---------------------------------------------------------------- signal logic
def test_phase_windows_partition_cycle():
    p = S.SignalPlan(cycle_s=90, ave_share=0.6)
    a0, a1 = S.phase_window(p, "A")
    b0, b1 = S.phase_window(p, "B")
    assert a0 == 0 and a1 == pytest.approx(48.0)
    assert b0 == pytest.approx(a1 + S.YELLOW_ALLRED_S)
    assert b1 + S.YELLOW_ALLRED_S == pytest.approx(90)


def test_lpi_holds_vehicles_but_not_walkers():
    p = S.SignalPlan(lpi_s=7)
    (vs, _), = S.vehicle_windows(p, "A", turning=True)
    ws, we, fe = S.walk_windows(p, "E", 12.0)
    assert ws == 0 and vs == 7
    assert S.ped_state(p, "E", 12.0, np.array([3.0]))[0] == 0  # walking during the head start


def test_walk_flash_dont_sequence():
    p = S.SignalPlan()
    L = 15.0
    ws, we, fe = S.walk_windows(p, "N", L)
    assert fe - we == pytest.approx(L / S.PED_CLEARANCE_MPS)
    st = S.ped_state(p, "N", L, np.array([ws + 1, we + 0.5, fe + 1]))
    assert list(st) == [0, 1, 2]


def test_split_turns_wait_for_walk_to_clear():
    p = S.SignalPlan(split_turns=True)
    (start, end), = S.vehicle_windows(p, "A", turning=True, conflict_leg_m=10.0)
    _, _, fe = S.walk_windows(p, "E", 10.0)
    assert start >= min(fe, end - 6.0) - 1e-9


def test_exclusive_phase_no_walk_during_vehicle_phases():
    p = S.SignalPlan(excl_ped_s=20)
    for leg in "NESW":
        st = S.ped_state(p, leg, 10.0, np.arange(0, p.cycle_s - p.excl_ped_s - 0.5, 0.5))
        assert (st != 0).all()


def test_green_release_respects_red_and_headway():
    w = [(10.0, 30.0)]
    rel = S.green_release(w, 60.0, np.array([0.0, 0.5, 31.0]), 2.0)
    assert rel[0] == 10.0 and rel[1] == 12.0
    assert rel[2] == 70.0  # arrives on red, waits for next cycle's green


# ---------------------------------------------------------------- PET
def _peds(start, speed=1.0, side=0, L=10.0, against=False):
    return {"start": np.array([start], float), "speed": np.array([speed], float), "side": np.array([side]),
            "against": np.array([against]), "length": L, "leg": "E"}


def _veh(release, detect=0.0, gap=1.0, lane=0.5):
    return {"release": np.array([release], float), "arrive": np.array([release], float),
            "u_detect": np.array([detect]), "gap": np.array([gap]), "lane": np.array([lane])}


def test_pet_near_miss_when_car_squeezes_behind_walker():
    # walker leaves the zone (centre 5 m, half-width 1 m) at t = 6; driver sees and yields with a 1.0 s gap
    r = CF.pet_stream(_peds(0.0), _veh(5.0, detect=0.0, gap=1.0), dist_to_zone_m=0.0, speed_mps=5.0, p_detect=0.95)
    assert r["near_miss"] == 1
    assert r["yield_delay"][0] == pytest.approx(2.0)  # waits from 5.0 to 6.0 + 1.0


def test_pet_no_near_miss_with_wide_gap():
    r = CF.pet_stream(_peds(0.0), _veh(5.0, detect=0.0, gap=3.0), dist_to_zone_m=0.0, speed_mps=5.0, p_detect=0.95)
    assert r["near_miss"] == 0


def test_pet_serious_when_driver_misses_walker():
    r = CF.pet_stream(_peds(0.0), _veh(4.5, detect=0.99), dist_to_zone_m=0.0, speed_mps=8.0, p_detect=0.5)
    assert r["near_miss"] == 1 and r["serious"] == 1
    assert r["sev"] == pytest.approx((8.0 / 9.0) ** 2)


def test_no_walker_no_conflict():
    r = CF.pet_stream(_peds(500.0), _veh(5.0), dist_to_zone_m=0.0, speed_mps=5.0, p_detect=0.9)
    assert r["near_miss"] == 0 and r["yield_delay"][0] == 0


# ---------------------------------------------------------------- intervention directions
def risk(r, nid):
    return r["nodes"][nid]["sev_ph"]


def test_lpi_reduces_turning_conflicts(inputs):
    base = SIM.simulate(inputs)
    r = SIM.simulate(inputs, {"w42": Changes(lpi_s=7)})  # w42 has no LPI today
    b_turn = sum(v["sev"] for k, v in base["nodes"]["w42"]["by_stream"].items() if "turn" in k or "left" in k or "right" in k)
    r_turn = sum(v["sev"] for k, v in r["nodes"]["w42"]["by_stream"].items() if "turn" in k or "left" in k or "right" in k)
    assert r_turn < b_turn
    assert r["nodes"]["w42"]["veh_delay_s_per_veh"] > base["nodes"]["w42"]["veh_delay_s_per_veh"]


def test_daylighting_and_hardening_reduce_risk(inputs):
    base = SIM.simulate(inputs)
    assert risk(SIM.simulate(inputs, {"w44": Changes(daylighting=True)}), "w44") < risk(base, "w44")
    eb = next(n.id for n in inputs if n.cross_dirs == ["EB"] and n.id != "w44")
    assert SIM.simulate(inputs, {eb: Changes(hardened_centerline=True)})["nodes"][eb]["by_stream"]["ave_left"]["sev"] \
        <= base["nodes"][eb]["by_stream"]["ave_left"]["sev"]


def test_turn_ban_moves_traffic_downstream(inputs):
    plan = {"w44": Changes(turn_ban="left")}  # w44 is eastbound: left turns from southbound 7 Av
    turns = SIM.diversions(inputs, plan, FUTURES["today"])
    assert turns["w44"]["left"] < turns["w44"]["diverted_out"]
    receiver = next(k for k, v in turns.items() if v["diverted_in"] > 0)
    assert int(receiver[1:]) < 44  # downstream = further south
    base = SIM.simulate(inputs)
    r = SIM.simulate(inputs, plan)
    assert risk(r, receiver) > risk(base, receiver)
    assert r["totals"]["detour_h"] > 0


def test_weaker_compliance_leaves_more_turns(inputs):
    plan = {"w44": Changes(turn_ban="left")}
    a = SIM.diversions(inputs, plan, FUTURES["today"])["w44"]["left"]
    b = SIM.diversions(inputs, plan, FUTURES["ban_ignored"])["w44"]["left"]
    assert b > a


def test_split_phase_costs_delay(inputs):
    base = SIM.simulate(inputs)
    r = SIM.simulate(inputs, {"w42": Changes(split_phase=True)})
    assert r["nodes"]["w42"]["veh_delay_s_per_veh"] > base["nodes"]["w42"]["veh_delay_s_per_veh"]


def test_crowds_raise_risk(inputs):
    assert SIM.simulate(inputs, {}, FUTURES["holiday"])["totals"]["sev_ph"] > SIM.simulate(inputs)["totals"]["sev_ph"]


def test_inputs_never_use_crash_data(inputs):
    for n in inputs:
        for s in n.sources():
            assert "crash" not in s["source"].lower()
