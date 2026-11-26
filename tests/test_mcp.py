import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cli_support import xpt

SOURCE = """\
from xpt import expect

def test_missing():
    expect(1 + 1)

def test_mismatch():
    expect("ab".upper(), "ab")

def test_broken():
    expect(None, [1])
"""


class Session:
    """An `xpt mcp` process, spoken to in raw JSON-RPC, one line per message."""

    def __init__(self, root: Path) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-m", "xpt", "mcp", "--root", str(root)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.next_id = 0

    def send(self, line: bytes) -> None:
        stdin = self.process.stdin
        assert stdin is not None
        stdin.write(line + b"\n")
        stdin.flush()

    def receive(self) -> Any:
        stdout = self.process.stdout
        assert stdout is not None
        line = stdout.readline()
        assert line.endswith(b"\n"), line
        return json.loads(line)

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(json.dumps(message).encode("utf-8"))
        response = self.receive()
        assert response["id"] == self.next_id
        return response

    def call(self, tool: str, **arguments: Any) -> Any:
        response = self.request("tools/call", {"name": tool, "arguments": arguments})
        return response["result"]

    def close(self) -> tuple[bytes, str]:
        out, err = self.process.communicate(timeout=60)
        return out, err.decode("utf-8")


def initialize(session: Session, version: str = "2025-11-25") -> Any:
    response = session.request(
        "initialize",
        {
            "protocolVersion": version,
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    )
    session.send(b'{"jsonrpc": "2.0", "method": "notifications/initialized"}')
    return response


@pytest.fixture
def project(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    (pytester.path / "test_it.py").write_text(SOURCE, encoding="utf-8")
    pytester.makeini("[pytest]\n")
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(failed=3)
    return pytester.path


@pytest.fixture
def session(project: Path) -> Iterator[Session]:
    s = Session(project)
    initialize(s)
    yield s
    s.process.kill()
    s.close()


def ids(project: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    groups = xpt(capsys, "failures", "--json", "--root", str(project)).json["groups"]
    return {g["site"]["qualname"]: g["id"] for g in groups}


def cli_json(capsys: pytest.CaptureFixture[str], *argv: str) -> Any:
    return xpt(capsys, *argv, "--json").json


def test_initialize_negotiates_the_version_and_advertises_tools(project: Path):
    s = Session(project)
    response = initialize(s)
    assert response["jsonrpc"] == "2.0"
    result = response["result"]
    assert result["protocolVersion"] == "2025-11-25"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "xpt"
    assert s.request("ping") == {"jsonrpc": "2.0", "id": 2, "result": {}}
    out, err = s.close()
    assert out == b""
    assert "xpt mcp: serving" in err


def test_an_older_version_is_kept_and_an_unknown_one_gets_the_newest(project: Path):
    s = Session(project)
    assert initialize(s, "2024-11-05")["result"]["protocolVersion"] == "2024-11-05"
    result = s.call("xpt_runs")
    assert "structuredContent" not in result
    s.close()
    s = Session(project)
    assert initialize(s, "1999-01-01")["result"]["protocolVersion"] == "2025-11-25"
    s.close()


def test_tools_list_gives_each_tool_a_json_schema(session: Session):
    tools = {t["name"]: t for t in session.request("tools/list")["result"]["tools"]}
    assert sorted(tools) == [
        "xpt_accept",
        "xpt_failures",
        "xpt_flaky",
        "xpt_reject",
        "xpt_runs",
        "xpt_show",
    ]
    assert tools["xpt_show"]["inputSchema"] == {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "A call site id from xpt_failures."},
            "run": {
                "type": "string",
                "description": "A run id from xpt_runs. Default: the latest run.",
            },
            "full": {"type": "boolean", "description": "Lift the change and length limits."},
            "since": {
                "type": "string",
                "description": "The git ref to diff the changed code against. Default: the "
                "merge base with the default branch, or HEAD.",
            },
        },
        "additionalProperties": False,
        "required": ["id"],
    }
    assert tools["xpt_reject"]["inputSchema"]["properties"]["ids"]["minItems"] == 1
    assert set(tools["xpt_accept"]["inputSchema"]["properties"]) == {
        "ids",
        "all",
        "missing",
        "dry_run",
        "force",
        "verify",
        "run",
        "since",
    }
    assert tools["xpt_failures"]["annotations"]["readOnlyHint"] is True
    assert tools["xpt_accept"]["annotations"]["readOnlyHint"] is False


@pytest.mark.parametrize(
    ("tool", "arguments", "argv"),
    [
        ("xpt_failures", {}, ["failures"]),
        ("xpt_runs", {}, ["runs"]),
        ("xpt_flaky", {}, ["flaky"]),
    ],
)
def test_read_tools_return_the_cli_json(
    session: Session,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    tool: str,
    arguments: dict[str, Any],
    argv: list[str],
):
    result = session.call(tool, **arguments)
    expected = cli_json(capsys, *argv, "--root", str(project))
    assert result["isError"] is False
    assert result["structuredContent"] == expected
    assert json.loads(result["content"][0]["text"]) == expected


def test_show_returns_the_cli_json(
    session: Session, project: Path, capsys: pytest.CaptureFixture[str]
):
    id = ids(project, capsys)["test_mismatch"]
    result = session.call("xpt_show", id=id, full=True)
    assert result["structuredContent"] == cli_json(
        capsys, "show", id, "--full", "--root", str(project)
    )
    assert result["structuredContent"]["group"]["status"] == "mismatch"


def test_accept_writes_the_source(
    session: Session, project: Path, capsys: pytest.CaptureFixture[str]
):
    found = ids(project, capsys)
    dry = session.call("xpt_accept", ids=[found["test_missing"]], dry_run=True)
    assert dry["structuredContent"]["dry_run"] is True
    assert "expect(1 + 1)\n" in (project / "test_it.py").read_text(encoding="utf-8")

    result = session.call("xpt_accept", ids=[found["test_missing"], found["test_mismatch"]])
    data = result["structuredContent"]
    assert data["command"] == "accept"
    assert [o["status"] for o in data["outcomes"]] == ["accepted", "accepted"]
    assert data["changed"] == ["test_it.py"]
    text = (project / "test_it.py").read_text(encoding="utf-8")
    assert "    expect(1 + 1, 2)\n" in text
    assert '    expect("ab".upper(), "AB")\n' in text


def test_accept_with_verify_reruns_the_tests_and_keeps_stdout_clean(
    session: Session, project: Path, capsys: pytest.CaptureFixture[str]
):
    result = session.call("xpt_accept", ids=[ids(project, capsys)["test_missing"]], verify=True)
    verify = result["structuredContent"]["verify"]
    assert verify["passed"] is True
    assert session.request("ping")["result"] == {}


def test_accept_refuses_a_stale_change(
    session: Session, project: Path, capsys: pytest.CaptureFixture[str]
):
    id = ids(project, capsys)["test_mismatch"]
    path = project / "test_it.py"
    path.write_text(SOURCE.replace('"ab")', '"abc")'), encoding="utf-8")
    result = session.call("xpt_accept", ids=[id])
    [outcome] = result["structuredContent"]["outcomes"]
    assert outcome["status"] == "stale"
    assert result["structuredContent"]["changed"] == []
    assert '"abc")' in path.read_text(encoding="utf-8")


def test_accept_refuses_a_suspect_value_unless_forced(
    session: Session, project: Path, capsys: pytest.CaptureFixture[str]
):
    id = ids(project, capsys)["test_broken"]
    [outcome] = session.call("xpt_accept", ids=[id])["structuredContent"]["outcomes"]
    assert outcome["status"] == "suspect"
    [outcome] = session.call("xpt_accept", ids=[id], force=True)["structuredContent"]["outcomes"]
    assert outcome["status"] == "accepted"


def test_reject_records_the_value(
    session: Session, project: Path, capsys: pytest.CaptureFixture[str]
):
    id = ids(project, capsys)["test_mismatch"]
    result = session.call("xpt_reject", ids=[id])
    assert [o["status"] for o in result["structuredContent"]["outcomes"]] == ["rejected"]
    failures = session.call("xpt_failures")["structuredContent"]["groups"]
    assert [g["rejected"] for g in failures if g["id"] == id] == [True]


@pytest.mark.parametrize(
    ("tool", "arguments", "message"),
    [
        ("xpt_accept", {}, "xpt accept needs ids, --missing, or --all."),
        ("xpt_show", {"id": "zzzz"}, "no group zzzz in run"),
        ("xpt_show", {}, "xpt_show needs id."),
        ("xpt_show", {"id": 7}, "id must be a string."),
        ("xpt_runs", {"extra": True}, "xpt_runs has no argument extra."),
        ("xpt_reject", {"ids": []}, "ids needs at least 1 item."),
    ],
)
def test_bad_arguments_are_tool_errors(
    session: Session, tool: str, arguments: dict[str, Any], message: str
):
    result = session.call(tool, **arguments)
    assert result["isError"] is True
    assert message in result["content"][0]["text"]


def test_protocol_errors(session: Session):
    unknown = session.request("tools/call", {"name": "xpt_nope", "arguments": {}})
    assert unknown["error"]["code"] == -32602
    assert session.request("resources/list")["error"]["code"] == -32601
    session.send(b"{not json")
    assert session.receive() == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32700, "message": session_parse_message()},
    }
    session.send(b"[1, 2]")
    assert session.receive()["error"]["code"] == -32600
    session.send(b'{"jsonrpc": "2.0", "id": 99}')
    assert session.receive()["error"]["code"] == -32600
    session.send(b'{"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}')
    assert session.request("ping")["result"] == {}


def session_parse_message() -> str:
    try:
        json.loads(b"{not json")
    except ValueError as error:
        return f"Parse error: {error}."
    raise AssertionError


@pytest.fixture
def empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ("PYTEST_ADDOPTS", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "PYTEST_PLUGINS"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


SERVER = {"command": "xpt", "args": ["mcp"]}


def test_init_mcp_creates_mcp_json(empty: Path, capsys: pytest.CaptureFixture[str]):
    ran = xpt(capsys, "init", "--mcp")
    assert ran.out.splitlines()[1] == "Created .mcp.json with the xpt server."
    config = json.loads((empty / ".mcp.json").read_text(encoding="utf-8"))
    assert config == {"mcpServers": {"xpt": SERVER}}
    data = xpt(capsys, "init", "--mcp", "--json").json
    assert data["mcp"] == {"path": ".mcp.json", "status": "present", "note": ""}


def test_init_without_mcp_leaves_mcp_json_alone(empty: Path, capsys: pytest.CaptureFixture[str]):
    assert xpt(capsys, "init", "--json").json["mcp"] is None
    assert not (empty / ".mcp.json").exists()


def test_init_mcp_merges_with_other_servers(empty: Path, capsys: pytest.CaptureFixture[str]):
    other = {"command": "other", "args": []}
    (empty / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"other": other}, "extra": 1}), encoding="utf-8"
    )
    ran = xpt(capsys, "init", "--mcp")
    assert "Added the xpt server to .mcp.json." in ran.out
    config = json.loads((empty / ".mcp.json").read_text(encoding="utf-8"))
    assert config == {"mcpServers": {"other": other, "xpt": SERVER}, "extra": 1}


def test_init_mcp_dry_run_writes_nothing(empty: Path, capsys: pytest.CaptureFixture[str]):
    ran = xpt(capsys, "init", "--mcp", "--dry-run")
    assert "Dry run, would create .mcp.json with the xpt server." in ran.out
    assert not (empty / ".mcp.json").exists()


def test_init_mcp_keeps_a_different_xpt_entry(empty: Path, capsys: pytest.CaptureFixture[str]):
    mine = '{"mcpServers": {"xpt": {"command": "uvx", "args": ["xpt", "mcp"]}}}\n'
    (empty / ".mcp.json").write_text(mine, encoding="utf-8")
    data = xpt(capsys, "init", "--mcp", "--json").json
    assert data["mcp"]["status"] == "kept"
    assert "already has an" in data["mcp"]["note"]
    assert (empty / ".mcp.json").read_text(encoding="utf-8") == mine


def test_init_mcp_refuses_malformed_json_and_writes_nothing(
    empty: Path, capsys: pytest.CaptureFixture[str]
):
    (empty / ".mcp.json").write_text("{", encoding="utf-8")
    ran = xpt(capsys, "init", "--mcp")
    assert ran.code == 2
    assert ".mcp.json is not valid JSON" in ran.err
    assert not (empty / "AGENTS.md").exists()
