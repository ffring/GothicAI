from gothic_ai.memory import MemoryStore


def test_branches_follow_saves() -> None:
    db = MemoryStore(":memory:")
    assert db.ensure_branch("A") == 0
    db.append("A", "XARDAS", "user", "Меня зовут Тестер", "день 1, 08:10")
    db.append("A", "XARDAS", "assistant", "Запомню.")
    saved_at = db.head("A")  # the game writes (A, 2) into the save
    db.append("A", "XARDAS", "user", "Это было после сохранения")
    db.append("A", "LESTER", "user", "Привет, Лестер")

    # loading the save starts branch B from (A, 2): later events are forgotten
    assert db.ensure_branch("B", "A", saved_at) == 2
    assert [e.content for e in db.load("B", "XARDAS").entries] == ["Меня зовут Тестер", "Запомню."]
    assert db.load("B", "LESTER").entries == []
    # the old branch is untouched, the new one continues after the inherited head
    assert len(db.load("A", "XARDAS").entries) == 3
    assert db.append("B", "XARDAS", "user", "Снова здесь") == 3
    # ensure_branch is idempotent (reconnects resend the session)
    assert db.ensure_branch("B", "A", saved_at) == 3

    # a branch of a branch inherits through the copy
    assert db.ensure_branch("C", "B", 3) == 3
    assert [e.seq for e in db.load("C", "XARDAS").entries] == [1, 2, 3]
    assert {b.id: b.head for b in db.branches()} == {"A": 4, "B": 3, "C": 3}


def test_summary_folds_older_messages() -> None:
    db = MemoryStore(":memory:")
    db.ensure_branch("A")
    for i in range(5):
        db.append("A", "XARDAS", "user", f"сообщение {i}")
    db.add_summary("A", "XARDAS", "Герой пять раз что-то говорил.", covers=3)
    mem = db.load("A", "XARDAS")
    assert mem.summary == "Герой пять раз что-то говорил." and mem.summary_seq == 3
    assert [e.content for e in mem.entries] == ["сообщение 3", "сообщение 4"]


def test_unknown_parent_starts_empty(tmp_path) -> None:
    db = MemoryStore(tmp_path / "m.sqlite3")
    assert db.ensure_branch("B", "LOST", 7) == 0
    db.close()
    # persisted across reopen
    db = MemoryStore(tmp_path / "m.sqlite3")
    assert [b.id for b in db.branches()] == ["B"]


def test_search_finds_older_messages_by_word_stems() -> None:
    store = MemoryStore(":memory:")
    store.ensure_branch("A")
    store.append("A", "X", "user", "Я видел ДРАКОНА у прохода!", "день 1, 10:00")
    store.append("A", "X", "assistant", "Драконы? Ты уверен?", "день 1, 10:00")
    store.append("A", "X", "user", "Принеси мне грибов", "день 1, 11:00")
    store.append("A", "X", "user", "Дракон снова прилетел", "день 2, 09:00")  # newer than asked for

    found = store.search("A", "X", "драконы", before_seq=3, limit=2)
    assert [(r.seq, r.game_time) for r in found] == [(1, "день 1, 10:00"), (2, "день 1, 10:00")]
    assert store.search("A", "X", "драконы", before_seq=0) == []
    # nothing matches: the latest older messages come back anyway (the model reads them)
    assert [r.seq for r in store.search("A", "X", "как меня зовут", before_seq=3, limit=2)] == [2, 3]
