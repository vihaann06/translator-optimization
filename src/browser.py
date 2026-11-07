"""Live browser: page-state capture (for agents and offline replay) and action execution."""
import asyncio
import json

TAG_JS = r"""() => {
  let n = window.__widCounter || 0;
  for (const el of document.querySelectorAll('*')) {
    if (!el.hasAttribute('data-wid')) { n += 1; el.setAttribute('data-wid', String(n)); }
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) {
      el.setAttribute('data-live-value', el.value == null ? '' : String(el.value));
      if (el.type === 'checkbox' || el.type === 'radio') el.setAttribute('data-live-checked', String(el.checked));
    }
    if (el instanceof HTMLOptionElement) el.setAttribute('data-live-selected', String(el.selected));
  }
  window.__widCounter = n;
  return {scrollX: window.scrollX, scrollY: window.scrollY, width: window.innerWidth,
          height: window.innerHeight, pageHeight: document.documentElement.scrollHeight};
}"""


async def capture(page, cdp):
    """Everything a translator may need, all JSON-serialisable and reusable offline."""
    viewport = None
    for frame in page.frames:
        try:
            result = await frame.evaluate(TAG_JS)
            if frame == page.main_frame:
                viewport = result
        except Exception:
            pass
    ax = await cdp.send("Accessibility.getFullAXTree")
    snap = await cdp.send("DOMSnapshot.captureSnapshot", {"computedStyles": [], "includeDOMRects": True})
    strings = snap["strings"]
    wid_by_backend, wid_parent, bounds = {}, {}, {}
    for doc in snap["documents"]:
        nodes = doc["nodes"]
        wid_of_index = {}
        for i, (backend, attributes) in enumerate(zip(nodes["backendNodeId"], nodes["attributes"])):
            attrs = {strings[k]: strings[v] for k, v in zip(attributes[::2], attributes[1::2])}
            if "data-wid" in attrs:
                wid_by_backend[str(backend)] = attrs["data-wid"]
                wid_of_index[i] = attrs["data-wid"]
        for i, parent in enumerate(nodes["parentIndex"]):
            p = parent
            while p != -1 and p not in wid_of_index:
                p = nodes["parentIndex"][p]
            if i in wid_of_index and p != -1:
                wid_parent[wid_of_index[i]] = wid_of_index[p]
        layout = doc["layout"]
        for index, rect in zip(layout["nodeIndex"], layout["bounds"]):
            if index in wid_of_index:
                bounds[wid_of_index[index]] = [round(x) for x in rect]
    html = await page.content()
    return {"url": page.url, "title": await page.title(), "viewport": viewport,
            "ax_nodes": ax["nodes"], "wid_by_backend": wid_by_backend,
            "wid_parent": wid_parent, "bounds": bounds, "html": html}


async def _settle(page):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass


def _loc(page, wid):
    selector = f'[data-wid="{wid}"]'
    for frame in page.frames:
        loc = frame.locator(selector)
        yield loc.first


async def _first_existing(page, wid):
    for loc in _loc(page, wid):
        try:
            if await loc.count():
                return loc
        except Exception:
            continue
    raise ValueError(f"element {wid} not found on page")


async def execute(page, action, wid):
    """Run one parsed action. Returns a short outcome string; raises on failure."""
    kind = action["type"]
    if kind == "click":
        loc = await _first_existing(page, wid)
        try:
            await loc.click(timeout=5000)
        except Exception:
            await loc.evaluate("el => el.click()")
    elif kind == "hover":
        await (await _first_existing(page, wid)).hover(timeout=5000)
    elif kind == "type":
        loc = await _first_existing(page, wid)
        try:
            await loc.fill(str(action.get("text", "")), timeout=5000)
        except Exception:
            await loc.click(timeout=5000)
            await page.keyboard.press("Control+A")
            await page.keyboard.type(str(action.get("text", "")))
        if action.get("press_enter"):
            await loc.press("Enter")
    elif kind == "select":
        loc = await _first_existing(page, wid)
        option = str(action.get("option", ""))
        try:
            await loc.select_option(label=option, timeout=5000)
        except Exception:
            await loc.select_option(value=option, timeout=5000)
    elif kind == "check" or kind == "uncheck":
        loc = await _first_existing(page, wid)
        await (loc.check if kind == "check" else loc.uncheck)(timeout=5000, force=True)
    elif kind == "press":
        await page.keyboard.press(str(action["key"]))
    elif kind == "scroll":
        dy = 600 if action.get("direction", "down") == "down" else -600
        await page.mouse.wheel(0, dy)
    elif kind == "goto":
        await page.goto(str(action["url"]), timeout=30000)
    elif kind == "go_back":
        await page.go_back(timeout=15000)
    elif kind == "noop":
        await asyncio.sleep(2)
    else:
        raise ValueError(f"unknown action type {kind!r}")
    await _settle(page)
    return "ok"
