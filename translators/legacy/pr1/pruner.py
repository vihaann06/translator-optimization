# Vendored unchanged from huskydj1/webpage-translator PR #1 (5ad829e5): pruning/workspace/pruner.py
"""Rounds 2-5: round-1 cleanup + collapse page chrome that is unrelated to the task.

Chrome = subtrees rooted at banner / navigation / contentinfo / complementary / dialog / alertdialog,
or a region named like ad content. Inside chrome we keep only: text-entry controls, named buttons,
lines whose name shares a content word with the task or history, and the ancestors of kept lines.
"""
import re

LINE = re.compile(r'^(\s*)(?:\[(\d+)\]\s+)?(\S+)(?:\s+"((?:[^"\\]|\\.)*)")?')
CHROME = {"banner", "navigation", "contentinfo"}
OVERLAY = re.compile(r"cookie|privacy|consent|newsletter|sign ?up|subscribe|feedback|survey|advert", re.I)
ENTRY = {"searchbox", "textbox", "combobox", "spinbutton"}
STOP = set("a an the and or of to for in on at by with from is are be as it this that my me i you your our "
           "find show get go buy view see what which who how all any new".split())
WORD = re.compile(r"[a-z0-9]+")


def words(text):
    return {w for w in WORD.findall(text.lower()) if len(w) > 2 and w not in STOP}


def parse(text):
    rows = []
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            rows.append({"line": line, "indent": len(m.group(1)), "id": m.group(2), "role": m.group(3),
                         "name": m.group(4)})
        else:
            rows.append({"line": line, "indent": 10 ** 6, "id": None, "role": None, "name": None})
    return rows


def round1_drop(rows):
    out, stack = [], []
    for row in rows:
        while stack and stack[-1][0] >= row["indent"]:
            stack.pop()
        if row["id"] is None and row["role"] == "ListMarker":
            continue
        if (row["id"] is None and row["role"] == "StaticText" and row["name"] is not None and stack
                and stack[-1][1] is not None and row["name"].strip().lower() == stack[-1][1].strip().lower()):
            continue
        stack.append((row["indent"], row["name"]))
        out.append(row)
    return out


def is_chrome(row):
    if row["role"] in CHROME:
        return True
    if row["role"] in ("dialog", "alertdialog") and row["name"] and OVERLAY.search(row["name"]):
        return True
    return row["role"] == "region" and row["name"] is not None and "ad" in words(row["name"]) | set(
        WORD.findall(row["name"].lower()))


def prune_chrome(representation, task, history):
    rows = round1_drop(parse(representation["text"]))
    vocab = words(task + " " + " ".join(history))
    keep = [True] * len(rows)
    i = 0
    while i < len(rows):
        if not is_chrome(rows[i]):
            i += 1
            continue
        root = rows[i]["indent"]
        end = i + 1
        while end < len(rows) and rows[end]["indent"] > root:
            end += 1
        wanted = [False] * (end - i)
        for k in range(i, end):
            row = rows[k]
            text = row["line"]
            for c in range(k + 1, end):
                if rows[c]["indent"] <= row["indent"] or rows[c]["id"] is not None:
                    break
                text += " " + rows[c]["line"]
            if row["role"] in ENTRY or (vocab & words(text)) or (row["role"] == "button" and row["name"]):
                wanted[k - i] = True
                # keep ancestors inside the chrome subtree
                depth = row["indent"]
                for a in range(k - 1, i - 1, -1):
                    if rows[a]["indent"] < depth:
                        wanted[a - i] = True
                        depth = rows[a]["indent"]
                # keep the direct children text of a kept element line (its label)
                for c in range(k + 1, end):
                    if rows[c]["indent"] <= row["indent"]:
                        break
                    if rows[c]["id"] is None:
                        wanted[c - i] = True
        wanted[0] = True
        for k in range(i, end):
            keep[k] = wanted[k - i]
        i = end
    lines = [row["line"] for row, k in zip(rows, keep) if k]
    ids = {row["id"] for row, k in zip(rows, keep) if k and row["id"]}
    return {"text": "\n".join(lines), "id_map": {k: v for k, v in representation["id_map"].items() if k in ids}}


MAX_OPTIONS = 8
MAX_TEXT_WORDS = 20
KEEP_TEXT_WORDS = 12
ID = re.compile(r"\[\d+\]\s+")


def subtree_end(rows, i):
    end = i + 1
    while end < len(rows) and rows[end]["indent"] > rows[i]["indent"]:
        end += 1
    return end


def compact(rows):
    """Round 3: drop options past the first few in collapsed pickers, later copies of identical
    subtrees (mobile/desktop duplicates), and shorten long prose lines."""
    keep = [True] * len(rows)
    for i, row in enumerate(rows):
        if row["role"] in ("combobox", "listbox") and "expanded=true" not in row["line"]:
            count = 0
            for j in range(i + 1, subtree_end(rows, i)):
                if rows[j]["role"] == "option":
                    count += 1
                    if count > MAX_OPTIONS:
                        for k in range(j, subtree_end(rows, j)):
                            keep[k] = False
    seen, i = set(), 0
    while i < len(rows):
        end = subtree_end(rows, i)
        if end - i >= 5 and keep[i]:
            signature = "\n".join(ID.sub("", r["line"]).strip() for r in rows[i:end])
            if signature in seen:
                for k in range(i, end):
                    keep[k] = False
                i = end
                continue
            seen.add(signature)
        i += 1
    out = []
    for row, k in zip(rows, keep):
        if not k:
            continue
        if row["role"] == "StaticText" and row["id"] is None and row["name"] and len(row["name"].split()) > MAX_TEXT_WORDS:
            pad = row["line"][:row["indent"]]
            row = dict(row, line=f'{pad}StaticText "{" ".join(row["name"].split()[:KEEP_TEXT_WORDS])} …"')
        out.append(row)
    return out


def prune_compact(representation, task, history):
    chrome = prune_chrome(representation, task, history)
    rows = compact(parse(chrome["text"]))
    ids = {r["id"] for r in rows if r["id"]}
    return {"text": "\n".join(r["line"] for r in rows), "id_map": {k: v for k, v in chrome["id_map"].items() if k in ids}}


DEFAULT_ATTRS = re.compile(r" (?:required|readonly|multiselectable|disabled|invalid)=false\b")
LISTITEM_LEVEL = re.compile(r"^(\s*\[\d+\]\s+listitem) level=\d+")


def fold(rows):
    """Round 4: fold a lone ID-less StaticText into its unnamed parent element as the parent's
    quoted name, and drop default-false attributes and listitem nesting levels."""
    out, i = [], 0
    while i < len(rows):
        row = rows[i]
        line = LISTITEM_LEVEL.sub(r"\1", DEFAULT_ATTRS.sub("", row["line"])) if row["role"] else row["line"]
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        after = rows[i + 2]["indent"] if i + 2 < len(rows) else -1
        if (row["id"] and row["name"] is None and nxt and nxt["role"] == "StaticText" and nxt["id"] is None
                and nxt["name"] is not None and nxt["indent"] > row["indent"] and after <= row["indent"]):
            m = re.match(r"^(\s*\[\d+\]\s+\S+)(.*)$", line)
            out.append(f'{m.group(1)} "{nxt["name"]}"{m.group(2)}')
            i += 2
            continue
        out.append(line)
        i += 1
    return out


def prune_fold(representation, task, history):
    compacted = prune_compact(representation, task, history)
    return {"text": "\n".join(fold(parse(compacted["text"]))), "id_map": compacted["id_map"]}


WRAPPERS = {"generic", "group", "none", "list", "listitem", "paragraph", "section"}


def drop_wrappers(rows):
    """Round 5: drop unnamed, attribute-less structural wrappers whose only direct child is an element;
    the child keeps its own line and indentation, so the hierarchy stays readable."""
    keep = [True] * len(rows)
    for i, row in enumerate(rows):
        if not (row["id"] and row["name"] is None and row["role"] in WRAPPERS and "=" not in row["line"]):
            continue
        end = subtree_end(rows, i)
        if end == i + 1:
            continue
        child_indent = rows[i + 1]["indent"]
        children = [j for j in range(i + 1, end) if rows[j]["indent"] == child_indent]
        if len(children) == 1 and rows[children[0]]["id"]:
            keep[i] = False
    return [r for r, k in zip(rows, keep) if k]


def prune(representation, task, history):
    folded = prune_fold(representation, task, history)
    rows = drop_wrappers(parse(folded["text"]))
    ids = {r["id"] for r in rows if r["id"]}
    return {"text": "\n".join(r["line"] for r in rows), "id_map": {k: v for k, v in folded["id_map"].items() if k in ids}}
