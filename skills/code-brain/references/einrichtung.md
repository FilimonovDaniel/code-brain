# Einrichtung von code-brain

Einmal pro Gerät. Danach läuft alles automatisch.

## 1. Zwei Angaben vom Nutzer erfragen

1. **Git-Repo für das Memory** - am besten ein privates, leeres oder schon von einem anderen Gerät befülltes Repo (z. B. `https://github.com/<name>/ClaudeMemory`). Ohne Repo funktioniert code-brain auch, dann nur auf diesem Gerät.
2. **Lokaler Ordner für den Vault** - Vorschlag: `~/Documents/ClaudeMemory` (Windows: `C:\Users\<name>\Documents\ClaudeMemory`). Der Ordner muss leer sein oder schon ein Klon des Repos.

Stelle beide Fragen in einer Nachricht und mach Vorschläge, damit der Nutzer nur bestätigen muss.

## 2. Zugriff prüfen

- `git ls-remote <repo-url>` muss ohne Fehler laufen.
- Bei GitHub ohne Zugriff: Anmeldung mit `gh auth status` prüfen. Gehört das Memory-Repo einem anderen Konto als dem aktiven (z. B. privates Konto neben einem Firmenkonto), **nicht** global umschalten, sondern bei `init` `--gh-user <konto>` angeben: Dann nutzt nur der Vault dieses Konto (repo-lokaler Credential-Helper über `gh auth token --user`), alle anderen Repos bleiben beim aktiven Konto.
- Zugriff mit einem bestimmten Konto prüfen: `GH_TOKEN=$(gh auth token --user <konto>) gh api repos/<owner>/<repo>`.

## 3. Einrichten

```
python "<pfad>/vault.py" init --vault "<ordner>" --remote "<repo-url>" [--gh-user <konto>]
```

Das Skript:
- holt das Repo in den Ordner (auch in einen frisch in Obsidian angelegten Vault, der nur `.obsidian/` enthält) oder legt ein neues Git-Repo an,
- legt `_brain/`, `projekte/`, `global/Arbeitsregeln.md`, `global/wissen/`, `README.md`, `.gitignore` und `.gitattributes` an,
- schreibt `~/.code-brain/config.json` mit dem Vault-Pfad,
- macht den ersten Commit und pusht.

Auf einem zweiten Gerät genauso - das Repo bringt dann alle Projekte schon mit.

## 4. Obsidian

Der Nutzer öffnet den Ordner in Obsidian über "Ordner als Vault öffnen". Ein Plugin ist nicht nötig. Wer auch Änderungen aus Obsidian heraus synchronisieren will, kann das Community-Plugin "Obsidian Git" nutzen. Notwendig ist es nicht, weil code-brain bei jedem Session-Start pullt und nach jedem Update pusht.

## 5. Prüfen

`python "<pfad>/vault.py" status` zeigt Vault, Remote und das für den aktuellen Ordner erkannte Projekt. Ab der nächsten Session lädt der Hook den Kontext automatisch.

## Voraussetzungen

- Python 3.9+ (`python`, `python3` oder `py -3`)
- Git
- unter Windows Git for Windows, weil die Hooks über Git Bash laufen
