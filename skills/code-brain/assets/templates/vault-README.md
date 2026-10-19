# code-brain Vault

Memory für Claude Code, angelegt am {{date}}. Claude pflegt es selbst - du kannst jederzeit lesen und korrigieren.

Jede Information steht an genau einem Ort:

```
global/
  Arbeitsregeln.md           wie Claude mit dir arbeitet (immer geladen)
  wissen/<Thema>.md          Wissen ohne Projekt: Server, Tools, Recherchen
projekte/<slug>/
  Projekt.md                 Zweck, Technik, Projektregeln, Verworfen (immer geladen)
  Arbeitsstand.md            in Arbeit, nächste Schritte, offene Fragen, später, zuletzt abgeschlossen (immer geladen)
  arbeitspakete/<nr-name>.md je Feature/Bug/Issue: Ziel, Stand, Todos, Festgelegt, Ergebnis
  wissen/<Thema>.md          Befehle, Stolperstellen, Architektur
_brain/projekte/             Zuordnung Ordner/Git-Remote -> Projekt (automatisch)
```

Kein Archiv: Unwichtiges wird gelöscht, die Git-Historie behält alles. `.brain-state/` ist lokal und steht nicht im Git.
