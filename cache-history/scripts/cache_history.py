"""Hospodaření s prompt cache v Claude Code podle lokálních přepisů (~/.claude/projects).

Použití: python -I cache_history.py [období]   (výchozí 30 = dní; "6h" = hodin)
Čte jen lokální soubory, nic neposílá. Vypíše přehled v Markdownu:
kolik útraty padlo na obnovu vypršelé cache (hlavní agent a subagenti) a kam šel zbytek.
"""
import json, os, sys, glob, collections
from datetime import datetime, timezone, timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.expanduser("~/.claude/projects")
ARG = sys.argv[1].strip().lower() if len(sys.argv) > 1 else "30"
if ARG.endswith("h"):
    SPAN = timedelta(hours=float(ARG[:-1].replace(",", ".")))
    LABEL = f"{ARG[:-1]} h"
else:
    SPAN = timedelta(days=float(ARG.rstrip("d").replace(",", ".")))
    LABEL = f"{ARG.rstrip('d')} dní"
SINCE = datetime.now(timezone.utc) - SPAN

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
            if t < SINCE:
                continue
            cc = u.get("cache_creation") or {}
            w1 = cc.get("ephemeral_1h_input_tokens", 0) or 0
            w5 = cc.get("ephemeral_5m_input_tokens", 0) or 0
            if not cc:
                w5 = u.get("cache_creation_input_tokens", 0) or 0
            key = m.get("id") or d.get("requestId") or d.get("uuid")
            calls[key] = dict(t=t, inp=u.get("input_tokens", 0) or 0, w5=w5, w1=w1,
                              rd=u.get("cache_read_input_tokens", 0) or 0,
                              out=u.get("output_tokens", 0) or 0, model=m.get("model"))
    if calls:
        streams.append((kind, sorted(calls.values(), key=lambda c: c["t"])))

if not streams:
    print(f"V {ROOT} nejsou za posledních {LABEL} žádné přepisy s údaji o tokenech.")
    sys.exit(0)

# Útrata po položkách: cost[(položka, kind)], tokeny tok[(položka, kind)]
cost = collections.Counter()
tok = collections.Counter()
miss = {"main": [], "sub": []}  # (přepsáno, mezera s, zbytečně navíc)
sub_write5 = 0.0                 # cena 5min zápisů subagentů (pro simulaci 1h)
sim_save = 0.0
gaps = collections.Counter()
ctx_sum = collections.Counter()
n_calls = collections.Counter()
n_streams = collections.Counter()

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
    return f"{x / 1e3:.0f} tis." if x >= 500 else f"{x:.0f}"


renew = cost[("renew", "main")] + cost[("renew", "sub")]
sub_share = sum(cost[(i, "sub")] for i, _ in ITEMS)
total_calls = n_calls["main"] + n_calls["sub"]

print(f"## Prompt cache za posledních {LABEL}\n")
print(f"**Obnova vypršelé cache: {p(renew)} útraty** "
      f"(hlavní agent {p(cost[('renew', 'main')])}, subagenti {p(cost[('renew', 'sub')])}).\n")
print(f"Hlavních sessions {n_streams['main']}, subagentů {n_streams['sub']}, "
      f"volání modelu {total_calls:,}".replace(",", " ").replace("  ", ", ") + f". Subagenti udělali {p(sub_share)} útraty.\n")

print("### Kam šla útrata\n")
print("| Položka | Tokenů | Podíl na útratě | z toho hlavní agent | z toho subagenti |")
print("|---|---|---|---|---|")
for item, label in ITEMS:
    t_ = tok[(item, "main")] + tok[(item, "sub")]
    print(f"| {label} | {n(t_)} | {p(cost[(item, 'main')] + cost[(item, 'sub')])} | "
          f"{p(cost[(item, 'main')])} | {p(cost[(item, 'sub')])} |")
print(f"| **Celkem** | | **100 %** | {p(T - sub_share)} | {p(sub_share)} |\n")
print("Útrata = tokeny × cena podle druhu, vůči běžnému vstupu: čtení z cache 0,05 (Opus) "
      "nebo 0,1 (ostatní), zápis do cache 1,25 (5 min) nebo 2 (1 h), výstup 5. "
      "Vysoký podíl čtení z cache je dobrý stav.\n")

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
