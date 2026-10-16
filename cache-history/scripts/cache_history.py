"""Hospodaření s prompt cache v Claude Code podle lokálních přepisů (~/.claude/projects).

Použití: python -I cache_history.py [období]
  30 = posledních 30 dní (výchozí), 6h = posledních 6 hodin,
  15.9.-15.10.2026 nebo 2026-09-15..2026-10-15 = období od–do (konec včetně celého dne)
Čte jen lokální soubory, nic neposílá. Vypíše přehled v Markdownu:
kolik útraty padlo na obnovu vypršelé cache (hlavní agent a subagenti) a kam šel zbytek.
"""
import json, os, re, sys, glob, collections
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.expanduser("~/.claude/projects")
ARGS = sys.argv[1:]
PROJECT = None  # --project NÁZEV: jen projekty, jejichž složka obsahuje NÁZEV
if "--project" in ARGS:
    i = ARGS.index("--project")
    ARGS_PROJECT = ARGS[i + 1]
    PROJECT = re.sub(r"[^A-Za-z0-9]", "-", ARGS[i + 1]).lower()
    ARGS = ARGS[:i] + ARGS[i + 2:]
ARG = "".join(ARGS).replace(" ", "").lower() if ARGS else "30"
HOME_PREFIX = re.sub(r"[^A-Za-z0-9]", "-", os.path.expanduser("~")).lower()


def project_name(path):
    """Složka v ~/.claude/projects → čitelný název projektu (bez cesty k domovu)."""
    d = os.path.relpath(path, ROOT).split(os.sep)[0]
    low = d.lower()
    return (d[len(HOME_PREFIX):] if low.startswith(HOME_PREFIX) else d).strip("-") or "~"
NOW = datetime.now(timezone.utc)
UNTIL = NOW
LOCAL = NOW.astimezone().tzinfo


def parse_day(s, year=None):
    """2026-09-15, 15.9.2026 nebo 15.9. (rok doplní year / letošní)."""
    if "-" in s:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=LOCAL)
    parts = [x for x in s.split(".") if x]
    y = int(parts[2]) if len(parts) > 2 else (year or NOW.year)
    return datetime(y, int(parts[1]), int(parts[0]), tzinfo=LOCAL)


if ".." in ARG or ARG.count(".") >= 2:
    # Období od–do: "2026-09-15..2026-10-15" nebo "15.9.-15.10.2026"; konec včetně celého dne.
    a, b = ARG.split("..") if ".." in ARG else ARG.split("-", 1)
    end = parse_day(b)
    start = parse_day(a, end.year)
    SINCE, UNTIL = start, end + timedelta(days=1)
    LABEL = f"{start.day}. {start.month}. {start.year} – {end.day}. {end.month}. {end.year}"
    LABEL_PREFIX = "Prompt cache za období"
elif ARG.endswith("h"):
    SINCE = NOW - timedelta(hours=float(ARG[:-1].replace(",", ".")))
    LABEL, LABEL_PREFIX = f"{ARG[:-1]} h", "Prompt cache za posledních"
else:
    SINCE = NOW - timedelta(days=float(ARG.rstrip("d").replace(",", ".")))
    LABEL, LABEL_PREFIX = f"{ARG.rstrip('d')} dní", "Prompt cache za posledních"

# Ceny vůči ceně běžného vstupu téhož modelu (ceník API).
W_INP, W_W5, W_W1, W_OUT = 1.0, 1.25, 2.0, 5.0
MIN_CTX = 20000  # menší kontext se za výpadek nepočítá


def w_read(model):
    return 0.05 if "opus" in (model or "").lower() else 0.1


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


streams = []  # (kind, calls)
for f in glob.glob(os.path.join(ROOT, "**", "*.jsonl"), recursive=True):
    if os.path.getmtime(f) < SINCE.timestamp():
        continue
    kind = "sub" if os.sep + "subagents" + os.sep in f or "/subagents/" in f else "main"
    proj = project_name(f)
    if PROJECT and PROJECT not in proj.lower():
        continue
    calls = {}
    with open(f, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            m = d.get("message") or {}
            u = m.get("usage")
            if not u or d.get("type") != "assistant" or not d.get("timestamp"):
                continue
            t = ts(d["timestamp"])
            if t < SINCE or t >= UNTIL:
                continue
            cc = u.get("cache_creation") or {}
            w1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
            w5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
            if not cc:
                w5 = u.get("cache_creation_input_tokens", 0) or 0
            key = m.get("id") or d.get("requestId") or d.get("uuid")
            vis = 0  # viditelný výstup ve znacích (text + vstupy nástrojů); thinking bývá skryté
            for blk in m.get("content") or []:
                if blk.get("type") == "text":
                    vis += len(blk.get("text", ""))
                elif blk.get("type") == "tool_use":
                    vis += len(json.dumps(blk.get("input", {}), ensure_ascii=False))
            old = calls.get(key)
            calls[key] = dict(t=t, inp=u.get("input_tokens", 0) or 0, w5=w5, w1=w1,
                              rd=u.get("cache_read_input_tokens", 0) or 0,
                              out=max(u.get("output_tokens", 0) or 0, old["out"] if old else 0),
                              model=m.get("model"), vis=vis + (old["vis"] if old else 0),
                              stop=m.get("stop_reason") or (old["stop"] if old else None),
                              ver=d.get("version"), proj=proj)
    if calls:
        streams.append((kind, sorted(calls.values(), key=lambda c: c["t"])))

if not streams:
    print(f"V {ROOT} nejsou ({LABEL}) žádné přepisy s údaji o tokenech.")
    sys.exit(0)

# Některé verze Claude Code (např. 2.1.296) zapisují output_tokens ze začátku streamu
# (zpráva bez stop_reason, pár tokenů). Takový výstup se odhadne z viditelného obsahu
# poměrem tokenů na znak z korektně zapsaných volání (zahrnuje i skryté thinking).
ok_out = ok_vis = 0
for _, calls in streams:
    for c in calls:
        if c["stop"] and c["vis"]:
            ok_out += c["out"]
            ok_vis += c["vis"]
TOK_PER_CHAR = ok_out / ok_vis if ok_vis else 0.3
fixed = collections.Counter()
for _, calls in streams:
    for c in calls:
        if not c["stop"]:
            est = c["vis"] * TOK_PER_CHAR
            if est > c["out"]:
                fixed["n"] += 1
                fixed["added"] += est - c["out"]
                fixed[("ver", c["ver"])] += 1
                c["out"] = est

# Útrata po položkách: cost[(položka, kind)], tokeny tok[(položka, kind)]
cost = collections.Counter()
tok = collections.Counter()
btok = collections.Counter()   # zápisy do cache podle TTL (pro tabulku spotřeby)
bcost = collections.Counter()
miss ={"main": [], "sub": []}  # (přepsáno, mezera s, zbytečně navíc)
sub_write5 = 0.0                 # cena 5min zápisů subagentů (pro simulaci 1h)
sim_save = 0.0
gaps = collections.Counter()
ctx_sum = collections.Counter()
n_calls = collections.Counter()
n_streams = collections.Counter()
pcost = collections.Counter()   # projekt -> útrata
psub = collections.Counter()    # projekt -> útrata subagentů
prenew = collections.Counter()  # projekt -> útrata za obnovu cache

for kind, calls in streams:
    n_streams[kind] += 1
    prev = None
    for c in calls:
        rd_w = w_read(c["model"])
        write_cost = c["w5"] * W_W5 + c["w1"] * W_W1
        renew_tok = 0
        if prev is not None:
            gap = (c["t"] - prev["t"]).total_seconds()
            if kind == "sub":
                gaps["< 1 min" if gap < 60 else "1–5 min" if gap < 300 else "5–60 min" if gap < 3600 else "> 1 h"] += 1
            prev_ctx = prev["rd"] + prev["w5"] + prev["w1"] + prev["inp"]
            ttl = 3600 if prev["w1"] > prev["w5"] else 300
            rewritten = min(c["w5"] + c["w1"], prev_ctx)
            if gap > ttl and prev_ctx > MIN_CTX and c["rd"] < 0.5 * prev_ctx and rewritten > 0.5 * prev_ctx:
                renew_tok = rewritten
                wr = W_W1 if c["w1"] > c["w5"] else W_W5
                miss[kind].append((rewritten, gap, rewritten * (wr - rd_w)))
                if kind == "sub" and gap <= 3600:
                    sim_save += rewritten * (W_W5 - rd_w)
        renew_cost = write_cost * renew_tok / max(1, c["w5"] + c["w1"])
        for item, t_, v in (
            ("read", c["rd"], c["rd"] * rd_w),
            ("new", c["w5"] + c["w1"] - renew_tok, write_cost - renew_cost),
            ("renew", renew_tok, renew_cost),
            ("inp", c["inp"], c["inp"] * W_INP),
            ("out", c["out"], c["out"] * W_OUT),
        ):
            tok[(item, kind)] += t_
            cost[(item, kind)] += v
            pcost[c["proj"]] += v
            if kind == "sub":
                psub[c["proj"]] += v
            if item == "renew":
                prenew[c["proj"]] += v
        for item, t_, v in (("w5", c["w5"], c["w5"] * W_W5), ("w1", c["w1"], c["w1"] * W_W1)):
            btok[item] += t_
            bcost[item] += v
        if kind == "sub":
            sub_write5 += c["w5"]
        ctx_sum[kind] += c["rd"] + c["w5"] + c["w1"]
        n_calls[kind] += 1
        prev = c

ITEMS = [
    ("read", "Čtení z cache: kontext poslaný znovu, levné"),
    ("new", "Zápis nového kontextu do cache"),
    ("renew", "**Obnova vypršelé cache: kontext zaplacený znovu**"),
    ("inp", "Vstup bez cache"),
    ("out", "Výstup modelu"),
]
T = sum(cost.values())


def p(x):
    return f"{x / T * 100:.1f} %".replace(".", ",") if T else "0 %"


def n(x):
    if x >= 1e9:
        return f"{x / 1e9:.1f} mld.".replace(".", ",", 1)
    if x >= 1e6:
        return f"{x / 1e6:.0f} mil."
    return f"{x / 1e3:.0f} tis." if x >= 10000 else f"{x:,.0f}".replace(",", " ")


renew = cost[("renew", "main")] + cost[("renew", "sub")]
sub_share = sum(cost[(i, "sub")] for i, _ in ITEMS)
total_calls = n_calls["main"] + n_calls["sub"]

print(f"## {LABEL_PREFIX} {LABEL}" + (f", projekt {ARGS_PROJECT}" if PROJECT else "") + "\n")
print(f"**Obnova vypršelé cache: {p(renew)} útraty** "
      f"(hlavní agent {p(cost[('renew', 'main')])}, subagenti {p(cost[('renew', 'sub')])}).\n")
print(f"Hlavních sessions {n_streams['main']}, subagentů {n_streams['sub']}, "
      f"volání modelu {total_calls:,}".replace(",", " ").replace("  ", ", ") + f". Subagenti udělali {p(sub_share)} útraty.\n")

TT = sum(tok.values())
pt = lambda x: f"{x / TT * 100:.1f} %".replace(".", ",") if TT else "0 %"
both = lambda d, item: d[(item, "main")] + d[(item, "sub")]

print("### 1. Spotřeba tokenů: za co platíš Anthropicu\n")
print("| Položka účtu | Cena za token (vstup = 1) | Tokenů | Podíl na tokenech | Podíl na útratě |")
print("|---|---|---|---|---|")
for label, price, t_, c_ in (
    ("Vstup bez cache", "1", both(tok, "inp"), both(cost, "inp")),
    ("Zápis do cache, 5 min", "1,25", btok["w5"], bcost["w5"]),
    ("Zápis do cache, 1 h", "2", btok["w1"], bcost["w1"]),
    ("Čtení z cache", "0,05 Opus / 0,1 ostatní", both(tok, "read"), both(cost, "read")),
    ("Výstup modelu (včetně thinking)", "5", both(tok, "out"), both(cost, "out")),
):
    print(f"| {label} | {price} | {n(t_)} | {pt(t_)} | {p(c_)} |")
print(f"| **Celkem** | | **{n(TT)}** | **100 %** | **100 %** |\n")
out_t = both(tok, "out")
print(f"Průměrné volání modelu: vstup {n((TT - out_t) / max(1, total_calls))} tokenů, "
      f"výstup {n(out_t / max(1, total_calls))}. Model dostane při každém volání celou dosavadní "
      "konverzaci znovu, ale vypíše jen novou odpověď. Proto je vstupu o dva řády víc.\n")
if fixed["n"]:
    vl = sorted({k[1] for k in fixed if isinstance(k, tuple) and k[1]},
                key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")])
    vers = (vl[0] if len(vl) == 1 else f"{vl[0]} až {vl[-1]}") if vl else "?"
    ratio = f"{TOK_PER_CHAR:.2f}".replace(".", ",")
    print(f"**Pozor:** u {n(fixed['n'])} z {n(total_calls)} volání zapsal Claude Code (verze {vers}) "
          f"jen počet výstupních tokenů ze začátku odpovědi. Výstup je u nich odhadnutý z délky obsahu "
          f"({ratio} tokenu na znak podle správně zapsaných volání, včetně skrytého thinking), "
          f"doplněno {n(fixed['added'])} tokenů.\n")

print("### 2. Rozpad cache\n")
print("| Co se s cache dělo | Tokenů | Podíl na útratě | z toho hlavní agent | z toho subagenti |")
print("|---|---|---|---|---|")
cache_items = [
    ("read", "Čtení z cache: kontext poslaný znovu (levné, žádoucí)"),
    ("new", "Zápis nového kontextu do cache (nevyhnutelný)"),
    ("renew", "**Obnova vypršelé cache: kontext zaplacený znovu (ztráta)**"),
]
for item, label in cache_items:
    print(f"| {label} | {n(both(tok, item))} | {p(both(cost, item))} | "
          f"{p(cost[(item, 'main')])} | {p(cost[(item, 'sub')])} |")
ct = sum(both(tok, i) for i, _ in cache_items)
cc_ = sum(both(cost, i) for i, _ in cache_items)
print(f"| **Cache celkem** | {n(ct)} | {p(cc_)} | "
      f"{p(sum(cost[(i, 'main')] for i, _ in cache_items))} | {p(sum(cost[(i, 'sub')] for i, _ in cache_items))} |\n")
print("Procenta jsou z celé útraty v tabulce 1. Zápis nového kontextu a obnova dohromady "
      "dávají oba řádky zápisu do cache z tabulky 1.\n")

if len(pcost) > 1:
    print("### 3. Podle projektů\n")
    print("| Projekt | Podíl na útratě | z toho subagenti | Obnova cache, podíl na celé útratě | Obnova cache v rámci projektu |")
    print("|---|---|---|---|---|")
    top = pcost.most_common(10)
    for pr, v in top:
        inner = f"{prenew[pr] / v * 100:.1f} %".replace(".", ",") if v else "0 %"
        print(f"| {pr} | {p(v)} | {p(psub[pr])} | {p(prenew[pr])} | {inner} |")
    rest = T - sum(v for _, v in top)
    if len(pcost) > len(top):
        print(f"| ostatní ({len(pcost) - len(top)}) | {p(rest)} | | | |")
    print()

print("### Výpadky cache\n")
ms, mm = miss["sub"], miss["main"]
avg = lambda xs: n(sum(x[0] for x in xs) / len(xs)) if xs else "0"
print(f"- Subagenti: {len(ms)}× cache vypršela (pauza nad 5 min)" + (f", pokaždé znovu zapsáno průměrně {avg(ms)} tokenů." if ms else "."))
mb = collections.Counter("1–2 h" if g < 7200 else "2–8 h" if g < 28800 else "nad 8 h" for _, g, _ in mm)
print(f"- Hlavní agent: {len(mm)}× cache vypršela (pauza nad 1 h)" + (f", průměrně {avg(mm)} tokenů; "
      f"po pauze 1–2 h {mb['1–2 h']}×, 2–8 h {mb['2–8 h']}×, nad 8 h {mb['nad 8 h']}×." if mm else "."))
print(f"- Mezery mezi voláními subagenta: " + ", ".join(f"{k} {gaps[k]}×" for k in ("< 1 min", "1–5 min", "5–60 min", "> 1 h")))
surcharge = sub_write5 * (W_W1 - W_W5)
bal = sim_save - surcharge
verdict = "vyplatila by se" if bal > 0 else "nevyplatila by se"
print(f"- Hodinová cache pro subagenty (`subagentPromptCacheTtl: \"1h\"`): ušetřila by {p(sim_save)}, "
      f"přirážka za dražší zápis {p(surcharge)}, celkem {'+' if bal > 0 else '−'}{p(abs(bal))} → {verdict}.")
print(f"- Průměrný kontext na volání: hlavní agent {n(ctx_sum['main'] / max(1, n_calls['main']))}, "
      f"subagent {n(ctx_sum['sub'] / max(1, n_calls['sub']))} tokenů.")
