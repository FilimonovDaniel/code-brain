# code-brain

Automatisches Memory für Claude Code in einem zentralen Obsidian-Vault - für Projekte und für Themen ohne Projekt.

- **Kein Befehl nötig:** Beim Session-Start erkennt code-brain das Projekt am Ordner (Pfad, sonst Git-Remote) und lädt Arbeitsregeln, Projektsteckbrief mit Projektregeln und Verworfenem, Arbeitsstand, offene Arbeitspakete und die Wissensthemen - fest begrenzt, egal wie groß das Projekt ist.
- **Jede Information an genau einem Ort:** `Projekt.md` (was dauerhaft gilt), `Arbeitsstand.md` (was läuft und kommt), `arbeitspakete/` (je Feature/Bug alles inkl. Ergebnis), `wissen/` (Nachschlagen, auch global).
- **Erinnert und sperrt, wo nötig:** Nach einer neuen Task-Liste sind Bash/PowerShell, Edit/Write, Agenten und neue Sessions gesperrt, bis `vault.py search` gelaufen ist. Sonst Hinweise beim Aufgabenende (Abschluss), nach 8 Nachrichten ohne Eintrag, nach Abbruch und nach Komprimierung.
- **Kein Archiv:** Unwichtiges wird gelöscht, die Git-Historie des Vaults behält alles.
- **Mehrere Geräte:** Der Vault ist ein Git-Repo. Bei jedem Session-Start (auch fortgesetzt) wird gepullt und gemeldet, was andere Geräte neu eingetragen haben; laufende Sessions holen alle 10 Minuten nach, nach jeder Antwort mit Änderungen committet und gepusht, mit Schutz vor Zugangsdaten.
- **Schlank:** nur Python-Standardbibliothek und Git, kein MCP, keine Datenbank.

## Installation

```
claude plugin marketplace add FilimonovDaniel/code-brain     # oder lokaler Pfad zu diesem Ordner
claude plugin install code-brain@code-brain --scope user
```

Das Repo ist privat: Git braucht Zugriff darauf (z. B. über `gh auth login` mit dem passenden Konto).

In der nächsten Session meldet code-brain, dass es noch nicht eingerichtet ist, und Claude fragt nach Memory-Repo und Vault-Ordner (siehe `skills/code-brain/references/einrichtung.md`).

## Aufbau

```
.claude-plugin/          plugin.json, marketplace.json
hooks/hooks.json         SessionStart, UserPromptSubmit, PreToolUse (wrap_up), PostToolUse, Stop, PreCompact, SessionEnd
hooks/run.sh             findet ein lauffähiges Python (Windows-Store-Platzhalter wird übersprungen)
skills/code-brain/
  SKILL.md               Arbeitsregeln für Claude
  references/            Notizformate, Einrichtung
  assets/templates/      Vorlagen für neue Projekte und den Vault
  scripts/vault.py       alles Ausführbare: init, new, link, context, search, check, sync, hooks
```

## Vault

```
global/Arbeitsregeln.md  global/wissen/<Thema>.md
projekte/<slug>/
  Projekt.md  Arbeitsstand.md  arbeitspakete/  wissen/
```

## Hooks

| Ereignis | Wirkung |
|---|---|
| `SessionStart` | Pull (jeder Start) mit Meldung neuer Einträge anderer Geräte, Startkontext; nach Komprimierung Hinweis zum Nachtragen; meldet nicht abgeschlossene Aufgaben früherer Sessions |
| `UserPromptSubmit` | Pull alle 10 Min. (nach gescheitertem Start-Pull bei jeder Nachricht), Hinweis auf neue Einträge anderer Geräte; Hinweis nach 8 Nachrichten ohne Vault-Eintrag, nach abgebrochener Antwort, bei gestopptem Commit |
| `PostToolUse` | Formatprüfung geschriebener Notizen; Task-Liste angelegt → Hinweis Aufgabenstart; alle Todos erledigt → Hinweis Aufgabenende |
| `PreToolUse` (Arbeitswerkzeuge) | offene Aufgabe ohne `vault.py search` → Bash/PowerShell, Edit/Write, Agent, Workflow, neue Session gesperrt; Lesen bleibt frei |
| `PreToolUse` (`mcp__ccd_turn__wrap_up`) | Abschluss ohne aktualisierten Arbeitsstand → einmal zurückgehalten mit Hinweis |
| `Stop`, `PreCompact`, `SessionEnd` | Commit + Push des Vaults, blockieren nie |

## Voraussetzungen

Python 3.9+, Git, unter Windows Git for Windows (Git Bash für die Hooks).
