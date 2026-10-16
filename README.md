# Skills pro Claude Code

Skilly, které používám v Claude Code. Každá složka je jeden skill.

| Skill | K čemu |
|---|---|
| [cache-history](cache-history/) | Ukáže, kolik procent útraty padlo na obnovu vypršelé prompt cache (hlavní agent a subagenti), kam šel zbytek a jestli se vyplatí hodinová cache pro subagenty. Čte jen lokální přepisy, nic neodesílá. |

## Instalace

Zkopíruj složku skillu do `~/.claude/skills/` (na Windows `C:\Users\<jméno>\.claude\skills\`):

```
git clone https://github.com/FrantisekJanos/skills.git
cp -r skills/cache-history ~/.claude/skills/
```

V nové session Claude Code se skill spustí sám podle dotazu, například „Jak hospodařím s prompt cache?“, nebo příkazem `/cache-history`.

Skilly se skripty potřebují Python 3.
