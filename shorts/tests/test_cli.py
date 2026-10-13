import json

from gothic_shorts.cli import _summary


def test_the_summary_survives_a_byte_of_the_game_code_page(tmp_path, capsys):
    events = [
        {"t": 0, "seg": "main", "frame": 0, "kind": "rec", "fps": 30},
        {"t": 0, "seg": "main", "frame": 30, "kind": "npc_line", "npc": "XARDAS", "name": "Ксардас",
         "text": "Уходи.", "ms": 1000},
        {"t": 0, "seg": "main", "frame": 60, "kind": "rec_stop", "frames": 60},
    ]
    (tmp_path / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
    # "speak Ксардас" as an old engine wrote it: the name in cp1251
    (tmp_path / "bench.log").write_bytes("speak Ксардас DIA_XARDAS_HELLO_14_08\nrealtime: behind 0 ms\n"
                                         .encode("cp1251"))
    _summary(tmp_path)
    out = capsys.readouterr().out
    assert "Ксардас: Уходи." in out
    assert "bench: realtime: behind 0 ms" in out
