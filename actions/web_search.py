#web_search.py
import re
from datetime import date

from agent.llm import complete

# Queries about current events go to news search first, limited to the past week.
_TIME_SENSITIVE = re.compile(
    r"\b(news|noticias?|today|hoy|latest|[uú]ltim[oa]s?|recent|recientes?|now|ahora|current|actual|"
    r"yesterday|ayer|this week|esta semana|price|precio|cotizaci[oó]n|score|marcador|resultados?)\b",
    re.I,
)


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    fresh = bool(_TIME_SENSITIVE.search(query))
    kw = {"max_results": max_results, "region": "es-es", "timelimit": "w" if fresh else None}
    with DDGS() as ddgs:
        first, second = (ddgs.news, ddgs.text) if fresh else (ddgs.text, ddgs.news)
        # Either search sometimes comes back empty at random; the other one is the fallback.
        try:
            raw = first(query, **kw)
        except Exception:
            raw = []
        if not raw:
            raw = second(query, **kw)

    results = []
    for r in raw:
        url, when = r.get("href") or r.get("url"), r.get("date") or ""
        if not url:  # unciteable
            continue
        results.append({
            "title":   r.get("title", ""),
            "snippet": r.get("body", ""),
            "url":     url,
            # ddgs dates are ISO strings; keep just the day. Never invent one.
            "date":    when[:10] if re.match(r"\d{4}-\d{2}-\d{2}", when) else when,
        })
    return results


def _format(query: str, results: list[dict]) -> str:
    if not results:
        return f"Could not find any web results for: {query}"

    lines = [f"Web results for: {query} (retrieved {date.today().isoformat()}). Cite them by number.\n"]
    for i, r in enumerate(results, 1):
        when = f"date: {r['date']}" if r.get("date") else "date unknown"
        lines.append(f"{i}. {r['title'] or 'Untitled'} — {r['snippet']} (source: {r['url']}, {when})")
    return "\n".join(lines)


def _compare(items: list[str], aspect: str) -> str:
    results = []
    for item in items:
        try:
            results += _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception as e:
            print(f"[WebSearch] ⚠️ DDG failed for {item!r}: {e}")

    sources = _format(f"{', '.join(items)} — {aspect}", results)
    if not results:
        return sources
    try:
        return complete(
            f"Compare {', '.join(items)} in terms of {aspect} using ONLY these web results:\n\n{sources}",
            system=(
                "Give specific facts and figures. Cite every fact with its source number, e.g. [2]. "
                "Keep each source's date ('date unknown' when it has none). "
                "End with the numbered sources you used: title, URL, date."
            ),
            fast=True,
        )
    except Exception as e:
        print(f"[WebSearch] ⚠️ LLM compare failed ({e}) — returning raw sources")
        return sources


def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query, sir."

    if items and mode != "compare":
        mode = "compare"

    if player:
        player.write_log(f"[Search] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 Query: {query!r}  Mode: {mode}")

    if mode == "compare":
        return _compare(items or [query], aspect)

    # DuckDuckGo returns real pages with URLs and dates; the live model answers from them.
    try:
        results = _ddg_search(query)
    except Exception as e:
        print(f"[WebSearch] ❌ DDG failed: {e}")
        return f"Could not search the web for: {query} ({e})"
    print(f"[WebSearch] ✅ DDG: {len(results)} result(s).")
    return _format(query, results)
