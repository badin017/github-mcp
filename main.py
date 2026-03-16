"""GitHub Enterprise MCP Server

A Model Context Protocol server that bridges AI coding assistants
with GitHub Enterprise (anbgithub.com) via Server-Sent Events.
"""

import asyncio
import base64
import json
import logging
import os
import re
import uuid
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from git import Repo
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool
from starlette.requests import Request

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITHUB_BASE_URL = os.getenv("GITHUB_BASE_URL", "https://anbgithub.com/api/v3")
GITHUB_HOST = os.getenv("GITHUB_HOST", "anbgithub.com")
GITHUB_TOKEN = os.getenv("GITHUB_ENTERPRISE_TOKEN", "")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger("github-mcp")

# ---------------------------------------------------------------------------
# MCP server + SSE transport
# ---------------------------------------------------------------------------
mcp_server = Server("github-mcp")
sse_transport = SseServerTransport("/messages/")

# ---------------------------------------------------------------------------
# GitHub API helper
# ---------------------------------------------------------------------------


def _auth_headers() -> dict:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }


async def github_api(method: str, path: str, **kwargs) -> httpx.Response:
    """Authenticated request to the GitHub Enterprise REST API."""
    url = f"{GITHUB_BASE_URL}{path}"
    logger.info("GitHub API  %s %s", method, path)
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=_auth_headers(), **kwargs)

    if resp.status_code == 401:
        raise RuntimeError(
            "Authentication failed - check GITHUB_ENTERPRISE_TOKEN."
        )
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        reset = resp.headers.get("X-RateLimit-Reset", "unknown")
        raise RuntimeError(f"Rate limit exceeded. Resets at epoch {reset}.")
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------
TOOLS = [
    Tool(
        name="clone_repository",
        description="Clone a repository from GitHub Enterprise to the local machine.",
        inputSchema={
            "type": "object",
            "properties": {
                "repo_name": {
                    "type": "string",
                    "description": "Full 'owner/repo' name",
                },
                "local_path": {
                    "type": "string",
                    "description": "Local directory to clone into (optional)",
                },
            },
            "required": ["repo_name"],
        },
    ),
    Tool(
        name="get_repo_rules",
        description="Retrieve active rulesets and branch-protection rules for a repository.",
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
            },
            "required": ["owner", "repo"],
        },
    ),
    Tool(
        name="create_dummy_pr",
        description=(
            "Create a pull request that appends a space to README.md "
            "using the Git Data API (blobs / trees / commits)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "base_branch": {
                    "type": "string",
                    "description": "Target branch (defaults to repo default branch)",
                },
            },
            "required": ["owner", "repo"],
        },
    ),
    Tool(
        name="analyze_workflow_run",
        description=(
            "Analyse a GitHub Actions workflow run and extract "
            "error details from failed steps."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "run_url": {
                    "type": "string",
                    "description": "Full URL to the GitHub Actions workflow run",
                },
            },
            "required": ["run_url"],
        },
    ),
    Tool(
        name="handle_failed_workflow",
        description=(
            "Re-run failed jobs for a workflow run if the failure "
            "looks transient (internal error, timeout, etc.)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "run_url": {
                    "type": "string",
                    "description": "Full URL to the GitHub Actions workflow run",
                },
                "action": {
                    "type": "string",
                    "description": "Action to take, e.g. 'rerun_failed'",
                },
            },
            "required": ["run_url", "action"],
        },
    ),
    # -- CI/CD & Actions Management --
    Tool(
        name="trigger_workflow",
        description=(
            "Trigger a workflow_dispatch event to start a new workflow run "
            "with custom input parameters."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "workflow_id": {
                    "type": "string",
                    "description": "Workflow ID or filename (e.g. 'build.yml')",
                },
                "ref": {
                    "type": "string",
                    "description": "Git ref to run against (branch/tag, defaults to repo default branch)",
                },
                "inputs": {
                    "type": "object",
                    "description": "JSON object of workflow input parameters",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["owner", "repo", "workflow_id"],
        },
    ),
    Tool(
        name="monitor_workflow_status",
        description=(
            "Poll a workflow run until it completes, streaming status updates "
            "with exponential backoff."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "run_id": {
                    "type": "integer",
                    "description": "Workflow run ID to monitor",
                },
                "poll_interval": {
                    "type": "integer",
                    "description": "Initial poll interval in seconds (default 10)",
                },
                "max_wait": {
                    "type": "integer",
                    "description": "Maximum total wait time in seconds (default 600)",
                },
            },
            "required": ["owner", "repo", "run_id"],
        },
    ),
    # -- Pull Request & Code Review --
    Tool(
        name="review_and_merge_pr",
        description=(
            "Check if all CI status checks have passed on a PR, approve it, "
            "and execute a squash-and-merge or rebase-and-merge."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pr_number": {
                    "type": "integer",
                    "description": "Pull request number",
                },
                "merge_method": {
                    "type": "string",
                    "enum": ["squash", "rebase", "merge"],
                    "description": "Merge strategy (default 'squash')",
                },
            },
            "required": ["owner", "repo", "pr_number"],
        },
    ),
    Tool(
        name="add_pr_comment",
        description=(
            "Add an inline review comment to a specific file and line "
            "in a pull request."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pr_number": {
                    "type": "integer",
                    "description": "Pull request number",
                },
                "file_path": {
                    "type": "string",
                    "description": "Relative path of the file to comment on",
                },
                "line": {
                    "type": "integer",
                    "description": "Line number in the diff to attach the comment to",
                },
                "comment": {
                    "type": "string",
                    "description": "The review comment body (markdown)",
                },
            },
            "required": ["owner", "repo", "pr_number", "file_path", "line", "comment"],
        },
    ),
    # -- Issue Tracking & Project Management --
    Tool(
        name="search_and_create_issue",
        description=(
            "Search for duplicate open issues matching a query. If none exist, "
            "create a new issue with labels and assign it to the authenticated user."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {
                    "type": "string",
                    "description": "Issue title / search query",
                },
                "body": {
                    "type": "string",
                    "description": "Issue body in markdown (optional)",
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels to apply (e.g. ['bug', 'enhancement'])",
                },
            },
            "required": ["owner", "repo", "title"],
        },
    ),
    Tool(
        name="link_pr_to_issue",
        description=(
            "Update a PR description to include 'Closes #<issue>' so "
            "merging the PR automatically resolves the issue."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "pr_number": {
                    "type": "integer",
                    "description": "Pull request number",
                },
                "issue_number": {
                    "type": "integer",
                    "description": "Issue number to link",
                },
            },
            "required": ["owner", "repo", "pr_number", "issue_number"],
        },
    ),
    # -- Code Search & Navigation --
    Tool(
        name="search_enterprise_codebase",
        description=(
            "Search for code across the Enterprise instance or a specific "
            "repository using the GitHub Search API."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Code search query (function names, error codes, etc.)",
                },
                "owner": {
                    "type": "string",
                    "description": "Org/user to scope search to (optional)",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository name to scope search to (optional)",
                },
                "language": {
                    "type": "string",
                    "description": "Filter by programming language (optional)",
                },
                "per_page": {
                    "type": "integer",
                    "description": "Results per page (default 10, max 100)",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_file_history",
        description=(
            "Fetch the commit history for a specific file in a repository "
            "to understand who changed it and why."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "file_path": {
                    "type": "string",
                    "description": "Path to the file within the repository",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch to query (defaults to repo default branch)",
                },
                "per_page": {
                    "type": "integer",
                    "description": "Number of commits to return (default 15)",
                },
            },
            "required": ["owner", "repo", "file_path"],
        },
    ),
    # -- Security & Enterprise Compliance --
    Tool(
        name="check_security_alerts",
        description=(
            "Retrieve Dependabot and Code Scanning (CodeQL) alerts "
            "for a repository including severity and remediation info."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {
                    "type": "string",
                    "enum": ["open", "fixed", "dismissed"],
                    "description": "Alert state filter (default 'open')",
                },
            },
            "required": ["owner", "repo"],
        },
    ),
    Tool(
        name="get_team_members",
        description=(
            "List members of a specific team in an organization, useful "
            "for dynamically assigning PR reviewers."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "org": {
                    "type": "string",
                    "description": "Organization name",
                },
                "team_slug": {
                    "type": "string",
                    "description": "Team slug (URL-friendly team name)",
                },
            },
            "required": ["org", "team_slug"],
        },
    ),
    # -- Release & Artifact Management --
    Tool(
        name="generate_and_publish_release",
        description=(
            "Auto-generate release notes from merged PRs between two tags "
            "and publish a new GitHub Release."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "tag_name": {
                    "type": "string",
                    "description": "Tag for the new release (e.g. 'v2.1.0')",
                },
                "previous_tag": {
                    "type": "string",
                    "description": "Previous tag to diff against for release notes",
                },
                "target_branch": {
                    "type": "string",
                    "description": "Branch the tag points to (defaults to repo default branch)",
                },
                "draft": {
                    "type": "boolean",
                    "description": "Create as draft release (default false)",
                },
                "prerelease": {
                    "type": "boolean",
                    "description": "Mark as prerelease (default false)",
                },
            },
            "required": ["owner", "repo", "tag_name", "previous_tag"],
        },
    ),
    Tool(
        name="download_workflow_artifact",
        description=(
            "Download and extract a build artifact from a workflow run "
            "into the AI workspace for analysis."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "run_id": {
                    "type": "integer",
                    "description": "Workflow run ID",
                },
                "artifact_name": {
                    "type": "string",
                    "description": "Name of the artifact to download (optional, downloads first if omitted)",
                },
                "output_dir": {
                    "type": "string",
                    "description": "Local directory to extract into (default './artifacts/<run_id>')",
                },
            },
            "required": ["owner", "repo", "run_id"],
        },
    ),
    # -- Environment & Configuration Automation --
    Tool(
        name="manage_repo_variables",
        description=(
            "Read or update GitHub Actions repository variables "
            "(non-secret configuration values)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "variable_name": {
                    "type": "string",
                    "description": "Variable name to read or update",
                },
                "value": {
                    "type": "string",
                    "description": "New value to set (omit to read current value)",
                },
            },
            "required": ["owner", "repo", "variable_name"],
        },
    ),
    Tool(
        name="get_deployment_status",
        description=(
            "Fetch the latest deployment history for an environment "
            "to verify if a release reached production."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "environment": {
                    "type": "string",
                    "description": "Environment name (e.g. 'production', 'staging')",
                },
                "per_page": {
                    "type": "integer",
                    "description": "Number of deployments to return (default 10)",
                },
            },
            "required": ["owner", "repo", "environment"],
        },
    ),
]


@mcp_server.list_tools()
async def list_tools():
    return TOOLS


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _clone_repository(args: dict) -> list[TextContent]:
    repo_name = args["repo_name"]
    local_path = args.get(
        "local_path", f"./cloned_repos/{repo_name.split('/')[-1]}"
    )
    dest = Path(local_path)

    if dest.exists():
        return [
            TextContent(
                type="text",
                text=f"Directory already exists: {dest}. Remove it or choose another path.",
            )
        ]

    clone_url = f"https://{GITHUB_TOKEN}@{GITHUB_HOST}/{repo_name}.git"
    dest.parent.mkdir(parents=True, exist_ok=True)
    Repo.clone_from(clone_url, str(dest))
    logger.info("Cloned %s -> %s", repo_name, dest)
    return [TextContent(type="text", text=f"Cloned {repo_name} to {dest}")]


async def _get_repo_rules(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    lines: list[str] = []

    # --- Rulesets (newer GitHub API) ---
    try:
        resp = await github_api("GET", f"/repos/{owner}/{repo}/rulesets")
        rulesets = resp.json()
        lines.append(f"Rulesets ({len(rulesets)}):")
        for rs in rulesets:
            lines.append(
                f"  - {rs.get('name', 'unnamed')}  "
                f"enforcement={rs.get('enforcement')}"
            )
            detail = (
                await github_api(
                    "GET", f"/repos/{owner}/{repo}/rulesets/{rs['id']}"
                )
            ).json()
            for rule in detail.get("rules", []):
                lines.append(f"    rule: {rule.get('type')}")
                if rule.get("parameters"):
                    lines.append(
                        f"    params: {json.dumps(rule['parameters'], indent=6)}"
                    )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            lines.append("Rulesets endpoint not available on this instance.")
        else:
            raise

    # --- Branch protection on default branch ---
    try:
        default_branch = (
            (await github_api("GET", f"/repos/{owner}/{repo}"))
            .json()
            .get("default_branch", "main")
        )
        bp = (
            await github_api(
                "GET",
                f"/repos/{owner}/{repo}/branches/{default_branch}/protection",
            )
        ).json()

        lines.append(f"\nBranch protection ({default_branch}):")

        checks = bp.get("required_status_checks")
        if checks:
            lines.append(
                f"  Required status checks: strict={checks.get('strict')}"
            )
            lines.append(
                f"  Contexts: {', '.join(checks.get('contexts', []))}"
            )

        reviews = bp.get("required_pull_request_reviews")
        if reviews:
            lines.append(
                f"  Required approvals: "
                f"{reviews.get('required_approving_review_count', 0)}"
            )
            lines.append(
                f"  Dismiss stale reviews: "
                f"{reviews.get('dismiss_stale_reviews')}"
            )
            lines.append(
                f"  Require CODEOWNERS review: "
                f"{reviews.get('require_code_owner_reviews')}"
            )

        if bp.get("enforce_admins", {}).get("enabled"):
            lines.append("  Enforce for admins: yes")
        if bp.get("required_signatures", {}).get("enabled"):
            lines.append("  Required commit signing: yes")
        if bp.get("restrictions"):
            lines.append("  Push restrictions: enabled")

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            lines.append("\nNo branch protection on default branch.")
        else:
            raise

    return [
        TextContent(
            type="text",
            text="\n".join(lines) or "No rules or protections found.",
        )
    ]


async def _create_dummy_pr(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]

    # Resolve base branch
    repo_info = (
        await github_api("GET", f"/repos/{owner}/{repo}")
    ).json()
    base = args.get("base_branch") or repo_info["default_branch"]

    # Latest commit on base
    base_sha = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/git/ref/heads/{base}"
        )
    ).json()["object"]["sha"]

    # Create feature branch
    branch_name = f"dummy-pr-{uuid.uuid4().hex[:8]}"
    await github_api(
        "POST",
        f"/repos/{owner}/{repo}/git/refs",
        json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
    )

    # Fetch current README.md
    readme = (
        await github_api(
            "GET",
            f"/repos/{owner}/{repo}/contents/README.md",
            params={"ref": base},
        )
    ).json()
    content = base64.b64decode(readme["content"]).decode() + " "

    # ---- Git Data API: blob -> tree -> commit ----
    blob_sha = (
        await github_api(
            "POST",
            f"/repos/{owner}/{repo}/git/blobs",
            json={
                "content": base64.b64encode(content.encode()).decode(),
                "encoding": "base64",
            },
        )
    ).json()["sha"]

    base_tree = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/git/commits/{base_sha}"
        )
    ).json()["tree"]["sha"]

    tree_sha = (
        await github_api(
            "POST",
            f"/repos/{owner}/{repo}/git/trees",
            json={
                "base_tree": base_tree,
                "tree": [
                    {
                        "path": "README.md",
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_sha,
                    }
                ],
            },
        )
    ).json()["sha"]

    commit_sha = (
        await github_api(
            "POST",
            f"/repos/{owner}/{repo}/git/commits",
            json={
                "message": "chore: update README.md (dummy PR)",
                "tree": tree_sha,
                "parents": [base_sha],
            },
        )
    ).json()["sha"]

    # Point branch at new commit
    await github_api(
        "PATCH",
        f"/repos/{owner}/{repo}/git/refs/heads/{branch_name}",
        json={"sha": commit_sha},
    )

    # Open PR
    pr = (
        await github_api(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            json={
                "title": f"Dummy PR - {branch_name}",
                "head": branch_name,
                "base": base,
                "body": "Automated dummy PR created by the GitHub MCP server.",
            },
        )
    ).json()

    return [
        TextContent(
            type="text",
            text=(
                f"PR #{pr['number']} created: {pr['html_url']}\n"
                f"Branch: {branch_name}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------
_RUN_URL_RE = re.compile(
    r"https?://[^/]+/([^/]+)/([^/]+)/actions/runs/(\d+)"
)


def _parse_run_url(url: str) -> tuple[str, str, int]:
    m = _RUN_URL_RE.match(url)
    if not m:
        raise ValueError(f"Cannot parse workflow run URL: {url}")
    return m.group(1), m.group(2), int(m.group(3))


async def _analyze_workflow_run(args: dict) -> list[TextContent]:
    owner, repo, run_id = _parse_run_url(args["run_url"])

    run = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}"
        )
    ).json()

    lines = [
        f"Workflow  : {run.get('name')}",
        f"Status   : {run.get('status')}",
        f"Conclusion: {run.get('conclusion')}",
        f"Branch   : {run.get('head_branch')}",
        f"Event    : {run.get('event')}",
        f"Attempt  : {run.get('run_attempt', 1)}",
    ]

    if run.get("conclusion") != "failure":
        return [TextContent(type="text", text="\n".join(lines))]

    jobs = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        )
    ).json().get("jobs", [])
    failed = [j for j in jobs if j.get("conclusion") == "failure"]

    if not failed:
        lines.append(
            "\nMarked as failed but no individual failed jobs found."
        )
        return [TextContent(type="text", text="\n".join(lines))]

    lines.append(f"\nFailed jobs ({len(failed)}):")
    for job in failed:
        lines.append(f"\n  Job: {job['name']} (ID {job['id']})")
        for step in job.get("steps", []):
            if step.get("conclusion") == "failure":
                lines.append(f"    Failed step #{step['number']}: {step['name']}")

        try:
            log = (
                await github_api(
                    "GET",
                    f"/repos/{owner}/{repo}/actions/jobs/{job['id']}/logs",
                )
            ).text
            tail = log.strip().splitlines()[-50:]
            lines.append("    Log tail:")
            lines.extend(f"      {line}" for line in tail)
        except Exception as exc:
            lines.append(f"    (could not fetch logs: {exc})")

    return [TextContent(type="text", text="\n".join(lines))]


_TRANSIENT = [
    "internal error",
    "timeout",
    "timed out",
    "runner error",
    "service unavailable",
    "502",
    "503",
]


async def _handle_failed_workflow(args: dict) -> list[TextContent]:
    owner, repo, run_id = _parse_run_url(args["run_url"])
    action = args["action"]

    if action != "rerun_failed":
        return [
            TextContent(
                type="text",
                text=f"Unsupported action '{action}'. Use 'rerun_failed'.",
            )
        ]

    run = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}"
        )
    ).json()

    if run.get("status") != "completed":
        return [
            TextContent(
                type="text",
                text=f"Run still in progress ({run['status']}). Cannot rerun yet.",
            )
        ]
    if run.get("conclusion") != "failure":
        return [
            TextContent(
                type="text",
                text=f"Run did not fail ({run['conclusion']}). Nothing to rerun.",
            )
        ]

    jobs = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        )
    ).json().get("jobs", [])
    failed = [j for j in jobs if j.get("conclusion") == "failure"]

    transient_found = False
    notes: list[str] = []

    for job in failed:
        try:
            log_text = (
                await github_api(
                    "GET",
                    f"/repos/{owner}/{repo}/actions/jobs/{job['id']}/logs",
                )
            ).text.lower()
            hit = next((t for t in _TRANSIENT if t in log_text), None)
            if hit:
                transient_found = True
                notes.append(
                    f"Job '{job['name']}': transient indicator '{hit}'"
                )
            else:
                notes.append(f"Job '{job['name']}': no transient indicators")
        except Exception:
            notes.append(f"Job '{job['name']}': could not fetch logs")

    if not transient_found:
        return [
            TextContent(
                type="text",
                text=(
                    "No transient errors detected. "
                    "Manual investigation recommended.\n\n"
                    + "\n".join(notes)
                ),
            )
        ]

    await github_api(
        "POST",
        f"/repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs",
    )

    return [
        TextContent(
            type="text",
            text=f"Re-run triggered for run {run_id}.\n\n" + "\n".join(notes),
        )
    ]


# ---------------------------------------------------------------------------
# CI/CD & Actions Management
# ---------------------------------------------------------------------------


async def _trigger_workflow(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    workflow_id = args["workflow_id"]
    inputs = args.get("inputs", {})

    # Resolve ref — default to repo's default branch
    ref = args.get("ref")
    if not ref:
        repo_info = (
            await github_api("GET", f"/repos/{owner}/{repo}")
        ).json()
        ref = repo_info["default_branch"]

    await github_api(
        "POST",
        f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
        json={"ref": ref, "inputs": inputs},
    )

    return [
        TextContent(
            type="text",
            text=(
                f"Workflow '{workflow_id}' dispatched on ref '{ref}'.\n"
                f"Inputs: {json.dumps(inputs) if inputs else '(none)'}\n"
                "Note: the run may take a few seconds to appear. "
                "Use monitor_workflow_status to track it."
            ),
        )
    ]


async def _monitor_workflow_status(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    run_id = args["run_id"]
    interval = args.get("poll_interval", 10)
    max_wait = args.get("max_wait", 600)

    elapsed = 0
    current_interval = interval
    updates: list[str] = []

    while elapsed < max_wait:
        run = (
            await github_api(
                "GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}"
            )
        ).json()

        status = run.get("status")
        conclusion = run.get("conclusion")
        updates.append(
            f"[{elapsed}s] status={status}  conclusion={conclusion}"
        )

        if status == "completed":
            updates.append(
                f"\nWorkflow completed: {conclusion}\n"
                f"URL: {run.get('html_url')}"
            )
            return [TextContent(type="text", text="\n".join(updates))]

        await asyncio.sleep(current_interval)
        elapsed += current_interval
        # Exponential backoff capped at 60s
        current_interval = min(current_interval * 2, 60)

    updates.append(
        f"\nTimed out after {max_wait}s. Last status: {status}."
    )
    return [TextContent(type="text", text="\n".join(updates))]


# ---------------------------------------------------------------------------
# Pull Request & Code Review
# ---------------------------------------------------------------------------


async def _review_and_merge_pr(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    pr_number = args["pr_number"]
    merge_method = args.get("merge_method", "squash")

    # Fetch PR details
    pr = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/pulls/{pr_number}"
        )
    ).json()

    if pr.get("state") != "open":
        return [
            TextContent(
                type="text",
                text=f"PR #{pr_number} is not open (state: {pr['state']}).",
            )
        ]

    head_sha = pr["head"]["sha"]

    # Check combined status for the head commit
    status_resp = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/commits/{head_sha}/status"
        )
    ).json()

    # Also check GitHub Actions check-runs
    checks_resp = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
        )
    ).json()

    # Evaluate overall readiness
    combined_state = status_resp.get("state", "unknown")
    failing_checks = [
        cr["name"]
        for cr in checks_resp.get("check_runs", [])
        if cr.get("conclusion") not in ("success", "skipped", "neutral", None)
    ]
    pending_checks = [
        cr["name"]
        for cr in checks_resp.get("check_runs", [])
        if cr.get("status") != "completed"
    ]

    if combined_state == "failure" or failing_checks:
        return [
            TextContent(
                type="text",
                text=(
                    f"Cannot merge PR #{pr_number} - checks have not all passed.\n"
                    f"Commit status: {combined_state}\n"
                    f"Failing checks: {', '.join(failing_checks) or 'none'}\n"
                    f"Pending checks: {', '.join(pending_checks) or 'none'}"
                ),
            )
        ]

    if pending_checks:
        return [
            TextContent(
                type="text",
                text=(
                    f"Cannot merge PR #{pr_number} - checks still running.\n"
                    f"Pending: {', '.join(pending_checks)}"
                ),
            )
        ]

    # Approve the PR
    await github_api(
        "POST",
        f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
        json={"event": "APPROVE", "body": "Approved via GitHub MCP server."},
    )

    # Merge
    merge_resp = (
        await github_api(
            "PUT",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
            json={
                "merge_method": merge_method,
                "commit_title": f"{pr['title']} (#{pr_number})",
            },
        )
    ).json()

    return [
        TextContent(
            type="text",
            text=(
                f"PR #{pr_number} approved and merged ({merge_method}).\n"
                f"SHA: {merge_resp.get('sha')}\n"
                f"Message: {merge_resp.get('message')}"
            ),
        )
    ]


async def _add_pr_comment(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    pr_number = args["pr_number"]

    # Get the latest commit SHA on the PR (required by the review comments API)
    pr = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/pulls/{pr_number}"
        )
    ).json()

    comment = (
        await github_api(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            json={
                "body": args["comment"],
                "commit_id": pr["head"]["sha"],
                "path": args["file_path"],
                "line": args["line"],
                "side": "RIGHT",
            },
        )
    ).json()

    return [
        TextContent(
            type="text",
            text=(
                f"Review comment added to PR #{pr_number}.\n"
                f"File: {args['file_path']}:{args['line']}\n"
                f"URL: {comment.get('html_url')}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Issue Tracking & Project Management
# ---------------------------------------------------------------------------


async def _search_and_create_issue(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    title = args["title"]

    # Search for duplicate open issues in this repo
    query = f"{title} repo:{owner}/{repo} is:issue is:open"
    search_resp = (
        await github_api("GET", "/search/issues", params={"q": query, "per_page": 5})
    ).json()

    matches = search_resp.get("items", [])
    if matches:
        lines = [f"Found {len(matches)} potential duplicate(s):"]
        for item in matches:
            lines.append(
                f"  #{item['number']}: {item['title']}\n"
                f"    {item['html_url']}"
            )
        lines.append("\nNo new issue created to avoid duplication.")
        return [TextContent(type="text", text="\n".join(lines))]

    # Resolve authenticated user for assignment
    user_resp = (await github_api("GET", "/user")).json()
    assignee = user_resp.get("login")

    # Build the issue body
    labels = args.get("labels", [])
    body = args.get("body") or f"Automatically created by GitHub MCP server.\n\n**Details:**\n{title}"

    issue = (
        await github_api(
            "POST",
            f"/repos/{owner}/{repo}/issues",
            json={
                "title": title,
                "body": body,
                "labels": labels,
                "assignees": [assignee] if assignee else [],
            },
        )
    ).json()

    return [
        TextContent(
            type="text",
            text=(
                f"Issue #{issue['number']} created: {issue['html_url']}\n"
                f"Assigned to: {assignee}\n"
                f"Labels: {', '.join(labels) or '(none)'}"
            ),
        )
    ]


async def _link_pr_to_issue(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    pr_number = args["pr_number"]
    issue_number = args["issue_number"]

    # Fetch current PR body
    pr = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/pulls/{pr_number}"
        )
    ).json()

    current_body = pr.get("body") or ""
    closing_keyword = f"Closes #{issue_number}"

    if closing_keyword.lower() in current_body.lower():
        return [
            TextContent(
                type="text",
                text=f"PR #{pr_number} already references '{closing_keyword}'.",
            )
        ]

    # Append the closing reference
    separator = "\n\n---\n" if current_body.strip() else ""
    updated_body = f"{current_body}{separator}{closing_keyword}"

    await github_api(
        "PATCH",
        f"/repos/{owner}/{repo}/pulls/{pr_number}",
        json={"body": updated_body},
    )

    return [
        TextContent(
            type="text",
            text=(
                f"PR #{pr_number} updated to close issue #{issue_number} on merge.\n"
                f"Added: {closing_keyword}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Code Search & Navigation
# ---------------------------------------------------------------------------


async def _search_enterprise_codebase(args: dict) -> list[TextContent]:
    query = args["query"]
    per_page = min(args.get("per_page", 10), 100)

    # Build qualified search query
    qualifiers: list[str] = []
    if args.get("owner") and args.get("repo"):
        qualifiers.append(f"repo:{args['owner']}/{args['repo']}")
    elif args.get("owner"):
        qualifiers.append(f"org:{args['owner']}")
    if args.get("language"):
        qualifiers.append(f"language:{args['language']}")

    full_query = f"{query} {' '.join(qualifiers)}".strip()

    resp = (
        await github_api(
            "GET",
            "/search/code",
            params={"q": full_query, "per_page": per_page},
        )
    ).json()

    total = resp.get("total_count", 0)
    items = resp.get("items", [])

    lines = [f"Search: '{full_query}'  ({total} total results)"]
    for item in items:
        repo_name = item.get("repository", {}).get("full_name", "?")
        path = item.get("path", "?")
        lines.append(f"  {repo_name}/{path}")
        # Include text fragment matches if available
        for match in item.get("text_matches", []):
            fragment = match.get("fragment", "").replace("\n", " ")[:120]
            lines.append(f"    ...{fragment}...")

    if not items:
        lines.append("  (no results)")

    return [TextContent(type="text", text="\n".join(lines))]


async def _get_file_history(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    file_path = args["file_path"]
    per_page = args.get("per_page", 15)

    params: dict = {"path": file_path, "per_page": per_page}
    if args.get("branch"):
        params["sha"] = args["branch"]

    commits_resp = (
        await github_api(
            "GET", f"/repos/{owner}/{repo}/commits", params=params
        )
    ).json()

    if not commits_resp:
        return [
            TextContent(
                type="text",
                text=f"No commit history found for {file_path}.",
            )
        ]

    lines = [f"History for {file_path} ({len(commits_resp)} commits):"]
    for c in commits_resp:
        sha_short = c["sha"][:7]
        msg = c["commit"]["message"].split("\n")[0][:80]
        author = c["commit"]["author"]["name"]
        date = c["commit"]["author"]["date"][:10]
        lines.append(f"  {sha_short}  {date}  {author}")
        lines.append(f"           {msg}")

    return [TextContent(type="text", text="\n".join(lines))]


# ---------------------------------------------------------------------------
# Security & Enterprise Compliance
# ---------------------------------------------------------------------------


async def _check_security_alerts(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    state = args.get("state", "open")
    lines: list[str] = []

    # --- Dependabot alerts ---
    try:
        dependabot = (
            await github_api(
                "GET",
                f"/repos/{owner}/{repo}/dependabot/alerts",
                params={"state": state, "per_page": 25},
            )
        ).json()

        lines.append(f"Dependabot alerts ({len(dependabot)}, state={state}):")
        for alert in dependabot:
            vuln = alert.get("security_vulnerability", {})
            pkg = vuln.get("package", {}).get("name", "?")
            severity = vuln.get("severity", "?")
            summary = alert.get("security_advisory", {}).get("summary", "")[:100]
            lines.append(
                f"  [{severity.upper()}] {pkg}: {summary}"
            )
            first_patched = vuln.get("first_patched_version", {})
            if first_patched:
                lines.append(
                    f"    Fix: upgrade to {first_patched.get('identifier', '?')}"
                )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            lines.append("Dependabot alerts not enabled or not accessible.")
        else:
            raise

    # --- Code scanning (CodeQL) alerts ---
    try:
        scanning = (
            await github_api(
                "GET",
                f"/repos/{owner}/{repo}/code-scanning/alerts",
                params={"state": state, "per_page": 25},
            )
        ).json()

        lines.append(f"\nCode scanning alerts ({len(scanning)}, state={state}):")
        for alert in scanning:
            rule = alert.get("rule", {})
            severity = rule.get("security_severity_level") or rule.get("severity", "?")
            desc = rule.get("description", "?")[:100]
            tool = alert.get("tool", {}).get("name", "?")
            location = alert.get("most_recent_instance", {}).get("location", {})
            path = location.get("path", "?")
            start_line = location.get("start_line", "?")
            lines.append(
                f"  [{severity.upper()}] {desc}  ({tool})"
            )
            lines.append(f"    Location: {path}:{start_line}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            lines.append("\nCode scanning not enabled or not accessible.")
        else:
            raise

    return [
        TextContent(
            type="text",
            text="\n".join(lines) or "No security alerts found.",
        )
    ]


async def _get_team_members(args: dict) -> list[TextContent]:
    org = args["org"]
    team_slug = args["team_slug"]

    members = (
        await github_api(
            "GET",
            f"/orgs/{org}/teams/{team_slug}/members",
            params={"per_page": 100},
        )
    ).json()

    if not members:
        return [
            TextContent(
                type="text",
                text=f"No members found for team '{org}/{team_slug}'.",
            )
        ]

    lines = [f"Team '{org}/{team_slug}' ({len(members)} members):"]
    for m in members:
        lines.append(f"  @{m['login']}  (id: {m['id']})")

    return [TextContent(type="text", text="\n".join(lines))]


# ---------------------------------------------------------------------------
# Release & Artifact Management
# ---------------------------------------------------------------------------


async def _generate_and_publish_release(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    tag_name = args["tag_name"]
    previous_tag = args["previous_tag"]
    draft = args.get("draft", False)
    prerelease = args.get("prerelease", False)

    # Resolve target branch
    target = args.get("target_branch")
    if not target:
        target = (
            await github_api("GET", f"/repos/{owner}/{repo}")
        ).json()["default_branch"]

    # Use the generate-release-notes API to build the body
    notes_resp = (
        await github_api(
            "POST",
            f"/repos/{owner}/{repo}/releases/generate-notes",
            json={
                "tag_name": tag_name,
                "target_commitish": target,
                "previous_tag_name": previous_tag,
            },
        )
    ).json()

    generated_name = notes_resp.get("name", tag_name)
    generated_body = notes_resp.get("body", "")

    # Create the release
    release = (
        await github_api(
            "POST",
            f"/repos/{owner}/{repo}/releases",
            json={
                "tag_name": tag_name,
                "target_commitish": target,
                "name": generated_name,
                "body": generated_body,
                "draft": draft,
                "prerelease": prerelease,
            },
        )
    ).json()

    status = "Draft release" if draft else "Release"
    return [
        TextContent(
            type="text",
            text=(
                f"{status} '{generated_name}' published.\n"
                f"URL: {release.get('html_url')}\n"
                f"Tag: {tag_name}  (base: {previous_tag})\n"
                f"Target: {target}\n\n"
                f"--- Generated notes ---\n{generated_body}"
            ),
        )
    ]


async def _download_workflow_artifact(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    run_id = args["run_id"]
    artifact_name = args.get("artifact_name")
    output_dir = args.get("output_dir", f"./artifacts/{run_id}")

    # List artifacts for the run
    artifacts_resp = (
        await github_api(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts",
        )
    ).json()

    artifacts = artifacts_resp.get("artifacts", [])
    if not artifacts:
        return [
            TextContent(
                type="text",
                text=f"No artifacts found for run {run_id}.",
            )
        ]

    # Pick the requested artifact or the first one
    target = None
    if artifact_name:
        target = next(
            (a for a in artifacts if a["name"] == artifact_name), None
        )
        if not target:
            names = ", ".join(a["name"] for a in artifacts)
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Artifact '{artifact_name}' not found.\n"
                        f"Available: {names}"
                    ),
                )
            ]
    else:
        target = artifacts[0]

    # Download the zip archive
    download_resp = await github_api(
        "GET",
        f"/repos/{owner}/{repo}/actions/artifacts/{target['id']}/zip",
    )

    # Write and extract
    import io
    import zipfile

    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(download_resp.content)) as zf:
        zf.extractall(dest)

    extracted = [str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()]
    file_list = "\n".join(f"  {f}" for f in extracted[:30])
    suffix = f"\n  ... and {len(extracted) - 30} more" if len(extracted) > 30 else ""

    return [
        TextContent(
            type="text",
            text=(
                f"Artifact '{target['name']}' downloaded and extracted to {dest}\n"
                f"Size: {target.get('size_in_bytes', 0)} bytes\n"
                f"Files ({len(extracted)}):\n{file_list}{suffix}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Environment & Configuration Automation
# ---------------------------------------------------------------------------


async def _manage_repo_variables(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    var_name = args["variable_name"]
    new_value = args.get("value")

    if new_value is None:
        # Read mode
        try:
            resp = (
                await github_api(
                    "GET",
                    f"/repos/{owner}/{repo}/actions/variables/{var_name}",
                )
            ).json()
            return [
                TextContent(
                    type="text",
                    text=(
                        f"Variable: {resp['name']}\n"
                        f"Value: {resp['value']}\n"
                        f"Created: {resp.get('created_at', '?')}\n"
                        f"Updated: {resp.get('updated_at', '?')}"
                    ),
                )
            ]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return [
                    TextContent(
                        type="text",
                        text=f"Variable '{var_name}' does not exist in {owner}/{repo}.",
                    )
                ]
            raise
    else:
        # Write mode -- try update first, create if not found
        try:
            await github_api(
                "PATCH",
                f"/repos/{owner}/{repo}/actions/variables/{var_name}",
                json={"name": var_name, "value": new_value},
            )
            return [
                TextContent(
                    type="text",
                    text=f"Variable '{var_name}' updated to '{new_value}'.",
                )
            ]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Variable doesn't exist yet -- create it
                await github_api(
                    "POST",
                    f"/repos/{owner}/{repo}/actions/variables",
                    json={"name": var_name, "value": new_value},
                )
                return [
                    TextContent(
                        type="text",
                        text=f"Variable '{var_name}' created with value '{new_value}'.",
                    )
                ]
            raise


async def _get_deployment_status(args: dict) -> list[TextContent]:
    owner, repo = args["owner"], args["repo"]
    environment = args["environment"]
    per_page = args.get("per_page", 10)

    # Fetch deployments filtered by environment
    deployments = (
        await github_api(
            "GET",
            f"/repos/{owner}/{repo}/deployments",
            params={"environment": environment, "per_page": per_page},
        )
    ).json()

    if not deployments:
        return [
            TextContent(
                type="text",
                text=f"No deployments found for environment '{environment}'.",
            )
        ]

    lines = [
        f"Deployments to '{environment}' ({len(deployments)} most recent):"
    ]

    for dep in deployments:
        dep_id = dep["id"]
        ref = dep.get("ref", "?")
        sha = dep.get("sha", "?")[:7]
        creator = dep.get("creator", {}).get("login", "?")
        created = dep.get("created_at", "?")[:19].replace("T", " ")
        description = dep.get("description") or ""

        # Fetch the latest status for this deployment
        statuses = (
            await github_api(
                "GET",
                f"/repos/{owner}/{repo}/deployments/{dep_id}/statuses",
                params={"per_page": 1},
            )
        ).json()

        if statuses:
            state = statuses[0].get("state", "?")
            status_desc = statuses[0].get("description", "")
            env_url = statuses[0].get("environment_url", "")
        else:
            state = "unknown"
            status_desc = ""
            env_url = ""

        lines.append(f"\n  #{dep_id}  {state.upper()}")
        lines.append(f"    Ref: {ref}  ({sha})")
        lines.append(f"    By: @{creator}  at {created}")
        if description:
            lines.append(f"    Description: {description}")
        if status_desc:
            lines.append(f"    Status detail: {status_desc}")
        if env_url:
            lines.append(f"    URL: {env_url}")

    return [TextContent(type="text", text="\n".join(lines))]


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------
_DISPATCH = {
    "clone_repository": _clone_repository,
    "get_repo_rules": _get_repo_rules,
    "create_dummy_pr": _create_dummy_pr,
    "analyze_workflow_run": _analyze_workflow_run,
    "handle_failed_workflow": _handle_failed_workflow,
    "trigger_workflow": _trigger_workflow,
    "monitor_workflow_status": _monitor_workflow_status,
    "review_and_merge_pr": _review_and_merge_pr,
    "add_pr_comment": _add_pr_comment,
    "search_and_create_issue": _search_and_create_issue,
    "link_pr_to_issue": _link_pr_to_issue,
    "search_enterprise_codebase": _search_enterprise_codebase,
    "get_file_history": _get_file_history,
    "check_security_alerts": _check_security_alerts,
    "get_team_members": _get_team_members,
    "generate_and_publish_release": _generate_and_publish_release,
    "download_workflow_artifact": _download_workflow_artifact,
    "manage_repo_variables": _manage_repo_variables,
    "get_deployment_status": _get_deployment_status,
}


@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict):
    logger.info("Tool call: %s  args=%s", name, arguments)
    handler = _DISPATCH.get(name)
    if not handler:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    try:
        return await handler(arguments)
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return [TextContent(type="text", text=f"Error in {name}: {exc}")]


# ---------------------------------------------------------------------------
# FastAPI application + MCP SSE transport
# ---------------------------------------------------------------------------
app = FastAPI(title="GitHub Enterprise MCP Server")


@app.get("/health")
async def health():
    return {"status": "ok", "server": "github-mcp"}


async def _handle_sse(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


# SSE endpoint (raw Starlette route — MCP SDK operates at ASGI level)
app.add_route("/sse", _handle_sse)
# Message POST endpoint used by MCP clients
app.mount("/messages", sse_transport.handle_post_message)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not GITHUB_TOKEN:
        logger.error(
            "GITHUB_ENTERPRISE_TOKEN is not set. "
            "Add it to a .env file and restart."
        )
        raise SystemExit(1)
    logger.info("Starting GitHub MCP Server on http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
