# Yukino Core 0.5.0 architecture

Python 3.12+, asyncio, aiohttp and Pillow. The core owns message policy, queues,
configuration, persistence and the administration API. NapCat/QQ, model servers,
Ollama inference, the optional webpage bridge and optional download sidecars are
external components. The supported platform transport is OneBot v11 forward
WebSocket. MaiBot/AstrBot plugin APIs are not implemented.

## Runtime construction

`__main__.py` parses process arguments and constructs `Config`; `app.make_runtime`
opens SQLite and local resources, creates `ProviderManager`, loads plugins and
creates `OneBotClient`. Startup starts the health loop and optionally the platform
connection. Shutdown cancels queued workers, stops transport and plugin workers,
then closes model HTTP sessions and SQLite.

`config.py` is the schema authority for mutable dialogue settings. `DEFAULTS`,
`LIMITS` and `LABELS` produce the typed schema at `/api/settings/schema`. Runtime
code reads those settings for new requests. Process bind address, port and data
paths remain deployment arguments and are not falsely marked as hot settings.

## Configuration boundaries

| File | Contract |
| --- | --- |
| `config.json` | General settings, plugin flags/settings, per-session model routes |
| `connections.json` | Private OneBot URL/token, bot account, owners, receive and reconnect parameters |
| `providers.json` | Private versioned model profiles, credentials and default model ID |
| `secrets.json` | Administrator token and compatibility secrets loaded by the launcher |
| `core.db` | Events, messages, sessions, memories and outbox records |

Credentials are write-only at the public API boundary. Blank submitted API keys
preserve the existing key; explicit clear operations remove it. Model profile
writes use a private temporary file and replacement. New source-release instances
are initialized with random secrets, an empty provider registry, no QQ account,
an empty resource catalogue and receiving disabled.

Existing environment-based deployments can migrate the previous webpage bridge
into a model profile. Saved instance settings take precedence where documented;
the source release does not include the previous deployment's accounts or persona.

## Message, transport and persistence

`transport.normalize_event(raw, self_id='') -> dict | None` normalizes incoming
messages. Event fields include `event_id`, `session_id`, `message_id`, `user_id`,
`group_id`, `self_id`, `message_type`, `text`, `display_name`, `segments`,
`mentioned`, `reply_to` and `time`; a bot marker is preserved when supplied by the
platform. IDs are strings. Sessions use `p:<user_id>` or `g:<group_id>`.

`OneBotClient` separates the WebSocket reader from event callbacks, provides
bounded event dispatch, configurable action deadlines/reconnect intervals and
redacted diagnostics. Outbound messages use OneBot segment arrays. `OneBotError`
marks uncertain action results; an uncertain send is not automatically retried.

`core.Kernel` applies access policy and triggers, handles local commands before
model work, merges recent messages and uses a bounded per-session queue plus a
global generation lock. Per-session model selection overrides the default.
Resets increment an epoch so late model results cannot reply into a reset context.

`store.Store` serializes SQLite work off the event loop. Ingest deduplicates and
persists incoming messages; context reset preserves historical records and sets a
new boundary. Memories are scoped to a session. Outbox prepare/claim/mark records
delivery attempts; uncertain delivery is retained for inspection instead of
replayed. No completed delivery is inferred solely from a timeout.

## Model manager

`models.ProviderManager` supplies `start`, `close`, `inspect`, `snapshot`,
`complete`, `test`, model discovery, profile CRUD and default selection. Its six
adapters are `chatgpt_web`, `openai_compatible`, `openai_responses`, `anthropic`,
`gemini` and `ollama`.

Requests use explicit profile URLs and protocol-native authentication/body
formats. Redirects are rejected, response size is bounded, reserved input/auth
fields cannot be overridden by `extra_body`, and errors omit upstream bodies and
credentials. A generation is attempted once; profile editing is rejected while
generation holds the model lock. Empty or disabled profiles fail without network
calls. Health/list discovery is separate from a billed generation test.

`provider.build_prompt` constructs a bounded JSON conversation envelope containing
persona/rules, selected history, session memories, references and the current
message. The webpage compatibility marker is configurable. Changing that marker
does not edit any external ChatGPT account's memory or custom instructions.
Incoming QQ image placeholders are not a vision pipeline.

## Plugins and resources

`plugins.PluginManager` owns built-ins, trusted administrator manifests, toggles,
typed settings and per-worker locks. External Python plugins implement
`handle(text, context) -> str | None`; context includes session/user IDs and a
settings snapshot. The worker host uses JSON lines over standard input/output,
isolates plugin print output and enforces a deadline. This is fault isolation,
not an operating-system sandbox. Exact configured URL hosts/path prefixes may
trigger an enabled plugin without an @ mention, subject to conversation policy.

`resources.ResourceLibrary` performs bounded local text/image retrieval and
supports user overlays plus validated image uploads. Image sends resolve only
verified local catalogue items. An explicit optional legacy path can expose the
old image registry read-only; it is not a general database migration or permission
to redistribute old resources. The source release starts with no media pack.

The development `integrations` directory contains separate JM/video sidecars.
They are not part of the default core release and do not define the public core
plugin ABI. Their transport, endpoints and runtime need independent deployment.

## Administration API and console

The static console has no external CDN dependency. All administration `/api/*`
routes require a login session except `/api/login`. Login returns a CSRF token
and an HttpOnly SameSite cookie; mutating requests require `X-CSRF-Token` and
matching Origin when present. Public health and static UI do not expose secrets.
Ordinary responses use `{ok:true,data:...}`; failures use
`{ok:false,error:{code,message}}`. Login is the documented exception returning
the CSRF token at the top level.

`settings_api.py` provides schema, QQ connection changes/reconnect, model profile
operations and tests, per-session routes, validation, export/import, plugin
settings and system diagnostics. Connection changes wait for an idle core before
replacing the transport. The API also covers history, memory CRUD, resource CRUD,
image upload, events, plugin toggles and isolated model tests. Isolated tests do
not send QQ messages but do call the chosen real model service.

Export excludes administrator, OneBot and provider credentials and message
history. General config, role text, nonsecret account/model metadata and plugin
parameters may still be personal, so review exported content before sharing.
Import intentionally restores only validated general settings and settings/flags
for installed plugins. It does not restore credentials, connections, models or
session routes; the response reports that boundary explicitly. It is not an
instance backup format.

## Release and validation

The release builder accepts only core source, text UI, tests, documentation,
release tooling and neutral examples. It replaces the private image logo only in
the archive, generates an empty resource manifest and emits file hashes plus a
source ZIP hash. Runtime data, accounts, credentials, old databases, media and
private deployment instructions stay outside the package.

Local tests verify adapters over real loopback HTTP, API authentication/CSRF,
redaction, persistence, routing, policy and bounded data handling. Installing an
unpacked source ZIP and starting an empty instance validates packaging and console
availability. These checks do not prove paid service access, production platform
delivery, all third-party provenance, or licensing of imported media. Those gaps
remain explicit in release documentation.
