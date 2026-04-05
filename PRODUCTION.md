# Production Readiness

Status as of 2026-04-05. Updated as work is completed.

## Test Results (baseline)

| Suite | Result | Notes |
|-------|--------|-------|
| Unit: catalog + search | 39/39 | All passing |
| Trigger (should search) | 20/20 (100%) | 21 cases, concurrent |
| Trigger (should not search) | 17/17 (100%) | 17 cases, concurrent |
| E2E delivery | 15/15 (100%) | Full pipeline: search → find → read |
| MCP integration | skipped | Requires manual server start |

## Work Items

### P0 — Fix before any deployment

- [x] **Search tiebreaker bug** — Fixed. Results now sort by score desc, then
  skill name asc as a stable secondary key. No more non-deterministic results
  when scores are equal.

- [ ] **Skills-dir hardcoded in tests** — `conftest.py` and `test_e2e.py` both
  hardcode `/home/brealx/repos/skills/skills`. Tests won't run for anyone else
  or in CI. Should read from an env var with a sensible fallback.

### P1 — Required for company-wide rollout

- [ ] **Authentication** — No auth on the server. Anyone who can reach the
  endpoint can read all skills. At minimum: a static bearer token checked on
  every request. Full OAuth/SSO is future work.

- [ ] **Health endpoint** — `GET /health` returning `{"status": "ok", "skills":
  N}`. Required for load balancers and orchestrators to know the server is up
  and has loaded skills.

- [ ] **Request logging / observability** — No visibility into which skills are
  searched, what queries produce no results, what the search latency is. Need
  at least structured logs per request (query, result count, top hit, latency).

### P2 — Quality of life

- [ ] **Hot-reload** — Adding or updating skills requires a server restart. A
  file-watcher or a `POST /reload` admin endpoint would let the team update
  skills without downtime.

- [ ] **Automate MCP integration tests** — `test_server.py` is permanently
  skipped because it requires a manually running server. Refactor to spin up
  the server in-process using an ASGI test client.

- [ ] **Update DESIGN.md** — Still references LLM-as-judge (removed), missing
  `test_trigger.py`, missing `_any` expected_skill convention.

- [ ] **Search quality: `slides` vs `pptx` and `webapp-testing` vs
  `develop-web-game`** — These pairs of skills score equally or near-equally
  for overlapping queries. The agent only reads the top result. Both skills are
  relevant — the agent should receive both and decide. Consider either: (1)
  raising the default `limit` from 5 to a higher value, or (2) having the
  agent always search with limit=5 and evaluate all returned options before
  reading. The current behavior is deterministic (alphabetical tiebreaker) but
  may not return the most relevant skill first.

## Architecture Summary

```
Agent (any MCP client)
  └─ search_skills(query) → ranked list of skill names + descriptions
  └─ read_skill(name)     → full skill instructions + resource list
  └─ read_skill(name, file) → specific supporting file

Server: FastMCP over Streamable HTTP (port 8080)
  └─ SkillCatalog: in-memory, loaded at startup from skills directory
  └─ search(): keyword matching on name + description + tags
  └─ Skills: any directory containing SKILL.md (agentskills spec)
```

## Key Design Decisions

**We test delivery, not model behavior.** Assertions check whether the agent
called `search_skills` and read the correct skill. What the model does with the
skill content after reading it is the model's problem, not the MCP server's.

**Three test layers, each independent:**
- Search quality (no LLM) — does the search engine rank the right skill first?
- Trigger (LLM, deterministic assertions) — does the agent decide to search?
- E2E delivery (LLM, deterministic assertions) — full loop, correct skill read?

**`expected_skill` values in test_cases.json:**
- `"skill-name"` — agent must search and read this exact skill
- `"_any"` — agent must search, no specific skill required (e.g. `code-review`,
  `board-document` where pdf or docx are both valid)
- `null` — agent must NOT call any tools (trivial tasks)

## What's Good

The critical path works end-to-end. The trigger behavior — the hardest problem
— is 100%. The search quality is solid for unambiguous queries. The server code
is simple, stateless, and easy to deploy.
