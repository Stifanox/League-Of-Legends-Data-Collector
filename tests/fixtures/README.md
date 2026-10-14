Real API responses used by tests. Never store an API key here.

Regenerate with a dev key: `python -m crawler status --probe eun1 --save-fixtures tests/fixtures`
(tests may then need new expected values).

| File | Source |
|---|---|
| `match.json` | match-v5 match (Ranked Solo/Duo, from a CHALLENGER player) |
| `timeline.json` | match-v5 timeline of the same match |
| `league_entries_page.json` | league-v4 `entries/RANKED_SOLO_5x5/GOLD/II?page=1`, trimmed to 5 entries |
| `apex_league.json` | league-v4 `challengerleagues/by-queue/RANKED_SOLO_5x5`, trimmed to 5 entries |
| `entries_by_puuid_ranked.json` | league-v4 `entries/by-puuid` of a participant (Solo/Duo + other queues) |
| `entries_by_puuid_empty.json` | `[]`, what `entries/by-puuid` returns for an unranked player |
| `account.json` | account-v1 `by-riot-id` of a participant |
