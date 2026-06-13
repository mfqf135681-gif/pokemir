"""砖5 每街投入时序求解 — 纯逻辑单测。场景取材实战:铁臂阿童木 call 读爆、
limp→call 二次动作、raise 链、短/超 all-in、全 check 街。"""
from solver.street_solve import solve_street_contrib


def E(player, street, action, amount, stk_b=None):
    return (player, street, action, amount, stk_b)


def test_tiezhi_call_read_blowup():
    # d10423c1 实案:鼠鼠宝 raise 100,铁臂 call(真106)后 6 帧读爆 1/19018/644798/311/1030/89.6
    # → call 锁定到最高注 100,误读全部无视;铁臂 preflop = 100(非 644798)
    evs = [
        E("鼠鼠宝", "preflop", "raise", 100, 1033),
        E("铁臂", "preflop", "call", 106, 2519),
        E("铁臂", "preflop", "call", 1, 2519),
        E("铁臂", "preflop", "call", 19018, 2519),
        E("铁臂", "preflop", "call", 644798, 2519),
        E("铁臂", "preflop", "call", 89.6, 2519),
    ]
    out = solve_street_contrib(evs)
    assert out[("铁臂", "preflop")] == 100.0      # 锁定最高注,爆值全杀
    assert out[("鼠鼠宝", "preflop")] == 100.0


def test_limp_then_call_raise():
    # limp 跟到 bb,后面有人 raise,再 call → to-amount 跟到最高注(单调累计)
    evs = [
        E("BB", "preflop", "post_bb", 4, 200),
        E("A", "preflop", "call", 4, 200),        # limp
        E("B", "preflop", "raise", 30, 200),
        E("A", "preflop", "call", 30, 196),       # 跟到 30
    ]
    out = solve_street_contrib(evs)
    assert out[("A", "preflop")] == 30.0          # 最终跟到最高注 30
    assert out[("B", "preflop")] == 30.0


def test_raise_chain_monotonic():
    # 加注链 30→90→240,各 raiser 投入 = 自己设的最高注
    evs = [
        E("A", "preflop", "raise", 30, 500),
        E("B", "preflop", "raise", 90, 500),
        E("C", "preflop", "raise", 240, 500),
        E("A", "preflop", "call", 240, 470),      # A 跟到 240
    ]
    out = solve_street_contrib(evs)
    assert out[("A", "preflop")] == 240.0
    assert out[("C", "preflop")] == 240.0


def test_raise_readblowup_ignored():
    # raise 读爆(超栈)→ 忽略读数,但该座至少跟到当前 max
    evs = [
        E("A", "preflop", "raise", 50, 500),
        E("B", "preflop", "raise", 99999, 300),   # 读爆 > 栈 → 忽略,B 至少跟到 50
    ]
    out = solve_street_contrib(evs)
    assert out[("B", "preflop")] == 50.0          # 不被 99999 污染
    assert out[("A", "preflop")] == 50.0


def test_short_allin():
    # 短码 all-in:投入 = 行动前栈(< 最高注)
    evs = [
        E("A", "flop", "bet", 200, 1000),
        E("B", "flop", "all_in", None, 80),       # 短码全推 80
    ]
    out = solve_street_contrib(evs)
    assert out[("B", "flop")] == 80.0
    assert out[("A", "flop")] == 200.0


def test_over_allin_sets_max():
    # 超额 all-in:投入 = 栈,且抬高最高注
    evs = [
        E("A", "turn", "bet", 100, 5000),
        E("B", "turn", "all_in", None, 900),      # 全推 900 > 100
        E("A", "turn", "call", 999, 4900),        # A 跟到 900(call 锁定 max=900)
    ]
    out = solve_street_contrib(evs)
    assert out[("B", "turn")] == 900.0
    assert out[("A", "turn")] == 900.0


def test_all_check_street():
    # 全过牌街:无投入
    evs = [E("A", "flop", "check", None, 500), E("B", "flop", "check", None, 500)]
    assert solve_street_contrib(evs) == {}


def test_ante_excluded():
    # ante 不进 street_contrib(由 build_facts 单列)
    evs = [E("A", "preflop", "post_ante", 4, 200), E("A", "preflop", "call", 4, 196)]
    out = solve_street_contrib(evs)
    # A 只有 call 到 0(无人 raise,cur_max=0)→ 不产 contrib;ante 不计
    assert ("A", "preflop") not in out or out[("A", "preflop")] == 0.0
