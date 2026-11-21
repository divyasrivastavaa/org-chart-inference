"""
org_chart.py  -  interactive, expandable org chart for one company (plain HTML/CSS/JS).

Starts with the top of the company only. Each card has a pill with the number of people
under that person; clicking it shows their direct reports below, joined by a line.
People whose line wasn't drawn are listed below the chart, so nobody is lost: team leads
(each with their own small chart) and "Not yet placed" (a simple list, grouped by reason).
Rendered with st.iframe (the replacement for the deprecated st.components.v1.html),
so no extra library is needed.
"""
import html
import json
import re

import pandas as pd


def plain_reason(reason):
    """The stored reason in words for the page (the database keeps the original). These
    people do have a manager: the reason says why the model couldn't tell who."""
    r = reason.strip() if isinstance(reason, str) else ""
    if not r or r in ("below threshold", "confidence below threshold"):
        return "Confidence below the threshold"
    if r.startswith("team manager not found"):
        return "Their team's lead may be missing from the data"
    if r == "candidate managers have unknown team":
        return "The possible managers' teams are unknown"
    if "possible managers" in r:
        return "Several possible managers"
    if r == "title could not be parsed":
        return "Job title could not be read"
    if r == "no more-senior person in same function":
        return "No one more senior in the same function"
    if r.startswith("a teamless 'Head of'"):
        return "A 'Head of' with no team may be the VP"
    if r.startswith("team belongs to"):
        return "Sits in another function's team (dotted line?)"
    if re.fullmatch(r"\d+ CEOs found", r):
        return "The top of the company is unclear"
    return r


def build_payload(view, show_truth, highlight=None):
    """view = one company's rows (after the threshold). Returns a JSON-able dict.
    highlight = an employee_id to open the chart at and outline (from the People tab)."""
    ids = set(view.employee_id)
    nodes = {}
    for r in view.itertuples():
        mgr = r.manager_id if isinstance(r.manager_id, str) and r.manager_id in ids else None
        # Domain view: their line is drawn, but their manager is in another domain
        elsewhere = isinstance(r.manager_id, str) and r.manager_id not in ids
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
            "why": plain_reason(r.reason),
            "top": r.reason == "top of company",
            "x": elsewhere,
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

    def at_top(n):
        return n["l"] == 1 or n["top"] or n["x"]

    roots = sorted([p for p, n in nodes.items() if not n["m"] and at_top(n)], key=sort_key)
    unplaced = [p for p, n in nodes.items() if not n["m"] and not at_top(n)]
    leads = sorted([p for p in unplaced if nodes[p]["k"]],
                   key=lambda p: (-nodes[p]["tot"], sort_key(p)))
    groups = {}
    for p in sorted([p for p in unplaced if not nodes[p]["k"]], key=sort_key):
        groups.setdefault(nodes[p]["why"], []).append(p)
    rest = [{"r": r, "ids": v} for r, v in
            sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))]
    return {"nodes": nodes, "roots": roots, "leads": leads, "rest": rest,
            "n_rest": sum(len(g["ids"]) for g in rest),
            "led": sum(nodes[p]["tot"] for p in leads), "truth": bool(show_truth),
            "vacancies": any(n["v"] for n in nodes.values()),
            "leavers": any(n["lf"] for n in nodes.values()),
            "unchecked": any(n["u"] for n in nodes.values()),
            "hl": highlight if highlight in nodes else None}


def render_html(view, show_truth, highlight=None):
    data = json.dumps(build_payload(view, show_truth, highlight), separators=(",", ":"))
    return TEMPLATE.replace("__DATA__", data.replace("</", "<\\/"))


LEVEL_CSS = """<style>
.lv-grid { display:grid; gap:12px; align-items:start;
           grid-template-columns:repeat(auto-fit, minmax(min(100%, 300px), 1fr)); }
.lv-box { border:1px solid rgba(128,128,128,.4); border-radius:10px; min-width:0; }
.lv-head { font-weight:600; padding:8px 12px;
           border-bottom:1px solid rgba(128,128,128,.4); }
.lv-list { max-height:420px; overflow-y:auto; padding:4px 12px 8px; }
.lv-p { padding:5px 0; border-bottom:1px solid rgba(128,128,128,.18);
        overflow-wrap:anywhere; font-size:14px; line-height:1.3; }
.lv-p summary { cursor:pointer; }
.lv-t, .lv-n { display:block; font-size:12.5px; opacity:.72; }
.lv-p ul { margin:4px 0 2px; padding-left:16px; }
.lv-p li { margin:3px 0; font-size:13px; }
.lv-none { opacity:.65; font-size:13px; padding:6px 0; }
</style>"""


def render_levels(people, labels):
    """Reports tab: one box per level, each scrolling on its own. people = employee_id,
    full_name, title, level_now, manager_id, reason. Everything is escaped."""
    def who(r):
        title = r.title.strip() if isinstance(r.title, str) and r.title.strip() else "(no title)"
        return html.escape(str(r.full_name)), html.escape(title)

    def level_of(x):
        return int(x) if pd.notna(x) and int(x) in labels else None

    rows = list(people.assign(_nm=people.full_name.astype(str)).sort_values("_nm").itertuples())
    reports, by_level = {}, {}
    for r in rows:
        by_level.setdefault(level_of(r.level_now), []).append(r)
        if isinstance(r.manager_id, str):
            reports.setdefault(r.manager_id, []).append(r)
    boxes = []
    for key, label in [*labels.items(), (None, "unknown")]:
        here = by_level.get(key, [])
        items = []
        for r in here:
            name, title = who(r)
            line = "" if isinstance(r.manager_id, str) or r.reason == "top of company" \
                else '<span class="lv-n">reporting line not confirmed</span>'
            mine = reports.get(r.employee_id, [])
            if mine:
                under = "".join("<li><b>{}</b><span class=\"lv-t\">{}</span></li>".format(*who(k))
                                for k in mine)
                n = f"{len(mine)} direct report{'s' if len(mine) != 1 else ''}"
                items.append(f'<div class="lv-p"><details><summary><b>{name}</b>'
                             f'<span class="lv-t">{title}</span><span class="lv-n">{n}</span>'
                             f"{line}</summary><ul>{under}</ul></details></div>")
            else:
                items.append(f'<div class="lv-p"><b>{name}</b><span class="lv-t">{title}</span>'
                             f"{line}</div>")
        body = "".join(items) or '<div class="lv-none">No one at this level.</div>'
        boxes.append(f'<section class="lv-box"><div class="lv-head">{html.escape(label)} '
                     f'({len(here):,})</div><div class="lv-list" tabindex="0">{body}</div>'
                     "</section>")
    return LEVEL_CSS + '<div class="lv-grid">' + "".join(boxes) + "</div>"


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
  #np-note { margin:2px 0 10px; font-size:13.5px; color:var(--muted); }
  .np-part { margin:0 0 10px; }
  .np-part > summary { margin:6px 0; font-size:15px; font-weight:700; cursor:pointer; }
  .np-box { max-height:calc(100vh - 60px); overflow:auto; border:1px solid #e5e7eb;
            border-radius:10px; padding:6px 8px; }
  .lead { border-bottom:1px solid #eef0f3; padding:4px 0; }
  .lead > summary { cursor:pointer; font-size:13.5px; padding:3px 2px; }
  .lead .size { color:var(--muted); font-size:12.5px; }
  /* One team lead's small chart: scrolls on its own, so the lines scroll with it */
  .lead .tree { position:relative; max-height:520px; overflow:auto; padding:0 4px 8px; }
  .why { border-bottom:1px solid #eef0f3; padding:4px 0; }
  .why > summary { cursor:pointer; font-size:13.5px; font-weight:700; padding:3px 2px; }
  .rest-grid { display:grid; gap:12px; padding:6px 4px 10px;
               grid-template-columns:repeat(auto-fill, minmax(min(100%, 210px), 1fr)); }
  .rest-grid .card { width:auto; }
  hr { border:0; border-top:1px solid #e5e7eb; margin:6px 0; }
</style>
<div class="section" id="main"><h4>Org chart <span class="hint">· click a number to see that
  person's team</span></h4><div class="tree"></div></div>
<hr>
<div class="section" id="np">
  <div id="np-note">These people do have a manager; the model wasn't confident enough to say
    who, so it drew no line.</div>
  <details class="np-part" id="leads"><summary id="leads-title"></summary>
    <div class="np-box" id="lead-list"></div></details>
  <details class="np-part" id="rest"><summary id="rest-title"></summary>
    <div class="np-box" id="rest-list"></div></details>
</div>
<script>
const D = __DATA__;
const N = D.nodes;
const state = { main: [] };                    // expanded path per chart (main, L0, L1 ...)
const leadsBox = document.getElementById("leads"), restBox = document.getElementById("rest");
// Open the CEO's team straight away (only that one level; deeper levels stay closed).
// With two people at the top, the first one who has a team is opened.
const ceo = D.roots.find(id => N[id].k.length);
if (ceo) state.main.push(ceo);
const fmt = n => n.toLocaleString("en-US");
D.leads.forEach((id, i) => { state["L" + i] = [id]; });   // a lead's chart opens on their team

document.getElementById("leads-title").textContent = "Team leads – reporting line not confirmed (" +
  fmt(D.leads.length) + ", leading " + fmt(D.led) + " people)";
document.getElementById("rest-title").textContent = "Not yet placed (" + fmt(D.n_rest) + ")";
if (!D.leads.length) document.getElementById("lead-list").textContent = "No team leads here.";
if (!D.n_rest) document.getElementById("rest-list").textContent = "No one here.";

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
  else if (n.x) meta = "reports to someone in another domain";
  else meta = (n.k.length ? "line not confirmed: " : "not placed: ") + esc(n.why);
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
  const first = sec === "main" ? D.roots : [D.leads[+sec.slice(1)]];
  if (!first.length) {
    root.innerHTML = '<div class="empty">No CEO found in this view.</div>';
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
  // A team lead's chart scrolls inside its own box: draw its lines there, so they scroll along
  const box = sec === "main" ? document.getElementById(sec)
                             : document.querySelector("#" + sec + " .tree");
  box.querySelectorAll("svg.links").forEach(s => s.remove());
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "links");
  svg.setAttribute("width", box.scrollWidth); svg.setAttribute("height", box.scrollHeight);
  const b = box.getBoundingClientRect();
  box.querySelectorAll(".group").forEach(g => {
    const parent = box.querySelector('.card.open[data-id="' + CSS.escape(g.dataset.parent) + '"]');
    if (!parent) return;
    const p = parent.getBoundingClientRect(), q = g.getBoundingClientRect();
    const x = p.left + p.width / 2 - b.left + box.scrollLeft;   // scroll is 0 for "main"
    const y1 = p.bottom - b.top + box.scrollTop, y2 = q.top - b.top + box.scrollTop;
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

// Team leads: one small collapsible chart each, drawn when it is opened
const leadList = document.getElementById("lead-list");
D.leads.forEach((id, i) => {
  const n = N[id], el = document.createElement("details");
  el.className = "lead"; el.id = "L" + i;
  el.innerHTML = "<summary><b>" + esc(n.n) + "</b> · " + esc(n.t) + ' <span class="size">· team of ' +
                 fmt(n.tot) + '</span></summary><div class="tree"></div>';
  el.addEventListener("toggle", () => { if (el.open) render(el.id); });
  leadList.appendChild(el);
});
// Not yet placed: a grid of cards per reason; each group is closed and drawn when first opened
state.rest = [];
const restList = document.getElementById("rest-list");
D.rest.forEach(g => {
  const el = document.createElement("details");
  el.className = "why";
  el.innerHTML = "<summary>" + esc(g.r) + " (" + fmt(g.ids.length) + ')</summary>' +
                 '<div class="rest-grid"></div>';
  const grid = el.querySelector(".rest-grid");
  const fill = () => {
    if (!grid.children.length) g.ids.forEach(id => grid.appendChild(card(id, "rest", 0)));
  };
  el.addEventListener("toggle", () => { if (el.open) fill(); });
  if (g.ids.includes(D.hl)) { fill(); el.open = true; }     // picked in the People tab
  restList.appendChild(el);
});

// A person picked in the People tab: open the chart or the list they are in
if (D.hl) {
  const chain = [];
  for (let p = D.hl; N[p].m && chain.length < 100; p = N[p].m) chain.unshift(N[p].m);
  const top = chain.length ? chain[0] : D.hl, i = D.leads.indexOf(top);
  if (D.roots.includes(top)) { if (chain.length) state.main = chain; }
  else if (i >= 0) {
    if (chain.length) state["L" + i] = chain;
    leadsBox.open = true;
    document.getElementById("L" + i).open = true;
    render("L" + i);
  } else restBox.open = true;
}
[leadsBox, restBox].forEach(b => b.addEventListener("toggle", () => {
  if (b.open && !b.querySelector(".hl")) b.scrollIntoView({ block: "start" });
}));
render("main");
const picked = document.querySelector(".card.hl");
if (picked) picked.scrollIntoView({ block: "center" });
window.addEventListener("resize", () => {
  drawLinks("main");
  document.querySelectorAll(".lead[open]").forEach(el => drawLinks(el.id));
});
</script>
"""
