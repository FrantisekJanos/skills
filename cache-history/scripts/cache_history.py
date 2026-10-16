"""Hospodaření s prompt cache v Claude Code podle lokálních přepisů (~/.claude/projects).

Použití: python -I cache_history.py [období]   (výchozí 30 = dní; "6h" = hodin)
Čte jen lokální soubory, nic neposílá. Vypíše dvě tabulky v Markdownu:
  1. přehled spotřeby (váha čtení z cache 0,1 = Sonnet/Haiku),
  2. podíl obnovy cache na spotřebě (váha čtení 0,05 = Opus 5.x).
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
# Váhy vůči ceně neuloženého vstupu: zápis 5min cache 1,25, 1h cache 2, výstup 5.
BASE = dict(inp=1.0, w5=1.25, w1=2.0, out=5.0)
MIN_CTX = 20000  # menší kontext se za výpadek nepočítá


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
                              out=u.get("output_tokens", 0) or 0)
    if calls:
        streams.append((kind, sorted(calls.values(), key=lambda c: c["t"])))

if not streams:
    print(f"V {ROOT} nejsou za posledních {LABEL} žádné přepisy s údaji o tokenech.")
    sys.exit(0)

raw = collections.Counter()          # (kind, pole) -> tokeny
miss = {"main": [], "sub": []}        # (přepsáno, zápis 1h?, mezera s)
sub_w5_total = 0
n_streams = collections.Counter()
for kind, calls in streams:
    n_streams[kind] += 1
    prev = None
    for c in calls:
        for k in ("inp", "w5", "w1", "rd", "out"):
            raw[(kind, k)] += c[k]
        if kind == "sub":
            sub_w5_total += c["w5"]
        if prev is not None:
            gap = (c["t"] - prev["t"]).total_seconds()
            prev_ctx = prev["rd"] + prev["w5"] + prev["w1"] + prev["inp"]
            ttl = 3600 if prev["w1"] > prev["w5"] else 300
            rewritten = min(c["w5"] + c["w1"], prev_ctx)
            if gap > ttl and prev_ctx > MIN_CTX and c["rd"] < 0.5 * prev_ctx and rewritten > 0.5 * prev_ctx:
                miss[kind].append((rewritten, c["w1"] > c["w5"], gap))
        prev = c


def evaluate(rd_w):
    W = dict(BASE, rd=rd_w)
    tot = {k: sum(raw[(k, f)] * W[f] for f in W) for k in ("main", "sub")}
    T = tot["main"] + tot["sub"]
    loss = {k: sum(r * ((W["w1"] if one_h else W["w5"]) - rd_w) for r, one_h, _ in miss[k])
            for k in ("main", "sub")}
    read = (raw[("main", "rd")] + raw[("sub", "rd")]) * rd_w
    sim_save = sum(r * (W["w5"] - rd_w) for r, _, gap in miss["sub"] if gap <= 3600)
    surcharge = sub_w5_total * (W["w1"] - W["w5"])
    return dict(T=T, tot=tot, loss=loss, read=read, sim=sim_save - surcharge,
                sim_save=sim_save, surcharge=surcharge)


def mil(x):
    return f"{x / 1e6:,.0f} M".replace(",", " ")


def pct(x, T):
    return f"{x / T * 100:.1f} %".replace(".", ",")


def avg_k(k):
    return f"{sum(r for r, _, _ in miss[k]) / max(1, len(miss[k])) / 1e3:.0f} tis."


a = evaluate(0.1)
b = evaluate(0.05)
reads = raw[("main", "rd")] + raw[("sub", "rd")]
n_calls = sum(len(c) for _, c in streams)

print(f"## Hospodaření s cache za posledních {LABEL}\n")
print(f"Hlavních sessions {n_streams['main']}, subagentů {n_streams['sub']}, "
      f"volání modelu {str(n_calls) if n_calls < 1000 else f'{n_calls:,}'.replace(',', ' ')}.")
print("Hodnoty v M jsou cena, ne počet tokenů: M = cena milionu tokenů běžného (neuloženého) vstupu.")
print("Čtení z cache stojí 0,05–0,1 této ceny. Vysoký podíl čtení je dobrý stav, kontext se neplatil znovu.\n")

print("### 1. Přehled (váha čtení z cache 0,1)\n")
print("| Co | Hodnota | Podíl |\n|---|---|---|")
print(f"| Celkem | {mil(a['T'])} | 100 % |")
print(f"| Subagenti celkem | {mil(a['tot']['sub'])} | {pct(a['tot']['sub'], a['T'])} |")
print(f"| Čtení z cache (vše) | přečteno {mil(reads)} tokenů, cena {mil(a['read'])} | {pct(a['read'], a['T'])} |")
print(f"| Výpadek cache subagenta (pauza > 5 min) | {len(miss['sub'])}×, {mil(a['loss']['sub'])}, "
      f"průměrně {avg_k('sub')} přepsáno | {pct(a['loss']['sub'], a['T'])} |")
print(f"| Výpadek cache hlavní session (pauza > TTL) | {len(miss['main'])}×, {mil(a['loss']['main'])}, "
      f"průměrně {avg_k('main')} | {pct(a['loss']['main'], a['T'])} |")
print(f"| Simulace `subagentPromptCacheTtl: \"1h\"` | úspora {mil(a['sim_save'])}, "
      f"přirážka {mil(a['surcharge'])} | {'+' if a['sim'] >= 0 else '−'}{pct(abs(a['sim']), a['T'])} |")

print("\n### 2. Kolik spotřeby padlo na obnovu cache (váha čtení 0,05)\n")
print("| Co | Hodnota | Podíl ze spotřeby |\n|---|---|---|")
print(f"| Celkem | {mil(b['T'])} | 100 % |")
print(f"| Obnova cache subagentů | {mil(b['loss']['sub'])} | {pct(b['loss']['sub'], b['T'])} |")
print(f"| Obnova cache hlavního agenta | {mil(b['loss']['main'])} | {pct(b['loss']['main'], b['T'])} |")
both = b['loss']['sub'] + b['loss']['main']
print(f"| **Obnova cache dohromady** | {mil(both)} | **{pct(both, b['T'])}** |")
print(f"| Čtení z cache (levné, žádoucí) | {mil(b['read'])} | {pct(b['read'], b['T'])} |")
print(f"| Simulace 1h TTL pro subagenty | bilance | {'+' if b['sim'] >= 0 else '−'}{pct(abs(b['sim']), b['T'])} |")

gaps = collections.Counter()
for kind, calls in streams:
    if kind != "sub":
        continue
    for p, c in zip(calls, calls[1:]):
        g = (c["t"] - p["t"]).total_seconds()
        gaps["< 1 min" if g < 60 else "1–5 min" if g < 300 else "5–60 min" if g < 3600 else "> 1 h"] += 1
mb = collections.Counter()
for _, _, g in miss["main"]:
    mb["1–2 h" if g < 7200 else "2–8 h" if g < 28800 else "> 8 h"] += 1
ctx = {k: sum(c["rd"] + c["w5"] + c["w1"] for kk, cs in streams if kk == k for c in cs)
          / max(1, sum(len(cs) for kk, cs in streams if kk == k)) for k in ("main", "sub")}
print("\n### Podklady\n")
print("- Mezery mezi voláními subagenta: " + ", ".join(f"{k} {gaps[k]}×" for k in ("< 1 min", "1–5 min", "5–60 min", "> 1 h")))
print("- Výpadky hlavní session podle pauzy: " + ", ".join(f"{k} {mb[k]}×" for k in ("1–2 h", "2–8 h", "> 8 h")))
print(f"- Průměrný kontext na volání: hlavní {ctx['main'] / 1e3:.0f} tis., subagent {ctx['sub'] / 1e3:.0f} tis.")
