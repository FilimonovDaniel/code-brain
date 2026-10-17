#!/usr/bin/env bash
# Startet vault.py mit einem Python, das wirklich laeuft.
# Unter Windows ist "python3" oft nur der Microsoft-Store-Platzhalter: existiert, gibt nichts aus,
# endet mit Fehler. Deshalb wird jeder Kandidat ausgefuehrt, nicht nur gesucht.
# stdin (Hook-JSON) wird unveraendert durchgereicht.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$HERE/../skills/code-brain/scripts/vault.py"

for candidate in python3 python "py -3"; do
  # Unquoted: "py -3" ist Befehl plus Argument.
  # </dev/null: der Store-Platzhalter kann sonst das Hook-JSON auf stdin verschlucken.
  if $candidate -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" </dev/null >/dev/null 2>&1; then
    exec $candidate "$SCRIPT" "$@"
  fi
done

echo "code-brain: kein lauffaehiges Python 3.9+ gefunden (python3, python, py -3). Memory wurde nicht geladen." >&2
# Gueltige, leere Antwort, damit die Session trotzdem normal startet.
exit 0
