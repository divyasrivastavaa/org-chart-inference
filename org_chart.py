"""
org_chart.py  -  interactive, expandable org chart for one company (plain HTML/CSS/JS).

Starts with the top of the company only. Each card has a pill with the number of people
under that person; clicking it shows their direct reports below, joined by a line.
People without a manager are listed in a separate "Not placed" section, so nobody is lost.
Rendered with st.iframe (the replacement for the deprecated st.components.v1.html),
so no extra library is needed.
"""
import json

import pandas as pd


def build_payload(view, show_truth, highlight=None):
    """view = one company's rows (after the threshold). Returns a JSON-able dict.
    highlight = an employee_id to open the chart at and outline (from the People tab)."""
    ids = set(view.employee_id)
    nodes = {}
    for r in view.itertuples():
        mgr = r.manager_id if isinstance(r.manager_id, str) and r.manager_id in ids else None
        title = r.title.strip() if isinstance(r.title, str) and r.title.strip() else "(no title)"
        vacant = str(getattr(r, "vacant", False)) == "True"     # leavers.csv / what-if
        if vacant:                       # "Chief Security Officer (vacant since Nov 2026, ...)"
            title = f"{title} ({r.vacancy_note})"
        left = str(getattr(r, "left", False)) == "True"         # what-if: left the company
        moving = getattr(r, "left_to", "")
        tag = getattr(r, "tag", None)                            # "manually added", ...
        if getattr(r, "judge", None) == "Gemini":                # what-if: placed by Gemini
            tag = (tag + " · " if isinstance(tag, str) else "") + "placed by Gemini"
        was_under = getattr(r, "was_under", None)                # their manager left
        why = getattr(r, "was_under_why", None)                  # ... or was promoted
        changed = getattr(r, "change_note", None)                # title change, one per line
        # Can this line be checked? Not for empty positions, people added by hand, or real
        # people without a written source (app.py sets "checkable"; default yes)
        checkable = str(getattr(r, "checkable", True)) == "True" and not vacant and not left
        nodes[r.employee_id] = {
            "n": r.full_name, "t": title,
            "l": None if pd.isna(r.level) else int(r.level),
            "m": mgr,
            "c": round(float(r.confidence), 2) if mgr else None,
            "ok": bool(r.manager_id == r.true_manager_id) if (show_truth and mgr and checkable)
            else None,
            "u": bool(show_truth and mgr and not checkable),     # drawn, but not checkable
            "why": r.reason if isinstance(r.reason, str) else "",
            "v": vacant,
            "lf": ("Left → " + moving if isinstance(moving, str) and moving else "Left")
            if left else None,
            "tag": tag if isinstance(tag, str) else "",
            "wu": was_under if isinstance(was_under, str) else "",
            "wy": why if isinstance(why, str) else "manager left",
            "ch": changed.split("\n") if isinstance(changed, str) else [],
            "it": str(getattr(r, "interim", False)) == "True",   # interim: under the CEO
            "k": [], "tot": 0,
        }
    for pid, n in nodes.items():
        if n["m"]:
            nodes[n["m"]]["k"].append(pid)

    def sort_key(pid):
        n = nodes[pid]
        return (n["l"] if n["l"] is not None else 9, n["n"])

    for n in nodes.values():
        n["k"].sort(key=sort_key)

    # People under each person (all levels), bottom-up without recursion
    order, seen = [], set()
    for start in nodes:
        stack = [(start, False)]
        while stack:
            pid, done = stack.pop()
            if done:
                order.append(pid)
                continue
            if pid in seen:
                continue
            seen.add(pid)
            stack.append((pid, True))
            stack.extend((k, False) for k in nodes[pid]["k"] if k not in seen)
    for pid in order:
        nodes[pid]["tot"] = sum(1 + nodes[k]["tot"] for k in nodes[pid]["k"])

    roots = sorted([p for p, n in nodes.items() if not n["m"] and n["l"] == 1], key=sort_key)
    unplaced = sorted([p for p, n in nodes.items() if not n["m"] and n["l"] != 1],
                      key=lambda p: (-nodes[p]["tot"], sort_key(p)))
    return {"nodes": nodes, "roots": roots, "unplaced": unplaced, "truth": bool(show_truth),
            "vacancies": any(n["v"] for n in nodes.values()),
            "leavers": any(n["lf"] for n in nodes.values()),
            "unchecked": any(n["u"] for n in nodes.values()),
            "hl": highlight if highlight in nodes else None}


def render_html(view, show_truth, highlight=None):
    data = json.dumps(build_payload(view, show_truth, highlight), separators=(",", ":"))
    return TEMPLATE.replace("__DATA__", data.replace("</", "<\\/"))


TEMPLATE = r"""
<style>
  :root { --line:#9aa3b2; --ink:#1f2937; --muted:#6b7280; --card:#ffffff; --edge:#d6dae1;
          --pill:#eef1f6; --accent:#2E6FD8; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:"Source Sans Pro",-apple-system,"Segoe UI",Roboto,sans-serif;
         color:var(--ink); background:#fff; }
  .section { position:relative; padding:8px 4px 18px; }
  .section h4 { margin:6px 0 10px; font-size:15px; }
  .section .hint { font-weight:400; color:var(--muted); font-size:13px; }
  .row { position:relative; display:flex; flex-wrap:wrap; justify-content:center; gap:12px;
         padding:6px 0; z-index:1; }
  .group { position:relative; margin-top:34px; border:1px dashed #c8ced8; border-radius:12px;
           padding:26px 10px 10px; background:#fafbfc; z-index:1; }
  .group-label { position:absolute; top:6px; left:12px; font-size:12px; color:var(--muted); }
  .card { width:210px; background:var(--card); border:1px solid var(--edge); border-radius:10px;
          padding:9px 10px 8px; display:flex; gap:9px; align-items:flex-start;
          box-shadow:0 1px 2px rgba(0,0,0,.05); }
  .card.gone { opacity:.5; border-style:dashed; box-shadow:none; }   /* left the company */
  .card.vacant { background:#f1f2f4; border:1.5px dashed #8a93a3; box-shadow:none; }
  .card.vacant .name { color:var(--muted); font-style:italic; }
  .card.vacant .title { max-height:none; }           /* show the whole "(vacant since ...)" note */
  .card.vacant .avatar { background:#b8bec9 !important; }
  .left { color:#a14b00; font-weight:600; }
  .card.open { outline:2px solid var(--accent); outline-offset:1px; }
  .card.hl { outline:3px solid var(--accent); outline-offset:2px; background:#eef3fc; }
  .avatar { flex:0 0 34px; height:34px; border-radius:50%; color:#fff; font-size:13px;
            font-weight:600; display:flex; align-items:center; justify-content:center; }
  .body { min-width:0; flex:1; }
  .name { font-weight:600; font-size:13.5px; white-space:nowrap; overflow:hidden;
          text-overflow:ellipsis; }
  .title { font-size:12px; color:var(--muted); line-height:1.25; max-height:2.5em;
           overflow:hidden; }
  .meta { font-size:11px; color:var(--muted); margin-top:3px; }
  .change { font-size:11px; color:var(--ink); margin-top:4px; padding-top:4px;
            border-top:1px dashed var(--edge); line-height:1.35; }
  .pill { margin-top:6px; border:0; background:var(--pill); color:var(--ink); border-radius:999px;
          padding:2px 9px; font-size:12px; cursor:pointer; display:inline-flex; gap:5px;
          align-items:center; }
  .pill:hover { background:#dfe5ee; }
  .chev { width:6px; height:6px; border-right:1.6px solid currentColor;
          border-bottom:1.6px solid currentColor; transform:rotate(45deg) translateY(-2px); }
  .chev.up { transform:rotate(-135deg) translate(-1px,-1px); }
  svg.links { position:absolute; left:0; top:0; pointer-events:none; z-index:0; overflow:visible; }
  .empty { color:var(--muted); font-size:13px; padding:6px 4px; }
  hr { border:0; border-top:1px solid #e5e7eb; margin:6px 0; }
</style>
<div class="section" id="main"><h4>Org chart <span class="hint">· click a number to see that
  person's team</span></h4><div class="tree"></div></div>
<hr>
<div class="section" id="np"><h4>Not placed <span class="hint" id="np-hint"></span></h4>
  <div class="tree"></div></div>
<script>
const D = __DATA__;
const N = D.nodes;
const state = { main: [], np: [] };            // expanded path per section
// Open the CEO's team straight away (only that one level; deeper levels stay closed).
// With two people at the top, the first one who has a team is opened.
const ceo = D.roots.find(id => N[id].k.length);
if (ceo) state.main.push(ceo);
// A person picked in the People tab: open every manager above them, in the right section
if (D.hl) {
  const chain = [];
  for (let p = D.hl; N[p].m && chain.length < 100; p = N[p].m) chain.unshift(N[p].m);
  const top = chain.length ? chain[0] : D.hl;
  const sec = D.roots.includes(top) ? "main" : "np";
  if (chain.length || sec === "np") state[sec] = chain;
}
const fmt = n => n.toLocaleString("en-US");

document.getElementById("np-hint").textContent =
  "· " + fmt(D.unplaced.length) + " people without a manager (not confident enough to draw a line)";

function initials(name) {
  const p = name.replace(/[^A-Za-z' -]/g, "").trim().split(/\s+/);
  return ((p[0] || "?")[0] + (p.length > 1 ? p[p.length - 1][0] : "")).toUpperCase();
}
function colour(name) {
  let h = 0; for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return "hsl(" + h + ",45%,48%)";
}
function esc(s) {
  return String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
}

function card(id, sec, rowIdx) {
  const n = N[id], el = document.createElement("div");
  const open = state[sec][rowIdx] === id;
  el.className = "card" + (n.lf ? " gone" : "") +           // every card the same colour
                 (n.v ? " vacant" : "") + (open ? " open" : "") + (id === D.hl ? " hl" : "");
  el.dataset.id = id;
  let meta;
  if (n.lf) meta = '<span class="left">' + esc(n.lf) + '</span>';
  else if (n.m) meta = "line confidence " + n.c.toFixed(2);
  else if (n.l === 1) meta = "top of company";
  else if (n.wu) meta = "waiting for a new manager";
  else meta = n.why ? "not placed: " + esc(n.why) : "not placed";
  if (n.wu && n.it && n.m)
    meta = '<span class="left">interim</span> · reports to CEO · was under ' + esc(n.wu) +
           " · " + meta;
  else if (n.wu) meta = '<span class="left">' + esc(n.wy) + '</span> · was under ' + esc(n.wu) + " · " + meta;
  else if (n.m && N[n.m].v) meta = '<span class="left">manager left</span> · ' + meta;
  if (n.tag) meta += " · " + esc(n.tag);
  el.innerHTML =
    '<div class="avatar" style="background:' + colour(n.n) + '">' +
    (n.v ? "?" : esc(initials(n.n))) + '</div>' +
    '<div class="body"><div class="name" title="' + esc(n.n) + '">' + esc(n.n) + '</div>' +
    '<div class="title" title="' + esc(n.t) + '">' + esc(n.t) + '</div>' +
    '<div class="meta">' + meta + '</div>' +
    (n.ch.length ? '<div class="change">' + n.ch.map(esc).join("<br>") + '</div>' : "") +
    '</div>';
  if (n.k.length) {
    const b = document.createElement("button");
    b.className = "pill";
    b.title = n.k.length + " direct report(s), " + fmt(n.tot) + " people in total";
    b.innerHTML = open ? 'Collapse <span class="chev up"></span>'
                       : fmt(n.tot) + ' <span class="chev"></span>';
    b.onclick = () => toggle(sec, rowIdx, id);
    el.querySelector(".body").appendChild(b);
  }
  return el;
}

function render(sec) {
  const root = document.querySelector("#" + sec + " .tree");
  root.innerHTML = "";
  const first = sec === "main" ? D.roots : D.unplaced;
  if (!first.length) {
    root.innerHTML = '<div class="empty">' +
      (sec === "main" ? "No CEO found in this view." : "Everyone has a manager.") + "</div>";
    return;
  }
  const row0 = document.createElement("div");
  row0.className = "row";
  first.forEach(id => row0.appendChild(card(id, sec, 0)));
  root.appendChild(row0);
  state[sec].forEach((pid, i) => {
    const g = document.createElement("div");
    g.className = "group"; g.dataset.parent = pid;
    g.innerHTML = '<div class="group-label">Direct reports of ' + esc(N[pid].n) +
                  " · " + N[pid].k.length + "</div>";
    const row = document.createElement("div");
    row.className = "row";
    N[pid].k.forEach(id => row.appendChild(card(id, sec, i + 1)));
    g.appendChild(row);
    root.appendChild(g);
  });
  drawLinks(sec);
}

function drawLinks(sec) {
  const box = document.getElementById(sec);
  box.querySelectorAll("svg.links").forEach(s => s.remove());
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "links");
  svg.setAttribute("width", box.scrollWidth); svg.setAttribute("height", box.scrollHeight);
  const b = box.getBoundingClientRect();
  box.querySelectorAll(".group").forEach(g => {
    const parent = box.querySelector('.card.open[data-id="' + CSS.escape(g.dataset.parent) + '"]');
    if (!parent) return;
    const p = parent.getBoundingClientRect(), q = g.getBoundingClientRect();
    const x = p.left + p.width / 2 - b.left, y1 = p.bottom - b.top, y2 = q.top - b.top;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", "M" + x + " " + y1 + " V" + y2);
    path.style.stroke = "var(--line)"; path.style.strokeWidth = "1.6"; path.style.fill = "none";
    svg.appendChild(path);
  });
  box.prepend(svg);
}

function toggle(sec, rowIdx, id) {
  const path = state[sec];
  const wasOpen = path[rowIdx] === id;
  path.length = rowIdx;                         // close this row and everything below it
  if (!wasOpen) path.push(id);
  render(sec);
  if (!wasOpen) {
    const groups = document.querySelectorAll("#" + sec + " .group");
    const last = groups[groups.length - 1];
    if (last) last.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

render("main"); render("np");
const picked = document.querySelector(".card.hl");
if (picked) picked.scrollIntoView({ block: "center" });
window.addEventListener("resize", () => { drawLinks("main"); drawLinks("np"); });
</script>
"""
