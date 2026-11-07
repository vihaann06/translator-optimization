"""Local (no LLM, no browser) tools over a v2 render: query a collection, expand a collapsed block."""
import re

MAX_ROWS = 50


def num(value):
    value = re.sub(r"\[\d+\]", " ", str(value)).replace("$", "")
    m = re.search(r"-?\d[\d,]*\.?\d*", value)
    return float(m.group().replace(",", "")) if m else None


def _col(header, col):
    if col is None:
        return None
    if isinstance(col, int) or str(col).isdigit():
        return int(col)
    if not header:
        return None
    c = str(col).strip().lower()
    for i, h in enumerate(header):
        if h.strip().lower() == c:
            return i
    for i, h in enumerate(header):
        if c in h.lower():
            return i
    return None


def _cell(row, i):
    if isinstance(row, list):
        return row[i] if i is not None and i < len(row) else ""
    return row


def _ok(value, op, target):
    a, b = num(value), num(target)
    v, t = str(value).lower(), str(target).lower()
    if op in ("<", ">", "<=", ">=") and a is not None and b is not None:
        return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
    if op in ("=", "=="):
        return v.strip() == t.strip() or (a is not None and a == b and re.fullmatch(r"[\d$,.\s-]+", t) is not None)
    if op == "!=":
        return v.strip() != t.strip()
    return t in v


def query(render, collection, contains=None, where=None, count=False, sum=None, group_by=None, offset=0, limit=20,
          **_):
    coll = render["collections"].get(str(collection).strip())
    if not coll:
        return f"query error: unknown collection {collection!r}; available: {', '.join(render['collections']) or 'none'}"
    header, rows = coll["header"], coll["rows"]
    line = lambda r: " | ".join(r) if isinstance(r, list) else r
    hits = list(range(len(rows)))
    if contains:
        hits = [i for i in hits if str(contains).lower() in line(rows[i]).lower()]
    for cond in where or []:
        if not isinstance(cond, (list, tuple)) or len(cond) != 3:
            return f"query error: where items must be [column, op, value], got {cond!r}"
        ci = _col(header, cond[0])
        if ci is None and header:
            return f"query error: unknown column {cond[0]!r}; columns: {' | '.join(header)}"
        hits = [i for i in hits if _ok(_cell(rows[i], ci), cond[1], cond[2])]
    out = [f"{collection}: {len(hits)} of {len(rows)} loaded rows match"
           + (" (only rows loaded on this page are searched)" if len(rows) >= 20 else "")]
    if sum:
        si = _col(header, sum)
        vals = [num(_cell(rows[i], si)) for i in hits]
        nums = [v for v in vals if v is not None]
        out.append(f"sum({sum}) = {round(_sum(nums), 4)} over {len(nums)} numeric values")
    if group_by:
        gi = _col(header, group_by)
        groups = {}
        for i in hits:
            groups.setdefault(_cell(rows[i], gi), []).append(i)
        for k, idx in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:MAX_ROWS]:
            out.append(f" {k!s}: {len(idx)} rows")
    if count or sum or group_by:
        return "\n".join(out)
    if header:
        out.append(" cols: " + " | ".join(header))
    limit = max(1, min(int(limit or 20), MAX_ROWS))
    offset = max(0, int(offset or 0))
    out.extend(" " + line(rows[i]) for i in hits[offset:offset + limit])
    if len(hits) > offset + limit:
        out.append(f" … {len(hits) - offset - limit} more (use offset={offset + limit})")
    return "\n".join(out)


def _sum(values):
    total = 0.0
    for v in values:
        total += v
    return total


def expand(render, block, offset=0, **_):
    ref = str(block).strip().lstrip("§[").rstrip("]")
    if ref in render["collections"]:
        return query(render, ref, offset=offset, limit=MAX_ROWS)
    text = render["blocks"].get(ref)
    if text is None:
        return f"expand error: unknown block {block!r}"
    return text
