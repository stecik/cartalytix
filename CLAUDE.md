# Core Rules

- Be brief; save tokens.
- Always use $caveman ultra or /caveman ultra mode
- never use * (wildcard) for imports
- use skills olc-skills and olc-powers where appropriate
- always use type annotations and check with ruff and ty
- always make sure we avoid N+1 issue or missing select/prefetch related
- do not run commands in bash, always use powershell or cmd
- if anything is ambiguous or unclear, always ask for clarification. Include your recomendation NEVER assume
- the web must be fast - write optimal queries and speed optimal code
- if you find any unoptimal query or N+1 or slowdown in code even if unrelated to current task, tell me
- for missing annotations use `uvx pyrefly infer path/to/file` to infer basic annotations. It is not perfect but gives a nice starting point. After that manualy fill the rest
- in python prefer dict.get(value, fallback) over dict[""]
- fix the cause, not the symptom if possible. Or at least tell me
- follow ruff best practices. Always check newly written code with ruff check and fix issues.
- write only important stuff to README so it is not bloated (project structure, deployment, project guidelines etc)

## Workflow

1. Understand request.
2. Find relevant files using `fd` / `rg`.
3. Read needed context only.
4. Make requested change only.
5. Verify it works.
6. Reply briefly with what changed.

## Tooling

- Use `rg`, never grep.
- Use `fd`, never find.
- Use `uv`, never pip.
- Use subagents when useful - fit proper model to task to save tokens => easy task = cheap simple model, difficult task = more expensive capable model.
- Powershell and cmd have basic linux tooling available - you can use it

## Python / General Code

- Respect existing style/formatting.
- Do not change formatting unless needed.
- Use enums where sensible in FastAPI.
- All UI text added by you must:
  - be English
  - use translation wrapper

## SQL / Database

- Use full table names only.
- Never use SQL aliases.
- Always check select/prefetch related is used where apropriate
- Avoid N + 1 issue

## Third-Party Code

- Do not modify third-party libraries unless explicitly asked.
- Only mention issue if bug is there.

## Forbidden Unless Asked

- No `.md` files.
- No scripts (`.ps1`, `.sh`, utility/deployment python).
- No SQL scripts unless primary task.
- No deployment docs.
- No TODO comments.
- No extra features / abstractions / future-proofing.

## Ignore These Paths

- `_docs/`
- `_src/`
- `.venv/`

## Response Style

- No summaries/changelogs.
- No long explanations.
- Final response = 1–2 short sentences max.

## File Links (Codex agent only)

Use exact format:
[api/routers/entries.py (line 50)](/c:/Users/marst/Home/olc/projects/cjf/cjf/api/routers/entries.py#L50)

## Principle

- Do only what is requested.
- No extras.
- Less is more.
