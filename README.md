# code-brain

Automatisches Projekt-Memory für Claude Code in einem zentralen Obsidian-Vault.

- **Kein Befehl nötig:** Beim Session-Start erkennt code-brain das Projekt am Ordner (Git-Remote, sonst Pfad) und lädt Ist-Stand, offene Todos, Plan, Verbote und einen Index aller gültigen Notizen. Ziel sind 2-4k Tokens, egal wie groß das Projekt ist.
- **Laufend aktuell:** Claude dokumentiert während der Arbeit Entscheidungen, verworfene Ansätze, Todos, Plan und Wissen. Jedes Update ist eine vollständige Übergabe. Ändert sich Code ohne Doku, erzwingt der Stop-Hook das Nachtragen.
- **Selbstreinigend:** Ersetzte und verworfene Entscheidungen, fertige Features und alte erledigte Todos wandern automatisch ins Archiv. Dort bleiben sie durchsuchbar, werden aber nicht mehr geladen.
- **Mehrere Geräte:** Der Vault ist ein Git-Repo. Beim Start wird gepullt, nach jedem Update committet und gepusht, mit Schutz vor Zugangsdaten.
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
hooks/hooks.json         SessionStart, Stop, SessionEnd, PostToolUse
hooks/run.sh             findet ein lauffähiges Python (Windows-Store-Platzhalter wird übersprungen)
skills/code-brain/
  SKILL.md               Arbeitsregeln für Claude
  references/            Notizformate, Einrichtung
  assets/templates/      Vorlagen für neue Projekte und den Vault
  scripts/vault.py       alles Ausführbare: init, new, link, context, search, check, sync, hooks
```

## Vault

```
global/Regeln.md                 gilt für alle Projekte
projekte/<slug>/
  <slug>.md  Stand.md  Plan.md  Todos.md  Index.md
  features/  entscheidungen/  wissen/  sessions/  archiv/
```

## Voraussetzungen

Python 3.9+, Git, unter Windows Git for Windows (Git Bash für die Hooks).
