"""web_fetch: fetch a known http(s) URL with timeout, size and redirect limits."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from .results import ToolError, run_tool


class WebFetchArgs(BaseModel):
    url: str = Field(description="Absolute http(s) URL of a text or HTML page")


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template", "svg"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    p.feed(html)
    text = re.sub(r"[ \t]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return p.title.strip(), text


class WebTools:
    def __init__(self, timeout_s: float = 10.0, max_bytes: int = 500_000,
                 max_redirects: int = 5, max_chars: int = 12000) -> None:
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.max_redirects = max_redirects
        self.max_chars = max_chars

    def web_fetch(self, url: str) -> dict:
        parsed = urlparse(url or "")
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ToolError("invalid_url", f"Only absolute http(s) URLs are supported: {url!r}")
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout_s), follow_redirects=True,
                              max_redirects=self.max_redirects,
                              headers={"User-Agent": "itmo-lab-file-organizer/1.0"}) as client:
                with client.stream("GET", url) as resp:
                    chunks, size, truncated = [], 0, False
                    for chunk in resp.iter_bytes():
                        if size + len(chunk) > self.max_bytes:
                            chunks.append(chunk[: self.max_bytes - size])
                            size = self.max_bytes
                            truncated = True
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    raw = b"".join(chunks)
                    status = resp.status_code
                    final_url = str(resp.url)
                    ctype = resp.headers.get("content-type", "")
                    encoding = resp.encoding or "utf-8"
                    redirects = [str(r.url) for r in resp.history]
        except httpx.TimeoutException:
            raise ToolError("timeout", f"Request timed out after {self.timeout_s}s", url=url)
        except httpx.TooManyRedirects:
            raise ToolError("too_many_redirects", f"More than {self.max_redirects} redirects", url=url)
        except httpx.HTTPError as e:
            raise ToolError("network_error", f"{type(e).__name__}: {e}", url=url)

        if status >= 400:
            raise ToolError("http_error", f"HTTP {status}", url=url, final_url=final_url, status=status)
        if not (ctype.startswith("text/") or "html" in ctype or "json" in ctype or "xml" in ctype or not ctype):
            raise ToolError("unsupported_content_type", f"Not a text/HTML response: {ctype}",
                            url=url, final_url=final_url, status=status)
        body = raw.decode(encoding, errors="replace")
        title = ""
        if "html" in ctype:
            title, body = html_to_text(body)
        text, clipped = body[: self.max_chars], len(body) > self.max_chars
        return {"url": url, "final_url": final_url, "status": status, "content_type": ctype,
                "redirects": redirects, "title": title, "text": text,
                "truncated": truncated or clipped, "bytes_read": size}

    def as_tools(self) -> list[BaseTool]:
        return [StructuredTool.from_function(
            func=lambda url: run_tool("web_fetch", self.web_fetch, url),
            name="web_fetch", args_schema=WebFetchArgs,
            description="Fetch a known text/HTML URL (timeout, size and redirect limits). Returns "
                        "original/final URL, HTTP status, extracted text and truncation flag. "
                        "Optional: not needed for organizing local files.")]
