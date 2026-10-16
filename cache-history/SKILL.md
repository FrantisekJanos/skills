---
name: cache-history
description: Use when the user asks how well Claude Code uses prompt cache, how many tokens go to rebuilding expired cache (main session vs. subagents), whether a 1-hour cache TTL for subagents would pay off, or why the usage limit runs out fast. Czech triggers include "jak hospodařím s cache", "výpadky cache", "kolik stojí obnova cache", "spotřeba tokenů subagentů", "vyplatí se 1h cache", "proč mi dochází limit".
---

# Hospodaření s prompt cache

## Přehled

Skript `scripts/cache_history.py` projde lokální přepisy Claude Code (`~/.claude/projects/**/*.jsonl`, včetně subagentů) a spočítá, kolik spotřeby padlo na obnovu cache. Data neopouštějí počítač. Výsledkem jsou dvě tabulky v Markdownu, které ukážeš uživateli beze změny.

## Postup

1. Spusť skript (podle objemu přepisů běží sekundy až několik minut; v Claude Code nastav `timeout` 600000):
   ```
   python -I "<adresář tohoto skillu>/scripts/cache_history.py" 30
   ```
   Číslo je počet dní zpět, výchozí 30. Období v hodinách se zadá s `h`, např. `6h`. Na Windows může být `python` místo `python3` a naopak.
2. Výstup ukaž celý, obě tabulky i podklady.
3. Zjisti, jestli je v `~/.claude/settings.json` nastavené `subagentPromptCacheTtl`.
4. Pod tabulky napiš výklad podle sekce Jak číst výsledky: 2–4 věty o tom, co z čísel plyne pro tohoto uživatele.

## Co tabulky znamenají

Ceny jsou přepočtené na cenu neuloženého vstupu: zápis do 5min cache 1,25, do hodinové 2, výstup 5. Čtení z cache stojí u Opus 5.x 0,05, u Sonnet a Haiku 0,1. Proto dvě tabulky:

- **Tabulka 1** (čtení 0,1): přehled spotřeby. Podíl subagentů, kolik tvoří čtení z cache, výpadky cache a simulace hodinové cache pro subagenty.
- **Tabulka 2** (čtení 0,05, odpovídá Opusu): kolik procent spotřeby padlo na obnovu cache, zvlášť hlavní agent, subagenti a dohromady.

**Výpadek cache** = mezi dvěma voláními modelu uplynulo víc než TTL (subagent 5 min, hlavní session obvykle 1 h), kontext měl přes 20 tis. tokenů a víc než polovina se musela zapsat znovu. Ztráta = přepsané tokeny × (cena zápisu − cena čtení).

Procenta v tabulkách se liší, protože každá má jiný celek (jmenovatel). Prahy níže platí pro tabulku 2, jen podíl subagentů se bere z tabulky 1.

**Simulace 1h** porovná úsporu na výpadcích subagentů s pauzou 5–60 min s přirážkou za dražší zápis (2 místo 1,25) u všech zápisů subagentů. Záporné číslo znamená, že by se nastavení `subagentPromptCacheTtl: "1h"` nevyplatilo.

## Jak číst výsledky

| Zjištění | Co doporučit |
|---|---|
| Obnova cache dohromady (tab. 2) pod 5 % | Cache funguje dobře, nic neměnit. |
| Výpadky hlavní session (Podklady) hlavně po pauzách 2 h a víc | Po delší pauze nepokračovat ve staré session: uložit stav do poznámky v projektu a začít `/clear`. Každý návrat platí zápis celého kontextu znovu. |
| Obnova cache subagentů (tab. 2) nad 3 % | Subagenti čekají na dlouhé příkazy (build, testy) déle než 5 min. Dlouhé příkazy pouštět na pozadí a kontrolovat do 4 minut, nebo dát subagentům užší zadání. |
| Simulace 1h (tab. 2) kladná | Zvážit `subagentPromptCacheTtl: "1h"` v `settings.json`. Při záporné bilanci nenastavovat. |
| Průměrný kontext na volání (Podklady) přes 200 tis. | Hlavní páka je velikost kontextu: každý tah čte celý kontext znovu. Častěji `/clear`, menší úkoly pro subagenty. |
| Subagenti celkem (tab. 1) přes 60 % | Rutinní subagenty pouštět na levnějším modelu (`sonnet`). |

## Známá omezení

- Váhy odpovídají cenám API. Jak se spotřeba promítá do limitu předplatného (Pro, Max, Team), Anthropic nezveřejňuje; poměry jsou odhad.
- Skript nerozlišuje model u jednotlivých volání, proto dvě tabulky s pevnou vahou čtení.
- Do výpadku se nepočítá ztráta z příliš velkého kontextu, jen přepis cache po vypršení.
- Přepisy starší, než drží Claude Code (výchozí `cleanupPeriodDays` 30), už na disku nejsou.
