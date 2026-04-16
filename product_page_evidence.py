import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional
from urllib import parse, request
from urllib.error import HTTPError, URLError


DEFAULT_TIMEOUT_SECONDS = 2
DEFAULT_MAX_PRODUCTS = 3
MAX_RESPONSE_BYTES = 150_000
PAGE_SNIPPET_LIMIT = 3
PAGE_SNIPPET_MIN_LENGTH = 20
PRODUCT_PAGE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)

_WHITESPACE_RE = re.compile(r"\s+")


class _PageEvidenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.og_title = ""
        self.og_description = ""
        self._capture_title = False
        self._suppress_depth = 0
        self._json_ld_depth = 0
        self._json_ld_chunks: List[str] = []
        self._text_chunks: List[str] = []
        self.structured_name = ""
        self.structured_description = ""
        self.structured_brand = ""

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "title":
            self._capture_title = True
            return
        if tag in {"script", "style", "noscript"}:
            script_type = (attrs_dict.get("type") or "").lower()
            if tag == "script" and script_type == "application/ld+json":
                self._json_ld_depth += 1
                self._json_ld_chunks.append("")
            else:
                self._suppress_depth += 1
            return
        if tag == "meta":
            name = (attrs_dict.get("name") or "").lower()
            prop = (attrs_dict.get("property") or "").lower()
            content = _clean_text(attrs_dict.get("content") or "")
            if not content:
                return
            if name == "description" and not self.meta_description:
                self.meta_description = content
            if prop == "og:title" and not self.og_title:
                self.og_title = content
            if prop == "og:description" and not self.og_description:
                self.og_description = content

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._capture_title = False
            return
        if tag in {"style", "noscript"} and self._suppress_depth:
            self._suppress_depth -= 1
            return
        if tag == "script":
            if self._json_ld_depth:
                self._json_ld_depth -= 1
                self._consume_json_ld(self._json_ld_chunks.pop())
            elif self._suppress_depth:
                self._suppress_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            cleaned = _clean_text(data)
            if cleaned:
                self.title = f"{self.title} {cleaned}".strip()
            return
        if self._json_ld_depth:
            self._json_ld_chunks[-1] += data
            return
        if self._suppress_depth:
            return
        cleaned = _clean_text(data)
        if cleaned:
            self._text_chunks.append(cleaned)

    def text_snippets(self) -> List[str]:
        snippets: List[str] = []
        seen = set()
        for chunk in self._text_chunks:
            if len(chunk) < PAGE_SNIPPET_MIN_LENGTH:
                continue
            normalized = chunk.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            snippets.append(chunk)
            if len(snippets) >= PAGE_SNIPPET_LIMIT:
                break
        return snippets

    def _consume_json_ld(self, raw_json: str) -> None:
        cleaned = raw_json.strip()
        if not cleaned:
            return
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            return
        for candidate in _flatten_json_ld(payload):
            if not self.structured_name:
                self.structured_name = _clean_text(candidate.get("name") or "")
            if not self.structured_description:
                self.structured_description = _clean_text(candidate.get("description") or "")
            if not self.structured_brand:
                brand = candidate.get("brand")
                if isinstance(brand, dict):
                    self.structured_brand = _clean_text(brand.get("name") or "")
                elif isinstance(brand, str):
                    self.structured_brand = _clean_text(brand)


def _flatten_json_ld(payload: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("@graph"), list):
            for item in payload["@graph"]:
                if isinstance(item, dict):
                    yield item
        yield payload
    elif isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item


def _clean_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip()


def build_product_page_url(product: Dict[str, Any]) -> Optional[str]:
    metadata = product.get("metadata") if isinstance(product.get("metadata"), dict) else product
    page_key = metadata.get("pageKey") or metadata.get("productId") or metadata.get("product_id")
    item_id = metadata.get("itemId") or metadata.get("item_id")
    vendor_item_id = metadata.get("vendorItemId") or metadata.get("vendor_item_id")
    if page_key and item_id and vendor_item_id:
        return (
            f"https://www.coupang.com/vp/products/{page_key}"
            f"?itemId={item_id}&vendorItemId={vendor_item_id}"
        )

    raw_url = (
        product.get("landing_page_url")
        or product.get("url")
        or product.get("productUrl")
        or product.get("deeplink")
    )
    if not raw_url:
        return None
    parsed = parse.urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    if host in {"www.coupang.com", "m.coupang.com", "coupang.com"}:
        return raw_url
    return None


def fetch_product_page_evidence(
    product: Dict[str, Any],
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    opener: Any = None,
) -> Optional[Dict[str, Any]]:
    page_url = build_product_page_url(product)
    if not page_url:
        return None

    http_request = request.Request(
        page_url,
        headers={
            "User-Agent": PRODUCT_PAGE_USER_AGENT,
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
        method="GET",
    )
    urlopen = opener or request.urlopen
    try:
        with urlopen(http_request, timeout=timeout_seconds) as response:
            raw_bytes = response.read(MAX_RESPONSE_BYTES)
            final_url = response.geturl()
            content_type = response.headers.get_content_charset() if hasattr(response.headers, "get_content_charset") else None
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None

    parsed_final = parse.urlparse(final_url)
    if (parsed_final.hostname or "").lower() not in {"www.coupang.com", "m.coupang.com", "coupang.com"}:
        return None

    html_text = raw_bytes.decode(content_type or "utf-8", errors="ignore")
    parser = _PageEvidenceParser()
    parser.feed(html_text)
    parser.close()

    title = parser.og_title or parser.title
    description = parser.og_description or parser.meta_description or parser.structured_description
    snippets = parser.text_snippets()
    facts = []
    if title:
        facts.append(f"Landing page title: {title}")
    if parser.structured_brand:
        facts.append(f"Landing page brand: {parser.structured_brand}")
    if description:
        facts.append("Landing page description captured")
    if snippets:
        facts.append(f"Landing page snippet count: {len(snippets)}")

    if not any([title, description, snippets, facts]):
        return None
    return {
        "page_url": final_url,
        "page_title": title,
        "page_description": description,
        "page_snippets": snippets,
        "page_facts": facts,
    }


def enrich_products_with_page_evidence(
    products: Iterable[Dict[str, Any]],
    *,
    max_products: int = DEFAULT_MAX_PRODUCTS,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    opener: Any = None,
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for index, product in enumerate(products):
        enriched_product = dict(product)
        if index < max_products:
            page_evidence = fetch_product_page_evidence(
                enriched_product,
                timeout_seconds=timeout_seconds,
                opener=opener,
            )
            if page_evidence:
                existing_description = _clean_text(str(enriched_product.get("description") or ""))
                page_description = _clean_text(page_evidence.get("page_description") or "")
                merged_description_parts = [part for part in [existing_description, page_description] if part]
                if merged_description_parts:
                    deduped_parts = []
                    seen = set()
                    for part in merged_description_parts:
                        lowered = part.lower()
                        if lowered in seen:
                            continue
                        seen.add(lowered)
                        deduped_parts.append(part)
                    enriched_product["description"] = " ".join(deduped_parts)
                enriched_product.update(page_evidence)
        enriched.append(enriched_product)
    return enriched
