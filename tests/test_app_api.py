"""Offline tests for the app-API tools (upload_documents / assembly_app / assembly_app_guide): a stub
assembly-api.mjs stands in for the real one (no network, no sign-in)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from assembly_mcp import server
from assembly_mcp.server import assembly_app, assembly_app_guide, upload_documents

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")

# Echoes argv as JSON; `login` always succeeds; "upload-docs … " without --yes exits 2 like a dry run.
STUB = """
const a = process.argv.slice(2);
if (a[0] === 'login') { console.log('signed in'); process.exit(0); }
if (a[0] === 'upload-docs' && !a.includes('--yes')) { console.log(JSON.stringify({dry_run: true, argv: a})); process.exit(2); }
if (a[0] === 'post' && !a.includes('--yes')) { console.error('assembly-api: would change PROD data'); process.exit(2); }
console.error('HTTP 200');
console.log(JSON.stringify({argv: a}));
"""


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "api"
    (d / "reference").mkdir(parents=True)
    (d / "assembly-api.mjs").write_text(STUB, encoding="utf-8")
    (d / "README.md").write_text("# guide\nhello", encoding="utf-8")
    (d / "reference" / "tables.md").write_text("# T\n\n## projects\n\ncols\n\n## project_products\n\nsel\n", encoding="utf-8")
    (d / "reference" / "functions.md").write_text("# F\n\n## issue_collection\n\nargs\n", encoding="utf-8")
    (d / "reference" / "storage-and-edge.md").write_text("# S\nbuckets", encoding="utf-8")
    monkeypatch.setenv("ASSEMBLY_API_DIR", str(d))
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"p@assembly.nz": "pw", "_default": "p@assembly.nz"}), encoding="utf-8")
    monkeypatch.setenv("ASSEMBLY_CREDENTIALS_FILE", str(creds))
    return tmp_path


def test_guide_and_topics(api: Path) -> None:
    assert assembly_app_guide().startswith("# guide")
    assert assembly_app_guide("projects").startswith("## projects\n")
    assert "sel" not in assembly_app_guide("projects")
    assert assembly_app_guide("issue_collection").startswith("## issue_collection")
    assert "project_products" in assembly_app_guide("product")
    assert assembly_app_guide("storage").startswith("# S")


@needs_node
def test_upload_documents_dry_run_then_confirm_walks_folders(api: Path) -> None:
    (api / "f").mkdir()
    (api / "f" / "a.pdf").write_text("x")
    (api / "f" / "Thumbs.db").write_text("x")
    (api / "b.pdf").write_text("x")
    dry = upload_documents("2610", [str(api / "f"), str(api / "b.pdf")], issue_name="DD")
    assert dry["exit"] == 2 and dry["result"]["dry_run"] is True
    argv = dry["result"]["argv"]
    assert argv[:2] == ["upload-docs", "2610"] and "--name" in argv and "DD" in argv
    assert any(x.endswith("a.pdf") for x in argv) and not any(x.endswith("Thumbs.db") for x in argv)
    done = upload_documents("2610", [str(api / "b.pdf")], confirm=True, add_to_draft_id="d1")
    assert done["ok"] and "--yes" in done["result"]["argv"] and "--draft" in done["result"]["argv"]


@needs_node
def test_assembly_app_methods_and_prod_write_guard(api: Path) -> None:
    r = assembly_app("GET", "rest/v1/projects?select=id")
    assert r["ok"] and r["result"]["argv"][:2] == ["get", "rest/v1/projects?select=id"]
    assert assembly_app("RPC", "get_project_team", {"p_project_id": "x"}, confirm=True)["result"]["argv"][:2] == ["rpc", "get_project_team"]
    with pytest.raises(RuntimeError, match="PROD"):
        assembly_app("POST", "rest/v1/x", {"a": 1})
    with pytest.raises(ValueError):
        assembly_app("PUT", "rest/v1/x")


@needs_node
def test_nobody_signed_in_says_to_ask_for_the_login(api: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (api / "empty.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ASSEMBLY_CREDENTIALS_FILE", str(api / "empty.json"))
    with pytest.raises(RuntimeError, match="save_login"):
        assembly_app("GET", "rest/v1/projects")


def test_status_report_lists_the_tools(api: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASSEMBLY_STATUS_DIR", str(api / "status"))
    server._report_status()
    [f] = list((api / "status").glob("*.json"))
    info = json.loads(f.read_text(encoding="utf-8"))
    assert "upload_documents" in info["tools"] and info["saved_login"] == "p@assembly.nz"
