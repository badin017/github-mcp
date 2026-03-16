# CLAUDE.md

## Project Overview
github-mcp — A Python MCP server using FastAPI + SSE that bridges AI coding assistants (Copilot/Windsurf/Cursor) with GitHub Enterprise (anbgithub.com).

## Tech Stack
- Python 3.11+, FastAPI, uvicorn
- MCP SDK (`mcp`) with SSE transport
- `httpx` for async GitHub API calls, `GitPython` for clone operations
- License: MIT

## Run
```bash
cp .env.example .env        # add your PAT
pip install -r requirements.txt
python main.py               # starts on http://localhost:8000
```

## MCP Tools (19 total)
**Core:**
- `clone_repository` — clone a repo via HTTPS+PAT
- `get_repo_rules` — fetch rulesets & branch protection
- `create_dummy_pr` — create a PR (blob→tree→commit via Git Data API)
- `analyze_workflow_run` — extract errors from a failed Actions run
- `handle_failed_workflow` — rerun failed jobs if error is transient

**CI/CD & Actions:**
- `trigger_workflow` — dispatch a workflow with custom inputs
- `monitor_workflow_status` — poll a run until completion (exponential backoff)

**PR & Code Review:**
- `review_and_merge_pr` — check statuses, approve, squash/rebase merge
- `add_pr_comment` — inline review comment on a file/line

**Issues & Project Management:**
- `search_and_create_issue` — deduplicate then create with labels+assignee
- `link_pr_to_issue` — append "Closes #N" to PR body

**Code Search & Navigation:**
- `search_enterprise_codebase` — GitHub Search API across org/repo
- `get_file_history` — commit log for a specific file

**Security & Compliance:**
- `check_security_alerts` — Dependabot + CodeQL alerts with severity
- `get_team_members` — list team members for reviewer assignment

**Release & Artifact Management:**
- `generate_and_publish_release` — auto-generate release notes between tags and publish
- `download_workflow_artifact` — download + extract build artifacts for AI analysis

**Environment & Configuration:**
- `manage_repo_variables` — read or update GitHub Actions repo variables
- `get_deployment_status` — fetch deployment history for an environment

## Key Endpoints
- `GET /health` — health check
- `GET /sse` — MCP SSE connection
- `POST /messages/` — MCP message transport

## Config
All config via `.env`:
- `GITHUB_ENTERPRISE_TOKEN` (required) — GitHub PAT
- `GITHUB_BASE_URL` — API base (default: `https://anbgithub.com/api/v3`)
- `GITHUB_HOST` — hostname (default: `anbgithub.com`)
