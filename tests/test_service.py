from swagger.service import LABEL, _payload


def test_launch_agent_contains_no_credentials_and_restarts_failures(tmp_path):
    root = tmp_path / "repo"
    python = root / ".venv" / "bin" / "python"
    payload = _payload(root, python)
    serialized = repr(payload)
    assert payload["Label"] == LABEL
    assert payload["WorkingDirectory"] == str(root)
    assert payload["KeepAlive"] == {"SuccessfulExit": False}
    assert str(python) in payload["ProgramArguments"]
    assert "ALPACA_API_KEY" not in serialized
    assert "ROBINHOOD" not in serialized
