# Dirac

Dirac is a local, Discord-centered agent runtime written in Python 3.14+. It connects Discord events, deterministic commands, scoped permissions, provider-backed inference, tool execution, periodic tasks, and quiet-phase memory consolidation behind narrow module contracts.

This repository is a rewrite target. The legacy implementation is a behavior specimen, not an architecture specimen. Preserve the working behavior, not the file shape.

## Design goals

- Keep Discord as the center of the product.
- Keep commands, permissions, scopes, tool filtering, and Discord identity grounding deterministic before any model call.
- Keep provider calls simple: Python, `httpx`, explicit request payloads, no provider SDK lock-in.
- Keep the WebUI replaceable. The current UI may be Svelte/Vite, but it must talk only to the HTTP API.
- Keep memory access behind one memory contract. The model, tasks, API, and UI must not know how memory is stored.
- Keep quiet-phase consolidation separate from ordinary periodic tasks.
- Keep every background coroutine owned by an `asyncio.TaskGroup` from the runtime composition root.
- Keep modules small enough that an agent can edit one owner without learning the whole application.

## What Dirac does

1. Receives Discord messages.
2. Records visible events for later context and consolidation.
3. Parses deterministic commands before model logic.
4. Applies permission, scope, and capability gates.
5. Builds model context from scoped instructions, recent visible conversation, durable memory, task state, and trusted runtime notes.
6. Routes the request to a provider client.
7. Executes only enabled tools for the current scope.
8. Sends the final response back to Discord or API clients.
9. Runs a loose periodic task loop.
10. Runs quiet-phase consolidation to turn short-term visible events into durable memory.

## Runtime surfaces

- `discord`: gateway adapter, event normalization, message delivery, typing indicators, reactions, and Discord-specific identity helpers.
- `api`: authenticated HTTP API for the WebUI, operators, and external assistant clients.
- `task`: periodic task definitions, due-task selection, task attempts, and task result delivery.
- `consolidation`: quiet-phase memory assimilation. This is the successor to the old REM behavior.
- `doctor`: maintenance and repair surface. It is allowed to use lower-level repair ports that the normal API must not expose.
- `web`: replaceable frontend. It imports no Python internals and depends only on the API contract.

## Target package shape

```text
dirac/
  runtime/          composition root, TaskGroup ownership, lifecycle
  config/           static config, secrets, provider URLs, operator settings
  log/              structured logging, console controls, redaction, sinks
  provider/         BaseProviderClient, concrete clients, model routing
  permission/       scope and capability decisions before model calls
  discord/          Discord adapter and delivery
  command/          deterministic command parser and handlers
  context/          model context assembly and filters
  memory/           BaseMemory, durable memory, recent event stream
  tool/             tool registry, definitions, scoped execution
  task/             periodic tasks and task attempts
  consolidation/    quiet-phase memory consolidation
  doctor/           diagnosis and repair ports
  api/              HTTP API routes and DTOs
  types.py          shared domain values only
web/                Svelte/Vite or replacement UI, API client only
```

The structure may evolve, but the dependency direction must not: adapters call services, services call ports, ports hide implementation details.

## Core contracts

Use interfaces or abstract base classes at module boundaries. Domain modules receive dependencies through constructors. They do not import concrete downstream implementations.

```python
class BaseProviderClient:
    async def chat(self, request: ProviderRequest) -> ProviderResponse: ...

class BaseMemory:
    async def search(self, query: MemoryQuery) -> list[MemoryRecord]: ...
    async def add(self, record: MemoryWrite) -> MemoryRecord: ...
    async def update(self, memory_id: str, patch: MemoryPatch) -> MemoryRecord: ...
    async def delete(self, memory_id: str) -> None: ...

class BaseConfig:
    def provider_settings(self) -> ProviderSettings: ...
    def discord_settings(self) -> DiscordSettings: ...
    def api_settings(self) -> ApiSettings: ...
    def logging_settings(self) -> LoggingSettings: ...

class BaseLog:
    async def event(self, event: LogEvent) -> None: ...
```

The exact DTOs belong in code, but the rule is stable: cross-module calls pass explicit values, not raw internal state.

## Provider layer

Provider clients own provider-specific URLs, headers, payload translation, response parsing, unsupported parameter warnings, redaction, latency, and usage extraction.

The rest of Dirac calls a provider through `BaseProviderClient.chat(request)`. No command handler, task runner, context assembler, WebUI route, or memory service may build provider HTTP requests directly.

Provider routing resolves:

- selected provider
- model tag
- provider parameters
- tool availability
- runtime source
- provenance fields

Fake provider clients and fake HTTP transports must be first-class so provider behavior can be tested without live services.

## Permissions, scopes, and tools

The permission path is before the model path.

Unauthorized commands are deterministic failures and never become model context. Authorized commands are handled by code and excluded from reusable conversation context. Blocked users are excluded from model-facing context.

Scopes and capability filtering are preserved as product behavior. A scoped tool is visible only when the current scope allows it. The Discord-facing behavior is the reference behavior here: keep it strict, explicit, and boring.

## Context assembly

The context module owns all model-facing context construction. It may receive domain objects from permission, memory, task, and Discord services, but it must not query their internals.

Context should include:

- current scoped instructions
- trusted runtime time and request metadata
- Discord identity grounding when relevant
- durable memory selected through `BaseMemory`
- recent visible conversation after filters
- task state summaries when relevant
- tool and skill capability notes when relevant

Exact `dirac` fenced blocks are runtime output and must be removed from model-facing historical context and consolidation slices. Other fenced blocks are ordinary content unless a narrower filter says otherwise.

## Memory and consolidation

Memory is a product boundary, not a storage detail.

Dirac has two memory streams:

- Recent visible events: short-term observations from Discord, API clients, command outputs, and task results.
- Durable memory: curated facts, preferences, decisions, identities, and unresolved threads that should survive context loss.

The model may access durable memory only through memory tools or API methods:

- search
- add
- update
- delete

Quiet-phase consolidation reads recent visible events and the previous consolidation audit, then uses the same memory contract to preserve, merge, supersede, or remove durable memories. It is not an ordinary user task and must live in the `consolidation` module, even if it shares the provider and tool loop machinery.

Consolidation must leave an audit trail that distinguishes model-authored output from runtime-authored warnings. A failed or cut-short consolidation must never be recorded as a successful completion.

## Periodic tasks

Dirac does not need a heavyweight scheduler.

The task service owns a loose local ticker. On each tick it finds enabled periodic tasks whose next run is due, selects one due task at random, advances that task's next due time before execution, and launches exactly that attempt through the runtime TaskGroup. Task intervals are configured in minutes. If many tasks are due, later ticks pick from the remaining due tasks.

Task state describes intent and last known result. A task attempt describes what actually happened. Runtime failures are recorded as failed attempts and do not poison the task forever.

Quiet-phase consolidation is not modeled as a normal task, even if it uses similar plumbing internally.

## API and WebUI

The HTTP API is the contract. The WebUI is just one client.

The API exposes operator-safe methods for:

- runtime status
- providers and model routing
- scopes and capability state
- commands
- memory
- tasks and task attempts
- logs
- config summaries
- doctor handoff where appropriate

Secrets are never returned unredacted. Provider keys, Discord tokens, API auth tokens, and bearer headers must never appear in logs, API responses, task output, or model context.

The WebUI may be Svelte/Vite now and something else later. Replacing it must not require touching provider, memory, task, Discord, or doctor modules.

## Doctor

Doctor is not a high-level assistant. Doctor is a maintenance surface for diagnosis, repair, exports, consistency checks, and recovery actions.

Doctor may use repair ports that normal API clients cannot use. Doctor must redact secrets, back up before destructive changes, and report exactly what changed. High-level assistant clients use the authenticated API and do not receive plumbing access.

## Logging

Logging is a module with replaceable sinks. Valid sinks include no-op, console, file, event stream, and persistence-backed sinks. The rest of the code emits structured events and does not care where they go.

Preserve the useful console behavior: adjustable verbosity and filters for system, user, assistant, tool content, tool lists, and tool calls.

## Configuration

Static configuration belongs in files and environment variables. Config owns secrets, provider URLs, Discord runtime options, API bind settings, and logging defaults.

Domain modules ask Config for typed settings. They do not read raw config files or poke through nested dictionaries.

## Implementation policy

- Python 3.14+ is the baseline.
- Use `asyncio.TaskGroup` for runtime-owned background work.
- Use `httpx` for provider HTTP calls unless a concrete provider client deliberately encapsulates another dependency.
- Do not add provider SDKs to the core path.
- New dependencies are allowed when they remove complexity and stay behind a module boundary.
- Tests use fake providers, fake memory, fake logs, and fake clocks where practical.

## Contributor quality gate

`pytest` is configured as a fail-fast project gate. Before test collection starts, it now runs:

- `ruff check .`
- `ruff format --check .`
- `mypy --config-file pyproject.toml`
- architecture checks for duplicate module bodies and forbidden cross-owner imports of concrete provider/memory implementations

If any gate fails, pytest exits immediately and no tests are collected. This keeps encapsulation and typing violations from being discovered only after behavioral tests run.

## Migration rule

Keep the legacy code available only as a behavior reference until the replacement reaches parity. Do not copy its architecture. Do not add new features to the old large file. Extract behavior into modules by contract, then delete the old path once the replacement owns that behavior.
