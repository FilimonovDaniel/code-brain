# code-brain Vault

Projekt-Memory für Claude Code, angelegt am {{date}}. Wird von Claude automatisch gepflegt - du kannst jederzeit lesen und von Hand korrigieren.

## Aufbau

```
_brain/projekte/          je Projekt eine JSON: Zuordnung Ordner/Git-Remote -> Projekt (nicht von Hand nötig)
global/Regeln.md          gilt für alle Projekte, wird immer geladen
projekte/<slug>/
  <slug>.md               Projekt-Hub: Zweck, Stack, Konventionen, "Nicht tun"
  Stand.md                aktueller Ist-Stand + nächster Schritt (wird überschrieben)
  Plan.md                 als Nächstes / später / Ideen / verworfen
  Todos.md                offene Aufgaben außerhalb von Features
  Index.md                automatisch: eine Zeile pro aktiver Notiz
  features/               je Feature/Branch: Ziel, Stand, Todos, Scratchpad
  entscheidungen/         je Entscheidung eine Notiz (aktiv / ersetzt / verworfen)
  wissen/                 Stolperstellen, Befehle, Architektur
  sessions/               kurzer Verlauf pro Tag
  archiv/                 Ausgemistetes - bleibt durchsuchbar, wird nicht mehr geladen
```

`.brain-state/` ist lokal und steht nicht im Git.
