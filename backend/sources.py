import hashlib
import html
import ipaddress
import re
import socket
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, urljoin, quote
import httpx
from .config import MAX_FILE

HEADERS = {"User-Agent": "PaperWorkbench/0.1 (personal research client)"}
_arxiv_last = 0.0


def clean(s):
    return html.unescape(re.sub("<[^>]+>", " ", s or "")).strip()


def norm_doi(s):
    return re.sub(r"^https?://(?:dx\.)?doi.org/", "", s.strip(), flags=re.I).lower()


def identity(c):
    if c.get("doi"):
        return "doi:" + norm_doi(c["doi"])
    if c.get("arxiv_id"):
        return "arxiv:" + re.sub(r"v\d+$", "", c["arxiv_id"])
    if c.get("sha256"):
        return "sha256:" + c["sha256"]
    return (
        "record:"
        + hashlib.sha256(
            (
                c.get("url", "")
                + "\n"
                + c.get("title", "")
                + "\n"
                + c.get("abstract", "")
            ).encode()
        ).hexdigest()
    )


def public_addresses(url):
    p = urlparse(url)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        raise ValueError("只允许公开 HTTP/HTTPS 文献链接")
    if p.port and p.port not in {80, 443}:
        raise ValueError("文献链接端口不允许")
    ips = list(
        dict.fromkeys(
            a[4][0]
            for a in socket.getaddrinfo(
                p.hostname,
                p.port or (443 if p.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        )
    )
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("禁止访问本机或内网地址")
    return p, ips


def download(url):
    for _ in range(5):
        parsed, ips = public_addresses(url)
        target = httpx.URL(url).copy_with(host=ips[0])
        with httpx.Client(timeout=30, trust_env=False) as client:
            with client.stream(
                "GET",
                target,
                headers={**HEADERS, "Host": parsed.netloc},
                extensions={"sni_hostname": parsed.hostname},
            ) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["location"])
                    continue
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_FILE:
                        raise ValueError("文档超过 25 MB 上限")
                return bytes(data)
    raise ValueError("重定向过多")


def crossref_item(x):
    parts = (x.get("published", x.get("issued", {})).get("date-parts") or [[]])[0]
    return {
        "title": (x.get("title") or ["无标题"])[0],
        "doi": x.get("DOI", ""),
        "authors": [
            " ".join(filter(None, [a.get("given"), a.get("family")]))
            for a in x.get("author", [])
        ],
        "year": parts[0] if parts else None,
        "venue": (x.get("container-title") or ["未知来源"])[0],
        "url": x.get("URL", ""),
        "abstract": clean(x.get("abstract", "")),
        "source": "crossref",
        "pdf_url": next(
            (
                a["URL"]
                for a in x.get("link", [])
                if a.get("content-type") == "application/pdf"
            ),
            "",
        ),
    }


def crossref(query, limit, year_from=None, year_to=None):
    params = {"query.bibliographic": query, "rows": limit}
    filters = []
    if year_from:
        filters.append(f"from-pub-date:{year_from}-01-01")
    if year_to:
        filters.append(f"until-pub-date:{year_to}-12-31")
    if filters:
        params["filter"] = ",".join(filters)
    r = httpx.get(
        "https://api.crossref.org/works", params=params, headers=HEADERS, timeout=35
    )
    r.raise_for_status()
    return [crossref_item(x) for x in r.json()["message"]["items"]]


def arxiv(query, limit, **_):
    global _arxiv_last
    time.sleep(max(0, 3 - (time.monotonic() - _arxiv_last)))
    _arxiv_last = time.monotonic()
    r = httpx.get(
        "https://export.arxiv.org/api/query",
        params={"search_query": "all:" + query, "start": 0, "max_results": limit},
        headers=HEADERS,
        timeout=35,
    )
    r.raise_for_status()
    return parse_arxiv(r.text)


def parse_arxiv(text):
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    out = []
    for e in ET.fromstring(text).findall("a:entry", ns):
        get = lambda tag: e.findtext(tag, "", ns).strip()
        url = get("a:id")
        out.append(
            {
                "title": " ".join(get("a:title").split()),
                "arxiv_id": url.split("/abs/")[-1],
                "doi": get("x:doi"),
                "authors": [
                    a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)
                ],
                "year": int(get("a:published")[:4]) if get("a:published") else None,
                "venue": get("x:journal_ref") or "arXiv（预印本）",
                "url": url.replace("http:", "https:"),
                "abstract": " ".join(get("a:summary").split()),
                "source": "arxiv",
                "pdf_url": next(
                    (
                        x.attrib["href"].replace("http:", "https:")
                        for x in e.findall("a:link", ns)
                        if x.attrib.get("title") == "pdf"
                    ),
                    "",
                ),
            }
        )
    return out


def resolve(candidate):
    c = dict(candidate)
    url = c.get("url", "")
    if not c.get("doi") and urlparse(url).hostname in {"doi.org", "dx.doi.org"}:
        c["doi"] = norm_doi(url)
    if c.get("doi") and not c.get("title"):
        r = httpx.get(
            "https://api.crossref.org/works/" + quote(norm_doi(c["doi"]), safe=""),
            headers=HEADERS,
            timeout=30,
        )
        r.raise_for_status()
        c = {**crossref_item(r.json()["message"]), **{k: v for k, v in c.items() if v}}
    url = c.get("url", "")
    if not c.get("title") and "arxiv.org/abs/" in url:
        aid = url.split("/abs/")[-1].split("?")[0]
        r = httpx.get(
            "https://export.arxiv.org/api/query",
            params={"id_list": aid},
            timeout=30,
            headers=HEADERS,
        )
        r.raise_for_status()
        entries = parse_arxiv(r.text)
        if entries:
            c = {**entries[0], **{k: v for k, v in c.items() if v}}
    if not c.get("pdf_url") and url.lower().split("?")[0].endswith(".pdf"):
        c["pdf_url"] = url
    c.setdefault("title", url or c.get("doi") or "未命名论文")
    return c


def ranked(candidates, topic, year_from=None, year_to=None):
    unique = {}
    words = set(re.findall(r"[a-zA-Z]{3,}", topic.lower()))
    for rank, c0 in enumerate(candidates):
        c = dict(c0)
        year = c.get("year")
        if (year_from or year_to) and (
            not year or (year_from and year < year_from) or (year_to and year > year_to)
        ):
            continue
        key = identity(c)
        provenance = {"source": c.get("source"), "query": c.get("query")}
        if key in unique:
            old = unique[key]
            old["provenance"].append(provenance)
            for k, v in c.items():
                if not old.get(k) and v:
                    old[k] = v
            continue
        hay = (c["title"] + " " + c.get("abstract", "")).lower()
        hits = sum(w in hay for w in words)
        c.update(
            identity=key,
            score=hits + 1 / (rank + 1),
            reason=f"关键词命中 {hits} 个；来源结果序号 {rank + 1}",
            provenance=[provenance],
        )
        unique[key] = c
    return sorted(unique.values(), key=lambda c: c["score"], reverse=True)


OPENALEX = "https://api.openalex.org"
OPENALEX_MAX = 500
FRONT_MATTER = re.compile(
    r"^(message from|welcome|preface|foreword|copyright|title page|"
    r"organizing committee|program committee|author index|table of contents|"
    r"front matter|editorial|steering committee|industry track program committee|"
    r"sponsors?|keynote)",
    re.I,
)


def _openalex(path, params):
    r = httpx.get(OPENALEX + path, params=params, headers=HEADERS, timeout=45)
    r.raise_for_status()
    return r.json()


def abstract_from_inverted(index):
    """Rebuild OpenAlex's abstract_inverted_index into readable text."""
    if not isinstance(index, dict):
        return ""
    pairs = []
    for word, positions in index.items():
        for position in positions or []:
            pairs.append((position, word))
    pairs.sort(key=lambda item: item[0])
    return " ".join(word for _, word in pairs)


def _openalex_pdf(work):
    for location in [work.get("best_oa_location")] + (work.get("locations") or []):
        if location and location.get("pdf_url"):
            return location["pdf_url"]
    oa_url = (work.get("open_access") or {}).get("oa_url") or ""
    if oa_url.lower().split("?")[0].endswith(".pdf"):
        return oa_url
    return ""


def openalex_item(work):
    source = (work.get("primary_location") or {}).get("source") or {}
    doi = norm_doi(work.get("doi") or "")
    landing = (work.get("primary_location") or {}).get("landing_page_url") or ""
    return {
        "title": work.get("title") or "无标题",
        "doi": doi,
        "authors": [
            (a.get("author") or {}).get("display_name", "")
            for a in work.get("authorships") or []
            if (a.get("author") or {}).get("display_name")
        ],
        "year": work.get("publication_year"),
        "venue": source.get("display_name") or "未知来源",
        "url": ("https://doi.org/" + doi) if doi else landing,
        "abstract": abstract_from_inverted(work.get("abstract_inverted_index")),
        "source": "openalex",
        "pdf_url": _openalex_pdf(work),
        "openalex_id": (work.get("id") or "").rsplit("/", 1)[-1],
    }


def _date_filters(year_from, year_to):
    if year_from and year_to and year_from == year_to:
        return [f"publication_year:{year_from}"]
    filters = []
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    return filters


def resolve_venue(name):
    data = _openalex("/sources", {"search": name, "per-page": 10})
    results = data.get("results") or []
    if not results:
        raise ValueError("未找到会议或期刊：" + name)
    query = re.sub(r"[^a-z0-9 ]", " ", name.lower()).strip()
    tokens = set(query.split())

    def score(source):
        display = (source.get("display_name") or "").lower()
        value = 0
        if query and query in display:
            value += 50
        value += len(tokens & set(re.findall(r"[a-z0-9]+", display))) * 5
        if source.get("type") in {"conference", "journal"}:
            value += 3
        return value + min(source.get("works_count") or 0, 20000) / 20000

    ordered = sorted(results, key=score, reverse=True)
    best = ordered[0]
    return {
        "id": (best.get("id") or "").rsplit("/", 1)[-1],
        "name": best.get("display_name") or name,
        "type": best.get("type") or "",
        "works_count": best.get("works_count") or 0,
        "alternatives": [
            item.get("display_name")
            for item in ordered[1:4]
            if item.get("display_name")
        ],
    }


def openalex_venue(venue, year_from=None, year_to=None):
    info = resolve_venue(venue)
    filters = [f"primary_location.source.id:{info['id']}"] + _date_filters(
        year_from, year_to
    )
    items = []
    cursor = "*"
    raw_total = 0
    processed = 0
    filtered = 0
    cursors = set()
    while cursor:
        if cursor in cursors:
            break
        cursors.add(cursor)
        data = _openalex(
            "/works",
            {
                "filter": ",".join(filters),
                "per-page": 200,
                "cursor": cursor,
                "sort": "publication_date:desc",
            },
        )
        for work in data.get("results") or []:
            if len(items) >= OPENALEX_MAX:
                break
            processed += 1
            item = openalex_item(work)
            if FRONT_MATTER.match(item["title"].strip()):
                filtered += 1
                continue
            items.append(item)
        meta = data.get("meta") or {}
        raw_total = meta.get("count", len(items))
        cursor = meta.get("next_cursor")
        if not cursor or len(items) >= OPENALEX_MAX or not data.get("results"):
            break
    info["raw_total"] = raw_total
    info["filtered_out"] = filtered
    info["processed_records"] = processed
    info["total"] = raw_total - filtered if processed < raw_total else len(items)
    info["fetched"] = len(items)
    info["truncated"] = processed < raw_total
    return items, info


def openalex(query, limit, year_from=None, year_to=None):
    safe = re.sub(r"[(),:]", " ", query)
    filters = [f"title_and_abstract.search:{safe}"] + _date_filters(year_from, year_to)
    data = _openalex(
        "/works",
        {
            "filter": ",".join(filters),
            "per-page": min(200, max(1, limit)),
            "sort": "relevance_score:desc",
        },
    )
    return [openalex_item(work) for work in data.get("results") or []]


def ranked_venue(candidates, year_from=None, year_to=None):
    unique = {}
    for rank, c0 in enumerate(candidates):
        c = dict(c0)
        year = c.get("year")
        if (year_from or year_to) and (
            not year or (year_from and year < year_from) or (year_to and year > year_to)
        ):
            continue
        key = identity(c)
        provenance = {"source": c.get("source"), "query": "venue"}
        if key in unique:
            old = unique[key]
            old["provenance"].append(provenance)
            for k, v in c.items():
                if not old.get(k) and v:
                    old[k] = v
            continue
        c.update(
            identity=key,
            score=1 / (rank + 1),
            reason="会议/期刊目录收录",
            provenance=[provenance],
        )
        unique[key] = c
    return list(unique.values())
