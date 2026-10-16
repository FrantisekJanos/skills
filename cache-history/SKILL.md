---
name: cache-history
description: Use when the user asks how well Claude Code uses prompt cache, how many tokens go to rebuilding expired cache (main session vs. subagents), whether a 1-hour cache TTL for subagents would pay off, or why the usage limit runs out fast. Czech triggers include "jak hospodařím s cache", "výpadky cache", "kolik stojí obnova cache", "spotřeba tokenů subagentů", "vyplatí se 1h cache", "proč mi dochází limit".
---

# Hospodaření s prompt cache

## Přehled

Skript `scripts/cache_history.py` projde lokální přepisy Claude Code (`~/.claude/projects/**/*.jsonl`, včetně subagentů) a spočítá, kolik útraty padlo na obnovu vypršelé cache. Data neopouštějí počítač. Výstup je hotový Markdown, který ukážeš uživateli beze změny.

## Postup

1. Spusť skript (podle objemu přepisů běží sekundy až několik minut; v Claude Code nastav `timeout` 600000):
   ```
   python -I "<adresář tohoto skillu>/scripts/cache_history.py" 30
   ```
   Období: `30` = posledních 30 dní (výchozí), `6h` = posledních 6 hodin, `15.9.-15.10.2026` nebo `2026-09-15..2026-10-15` = od–do včetně koncového dne. Data starší než `cleanupPeriodDays` (výchozí 30 dní) už na disku nejsou; když začátek období sahá dál, řekni to uživateli. Na Windows může být `python` místo `python3` a naopak.
2. Výstup ukaž celý.
3. Zjisti, jestli je v `~/.claude/settings.json` nastavené `subagentPromptCacheTtl`.
4. Pod výstup napiš výklad podle sekce Jak číst výsledky: 2–4 věty o tom, co z čísel plyne pro tohoto uživatele.

## Co výstup znamená

- **První věta** je hlavní výsledek: kolik procent útraty šlo na obnovu vypršelé cache, zvlášť hlavní agent a subagenti.
- **Kam šla útrata**: tabulka všech tokenů (vstup, cache, výstup), jejíž řádky dávají dohromady 100 %. Sloupec *Podíl na tokenech* počítá kusy tokenů, *Podíl na útratě* jejich cenu. Útrata = tokeny × cena podle druhu (vůči běžnému vstupu téhož modelu): čtení z cache 0,05 u Opus, 0,1 u ostatních; zápis do cache 1,25 (5 min) nebo 2 (1 h); výstup 5.
  - *Čtení z cache*: kontext, který model dostává při každém tahu znovu a který byl v cache. Velký podíl je dobrý stav, protože bez cache by stál 10–20× víc.
  - *Zápis nového kontextu*: nové zprávy a výstupy nástrojů. Nevyhnutelné.
  - *Obnova vypršelé cache*: cache vypršela a stejný kontext se zaplatil znovu jako zápis. To je ztráta, kterou jde ovlivnit.
- **Výpadek cache** = mezi dvěma voláními modelu uplynulo víc než TTL (subagent 5 min, hlavní agent obvykle 1 h), kontext měl přes 20 tis. tokenů a víc než polovina se musela zapsat znovu.
- **Hodinová cache pro subagenty** porovná úsporu na výpadcích s pauzou 5–60 min s přirážkou za dražší zápis u všech zápisů subagentů.

## Jak číst výsledky

| Zjištění | Co doporučit |
|---|---|
| Obnova vypršelé cache pod 5 % | Cache funguje dobře, nic neměnit. |
| Výpadky hlavního agenta hlavně po pauzách 2 h a víc | Po delší pauze nepokračovat ve staré session: uložit stav do poznámky v projektu a začít `/clear`. Každý návrat platí zápis celého kontextu znovu. |
| Obnova cache u subagentů nad 3 % | Subagenti čekají na dlouhé příkazy (build, testy) déle než 5 min. Dlouhé příkazy pouštět na pozadí a kontrolovat do 4 minut, nebo dát subagentům užší zadání. |
| Hodinová cache pro subagenty „vyplatila by se“ | Zvážit `subagentPromptCacheTtl: "1h"` v `settings.json`. Jinak nenastavovat. |
| Průměrný kontext na volání přes 200 tis. | Hlavní páka je velikost kontextu: každý tah čte celý kontext znovu. Častěji `/clear`, menší úkoly pro subagenty. |
| Subagenti přes 60 % útraty | Rutinní subagenty pouštět na levnějším modelu (`sonnet`). |

## Známá omezení

- Ceny jsou poměry z ceníku API, procenta tedy platí pro útratu přes API. Jak se spotřeba promítá do limitu předplatného (Pro, Max, Team), Anthropic nezveřejňuje.
- Útrata se počítá vůči ceně vstupu daného modelu; rozdíl cen mezi modely (Opus dražší než Sonnet) v procentech není.
- Do výpadku se nepočítá ztráta z příliš velkého kontextu, jen přepis cache po vypršení.
- Přepisy starší, než drží Claude Code (výchozí `cleanupPeriodDays` 30), už na disku nejsou.
