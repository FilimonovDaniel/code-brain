# Notizformate

`Projekt.md`, `Arbeitsstand.md` und `global/Arbeitsregeln.md` entstehen aus `assets/templates/`. Arbeitspakete und Wissen legst du selbst an.

## Arbeitspaket - `arbeitspakete/<nr-name>.md`

Name nach Issue-Nummer und Thema (`74-berichte-detail`), ohne Issue nur nach Thema (`login-umbau`).

```markdown
---
type: arbeitspaket
status: in-arbeit          # geplant | in-arbeit | review | blockiert | fertig | verworfen
summary: Berichte-Detailseite mit Kennzahlen je Buchung
stichworte: [berichte, revive, posthog, kennzahlen]
updated: 2026-10-17
issue: 74
branch: feature/74-berichte
pr: 76
---

# 74 Berichte-Detail

## Ziel
- Was am Ende funktionieren soll, Abnahmekriterien.

## Stand
- Wo es steht (Stand 2026-10-17), was fehlt, wo es liegt (Worktree, Ports).

## Todos
- [ ] Offene Aufgabe
- [x] Erledigte Aufgabe (2026-10-17)

## Festgelegt
- Entscheidungen, die nur dieses Paket betreffen - je Zeile mit Grund (Datum).

## Ergebnis
- Beim Abschluss: was gebaut wurde, wo (Branch, PR, wichtige Dateien), was offen blieb.
```

- `status: fertig` + Ergebnis ausgefüllt: das Paket bleibt stehen und wird über `search` gefunden, auch nach Jahren.
- `verworfen`: Grund in einer Zeile unter Ergebnis; soll der Ansatz nicht wieder vorgeschlagen werden, zusätzlich eine Zeile unter `Projekt.md` › Verworfen. Ist das Paket ohne Wert, löschen.
- Hypothesen und Debug-Notizen gehören unter `## Stand`, solange sie gelten - danach löschen.

## Wissen - `wissen/<Thema>.md` (Projekt) oder `global/wissen/<Thema>.md`

```markdown
---
type: wissen
summary: Worktree-Frontends nur mit --webpack starten (Turbopack bricht bei Symlinks)
stichworte: [worktree, frontend, webpack, turbopack, next]
updated: 2026-10-17
---

# Frontend in Worktrees

> [!warning] Stolperstelle
> `npm run dev` ohne `--webpack` hängt in Worktrees.

- Befehl: `npm run dev -- --webpack -p 3101`
- Ursache: ...
```

Ein Thema pro Notiz, Name = Thema. Neues zum Thema in die vorhandene Notiz, nicht in eine zweite.

## Projektregeln und Verworfen in `Projekt.md`

```markdown
## Projektregeln
- Eigene App (Rust + Next.js + Postgres) statt WordPress-Plugin - unabhängig deploybar (2026-09-14)
- Konten nur per Einladungslink des Kundenservice, 7 Tage gültig (2026-10-12)
- mcos-wp nur lesen, nie ändern - fremdes Repo, Felder als Frage an den Nutzer (2026-10-15)

## Verworfen
- Öffentliche Selbstregistrierung - Konten entstehen per Einladung (2026-10-12)
```

Eine Zeile je Eintrag: Regel bzw. Idee - Grund (Datum). Wird eine Regel gekippt, die Zeile ersetzen.

## Frontmatter

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `type` | ja | `arbeitspaket`, `wissen`, `projekt`, `arbeitsstand`, `arbeitsregeln` |
| `summary` | ja | eine Zeile - steht im Startkontext bzw. in Suchtreffern |
| `updated` | ja | `JJJJ-MM-TT`, bei jeder Änderung auf heute |
| `stichworte` | Arbeitspaket, Wissen | Suchbegriffe, auch Synonyme und Fachwörter |
| `status` | Arbeitspaket | siehe oben |
| `issue`, `branch`, `pr` | wenn vorhanden | Verbindung zu Git und Tracker |

## Callouts und Kommentare

- `> [!warning]` für Stolperstellen, `> [!info]` für Hinweise.
- `%% ... %%` sind Ausfüllhinweise in den Vorlagen und werden nicht geladen.
