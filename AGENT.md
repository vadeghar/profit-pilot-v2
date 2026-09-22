# Workspace conventions

You are running inside a Linux sandbox at `/workspace`. Treat each top-level
subdirectory as a project. The IDE auto-detects whatever you do -- no need to
tell the platform about ports or runtimes.

## Running things

- For one-shot commands (install, build, lint, test), use `run_bash`. If one
  overruns its timeout it is promoted to a background job, not killed -- wait
  on it with `await_job` or kill it with `stop_job`.
- For long-running services (web servers, watchers), use `start_job` -- it
  captures output natively (no nohup/redirect needed) and the IDE picks up
  listening ports automatically. Inspect a job with `await_job` (pass a
  `pattern` to block until a line appears, or `timeout_seconds: 0` for an
  instant snapshot).
- The user can share the preview URL with their phone via the QR button in
  the renderer.
- Don't pin to a specific port unless the user asked for one. Default
  framework ports (Vite 5173, Next 3000, Hono 3000, FastAPI 8000) all work.

## Persistence

- `<project>/.data/` is for durable per-project state (SQLite, blobs).
  Anything outside is fair game to delete on a clean rebuild.
- `<project>/.ze/logs/` is the conventional spot for background process logs.
  These are not checked in.

## Sharing & deploying

The platform is intentionally not a deploy target. When the user asks to
ship something, suggest one of:

- **Tunnels for sharing a dev server**: `ngrok http <port>` or
  `cloudflared tunnel --url http://localhost:<port>`.
- **Static sites**: `vercel`, `wrangler pages deploy`, `netlify deploy`.
- **Full apps**: `fly launch`, `railway up`, `render deploy`.
- **GitHub**: `gh repo create` then `git push`.

Prefer the user's existing tooling if any project already has a `vercel.json`,
`fly.toml`, `wrangler.toml`, `Dockerfile`, etc.

## Project-level overrides

Drop an `AGENT.md` at the project root to override or extend these
conventions. The agent reads workspace-level + project-level docs at the
start of every turn.
