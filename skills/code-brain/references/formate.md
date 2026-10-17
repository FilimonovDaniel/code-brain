# Notizformate

Vorlagen für die Notizen, die du selbst anlegst. `Stand.md`, `Plan.md`, `Todos.md` und der Projekt-Hub entstehen bei `vault.py new` aus `assets/templates/`.

## Entscheidung - `entscheidungen/JJJJ-MM-TT <Titel>.md`

```markdown
---
type: entscheidung
status: aktiv            # aktiv | ersetzt | verworfen
summary: Cache über Postgres statt Redis - eine Infrastruktur weniger
updated: 2026-10-17
datum: 2026-10-17
ersetzt: "[[2026-08-02 Redis als Cache]]"     # optional
ersetzt_durch:                                 # gesetzt, sobald überholt
tags:
  - projekt/<slug>
  - entscheidung
  - bereich/cache
---

# Cache über Postgres statt Redis

## Kontext
- Warum stand die Frage an? Fakten, Zwänge.

## Entscheidung
- Was gilt ab jetzt - eindeutig formuliert.

## Begründung
- Warum diese Option.

## Verworfene Alternativen
- Redis: zusätzlicher Dienst, Betrieb auf der VM aufwendig.

## Folgen
- Was sich dadurch ändert, was zu beachten ist.
```

## Feature - `features/<branch-oder-thema>.md`

```markdown
---
type: feature
status: in-arbeit          # geplant | in-arbeit | review | blockiert | fertig | verworfen
summary: Berichte-Detailseite mit Kennzahlen pro Kampagne
updated: 2026-10-17
branch: feature/74-berichte-detail
issue: 74
pr: 76
tags:
  - projekt/<slug>
  - feature
---

# Berichte-Detail

## Ziel
- Was am Ende funktionieren soll, Abnahmekriterien.

## Stand
- Wo das Feature gerade steht (Stand 2026-10-17), was fehlt.

## Todos
- [ ] Offene Aufgabe
- [x] Erledigte Aufgabe (2026-10-17)

## Entscheidungen
- [[2026-10-17 Kennzahlen aus PostHog|Kennzahlen aus PostHog]]
- Kleine Festlegung direkt hier (eine Zeile).

## Scratchpad
- Zwischengedanken, Hypothesen, Notizen beim Debuggen. Wird geleert, wenn das Feature fertig ist.
```

## Wissen - `wissen/<Thema>.md`

```markdown
---
type: wissen
summary: Worktree-Frontends nur mit --webpack starten (Turbopack bricht bei Symlinks)
updated: 2026-10-17
tags:
  - projekt/<slug>
  - wissen
  - bereich/frontend
---

# Frontend in Worktrees starten

> [!warning] Stolperstelle
> `npm run dev` ohne `--webpack` hängt in Worktrees.

- Befehl: `npm run dev -- --webpack -p 3101`
- Ursache: ...
```

Ein Thema pro Notiz. Kommt Neues zu einem bestehenden Thema hinzu, die vorhandene Notiz erweitern statt eine zweite anzulegen.

## Session-Verlauf - `sessions/JJJJ-MM-TT.md`

```markdown
---
type: session
summary: Verlauf 2026-10-17
---

# 2026-10-17

- 10:12 Berichte-Detail: API-Endpunkt fertig, Tests grün -> [[features/74-berichte-detail|74]]
- 11:40 Entscheidung: Kennzahlen aus PostHog statt eigener Tabelle -> [[2026-10-17 Kennzahlen aus PostHog]]
- 15:05 PR #76 offen, wartet auf manuellen Test
```

Kurz, eine Zeile pro Schritt mit Uhrzeit, Links auf die Notizen mit den Details. Mehrere Sessions am selben Tag schreiben in dieselbe Datei.

## Callouts und Kommentare

- `> [!warning]` für Stolperstellen, `> [!info]` für Hinweise, `> [!todo]` für Wichtiges, das ansteht.
- `%% ... %%` sind Ausfüllhinweise in den Vorlagen. Sie werden nicht in den Startkontext geladen. Du kannst sie stehen lassen oder entfernen, sobald der Abschnitt gefüllt ist.
