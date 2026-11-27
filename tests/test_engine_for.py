"""The public engine-size selector."""
import pytest
from viva_maboss import engine_for


def test_picks_smallest_fitting_build():
    assert engine_for(1).__name__ == "cmaboss"          # 64-node base
    assert engine_for(64).__name__ == "cmaboss"
    assert engine_for(65).__name__ == "cmaboss_128n"
    assert engine_for(256).__name__ == "cmaboss_256n"
    assert engine_for(1024).__name__ == "cmaboss_1024n"


def test_rejects_oversized_network():
    with pytest.raises(ValueError):
        engine_for(1025)


def test_selected_build_runs_maboss():
    eng = engine_for(2)
    bnd = "node A { logic = A; rate_up = @logic ? 1 : 0; rate_down = @logic ? 0 : 1; }\n"
    cfg = "time_tick=1; max_time=1; sample_count=10; seed_pseudorandom=1; thread_count=1;\nA.is_internal=0;\n[A].istate = 1 [1], 0 [0];\n"
    res = eng.MaBoSSSim(network_str=bnd, config_str=cfg).run(True)
    assert res.get_last_nodes_probtraj() is not None
