# Phase 1 — HTTP core: client, rate limiter, errors, key handling

You are implementing phase 1 of the Riot API crawler rewrite. Phase 0 (scaffold, `crawler/config.py`, logging with key masking) is done.

## Read first
- `docs/DESIGN.md` §4 (config), §6 (rate limiting), §7 (error policy), §13 (verification).
- Existing `crawler/config.py`, `crawler/logging_setup.py`.

## Rules
- **No git write actions.** The user commits.
- `riot_client/` must not import from `crawler/`. Pass settings in as plain parameters/callbacks.
- No live API calls in tests — use `httpx.MockTransport` and a fake clock (inject `time`/`sleep` functions so tests run instantly).

## Tasks
1. `riot_client/errors.py` — exception hierarchy: `RiotApiError` (status, url without query secrets, method key), `BadRequest` (400), `NotFound` (404), `KeyInvalid` (401/403), `RateLimited` (429, retry_after, limit_type), `ServerError` (5xx), `NetworkError` (timeouts/connection), `InvalidResponse` (unparseable JSON). Mark which are retryable.
2. `riot_client/ratelimit.py`:
   - `RateWindow(limit, seconds)` tracking window start + count client-side;
   - `HostLimiter` per routing host: app windows + per-method windows (method key = endpoint template, e.g. `match-v5.matches`);
   - parse `X-App-Rate-Limit`, `X-App-Rate-Limit-Count`, `X-Method-Rate-Limit`, `X-Method-Rate-Limit-Count` (`"20:1,100:120"` format); re-sync counts with `max(local, server)`;
   - initial app limits `20:1,100:120` until headers arrive;
   - `async acquire(method_key)` waits until **every** relevant window has room under `limit × safety`;
   - `block(seconds, scope)` for 429 (`application` → whole host, `method` → that method);
   - `reset()` for key change;
   - `RateLimiterRegistry` keyed by host.
3. `riot_client/keys.py` — `KeyProvider`: holds the current key, `async wait_for_new_key()` that polls a callback (re-reads `.env` via `crawler.config.load_settings`, injected) every `KEY_POLL_SECONDS` **or** returns immediately when Enter is pressed on stdin if `sys.stdin.isatty()` (use `loop.add_reader`, fall back to polling only). Optionally call `notify-send` if available (ignore failure). Never logs the key.
4. `riot_client/http.py` — `RiotHttpClient`:
   - one shared `httpx.AsyncClient` (timeouts set, HTTP keep-alive), header `X-Riot-Token`, **never** key in URL;
   - `async get_json(host, path, params, method_key)` implementing DESIGN §7 exactly:
     - acquire limiter → request → update limiter from headers;
     - 429: `Retry-After` → block + retry; no `Retry-After` (`service`) → exponential backoff + jitter;
     - 401/403: global pause — **all** in-flight and future requests wait on one shared `asyncio.Event`; one coroutine runs `KeyProvider.wait_for_new_key()`, validates the new key with a cheap call (inject a validation callable), resets limiters, releases the event, and the original request is retried;
     - 5xx / network: backoff + jitter (1 s doubling, cap 60 s), up to `MAX_RETRIES`, then raise;
     - offline mode: if every request has failed for `OFFLINE_AFTER_SECONDS`, switch to probing every 30 s and do **not** consume retry attempts while offline;
     - 400 → `BadRequest`, 404 → `NotFound`, bad JSON → `InvalidResponse`;
   - expose state for the UI: `paused_reason` (`None | "key" | "offline"`), per-host request counters.
5. Tests (fake clock, MockTransport):
   - header parsing; windows respected (21st request in 1 s waits; 101st in 120 s waits); safety factor; count re-sync;
   - per-method window independent of app window; separate hosts independent;
   - 429 with `Retry-After` waits exactly that; 429 `service` without it backs off;
   - 401 → pause → key file changed → resumes and retries the same request; limiters reset;
   - 5xx retried then raised after `MAX_RETRIES`; timeouts likewise;
   - offline mode entered and exited; retries not consumed while offline;
   - key never appears in URLs, exception messages or log records.

## Acceptance
- `.venv/bin/pytest -q` passes, quickly (fake clock, no real sleeping).
- Smoke (real dev key): a tiny script or `python -m crawler status --ping` that calls `GET https://eun1.api.riotgames.com/lol/status/v4/platform-data` (or any cheap endpoint) through `RiotHttpClient` and prints the parsed rate-limit headers. Then put an invalid key in `.env`, run again, observe the pause message, fix `.env`, press Enter, observe it resume.

## Hand-over
Reply with: files, test summary, smoke command(s), design deviations (if any) with reasons. Then stop.
