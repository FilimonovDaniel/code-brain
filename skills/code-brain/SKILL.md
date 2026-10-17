---
name: code-brain
description: Automatisches Projekt-Memory für Coding-Projekte in einem zentralen Obsidian-Vault. Nutze diesen Skill immer, wenn der Startkontext "code-brain" erwähnt, wenn ein Hook "code-brain:" meldet, wenn du Entscheidungen, Todos, Pläne, Ist-Stand, Feature-Fortschritt oder Projektwissen festhalten oder nachschlagen willst, wenn ein neues Projekt angelegt oder code-brain eingerichtet werden soll - auch wenn der Nutzer das Memory nicht ausdrücklich erwähnt. Der Nutzer gibt nie Befehle dafür; du pflegst das Memory selbstständig.
---

# code-brain - Projekt-Memory im Obsidian-Vault

Die nächste Session weiß nichts von dieser. Das Einzige, was sie erbt, ist der Vault. Deshalb pflegst du ihn selbstständig, laufend und so, dass jede Version eine vollständige Übergabe ist: Der Nutzer kann die Session jederzeit schließen, und der aktuelle Stand ist gesichert. Der Nutzer ruft dafür nie etwas auf und soll auch nicht gefragt werden, ob dokumentiert werden soll.

## Was automatisch passiert (nicht doppelt machen)

| Zeitpunkt | Hook | Wirkung |
|---|---|---|
| Session-Start | `SessionStart` | holt Updates aus dem Git-Remote, erkennt das Projekt am Ordner (Git-Remote, sonst Pfad), mistet aus, erzeugt `Index.md` neu und lädt Stand, Todos, "Als Nächstes", "Nicht tun", Index und letzte Session in deinen Kontext |
| nach jedem Schreiben | `PostToolUse` | prüft Vault-Notizen auf Frontmatter, Tags und Zugangsdaten und meldet Fehler sofort |
| Ende jeder Antwort | `Stop` | Code geändert, Vault nicht -> du musst nachdokumentieren. Vault geändert -> Index neu, Commit + Push |
| Session-Ende | `SessionEnd` | letzter Commit + Push |

Der Startkontext ist also schon da - lies `Stand.md`, `Todos.md` usw. nicht noch einmal, außer du brauchst den vollen Text. Den Pfad zum Skript (`vault.py`) und zum Projektordner nennt der Startkontext.

## Wann du schreibst

Schreib nach jedem Schritt, der für die Zukunft zählt - während der Arbeit, nicht gesammelt am Ende:

- eine Entscheidung ist gefallen oder eine alte wurde gekippt
- ein Ansatz wurde verworfen (das ist genauso wertvoll: verhindert, dass ihn jemand erneut versucht)
- Plan oder Reihenfolge haben sich geändert, neue Todos sind entstanden, Todos sind erledigt
- ein Feature hat den Status gewechselt (begonnen, Review, fertig, PR offen, gemergt)
- du hast etwas gelernt, das man sonst wieder mühsam herausfinden müsste (Stolperstelle, Befehl, Ursache eines Fehlers, Architekturdetail)
- der Ist-Stand hat sich spürbar bewegt

Nicht dokumentieren: Smalltalk, reine Wissensfragen ohne Projektbezug, Zwischenschritte ohne Folgen, Dinge, die schon im Code oder Git-Log selbsterklärend stehen.

Schreib ohne Ankündigung und ohne Rückfrage, und erwähne es höchstens in einem Halbsatz. Dokumentation ist Nebenarbeit, nicht Thema des Gesprächs.

## Wohin was gehört

Alles liegt unter `projekte/<slug>/` (Formate und Vorlagen: `references/formate.md`).

| Inhalt | Ort | Art |
|---|---|---|
| Ist-Stand, woran gerade gearbeitet wird, nächster Schritt, Blocker | `Stand.md` | **komplett überschreiben**, < 40 Zeilen |
| Fahrplan: als Nächstes / später / Ideen / verworfen | `Plan.md` | Abschnitte pflegen |
| Aufgaben ohne eigenes Feature | `Todos.md` | Checkboxen |
| ein Feature oder Branch: Ziel, Stand, Todos, Scratchpad | `features/<name>.md` | eine Notiz pro Feature |
| eine Entscheidung mit Begründung und Alternativen | `entscheidungen/JJJJ-MM-TT <Titel>.md` | eine Notiz pro Entscheidung |
| Stolperstellen, Befehle, Architektur, Konventionen im Detail | `wissen/<Thema>.md` | thematisch, erweitern statt duplizieren |
| kurzer Verlauf (was wurde heute gemacht) | `sessions/JJJJ-MM-TT.md` | anhängen, 1 Zeile pro Schritt |
| Zweck, Stack, Konventionen, **Nicht tun** | `<slug>.md` (Projekt-Hub) | selten ändern |
| projektübergreifende Regeln/Vorlieben des Nutzers | `global/Regeln.md` | sehr kurz halten |

Faustregel für Entscheidungen: Eine eigene Notiz lohnt sich, wenn sie schwer rückgängig zu machen ist, ohne Kontext überraschen würde oder ein echter Zielkonflikt war. Kleinere Festlegungen reichen als Zeile in der Feature-Notiz oder in `Stand.md`.

Jedes Feature bekommt genau eine Notiz, die alles zu seiner Arbeit bündelt: Ziel, aktuellen Stand, Todos und ein Scratchpad.
- Dateiname nach Branch oder Thema, z. B. `features/74-berichte-detail.md`.
- Im Frontmatter die Verknüpfung zu Git und Tracker: `branch:`, `issue:`, `pr:`. So findet eine spätere Session vom Branch zur Notiz und umgekehrt.
- `## Scratchpad` ist für Zwischengedanken beim Arbeiten (Hypothesen, Debug-Notizen, „probiert, ging nicht, weil …“) und darf unordentlich sein. Ist das Feature fertig, räum ihn auf: Wichtiges wandert in eine Entscheidung oder nach `wissen/`, der Rest wird gelöscht.

## Schreibregeln

- **Jede Notiz hat Frontmatter** mit `type`, `summary` (eine Zeile - daraus baut der Index die Übersicht, die beim Start geladen wird) und `updated: JJJJ-MM-TT` (bei jeder Änderung auf heute setzen). Ausnahme: `sessions/`.
- **Tags** als YAML-Liste ohne `#`: `projekt/<slug>`, dazu Art und Thema, z. B. `entscheidung`, `bereich/auth`. Nur Buchstaben, Ziffern, `_ - /`.
- **Verlinken statt wiederholen**: `[[projekte/<slug>/entscheidungen/2026-10-17 Cache über Postgres|Cache über Postgres]]`. Links in Properties in Anführungszeichen: `ersetzt_durch: "[[...]]"`.
- **Flüchtige Fakten datieren** ("CI grün", "PR #76 offen", "3 Tests rot"): `(Stand 2026-10-17)` - oder auf die Quelle verweisen. Undatierte Momentaufnahmen werden sonst still falsch.
- **Nichts erfinden**: Unbekanntes als `TBD` markieren. Bevor du behauptest, etwas sei nicht dokumentiert, im Index nachsehen und `search` nutzen.
- **Keine Zugangsdaten** in den Vault - nur Verweise ("Key liegt in `Backend/.env`"). Der Commit wird sonst gestoppt.
- **Kurz und konkret**: Stichpunkte statt Prosa, jede Notiz für sich verständlich (kein "siehe oben"), deutsch.
- Wiederhole nicht, was im Code steht - halte fest, *warum* etwas so ist und was als Nächstes kommt.

## Aktuell halten und ausmisten

Ziel ist ein kleiner Startkontext, egal wie alt das Projekt ist:

- `Stand.md` beschreibt nur das Jetzt. Altes fliegt raus (der Verlauf steht in `sessions/`).
- Überholte Entscheidung: Frontmatter `status: ersetzt` + `ersetzt_durch: "[[neue]]"`, die neue bekommt `ersetzt: "[[alte]]"`. Verworfene Idee oder verworfener Ansatz: `status: verworfen` und eine Zeile unter `Plan.md` -> `## Verworfen` (oder im Hub unter `## Nicht tun`, wenn es ein Verbot ist).
- Erledigte Todos abhaken mit Datum: `- [x] Aufgabe (2026-10-17)`.
- Fertige Features: `status: fertig`, Stand und Ergebnis in zwei Zeilen festhalten, Scratchpad leeren.
- Den Rest erledigt der Session-Start: ersetzte/verworfene Entscheidungen, Features länger als 14 Tage fertig und Todos länger als 14 Tage erledigt wandern nach `archiv/` und tauchen nicht mehr im Startkontext auf. Im Archiv bleibt alles durchsuchbar.

## Nachschlagen

1. Startkontext lesen (Stand, Todos, Index).
2. Für Details den Link aus dem Index öffnen.
3. Nur wenn das nicht reicht, z. B. bei alten Fehlern oder bei "warum haben wir damals...": `python "<vault.py>" search "<begriffe>"` (Standard: aktuelles Projekt + global; `--archiv` schließt das Archiv ein, `--all` alle Projekte).

Vor dem Planen eines Features, vor einer neuen Entscheidung und wenn der Nutzer sich auf Früheres bezieht ("wie letztes Mal", "das Problem mit dem Login"), lohnt sich ein Blick in Index und ggf. `search`: Gab es dazu schon Entscheidungen oder verworfene Ansätze?

## Neues Projekt / unbekannter Ordner

Meldet der Startkontext "kein Projekt für diesen Ordner":

- Wird hier am Code eines Projekts gearbeitet, leg es an, sobald klar ist, worum es geht: `python "<vault.py>" new --name "<Projektname>" --cwd "<ordner>"`. Danach Hub (Zweck, Stack), `Stand.md` und `Plan.md` mit dem füllen, was du schon weißt.
- Gehört der Ordner zu einem bestehenden Projekt (anderer Klon, Sammelordner mit Repo + Worktrees): `python "<vault.py>" link <slug> --cwd "<ordner>"`.
- Ein Repo mit seinen Worktrees ist ein Projekt; erkannt wird es am Git-Remote.
- Allgemeine Fragen ohne Projektbezug: nichts anlegen.

## Einrichtung (einmalig)

Meldet der Startkontext "noch nicht eingerichtet", lies `references/einrichtung.md` und führe den Nutzer durch die Einrichtung (privates Git-Repo + lokaler Vault-Ordner).

## Befehle

```
vault.py status                      Konfiguration + erkanntes Projekt
vault.py new --name "<Name>"         Projekt für den aktuellen Ordner anlegen
vault.py link <slug>                 Ordner einem Projekt zuordnen
vault.py context                     Startkontext anzeigen
vault.py search "<text>" [--archiv] [--all]
vault.py check [--fix]               Format, tote Links, Größe; --fix archiviert Veraltetes
vault.py archive <datei>             Notiz sofort archivieren
vault.py sync pull|push              Git-Sync von Hand
vault.py init --vault <ordner> --remote <git-url>
```
