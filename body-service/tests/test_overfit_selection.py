"""Selection coverage is a model-free check before the numerical diagnostic."""
import pytest

from gothic_body.training import select_overfit_chunks


def chunk(labels, *, burn_label=None):
    rows = [{"label": label, "loss_mask": True} for label in labels]
    if burn_label is not None:
        rows.insert(0, {"label": burn_label, "loss_mask": False})
    return {"observations": rows}


def test_overfit_fails_when_declared_chunk_limit_cannot_cover_all_heads():
    chunks = [chunk([action]) for action in range(1, 12)]
    with pytest.raises(ValueError, match="max_chunks=8.*block, wait_200, wait_300"):
        select_overfit_chunks(chunks, max_chunks=8, included_actions=list(range(1, 12)))


def test_overfit_greedy_selection_covers_every_head_without_burnin_labels():
    chunks = [chunk([1, 2, 3, 4]), chunk([5, 6, 7, 8]), chunk([9, 10, 11]), chunk([1], burn_label=11)]
    selected, support = select_overfit_chunks(chunks, max_chunks=3, included_actions=list(range(1, 12)))
    assert len(selected) == 3
    assert support == dict.fromkeys(range(1, 12), 1)


def test_burnin_only_support_cannot_satisfy_overfit_coverage():
    with pytest.raises(ValueError, match="wait_300"):
        select_overfit_chunks([chunk(list(range(1, 11)), burn_label=11)], max_chunks=1,
                              included_actions=list(range(1, 12)))
