"""v2 compact translator: blocks, collections, typed links/forms, change-only updates.

render(state, memo=None) -> {"text", "id_map", "collections", "blocks", "memo"}
  memo is the previous step's returned memo (None on the first step). When the URL is unchanged,
  blocks whose content did not change collapse to one-line stubs and element IDs stay stable.
No LLM involved; a single pass over the Chromium AX tree plus element bounds.
"""
import hashlib
import re
import sys
from urllib.parse import urlsplit

sys.setrecursionlimit(20000)

PUA = re.compile("[\ue000-\uf8ff\u200b]")
DROP = {"InlineTextBox", "LineBreak", "ListMarker"}
BLOCK_ROLES = {"banner", "navigation", "main", "contentinfo", "complementary", "search", "form", "dialog",
               "alertdialog", "tabpanel", "region"}
NOTICE_ROLES = {"alert", "status"}
TEXT_ROLES = {"StaticText"}
FIELD_ROLES = {"textbox", "searchbox", "spinbutton", "combobox", "listbox", "slider"}
TOGGLE_ROLES = {"checkbox", "radio", "switch", "menuitemcheckbox", "menuitemradio"}
CLICK_ROLES = {"button", "menuitem", "tab", "option", "MenuListOption", "treeitem", "PopUpButton", "ToggleButton",
               "DisclosureTriangle", "disclosure-triangle", "summary"}
LINKISH = {"link", "menuitem", "tab"}
ITEM_ROLES = {"article", "listitem", "LayoutTableRow", "row", "group", "generic", "region", "feed", "Section"}
STATE_KEYS = ("expanded", "selected", "pressed", "disabled", "required", "invalid", "readonly")
ABBR = {"button": "btn", "menuitem": "menuitem", "tab": "tab", "option": "option", "MenuListOption": "option",
        "PopUpButton": "btn", "ToggleButton": "btn", "treeitem": "item", "summary": "btn"}
COLL_MIN_ITEMS = 5
COLL_FULL_CHARS = 4500        # ~1.2k tokens: render collections up to this size in full
COLL_PREVIEW_ROWS = 5
MAX_OPTIONS = 15
FULL_EVERY = 5                # force a full render after this many consecutive change-only steps


def clean(value):
    return " ".join(PUA.sub("", str(value or "")).split())


def q(text, limit=120):
    text = clean(text)
    return '"' + (text if len(text) <= limit else text[:limit] + "…") + '"'


def _h(parts):
    return hashlib.md5("\n".join(parts).encode()).hexdigest()[:12]


class Block:
    def __init__(self, key, label, kind="block"):
        self.key, self.label, self.kind, self.lines = key, label, kind, []


class Renderer:
    def __init__(self, state, memo=None):
        self.state = state
        self.nodes = {n["nodeId"]: n for n in state["ax_nodes"]}
        self.wids = state["wid_by_backend"]
        self.bounds = state.get("bounds", {})
        page = urlsplit(state.get("url", ""))
        self.origin = (page.scheme, page.netloc)
        self.same_url = bool(memo) and memo.get("url") == self._url_key(state.get("url", ""))
        self.reverse = dict(memo["reverse"]) if self.same_url else {}
        self.next_id = memo["next_id"] if self.same_url else 1
        self.id_map = {s: w for w, s in self.reverse.items()}
        self.blocks, self.order, self.collections, self.notices = {}, [], {}, []
        self.area_memo = {}
        self.stack = []
        self.last_text = ""
        self.last_heading = ""

    @staticmethod
    def _url_key(url):
        return url.split("#")[0].rstrip("/")

    # ---- basics ------------------------------------------------------------------------------
    def wid(self, node):
        return self.wids.get(str(node.get("backendDOMNodeId")))

    def sid(self, node):
        wid = self.wid(node)
        if wid is None:
            return None
        if wid not in self.reverse:
            self.reverse[wid] = str(self.next_id)
            self.id_map[str(self.next_id)] = wid
            self.next_id += 1
        return self.reverse[wid]

    def role(self, node):
        return node.get("role", {}).get("value", "")

    def name(self, node):
        return clean(node.get("name", {}).get("value", ""))

    def props(self, node):
        return {p["name"]: p["value"].get("value") for p in node.get("properties", [])}

    def kids(self, node):
        return [self.nodes[c] for c in node.get("childIds", []) if c in self.nodes]

    def has_area(self, node):
        nid = node["nodeId"]
        if nid not in self.area_memo:
            wid = self.wid(node)
            own = False
            if wid is not None and wid in self.bounds:
                b = self.bounds[wid]
                own = b[2] > 0 and b[3] > 0
            self.area_memo[nid] = own or any(self.has_area(k) for k in self.kids(node))
        return self.area_memo[nid]

    def hidden(self, node):
        """Element that has no layout box and no visible descendants (collapsed menus, off-DOM popups)."""
        if self.role(node) in {"option", "MenuListOption", "MenuListPopup", "StaticText", "RootWebArea"}:
            return False
        wid = self.wid(node)
        return wid is not None and not self.has_area(node)

    def interactive(self, node):
        role = self.role(node)
        if role in {"link"} | FIELD_ROLES | TOGGLE_ROLES | CLICK_ROLES:
            return True
        return bool(self.props(node).get("focusable")) and role not in {"RootWebArea", "generic", "none", "main",
                                                                        "table", "grid", "rowgroup", "row", "list"}

    def text_of(self, node, skip=None):
        out = []

        def walk(n):
            if n.get("ignored") and not n.get("childIds"):
                return
            r = self.role(n)
            if r in TEXT_ROLES:
                t = self.name(n)
                if t:
                    out.append(t)
                return
            if r in DROP:
                return
            for k in self.kids(n):
                walk(k)
        walk(node)
        return clean(" ".join(out))

    # ---- output ------------------------------------------------------------------------------
    @property
    def block(self):
        return self.stack[-1]

    def emit(self, line, depth=0):
        self.flush_text()
        self.block.lines.append(" " * depth + line)

    def add_text(self, text):
        text = clean(text)
        if text:
            self.pending = (self.pending + " " + text).strip() if getattr(self, "pending", "") else text
            self.last_text = text

    def flush_text(self):
        text = getattr(self, "pending", "")
        if text and self.stack:
            self.block.lines.append(text if len(text) <= 600 else text[:600] + "…")
        self.pending = ""

    def open_block(self, key, label, kind="block"):
        self.flush_text()
        b = Block(key, label, kind)
        self.blocks[key] = b
        if self.stack:
            self.block.lines.append("\x00" + key)
        self.order.append(key)
        self.stack.append(b)
        return b

    def close_block(self):
        self.flush_text()
        self.stack.pop()

    # ---- element lines -----------------------------------------------------------------------
    def states(self, node):
        p, out = self.props(node), []
        for k in STATE_KEYS:
            v = p.get(k)
            if v in (True, "true") or (k == "invalid" and v not in (None, False, "false")):
                out.append(k if k != "required" else "*")
            elif k == "expanded" and v in (False, "false"):
                out.append("collapsed")
        return (" " + " ".join(out)) if out else ""

    def href(self, node):
        url = self.props(node).get("url")
        if not url or url.startswith("javascript:") or url.endswith("#"):
            return ""
        u = urlsplit(url)
        if (u.scheme, u.netloc) == self.origin:
            path = u.path + (("?" + u.query) if u.query else "")
            if u.fragment and not path.strip("/"):
                return ""
            return " →" + (path if len(path) <= 70 else path[:70] + "…")
        host = u.hostname or ""
        if host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
            short = f"{u.netloc}{u.path}"
            return " →" + (short if len(short) <= 70 else short[:70] + "…")
        return f" ↗external {host}"

    def options(self, node):
        opts, current = [], None
        stack = list(self.kids(node))
        while stack:
            n = stack.pop(0)
            r = self.role(n)
            if r in {"option", "MenuListOption"}:
                label = self.name(n) or self.text_of(n)
                opts.append(label)
                if self.props(n).get("selected") in (True, "true"):
                    current = label
            else:
                stack = self.kids(n) + stack
        return opts, current

    def element(self, node, row=False):
        """One-line rendering of an interactive element (used both in trees and in collection rows)."""
        role, name, sid = self.role(node), self.name(node), self.sid(node)
        tag = f"[{sid}]" if sid else ""
        if role == "link":
            label = name or self.text_of(node)
            if not label:
                url = self.props(node).get("url") or ""
                label = urlsplit(url).path.rstrip("/").split("/")[-1] if url else ""
            return f"{tag} {q(label, 90)}{self.href(node)}{self.states(node)}".strip()
        if role in TOGGLE_ROLES:
            checked = self.props(node).get("checked") in (True, "true", "mixed")
            box = ("(o)" if checked else "( )") if "radio" in role else ("[x]" if checked else "[ ]")
            label = name or ("" if row else self.last_text)
            return f"{tag} {box} {q(label, 80) if label else ''}{self.states(node)}".rstrip()
        if role in FIELD_ROLES:
            label = name or ("" if row else self.last_text)
            value = clean(node.get("value", {}).get("value", ""))
            opts, current = self.options(node)
            if opts:
                shown = opts[:MAX_OPTIONS]
                more = f" +{len(opts) - len(shown)} more" if len(opts) > len(shown) else ""
                value = current or value
                kind = "select"
                extra = " {" + " | ".join(o[:40] for o in shown) + more + "}"
            else:
                kind = {"textbox": "input", "searchbox": "search", "spinbutton": "number"}.get(role, role)
                if role == "textbox" and self.props(node).get("multiline"):
                    kind = "textarea"
                extra = ""
            val = f" = {q(value, 200)}" if value else ""
            return f"{tag} {kind} {q(label, 60) if label else ''}{val}{extra}{self.states(node)}".replace("  ", " ").strip()
        label = name or self.text_of(node)
        kind = ABBR.get(role, role)
        return f"{tag} {kind} {q(label, 80) if label else ''}{self.states(node)}".rstrip()

    # ---- collections -------------------------------------------------------------------------
    def inline(self, node):
        """Flatten a subtree into one line: text plus inline interactive elements."""
        parts = []

        def walk(n, parent_name=""):
            if n.get("ignored"):
                for k in self.kids(n):
                    walk(k, parent_name)
                return
            if self.hidden(n):
                return
            r = self.role(n)
            if r in DROP or r == "image":
                return
            if r in TEXT_ROLES:
                t = self.name(n)
                if t and t not in parent_name:
                    parts.append(t)
                return
            if self.interactive(n):
                parts.append(self.element(n, row=True))
                if r in FIELD_ROLES or r in TOGGLE_ROLES or r == "link":
                    return
                nm = self.name(n)
                for k in self.kids(n):
                    walk(k, nm)
                return
            for k in self.kids(n):
                walk(k, parent_name)
        walk(node)
        out, prev = [], None
        for p in parts:
            if p != prev:
                out.append(p)
            prev = p
        return clean(" ".join(out))

    def cell(self, node):
        text, name = self.inline(node), self.name(node)
        return f"{name} {text}".strip() if name and name not in text else text

    def table_rows(self, node):
        rows = []
        stack = list(self.kids(node))
        while stack:
            n = stack.pop(0)
            if self.role(n) == "row":
                rows.append(n)
            elif self.role(n) not in {"table", "grid", "treegrid"}:
                stack = self.kids(n) + stack
        header, body = None, []
        for r in rows:
            cells = [c for c in self.kids(r) if self.role(c) in {"cell", "gridcell", "columnheader", "rowheader"}]
            if not cells:
                continue
            if header is None and all(self.role(c) == "columnheader" for c in cells):
                header = [self.name(c) or self.text_of(c) for c in cells]
                continue
            if self.hidden(r):
                continue
            body.append([self.cell(c) for c in cells])
        return header, body

    def collection(self, node, items_rows, header, label):
        cid = f"C{len(self.collections) + 1}"
        key = f"{cid}:{self.wid(node) or node['nodeId']}"
        lines = [" | ".join(r) if isinstance(r, list) else r for r in items_rows]
        self.collections[cid] = {"label": label, "header": header, "rows": items_rows, "lines": lines}
        b = self.open_block(key, f"{cid} {label}".strip(), kind="collection")
        b.cid = cid
        size = sum(len(x) for x in lines)
        head = f"collection {cid} {q(label, 60) if label else ''} ({len(lines)} rows)".replace("  ", " ")
        if header:
            head += "\n cols: " + " | ".join(h for h in header)
        b.lines.append(head)
        if size <= COLL_FULL_CHARS:
            b.lines.extend(" " + x for x in lines)
        else:
            b.lines.extend(" " + x for x in lines[:COLL_PREVIEW_ROWS])
            b.lines.append(f" … {len(lines) - COLL_PREVIEW_ROWS} more rows hidden: query({cid}) or expand({cid})")
        b.full = [head] + [" " + x for x in lines]
        self.close_block()

    def repeated_children(self, node):
        groups = {}
        for k in self.kids(node):
            if k.get("ignored") or self.hidden(k):
                continue
            r = self.role(k)
            if r in ITEM_ROLES:
                groups.setdefault(r, []).append(k)
        best = max(groups.values(), key=len, default=[])
        if len(best) < COLL_MIN_ITEMS:
            return None
        if self.role(best[0]) == "generic" and len(best) < 2 * COLL_MIN_ITEMS:
            return None
        return best

    # ---- tree walk ---------------------------------------------------------------------------
    def try_collection(self, node, depth, parent_name, name):
        items = self.repeated_children(node)
        if items is None:
            return False
        rows = [self.inline(x) for x in items]
        avg = sum(len(r) for r in rows) / max(1, len(rows))
        if not 0 < avg <= 600:
            return False
        for k in self.kids(node):
            if k in items:
                if k is items[0]:
                    self.collection(node, rows, None, name or self.last_heading)
            else:
                self.walk(k, depth, parent_name)
        return True

    def walk(self, node, depth=0, parent_name=""):
        if node.get("ignored"):
            if self.try_collection(node, depth, parent_name, ""):
                return
            for k in self.kids(node):
                self.walk(k, depth, parent_name)
            return
        if self.hidden(node):
            return
        role, name = self.role(node), self.name(node)
        if role in DROP:
            return
        if role in TEXT_ROLES:
            if name and name != parent_name:
                self.add_text(name)
            return
        if role in NOTICE_ROLES:
            text = self.text_of(node) or name
            if text:
                self.notices.append(text[:300])
            return
        if role == "image":
            if name and len(name) > 3 and name.lower() not in {"image", "img", "logo"} and parent_name != name:
                self.emit(f"img {q(name, 80)}", depth)
            return
        if role in {"table", "grid", "treegrid"}:
            header, body = self.table_rows(node)
            if body:
                self.collection(node, body, header, name or self.last_heading)
            return
        if role == "heading":
            inter = [k for k in self.kids(node) if self.interactive(k)]
            level = int(self.props(node).get("level") or 2)
            mark = "#" * min(level, 3)
            if len(inter) == 1 and clean(self.name(inter[0])) in (name, ""):
                self.emit(f"{mark} {self.element(inter[0])}", depth)
            else:
                self.emit(f"{mark} {name or self.text_of(node)}", depth)
                for k in inter:
                    self.emit(self.element(k), depth + 1)
            self.last_text = self.last_heading = name or self.text_of(node)
            return
        if self.interactive(node):
            self.emit(self.element(node), depth)
            if role in FIELD_ROLES or role in TOGGLE_ROLES or role == "link":
                return
            for k in self.kids(node):
                if self.role(k) not in TEXT_ROLES:
                    self.walk(k, depth + 1, name)
            return
        # link-only lists (menus, nav bars, pagers): one line
        if role in {"list", "menu", "menubar", "navigation", "tablist", "toolbar"} or (role == "generic" and len(node.get("childIds", [])) > 2):
            leaves = self.leaf_interactives(node)
            if leaves is not None and len(leaves) >= 3:
                if role == "navigation":
                    self.open_block(self.wid(node) or node["nodeId"], f"navigation {name}".strip())
                    self.emit((f"{name}: " if name else "") + " · ".join(self.element(x) for x in leaves))
                    self.close_block()
                else:
                    self.emit((f"{name}: " if name else "") + " · ".join(self.element(x) for x in leaves), depth)
                return
        if role != "RootWebArea" and role not in BLOCK_ROLES and self.try_collection(node, depth, parent_name, name):
            return
        opened = False
        if role in BLOCK_ROLES and (role != "region" or name):
            self.open_block(self.wid(node) or node["nodeId"], f"{role} {q(name, 50) if name else ''}".strip())
            opened = True
        elif role == "group" and name:
            self.emit(f"group {q(name, 60)}", depth)
            depth += 1
        if not (opened and self.try_collection(node, depth, parent_name, "")):
            for k in self.kids(node):
                self.walk(k, depth + (1 if opened and role == "form" else 0), name or parent_name)
        if opened:
            self.close_block()

    def leaf_interactives(self, node):
        """Interactive leaves if the subtree holds only links/buttons and their own text, else None."""
        found = []

        def visit(n, inside):
            if n.get("ignored"):
                return all(visit(k, inside) for k in self.kids(n))
            if self.hidden(n):
                return True
            r = self.role(n)
            if r in DROP or r == "image":
                return True
            if r in TEXT_ROLES:
                return inside or not self.name(n)
            if r in LINKISH or r == "button":
                found.append(n)
                return all(visit(k, True) for k in self.kids(n))
            if self.interactive(n) or r in {"heading", "table", "article", "form"}:
                return False
            return all(visit(k, inside) for k in self.kids(n))
        return found if visit(node, False) else None

    # ---- assembly ----------------------------------------------------------------------------
    def assemble(self, root_key, memo):
        hashes = {}

        def block_hash(key):
            b = self.blocks[key]
            parts = [b.label] + [block_hash(x[1:]) if x.startswith("\x00") else x for x in b.lines]
            hashes[key] = _h(parts)
            return hashes[key]
        block_hash(root_key)
        prev = memo.get("hashes", {}) if (self.same_url and memo) else {}
        streak = (memo.get("streak", 0) + 1) if (self.same_url and memo) else 0
        diff = bool(prev) and streak < FULL_EVERY
        changed, unchanged = [], []
        out = []

        def emit_block(key, depth):
            b = self.blocks[key]
            if diff and key != root_key and prev.get(key) == hashes[key]:
                n = sum(1 for x in b.lines if not x.startswith("\x00"))
                ref = b.cid if b.kind == "collection" else f"§{self.short_block(key)}"
                out.append(" " * depth + f"{ref} {b.label}: unchanged ({n} lines) — expand({ref.lstrip('§')})")
                unchanged.append(key)
                return
            if key != root_key:
                tag = "" if not diff else (" (changed)" if key in prev else " (new)")
                if diff:
                    changed.append(key)
                if b.kind != "collection":
                    out.append(" " * depth + f"§{self.short_block(key)} {b.label}{tag}")
                elif tag:
                    out.append(" " * depth + f"{b.cid}{tag}")
            for line in b.lines:
                if line.startswith("\x00"):
                    emit_block(line[1:], depth + (1 if key != root_key else 0))
                else:
                    out.append(" " * (depth + (1 if key != root_key and b.kind != "collection" else 0)) + line)
        emit_block(root_key, 0)
        header = [f"PAGE {q(self.state.get('title', ''), 100)}"]
        if diff:
            removed = [k for k in prev if k not in hashes]
            if not changed and not removed and prev.get(root_key) == hashes[root_key]:
                header.append("NO VISIBLE CHANGE since your previous action (the page is identical).")
            else:
                header.append(f"Same page as before; only changed sections are shown in full "
                              f"({len(changed)} changed/new, {len(unchanged)} unchanged, {len(removed)} removed).")
        for n in self.notices:
            header.append(f"NOTICE: {n}")
        return "\n".join(header + out), hashes, streak if diff else 0

    def short_block(self, key):
        if not hasattr(self, "_bmap"):
            self._bmap = {}
        if key not in self._bmap:
            wid = key.split(":")[-1]
            sid = self.reverse.get(wid)
            self._bmap[key] = sid if sid else f"b{len(self._bmap) + 1}"
        return self._bmap[key]

    def run(self, memo):
        roots = [n for n in self.nodes.values() if not n.get("parentId") or n["parentId"] not in self.nodes]
        root = self.open_block("root", "page")
        for r in roots:
            self.walk(r)
        self.close_block()
        # block keys: give landmark blocks a short id so expand(N) works
        for key in self.order:
            wid = key.split(":")[-1]
            if wid in self.wids.values() and wid not in self.reverse:
                self.reverse[wid] = str(self.next_id)
                self.id_map[str(self.next_id)] = wid
                self.next_id += 1
        text, hashes, streak = self.assemble("root", memo or {})
        expand = {}
        for key, b in self.blocks.items():
            ref = getattr(b, "cid", None) or self.short_block(key)
            expand[ref] = "\n".join(getattr(b, "full", None) or self.flat(key))
        memo_out = {"url": self._url_key(self.state.get("url", "")), "reverse": self.reverse,
                    "next_id": self.next_id, "hashes": hashes, "streak": streak}
        return {"text": text, "id_map": dict(self.id_map), "collections": self.collections, "blocks": expand,
                "memo": memo_out}

    def flat(self, key):
        out = []
        for line in self.blocks[key].lines:
            if line.startswith("\x00"):
                sub = self.blocks[line[1:]]
                out.extend(getattr(sub, "full", None) or self.flat(line[1:]))
            else:
                out.append(line)
        return out


def render(state, memo=None):
    return Renderer(state, memo).run(memo)
