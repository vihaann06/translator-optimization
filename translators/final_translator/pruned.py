"""Configurable pruned renderer of the Chromium AX tree (task-agnostic).

Pruning levers (each a keyword option of render()):
  ids="interactive"  only interactive/focusable elements get an [id]; text and layout nodes get none
  dup_text           drop StaticText already contained in the enclosing element's name
  collapse           drop unnamed layout wrappers (generic, list, listitem, paragraph, ...), keep children
  merge_text         join consecutive text lines at the same depth into one line, no role prefix
  indent             indentation string per depth level
  labels             unnamed form controls borrow the nearest preceding text as a label
  tables             one line per table row: "row: cell | cell | [id] link "View""
  max_options        long <select>/listbox option lists keep the selected + first N options
  sections           landmarks become "## role" headers; banner/navigation/contentinfo on one line
  viewport           0 = whole page; k>0 = keep only elements within k screens of the visible area
  block_breaks       merge_text only joins text from the same block (same parent, ignoring inline wrappers)
  value_dedup        drop text that repeats a form control's current value
  group_items        items of a repeated container (>=3 children) that render to 2-12 lines get a "- " bullet
                     on their first line and "  " on the rest (outermost such item wins)
  cleanup            also drop layout-table wrappers, icon-font glyphs (Unicode private use area) and lines made
                     only of invisible characters
"""
import json

INTERACTIVE = {"link", "button", "textbox", "searchbox", "combobox", "listbox", "option", "checkbox", "radio",
               "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "switch", "slider", "spinbutton",
               "treeitem", "MenuListOption", "PopUpButton", "ToggleButton", "disclosure-triangle", "DisclosureTriangle"}
FORM_CONTROLS = {"textbox", "searchbox", "combobox", "listbox", "checkbox", "radio", "spinbutton", "slider", "switch"}
WRAPPERS = {"generic", "none", "group", "list", "listitem", "LabelText", "paragraph", "rowgroup", "Section", "div",
            "strong", "emphasis", "DescriptionList", "DescriptionListTerm", "DescriptionListDetail", "term",
            "definition", "Abbr", "time", "mark", "code", "superscript", "subscript", "blockquote", "figure",
            "article", "region", "form", "Pre", "Canvas", "Ruby", "insertion", "deletion", "ListMarker",
            "MenuListPopup", "Iframe", "IframePresentational", "RootWebArea", "WebArea", "document", "status",
            "log", "tooltip", "note", "presentation", "contentinfo_section", "FooterAsNonLandmark",
            "HeaderAsNonLandmark", "SectionWithoutName"}
DROP = {"InlineTextBox", "LineBreak", "ListMarker"}
LANDMARKS = {"banner", "navigation", "contentinfo", "complementary", "main", "search"}
COMPACT_LANDMARKS = {"banner", "navigation", "contentinfo"}
INLINE = {"strong", "emphasis", "mark", "code", "Abbr", "time", "superscript", "subscript", "insertion", "deletion"}
LAYOUT_TABLE = {"LayoutTable", "LayoutTableRow", "LayoutTableCell"}
INVISIBLE = " \t\xa0\u200b\u200c\u200d\u2060\ufeff"
CELLS = {"cell", "gridcell", "columnheader", "rowheader"}
STATES = {"checked", "selected", "disabled", "expanded", "pressed", "required", "readonly", "level",
          "valuetext", "invalid"}


def _val(node, key):
    return " ".join(str(node.get(key, {}).get("value", "") or "").split())


class Renderer:
    def __init__(self, state, ids="interactive", dup_text=True, collapse=True, merge_text=True, indent=" ",
                 labels=False, tables=False, max_options=0, sections=False, viewport=0, block_breaks=False,
                 value_dedup=False, group_items=False, cleanup=False):
        self.nodes = {n["nodeId"]: n for n in state["ax_nodes"]}
        self.wids = state["wid_by_backend"]
        self.bounds = state.get("bounds", {})
        vp = state.get("viewport") or {}
        self.top = vp.get("scrollY", 0) - viewport * vp.get("height", 720)
        self.bottom = vp.get("scrollY", 0) + (1 + viewport) * vp.get("height", 720)
        self.vp_y, self.vp_h = vp.get("scrollY", 0), vp.get("height", 720)
        self.o = dict(ids=ids, dup_text=dup_text, collapse=collapse, merge_text=merge_text, indent=indent,
                      labels=labels, tables=tables, max_options=max_options, sections=sections, viewport=viewport,
                      block_breaks=block_breaks, value_dedup=value_dedup, group_items=group_items, cleanup=cleanup)
        self.parent = {c: n["nodeId"] for n in state["ax_nodes"] for c in n.get("childIds", [])}
        self.last_block = None
        self.merge_floor = 0
        self.lines, self.id_map, self.reverse = [], {}, {}
        self.recent_text = ""
        self.recent_name = ""
        self.hidden_above = self.hidden_below = 0

    # ---- helpers -------------------------------------------------------------------------------
    def wid(self, node):
        return self.wids.get(str(node.get("backendDOMNodeId")))

    def props(self, node):
        out = {}
        for p in node.get("properties", []):
            if p["name"] in STATES:
                v = p["value"].get("value")
                if p["name"] == "level" and _val(node, "role") != "heading":
                    continue
                if p["name"] == "invalid" and v in ("false", False):
                    continue
                if p["name"] in ("checked", "selected", "expanded", "pressed") or v not in (False, None, ""):
                    out[p["name"]] = v
        return out

    def focusable(self, node):
        return any(p["name"] == "focusable" and p["value"].get("value") is True for p in node.get("properties", []))

    def interactive(self, node, role):
        return self.wid(node) is not None and (role in INTERACTIVE or self.focusable(node) or self.o["ids"] == "all")

    def short(self, wid):
        if wid not in self.reverse:
            self.reverse[wid] = str(len(self.reverse) + 1)
            self.id_map[self.reverse[wid]] = wid
        return self.reverse[wid]

    def visible(self, wid):
        if not self.o["viewport"] or wid is None or wid not in self.bounds:
            return 0
        x, y, w, h = self.bounds[wid]
        if w * h == 0:
            return 0
        if y + h < self.top:
            return -1
        if y > self.bottom:
            return 1
        return 0

    def fmt(self, node, role, name, interactive, label=None):
        s = (f"[{self.short(self.wid(node))}] " if interactive else "") + role
        if name:
            s += " " + json.dumps(name[:200], ensure_ascii=False)
        elif label:
            s += f" (label: {json.dumps(label[:60], ensure_ascii=False)})"
        value = _val(node, "value")
        if value and value != name:
            s += " value=" + json.dumps(value[:200], ensure_ascii=False)
        for k, v in self.props(node).items():
            s += f" {k}={json.dumps(v, ensure_ascii=False)}"
        return s

    def block_of(self, nid):
        p = self.parent.get(nid)
        while p and _val(self.nodes.get(p, {}), "role") in INLINE:
            p = self.parent.get(p)
        return p

    def emit(self, depth, text, kind="node", block=None):
        if not text.strip() or (self.o["cleanup"] and not text.strip(INVISIBLE)):
            return
        last = self.lines[-1] if len(self.lines) > self.merge_floor else None
        if kind == "text" and self.o["merge_text"] and last and last[0] == depth and last[2] == "text" \
                and len(last[1]) < 300 and (not self.o["block_breaks"] or block == self.last_block):
            last[1] += " " + text
        else:
            self.lines.append([depth, text, kind, None])
        if kind == "text":
            self.recent_text = text
            self.recent_name = ""
            self.last_block = block

    def text_of(self, node):
        name = _val(node, "name")
        if self.o["cleanup"]:
            name = " ".join("".join(c for c in name if not 0xE000 <= ord(c) <= 0xF8FF).split())
        return name

    # ---- inline rendering (rows, compact landmarks) --------------------------------------------
    def inline(self, nid, ctx):
        node = self.nodes.get(nid)
        if not node:
            return []
        role = _val(node, "role")
        if role in DROP:
            return []
        if node.get("ignored"):
            return [p for c in node.get("childIds", []) for p in self.inline(c, ctx)]
        name = self.text_of(node)
        if role == "StaticText":
            name = name.strip()
            return [name] if name and not (self.o["dup_text"] and ctx and name in ctx) else []
        if self.interactive(node, role):
            return [self.fmt(node, role, name, True)]
        parts = [p for c in node.get("childIds", []) for p in self.inline(c, name or ctx)]
        if name and not any(p.startswith("[") for p in parts):
            return [name]
        return parts

    # ---- main traversal ------------------------------------------------------------------------
    def options(self, nid, acc):
        node = self.nodes.get(nid)
        if not node:
            return acc
        if _val(node, "role") in ("option", "MenuListOption"):
            acc.append(node)
            return acc
        for c in node.get("childIds", []):
            self.options(c, acc)
        return acc

    def visit(self, nid, depth, ctx, inherited_wid=None):
        node = self.nodes.get(nid)
        if not node:
            return
        role = _val(node, "role")
        if role in DROP:
            return
        wid = self.wid(node) or inherited_wid
        if node.get("ignored"):
            self.visit_children(node, depth, ctx, wid)
            return
        side = self.visible(self.wid(node))
        if side:
            count = 1 + len(node.get("childIds", []))
            if side < 0:
                self.hidden_above += count
            else:
                self.hidden_below += count
            return
        name = self.text_of(node)
        if role == "StaticText":
            name = name.strip()
            if name and not (self.o["dup_text"] and ((ctx and name in ctx) or name in self.recent_name)):
                self.emit(depth, name if self.o["merge_text"] else f"StaticText {json.dumps(name, ensure_ascii=False)}",
                          "text", self.block_of(nid))
            return
        interactive = self.interactive(node, role)
        if role == "image" and not name and not interactive:
            return
        children = node.get("childIds", [])
        if self.o["tables"] and role == "row":
            parts = [p for c in children for p in self.inline(c, None)]
            if interactive:
                parts.insert(0, f"[{self.short(self.wid(node))}]")
            if parts:
                self.emit(depth, "row: " + " | ".join(parts))
            return
        if self.o["sections"] and role in LANDMARKS:
            if role in COMPACT_LANDMARKS:
                parts = [p for c in children for p in self.inline(c, name)]
                if parts:
                    self.emit(depth, f"{role}{' ' + json.dumps(name) if name else ''}: " + " · ".join(parts))
                return
            self.emit(depth, f"## {role}{' ' + json.dumps(name) if name else ''}")
            self.visit_children(node, depth, ctx, wid)
            return
        if self.o["max_options"] and role in ("combobox", "listbox"):
            opts = self.options(nid, [])
            if len(opts) > self.o["max_options"]:
                self.emit(depth, self.fmt(node, role, name, interactive))
                keep = [o for o in opts if self.props(o).get("selected")]
                keep += [o for o in opts[: self.o["max_options"]] if o not in keep]
                for o in keep:
                    self.emit(depth + 1, self.fmt(o, "option", self.text_of(o), self.interactive(o, "option")))
                self.emit(depth + 1, f"... ({len(opts) - len(keep)} more options)", "note")
                return
        named_wrapper = role in WRAPPERS and name and role not in ("RootWebArea", "WebArea", "document")
        is_wrapper = role in WRAPPERS or (self.o["cleanup"] and role in LAYOUT_TABLE)
        if self.o["collapse"] and is_wrapper and not interactive and not self.props(node) and not named_wrapper:
            self.visit_children(node, depth, ctx, wid)
            return
        label = None
        if self.o["labels"] and role in FORM_CONTROLS and not name:
            label = self.recent_text
        self.emit(depth, self.fmt(node, role, name, interactive, label))
        value = _val(node, "value") if self.o["value_dedup"] else ""
        own = f"{name} {value}".strip()
        self.recent_name = own if self.o["dup_text"] else ""
        self.visit_children(node, depth + 1, own or ctx, wid)

    def visit_children(self, node, depth, ctx, wid):
        kids = node.get("childIds", [])
        group = self.o["group_items"] and len(kids) >= 3
        for c in kids:
            start, floor = len(self.lines), self.merge_floor
            if group:
                self.merge_floor = start
            self.visit(c, depth, ctx, wid)
            self.merge_floor = floor
            if group and 2 <= len(self.lines) - start <= 12:
                for line in self.lines[start:]:
                    line[3] = "  "
                self.lines[start][3] = "- "

    def render(self):
        child_ids = {c for n in self.nodes.values() for c in n.get("childIds", [])}
        for nid, node in self.nodes.items():
            if nid not in child_ids:
                self.visit(nid, 0, None)
        text = "\n".join(self.o["indent"] * d + (g or "") + t for d, t, _, g in self.lines)
        if self.o["viewport"]:
            pos = f"[viewport: y={self.vp_y}-{self.vp_y + self.vp_h}; showing about {2 * self.o['viewport'] + 1} screens]"
            if self.hidden_above:
                text = f"({self.hidden_above} elements above, scroll up to see)\n" + text
            if self.hidden_below:
                text += f"\n({self.hidden_below} elements below, scroll down to see)"
            text = pos + "\n" + text
        return {"text": text, "id_map": self.id_map}


def render(state, **opts):
    return Renderer(state, **opts).render()
