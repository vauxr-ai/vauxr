"""Bounded static interface source; no shell, runner, filesystem paths or network execution."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import nh3
from aiohttp import web

from vauxr.config import get_config
from vauxr.config_files import read_json, unique_object, update_json
from vauxr.web.owner import session_principal

DEFAULT = {"title": "My interface", "html": "<section><h2>My workspace</h2><p>Edit this static interface in Settings.</p></section>", "css": "body { font-family: system-ui; padding: 1rem; color: #272239; }"}
TAGS = {"div", "section", "article", "header", "footer", "main", "nav", "p", "h1", "h2", "h3", "h4", "h5", "h6", "span", "strong", "em", "b", "i", "u", "small", "ul", "ol", "li", "dl", "dt", "dd", "pre", "code", "blockquote", "hr", "br", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "figure", "figcaption"}
CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'"


def validate(body: object) -> dict[str, str]:
    if not isinstance(body, dict) or set(body) != {"title", "html", "css"}:
        raise ValueError("Expected title, HTML and CSS only")
    if any(not isinstance(body[key], str) or len(body[key]) > limit for key, limit in [("title", 120), ("html", 20000), ("css", 12000)]):
        raise ValueError("Interface source exceeded its size limit")
    if not body["title"].strip() or any(ord(c) < 32 for c in body["title"]):
        raise ValueError("Use a plain interface title")
    return {**body, "title": body["title"].strip()}


def revision(source: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()


def read() -> dict:
    path = Path(get_config().data_dir) / "interface.json"
    source = validate(read_json(path)) if path.exists() else dict(DEFAULT)
    return {**source, "revision": revision(source), "published": path.exists()}


def save(body: dict) -> dict:
    if set(body) != {"title", "html", "css", "revision"} or not isinstance(body["revision"], str):
        raise ValueError("Expected source and exact revision")
    source = validate({key: body[key] for key in DEFAULT})

    def change(current: dict) -> dict:
        if revision(validate(current) if current else DEFAULT) != body["revision"]:
            raise ValueError("Interface changed; refresh before saving")
        return source

    update_json(Path(get_config().data_dir) / "interface.json", change)
    return read()


def render(source: dict) -> str:
    source = validate(source)
    clean = nh3.clean(source["html"], tags=TAGS,
                      clean_content_tags={"script", "style", "iframe", "object", "template", "svg", "math", "form"},
                      attributes={"*": {"class", "id", "title", "role", "aria-label"}},
                      url_schemes=set(), link_rel=None)
    # CSS cannot close its style element. All CSS resource loads are blocked by CSP.
    css = source["css"].replace("<", "\\3c ")
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="' + html.escape(CSP, quote=True) + '">'
            '<title>' + html.escape(source["title"]) + '</title><style>' + css + '</style></head><body>'
            + clean + '</body></html>')


def attach_interface(
    app: web.Application,
    boundary: Callable[[Callable[[web.Request], Awaitable[web.StreamResponse]]], Callable[[web.Request], Awaitable[web.StreamResponse]]],
) -> None:
    async def handle(request: web.Request) -> web.Response:
        if session_principal(request) is None:
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            if request.query_string:
                raise ValueError()
            if request.method == "GET":
                result = read()
            else:
                if request.content_type != "application/json":
                    raise ValueError()
                async with asyncio.timeout(10):
                    raw = await request.content.read(131073)
                if len(raw) > 131072:
                    raise ValueError()
                body = json.loads(raw, object_pairs_hook=unique_object)
                result = {"document": render(body)} if request.path.endswith("/preview") else save(body)
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except (ValueError, KeyError, TypeError, TimeoutError):
            return web.json_response({"error": "Invalid source or interface revision changed; refresh before saving"}, status=409)
        except OSError:
            return web.json_response({"error": "Save unavailable; refresh before retrying"}, status=503)
    app.router.add_get("/api/interface", boundary(handle))
    app.router.add_post("/api/interface", boundary(handle))
    app.router.add_post("/api/interface/preview", boundary(handle))
