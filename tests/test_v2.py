from translators.legacy import v2_compact
from translators.legacy.v2 import tools
from translators.legacy.v2.translator import render


def ax(nodes):
    """Build a minimal AX snapshot: nodes = list of (id, role, name, parent, props, wid, value)."""
    ax_nodes, wid_by_backend, bounds = [], {}, {}
    for nid, role, name, parent, props, wid, *rest in nodes:
        n = {"nodeId": nid, "role": {"value": role}, "name": {"value": name}, "backendDOMNodeId": int(nid),
             "properties": [{"name": k, "value": {"value": v}} for k, v in props.items()], "childIds": []}
        if rest:
            n["value"] = {"value": rest[0]}
        if parent:
            n["parentId"] = parent
            next(x for x in ax_nodes if x["nodeId"] == parent)["childIds"].append(nid)
        ax_nodes.append(n)
        if wid:
            wid_by_backend[str(int(nid))] = wid
            bounds[wid] = [0, 0, 100, 20]
    return {"url": "http://localhost:7770/shop", "title": "Shop", "ax_nodes": ax_nodes,
            "wid_by_backend": wid_by_backend, "wid_parent": {}, "bounds": bounds}


def page(rows=3, alert=None, hidden_wid=None):
    nodes = [("1", "RootWebArea", "Shop", None, {}, None),
             ("2", "navigation", "", "1", {}, "w2"),
             ("3", "link", "Home", "2", {"url": "http://localhost:7770/"}, "w3"),
             ("4", "link", "Docs", "2", {"url": "https://docs.magento.com/x"}, "w4"),
             ("5", "link", "Cart", "2", {"url": "http://localhost:7770/cart"}, "w5"),
             ("6", "main", "", "1", {}, "w6"),
             ("7", "form", "", "6", {}, "w7"),
             ("8", "textbox", "Email", "7", {"required": True}, "w8", "a@b.c"),
             ("9", "button", "Save", "7", {}, "w9"),
             ("10", "table", "", "6", {}, "w10"),
             ("11", "row", "", "10", {}, "w11"),
             ("12", "columnheader", "Order", "11", {}, "w12"),
             ("13", "columnheader", "Total", "11", {}, "w13")]
    for i in range(rows):
        b = 20 + 4 * i
        nodes += [(str(b), "row", "", "10", {}, f"w{b}"),
                  (str(b + 1), "cell", f"#{i}", str(b), {}, f"w{b + 1}"),
                  (str(b + 2), "cell", f"${10 * (i + 1)}.00", str(b), {}, f"w{b + 2}"),
                  (str(b + 3), "link", "View", str(b + 2), {"url": f"http://localhost:7770/o/{i}"}, f"w{b + 3}")]
    if alert:
        nodes.append(("90", "alert", alert, "6", {}, "w90"))
    if hidden_wid:
        nodes.append(("95", "button", "Hidden", "6", {}, hidden_wid))
    state = ax(nodes)
    if hidden_wid:
        state["bounds"][hidden_wid] = [0, 0, 0, 0]
    return state


def test_links_forms_tables_and_ids():
    out = render(page())
    text = out["text"]
    assert '"Home" →/' in text and "↗external docs.magento.com" in text
    assert 'input "Email" = "a@b.c" *' in text
    assert "collection C1" in text and "cols: Order | Total" in text and "#1 | $20.00" in text
    assert set(out["id_map"].values()) >= {"w3", "w8", "w9", "w23"}
    assert v2_compact.translate(page())["text"] == text


def test_hidden_elements_dropped_and_alerts_pinned():
    text = render(page(alert="Saved the product.", hidden_wid="w99"))["text"]
    assert "Hidden" not in text
    assert text.splitlines()[1] == "NOTICE: Saved the product."


def test_large_collection_preview_and_query():
    out = render(page(rows=300))
    assert "more rows hidden" in out["text"]
    assert "#299" not in out["text"]
    assert "300 of 300" in tools.query(out, "C1", count=True)
    assert "sum(Total) = 550.0" in tools.query(out, "C1", where=[["Total", "<=", "100"]], sum="Total")
    assert "#299" in tools.query(out, "C1", contains="#299")
    assert "unknown column" in tools.query(out, "C1", where=[["Nope", "=", "1"]])


def test_change_only_and_stable_ids():
    first = render(page())
    same = render(page(), first["memo"])
    assert "NO VISIBLE CHANGE" in same["text"] and "unchanged" in same["text"]
    assert same["id_map"] == first["id_map"]
    changed = render(page(alert="Error: Email is invalid"), first["memo"])
    assert "NOTICE: Error" in changed["text"] and "changed" in changed["text"]
    block = next(k for k in first["blocks"] if k != "b1" and "Home" in first["blocks"][k])
    assert "Home" in tools.expand(first, block)


def test_new_url_resets_to_full_render():
    first = render(page())
    other = page()
    other["url"] = "http://localhost:7770/other"
    assert "unchanged" not in render(other, first["memo"])["text"]
