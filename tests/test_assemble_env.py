import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("assemble_env", Path(__file__).resolve().parent.parent / "deploy" / "assemble_env.py")
assemble_env = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assemble_env)


def test_dash_named_parameters_are_skipped_not_written():
    lines, skipped = assemble_env.render([
        {"Name": "/trading-bot/TECH_TELEGRAM_BOT_TOKEN", "Value": "123:abc"},
        {"Name": "/trading-bot/anthropic-api-key", "Value": "sk-secret"},
        {"Name": "/trading-bot/github-token", "Value": "ghp_secret"},
        {"Name": "/trading-bot/1BAD", "Value": "x"},
    ])
    assert lines == ['TECH_TELEGRAM_BOT_TOKEN="123:abc"']
    assert skipped == ["/trading-bot/anthropic-api-key", "/trading-bot/github-token", "/trading-bot/1BAD"]
    assert not any("secret" in l for l in lines)


def test_values_are_quoted_and_escaped():
    lines, _ = assemble_env.render([
        {"Name": "/trading-bot/TECH_DATABASE_URL", "Value": "postgresql://u:p#ss w\"d@127.0.0.1/db"},
        {"Name": "/trading-bot/X", "Value": "back" + chr(92) + "slash"},
    ])
    assert lines[0] == 'TECH_DATABASE_URL="postgresql://u:p#ss w\\"d@127.0.0.1/db"'
    assert lines[1] == 'X="back' + chr(92) * 2 + 'slash"'  # one backslash in the value -> two in the file


def test_quoted_lines_round_trip_through_dotenv(tmp_path):
    from dotenv import dotenv_values
    raw = 'v#1 "q" ' + chr(92) + 'x'  # ends in a literal backslash-x
    lines, _ = assemble_env.render([{"Name": "/trading-bot/A", "Value": raw}])
    f = tmp_path / ".env"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert dotenv_values(f)["A"] == raw


def test_main_writes_file_and_reports_names_only(tmp_path, monkeypatch, capsys):
    import io
    env = tmp_path / ".env"
    env.write_text("BASE=1\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["assemble_env.py", str(env)])
    monkeypatch.setattr(sys, "stdin", io.StringIO('[{"Name":"/trading-bot/OK","Value":"v"},{"Name":"/trading-bot/bad-key","Value":"LEAK"}]'))
    assert assemble_env.main() == 0
    text = env.read_text(encoding="utf-8")
    assert 'OK="v"' in text and "LEAK" not in text and "bad-key" not in text
    err = capsys.readouterr().err
    assert "bad-key" in err and "LEAK" not in err
