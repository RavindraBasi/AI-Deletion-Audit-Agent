# Agent Trace Instructions

Please submit the complete agent trace for the work you did on this challenge as a **ZIP file**. A trace helps us understand the prompts, model responses, tool calls, and work performed during the session and improve our evaluation from the next time.

Include every session you used for this challenge, including sessions in which you planned, coded, debugged, or reviewed the solution. If you used more than one agent harness, include traces from each one. Keep the original files and folder structure where practical; do not convert JSONL, SQLite, or other structured logs into screenshots or summaries.

## Before you start

1. Finish the session(s) you intend to submit, then quit the app or CLI before copying its files. This helps ensure logs are written completely.
2. Create a temporary folder named `agent-trace` somewhere easy to find, such as your Desktop. Copy only the relevant trace files into it. Keep each tool in its own subfolder, for example `agent-trace/claude-code/` and `agent-trace/codex/`.
3. **Identify which session files belong to this challenge** (do not copy your entire home directory or application-data folder):
   - **By project folder name:** Tools like Claude Code and Cursor group transcripts under a folder named after your checkout path (for example, containing `hackerrank-orchestrate-october26`).
   - **By `workspace.json`:** VS Code (Copilot), Cursor, and Windsurf `workspaceStorage/<workspace-hash>/` folders contain a `workspace.json` file showing the workspace path.
   - **By timestamp or repo path in the trace:** Tools like Codex, Antigravity, OpenCode, and Windsurf Cascade store sessions globally or by date — check the date folder, file modification time, `opencode session list`, or search the trace file for your repository path to pick only the sessions from this challenge.
4. Review the staged files before sharing. Traces can contain source code, local file paths, prompts, tool arguments and output, screenshots, and other private information. Remove or redact secrets (API keys, tokens, passwords, cookies, private keys), personal information, and material you are not allowed to share. Do not include authentication files, settings, or credentials.
5. Zip the `agent-trace` folder using your operating system’s built-in archive tool. Name the result `agent-trace.zip`.

## Claude Code

### Where traces are stored

Claude Code stores session transcripts by project under:

- **macOS/Linux:** `~/.claude/projects/`
- **Windows:** `%USERPROFILE%\.claude\projects\`

Each project folder contains session files, commonly JSONL files named with a session ID. Find the folder corresponding to your challenge checkout and copy the session files you created for this challenge into `agent-trace/claude-code/`. The folder name may encode the project path, so check the session content and timestamps to identify the right files.

### Export a readable transcript

In each Claude Code session, run:

```text
/export
```

Save the exported transcript in `agent-trace/claude-code/`. `/export` produces a readable conversation export; include the original session JSONL files as well when available because they preserve the structured trace.

If you used Claude in the Claude web app or desktop chat rather than Claude Code, that is a different product history: use **Settings → Privacy → Export data** and follow the download instructions. Include only the relevant conversation export.

## OpenAI Codex

### Where traces are stored

Codex CLI and local Codex app sessions commonly store structured rollout files under:

- **macOS/Linux:** `~/.codex/sessions/`
- **Windows:** `%USERPROFILE%\.codex\sessions\`

The files are organized into date folders and are typically named `rollout-*.jsonl`. If you set `CODEX_HOME`, look under `$CODEX_HOME/sessions/` instead. Copy the rollout JSONL files for the challenge sessions into `agent-trace/codex/`. You can use their timestamps and session IDs to identify them. Include a matching `session_index.jsonl` only if useful for mapping session IDs to titles; review it for unrelated information first.

Do **not** copy `auth.json`, `config.toml`, credential stores, or the whole `.codex` directory.

If you used Codex only in a hosted/cloud interface and there is no local rollout file, use the interface’s conversation export or share/download option if available. If no transcript download is available, submit the conversation transcript in the format the interface provides and note that it was a hosted session with no local trace file.

## Google Antigravity

Antigravity writes a `transcript.jsonl` for each conversation under that conversation’s `brain` folder. The transcript includes agent activity; the same folder can also contain artifacts and screenshots associated with the conversation. Preserve the full relevant conversation folder when you need its artifacts to understand the trace.

Typical locations:

- **Antigravity app:** `~/.gemini/antigravity/brain/<conversation-id>/`
- **Antigravity CLI:** `~/.gemini/antigravity-cli/brain/<conversation-id>/`
- **Antigravity IDE:** `~/.gemini/antigravity-ide/brain/<conversation-id>/`
- **Windows:** look under `%USERPROFILE%\.gemini\antigravity\brain\`, `%USERPROFILE%\.gemini\antigravity-cli\brain\`, or `%USERPROFILE%\.gemini\antigravity-ide\brain\`, depending on the product used.

Within the relevant conversation folder, the transcript is at:

```text
.system_generated/logs/transcript.jsonl
```

Copy the complete `<conversation-id>` folder(s) for this challenge to `agent-trace/antigravity/`. Do not copy the entire `.gemini` directory: it may contain unrelated data. Antigravity CLI’s `last_conversations.json` is a session lookup cache, not the transcript itself.

## OpenCode

OpenCode stores session and message data in a local SQLite database. Its default application-data directory is:

- **macOS/Linux:** `~/.local/share/opencode/`
- **Windows:** `%USERPROFILE%\.local\share\opencode\`

The database is usually `opencode.db`. To find its active path (including custom XDG or `OPENCODE_DB` locations), run:

```sh
opencode db path
```

Prefer exporting each relevant session as JSON rather than copying the database. List sessions and export each challenge session:

```sh
opencode session list --format json
opencode export <session-id> > <session-id>.json
```

To redact sensitive transcript and file data during export, add `--sanitize`:

```sh
opencode export <session-id> --sanitize > <session-id>.json
```

Place the exported files in `agent-trace/opencode/`. If the CLI is unavailable and you need to provide the raw database instead, quit OpenCode first, copy only `opencode.db` into the staging folder, and inspect the copy for unrelated/private data. Never include `auth.json`, which can contain provider credentials. The default OpenCode data folder also contains logs and snapshots; those are not substitutes for the session exports.

## Cursor

### Where traces are stored

Cursor stores structured agent transcripts by project under:

- **macOS/Linux:** `~/.cursor/projects/<project-folder>/agent-transcripts/`
- **Windows:** `%USERPROFILE%\.cursor\projects\<project-folder>\agent-transcripts\`

The `<project-folder>` name encodes the workspace path (for example, `Users-name-repo-hackerrank-orchestrate-october26`). Inside `agent-transcripts/`, each session is stored either as `<session-id>.jsonl` or inside a `<session-id>/` folder containing `<session-id>.jsonl` and any `subagents/*.jsonl` traces. Copy the session files and folders for this challenge into `agent-trace/cursor/`, preserving any `subagents/` subfolders.

If `agent-transcripts/` is not present in your version of Cursor, locate the workspace storage folder for your challenge checkout under:

- **macOS:** `~/Library/Application Support/Cursor/User/workspaceStorage/`
- **Linux:** `~/.config/Cursor/User/workspaceStorage/`
- **Windows:** `%APPDATA%\Cursor\User\workspaceStorage\`

Check `workspace.json` inside each `<workspace-hash>` folder to find the one matching your challenge repository path, and copy its session data (such as `state.vscdb`) after quitting Cursor.

### Export a readable transcript

In each Cursor chat or agent session, open the panel menu (**`...`**) and choose **Export Chat** to save a Markdown transcript. Place the exported `.md` file(s) in `agent-trace/cursor/` alongside the structured JSONL files from `agent-transcripts/`.

Do **not** copy `mcp.json`, `auth.json`, user settings, credential files, or your entire `.cursor` directory.

## GitHub Copilot

### Export from VS Code

In Visual Studio Code, open each Copilot Chat / Edits / Agent session you used for this challenge, open the Command Palette (`Cmd+Shift+P` on macOS, `Ctrl+Shift+P` on Windows/Linux), and run:

```text
Chat: Export Chat...
```

Save each exported `.json` file into `agent-trace/copilot/`.

### Where local session files are stored

VS Code also stores structured chat and editing session files per workspace under:

- **macOS:** `~/Library/Application Support/Code/User/workspaceStorage/<workspace-hash>/`
- **Linux:** `~/.config/Code/User/workspaceStorage/<workspace-hash>/`
- **Windows:** `%APPDATA%\Code\User\workspaceStorage\<workspace-hash>\`

Check `workspace.json` in each `<workspace-hash>` directory to find the folder whose `"folder"` URI matches your challenge checkout. Copy its `chatSessions/` and `chatEditingSessions/` folders (which contain `.json` or `.jsonl` session files) into `agent-trace/copilot/`.

If you used **GitHub Copilot CLI** in the terminal, also copy the relevant session files from:

- **macOS/Linux:** `~/.copilot/session-state/` and `~/.copilot/logs/`
- **Windows:** `%USERPROFILE%\.copilot\session-state\` and `%USERPROFILE%\.copilot\logs\`

Do **not** copy `~/.copilot/config.json`, `hosts.json`, `apps.json`, or any authentication tokens.

## Windsurf

### Export from Cascade

In each Windsurf Cascade session used for this challenge, open the conversation menu (`...` or the export action in the Cascade panel) and export the conversation/trajectory (Markdown or JSON). Save the exported files in `agent-trace/windsurf/`.

### Where local session files are stored

Windsurf stores local Cascade session trajectories under:

- **macOS/Linux:** `~/.codeium/windsurf/cascade/`
- **Windows:** `%USERPROFILE%\.codeium\windsurf\cascade\`

Copy the session files created during this challenge (identifying them by modification timestamp) into `agent-trace/windsurf/`. If your Windsurf version also writes workspace-scoped session data, check the workspace storage directory matching `workspace.json` for your checkout under:

- **macOS:** `~/Library/Application Support/Windsurf/User/workspaceStorage/<workspace-hash>/`
- **Linux:** `~/.config/Windsurf/User/workspaceStorage/<workspace-hash>/`
- **Windows:** `%APPDATA%\Windsurf\User\workspaceStorage\<workspace-hash>\`

Do **not** copy `~/.codeium/windsurf/mcp_config.json`, `user_settings.pb`, installation IDs, or authentication credentials.

## Other Harnesses

If any agent harness or coding tool you used is not listed above:

1. Use the tool’s built-in session/trajectory export command or locate its local session storage directory for this challenge.
2. Copy the raw structured trace files (such as `.jsonl`, `.json`, or `.db`) and any exported readable transcripts into `agent-trace/<harness-name>/`.
3. Remove or redact any API keys, tokens, credentials, and authentication/config files.
4. Follow the steps below to package `agent-trace.zip` and upload the file with your submission.

## Create the ZIP

### macOS or Linux

From the directory containing `agent-trace/`:

```sh
zip -r agent-trace.zip agent-trace
```

### Windows PowerShell

From the directory containing `agent-trace/`:

```powershell
Compress-Archive -Path .\agent-trace -DestinationPath .\agent-trace.zip -Force
```

### Verify the archive

Before submitting, verify that `agent-trace.zip` is readable, preserves per-tool subfolders (for example `agent-trace/claude-code/`, `agent-trace/codex/`, `agent-trace/antigravity/`, `agent-trace/opencode/`, `agent-trace/cursor/`, `agent-trace/copilot/`, `agent-trace/windsurf/`), and contains no credentials or unrelated files:

- **macOS or Linux:**

```sh
unzip -l agent-trace.zip
```

- **Windows PowerShell:**

```powershell
Expand-Archive -Path .\agent-trace.zip -DestinationPath .\agent-trace-check -Force
Get-ChildItem -Path .\agent-trace-check -Recurse
```

Open the ZIP once after creating it and confirm it contains the intended traces and no credentials or unrelated files. Submit `agent-trace.zip` with your challenge submission.
