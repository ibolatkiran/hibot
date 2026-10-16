"""Parser for Jira "Export -> XML" (RSS) files and merge helper (stdlib only).

parse_file(path)      -> list of issue dicts (see below)
load_dir(dir)         -> {key: issue} for every *.xml in dir (sorted; later files override earlier ones; empty if no dir)
issue dict: key, summary, description (plain text), status, resolution, type, priority, created, updated, resolved,
            assignee, reporter (display names, raw - the caller pseudonymises), components[], labels[],
            comments[{author, created, text}] (document order), custom_fields{name: [values...]}
"""
import glob
import os
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "ul", "ol", "table"}


class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.out.append("\n- ")
        elif tag in BLOCK_TAGS:
            self.out.append("\n")
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.out.append("[image: %s]" % alt)

    def handle_endtag(self, tag):
        if tag in BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        self.out.append(data)


def html_to_text(html):
    """HTML (as found in Jira description/comment bodies) -> plain text with block structure kept as newlines."""
    if not html:
        return ""
    p = _Stripper()
    try:
        p.feed(html)
        p.close()
    except Exception:  # malformed html: fall back to the raw string
        return html.strip()
    lines = [" ".join(l.split()) for l in "".join(p.out).replace("\u00a0", " ").split("\n")]
    return "\n".join(l for l in lines if l).strip()


def _t(el, tag):
    c = el.find(tag)
    return (c.text or "").strip() if c is not None and c.text else ""


def _parse_item(item):
    issue = {
        "key": _t(item, "key"), "summary": _t(item, "summary"), "type": _t(item, "type"), "priority": _t(item, "priority"),
        "status": _t(item, "status"), "resolution": _t(item, "resolution"), "created": _t(item, "created"),
        "updated": _t(item, "updated"), "resolved": _t(item, "resolved"), "assignee": _t(item, "assignee"),
        "reporter": _t(item, "reporter"),
        "description": html_to_text(item.findtext("description") or ""),
        "components": [(c.text or "").strip() for c in item.findall("component") if (c.text or "").strip()],
        "labels": [(l.text or "").strip() for l in item.findall("labels/label") if (l.text or "").strip()],
        "comments": [], "custom_fields": {},
    }
    for c in item.findall("comments/comment"):
        body = "".join(c.itertext()) if len(c) else (c.text or "")
        issue["comments"].append({"author": c.get("author", ""), "created": c.get("created", ""),
                                  "text": html_to_text(body)})
    for cf in item.findall("customfields/customfield"):
        name = _t(cf, "customfieldname")
        if not name:
            continue
        vals = []
        for v in cf.findall("customfieldvalues/customfieldvalue"):
            txt = html_to_text("".join(v.itertext()) if len(v) else (v.text or ""))
            if txt:
                vals.append(txt)
        if vals:
            issue["custom_fields"].setdefault(name, []).extend(vals)
    return issue


def parse_file(path):
    """Issues of one export file. A truncated / partially downloaded file yields the complete items before the error
    (a warning goes to stderr)."""
    issues = []
    try:
        for _, el in ET.iterparse(path, events=("end",)):
            if el.tag == "item":
                issue = _parse_item(el)
                if issue["key"]:
                    issues.append(issue)
                el.clear()
    except ET.ParseError as e:
        sys.stderr.write("WARNING: %s: XML parse error after %d items: %s\n" % (os.path.basename(path), len(issues), e))
    return issues


def load_dir(directory):
    """All *.xml files of a folder merged by key; returns {} when the folder does not exist or is empty."""
    res = {}
    if not directory or not os.path.isdir(directory):
        return res
    for p in sorted(glob.glob(os.path.join(directory, "*.xml"))):
        for iss in parse_file(p):
            res[iss["key"]] = iss
    return res
