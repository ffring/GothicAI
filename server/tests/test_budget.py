from gothic_ai.budget import Budget

LOBART, HILDA = "BAU_950_LOBART", "BAU_951_HILDA"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _tokens(budget: Budget) -> tuple[float, float, float]:
    assert budget._total is not None
    return budget._npcs[LOBART].tokens, budget._npcs[HILDA].tokens, budget._total.tokens


def test_a_scene_takes_one_from_each_and_one_from_the_total():
    budget = Budget(per_npc_per_min=3, total_per_min=10, clock=Clock())
    assert budget.can_all((LOBART, HILDA))
    assert _tokens(budget) == (3, 3, 10)  # asking takes nothing
    assert budget.allow_all((LOBART, HILDA, LOBART))  # each npc once
    assert _tokens(budget) == (2, 2, 9)
    assert budget.allow(LOBART)
    assert _tokens(budget) == (1, 2, 8)


def test_nothing_is_taken_if_one_of_them_lacks_it():
    clock = Clock()
    budget = Budget(per_npc_per_min=2, total_per_min=10, clock=clock)
    assert budget.allow(HILDA) and budget.allow(HILDA)  # her bucket is empty
    assert not budget.can_all((LOBART, HILDA))
    assert not budget.allow_all((LOBART, HILDA))
    assert _tokens(budget) == (2, 0, 8)
    clock.now = 30.0  # a token per 30 s comes back
    assert budget.can_all((LOBART, HILDA)) and budget.allow_all((LOBART, HILDA))
    assert _tokens(budget) == (1, 0, 9)  # the total came back to full in 30 s


def test_the_total_counts_once_for_a_scene():
    budget = Budget(per_npc_per_min=5, total_per_min=2, clock=Clock())
    assert budget.allow_all((LOBART, HILDA))
    assert _tokens(budget) == (4, 4, 1)
    assert budget.allow_all([HILDA, LOBART])
    assert not budget.can_all((LOBART, HILDA)) and not budget.allow_all((LOBART, HILDA))
    assert _tokens(budget) == (3, 3, 0)
    assert not budget.can_all(()) and not budget.allow_all(())  # nobody to call for
