# Standalone SearXNG preparation for OpenWebUI 0.13.3 slim

These are reviewable templates only. No container or production configuration
has been created, started, or changed. No derivative OpenWebUI image is needed.
SearXNG is an independent search backend; the model server and L3 inference
service do not execute searches.

The Q024 read-only host audit found OpenWebUI 0.13.3 with slim enabled, web search
disabled, an empty search engine, no checked search-provider credentials/URL,
and no DDGS/Playwright packages. Its container could GET both Baidu and Google
with HTTP 200. That proves basic container outbound HTTP, not search-result
quality or network access from the separate browser Python sandbox.

## Reviewed configuration

`compose.yaml` joins an existing network shared with OpenWebUI. It exposes no
host port. The alias `dai-search` is resolvable inside that network. No API Key
is needed from a search provider, but SearXNG still needs a private random
instance/session secret. `.env.example` deliberately has no real secret,
image digest or guessed production network name. Compose refuses to start until
these are supplied. Select and verify a real immutable image digest; a mutable
`latest` tag is not a recorded deployment version.

`settings.yml` inherits SearXNG defaults and explicitly enables JSON search
results. The limiter is disabled only because this endpoint is private to the
selected Docker network. Do not publish this template as a public search API.
An existing upstream proxy may be required for some search engines even when
plain HTTP GET tests work; that must be tested in the future SearXNG container.

`openwebui-search.json` is a subset of canonical persistent configuration keys,
not a complete config export and not an instruction to replace the database.
After separate deployment authorization, apply these fields through OpenWebUI's
admin Web Search settings or a reviewed merge using its supported config API.
Do not overwrite unrelated settings. Persistent database settings can override
environment defaults; changing environment variables alone may not enable search.

The JSON query URL is:
`http://dai-search:8080/search?q=<query>&format=json`.
Enable search globally, use the `searxng` engine and allow normal HTTP page
loading. The supplied embedding bypass avoids requiring a local embedding model
for the older retrieval path. Native `search_web` already returns snippets
without vector retrieval; `fetch_url` retrieves page text. Slim excludes DDGS
and Playwright, so this plan uses an external SearXNG service and ordinary HTTP
loading. JavaScript-only pages can still need a separately configured external
loader; that does not require modifying model weights or the inference Gateway.

Run the existing adapter publication after enabling global search: it derives
model web-search capability, builtin category and default feature from verified
native tool calling and the global flag. Ensure the user has search permission,
and that the current chat has Web Search enabled (old chats may preserve their
previous toggle). Muse has demonstrated native calls. Swift's native tool
capability is currently unverified, so this integration must not advertise it as
verified merely to make a toggle appear. It needs its own future acceptance.

## Future acceptance (not executed in production)

1. Validate Compose interpolation with private deployment values and inspect the
   resolved image/network; do not print or commit the resolved secret.
2. Start only the separately approved search service. Confirm JSON searches
   return nonempty `results` with title, URL and content. HTTP 200 alone is not
   sufficient; engine CAPTCHA/rate limiting can produce empty results.
3. Verify the URL from inside OpenWebUI's actual network namespace.
4. Apply only the reviewed search settings, then publish model metadata through
   the adapter. No capability should be enabled for an unverified model path.
5. In a new Muse chat, enable Web Search, request dated current information, and
   inspect actual `search_web` and `fetch_url` tool returns and cited pages.
6. Separately test page retrieval and Google/Baidu HTTP status. Report any
   unsupported sites accurately; do not infer all Internet access from two URLs.

Rollback is limited to restoring the previous search fields and stopping this
new backend. It does not affect model weights, remote Workers, chat history or
the existing inference connection. Record before-images before any future apply.

Sources: [SearXNG container configuration](https://docs.searxng.org/admin/installation-docker.html),
[JSON search API](https://docs.searxng.org/dev/search_api.html),
[OpenWebUI slim](https://docs.openwebui.com/getting-started/quick-start/),
[native search gates](https://docs.openwebui.com/features/chat-conversations/web-search/agentic-search/).
