#!/usr/bin/env python3
"""code-brain: automatisches Projekt-Memory in einem zentralen Obsidian-Vault.

Nur Python-Standardbibliothek. Ein Skript für alles:

  vault.py init --vault <pfad> [--remote <git-url>]   Vault einrichten (klonen oder neu anlegen)
  vault.py status [--cwd <ordner>]                    Konfiguration und Projekterkennung zeigen
  vault.py new --name "<Name>" [--slug s] [--cwd d]   Projekt für einen Ordner anlegen und registrieren
  vault.py link <slug> [--cwd d]                      weiteren Ordner/Remote einem Projekt zuordnen
  vault.py context [--cwd d]                          Startkontext ausgeben (das, was der Hook einspielt)
  vault.py search "<text>" [--all] [-n 8]             Stichwortsuche mit Ranking
  vault.py check [<slug>] [--all]                     Pflege: Format, Links, Größe
  vault.py sync pull|push                             Vault mit dem Git-Remote abgleichen
  vault.py hook <ereignis>                            Einstieg für die Claude-Code-Hooks (JSON auf stdin)

Aufbau des Vaults (jede Information an genau einem Ort):
  global/Arbeitsregeln.md, global/wissen/<Thema>.md
  projekte/<slug>/Projekt.md, Arbeitsstand.md, arbeitspakete/<nr-name>.md, wissen/<Thema>.md

Konfiguration: ~/.code-brain/config.json  {"vault": "<pfad>"}
Überschreibbar mit den Umgebungsvariablen CODE_BRAIN_VAULT und CODE_BRAIN_CONFIG.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Windows-Konsolen schreiben sonst cp1252; Hook-Ausgaben müssen UTF-8 sein.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATES = SKILL_DIR / "assets" / "templates"

# Claude Code ersetzt Hook-Ausgaben über 10.000 Zeichen durch eine Vorschau.
CONTEXT_BUDGET = 9300
NUDGE_AFTER_PROMPTS = 8          # Hinweis nach so vielen Nutzer-Nachrichten ohne Eintrag im Vault
STALE_TASK_HOURS = 3             # offene Aufgabe einer anderen, so lange stillen Session gilt als abgebrochen
GIT_TIMEOUT = 12
PULL_EVERY = 600                 # Sekunden: so oft holt eine laufende Session Einträge anderer Geräte nach

PROJECTS_DIR = "projekte"
GLOBAL_DIR = "global"
SYSTEM_DIR = "_brain"
STATE_DIR = ".brain-state"
RULES_FILE = "Arbeitsregeln.md"
HUB_FILE = "Projekt.md"
STAND_FILE = "Arbeitsstand.md"
PAKETE_DIR = "arbeitspakete"
WISSEN_DIR = "wissen"
LEGACY = ("Stand.md", "Plan.md", "Todos.md", "Index.md", "entscheidungen", "features", "sessions", "archiv")

# Grenzen im Startkontext (Zeichen) - zusammen unter CONTEXT_BUDGET
MAX_RULES = 2200
MAX_HUB = 2800
MAX_STAND = 2800

ACTIVE_STATUS = {"geplant", "in-arbeit", "review", "blockiert", ""}

PLAN_TOOLS = {"TodoWrite", "TaskCreate", "mcp__ccd_turn__update_plan"}
END_TOOLS = {"mcp__ccd_turn__wrap_up"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
# Gesperrt nach einer neuen Aufgabenliste, bis `vault.py search` gelaufen ist. Lesen (Read/Grep/Glob) bleibt frei.
GATE_TOOLS = WRITE_TOOLS | SHELL_TOOLS | {"Agent", "Task", "Workflow", "mcp__ccd_session__start_session"}
SEARCH_GRACE = 300               # Suche bis so viele Sekunden vor dem Aufgabenstart zählt (paralleler Aufruf)


# --------------------------------------------------------------------------- Grundlagen

def today() -> str:
    return dt.date.today().isoformat()


def now_ts() -> float:
    return dt.datetime.now().timestamp()


def norm_path(p: str | Path) -> str:
    """Vergleichbare Pfadform: Kleinbuchstaben, Schrägstriche, /c/... statt C:\\..."""
    s = str(p).replace("\\", "/").rstrip("/")
    m = re.match(r"^([A-Za-z]):(.*)$", s)
    if m:
        s = f"/{m.group(1).lower()}{m.group(2)}"
    return s.lower()


def norm_remote(url: str) -> str:
    """git@github.com:Org/Repo.git und https://github.com/org/repo -> github.com/org/repo"""
    u = url.strip()
    u = re.sub(r"^[a-z+]+://", "", u)
    u = re.sub(r"^[^@/]+@", "", u)
    u = u.replace(":", "/", 1) if re.match(r"^[^/]+:[^/]", u) else u
    u = re.sub(r"\.git$", "", u).rstrip("/")
    return u.lower()


def slugify(text: str) -> str:
    s = text.strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "projekt"


def run_git(args: list[str], cwd: str | Path, timeout: int = GIT_TIMEOUT) -> tuple[int, str]:
    try:
        # Ohne Terminal-Prompt: fehlende Anmeldung scheitert sofort statt bis zum Timeout zu warten.
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, env=env)
        return r.returncode, (r.stdout + r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def read_text(p: Path) -> str:
    """UTF-8 mit/ohne BOM; CRLF (Obsidian unter Windows) wird zu LF."""
    try:
        return p.read_text(encoding="utf-8-sig", errors="replace").replace("\r\n", "\n")
    except OSError:
        return ""


def write_text(p: Path, text: str) -> None:
    """Atomar schreiben: erst Temp-Datei, dann ersetzen - nie halbe Notizen im Vault."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


GENERIC_DIRS = {"", "desktop", "documents", "dokumente", "downloads", "tmp", "temp"}


def is_generic_dir(cwd: Path) -> bool:
    """Home, Laufwerkswurzel, Desktop, Downloads ... sind keine Projekte."""
    c = norm_path(cwd)
    home = norm_path(Path.home())
    if c in (home, "/") or re.fullmatch(r"/[a-z]", c):
        return True
    return c.startswith(home + "/") and c[len(home) + 1:] in GENERIC_DIRS


SECRET_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{24,}|ghp_[A-Za-z0-9]{36}"
    r"|github_pat_[A-Za-z0-9_]{20,}|xox[bpars]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}"
    r"|phc_[A-Za-z0-9]{30,}|(?i:password|passwort|secret|api[_-]?key)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")


# --------------------------------------------------------------------------- Frontmatter

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Kleiner YAML-Ausschnitt: key: wert, key: [a, b], Listen mit '- '."""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    data: dict = {}
    key = None
    for raw in m.group(1).splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and line.strip().startswith("- ") and key:
            data.setdefault(key, [])
            if isinstance(data[key], list):
                data[key].append(_scalar(line.strip()[2:]))
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                data[key] = [_scalar(v) for v in val[1:-1].split(",") if v.strip()]
            elif val == "":
                data[key] = []
            else:
                data[key] = _scalar(val)
    return data, text[m.end():]


def _scalar(v: str):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def as_list(v) -> list:
    if v is None or v == "":
        return []
    return v if isinstance(v, list) else [v]


# --------------------------------------------------------------------------- Konfiguration

def config_path() -> Path:
    env = os.environ.get("CODE_BRAIN_CONFIG")
    return Path(env) if env else Path.home() / ".code-brain" / "config.json"


def load_config() -> dict:
    cfg = {}
    p = config_path()
    if p.is_file():
        try:
            cfg = json.loads(read_text(p) or "{}")
        except json.JSONDecodeError:
            cfg = {}
    if os.environ.get("CODE_BRAIN_VAULT"):
        cfg["vault"] = os.environ["CODE_BRAIN_VAULT"]
    return cfg


def vault_root(required: bool = True) -> Path | None:
    v = load_config().get("vault")
    if v and Path(v).is_dir():
        return Path(v)
    if required:
        sys.exit("code-brain ist nicht eingerichtet. Zuerst: vault.py init --vault <pfad> [--remote <git-url>]")
    return None


def registry_dir(vault: Path) -> Path:
    """Eine JSON-Datei pro Projekt: zwei Geräte, die gleichzeitig Projekte anlegen, kollidieren nicht."""
    return vault / SYSTEM_DIR / "projekte"


def load_registry(vault: Path) -> dict:
    projects = {}
    d = registry_dir(vault)
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                projects[p.stem] = json.loads(read_text(p) or "{}")
            except json.JSONDecodeError:
                print(f"code-brain: {p} ist kein gültiges JSON - übersprungen", file=sys.stderr)
    return {"projects": projects}


def save_registry(vault: Path, reg: dict) -> None:
    for slug, entry in reg["projects"].items():
        p = registry_dir(vault) / f"{slug}.json"
        new = json.dumps(entry, indent=2, ensure_ascii=False) + "\n"
        if read_text(p) != new:
            write_text(p, new)


# --------------------------------------------------------------------------- Projekterkennung

def git_info(cwd: Path) -> dict:
    """Remote und Wurzel des Git-Repos, in dem cwd liegt (Worktrees teilen den Remote)."""
    info = {"remote": "", "toplevel": ""}
    code, out = run_git(["rev-parse", "--show-toplevel"], cwd, timeout=5)
    if code == 0 and out:
        info["toplevel"] = out.splitlines()[-1]
        code, out = run_git(["config", "--get", "remote.origin.url"], cwd, timeout=5)
        if code == 0 and out:
            info["remote"] = norm_remote(out.splitlines()[-1])
    return info


def detect_project(vault: Path, cwd: Path) -> tuple[str | None, dict, str]:
    """(slug, eintrag, wie) - zuerst über den längsten passenden Pfad, dann über den Git-Remote."""
    reg = load_registry(vault)
    here = norm_path(cwd)
    best, best_len = None, -1
    for slug, p in reg["projects"].items():
        for path in as_list(p.get("paths")):
            np_ = norm_path(path)
            if (here == np_ or here.startswith(np_ + "/")) and len(np_) > best_len:
                best, best_len = slug, len(np_)
    if best:
        return best, reg["projects"][best], "Ordner"
    gi = git_info(cwd)
    if gi["remote"]:
        for slug, p in reg["projects"].items():
            if gi["remote"] in [norm_remote(r) for r in as_list(p.get("remotes"))]:
                return slug, p, f"Git-Remote {gi['remote']}"
    return None, {}, ""


def project_dir(vault: Path, slug: str) -> Path:
    return vault / PROJECTS_DIR / slug


# --------------------------------------------------------------------------- Vorlagen

def render_template(template: str, **values) -> str:
    text = read_text(TEMPLATES / template)
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


# --------------------------------------------------------------------------- Notizen

def iter_notes(base: Path):
    for p in sorted(base.rglob("*.md")):
        rel = p.relative_to(base).as_posix()
        if "/." in "/" + rel or rel.startswith("."):
            continue
        yield p


def note_meta(p: Path) -> tuple[dict, str]:
    return parse_frontmatter(read_text(p))


def title_of(p: Path, body: str) -> str:
    m = re.search(r"^#\s+(.+)$", body, re.M)
    return m.group(1).strip() if m else p.stem


COMMENT_RE = re.compile(r"%%.*?%%", re.S)


def strip_comments(text: str) -> str:
    """Obsidian-Kommentare (%% ... %%) sind Ausfüllhinweise - nicht in den Kontext laden."""
    return re.sub(r"\n{3,}", "\n\n", COMMENT_RE.sub("", text)).strip()


def note_body(p: Path) -> str:
    """Inhalt ohne Frontmatter, Kommentare und erste Überschrift."""
    _, body = note_meta(p)
    return re.sub(r"^#\s.*\n?", "", strip_comments(body), count=1).strip()


def demote(text: str) -> str:
    """## -> ### usw., damit Notizinhalt unter eine ##-Überschrift des Kontexts passt."""
    return re.sub(r"^(#{2,5})\s", r"#\1 ", text, flags=re.M)


def pakete(base: Path, active_only: bool = True) -> list[tuple[Path, dict]]:
    d = base / PAKETE_DIR
    out = []
    for p in sorted(d.glob("*.md")) if d.is_dir() else []:
        meta, _ = note_meta(p)
        if not active_only or str(meta.get("status", "")).lower() in ACTIVE_STATUS:
            out.append((p, meta))
    return out


def wissen_names(d: Path) -> list[str]:
    return [p.stem for p in sorted(d.glob("*.md"))] if d.is_dir() else []


# --------------------------------------------------------------------------- Kontext

CORE_RULES = """\
**code-brain - zuerst den Skill `code-brain` mit dem Skill-Tool laden (vor der ersten Antwort).**
- Nach jeder neuen Aufgabenliste sind Bash/PowerShell, Edit/Write, Agent und neue Sessions gesperrt, bis `{cmd} search "<begriffe>"` gelaufen ist.
- Jede Information an genau einem Ort: was dauerhaft gilt und was verworfen ist → `Projekt.md`; was läuft und was kommt → `Arbeitsstand.md`; alles zu einem Feature/Bug (Ziel, Todos, Festgelegt, Ergebnis) → `arbeitspakete/<nr-name>.md`; wie etwas geht → `wissen/` (ohne Projekt: `global/wissen/`).
- Sofort eintragen, wenn der Nutzer etwas entscheidet, ändert oder streicht. Aufgabenstart: erst `{cmd} search "<begriffe>"`, Treffer lesen, dann Arbeitspaket + Arbeitsstand. Aufgabenende: Ergebnis + Arbeitsstand.
- Unwichtiges löschen statt sammeln, nichts doppelt. Frontmatter: `type`, `summary`, `stichworte`, `updated`."""


def clip(text: str, limit: int, pointer: str) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n", 1)[0]
    return cut + f"\n... (gekürzt - vollständig in `{pointer}`)"


def cli_cmd() -> str:
    return f'python "{Path(__file__).resolve().as_posix()}"'


def wissen_part(vault: Path, base: Path | None) -> str:
    lines = []
    if base:
        names = wissen_names(base / WISSEN_DIR)
        if names:
            lines.append("- Projekt: " + ", ".join(names))
    names = wissen_names(vault / GLOBAL_DIR / WISSEN_DIR)
    if names:
        lines.append("- Global: " + ", ".join(names))
    if not lines:
        return ""
    return ("## Wissen (nur Themen - bei Bezug Datei öffnen oder `search`)\n" + "\n".join(lines))


def build_context(vault: Path, cwd: Path, notes: list[str] | None = None, sync_info: str = "") -> str:
    slug, entry, how = detect_project(vault, cwd)
    cmd = cli_cmd()
    parts: list[str] = []
    if sync_info:
        parts.append(f"> [!info] {sync_info}")
    for n in notes or []:
        parts.append(f"> [!warning] code-brain\n> {n}")
    rules = note_body(vault / GLOBAL_DIR / RULES_FILE)
    rules_part = ("## Arbeitsregeln\n" + clip(demote(rules), MAX_RULES, f"{GLOBAL_DIR}/{RULES_FILE}")
                  if rules else "")

    if not slug and is_generic_dir(cwd):
        parts.append(f"# code-brain: allgemeiner Ordner (`{cwd}`) - kein Projekt\n"
                     f"Projektunabhängiges Wissen gehört nach `{vault / GLOBAL_DIR / WISSEN_DIR}`. "
                     f"Vorher suchen: `{cmd} search \"<begriffe>\"`.")
        parts += [x for x in (rules_part, wissen_part(vault, None)) if x]
    elif not slug:
        reg = load_registry(vault)
        listing = "\n".join(f"- `{s}` - {p.get('name', s)}" for s, p in sorted(reg["projects"].items())) \
            or "- (noch keine Projekte)"
        parts.append(
            f"# code-brain: kein Projekt für diesen Ordner\n"
            f"Ordner: `{cwd}` - Vault: `{vault}`\n\n"
            "Wird hier an Code gearbeitet, lege das Projekt an, sobald klar ist, worum es geht "
            f'(ohne Rückfrage, außer der Name ist unklar): `{cmd} new --name "<Projektname>" --cwd "{cwd}"`\n'
            f'Gehört der Ordner zu einem bestehenden Projekt: `{cmd} link <slug> --cwd "{cwd}"`\n'
            f"Projektunabhängiges Wissen gehört nach `global/wissen/`.\n\nBekannte Projekte:\n{listing}")
        parts += [x for x in (rules_part, wissen_part(vault, None)) if x]
    else:
        base = project_dir(vault, slug)
        rel = f"{PROJECTS_DIR}/{slug}"
        parts.append(f"# code-brain: Projekt {entry.get('name', slug)} (`{slug}`)\n"
                     f"Erkannt über: {how} - Projektordner: `{base}`\nSkript: `{cmd}`")
        parts.append(CORE_RULES.format(cmd=cmd))
        if rules_part:
            parts.append(rules_part)
        hub = note_body(base / HUB_FILE)
        parts.append("## Projekt\n" + clip(demote(hub) or "(leer)", MAX_HUB, f"{rel}/{HUB_FILE}"))
        stand = note_body(base / STAND_FILE)
        parts.append("## Arbeitsstand\n" + clip(demote(stand) or "(leer)", MAX_STAND, f"{rel}/{STAND_FILE}"))
        offen = []
        for p, meta in pakete(base):
            status = str(meta.get("status", "")) or "?"
            summary = str(meta.get("summary", "")).strip()
            line = f"- {p.stem} `{status}` - {summary}"
            offen.append(line if len(line) <= 170 else line[:167] + "...")
        if offen:
            parts.append(f"## Offene Arbeitspakete (`{rel}/{PAKETE_DIR}/<Name>.md`)\n" + "\n".join(offen))
        w = wissen_part(vault, base)
        if w:
            parts.append(w)

    out = "\n\n".join(parts)
    # Gemessen wird die JSON-kodierte Länge - so zählt Claude Code das 10.000-Zeichen-Limit.
    while len(json.dumps(out, ensure_ascii=False)) > CONTEXT_BUDGET + 300 and len(out) > 500:
        out = out[: int(len(out) * 0.9)].rsplit("\n", 1)[0]
        out += f"\n... (Kontext gekürzt - Rest im Vault lesen; `{cmd} check` zeigt, was zu lang ist)"
    return out


def last_sync_file(vault: Path) -> Path:
    return vault / STATE_DIR / "last-sync.txt"


def remember_sync(vault: Path, msg: str) -> None:
    """Hook-Fehler auf stderr sieht niemand - deshalb beim nächsten Start im Kontext melden."""
    p = last_sync_file(vault)
    if msg:
        write_text(p, msg)
    elif p.exists():
        try:
            p.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------- Git-Sync

def vault_has_git(vault: Path) -> bool:
    return (vault / ".git").exists()


def vault_has_remote(vault: Path) -> bool:
    code, out = run_git(["remote"], vault, timeout=5)
    return code == 0 and bool(out.strip())


def conflict_files(vault: Path) -> list[str]:
    hits = []
    for p in vault.rglob("*.md"):
        rel = p.relative_to(vault).as_posix()
        if rel.startswith(".") or "/." in rel:
            continue
        if re.search(r"^(<<<<<<< |>>>>>>> )", read_text(p), re.M):
            hits.append(rel)
    return hits


def pull_rebase(vault: Path) -> tuple[int, str]:
    args = ["pull", "--rebase", "--autostash", "-q"]
    if run_git(["rev-parse", "--abbrev-ref", "@{u}"], vault, timeout=5)[0] != 0:
        # Noch kein Upstream (z. B. leeres Remote vor dem ersten Push): gleichnamigen Branch holen, falls es ihn gibt.
        _, branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], vault, timeout=5)
        code, out = run_git(["ls-remote", "--heads", "origin", branch.strip()], vault)
        if code != 0:
            return code, out
        if not out.strip():
            return 0, ""
        args += ["origin", branch.strip()]
    code, out = run_git(args, vault)
    if code != 0:
        run_git(["rebase", "--abort"], vault, timeout=5)
    return code, out


def upstream_head(vault: Path) -> str:
    code, out = run_git(["rev-parse", "-q", "--verify", "@{u}"], vault, timeout=5)
    return out.strip() if code == 0 else ""


def remote_news(vault: Path, before: str) -> str:
    """Was der Pull von anderen Geräten gebracht hat (Commits zwischen altem und neuem Remote-Stand)."""
    after = upstream_head(vault)
    if not after or after == before:
        return ""
    rng = f"{before}..{after}" if before else after
    _, log = run_git(["log", "--format=%s", rng], vault, timeout=5)
    _, files = run_git(["diff", "--name-only", rng, "--", "*.md"] if before
                       else ["show", "--name-only", "--format=", after, "--", "*.md"], vault, timeout=5)
    commits = [c for c in log.splitlines() if c.strip()]
    changed = [f for f in files.splitlines() if f.strip() and not f.startswith(".")]
    if not commits:
        return ""
    text = f"{len(commits)} Commit(s) von anderen Geräten übernommen: " + "; ".join(commits[:4])
    if len(commits) > 4:
        text += " …"
    if changed:
        text += ". Geänderte Notizen: " + ", ".join(f"`{f}`" for f in changed[:8]) + (" …" if len(changed) > 8 else "")
    return text


def sync_pull(vault: Path) -> tuple[bool, str, str]:
    """Holt den Vault vom Remote. Ergebnis: (ok, Warnung, Neues von anderen Geräten)."""
    if not vault_has_git(vault) or not vault_has_remote(vault):
        return True, "", ""
    # Erst lokal committen: Rebase auf sauberem Stand statt Stash-Akrobatik.
    warn = commit_local(vault, "lokal vor pull")
    before = upstream_head(vault)
    code, out = pull_rebase(vault)
    ok = code == 0
    if not ok:
        low = out.lower()
        if "could not resolve" in low or "unable to access" in low or "timed out" in low:
            reason = "Remote nicht erreichbar (offline?)"
        else:
            reason = f"git pull fehlgeschlagen: {out[:300]}"
        warn = (warn + " " if warn else "") + (
            f"Vault NICHT mit anderen Geräten abgeglichen - {reason}. Einträge anderer Geräte können fehlen; "
            "code-brain versucht es bei jeder Nachricht erneut, von Hand: `vault.py sync pull`.")
    conflicts = conflict_files(vault)
    if conflicts:
        warn = (warn + " " if warn else "") + ("Konfliktmarker in: " + ", ".join(conflicts[:5])
                                                + " - beide Fassungen zusammenführen und Marker entfernen.")
    return ok, warn, remote_news(vault, before) if ok else ""


def staged_secrets(vault: Path) -> list[str]:
    """Dateien im Commit, die nach Zugangsdaten aussehen - die landen sonst dauerhaft im Git."""
    _, out = run_git(["diff", "--cached", "-U0", "--no-color"], vault)
    hits, current = [], ""
    for line in out.splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and SECRET_RE.search(line):
            if current not in hits:
                hits.append(current)
    return hits


def commit_local(vault: Path, label: str) -> str:
    """Alle Vault-Änderungen committen. Leerer String = ok, sonst Grund, warum nicht."""
    if not vault_has_git(vault):
        return ""
    run_git(["add", "-A"], vault)
    code, _ = run_git(["diff", "--cached", "--quiet"], vault, timeout=5)
    if code == 0:
        return ""
    secrets = staged_secrets(vault)
    if secrets:
        run_git(["reset", "-q"], vault)
        return ("Commit gestoppt - mögliche Zugangsdaten in: " + ", ".join(secrets)
                + ". Entferne sie aus den Notizen (nur Verweis auf .env o. ä.), dann wird automatisch synchronisiert.")
    msg = f"code-brain: {label or 'update'} {dt.datetime.now():%Y-%m-%d %H:%M}"
    code, out = run_git(["commit", "-q", "-m", msg], vault)
    return "" if code == 0 else f"Commit im Vault fehlgeschlagen: {out[:300]}"


def sync_push(vault: Path, label: str = "") -> str:
    if not vault_has_git(vault):
        return ""
    warn = commit_local(vault, label)
    if warn or not vault_has_remote(vault):
        return warn
    code, out = run_git(["push", "-q", "-u", "origin", "HEAD"], vault, timeout=20)
    if code != 0:
        # Remote hat neuere Commits (anderes Gerät): einmal rebasen, dann erneut pushen.
        c2, o2 = pull_rebase(vault)
        if c2 == 0:
            code, out = run_git(["push", "-q", "-u", "origin", "HEAD"], vault, timeout=20)
        else:
            out = o2
    return "" if code == 0 else f"git push im Vault fehlgeschlagen: {out[:300]}"


# --------------------------------------------------------------------------- Zustand je Session

def state_dir(vault: Path) -> Path:
    return vault / STATE_DIR / "sessions"


def state_file(vault: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unbekannt")
    return state_dir(vault) / f"{safe}.json"


def load_state(vault: Path, session_id: str) -> dict:
    try:
        return json.loads(read_text(state_file(vault, session_id)) or "{}")
    except json.JSONDecodeError:
        return {}


def save_state(vault: Path, session_id: str, state: dict) -> None:
    state["seen"] = now_ts()
    write_text(state_file(vault, session_id), json.dumps(state, indent=1, ensure_ascii=False))


def prune_states(vault: Path, days: int = 14) -> None:
    d = state_dir(vault)
    if not d.is_dir():
        return
    limit = now_ts() - days * 86400
    for p in d.glob("*.json"):
        try:
            if p.stat().st_mtime < limit:
                p.unlink()
        except OSError:
            pass


def vault_fingerprint(vault: Path, slug: str | None) -> str:
    """Ändert sich bei jeder Notiz-Änderung in global/ und im Projekt."""
    targets = [vault / GLOBAL_DIR]
    if slug:
        targets.append(project_dir(vault, slug))
    h = hashlib.sha1()
    for t in targets:
        if not t.is_dir():
            continue
        for p in sorted(t.rglob("*.md")):
            try:
                st = p.stat()
                h.update(f"{p}:{st.st_mtime_ns}:{st.st_size}".encode())
            except OSError:
                pass
    return h.hexdigest()


def stand_mark(vault: Path, slug: str | None) -> float:
    return mtime(project_dir(vault, slug) / STAND_FILE) if slug else 0.0


def stale_tasks(vault: Path, slug: str, own_sid: str) -> list[dict]:
    """Offene Aufgaben anderer Sessions im selben Projekt, die beendet oder lange still sind."""
    found = []
    d = state_dir(vault)
    own = state_file(vault, own_sid).name
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        if p.name == own:
            continue
        try:
            st = json.loads(read_text(p) or "{}")
        except json.JSONDecodeError:
            continue
        task = st.get("task") or {}
        if st.get("slug") != slug or not task.get("open"):
            continue
        if st.get("ended") or now_ts() - float(st.get("seen", 0)) > STALE_TASK_HOURS * 3600:
            found.append({"file": p, "state": st, "task": task})
    return found


# --------------------------------------------------------------------------- Pflege (check)

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def all_note_names(vault: Path) -> tuple[set, set]:
    stems, paths = set(), set()
    for p in vault.rglob("*.md"):
        rel = p.relative_to(vault).as_posix()
        if rel.startswith("."):
            continue
        stems.add(p.stem.lower())
        paths.add(rel[:-3].lower())
    return stems, paths


def check_note(vault: Path, p: Path, stems: set, paths: set) -> list[str]:
    rel = p.relative_to(vault).as_posix()
    meta, body = note_meta(p)
    issues = []
    missing = [k for k in ("type", "summary", "updated") if not meta.get(k)]
    if f"/{PAKETE_DIR}/" in rel and not meta.get("status"):
        missing.append("status")
    if missing:
        issues.append(f"{rel}: Frontmatter fehlt {', '.join(missing)}")
    if (f"/{PAKETE_DIR}/" in rel or f"/{WISSEN_DIR}/" in rel or rel.startswith(f"{GLOBAL_DIR}/{WISSEN_DIR}/")) \
            and not meta.get("stichworte"):
        issues.append(f"{rel}: `stichworte` fehlen (Suchbegriffe)")
    for target in WIKILINK_RE.findall(strip_comments(body)):
        t = target.strip().lower().removesuffix(".md")
        if t not in stems and t not in paths:
            issues.append(f"{rel}: toter Link [[{target.strip()}]]")
    return issues


def check_project(vault: Path, slug: str) -> list[str]:
    base = project_dir(vault, slug)
    if not base.is_dir():
        return [f"Projektordner fehlt: {base}"]
    stems, paths = all_note_names(vault)
    issues: list[str] = []
    legacy = [n for n in LEGACY if (base / n).exists()]
    if legacy:
        issues.append(f"alte Struktur noch vorhanden: {', '.join(legacy)} - in Projekt.md, Arbeitsstand.md, "
                      "arbeitspakete/ und wissen/ überführen, dann löschen")
    for name in (HUB_FILE, STAND_FILE):
        if not (base / name).exists():
            issues.append(f"{name} fehlt")
    for p in iter_notes(base):
        issues += check_note(vault, p, stems, paths)
    for name, limit in ((HUB_FILE, MAX_HUB), (STAND_FILE, MAX_STAND)):
        n = len(demote(note_body(base / name)))
        if n > limit:
            issues.append(f"{name} hat {n} Zeichen (Startkontext lädt {limit}) - Überholtes löschen, kürzen")
    return issues


def check_global(vault: Path) -> list[str]:
    stems, paths = all_note_names(vault)
    issues = []
    g = vault / GLOBAL_DIR
    if (g / "Regeln.md").exists():
        issues.append(f"{GLOBAL_DIR}/Regeln.md ist die alte Datei - Inhalt nach {RULES_FILE} überführen, dann löschen")
    for p in iter_notes(g) if g.is_dir() else []:
        issues += check_note(vault, p, stems, paths)
    n = len(demote(note_body(g / RULES_FILE)))
    if n > MAX_RULES:
        issues.append(f"{GLOBAL_DIR}/{RULES_FILE} hat {n} Zeichen (Startkontext lädt {MAX_RULES}) - kürzen")
    return issues


# --------------------------------------------------------------------------- Suche

STOP = set("""der die das und oder nicht ein eine einer eines einem einen ist sind war wird wurde werden
mit für von zu im in am an auf aus bei nach über unter wie was wer wo warum wieso weshalb dass
es er sie wir ihr ich du man auch noch nur schon so dann denn als wenn aber doch mal hat haben hatte
the a an and or not is are was were be to of in on at for with from by as it this that what why how
""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\wäöüß]+", text.lower()) if len(t) > 1 and t not in STOP]


def search(vault: Path, query: str, slug: str | None, all_projects: bool, limit: int) -> list[dict]:
    q = tokens(query)
    if not q:
        return []
    bases = [vault] if all_projects or not slug else [project_dir(vault, slug), vault / GLOBAL_DIR]
    results = []
    for base in bases:
        if not base.is_dir():
            continue
        for p in iter_notes(base):
            rel = p.relative_to(vault).as_posix()
            if rel.startswith(SYSTEM_DIR + "/") or rel == "README.md":
                continue
            meta, body = note_meta(p)
            title = title_of(p, body) + " " + p.stem
            head = " ".join(re.findall(r"^#+\s+(.+)$", body, re.M))
            meta_txt = " ".join(" ".join(map(str, as_list(v))) for k, v in meta.items()
                                if k in ("summary", "stichworte", "tags", "issue", "branch", "pr", "status"))
            fields = {"title": (tokens(title), 4.0), "meta": (tokens(meta_txt), 3.0),
                      "head": (tokens(head), 2.0), "body": (tokens(body), 1.0)}
            score, hit_terms = 0.0, set()
            for term in q:
                for toks, w in fields.values():
                    c = sum(1 for t in toks if t == term or (len(term) > 4 and t.startswith(term)))
                    if c:
                        score += w * min(c, 5)
                        hit_terms.add(term)
            if not score:
                continue
            score *= (len(hit_terms) / len(q)) ** 1.5        # alle Begriffe getroffen > einer oft
            snippet = ""
            for line in body.splitlines():
                if any(t in line.lower() for t in q):
                    snippet = line.strip()[:160]
                    break
            results.append({"score": round(score, 2), "path": rel, "status": str(meta.get("status", "")),
                            "summary": str(meta.get("summary", "")), "snippet": snippet})
    results.sort(key=lambda r: -r["score"])
    return results[:limit]


# --------------------------------------------------------------------------- Befehle

def gh_credential_helper(user: str) -> str:
    """Git-Credential-Helper, der das Token eines bestimmten gh-Kontos liefert.
    So nutzt nur der Vault dieses Konto - das global aktive gh-Konto bleibt unberührt."""
    gh = (shutil.which("gh") or "gh").replace("\\", "/")
    return (f'!f() {{ test "$1" = get || exit 0; echo username={user}; '
            f'echo password=$("{gh}" auth token --user {user}); }}; f')


def cmd_init(a) -> None:
    vault = Path(a.vault).expanduser().resolve()
    cred = ["-c", "credential.helper=", "-c", f"credential.helper={gh_credential_helper(a.gh_user)}"] if a.gh_user else []
    if a.remote and not (vault / ".git").exists():
        vault.mkdir(parents=True, exist_ok=True)
        leftovers = [p.name for p in vault.iterdir() if p.name != ".obsidian"]
        if leftovers:
            sys.exit(f"{vault} enthält schon Dateien ({', '.join(leftovers[:5])}) - bitte leeren Ordner "
                     "oder einen Obsidian-Vault ohne Notizen angeben.")
        # Auch ein frisch in Obsidian angelegter Vault (nur .obsidian/) ist ok: init + fetch statt clone.
        run_git(["init", "-q", "-b", "main"], vault)
        run_git(["remote", "add", "origin", a.remote], vault)
        code, out = run_git([*cred, "fetch", "-q", "origin"], vault, timeout=120)
        if code != 0:
            sys.exit(f"Zugriff auf {a.remote} fehlgeschlagen: {out}")
        _, heads = run_git(["branch", "-r"], vault)
        if "origin/" in heads:
            branch = "main" if "origin/main" in heads else heads.split()[0].split("/", 1)[1]
            code, out = run_git(["checkout", "-q", "-B", branch, "--track", f"origin/{branch}"], vault)
            if code != 0:
                sys.exit(f"Auschecken fehlgeschlagen: {out}")
    vault.mkdir(parents=True, exist_ok=True)
    if not (vault / ".git").exists():
        run_git(["init", "-q", "-b", "main"], vault)
    if a.gh_user:
        run_git(["config", "--replace-all", "credential.helper", ""], vault)
        run_git(["config", "--add", "credential.helper", gh_credential_helper(a.gh_user)], vault)
        # Die globale github.com-Regel (gh mit aktivem Konto) hat Vorrang vor credential.helper -
        # deshalb für dieses Repo ebenfalls überschreiben.
        run_git(["config", "--replace-all", "credential.https://github.com.helper", ""], vault)
        run_git(["config", "--add", "credential.https://github.com.helper", gh_credential_helper(a.gh_user)], vault)
    for d in (SYSTEM_DIR, PROJECTS_DIR, f"{GLOBAL_DIR}/{WISSEN_DIR}"):
        (vault / d).mkdir(parents=True, exist_ok=True)
    gi = vault / ".gitignore"
    wanted = [f"{STATE_DIR}/", ".obsidian/workspace*.json", ".obsidian/cache", ".trash/"]
    have = read_text(gi).splitlines()
    write_text(gi, "\n".join(have + [w for w in wanted if w not in have]).strip() + "\n")
    # eol=lf: Obsidian unter Windows schreibt CRLF - sonst Phantom-Diffs zwischen Geräten.
    # merge=union: ändern zwei Geräte dieselbe Notiz, bleiben beide Zeilen erhalten statt
    # Konfliktmarkern in der Notiz; Claude räumt Doppeltes beim nächsten Update auf.
    ga = vault / ".gitattributes"
    have = read_text(ga).splitlines()
    wanted = ["* text=auto eol=lf", "*.md merge=union"]
    write_text(ga, "\n".join(have + [w for w in wanted if w not in have]).strip() + "\n")
    for keep in (vault / PROJECTS_DIR, registry_dir(vault), vault / GLOBAL_DIR / WISSEN_DIR):
        keep.mkdir(parents=True, exist_ok=True)
        if not any(keep.iterdir()):
            write_text(keep / ".gitkeep", "")
    if not (vault / "README.md").exists():
        write_text(vault / "README.md", render_template("vault-README.md", date=today()))
    if not (vault / GLOBAL_DIR / RULES_FILE).exists():
        write_text(vault / GLOBAL_DIR / RULES_FILE, render_template("Arbeitsregeln.md", date=today()))
    cfg = load_config()
    cfg["vault"] = str(vault)
    write_text(config_path(), json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    msg = sync_push(vault, "init")
    print(f"Vault eingerichtet: {vault}\nKonfiguration: {config_path()}")
    if msg:
        print(f"Hinweis: {msg}")


def cmd_status(a) -> None:
    cfg = load_config()
    print(f"Konfiguration: {config_path()} -> {cfg or '(fehlt)'}")
    vault = vault_root(required=False)
    if not vault:
        print("Vault: nicht eingerichtet")
        return
    cwd = Path(a.cwd or os.getcwd())
    slug, entry, how = detect_project(vault, cwd)
    print(f"Vault: {vault} (git: {'ja' if vault_has_git(vault) else 'nein'}, remote: {'ja' if vault_has_git(vault) and vault_has_remote(vault) else 'nein'})")
    print(f"Ordner: {cwd}\nProjekt: {slug or '(keins)'} {('- ' + how) if how else ''}")
    print(f"Projekte gesamt: {len(load_registry(vault)['projects'])}")


def cmd_new(a) -> None:
    vault = vault_root()
    cwd = Path(a.cwd or os.getcwd()).resolve()
    slug = a.slug or slugify(a.name)
    reg = load_registry(vault)
    existing, _, _ = detect_project(vault, cwd)
    if existing:
        sys.exit(f"Ordner gehört schon zu Projekt '{existing}'.")
    if slug in reg["projects"]:
        sys.exit(f"Slug '{slug}' existiert schon - 'link {slug}' verwenden oder --slug angeben.")
    gi = git_info(cwd)
    entry = {"name": a.name, "remotes": [gi["remote"]] if gi["remote"] else [],
             "paths": [str(cwd)], "created": today()}
    reg["projects"][slug] = entry
    save_registry(vault, reg)
    base = project_dir(vault, slug)
    values = dict(name=a.name, slug=slug, date=today(), remote=gi["remote"] or "-",
                  path=str(cwd).replace("\\", "/"))
    for target, tpl in ((HUB_FILE, "Projekt.md"), (STAND_FILE, "Arbeitsstand.md")):
        if not (base / target).exists():
            write_text(base / target, render_template(tpl, **values))
    for d in (PAKETE_DIR, WISSEN_DIR):
        (base / d).mkdir(parents=True, exist_ok=True)
        if not any((base / d).iterdir()):
            write_text(base / d / ".gitkeep", "")
    print(f"Projekt angelegt: {slug}\nOrdner: {base}\nRemote: {gi['remote'] or '-'}")


def cmd_link(a) -> None:
    vault = vault_root()
    reg = load_registry(vault)
    if a.slug not in reg["projects"]:
        sys.exit(f"Unbekanntes Projekt '{a.slug}'.")
    cwd = Path(a.cwd or os.getcwd()).resolve()
    entry = reg["projects"][a.slug]
    gi = git_info(cwd)
    if gi["remote"] and gi["remote"] not in entry.setdefault("remotes", []):
        entry["remotes"].append(gi["remote"])
    if str(cwd) not in entry.setdefault("paths", []):
        entry["paths"].append(str(cwd))
    save_registry(vault, reg)
    print(f"{cwd} -> {a.slug}")


def cmd_context(a) -> None:
    vault = vault_root()
    print(build_context(vault, Path(a.cwd or os.getcwd())))


def cmd_search(a) -> None:
    vault = vault_root()
    slug, _, _ = detect_project(vault, Path(os.getcwd()))
    res = search(vault, a.query, slug, a.all, a.n)
    if not res:
        print("Keine Treffer." + ("" if a.all else " Tipp: --all sucht in allen Projekten"))
        return
    for r in res:
        status = f" [{r['status']}]" if r["status"] else ""
        print(f"{r['score']:6.1f}  {r['path']}{status}")
        if r["summary"]:
            print(f"        {r['summary']}")
        if r["snippet"]:
            print(f"        > {r['snippet']}")


def cmd_check(a) -> None:
    vault = vault_root()
    if a.slug:
        slugs = [a.slug]
    elif a.all:
        slugs = list(load_registry(vault)["projects"])
    else:
        slug, _, _ = detect_project(vault, Path(os.getcwd()))
        slugs = [slug] if slug else []
    total = 0
    sections = [("global", check_global(vault))] + [(s, check_project(vault, s)) for s in slugs]
    for name, issues in sections:
        print(f"## {name}: {len(issues)} Punkte")
        for i in issues:
            print(f"- {i}")
        total += len(issues)
    if total == 0:
        print("Alles in Ordnung.")


def cmd_sync(a) -> None:
    vault = vault_root()
    if a.direction == "pull":
        _, warn, news = sync_pull(vault)
        print("\n".join(x for x in (warn, news) if x) or "ok - Vault aktuell")
    else:
        print(sync_push(vault, "manuell") or "ok")


# --------------------------------------------------------------------------- Hooks
#
# Grundsatz: Hinweise gehen als additionalContext mit dem nächsten Tool-Ergebnis oder der nächsten
# Nutzer-Nachricht mit; Stop und SessionEnd speichern nur. Zwei Ausnahmen sperren per PreToolUse:
# Arbeitswerkzeuge vor der Suche (search_gate) und der Abschluss bei unverändertem Arbeitsstand.

def hook_payload() -> dict:
    # Claude Code schickt UTF-8; unter Windows wäre sys.stdin cp1252 -> Umlaute in Pfaden kaputt.
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, OSError, AttributeError):
        return {}


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def emit_context(event: str, texts: list[str]) -> None:
    texts = [t for t in texts if t]
    if texts:
        emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": "\n\n".join(texts)}})


def hook_session_start(payload: dict) -> None:
    vault = vault_root(required=False)
    if not vault:
        ctx = ("# code-brain ist installiert, aber noch nicht eingerichtet\n"
               "Frage den Nutzer einmal nach dem privaten Git-Repo für das Memory und dem lokalen "
               "Vault-Ordner, dann richte es ein (Anleitung im Skill `code-brain`, Abschnitt Einrichtung):\n"
               f'`{cli_cmd()} init --vault "<ordner>" --remote "<git-url>"`')
        emit_context("SessionStart", [ctx])
        return
    cwd = Path(payload.get("cwd") or os.getcwd())
    source = payload.get("source", "startup") or "startup"
    notes = []
    # Jeder Start (neu, fortgesetzt, nach /clear oder Komprimierung) gleicht zuerst mit anderen Geräten ab.
    pull_ok, warn, news = sync_pull(vault)
    if warn:
        notes.append(warn)
    sync_info = ""
    if pull_ok and vault_has_remote(vault):
        sync_info = (f"Vault-Sync {dt.datetime.now():%H:%M}: "
                     + (news + "." if news else "aktuell, keine neuen Einträge anderer Geräte."))
    earlier = read_text(last_sync_file(vault)).strip()
    if earlier:
        notes.append(f"Letzter Sync: {earlier}")
    slug, _, _ = detect_project(vault, cwd)
    sid = payload.get("session_id", "")
    st = load_state(vault, sid)
    if slug:
        for t in stale_tasks(vault, slug, sid):
            when = dt.datetime.fromtimestamp(float(t["task"].get("started", 0))).strftime("%Y-%m-%d %H:%M")
            notes.append(f"Die Aufgabe „{t['task'].get('title', '?')}“ (gestartet {when}) wurde nicht abgeschlossen. "
                         "Prüfe `Arbeitsstand.md` und das Arbeitspaket gegen Git (Branch, Commits, PR) und trag "
                         "den echten Stand ein.")
            t["state"]["task"]["open"] = False          # nur einmal melden
            write_text(t["file"], json.dumps(t["state"], indent=1, ensure_ascii=False))
    if source == "compact":
        notes.append("Das Gespräch wurde eben komprimiert. Prüfe, ob `Arbeitsstand.md` und das Arbeitspaket "
                     "alles aus der Zusammenfassung enthalten (Entscheidungen, Stand, nächste Schritte), und trag "
                     "Fehlendes nach.")
    if source in ("startup", "clear") or not st:
        st = {"slug": slug, "vault_fp": vault_fingerprint(vault, slug), "prompts": 0, "doc_at": 0,
              "awaiting_stop": False, "task": {}, "stand_mark": stand_mark(vault, slug)}
    else:
        st.update({"slug": slug, "ended": False})
    st.update({"pull_ok": pull_ok, "pulled_at": now_ts() if pull_ok else 0})
    save_state(vault, sid, st)
    prune_states(vault)
    emit_context("SessionStart", [build_context(vault, cwd, notes, sync_info)])


def note_vault_activity(vault: Path, slug: str | None, st: dict) -> None:
    """Vault geändert seit dem letzten Blick -> Zähler für die Erinnerung zurücksetzen."""
    fp = vault_fingerprint(vault, slug)
    if fp != st.get("vault_fp"):
        st["vault_fp"] = fp
        st["doc_at"] = st.get("prompts", 0)
        st["dirty"] = True


def pull_during_session(vault: Path, slug: str | None, st: dict) -> list[str]:
    """Holt Einträge anderer Geräte nach: nach gescheitertem Start-Pull bei jeder Nachricht, sonst alle PULL_EVERY s."""
    was_ok = st.get("pull_ok", True)
    if was_ok and now_ts() - float(st.get("pulled_at", 0)) < PULL_EVERY:
        return []
    ok, warn, news = sync_pull(vault)
    # Vom Remote geholte Notizen zählen nicht als eigener Eintrag dieser Session.
    st["vault_fp"] = vault_fingerprint(vault, slug)
    st["pull_ok"] = ok
    if ok:
        st["pulled_at"] = now_ts()
    hints = []
    if warn and (ok or was_ok):             # Offline-Warnung nur beim Wechsel, nicht bei jeder Nachricht
        hints.append(f"code-brain: {warn}")
    if news:
        hints.append(f"code-brain: {news}. Betroffene Notizen neu lesen; der Startkontext dieser Session ist "
                     "dafür veraltet.")
    elif ok and not was_ok:
        hints.append("code-brain: Vault jetzt mit anderen Geräten abgeglichen, keine neuen Einträge.")
    return hints


def hook_user_prompt(payload: dict) -> None:
    vault = vault_root(required=False)
    if not vault:
        return
    cwd = Path(payload.get("cwd") or os.getcwd())
    sid = payload.get("session_id", "")
    slug, _, _ = detect_project(vault, cwd)
    st = load_state(vault, sid)
    st["slug"] = slug
    hints = st.pop("pending", [])
    note_vault_activity(vault, slug, st)
    hints += pull_during_session(vault, slug, st)
    if slug:
        base = project_dir(vault, slug)
        if st.get("awaiting_stop"):
            hints.append("code-brain: Die letzte Antwort wurde abgebrochen. Prüfe kurz, ob `Arbeitsstand.md` und "
                         "das Arbeitspaket den tatsächlichen Stand zeigen, und korrigiere sie bei Bedarf.")
        since = st.get("prompts", 0) - st.get("doc_at", 0)
        if since >= NUDGE_AFTER_PROMPTS:
            hints.append(f"code-brain: Seit {since} Nachrichten kein Eintrag im Vault (`{base.as_posix()}`). Prüfe, ob seitdem "
                         "Entscheidungen, gestrichene Vorhaben, neue Aufgaben oder Wissen entstanden sind, und trag "
                         "sie am richtigen Ort ein. Nichts davon → nichts tun.")
            st["doc_at"] = st.get("prompts", 0)
    st["prompts"] = st.get("prompts", 0) + 1
    st["awaiting_stop"] = True
    save_state(vault, sid, st)
    emit_context("UserPromptSubmit", hints)


def plan_items(tool: str, ti: dict) -> tuple[list[str], bool] | None:
    """(Titel der Schritte, alle erledigt) oder None, wenn der Aufruf keine neue Liste enthält."""
    if tool == "TodoWrite":
        todos = ti.get("todos") or []
        items = [str(t.get("content", "")) for t in todos if isinstance(t, dict)]
        done = bool(todos) and all(isinstance(t, dict) and t.get("status") == "completed" for t in todos)
        return items, done
    if tool == "mcp__ccd_turn__update_plan":
        if not ti.get("items"):
            return None                     # nur advance/done/start - keine neue Liste
        items = [str(i.get("content", "")) for i in ti["items"] if isinstance(i, dict)]
        done = all(isinstance(i, dict) and i.get("status") == "completed" for i in ti["items"])
        return items, done
    if tool == "TaskCreate":
        return [str(ti.get("subject") or ti.get("title") or ti.get("description") or "Aufgabe")], False
    return None


def start_hint(slug: str, base: Path, title: str) -> str:
    return (f"code-brain: Neue Aufgabe „{title}“ im Projekt `{slug}`. Bash, Edit/Write und Agenten sind gesperrt "
            f'bis zur Suche: `{cli_cmd()} search "<begriffe der aufgabe>"`, Treffer lesen (Arbeitspaket, Wissen, '
            f"Projektregeln). Dann `{(base / PAKETE_DIR).as_posix()}/<nr-name>.md` anlegen oder aktualisieren (Ziel, Todos) und "
            "in `Arbeitsstand.md` › In Arbeit eintragen. Ist es keine Projektarbeit, ignorieren.")


def end_hint(base: Path) -> str:
    return (f"code-brain: Aufgabe abgeschlossen, aber `{(base / STAND_FILE).as_posix()}` ist seit dem Start unverändert. "
            "Trag jetzt ein, bevor du abschließt: im Arbeitspaket › Ergebnis (was gebaut, wo: Branch/PR/Dateien, "
            "was offen) und Status; in `Arbeitsstand.md` In Arbeit → Zuletzt abgeschlossen, Nächste Schritte. "
            "Ist nichts Dauerhaftes passiert, nichts tun.")


def write_hint(vault: Path, note: Path) -> str:
    """Formatfehler einer Vault-Notiz direkt nach dem Schreiben melden."""
    if not note.name.lower().endswith(".md"):
        return ""
    try:
        rel = note.resolve().relative_to(vault.resolve()).as_posix()
    except (ValueError, OSError):
        return ""
    if rel.startswith((SYSTEM_DIR + "/", ".")) or note.name == "README.md":
        return ""
    text = read_text(note)
    meta, _ = parse_frontmatter(text)
    problems = []
    if rel.startswith((PROJECTS_DIR + "/", GLOBAL_DIR + "/")):
        missing = [k for k in ("type", "summary", "updated") if not meta.get(k)]
        if f"/{PAKETE_DIR}/" in rel and not meta.get("status"):
            missing.append("status")
        if missing:
            problems.append(f"Frontmatter fehlt: {', '.join(missing)}")
    if f"/{PAKETE_DIR}/" in rel or f"/{WISSEN_DIR}/" in rel:
        if not meta.get("stichworte"):
            problems.append("`stichworte` fehlen (Suchbegriffe, damit spätere Agenten die Notiz finden)")
    for name in LEGACY:
        if f"/{name}/" in "/" + rel + "/" and rel.startswith(PROJECTS_DIR + "/"):
            problems.append(f"`{name}` gehört zur alten Struktur - stattdessen Projekt.md, Arbeitsstand.md, "
                            "arbeitspakete/ oder wissen/ nutzen")
            break
    for tag in as_list(meta.get("tags")):
        if not re.fullmatch(r"[\w/-]*[A-Za-z_/-][\w/-]*", str(tag)):
            problems.append(f"ungültiger Tag `{tag}` (nur Buchstaben, Ziffern, _ - /, ohne #)")
    if SECRET_RE.search(text):
        problems.append("enthält etwas, das wie ein Passwort/Schlüssel aussieht - durch Verweis ersetzen "
                        "(sonst stoppt der Vault-Commit)")
    return f"code-brain: `{rel}` korrigieren: " + "; ".join(problems) if problems else ""


def hook_post_tool(payload: dict) -> None:
    vault = vault_root(required=False)
    if not vault:
        return
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    cwd = Path(payload.get("cwd") or os.getcwd())
    sid = payload.get("session_id", "")
    hints: list[str] = []

    if tool in WRITE_TOOLS:
        fp = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
        if not fp:
            return
        h = write_hint(vault, Path(fp))
        if h:
            hints.append(h)
        else:
            try:
                Path(fp).resolve().relative_to(vault.resolve())
                inside = True
            except (ValueError, OSError):
                inside = False
            if not inside and not is_generic_dir(cwd):
                slug, _, _ = detect_project(vault, cwd)
                st = load_state(vault, sid)
                if not slug and not st.get("asked_new"):
                    st["asked_new"] = True
                    save_state(vault, sid, st)
                    hints.append("code-brain: In diesem Ordner wird Code geändert, aber er gehört zu keinem Projekt "
                                 f'im Vault. Lege es an (`{cli_cmd()} new --name "<Projektname>" --cwd "{cwd}"`) oder '
                                 "ordne ihn mit `link <slug>` zu. Gehört die Arbeit bewusst zu keinem Projekt, ignorieren.")
        emit_context("PostToolUse", hints)
        return

    parsed = plan_items(tool, ti)
    if parsed is None:
        return
    slug, _, _ = detect_project(vault, cwd)
    if not slug:
        return
    base = project_dir(vault, slug)
    st = load_state(vault, sid)
    items, all_done = parsed
    task = st.get("task") or {}
    if all_done and items:
        if task.get("open") and stand_mark(vault, slug) == task.get("stand_mark"):
            hints.append(end_hint(base))
        st["task"] = {**task, "open": False}
    else:
        old = set(task.get("items") or [])
        new = set(items)
        fresh = not task.get("open") or (tool != "TaskCreate" and len(old & new) * 2 < max(len(new), 1))
        if fresh:
            st["task"] = {"open": True, "title": (items[0] if items else "Aufgabe")[:80], "items": items,
                          "started": now_ts(), "stand_mark": stand_mark(vault, slug)}
            hints.append(start_hint(slug, base, st["task"]["title"]))
        elif tool != "TaskCreate":
            st["task"]["items"] = items
    hints = st.pop("pending", []) + hints
    save_state(vault, sid, st)
    emit_context("PostToolUse", hints)


def is_vault_cmd(ti: dict) -> bool:
    return "vault.py" in str(ti.get("command", ""))


def is_search_cmd(ti: dict) -> bool:
    return bool(re.search(r"vault\.py[\"']?\s+search\b", str(ti.get("command", ""))))


def search_gate(vault: Path, sid: str, tool: str, ti: dict) -> None:
    """Nach einer neuen Aufgabenliste Arbeitswerkzeuge sperren, bis `vault.py search` gelaufen ist."""
    st = load_state(vault, sid)
    if tool in SHELL_TOOLS and is_search_cmd(ti):
        st["last_search"] = now_ts()
        save_state(vault, sid, st)
        return
    if tool in SHELL_TOOLS and is_vault_cmd(ti):
        return                          # check, new, link usw. bleiben frei
    task = st.get("task") or {}
    if not task.get("open") or not st.get("slug"):
        return
    if float(st.get("last_search", 0)) >= float(task.get("started", 0)) - SEARCH_GRACE:
        return
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                 "permissionDecisionReason":
                                     f"code-brain: Gesperrt, bis der Vault durchsucht ist. Zuerst "
                                     f'`{cli_cmd()} search "<begriffe der aufgabe>"` ausführen und die Treffer '
                                     "lesen (falls noch nicht geschehen, vorher den Skill `code-brain` laden). "
                                     "Danach diesen Aufruf wiederholen."}})


def hook_pre_tool(payload: dict) -> None:
    """Sperre vor der Suche (search_gate) und Abschluss (wrap_up): Ist Arbeitsstand.md seit dem
    Aufgabenstart unverändert, einmal zurückhalten, damit das Eintragen vor der Abschlussmeldung passiert."""
    vault = vault_root(required=False)
    if not vault:
        return
    tool = payload.get("tool_name", "")
    sid = payload.get("session_id", "")
    if tool in GATE_TOOLS:
        search_gate(vault, sid, tool, payload.get("tool_input") or {})
        return
    if tool not in END_TOOLS:
        return
    cwd = Path(payload.get("cwd") or os.getcwd())
    slug, _, _ = detect_project(vault, cwd)
    if not slug:
        return
    st = load_state(vault, sid)
    task = st.get("task") or {}
    if not task.get("open"):
        return                          # keine Aufgabe offen (reines Gespräch) oder schon erinnert
    st["task"] = {**task, "open": False}
    save_state(vault, sid, st)
    if stand_mark(vault, slug) == task.get("stand_mark"):
        emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": end_hint(project_dir(vault, slug))
                                     + " Danach den Abschluss erneut aufrufen."}})


def settle(vault: Path, slug: str | None, st: dict, label: str) -> None:
    """Vault committen und pushen, wenn sich etwas geändert hat; Probleme für die nächste Nachricht merken."""
    note_vault_activity(vault, slug, st)
    if not st.pop("dirty", False) and not st.get("unsynced"):
        return
    msg = sync_push(vault, label)
    remember_sync(vault, msg)
    st["unsynced"] = bool(msg)
    if msg.startswith("Commit gestoppt"):
        st.setdefault("pending", []).append(f"code-brain: {msg}")


def hook_stop(payload: dict) -> None:
    """Nach jeder Antwort: nur speichern. Blockiert nie."""
    vault = vault_root(required=False)
    if not vault:
        return
    sid = payload.get("session_id", "")
    slug, _, _ = detect_project(vault, Path(payload.get("cwd") or os.getcwd()))
    st = load_state(vault, sid)
    st["awaiting_stop"] = False
    settle(vault, slug, st, slug or "global")
    save_state(vault, sid, st)


def hook_pre_compact(payload: dict) -> None:
    vault = vault_root(required=False)
    if not vault:
        return
    sid = payload.get("session_id", "")
    slug, _, _ = detect_project(vault, Path(payload.get("cwd") or os.getcwd()))
    st = load_state(vault, sid)
    settle(vault, slug, st, (slug or "global") + " vor komprimierung")
    save_state(vault, sid, st)


def hook_session_end(payload: dict) -> None:
    vault = vault_root(required=False)
    if not vault:
        return
    sid = payload.get("session_id", "")
    slug, _, _ = detect_project(vault, Path(payload.get("cwd") or os.getcwd()))
    st = load_state(vault, sid)
    st["ended"] = True
    st["dirty"] = True                  # am Ende immer einmal synchronisieren
    settle(vault, slug, st, (slug or "global") + " session-ende")
    save_state(vault, sid, st)


HOOKS = {"session-start": hook_session_start, "user-prompt": hook_user_prompt, "pre-tool": hook_pre_tool,
         "post-tool": hook_post_tool, "post-write": hook_post_tool,   # post-write: alter Name, laufende Sessions
         "stop": hook_stop, "pre-compact": hook_pre_compact, "session-end": hook_session_end}


def cmd_hook(a) -> None:
    payload = hook_payload()
    try:
        HOOKS[a.event](payload)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - ein Hook darf die Session nie blockieren
        print(f"code-brain Hook-Fehler ({a.event}): {e}", file=sys.stderr)


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="vault.py", description="code-brain Projekt-Memory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--vault", required=True); p.add_argument("--remote")
    p.add_argument("--gh-user", help="gh-Konto für den Vault-Zugriff, falls nicht das aktive Konto")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("status"); p.add_argument("--cwd"); p.set_defaults(func=cmd_status)
    p = sub.add_parser("new"); p.add_argument("--name", required=True); p.add_argument("--slug")
    p.add_argument("--cwd"); p.set_defaults(func=cmd_new)
    p = sub.add_parser("link"); p.add_argument("slug"); p.add_argument("--cwd"); p.set_defaults(func=cmd_link)
    p = sub.add_parser("context"); p.add_argument("--cwd"); p.set_defaults(func=cmd_context)
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--all", action="store_true")
    p.add_argument("-n", type=int, default=8); p.set_defaults(func=cmd_search)
    p = sub.add_parser("check"); p.add_argument("slug", nargs="?"); p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("sync"); p.add_argument("direction", choices=["pull", "push"]); p.set_defaults(func=cmd_sync)
    p = sub.add_parser("hook"); p.add_argument("event", choices=sorted(HOOKS)); p.set_defaults(func=cmd_hook)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
