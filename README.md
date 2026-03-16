# github-mcp

A **Model Context Protocol (MCP) server** built with Python and FastAPI that connects your AI coding assistant (GitHub Copilot, Windsurf, Cursor) to your **GitHub Enterprise** instance (`anbgithub.com`).

The server exposes 19 tools over **Server-Sent Events (SSE)** that let your AI assistant clone repos, manage CI/CD pipelines, review and merge pull requests, track issues, search code, publish releases, manage environment config, and audit security alerts -- all through natural language.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Starting the Server](#starting-the-server)
- [Connecting to Your AI Assistant](#connecting-to-your-ai-assistant)
  - [GitHub Copilot (VS Code)](#github-copilot-vs-code)
  - [Windsurf](#windsurf)
  - [Cursor](#cursor)
- [Available Tools](#available-tools)
  - [Core Repository Operations](#1-core-repository-operations)
  - [CI/CD & Actions Management](#2-cicd--actions-management)
  - [Pull Request & Code Review](#3-pull-request--code-review)
  - [Issue Tracking & Project Management](#4-issue-tracking--project-management)
  - [Code Search & Navigation](#5-code-search--navigation)
  - [Security & Enterprise Compliance](#6-security--enterprise-compliance)
  - [Release & Artifact Management](#7-release--artifact-management)
  - [Environment & Configuration Automation](#8-environment--configuration-automation)
- [Usage Examples](#usage-examples)
- [API Endpoints](#api-endpoints)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **Python 3.11+**
- **Git** installed and available on your PATH
- A **GitHub Enterprise Personal Access Token (PAT)** from `anbgithub.com` with the following scopes:
  - `repo` (full control of repositories)
  - `workflow` (manage GitHub Actions)
  - `read:org` (read org and team membership)
  - `security_events` (read security alerts -- Dependabot / CodeQL)

## Installation

```bash
# 1. Clone this repository
git clone https://github.com/badin017/github-mcp.git
cd github-mcp

# 2. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt
```

## Configuration

```bash
# Copy the example env file
cp .env.example .env
```

Open `.env` and set your token:

```env
# REQUIRED -- your GitHub Enterprise PAT
GITHUB_ENTERPRISE_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# OPTIONAL -- override if your Enterprise instance uses a different URL
# GITHUB_BASE_URL=https://anbgithub.com/api/v3
# GITHUB_HOST=anbgithub.com
```

> **Security note:** `.env` is already listed in `.gitignore` and will never be committed.

## Starting the Server

```bash
python3 main.py
```

You should see:

```
2026-03-15 10:00:00 INFO     github-mcp  Starting GitHub MCP Server on http://localhost:8000
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Verify it is running:

```bash
curl http://localhost:8000/health
# {"status":"ok","server":"github-mcp"}
```

---

## Connecting to Your AI Assistant

### GitHub Copilot (VS Code)

1. Open VS Code and make sure GitHub Copilot and Copilot Chat extensions are installed.
2. Open your **User Settings** (`Cmd+,` on macOS / `Ctrl+,` on Windows).
3. Search for `mcp` in Settings, or directly edit your `settings.json`:

```jsonc
// settings.json
{
  "github.copilot.chat.mcpServers": {
    "github-enterprise": {
      "type": "sse",
      "url": "http://localhost:8000/sse"
    }
  }
}
```

4. Alternatively, create a `.vscode/mcp.json` file at the root of your workspace:

```json
{
  "servers": {
    "github-enterprise": {
      "type": "sse",
      "url": "http://localhost:8000/sse"
    }
  }
}
```

5. Reload VS Code (`Cmd+Shift+P` > **Developer: Reload Window**).
6. Open **Copilot Chat** and switch to **Agent Mode** (click the mode selector at the top of the chat panel).
7. You should see the MCP tools listed when you click the tools icon. You can now ask Copilot to use them in natural language.

### Windsurf

1. Open Windsurf and go to **Settings** (gear icon) > **MCP Servers** (under the Cascade section).
2. Click **Add Server** and choose **Server-Sent Events (SSE)**.
3. Enter the following configuration:

```json
{
  "mcpServers": {
    "github-enterprise": {
      "serverUrl": "http://localhost:8000/sse"
    }
  }
}
```

4. Alternatively, edit the Windsurf MCP config file directly:
   - **macOS:** `~/.windsurf/mcp_config.json`
   - **Windows:** `%APPDATA%\Windsurf\mcp_config.json`
   - **Linux:** `~/.config/Windsurf/mcp_config.json`

5. Restart Windsurf or reload the MCP configuration.
6. Open Cascade (Windsurf's AI panel). The 15 tools will now be available to the AI agent.

### Cursor

1. Open **Cursor Settings** (`Cmd+,`) > **MCP Servers**.
2. Click **+ Add new MCP server**.
3. Set type to **sse** and enter the URL:

```json
{
  "mcpServers": {
    "github-enterprise": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

4. Alternatively, create or edit `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "github-enterprise": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

5. Restart Cursor. The tools will appear in Agent mode in the Composer panel.

---

## Available Tools

### 1. Core Repository Operations

#### `clone_repository`

Clone a repository from GitHub Enterprise to your local machine using PAT-authenticated HTTPS.

| Parameter    | Type   | Required | Description                                  |
|-------------|--------|----------|----------------------------------------------|
| `repo_name` | string | Yes      | Full `owner/repo` name                       |
| `local_path`| string | No       | Local directory to clone into (default: `./cloned_repos/<repo>`) |

**Example prompt:**
> "Clone the repo `my-org/backend-service` to `./projects/backend`."

---

#### `get_repo_rules`

Retrieve active rulesets (newer GitHub API) and branch protection rules for the default branch.

| Parameter | Type   | Required | Description        |
|----------|--------|----------|--------------------|
| `owner`  | string | Yes      | Repository owner   |
| `repo`   | string | Yes      | Repository name    |

**Example prompt:**
> "What branch protection rules are configured on `my-org/api-gateway`?"

**Returns:** Rulesets with enforcement levels, required status checks, required approvals, CODEOWNERS requirements, signing requirements, and push restrictions.

---

#### `create_dummy_pr`

Create a test pull request by appending a space to `README.md` using the Git Data API (blob, tree, commit).

| Parameter     | Type   | Required | Description                                      |
|--------------|--------|----------|--------------------------------------------------|
| `owner`      | string | Yes      | Repository owner                                 |
| `repo`       | string | Yes      | Repository name                                  |
| `base_branch`| string | No       | Target branch (defaults to repo's default branch) |

**Example prompt:**
> "Create a dummy PR on `my-org/test-repo` targeting the `develop` branch."

**Returns:** PR number, URL, and the name of the created branch.

---

### 2. CI/CD & Actions Management

#### `analyze_workflow_run`

Analyse a GitHub Actions workflow run and extract detailed error information from failed steps, including log tails.

| Parameter | Type   | Required | Description                                  |
|----------|--------|----------|----------------------------------------------|
| `run_url`| string | Yes      | Full URL to the workflow run (e.g. `https://anbgithub.com/org/repo/actions/runs/123`) |

**Example prompt:**
> "Analyze this failed workflow run: https://anbgithub.com/my-org/service/actions/runs/456789"

**Returns:** Workflow name, status, conclusion, branch, event trigger, failed job names, failed step names, and the last 50 lines of logs from each failed job.

---

#### `handle_failed_workflow`

Automatically re-run failed jobs if the failure is caused by a transient error (internal error, timeout, runner error, 502/503).

| Parameter | Type   | Required | Description                        |
|----------|--------|----------|------------------------------------|
| `run_url`| string | Yes      | Full URL to the workflow run        |
| `action` | string | Yes      | Action to take -- use `rerun_failed` |

**Example prompt:**
> "The build at https://anbgithub.com/my-org/service/actions/runs/456789 failed with a timeout. Rerun the failed jobs."

**Behavior:** Scans failed job logs for transient error indicators. If found, triggers the rerun-failed-jobs API. If not transient, recommends manual investigation.

---

#### `trigger_workflow`

Trigger a `workflow_dispatch` event to start a new workflow run with custom input parameters.

| Parameter     | Type   | Required | Description                                           |
|--------------|--------|----------|-------------------------------------------------------|
| `owner`      | string | Yes      | Repository owner                                      |
| `repo`       | string | Yes      | Repository name                                       |
| `workflow_id`| string | Yes      | Workflow ID or filename (e.g. `build.yml`, `deploy.yml`) |
| `ref`        | string | No       | Git ref to run against (defaults to repo default branch) |
| `inputs`     | object | No       | JSON object of workflow input key-value pairs          |

**Example prompt:**
> "Trigger the `deploy.yml` workflow on `my-org/frontend` for the `staging` branch with input `environment` set to `staging`."

---

#### `monitor_workflow_status`

Poll a running workflow until it completes. Uses exponential backoff (starts at 10s, caps at 60s, times out at 600s by default).

| Parameter       | Type    | Required | Description                                  |
|----------------|---------|----------|----------------------------------------------|
| `owner`        | string  | Yes      | Repository owner                             |
| `repo`         | string  | Yes      | Repository name                              |
| `run_id`       | integer | Yes      | Workflow run ID to monitor                   |
| `poll_interval`| integer | No       | Initial poll interval in seconds (default 10) |
| `max_wait`     | integer | No       | Maximum wait time in seconds (default 600)    |

**Example prompt:**
> "Monitor workflow run 789012 on `my-org/backend` until it finishes."

**Returns:** Timestamped status updates and the final conclusion (success/failure) with a link to the run.

---

### 3. Pull Request & Code Review

#### `review_and_merge_pr`

Check if all CI status checks and check-runs have passed on a PR. If everything is green, approve the PR and merge it.

| Parameter      | Type    | Required | Description                                   |
|---------------|---------|----------|-----------------------------------------------|
| `owner`       | string  | Yes      | Repository owner                              |
| `repo`        | string  | Yes      | Repository name                               |
| `pr_number`   | integer | Yes      | Pull request number                           |
| `merge_method`| string  | No       | `squash` (default), `rebase`, or `merge`      |

**Example prompt:**
> "If all checks pass on PR #42 in `my-org/api`, approve it and squash-merge."

**Behavior:** Blocks if any check is failing or still running. On success, approves and merges in one operation.

---

#### `add_pr_comment`

Add an inline review comment on a specific file and line number in a pull request diff.

| Parameter   | Type    | Required | Description                                  |
|------------|---------|----------|----------------------------------------------|
| `owner`    | string  | Yes      | Repository owner                             |
| `repo`     | string  | Yes      | Repository name                              |
| `pr_number`| integer | Yes      | Pull request number                          |
| `file_path`| string  | Yes      | Relative path of the file to comment on      |
| `line`     | integer | Yes      | Line number in the diff                       |
| `comment`  | string  | Yes      | Comment body (supports markdown)             |

**Example prompt:**
> "On PR #42 in `my-org/api`, add a review comment on line 15 of `src/handler.py` saying 'This function should validate the input before processing.'"

---

### 4. Issue Tracking & Project Management

#### `search_and_create_issue`

Search the repository's open issues for duplicates first. If no match is found, create a new issue with labels and assign it to the authenticated user.

| Parameter | Type     | Required | Description                                      |
|----------|----------|----------|--------------------------------------------------|
| `owner`  | string   | Yes      | Repository owner                                 |
| `repo`   | string   | Yes      | Repository name                                  |
| `title`  | string   | Yes      | Issue title (also used as the search query)       |
| `body`   | string   | No       | Issue body in markdown                            |
| `labels` | string[] | No       | Labels to apply (e.g. `["bug", "high-priority"]`) |

**Example prompt:**
> "Create a bug issue on `my-org/backend` titled 'NullPointerException in UserService.getProfile' with the label `bug`."

**Behavior:** If a similar open issue already exists, it returns the duplicates instead of creating a new one.

---

#### `link_pr_to_issue`

Update a pull request's description to include `Closes #<issue_number>` so that merging the PR automatically closes the linked issue.

| Parameter      | Type    | Required | Description         |
|---------------|---------|----------|---------------------|
| `owner`       | string  | Yes      | Repository owner    |
| `repo`        | string  | Yes      | Repository name     |
| `pr_number`   | integer | Yes      | Pull request number |
| `issue_number`| integer | Yes      | Issue number to link |

**Example prompt:**
> "Link PR #42 to issue #17 in `my-org/backend`."

**Behavior:** Idempotent -- if the closing reference already exists in the PR body, it skips the update.

---

### 5. Code Search & Navigation

#### `search_enterprise_codebase`

Search for code across your entire GitHub Enterprise instance or scoped to a specific org/repo using the GitHub Search API.

| Parameter  | Type    | Required | Description                                         |
|-----------|---------|----------|-----------------------------------------------------|
| `query`   | string  | Yes      | Search query (function names, error codes, configs)  |
| `owner`   | string  | No       | Org or user to scope the search to                   |
| `repo`    | string  | No       | Repository to scope the search to                    |
| `language`| string  | No       | Filter by programming language (e.g. `python`, `java`) |
| `per_page`| integer | No       | Results per page (default 10, max 100)               |

**Example prompt:**
> "Search for `DatabaseConnectionPool` across all repos in `my-org`, filtered to Java files."

**Returns:** List of matching files with repository name, file path, and code fragment previews.

---

#### `get_file_history`

Fetch the commit history for a specific file to understand who changed it, when, and why.

| Parameter   | Type    | Required | Description                                  |
|------------|---------|----------|----------------------------------------------|
| `owner`    | string  | Yes      | Repository owner                             |
| `repo`     | string  | Yes      | Repository name                              |
| `file_path`| string  | Yes      | Path to the file within the repository        |
| `branch`   | string  | No       | Branch to query (defaults to repo default)    |
| `per_page` | integer | No       | Number of commits to return (default 15)      |

**Example prompt:**
> "Show me the commit history for `src/config/database.yml` in `my-org/backend`."

**Returns:** Commit SHA, date, author, and first line of each commit message.

---

### 6. Security & Enterprise Compliance

#### `check_security_alerts`

Retrieve active Dependabot vulnerability alerts and Code Scanning (CodeQL) alerts for a repository.

| Parameter | Type   | Required | Description                                    |
|----------|--------|----------|------------------------------------------------|
| `owner`  | string | Yes      | Repository owner                               |
| `repo`   | string | Yes      | Repository name                                |
| `state`  | string | No       | Filter by state: `open` (default), `fixed`, `dismissed` |

**Example prompt:**
> "Check for open security vulnerabilities in `my-org/payment-service`."

**Returns:** For each alert: severity level, affected package, advisory summary, and recommended fix version.

---

#### `get_team_members`

List all members of a specific team within an organization. Useful for dynamically assigning PR reviewers based on team structure.

| Parameter   | Type   | Required | Description                          |
|------------|--------|----------|--------------------------------------|
| `org`      | string | Yes      | Organization name                    |
| `team_slug`| string | Yes      | Team slug (URL-friendly team name)   |

**Example prompt:**
> "List all members of the `platform-eng` team in `my-org`."

**Returns:** List of team members with their GitHub usernames and IDs.

---

### 7. Release & Artifact Management

#### `generate_and_publish_release`

Auto-generate release notes summarizing all merged PRs between two tags and publish a new GitHub Release.

| Parameter        | Type    | Required | Description                                        |
|-----------------|---------|----------|----------------------------------------------------|
| `owner`         | string  | Yes      | Repository owner                                   |
| `repo`          | string  | Yes      | Repository name                                    |
| `tag_name`      | string  | Yes      | Tag for the new release (e.g. `v2.1.0`)            |
| `previous_tag`  | string  | Yes      | Previous tag to diff against for release notes     |
| `target_branch` | string  | No       | Branch the tag points to (defaults to repo default) |
| `draft`         | boolean | No       | Create as draft release (default `false`)           |
| `prerelease`    | boolean | No       | Mark as prerelease (default `false`)                |

**Example prompt:**
> "Generate release notes for `v2.1.0` based on changes since `v2.0.0` in `my-org/backend` and publish the release."

**Returns:** Release URL, generated name, tag info, and the full auto-generated release notes body listing all merged PRs, new contributors, etc.

---

#### `download_workflow_artifact`

Download and extract a build artifact (compiled logs, coverage reports, crash dumps, etc.) from a workflow run into the local workspace so the AI can analyze the raw files.

| Parameter       | Type    | Required | Description                                                      |
|----------------|---------|----------|------------------------------------------------------------------|
| `owner`        | string  | Yes      | Repository owner                                                 |
| `repo`         | string  | Yes      | Repository name                                                  |
| `run_id`       | integer | Yes      | Workflow run ID                                                  |
| `artifact_name`| string  | No       | Name of the artifact to download (downloads the first if omitted) |
| `output_dir`   | string  | No       | Local directory to extract into (default `./artifacts/<run_id>`)  |

**Example prompt:**
> "Download the test coverage artifact from workflow run 123456 in `my-org/backend` and tell me which files have low coverage."

**Returns:** Extraction path, artifact size, and a list of all extracted files. The AI can then read the extracted files directly.

---

### 8. Environment & Configuration Automation

#### `manage_repo_variables`

Read or update GitHub Actions repository variables (non-secret configuration values). Useful for toggling feature flags or changing configuration across environments directly from the AI chat.

| Parameter       | Type   | Required | Description                                      |
|----------------|--------|----------|--------------------------------------------------|
| `owner`        | string | Yes      | Repository owner                                 |
| `repo`         | string | Yes      | Repository name                                  |
| `variable_name`| string | Yes      | Variable name to read or update                  |
| `value`        | string | No       | New value to set (omit to read current value)    |

**Example prompt (read):**
> "What is the current value of the `FEATURE_FLAG_NEW_UI` variable in `my-org/frontend`?"

**Example prompt (write):**
> "Set the `DEPLOY_TARGET` variable to `staging-2` in `my-org/frontend`."

**Behavior:** When writing, the tool attempts to update the variable. If it doesn't exist yet, it creates it automatically.

---

#### `get_deployment_status`

Fetch the latest deployment history for a specific environment (e.g. `production`, `staging`). Lets the AI verify whether a recent PR actually made it to production.

| Parameter     | Type    | Required | Description                                      |
|--------------|---------|----------|--------------------------------------------------|
| `owner`      | string  | Yes      | Repository owner                                 |
| `repo`       | string  | Yes      | Repository name                                  |
| `environment`| string  | Yes      | Environment name (e.g. `production`, `staging`)  |
| `per_page`   | integer | No       | Number of deployments to return (default 10)      |

**Example prompt:**
> "Show me the last 5 deployments to `production` for `my-org/api-gateway`. Did the latest one succeed?"

**Returns:** For each deployment: status (SUCCESS/FAILURE/PENDING), git ref, commit SHA, deployer, timestamp, description, and environment URL.

---

## Usage Examples

Here are end-to-end scenarios you can accomplish by prompting your AI assistant:

### Deploy and Monitor

> "Trigger the `deploy.yml` workflow on `my-org/frontend` for the `release/v2.1` branch with input `environment=production`, then monitor it until it completes."

The AI will call `trigger_workflow` followed by `monitor_workflow_status`.

### Full PR Lifecycle

> "Create a bug issue titled 'Fix login timeout on mobile' with label `bug` on `my-org/app`. Then link PR #55 to that issue. Once CI passes, approve and squash-merge the PR."

The AI will chain `search_and_create_issue` > `link_pr_to_issue` > `review_and_merge_pr`.

### Investigate a Failure

> "Analyze the failed workflow at https://anbgithub.com/my-org/service/actions/runs/789. If it's a transient error, rerun the failed jobs."

The AI will call `analyze_workflow_run` and then `handle_failed_workflow`.

### Debug Build Artifacts

> "Download the test-results artifact from run 456789 in `my-org/backend`. What tests failed?"

The AI will call `download_workflow_artifact`, then read the extracted test report files to identify failures.

### Release a New Version

> "Generate release notes for `v3.0.0` from `v2.9.0` in `my-org/platform` and publish it. Then check if the production deployment succeeded."

The AI will chain `generate_and_publish_release` > `get_deployment_status`.

### Environment Config Update

> "Set the `MAINTENANCE_MODE` variable to `true` on `my-org/api`, trigger the `deploy.yml` workflow, and monitor it until done."

The AI will chain `manage_repo_variables` > `trigger_workflow` > `monitor_workflow_status`.

### Security Audit

> "Check for open security alerts in `my-org/payment-service` and create issues for any critical vulnerabilities."

The AI will call `check_security_alerts` and then `search_and_create_issue` for each critical finding.

### Code Archaeology

> "Search for `parseAuthToken` across `my-org` in TypeScript files, then show me the commit history for the file where it's defined."

The AI will call `search_enterprise_codebase` followed by `get_file_history`.

---

## API Endpoints

| Endpoint          | Method | Description                      |
|-------------------|--------|----------------------------------|
| `/health`         | GET    | Health check                     |
| `/sse`            | GET    | MCP SSE connection (for clients) |
| `/messages/`      | POST   | MCP message transport            |

---

## Troubleshooting

### Server won't start

```
GITHUB_ENTERPRISE_TOKEN is not set. Add it to a .env file and restart.
```

Make sure you created a `.env` file from `.env.example` and set your PAT.

### Authentication failed

```
Error: Authentication failed - check GITHUB_ENTERPRISE_TOKEN.
```

Your token may be expired or lack the required scopes. Generate a new PAT at `https://anbgithub.com/settings/tokens` with `repo`, `workflow`, `read:org`, and `security_events` scopes.

### Rate limit exceeded

```
Error: Rate limit exceeded. Resets at epoch 1741234567.
```

GitHub Enterprise has API rate limits. The server reports when your limit resets. Wait until then or use a token with higher limits.

### Tools not showing in Copilot / Windsurf / Cursor

1. Make sure the server is running (`curl http://localhost:8000/health`).
2. Verify the MCP config points to `http://localhost:8000/sse`.
3. Restart / reload your editor after changing MCP configuration.
4. In Copilot, ensure you are in **Agent Mode** (not Ask or Edit mode).

### 404 on Rulesets / Security Alerts

Some GitHub Enterprise versions may not support all API endpoints (rulesets, Dependabot alerts, code scanning). The tools handle this gracefully and report which endpoints are unavailable.

---

## License

[MIT](LICENSE)
