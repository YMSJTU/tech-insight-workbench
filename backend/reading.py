"""Document preparation and evidence-preserving consolidation."""

import copy
import re
from .agents import normalize_text

EXTRACT_VERSION = "extract-v2"
REFERENCE_HEADING = re.compile(
    r"(?im)^\s*(?:(?:\d+|[IVX]+)[.\s]+)?(?:references|bibliography|参考文献)\s*[:：]?\s*$"
)


def research_pages(pages):
    """Drop a standalone bibliography heading and following text, not inline mentions."""
    result = []
    stopped = False
    for p in pages:
        text = p["text"]
        match = REFERENCE_HEADING.search(text)
        if match:
            text = text[: match.start()]
            stopped = True
        if text.strip():
            result.append({**p, "text": text})
        if stopped:
            break
    return result, stopped


def merge_techniques(outputs, groups=None):
    techs = [copy.deepcopy(t) for o in outputs for t in o["techniques"]]
    if groups is None:
        by_name = {}
        for i, t in enumerate(techs):
            name = re.sub(r"\W+", "", normalize_text(t["name"]).casefold())
            by_name.setdefault(name, []).append(i)
        groups = list(by_name.values())
    merged = []
    for indices in groups:
        target = copy.deepcopy(techs[indices[0]])
        for field in ["mechanism", "problem", "conditions", "limitations"]:
            target[field] = "\n".join(dict.fromkeys(techs[i][field] for i in indices))
        evidence = {}
        classes = {}
        for i in indices:
            for e in techs[i]["evidence"]:
                evidence[(e["block_id"], normalize_text(e["quote"]))] = e
            for c in techs[i].get("classifications", []):
                if (
                    c["node_id"] not in classes
                    or c["confidence"] > classes[c["node_id"]]["confidence"]
                ):
                    classes[c["node_id"]] = c
        target["evidence"] = list(evidence.values())
        target["classifications"] = list(classes.values())
        merged.append(target)
    proposals = {}
    for o in outputs:
        for p in o.get("proposals", []):
            proposals[(p["dimension"], p.get("parent_id"), p["name"].strip())] = p
    return {
        "summary": "\n\n".join(
            dict.fromkeys(o["summary"] for o in outputs if o["summary"].strip())
        ),
        "techniques": merged,
        "proposals": list(proposals.values()),
    }
