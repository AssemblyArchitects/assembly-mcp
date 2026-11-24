"""Assembly MCP server.

Exposes these tools over stdio:
  * upload_documents — files / folders → a DRAFT issue on a project's Documents (Issued) tab, one call
  * assembly_app / assembly_app_guide — read or change anything in the app as the signed-in person,
    and the guide to it (wraps P:/assembly-app-skills/api/assembly-api.mjs, published from the app repo)
  * convert_pdftomd — PDF → Markdown (wraps the assembly-app pdf2md CLI, pdf.js)
  * convert_mdtopdf — Markdown → PDF (WeasyPrint)
  * upload_issue / issue_draft — files → a draft issue on a project's Documents tab, then issue it
    (wraps the assembly-app upload-issues CLI)
  * upload_renders — photos / renders → a project's Images tab, tagged (wraps the assembly-app upload-renders CLI)
  * dwg_to_geomap — a Revit DWG → lines on a project's Site → Revit Maps tab (wraps the assembly-app
    upload-dwg-geomap CLI; the DWG is converted by civil-map-service)
  * product_catalogue / find_products / upload_product / upload_products — products (with variants,
    photos and spec-sheet PDFs) → the person's org product library, adding to a product that's already
    there instead of duplicating it, optionally selected on a project (wraps the assembly-app
    upload-product CLI)

Run with:  assembly-mcp        (installed script)
      or:   python -m assembly_mcp.server
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .md_to_pdf import markdown_to_pdf

mcp = FastMCP("assembly")


def _app_dir() -> Path:
    """The assembly-app checkout whose pdf2md CLI is THE converter (env ASSEMBLY_APP_DIR, else the
    sibling `assembly-app` next to this repo)."""
    env = os.environ.get("ASSEMBLY_APP_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[3] / "assembly-app"


def _cli_dir() -> Path:
    """Where the self-contained CLI bundles live (`cli/bin/*.mjs`, built by `cli/build.mjs`; env
    ASSEMBLY_CLI_DIR overrides). With these present no assembly-app checkout is needed."""
    env = os.environ.get("ASSEMBLY_CLI_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[2] / "cli" / "bin"


def _cli_command(name: str) -> tuple[list[str], Path]:
    """(node argv prefix, cwd) to run the assembly-app CLI `name` (e.g. "upload-issues"): the bundled
    `<name>.mjs` when present, else `apps/frontend/scripts/<name>.ts` in an assembly-app checkout.
    A checkout's `apps/frontend/.env.local` is still loaded when one exists (for --env dev)."""
    node = shutil.which("node")
    if node is None:
        raise FileNotFoundError("node (>=22) not found on PATH — required for the assembly-app CLIs")
    frontend = _app_dir() / "apps" / "frontend"
    env_file = frontend / ".env.local"
    env_args = [f"--env-file-if-exists={env_file}"] if env_file.exists() else []
    bundled = _cli_dir() / f"{name}.mjs"
    if bundled.exists():
        return [node, *env_args, "--no-warnings", str(bundled)], bundled.parent
    script = frontend / "scripts" / f"{name}.ts"
    if script.exists():
        return [node, *env_args, "--experimental-strip-types", "--no-warnings", str(script)], frontend
    raise FileNotFoundError(
        f"{name} not found: no bundle at {bundled} (run `npm ci && npm run build` in cli/) and no "
        f"{script} — set ASSEMBLY_APP_DIR to an assembly-app checkout that has it"
    )


@mcp.tool()
def convert_pdftomd(
    pdf_path: str,
    output_path: str | None = None,
    strip_watermarks: bool = True,
    front_matter: bool = True,
) -> str:
    """Convert a PDF file to Markdown.

    Runs the assembly-app pdf2md converter (pdf.js: headings, lists, ruled
    tables, images saved beside the .md in `<stem>_images/`). `strip_watermarks`
    / `front_matter` are always on. When `output_path` is omitted, the Markdown
    and any extracted images are written to a new temp folder; the returned
    summary line names it — pass `output_path` to control the location.

    Args:
        pdf_path: Path to the source `.pdf` file.
        output_path: Optional path to also write the Markdown to (`.md`). If
            omitted, a fresh temp directory is used (named in the returned
            summary line).
        strip_watermarks: Kept for compatibility; always on in the unified converter.
        front_matter: Kept for compatibility; always on in the unified converter.

    Returns:
        The converted Markdown as a string, prefixed with a summary comment.
    """
    cmd, cwd = _cli_command("pdf2md")
    out = Path(output_path) if output_path else Path(tempfile.mkdtemp(prefix="pdf2md-")) / (Path(pdf_path).stem + ".md")
    proc = subprocess.run(
        [*cmd, str(Path(pdf_path).resolve()), str(out.resolve())],
        cwd=cwd, capture_output=True, text=True, timeout=600,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"pdf2md exited {proc.returncode}")
    summary = json.loads(proc.stdout.strip().splitlines()[-1])
    markdown = out.read_text(encoding="utf-8")
    return (
        f"<!-- pdf2md: {summary['pages']} pages, {summary['tables']} tables, {summary['images']} images; "
        f"written to {summary['output']} -->\n" + markdown
    )


@mcp.tool()
def convert_mdtopdf(
    output_path: str,
    md_path: str | None = None,
    markdown_text: str | None = None,
    title: str | None = None,
    css: str | None = None,
) -> str:
    """Convert Markdown to a PDF file.

    Renders Markdown → HTML → PDF with WeasyPrint (the Assembly MD→PDF stack)
    using a clean print stylesheet. Provide EITHER `md_path` OR `markdown_text`.
    A PDF is binary, so `output_path` is required.

    Args:
        output_path: Path to write the resulting `.pdf` (required).
        md_path: Path to a source `.md` file.
        markdown_text: Raw Markdown string (alternative to `md_path`).
        title: Optional document title (falls back to front-matter `title`).
        css: Optional CSS to replace the built-in print stylesheet.

    Returns:
        A confirmation string with the output path, byte size, and page count.
    """
    if md_path and markdown_text:
        raise ValueError("Provide only one of md_path or markdown_text, not both.")
    base_url: str | None = None
    if md_path:
        src = Path(md_path)
        if not src.exists():
            raise FileNotFoundError(f"Markdown file not found: {src}")
        markdown_text = src.read_text(encoding="utf-8")
        base_url = str(src.parent)
    if not markdown_text:
        raise ValueError("Provide either md_path or markdown_text.")

    result = markdown_to_pdf(
        markdown_text=markdown_text,
        output_path=output_path,
        title=title,
        css=css,
        base_url=base_url,
    )
    return (
        f"Wrote {result['bytes']:,} bytes to {result['output_path']} "
        f"({result['pages']} page(s))."
    )


def _run_upload_cli(script_name: str, args: list[str], child_env: dict[str, str] | None = None) -> dict:
    """Run an assembly-app upload CLI (`apps/frontend/scripts/<script_name>`) and return its
    `RESULT {json}` line. The CLI signs in AS the person (`--as <email>`); its password comes from
    ASSEMBLY_PASSWORD or ~/.assembly/credentials.json — never from a tool argument. Runs the bundled
    CLI (no checkout needed). Dev config comes from ASSEMBLY_DEV_SUPABASE_* (see `_dev_cli_env`)."""
    cmd, cwd = _cli_command(script_name.removesuffix(".ts"))
    proc = subprocess.run(
        [*cmd, *args],
        cwd=cwd, env=_dev_cli_env(child_env if child_env is not None else dict(os.environ)), capture_output=True, text=True, timeout=1800,
        encoding="utf-8", errors="replace",
    )
    result_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    if not result_lines:
        raise RuntimeError(proc.stderr.strip() or f"{script_name} exited {proc.returncode} without a result")
    result = json.loads(result_lines[-1][len("RESULT "):])
    if result.get("ok") is False and "error" in result:
        raise RuntimeError(result["error"])
    return result


def _env_arg(env: str) -> list[str]:
    if env not in ("prod", "dev"):
        raise ValueError('env must be "prod" or "dev"')
    return ["--env", env]


# ── Saved login ──────────────────────────────────────────────────────────────────────────────────
# One file per machine, shared with the CLIs (upload/common.ts reads the same file):
#   {"_default": "<email>", "<email>": "<password>", ...}
# `_default` is who /upload acts as when no one else is named — so it asks once, never again.

_DEFAULT_KEY = "_default"


def _credentials_path() -> Path:
    env = os.environ.get("ASSEMBLY_CREDENTIALS_FILE")
    return Path(env) if env else Path.home() / ".assembly" / "credentials.json"


def _read_credentials() -> dict:
    try:
        data = json.loads(_credentials_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _saved_password(email: str) -> str | None:
    want = email.strip().lower()
    for k, v in _read_credentials().items():
        if k != _DEFAULT_KEY and k.strip().lower() == want and isinstance(v, str) and v:
            return v
    return None


def _resolve_uploader(uploader_email: str | None) -> str:
    """The named person, else the saved default; raises a 'run save_login' error when neither."""
    if uploader_email and uploader_email.strip():
        return uploader_email.strip()
    default = _read_credentials().get(_DEFAULT_KEY)
    if isinstance(default, str) and default:
        return default
    raise RuntimeError("no saved login — ask for the person's app email + password once and call save_login")


def _dev_cli_env(child: dict[str, str]) -> dict[str, str]:
    """The CLIs read dev config as VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY. When the MCP server has
    ASSEMBLY_DEV_SUPABASE_URL / ASSEMBLY_DEV_SUPABASE_ANON_KEY (set by the P: installer), hand them over
    under those names, so `env="dev"` needs no assembly-app checkout either. Real env beats an
    `--env-file`, so these also win over a checkout's `.env.local`."""
    url, key = os.environ.get("ASSEMBLY_DEV_SUPABASE_URL"), os.environ.get("ASSEMBLY_DEV_SUPABASE_ANON_KEY")
    if url and key:
        child = {**child, "VITE_SUPABASE_URL": url, "VITE_SUPABASE_ANON_KEY": key}
    return child


def _supabase_config(env: str) -> tuple[str, str]:
    """(url, anon key) the CLIs would use: prod from ASSEMBLY_SUPABASE_* (URL defaults to
    newapi-next), dev from ASSEMBLY_DEV_SUPABASE_*, else a checkout's apps/frontend/.env.local."""
    if env == "prod":
        url = os.environ.get("ASSEMBLY_SUPABASE_URL", "https://newapi-next.assembly.nz")
        key = os.environ.get("ASSEMBLY_SUPABASE_ANON_KEY") or os.environ.get("ASSEMBLY_PROD_ANON_KEY")
        if not key:
            raise RuntimeError("ASSEMBLY_SUPABASE_ANON_KEY is not set for the assembly MCP server")
        return url, key
    dev_url, dev_key = os.environ.get("ASSEMBLY_DEV_SUPABASE_URL"), os.environ.get("ASSEMBLY_DEV_SUPABASE_ANON_KEY")
    if dev_url and dev_key:
        return dev_url, dev_key
    vals: dict[str, str] = {}
    env_file = _app_dir() / "apps" / "frontend" / ".env.local"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    url, key = vals.get("VITE_SUPABASE_URL"), vals.get("VITE_SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            f"dev config not found: set ASSEMBLY_DEV_SUPABASE_URL + ASSEMBLY_DEV_SUPABASE_ANON_KEY (the P: "
            f"installer does) or have an assembly-app checkout with {env_file}"
        )
    return url, key


def _check_sign_in(email: str, password: str, env: str) -> None:
    """Sign in once against the app's auth endpoint; raises with the server's message on failure."""
    import httpx

    url, key = _supabase_config(env)
    r = httpx.post(
        f"{url.rstrip('/')}/auth/v1/token?grant_type=password",
        headers={"apikey": key, "Content-Type": "application/json"},
        json={"email": email, "password": password},
        timeout=30,
    )
    if r.status_code != 200:
        try:
            body = r.json()
            msg = body.get("error_description") or body.get("msg") or body.get("message") or r.text
        except ValueError:
            msg = r.text
        raise RuntimeError(f"sign-in failed for {email}: {msg}")


@mcp.tool()
def save_login(email: str, password: str, env: str = "prod", make_default: bool = True) -> dict:
    """Save a person's app login on this machine so /upload never asks again.

    Checks the email + password by signing in first; saves only if that works. By default also makes
    them the default uploader (who /upload acts as when no one else is named). Never echo the
    password back to the person.

    Args:
        email: Their app account email.
        password: Their app password.
        env: Which backend to check the login against: "prod" (default) or "dev".
        make_default: Make this person the default uploader (default true).

    Returns:
        {ok, email, default, saved_to}
    """
    _env_arg(env)
    email = email.strip()
    if "@" not in email or not password:
        raise ValueError("an email and a password are required")
    _check_sign_in(email, password, env)
    creds = {k: v for k, v in _read_credentials().items() if k.strip().lower() != email.lower() or k == _DEFAULT_KEY}
    creds[email] = password
    if make_default or not creds.get(_DEFAULT_KEY):
        creds[_DEFAULT_KEY] = email
    path = _credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(creds, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return {"ok": True, "email": email, "default": creds[_DEFAULT_KEY], "saved_to": str(path)}


@mcp.tool()
def saved_login() -> dict:
    """Who /upload will act as on this machine — call this FIRST. Never returns passwords.

    Returns:
        {default: "<email>" | null, people: ["<email>", ...]} — default null means nobody is saved
        yet: ask once for their app email + password and call `save_login`.
    """
    creds = _read_credentials()
    default = creds.get(_DEFAULT_KEY)
    people = [k for k, v in creds.items() if k != _DEFAULT_KEY and isinstance(v, str) and v]
    return {"default": default if isinstance(default, str) and default else None, "people": people}


def _uploader_env(email: str) -> dict[str, str]:
    """The child env for a CLI run as `email`: its saved password as ASSEMBLY_PASSWORD, so a stale
    ASSEMBLY_PASSWORD for someone else can never be used for this person."""
    child = dict(os.environ)
    pw = _saved_password(email)
    if pw:
        child["ASSEMBLY_PASSWORD"] = pw
    return child


@mcp.tool()
def upload_issue(
    project: str,
    issue_type: str,
    issue_name: str,
    files: list[str],
    env: str = "prod",
    dry_run: bool = False,
    uploader_email: str | None = None,
) -> dict:
    """Upload files to an assembly-app project as a DRAFT issue (a Transmittal on the Documents tab).

    Signs in as the person uploading, so it can only reach projects they are on. Never issues — call
    `issue_draft` with the returned `collectionId` once the person confirms. Run with `dry_run=True`
    first to check the project, issue type and file list without writing anything.

    Args:
        project: The project's number (e.g. "2610") or UUID.
        issue_type: The issue purpose, e.g. "For Construction", "For Information", "Review".
        issue_name: The issue's name as it should appear on the Documents tab.
        files: File and/or folder paths (folders are walked).
        env: "prod" (app.assembly.nz, default) or "dev" (dev.assembly.nz).
        dry_run: Show the plan only; no writes.
        uploader_email: Who is uploading — omit to use the saved default (see `saved_login`).

    Returns:
        {ok, uploader, project, issueType, name, collectionId, status: "draft", uploaded, failed,
        recipientChoices: [{organisation, people}]} — `recipientChoices` is who it can be issued to.
    """
    who = _resolve_uploader(uploader_email)
    args = ["--as", who, "--project", project, "--type", issue_type, "--name", issue_name]
    for f in files:
        args += ["--file", str(Path(f).expanduser().resolve())]
    args += _env_arg(env)
    if dry_run:
        args.append("--dry-run")
    return _run_upload_cli("upload-issues.ts", args, _uploader_env(who))


@mcp.tool()
def list_drafts(project: str, env: str = "prod", uploader_email: str | None = None) -> dict:
    """List a project's DRAFT issues (not yet issued) on its Documents tab, newest first — use it to
    find the draft to add files to with `add_to_draft`.

    Args:
        project: The project's number (e.g. "2610") or UUID.
        env: "prod" (default) or "dev".
        uploader_email: Who is asking — omit to use the saved default.

    Returns:
        {ok, project: {id, name}, drafts: [{collectionId, name, createdAt, files}]}
    """
    who = _resolve_uploader(uploader_email)
    args = ["--drafts", "--as", who, "--project", project, *_env_arg(env)]
    return _run_upload_cli("upload-issues.ts", args, _uploader_env(who))


@mcp.tool()
def add_to_draft(
    collection_id: str,
    files: list[str],
    env: str = "prod",
    dry_run: bool = False,
    uploader_email: str | None = None,
) -> dict:
    """Add files to an existing DRAFT issue (appended after its current files). Refuses anything
    already issued — issued sets are a record and are never edited. Run with `dry_run=True` first.

    Args:
        collection_id: The draft's `collectionId` (from `upload_issue` or `list_drafts`).
        files: File and/or folder paths (folders are walked).
        env: "prod" (default) or "dev" — must match the draft's.
        dry_run: Show the plan only; no writes.
        uploader_email: Who is uploading — omit to use the saved default.

    Returns:
        {ok, project, collectionId, name, status: "draft", existingFiles, uploaded, failed}
    """
    who = _resolve_uploader(uploader_email)
    args = ["--add-to", collection_id, "--as", who]
    for f in files:
        args += ["--file", str(Path(f).expanduser().resolve())]
    args += _env_arg(env)
    if dry_run:
        args.append("--dry-run")
    return _run_upload_cli("upload-issues.ts", args, _uploader_env(who))


@mcp.tool()
def issue_draft(
    collection_id: str,
    issue_type: str,
    recipients: list[str] | None = None,
    cover_notes: str | None = None,
    env: str = "prod",
    uploader_email: str | None = None,
) -> dict:
    """Issue a draft created by `upload_issue` — allocates the next issue number. Irreversible:
    only call after the person has confirmed the issue type and recipients.

    Args:
        collection_id: `collectionId` returned by `upload_issue`.
        issue_type: The issue purpose (e.g. "For Construction").
        recipients: People ("Jane Smith") and/or organisations ("Smith Engineering" = all its
            members) from the draft's `recipientChoices`. May be empty.
        cover_notes: Optional transmittal cover notes.
        env: "prod" (default) or "dev" — must match the draft's.
        uploader_email: The person who created the draft — omit to use the saved default.

    Returns:
        {ok, collectionId, name, status: "issued", issueNumber, issueType, recipients}
    """
    who = _resolve_uploader(uploader_email)
    args = ["--issue", collection_id, "--as", who, "--type", issue_type]
    for r in recipients or []:
        args += ["--to", r]
    if cover_notes:
        args += ["--notes", cover_notes]
    args += _env_arg(env)
    return _run_upload_cli("upload-issues.ts", args, _uploader_env(who))


@mcp.tool()
def upload_renders(
    project: str,
    files: list[str],
    tags: list[str] | None = None,
    title: str | None = None,
    env: str = "prod",
    dry_run: bool = False,
    uploader_email: str | None = None,
) -> dict:
    """Upload photos / renders to an assembly-app project's Images tab (was "Renders"), tagged, as the
    person uploading.

    Non-images are skipped and reported (HEIC too — only Safari can show it; convert to JPEG first). An
    image already on the tab (same file name) is not uploaded again; when `tags` are given it gets any
    of them it lacks ADDED, so re-running with tags tags images that are already there.

    Args:
        project: The project's number (e.g. "2610") or UUID.
        files: Image and/or folder paths (folders are walked).
        tags: Tags for every image — the built-ins "Photos" / "Renders", or any other name (≤ 40
            characters), which makes a new tag on the project. Matching is case-insensitive and reuses
            the project's spelling. Omit = ["Renders"].
        title: Name for the render — only when uploading exactly one image (default: file name).
        env: "prod" (app.assembly.nz, default) or "dev" (dev.assembly.nz).
        dry_run: Show the plan only; no writes.
        uploader_email: Who is uploading — omit to use the saved default (see `saved_login`).

    Returns:
        {ok, uploader, project, tags, uploaded | toUpload, alreadyThere, tagged | toTag,
         skippedNotImages, failed}
    """
    who = _resolve_uploader(uploader_email)
    args = ["--as", who, "--project", project]
    for f in files:
        args += ["--file", str(Path(f).expanduser().resolve())]
    for t in tags or []:
        args += ["--tag", t]
    if title:
        args += ["--title", title]
    args += _env_arg(env)
    if dry_run:
        args.append("--dry-run")
    return _run_upload_cli("upload-renders.ts", args, _uploader_env(who))


@mcp.tool()
def upload_artifact(
    project: str,
    folder: str,
    title: str | None = None,
    description: str | None = None,
    source_url: str | None = None,
    entry: str = "index.html",
    env: str = "prod",
    dry_run: bool = False,
    uploader_email: str | None = None,
) -> dict:
    """Put an interactive HTML artifact (e.g. a claude.ai artifact saved to a folder) on an
    assembly-app project's Artifacts tab, as the person uploading.

    The folder is uploaded as-is (the page refers to its files by relative path) and opened in the
    app in a sandboxed frame. Re-uploading with the same `source_url` on the same project REPLACES
    that artifact's files and keeps its link, so a link already sent to a client keeps working.

    Args:
        project: The project's number (e.g. "2610"), UUID or name.
        folder: Local folder holding the artifact's entry page and its supporting files.
        title: Name shown on the Artifacts tab (default: the entry file's name).
        description: One line under the title (optional).
        source_url: The claude.ai artifact link it came from — the key for replacing it later.
        entry: The page to open, relative to the folder (default "index.html").
        env: "prod" (app.assembly.nz, default) or "dev" (dev.assembly.nz).
        dry_run: Show the plan only (files, size, create vs replace); no writes.
        uploader_email: Who is uploading — omit to use the saved default (see `saved_login`).

    Returns:
        {ok, uploader, project, mode: "create"|"replace", fileCount, sizeBytes,
         files (dry run) | artifact + viewUrl (the link anyone can open)}
    """
    who = _resolve_uploader(uploader_email)
    args = ["--as", who, "--project", project, "--dir", str(Path(folder).expanduser().resolve())]
    if title:
        args += ["--title", title]
    if description is not None:
        args += ["--description", description]
    if source_url:
        args += ["--source-url", source_url]
    if entry != "index.html":
        args += ["--entry", entry]
    args += _env_arg(env)
    if dry_run:
        args.append("--dry-run")
    return _run_upload_cli("upload-artifact.ts", args, _uploader_env(who))


@mcp.tool()
def dwg_to_geomap(
    project: str,
    file: str,
    name: str | None = None,
    env: str = "prod",
    dry_run: bool = False,
    uploader_email: str | None = None,
) -> dict:
    """Put a Revit DWG's linework on an assembly-app project's Site → Revit Maps tab, as the person
    uploading.

    The DWG is converted server-side (civil-map-service): its coordinate system (NZTM or the NZGD2000
    meridional circuit nearest the project's site) and units are detected automatically. Each
    drawing is stored by name; uploading the same name again REPLACES that drawing. Run with
    `dry_run=True` first: it converts the DWG and reports what would be stored, writing nothing.

    Args:
        project: The project's number (e.g. "2610"), UUID, or name (case-insensitive; an ambiguous
            name fails and lists the matching projects).
        file: Path to the .dwg (export from Revit with "Coordinate system basis: Shared").
        name: The drawing's name on the Revit Maps tab (default: the file name without .dwg).
        env: "prod" (app.assembly.nz, default) or "dev" (dev.assembly.nz).
        dry_run: Convert and show the result only; no writes.
        uploader_email: Who is uploading — omit to use the saved default (see `saved_login`).

    Returns:
        {ok, uploader, dryRun, project: {id, number, name}, drawing: {name, fileName, epsg, crsName,
        units, layers: [{name, lines}], lineCount, distanceToSiteKm}, replaced, warnings} —
        `replaced` (on a dry run: would replace) is true when a drawing with that name already
        existed; warn the person when `distanceToSiteKm` > 2 (the drawing is probably misplaced).
    """
    who = _resolve_uploader(uploader_email)
    args = ["--as", who, "--project", project, "--file", str(Path(file).expanduser().resolve())]
    if name:
        args += ["--name", name]
    args += _env_arg(env)
    if dry_run:
        args.append("--dry-run")
    return _run_upload_cli("upload-dwg-geomap.ts", args, _uploader_env(who))


def _abs_source(s: str) -> str:
    """A local file → its absolute path; an http(s) URL as given (the CLI downloads it)."""
    return s if s.lower().startswith(("http://", "https://")) else str(Path(s).expanduser().resolve())


def _resolve_product_files(spec: dict) -> dict:
    """Make every local file in one product spec absolute — photos, spec sheets and variant photos —
    since the CLI runs from the assembly-app checkout, not the caller's folder."""
    out = dict(spec)
    for key in ("images", "spec_sheets"):
        if out.get(key):
            out[key] = [_abs_source(s) for s in out[key]]
    if out.get("variants"):
        out["variants"] = [
            {**v, "images": [_abs_source(s) for s in v["images"]]} if v.get("images") else dict(v) for v in out["variants"]
        ]
    return out


def _run_with_spec(spec: dict | list | None, who: str, args: list[str]) -> dict:
    with tempfile.TemporaryDirectory(prefix="upload-product-") as tmp:
        spec_path = Path(tmp) / "products.json"
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        return _run_upload_cli("upload-product.ts", ["--as", who, "--spec", str(spec_path), *args], _uploader_env(who))


@mcp.tool()
def product_catalogue(env: str = "prod", uploader_email: str | None = None) -> dict:
    """The product types (with their spec attributes) and leaf categories a product can be filed
    under — call before `upload_product` / `upload_products` to pick `category` / `type` / `attributes`.

    Args:
        env: "prod" (app.assembly.nz, default) or "dev".
        uploader_email: Who is asking — omit to use the saved default (see `saved_login`).

    Returns:
        {types: [{slug, name, attributes: [{key, label, type, unit, options}]}],
        categories: [{name, uniclass, path}]}
    """
    who = _resolve_uploader(uploader_email)
    return _run_upload_cli("upload-product.ts", ["--as", who, "--catalogue", *_env_arg(env)], _uploader_env(who))


@mcp.tool()
def find_products(
    query: str,
    organisation: str | None = None,
    env: str = "prod",
    uploader_email: str | None = None,
) -> dict:
    """Search the person's organisation product library — before adding, to see what's already there
    (and get a product's id for `existing_id`), or to answer "do we have X?".

    Args:
        query: Words to find in the product's name, brand, model code or website (all must match;
            "" lists the library, up to 50).
        organisation: Whose library, when the person is in several organisations.
        env: "prod" (app.assembly.nz, default) or "dev".
        uploader_email: Who is asking — omit to use the saved default (see `saved_login`).

    Returns:
        {organisation, matches: [{id, model, manufacturer, model_code, website, photos (count),
        variants: [{label, model_code}], specSheets: [file names]}], truncated}
    """
    who = _resolve_uploader(uploader_email)
    args = ["--as", who, "--find", query]
    if organisation:
        args += ["--org", organisation]
    return _run_upload_cli("upload-product.ts", [*args, *_env_arg(env)], _uploader_env(who))


@mcp.tool()
def upload_product(
    model: str,
    manufacturer: str | None = None,
    model_code: str | None = None,
    website: str | None = None,
    price: float | None = None,
    price_note: str | None = None,
    notes: str | None = None,
    suppliers: list[str] | None = None,
    category: str | None = None,
    product_type: str | None = None,
    attributes: dict | None = None,
    images: list[str] | None = None,
    variants: list[dict] | None = None,
    spec_sheets: list[str] | None = None,
    project: str | None = None,
    space: str | None = None,
    quantity: int | None = None,
    location: str | None = None,
    status: str | None = None,
    selection_notes: str | None = None,
    existing_product_id: str | None = None,
    update: str | None = None,
    organisation: str | None = None,
    env: str = "prod",
    dry_run: bool = False,
    uploader_email: str | None = None,
) -> dict:
    """Add ONE product to the person's organisation product library — with its size / colour / fabric variants,
    photos and spec-sheet PDFs — and optionally select it on a project. Signs in as the person (org
    admin/editor for the library; project admin/editor for a selection). For several products use
    `upload_products`.

    A product already in the library (same website, same brand + model code, or same brand + name) is
    never created again: what's missing is added to it instead (see `update`), and with `project` it
    is what gets selected. `similar` products are only reported — if the person says one IS this
    product, re-run with `existing_product_id`. Always `dry_run=True` first.

    Args:
        model: The product's name (e.g. "INTELLO PLUS").
        manufacturer: The brand.
        model_code: SKU / model code (of the product itself; per-size codes go on the variants).
        website: The product page URL.
        price: Price in NZD (number only).
        price_note: "rrp" or "on_request".
        notes: A short description.
        suppliers: Supplier names.
        category: A leaf category name or uniclass code (see `product_catalogue`).
        product_type: A product type slug or name (see `product_catalogue`).
        attributes: Spec values keyed by the type's attribute keys.
        images: Photo file paths and/or http(s) image URLs; the first is the cover.
        variants: Sizes / colours / fabrics of this product: [{"size": "200 S"}, {"color": "Matte Black"}
            and/or {"fabric": "Tahiti"} (any mix; fabric / upholstery is its own field, never in
            "color"), "model_code", "price", "price_note", "source_url", "images": [...]}].
        spec_sheets: Spec-sheet / datasheet / manual PDFs — file paths and/or http(s) URLs.
        project: Also select it on this project — number (e.g. "2610"), name, or UUID.
        space: The project space (room) to put the selection in; created if new.
        quantity: Selection quantity (default 1).
        location: Where it goes (free text).
        status: Selection status: proposed (default), approved, rejected, option.
        selection_notes: Notes on the selection.
        existing_product_id: Use this library product instead of matching or creating one.
        update: For a product already in the library: "fill" (default — blank fields get a value;
            missing variants / photos / spec sheets are added), "overwrite" (also replaces the fields
            given), "none" (no field changes; missing variants / photos / spec sheets still added).
        organisation: Whose library, when the person is in several organisations.
        env: "prod" (app.assembly.nz, default) or "dev".
        dry_run: Check everything and report what would happen; no writes.
        uploader_email: Who is uploading — omit to use the saved default (see `saved_login`).

    Returns:
        {ok, dryRun, organisation, totals, products: [ONE report]} — see `upload_products`.
    """
    who = _resolve_uploader(uploader_email)
    spec = _resolve_product_files({
        "model": model,
        "manufacturer": manufacturer,
        "model_code": model_code,
        "website": website,
        "price": price,
        "price_note": price_note,
        "notes": notes,
        "suppliers": suppliers or [],
        "category": category,
        "type": product_type,
        "attributes": attributes,
        "images": images or [],
        "variants": variants or [],
        "spec_sheets": spec_sheets or [],
    })
    args: list[str] = []
    for flag, value in (
        ("--project", project),
        ("--space", space),
        ("--quantity", None if quantity is None else str(quantity)),
        ("--location", location),
        ("--status", status),
        ("--selection-notes", selection_notes),
        ("--existing", existing_product_id),
        ("--update", update),
        ("--org", organisation),
    ):
        if value:
            args += [flag, value]
    args += _env_arg(env)
    if dry_run:
        args.append("--dry-run")
    return _run_with_spec(spec, who, args)


@mcp.tool()
def upload_products(
    products: list[dict],
    organisation: str | None = None,
    env: str = "prod",
    dry_run: bool = False,
    uploader_email: str | None = None,
) -> dict:
    """Add MANY products in one go (a schedule, a supplier list, a folder of spec sheets) — one sign-in,
    one plan, one report. Each product follows the same rules as `upload_product`; a failure on one is
    reported on it and the rest still go ahead. Always `dry_run=True` first.

    Args:
        products: One dict per product: {"model" (required), "manufacturer", "model_code", "website",
            "price", "price_note", "notes", "suppliers": [...], "category", "type", "attributes": {...},
            "images": [...], "variants": [{"size" | "color" | "fabric", "model_code", "price", "price_note",
            "source_url", "images": [...]}], "spec_sheets": [...], "selection": {"project", "space",
            "quantity", "location", "status", "notes"}, "existing_id", "update": "fill"|"overwrite"|"none"}.
            Local file paths are made absolute here. Don't list the same product twice — merge its
            variants into one entry (a repeat only adds its selection).
        organisation: Whose library, when the person is in several organisations.
        env: "prod" (app.assembly.nz, default) or "dev".
        dry_run: Check everything and report what would happen; no writes.
        uploader_email: Who is uploading — omit to use the saved default (see `saved_login`).

    Returns:
        {ok, dryRun, organisation, totals: {products, create, addToExisting, alreadyThere, errors,
        selections}, products: [{index, action ("create" | "add to existing" | "already there" | "same
        as an earlier product" | "error"), error, product, productId, duplicateOf: {id, model, reason},
        sameAs, similar: [{id, model, manufacturer, website, reason}], update, changes: {field: value},
        photos: {add, alreadyThere}, specSheets: {add, alreadyThere}, variants: [{label, action (add |
        update), model_code, price, changes, photos}], selection: {project: {number, name}, action (add |
        update | already selected), changes, quantity, status, space: {name, created, alreadyIn}},
        failures: [{file, error}], warnings, done (after a real run)}]}
    """
    if not products:
        raise ValueError("upload_products needs at least one product")
    who = _resolve_uploader(uploader_email)
    args: list[str] = []
    if organisation:
        args += ["--org", organisation]
    args += _env_arg(env)
    if dry_run:
        args.append("--dry-run")
    return _run_with_spec({"products": [_resolve_product_files(p) for p in products]}, who, args)


@mcp.tool()
def project_selections(project: str, env: str = "prod", uploader_email: str | None = None) -> dict:
    """What a project's Selections page holds — every selected product with its selection id, space(s),
    group(s), quantity, status, variant and notes. Call it before `edit_selections` (edits name
    selections by these ids) and after `upload_products` to check where everything landed.

    Args:
        project: The project's number ("2610"), name or id.
        env: "prod" (app.assembly.nz, default) or "dev".
        uploader_email: Who is asking — omit to use the saved default (see `saved_login`).

    Returns:
        {project: {id, number, name, access}, spaces / groups / selections as the Selections page
        shows them (the CLI's `--selections` shape; each selection carries its `id`).}
    """
    who = _resolve_uploader(uploader_email)
    return _run_upload_cli("upload-product.ts", ["--as", who, "--selections", project, *_env_arg(env)], _uploader_env(who))


@mcp.tool()
def edit_selections(
    project: str,
    edits: list[dict],
    env: str = "prod",
    dry_run: bool = False,
    uploader_email: str | None = None,
) -> dict:
    """Change a project's Selections page the way the page itself does — move selections between
    spaces (rooms; "Lounge › Nook" for a sub-space), put them in / take them out of groups, change
    quantity, status, notes, location, the picked variant, and the project's own price / discount /
    supplier. Runs as the person (project admin or editor). Always `dry_run=True` first, show the plan,
    and run for real only on their "yes". At most 100 edits per call.

    Args:
        project: The project's number ("2610"), name or id.
        edits: Each one of
            {"op": "update", "selection": "<id>", "quantity"?: n, "status"?: "proposed"|"approved"|"rejected"|"option",
             "notes"?: "…", "location"?: "…", "variant"?: "<variant id or label, e.g. Aged Oak>",
             "unit_price"?: n|null, "discount_pct"?: n|null, "supplier"?: "…"|null}
            {"op": "add_to_space" | "move_to_space" | "remove_from_space", "selection": "<id>", "space": "<name or path>", "quantity"?: n}
            {"op": "add_to_group" | "remove_from_group", "selection": "<id>", "group": "<name>", "quantity"?: n}
            Selection ids come from `project_selections`. Spaces and groups are matched by name
            (ignoring case); a new group is made, but a space must already exist (`upload_products`
            with `selection.space` makes one).
        env: "prod" (app.assembly.nz, default) or "dev".
        dry_run: Plan every step and report it; no writes.
        uploader_email: Who is editing — omit to use the saved default (see `saved_login`).

    Returns:
        Dry run: {ok, dryRun, project, plan: [step text]}. Real run: {ok, dryRun, project, done: [{text, ok,
        error}], undo}.
    """
    if not edits:
        raise ValueError("edit_selections needs at least one edit")
    who = _resolve_uploader(uploader_email)
    args = _env_arg(env)
    if dry_run:
        args.append("--dry-run")
    with tempfile.TemporaryDirectory(prefix="edit-selections-") as tmp:
        path = Path(tmp) / "edits.json"
        path.write_text(json.dumps({"project": project, "edits": edits}), encoding="utf-8")
        return _run_upload_cli("upload-product.ts", ["--as", who, "--edit-selections", str(path), *args], _uploader_env(who))


# ── The app API (assembly-api.mjs on P:) ─────────────────────────────────────────────────────────
# One no-dependency node CLI published from the app repo to P:/assembly-app-skills/api (with its guide).
# These tools put it in front of Claude as real tools, so a session never has to know a file path.

def _api_dir() -> Path:
    env = os.environ.get("ASSEMBLY_API_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for cand in (Path("P:/assembly-app-skills/api"), here.parents[4] / "assembly-app-skills" / "api"):
        if (cand / "assembly-api.mjs").exists():
            return cand
    return Path("P:/assembly-app-skills/api")


def _run_api(args: list[str], who: str | None, timeout: int = 1800) -> dict:
    """Run assembly-api.mjs as the saved person. Exit 0 ok · 2 dry run / refused (the plan or the
    reason is returned, not raised) · 3 PATCH/DELETE changed 0 rows · 1 error (raised)."""
    script = _api_dir() / "assembly-api.mjs"
    if not script.exists():
        raise RuntimeError(f"the app API helper isn't at {script} — is the P: drive connected?")
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node isn't installed on this computer (needed for the app API) — re-run P:\\assembly-app-skills\\Install.bat")
    env = {**os.environ, "ASSEMBLY_API_NO_WINDOW": "1"}
    env_args = ["--env", args[args.index("--env") + 1]] if "--env" in args else []
    if who:
        pre = subprocess.run([node, str(script), "login", "--email", who, *env_args], env=env, capture_output=True,
                             text=True, timeout=120, encoding="utf-8", errors="replace")
        if pre.returncode != 0:
            raise RuntimeError(
                f"not signed in as {who} on this computer — ask the person for their app email and password "
                f"and call save_login (once per computer), then try again. ({(pre.stderr or pre.stdout).strip()[:200]})")
    else:
        raise RuntimeError("nobody is signed in on this computer — ask the person for their app email and password "
                           "and call save_login (once per computer; it's saved), then try again.")
    proc = subprocess.run([node, str(script), *args], env=env, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")
    out = proc.stdout.strip()
    try:
        body = json.loads(out) if out else None
    except ValueError:
        body = out
    msgs = proc.stderr.strip()
    if proc.returncode == 1 or (proc.returncode == 2 and body is None):
        raise RuntimeError(msgs.removeprefix("assembly-api: ") or f"failed: {out[:500]}")
    return {"ok": proc.returncode == 0, "exit": proc.returncode, "result": body, "messages": msgs}


def _who(uploader_email: str | None) -> str | None:
    try:
        return _resolve_uploader(uploader_email)
    except RuntimeError:
        return None


def _expand_files(files: list[str]) -> list[str]:
    out: list[str] = []
    for f in files:
        p = Path(f).expanduser().resolve()
        if p.is_dir():
            out += [str(x) for x in sorted(p.rglob("*"))
                    if x.is_file() and not x.name.startswith((".", "~$")) and x.name != "Thumbs.db"]
        else:
            out.append(str(p))
    return out


@mcp.tool()
def upload_documents(
    project: str,
    files: list[str],
    issue_name: str | None = None,
    add_to_draft_id: str | None = None,
    confirm: bool = False,
    env: str = "prod",
    uploader_email: str | None = None,
) -> dict:
    """Upload documents — drawings, PDFs, reports, specs, any files or whole folders — to an
    assembly-app project. In the app, files live on the project's Documents ("Issued") tab inside an
    issue: this puts them in a DRAFT issue (nobody is notified; issuing is a separate step,
    `issue_draft`). Signs in as the person — if nobody is saved on this computer, ask for their app
    email + password and call `save_login` first.

    On prod, confirm=False is a dry run returning the plan (project, draft name, files): show it and
    call again with confirm=True once they say yes. If the request already said exactly this ("upload
    these to 2610 as Developed design"), confirm=True straight away is fine.

    Args:
        project: Project number (e.g. "2610"), name, or id.
        files: File and/or folder paths (folders are walked).
        issue_name: Name for the new draft issue (default "Upload <date>").
        add_to_draft_id: Add to this existing DRAFT instead (also resumes a cut-off upload — files
            already on it are skipped).
        confirm: False = dry run on prod; True = do it.
        env: "prod" (app.assembly.nz, default) or "dev" (dev.assembly.nz).
        uploader_email: Who is uploading — omit for the saved default.

    Returns:
        {ok, exit, result, messages}: result = the dry-run plan, or {project, draft_issue, draft_id,
        files, open: link to the page}.
    """
    paths = _expand_files(files)
    if not paths:
        raise ValueError("no files to upload")
    args = ["upload-docs", project, *paths, *_env_arg(env)]
    if issue_name:
        args += ["--name", issue_name]
    if add_to_draft_id:
        args += ["--draft", add_to_draft_id]
    if confirm:
        args.append("--yes")
    return _run_api(args, _who(uploader_email))


@mcp.tool()
def assembly_app(
    method: str,
    path: str,
    data: dict | list | None = None,
    confirm: bool = False,
    env: str = "prod",
    uploader_email: str | None = None,
) -> dict:
    """Read or change ANYTHING in the assembly-app — projects, documents/issues, selections, products,
    contracts & questions, timesheets, team, sites, notifications — as the signed-in person (row-level
    security: they can do exactly what they could in the app). Call `assembly_app_guide` first for the
    tables, the ready-made calls for common tasks, and the rules.

    Args:
        method: "GET" (read), "POST" (insert), "PATCH" (update — needs a filter like ?id=eq.<uuid>),
            "DELETE" (needs a filter), or "RPC" (call a database function; `path` = its name).
        path: A PostgREST path, e.g. "rest/v1/projects?select=id,name&deleted_at=is.null&limit=20";
            for RPC just the function name, e.g. "get_project_team".
        data: JSON body for POST / PATCH / RPC.
        confirm: Writes on prod need confirm=True — only once the person OK'd that change (a plain,
            specific request for exactly it counts; see the guide).
        env: "prod" (default) or "dev".
        uploader_email: Who to act as — omit for the saved default.

    Returns:
        {ok, exit, result (rows / function result), messages (HTTP status, row counts)}. exit 2 = a prod
        write without confirm (nothing changed); exit 3 = an update/delete changed 0 rows.
    """
    m = method.strip().upper()
    if m not in ("GET", "POST", "PATCH", "DELETE", "RPC"):
        raise ValueError("method must be GET, POST, PATCH, DELETE or RPC")
    args = ["rpc", path.strip()] if m == "RPC" else [m.lower(), path.strip()]
    args += _env_arg(env)
    if confirm:
        args.append("--yes")
    if m == "GET":
        args.append("--count")
    with tempfile.TemporaryDirectory(prefix="assembly-app-") as tmp:
        if data is not None:
            body = Path(tmp) / "body.json"
            body.write_text(json.dumps(data), encoding="utf-8")
            args += ["--data", f"@{body}"]
        return _run_api(args, _who(uploader_email), timeout=300)


@mcp.tool()
def assembly_app_guide(topic: str | None = None) -> str:
    """The assembly-app API guide: how access works, the rules, and ready-made calls for the common
    tasks (find a project, documents/issues, images, products, selections, contracts, time, docs).
    Read it before using `assembly_app`.

    Args:
        topic: Omit for the guide. Or a table / function name (e.g. "project_products",
            "issue_collection") for its columns or arguments and who can use it; "storage" for buckets.
    """
    d = _api_dir()
    if not topic:
        return (d / "README.md").read_text(encoding="utf-8")
    want = topic.strip().lower()
    if want in ("storage", "buckets", "edge", "edge functions"):
        return (d / "reference" / "storage-and-edge.md").read_text(encoding="utf-8")
    for name in ("tables.md", "functions.md"):
        text = (d / "reference" / name).read_text(encoding="utf-8")
        start = text.find(f"\n## {want}\n")
        if start >= 0:
            end = text.find("\n## ", start + 4)
            return text[start + 1: end if end > 0 else None]
    hits = [line[3:] for name in ("tables.md", "functions.md")
            for line in (d / "reference" / name).read_text(encoding="utf-8").splitlines()
            if line.startswith("## ") and want in line.lower()]
    return f'No "{topic}" exactly. Close matches: {", ".join(hits[:40])}' if hits else f'Nothing called "{topic}".'


def _report_status() -> None:
    """On start-up, write this computer's state to P:/assembly-app-skills/status/, so a maintainer can
    see every PC (tools, saved login, helper reachable) without asking anyone. Never fails the server."""
    try:
        status_dir = Path(os.environ.get("ASSEMBLY_STATUS_DIR", "P:/assembly-app-skills/status"))
        if not status_dir.parent.exists():
            return
        status_dir.mkdir(exist_ok=True)
        creds = _read_credentials()
        default = creds.get(_DEFAULT_KEY)
        api = _api_dir() / "assembly-api.mjs"
        from datetime import datetime

        info = {
            "computer": os.environ.get("COMPUTERNAME", ""),
            "windows_user": os.environ.get("USERNAME", ""),
            "started": datetime.now().isoformat(timespec="seconds"),
            "mcp_dir": str(Path(__file__).resolve().parents[2]),
            "tools": sorted(t.name for t in mcp._tool_manager.list_tools()),
            "saved_login": default if isinstance(default, str) else None,
            "saved_people": [k for k, v in creds.items() if k != _DEFAULT_KEY and isinstance(v, str) and v],
            "api_helper": str(api) if api.exists() else f"MISSING ({api})",
            "node": shutil.which("node") or "MISSING",
            "prod_key_set": bool(os.environ.get("ASSEMBLY_SUPABASE_ANON_KEY")),
        }
        name = f"{info['computer'] or 'unknown'}__{info['windows_user'] or 'unknown'}.json"
        (status_dir / name).write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 — status is best-effort
        pass


def main() -> None:
    """Console-script entry point: run the MCP server over stdio."""
    _report_status()
    mcp.run()


if __name__ == "__main__":
    main()
