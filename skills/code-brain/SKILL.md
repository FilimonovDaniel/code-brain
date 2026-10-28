---
name: code-brain
description: Automatisches Memory für Coding-Projekte und allgemeine Themen in einem zentralen Obsidian-Vault. Nutze diesen Skill immer, wenn der Startkontext "code-brain" erwähnt, wenn ein Hook "code-brain:" meldet, wenn der Nutzer etwas entscheidet, ändert oder streicht, wenn eine Aufgabe beginnt oder endet, wenn du Projektwissen oder Wissen zu einem Thema festhalten oder nachschlagen willst, wenn ein neues Projekt angelegt oder code-brain eingerichtet werden soll - auch wenn der Nutzer das Memory nicht erwähnt. Der Nutzer gibt nie Befehle dafür; du pflegst das Memory selbstständig.
---

# code-brain

Ziel: Jeder neue Claude-Agent weiß so viel wie seine Vorgänger. Er erbt nur den Vault. Deshalb pflegst du ihn selbstständig, ohne Ankündigung und ohne Rückfrage - der Nutzer soll nie daran erinnern müssen.

## 1. Aufbau - jede Information an genau einem Ort

| Datei | Steht drin | Beim Start geladen |
|---|---|---|
| `global/Arbeitsregeln.md` | Wie Claude mit dem Nutzer arbeitet | ja, ganz |
| `global/wissen/<Thema>.md` | Wissen ohne Projekt (Server, Tools, Recherchen) | nur Name |
| `projekte/<slug>/Projekt.md` | Zweck, Technik, **Projektregeln** (was dauerhaft gilt, auch was nie getan wird; je Zeile: Regel - Grund (Datum)), **Verworfen** (Ideen, die bewusst nicht gemacht werden) | ja, ganz |
| `projekte/<slug>/Arbeitsstand.md` | In Arbeit · Nächste Schritte · Offene Fragen · Später · Zuletzt abgeschlossen (max. 5) | ja, ganz |
| `projekte/<slug>/arbeitspakete/<nr-name>.md` | ein Feature/Bug/Issue: Ziel · Stand · Todos · Festgelegt · Ergebnis | offene: Name + Status + summary |
| `projekte/<slug>/wissen/<Thema>.md` | Befehle, Stolperstellen, Architektur | nur Name |

Formate und Beispiele: `references/formate.md`.

## 2. Was wohin

- Entscheidung gilt fürs ganze Projekt und dauerhaft → **Projektregel** in `Projekt.md`.
- Entscheidung gilt nur für ein Feature → **Festgelegt** im Arbeitspaket.
- Idee oder Ansatz wird gestrichen und soll nicht wieder vorgeschlagen werden → **Verworfen** in `Projekt.md`. Sonst löschen.
- Todo zu einem Arbeitspaket → nur im Arbeitspaket. Todo ohne Paket → `Arbeitsstand` › Nächste Schritte.
- Wie etwas funktioniert → `wissen/`. Ohne Projektbezug → `global/wissen/` (auch aus Sessions außerhalb eines Projekts).
- Nie doppelt: `Arbeitsstand` verlinkt aufs Paket, wiederholt es nicht.

## 3. Wann du schreibst

1. **Der Nutzer entscheidet, ändert oder streicht etwas** ("machen wir so", "out of scope", "doch nicht", "später") → sofort an den Ort aus Abschnitt 2.
2. **Aufgabenstart** → erst `python "<vault.py>" search "<begriffe der aufgabe>"`, Treffer lesen (Arbeitspaket, Wissen, Projektregeln). Dann Arbeitspaket anlegen oder aktualisieren (Ziel, Todos), `Arbeitsstand` › In Arbeit.
3. **Während der Arbeit** → Todos abhaken, Festgelegt ergänzen, neues Wissen eintragen. Jeder Eintrag so, als wäre er die letzte Übergabe.
4. **Aufgabenende** → Paket › Ergebnis (was gebaut, wo: Branch/PR/Dateien, was offen) und Status. `Arbeitsstand`: aus In Arbeit raus, in Zuletzt abgeschlossen rein, Nächste Schritte neu.
5. **Hinweis vom Hook** (`code-brain: ...`) → prüfen und nachtragen. Ist nichts offen, nichts tun und nichts dazu sagen.

Nicht dokumentieren: Smalltalk, Wissensfragen ohne Wert für später, Zwischenschritte ohne Folgen, was im Code oder Git-Log selbsterklärend steht.

## 4. Löschen statt Archiv

- Überholte Projektregel → durch die neue ersetzen.
- Fertiges Arbeitspaket → bleibt mit Ergebnis, damit es auch nach Jahren gefunden wird. Löschen erst, wenn es nichts mehr erklärt (z. B. Funktion wieder ausgebaut).
- `Arbeitsstand` beschreibt nur das Jetzt; was erledigt oder überholt ist, fliegt raus.
- Alles Gelöschte bleibt in der Git-Historie des Vaults.

## 5. Format

- Frontmatter: `type`, `summary` (eine Zeile), `stichworte` (Suchbegriffe, bei Paketen und Wissen Pflicht), `updated: JJJJ-MM-TT`. Pakete zusätzlich `status`, `issue`, `branch`, `pr`.
- Stichpunkte, keine Prosa, jede Notiz für sich verständlich. Flüchtige Fakten datieren ("CI grün, Stand 2026-10-19").
- Links mit Pfad: `[[projekte/<slug>/arbeitspakete/74-berichte-detail|#74]]`.
- Nichts erfinden, Unbekanntes als `TBD`. Keine Zugangsdaten, nur Verweis ("Key in `Backend/.env`") - sonst stoppt der Commit.
- `Projekt.md` und `Arbeitsstand.md` unter 2.800 Zeichen, `Arbeitsregeln.md` unter 2.200: Was darüber liegt, wird beim Start abgeschnitten. `check` meldet es.

## 6. Was automatisch passiert

| Wann | Was |
|---|---|
| Session-Start (neu, fortgesetzt, `/clear`, nach Komprimierung) | Pull aus dem Git-Remote, meldet im Kontext "Vault-Sync": aktuell oder welche Commits/Notizen anderer Geräte neu sind; Projekt am Ordner erkennen, Spalte "ja" aus Abschnitt 1 laden; meldet eine nicht abgeschlossene Aufgabe einer früheren Session |
| nach Komprimierung (Gespräch zu lang, Claude Code fasst es zusammen) | lädt neu + Hinweis, fehlenden Stand nachzutragen |
| Task-Liste angelegt | Hinweis Aufgabenstart (3.2); Bash/PowerShell, Edit/Write, Agent, neue Sessions gesperrt bis `search` gelaufen ist (Lesen bleibt frei) |
| Abschluss (`wrap_up`) oder alle Todos erledigt | Hinweis Aufgabenende (3.4) - nur wenn `Arbeitsstand.md` seit dem Start unverändert ist |
| Nachricht des Nutzers, alle 10 Min. | Pull nachholen; neue Einträge anderer Geräte → Hinweis, betroffene Notizen neu lesen. Start-Pull gescheitert (offline) → Warnung im Kontext, neuer Versuch bei jeder Nachricht |
| Nachricht des Nutzers | Hinweis nach 8 Nachrichten ohne Eintrag oder nach einer abgebrochenen Antwort |
| Notiz geschrieben | Formatprüfung (Frontmatter, Stichworte, Zugangsdaten) |
| nach jeder Antwort, vor Komprimierung, Session-Ende | Commit + Push des Vaults, blockiert nie |

Der Startkontext ist schon da - lies `Projekt.md` und `Arbeitsstand.md` nicht noch einmal. Ausnahme: Ein Hinweis meldet neue Einträge anderer Geräte oder einen gescheiterten Sync - dann die genannten Notizen neu lesen; bei gescheitertem Sync dem Nutzer einmal sagen, dass Einträge anderer Geräte fehlen können. Pfad zu `vault.py` und zum Projektordner nennt der Startkontext.

## 7. Nachschlagen

1. Startkontext (Regeln, Projekt, Arbeitsstand, offene Pakete, Wissensthemen).
2. Passendes Paket oder Wissensthema öffnen.
3. Sonst `search "<begriffe>"` (aktuelles Projekt + global; `--all` alle Projekte). Bevor du sagst, etwas sei nicht dokumentiert: suchen.

## 8. Neues Projekt / unbekannter Ordner

- Wird an Code gearbeitet: `python "<vault.py>" new --name "<Projektname>" --cwd "<ordner>"`, dann `Projekt.md` und `Arbeitsstand.md` füllen.
- Gehört der Ordner zu einem bestehenden Projekt (anderer Klon, Worktree-Sammelordner): `link <slug> --cwd "<ordner>"`.
- Thema ohne Projekt: Notiz in `global/wissen/`.

## Einrichtung (einmalig)

Meldet der Startkontext "noch nicht eingerichtet", lies `references/einrichtung.md`.

## Befehle

```
vault.py status                      Konfiguration + erkanntes Projekt
vault.py new --name "<Name>"         Projekt für den aktuellen Ordner anlegen
vault.py link <slug>                 Ordner einem Projekt zuordnen
vault.py context                     Startkontext anzeigen
vault.py search "<text>" [--all]
vault.py check [<slug>|--all]        Format, Stichworte, tote Links, Größe, alte Struktur
vault.py sync pull|push              Git-Sync von Hand
vault.py init --vault <ordner> --remote <git-url>
```
