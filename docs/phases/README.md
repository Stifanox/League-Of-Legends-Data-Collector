# Implementation phases

Run the phases **in order**. Each file is a self-contained prompt for an AI coding agent. Paste it (or point the agent at the file) on branch `rewrite/riot-api-2026`.

| Phase | File | Result |
|---|---|---|
| 0 | [phase-0-scaffold.md](phase-0-scaffold.md) | Clean repo, package layout, config, secrets, logging |
| 1 | [phase-1-http-core.md](phase-1-http-core.md) | Async HTTP client, rate limiter, error/retry policy, key pause |
| 2 | [phase-2-endpoints.md](phase-2-endpoints.md) | match-v5, league-v4, account-v1, Data Dragon wrappers |
| 3 | [phase-3-storage-state.md](phase-3-storage-state.md) | SQLite state, raw `jsonl.gz` writer, resume, shutdown |
| 4 | [phase-4-crawler.md](phase-4-crawler.md) | Multi-region stratified crawler, ranks, CLI, progress |
| 5 | [phase-5-export.md](phase-5-export.md) | TSV + Parquet export of all tables |
| 6 | [phase-6-run-docs.md](phase-6-run-docs.md) | `run.sh`, README, end-to-end smoke test |

Workflow per phase: agent implements → tests green → agent hands over (summary, smoke command, open questions) → **user reviews, commits and pushes**. Agents never run git write commands.
