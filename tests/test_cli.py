import json

from ryo_pulse.cli import main


def test_pulse_json_and_replay(demo_path, tmp_path, capsys):
    out = tmp_path / "r.json"
    assert main(["pulse", "PEPE", "--fixture", str(demo_path), "--json", "--save", str(out)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "emerging"
    assert main(["replay", str(out), "--fixture", str(demo_path)]) == 0
    assert "replay OK" in capsys.readouterr().out


def test_replay_detects_tampering(demo_path, tmp_path, capsys):
    out = tmp_path / "r.json"
    main(["pulse", "BTC", "--fixture", str(demo_path), "--save", str(out)])
    data = json.loads(out.read_text())
    data["replay_hash"] = "0" * 64
    out.write_text(json.dumps(data))
    assert main(["replay", str(out), "--fixture", str(demo_path)]) == 1


def test_board_ignores_drifted_tokens(demo_path, capsys):
    assert main(["board", "--fixture", str(demo_path), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    status = {r["token"]: r["status"] for r in rows}
    assert status == {"BTC": "persistent", "SOL": "persistent", "PEPE": "emerging", "ETH": "transient", "WIF": "transient"}


def test_skill_run_matches_engine(demo_path):
    from ryo_pulse.skill import TOOL_DEFINITION, run

    raw = json.loads(demo_path.read_text())["snapshots"]
    out = run({"token": "sol", "snapshots": raw})
    assert out["status"] == "persistent" and out["token"] == "SOL"
    assert TOOL_DEFINITION["annotations"]["readOnlyHint"] is True


def test_committed_schemas_are_current():
    from pathlib import Path

    from ryo_pulse.skill import TOOL_DEFINITION

    root = Path(__file__).parent.parent / "docs" / "schema"
    assert json.loads((root / "tool_definition.json").read_text()) == json.loads(json.dumps(TOOL_DEFINITION))
