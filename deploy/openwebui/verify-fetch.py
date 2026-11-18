"""Read-only probe inside the installed OpenWebUI image; no model requests."""

import asyncio
import contextlib
import io
import json
import socket
import sys


def probe():
    # Register defaults before reading persisted Config values.
    from open_webui.config import DEFAULT_CONFIG, ENABLE_LOCAL_WEB_FETCH
    from open_webui.models.config import Config
    from open_webui.retrieval.web.utils import get_web_loader, validate_url

    fields = {
        "engine", "timeout", "ssl_verification", "concurrent_requests", "trust_env",
        "playwright_timeout", "playwright_ws_url", "firecrawl_api_key", "firecrawl_api_url",
        "tavily_extract_depth", "exa_api_key", "external_web_loader_url",
        "external_web_loader_api_key", "microsoft_web_iq_api_base_url",
        "microsoft_web_iq_api_key", "microsoft_web_iq_language",
    }
    keys = [k for k in DEFAULT_CONFIG if k.startswith(("web.loader.", "web.search."))
            and k.rsplit(".", 1)[-1] in fields]

    async def config():
        return await Config.get_many(*keys)

    cfg = asyncio.run(config())  # Secret values remain in memory, never in report.
    url = "https://docs.openwebui.com/features/chat-conversations/web-search/providers/searxng/"
    assert not ENABLE_LOCAL_WEB_FETCH, "Local web fetch unexpectedly enabled"
    assert validate_url(url)
    documents = get_web_loader(url, cfg).load()
    count = sum(len(document.page_content) for document in documents)
    assert documents and count > 100, "Official document empty"
    blocked = []
    for private in ["http://127.0.0.1/", "http://169.254.169.254/", "http://10.0.0.1/",
                    "http://[::1]/", "http://[fc00::23]/"]:
        try:
            accepted = validate_url(private)
        except ValueError:
            accepted = False
        assert not accepted, "Private URL unexpectedly accepted"
        blocked.append(private)
    return {
        "official_url": url,
        "addresses": sorted({x[4][0] for x in socket.getaddrinfo("docs.openwebui.com", None)}),
        "document_count": len(documents), "content_characters": count,
        "private_urls_blocked": blocked, "local_web_fetch_enabled": False,
        "inference_requested": False, "configuration_changed": False,
    }


if __name__ == "__main__":
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = probe()
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__}))
        sys.exit(1)
    print(json.dumps({"ok": True, **result}))
