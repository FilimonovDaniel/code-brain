#!/usr/bin/env python3
"""code-brain: automatisches Projekt-Memory in einem zentralen Obsidian-Vault.

Nur Python-Standardbibliothek. Ein Skript für alles:

  vault.py init --vault <pfad> [--remote <git-url>]   Vault einrichten (klonen oder neu anlegen)
  vault.py status [--cwd <ordner>]                    Konfiguration und Projekterkennung zeigen
  vault.py new --name "<Name>" [--slug s] [--cwd d]   Projekt für einen Ordner anlegen und registrieren
  vault.py link <slug> [--cwd d]                      weiteren Ordner/Remote einem Projekt zuordnen
  vault.py context [--cwd d]                          Startkontext ausgeben (das, was der Hook einspielt)
  vault.py index [<slug>]                             Index.md neu erzeugen
  vault.py search "<text>" [--all] [--archiv] [-n 8]  Stichwortsuche mit Ranking (Notnagel)
  vault.py check [<slug>] [--fix]                     Pflege: Format, Links, Größe, Ausmisten
  vault.py archive <datei>                            Notiz ins Archiv des Projekts verschieben
  vault.py sync pull|push                             Vault mit dem Git-Remote abgleichen
  vault.py hook session-start|stop|session-end        Einstieg für die Claude-Code-Hooks (JSON auf stdin)

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
NUDGE_AFTER_PROMPTS = 8          # Stop-Hook erinnert nach so vielen Nachrichten ohne Doku
ARCHIVE_AFTER_DAYS = 14          # erledigte Todos/Features wandern danach ins Archiv
STAND_MAX_CHARS = 3500
GIT_TIMEOUT = 12

PROJECTS_DIR = "projekte"
SYSTEM_DIR = "_brain"
STATE_DIR = ".brain-state"
CORE_FILES = ("Stand.md", "Plan.md", "Todos.md", "Index.md")
NOTE_DIRS = ("entscheidungen", "features", "wissen", "sessions")

ACTIVE_FEATURE = {"geplant", "in-arbeit", "review", "blockiert"}
DONE_STATUS = {"fertig", "erledigt", "verworfen", "ersetzt", "archiviert"}


# --------------------------------------------------------------------------- Grundlagen

def today() -> str:
    return dt.date.today().isoformat()


def now_hm() -> str:
    return dt.datetime.now().strftime("%H:%M")


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
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
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


def set_frontmatter_field(text: str, key: str, value: str) -> str:
    m = FM_RE.match(text)
    if not m:
        return f"---\n{key}: {value}\n---\n" + text
    block = m.group(1)
    if re.search(rf"^{re.escape(key)}:.*$", block, re.M):
        block = re.sub(rf"^{re.escape(key)}:.*$", f"{key}: {value}", block, flags=re.M)
    else:
        block += f"\n{key}: {value}"
    return f"---\n{block}\n---\n" + text[m.end():]


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
    """Eine JSON-Datei pro Projekt: zwei Geraete, die gleichzeitig Projekte anlegen, kollidieren nicht."""
    return vault / SYSTEM_DIR / "projekte"


def load_registry(vault: Path) -> dict:
    projects = {}
    d = registry_dir(vault)
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            try:
                projects[p.stem] = json.loads(read_text(p) or "{}")
            except json.JSONDecodeError:
                print(f"code-brain: {p} ist kein gueltiges JSON - uebersprungen", file=sys.stderr)
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
    """(slug, eintrag, wie) - zuerst über Git-Remote, dann über den längsten passenden Pfad."""
    reg = load_registry(vault)
    gi = git_info(cwd)
    if gi["remote"]:
        for slug, p in reg["projects"].items():
            if gi["remote"] in [norm_remote(r) for r in as_list(p.get("remotes"))]:
                return slug, p, f"Git-Remote {gi['remote']}"
    here = norm_path(cwd)
    best, best_len = None, -1
    for slug, p in reg["projects"].items():
        for path in as_list(p.get("paths")):
            np_ = norm_path(path)
            if (here == np_ or here.startswith(np_ + "/")) and len(np_) > best_len:
                best, best_len = slug, len(np_)
    if best:
        return best, reg["projects"][best], "Ordner"
    return None, {}, ""


def project_dir(vault: Path, slug: str) -> Path:
    return vault / PROJECTS_DIR / slug


# --------------------------------------------------------------------------- Vorlagen

def render_template(template: str, **values) -> str:
    text = read_text(TEMPLATES / template)
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


# --------------------------------------------------------------------------- Notizen einsammeln

def iter_notes(base: Path, include_archive: bool = False):
    for p in sorted(base.rglob("*.md")):
        rel = p.relative_to(base).as_posix()
        if "/." in "/" + rel or rel.startswith("."):
            continue
        if not include_archive and (rel.startswith("archiv/") or "/archiv/" in rel):
            continue
        yield p


def note_meta(p: Path) -> tuple[dict, str]:
    return parse_frontmatter(read_text(p))


def title_of(p: Path, body: str) -> str:
    m = re.search(r"^#\s+(.+)$", body, re.M)
    return m.group(1).strip() if m else p.stem


def open_todos(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if re.match(r"^\s*[-*] \[ \]", l)]


COMMENT_RE = re.compile(r"%%.*?%%", re.S)


def strip_comments(text: str) -> str:
    """Obsidian-Kommentare (%% ... %%) sind Ausfüllhinweise - nicht in den Kontext laden."""
    return re.sub(r"\n{3,}", "\n\n", COMMENT_RE.sub("", text)).strip()


def section(body: str, heading_pattern: str) -> str:
    """Inhalt unter der ersten ##-Überschrift, die auf das Muster passt (ohne Kommentare)."""
    lines = strip_comments(body).splitlines()
    out, inside = [], False
    for line in lines:
        if re.match(r"^##\s", line):
            if inside:
                break
            inside = bool(re.search(heading_pattern, line, re.I))
            continue
        if inside:
            out.append(line)
    return "\n".join(out).strip()


# --------------------------------------------------------------------------- Index

def build_index(vault: Path, slug: str) -> str:
    base = project_dir(vault, slug)
    groups: dict[str, list[str]] = {"features": [], "entscheidungen": [], "wissen": []}
    for sub in groups:
        d = base / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            meta, body = note_meta(p)
            status = str(meta.get("status", "")).lower()
            if sub == "features" and status and status not in ACTIVE_FEATURE:
                continue
            if sub == "entscheidungen" and status in DONE_STATUS:
                continue
            link = f"{PROJECTS_DIR}/{slug}/{sub}/{p.stem}"
            summary = str(meta.get("summary", "")).strip() or "(keine summary)"
            extra = f" `{status}`" if status and sub == "features" else ""
            groups[sub].append(f"- [[{link}|{p.stem}]]{extra} - {summary}")
    lines = [
        "---", "type: index", f"project: {slug}", f"updated: {today()}",
        "summary: Automatisch erzeugt - nicht von Hand bearbeiten", "---", "",
        f"# Index {slug}", "",
        "> [!info] Wird von code-brain bei jedem Session-Start und Stop neu erzeugt.", "",
    ]
    names = {"features": "Features (aktiv)", "entscheidungen": "Entscheidungen (gültig)", "wissen": "Wissen"}
    for sub, entries in groups.items():
        lines.append(f"## {names[sub]}")
        lines.extend(entries or ["- (noch nichts)"])
        lines.append("")
    return "\n".join(lines)


def write_index(vault: Path, slug: str) -> None:
    base = project_dir(vault, slug)
    if not base.is_dir():
        return
    new = build_index(vault, slug)
    old = read_text(base / "Index.md")
    # Datum allein ist keine Änderung - sonst gäbe es täglich einen leeren Commit.
    strip = lambda t: re.sub(r"^updated:.*$", "", t, flags=re.M)  # noqa: E731
    if strip(old) != strip(new):
        write_text(base / "Index.md", new)


# --------------------------------------------------------------------------- Kontext

CORE_RULES = """\
**So arbeitest du mit code-brain (Kurzfassung, Details im Skill `code-brain`):**
- Halte den Vault während der Arbeit laufend aktuell - nach jedem sinnvollen Schritt, nicht erst am Ende. Schreib jedes Update so, als wäre es die letzte Übergabe.
- `Stand.md` wird überschrieben (Ist-Stand, nächster Schritt), nicht verlängert. Todos als Checkboxen in `Todos.md` oder der Feature-Notiz.
- Entscheidung = eigene Notiz in `entscheidungen/`; Überholtes auf `status: ersetzt`/`verworfen` setzen (wandert automatisch ins Archiv).
- Jede Notiz braucht Frontmatter mit `summary:` (eine Zeile) - daraus entsteht der Index.
- Neues Wissen (Stolperstellen, Befehle, Architektur) nach `wissen/`, kurzer Verlauf nach `sessions/JJJJ-MM-TT.md`.
- Details nicht raten: Index-Link öffnen oder `{cmd} search "<begriff>"` (auch `--archiv`)."""


def clip(text: str, limit: int, pointer: str) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n", 1)[0]
    return cut + f"\n... (gekürzt - vollständig in `{pointer}`)"


def cli_cmd() -> str:
    return f'python "{Path(__file__).resolve().as_posix()}"'


def build_context(vault: Path, cwd: Path, sync_note: str = "") -> str:
    slug, entry, how = detect_project(vault, cwd)
    cmd = cli_cmd()
    parts: list[str] = []
    if sync_note:
        parts.append(f"> [!warning] Sync\n> {sync_note}")
    _, glob_body = parse_frontmatter(read_text(vault / "global" / "Regeln.md"))
    glob_body = re.sub(r"^#\s.*\n?", "", strip_comments(glob_body), count=1).strip()
    # Reihenfolge = Wichtigkeit: wird es zu lang, wird hinten gekürzt (Index, Verlauf), nie die Regeln.
    glob_part = "## Globale Regeln\n" + clip(glob_body, 2000, "global/Regeln.md") if glob_body else ""

    if not slug and is_generic_dir(cwd):
        parts.append(
            f"# code-brain: allgemeiner Ordner (`{cwd}`) - kein Projekt-Memory\n"
            "Hier wird nichts dokumentiert. Für Projektarbeit die Session im Projektordner starten."
        )
    elif not slug:
        reg = load_registry(vault)
        listing = "\n".join(
            f"- `{s}` - {p.get('name', s)}" for s, p in sorted(reg["projects"].items())
        ) or "- (noch keine Projekte)"
        parts.append(
            f"# code-brain: kein Projekt für diesen Ordner\n"
            f"Ordner: `{cwd}` - Vault: `{vault}`\n\n"
            "Wenn in dieser Session an Code in diesem Ordner gearbeitet wird, lege das Projekt "
            "an, sobald klar ist, worum es geht (ohne Rückfrage, außer der Name ist unklar):\n"
            f'`{cmd} new --name "<Projektname>" --cwd "{cwd}"`\n'
            "Gehört der Ordner zu einem bestehenden Projekt (z. B. anderer Klon/Ordner), stattdessen:\n"
            f'`{cmd} link <slug> --cwd "{cwd}"`\n'
            "Reine Fragen ohne Projektbezug (Rezepte, Allgemeinwissen) werden nicht dokumentiert.\n\n"
            f"Bekannte Projekte:\n{listing}"
        )
    else:
        base = project_dir(vault, slug)
        rel = f"{PROJECTS_DIR}/{slug}"
        parts.append(
            f"# code-brain: Projekt {entry.get('name', slug)} (`{slug}`)\n"
            f"Erkannt über: {how} - Projektordner: `{base}`\n"
            f"Skript: `{cmd}`"
        )
        parts.append(CORE_RULES.format(cmd=cmd))
        if glob_part:
            parts.append(glob_part)
            glob_part = ""
        _, stand = parse_frontmatter(read_text(base / "Stand.md"))
        stand = re.sub(r"^#\s.*\n?", "", strip_comments(stand), count=1).strip()
        stand = re.sub(r"^(#{2,5})\s", r"#\1 ", stand, flags=re.M)     # unter "## Stand" einruecken
        parts.append("## Stand\n" + clip(stand or "(leer)", STAND_MAX_CHARS, f"{rel}/Stand.md"))

        # Verbote wirken zuverlässiger als "schau mal nach" - deshalb immer direkt im Kontext.
        _, hub = parse_frontmatter(read_text(base / f"{slug}.md"))
        dont = section(hub, r"nicht tun")
        if re.sub(r"[-\s]", "", dont):
            parts.append("## Nicht tun\n" + clip(dont, 1200, f"{rel}/{slug}.md"))

        todos = open_todos(read_text(base / "Todos.md"))
        for p in sorted((base / "features").glob("*.md")) if (base / "features").is_dir() else []:
            meta, body = note_meta(p)
            if str(meta.get("status", "")).lower() in ACTIVE_FEATURE:
                todos += [f"{t}  _({p.stem})_" for t in open_todos(body)]
        if todos:
            shown = [t if len(t) <= 150 else t[:147] + "..." for t in todos[:20]]
            more = f"\n- ... und {len(todos) - 20} weitere" if len(todos) > 20 else ""
            parts.append("## Offene Todos\n" + "\n".join(shown) + more)

        _, plan = parse_frontmatter(read_text(base / "Plan.md"))
        nxt = section(plan, r"als n.chstes|next")
        if re.sub(r"[-\s]", "", nxt):
            parts.append("## Plan - als Nächstes\n" + clip(nxt, 1200, f"{rel}/Plan.md"))

        _, idx = parse_frontmatter(read_text(base / "Index.md"))
        idx_lines, heading, group = [], "", ""
        wissen: list[str] = []
        for l in idx.splitlines():
            if l.startswith("## "):
                heading, group = "#" + l, l[3:].lower()
            elif l.startswith("- ") and "(noch nichts)" not in l:
                # Im Kontext reicht der Name - der volle Pfad ergibt sich aus Ordner + Name.
                short = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", l)
                if group.startswith("wissen"):
                    wissen.append(short[2:].split(" - ", 1)[0])   # Thema steht im Namen
                    continue
                if heading:
                    idx_lines.append(heading)
                    heading = ""
                idx_lines.append(short if len(short) <= 160 else short[:157] + "...")
        if wissen:
            idx_lines.append("### Wissen\n- " + ", ".join(wissen))
        if idx_lines:
            parts.append(f"## Index (aktive Notizen, Dateien unter `{rel}/<ordner>/<Name>.md`)\n"
                         + clip("\n".join(idx_lines), 3500, f"{rel}/Index.md"))

        sess_dir = base / "sessions"
        if sess_dir.is_dir():
            sessions = sorted(sess_dir.glob("*.md"))
            if sessions:
                _, last = parse_frontmatter(read_text(sessions[-1]))
                bullets = [re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", l)[:200]
                           for l in last.splitlines() if l.startswith("- ")][-6:]
                if bullets:
                    parts.append(f"## Letzte Session ({sessions[-1].stem})\n" + "\n".join(bullets))

    if glob_part:
        parts.append(glob_part)

    out = "\n\n".join(parts)
    # Gemessen wird die JSON-kodierte Länge - so zählt Claude Code das 10.000-Zeichen-Limit.
    while len(json.dumps(out, ensure_ascii=False)) > CONTEXT_BUDGET + 300 and len(out) > 500:
        out = out[: int(len(out) * 0.9)].rsplit("\n", 1)[0]
        out += "\n... (Kontext gekürzt - Rest NICHT geladen, im Vault lesen: Stand.md, Todos.md, Index.md)"
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


def sync_pull(vault: Path) -> str:
    """Leerer String = ok/nichts zu tun, sonst Warnung für den Kontext."""
    if not vault_has_git(vault) or not vault_has_remote(vault):
        return ""
    # Erst lokal committen: Rebase auf sauberem Stand statt Stash-Akrobatik.
    warn = commit_local(vault, "lokal vor pull")
    code, out = pull_rebase(vault)
    if code != 0:
        low = out.lower()
        if "could not resolve" in low or "unable to access" in low or "timed out" in low:
            warn = (warn + " " if warn else "") + "Vault konnte nicht aktualisiert werden (offline?) - arbeite mit lokalem Stand."
        else:
            warn = (warn + " " if warn else "") + f"git pull im Vault fehlgeschlagen - bitte prüfen: {out[:300]}"
    conflicts = conflict_files(vault)
    if conflicts:
        warn = (warn + " " if warn else "") + ("Konfliktmarker in: " + ", ".join(conflicts[:5])
                                                + " - beide Fassungen zusammenführen und Marker entfernen.")
    return warn


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


# --------------------------------------------------------------------------- Zustand für den Stop-Hook

def state_file(vault: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unbekannt")
    return vault / STATE_DIR / "sessions" / f"{safe}.json"


def load_state(vault: Path, session_id: str) -> dict:
    p = state_file(vault, session_id)
    try:
        return json.loads(read_text(p) or "{}")
    except json.JSONDecodeError:
        return {}


def save_state(vault: Path, session_id: str, state: dict) -> None:
    write_text(state_file(vault, session_id), json.dumps(state, indent=1))


def prune_states(vault: Path, days: int = 14) -> None:
    d = vault / STATE_DIR / "sessions"
    if not d.is_dir():
        return
    limit = dt.datetime.now().timestamp() - days * 86400
    for p in d.glob("*.json"):
        try:
            if p.stat().st_mtime < limit:
                p.unlink()
        except OSError:
            pass


def code_fingerprint(cwd: Path) -> str:
    """Ändert sich bei Commits und bei jeder Dateiänderung im Arbeitsbaum."""
    roots: list[Path] = []
    gi = git_info(cwd)
    if gi["toplevel"]:
        roots = [Path(gi["toplevel"])]
    else:
        # Sammelordner ohne eigenes Repo (z. B. Repo + Worktrees nebeneinander)
        try:
            roots = [p for p in sorted(cwd.iterdir()) if (p / ".git").exists()][:12]
        except OSError:
            roots = []
    h = hashlib.sha1()
    for r in roots:
        for args in (["rev-parse", "HEAD"], ["status", "--porcelain"], ["diff", "--numstat", "HEAD"]):
            _, out = run_git(args, r, timeout=8)
            h.update(out.encode("utf-8", "replace"))
    return h.hexdigest() if roots else ""


def vault_fingerprint(vault: Path, slug: str | None) -> str:
    targets = [vault / "global"]
    if slug:
        targets.append(project_dir(vault, slug))
    h = hashlib.sha1()
    for t in targets:
        if not t.is_dir():
            continue
        for p in sorted(t.rglob("*.md")):
            if p.name == "Index.md":
                continue
            try:
                st = p.stat()
                h.update(f"{p}:{st.st_mtime_ns}:{st.st_size}".encode())
            except OSError:
                pass
    return h.hexdigest()


def count_prompts(transcript: str) -> int:
    """Echte Nutzer-Nachrichten im Transkript (Tool-Ergebnisse zählen nicht)."""
    if not transcript or not Path(transcript).is_file():
        return 0
    n = 0
    try:
        with open(transcript, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"type":"user"' not in line.replace(" ", ""):
                    continue
                if "tool_use_id" in line or '"isMeta":true' in line.replace(" ", ""):
                    continue
                n += 1
    except OSError:
        return 0
    return n


# --------------------------------------------------------------------------- Pflege (check / archive)

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


def archive_note(vault: Path, slug: str, note: Path) -> Path:
    base = project_dir(vault, slug)
    rel = note.relative_to(base)
    target = base / "archiv" / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    text = read_text(note)
    text = set_frontmatter_field(text, "archiviert", today())
    write_text(target, text)
    note.unlink()
    # Pfad-Links auf die alte Stelle umbiegen; reine Namens-Links löst Obsidian weiter auf.
    old_link = f"{PROJECTS_DIR}/{slug}/{rel.as_posix()[:-3]}"
    new_link = f"{PROJECTS_DIR}/{slug}/archiv/{rel.as_posix()[:-3]}"
    for p in iter_notes(base, include_archive=True):
        t = read_text(p)
        if old_link in t:
            write_text(p, t.replace(old_link, new_link))
    return target


def move_done_todos(vault: Path, slug: str, fix: bool) -> list[str]:
    base = project_dir(vault, slug)
    todo = base / "Todos.md"
    text = read_text(todo)
    if not text:
        return []
    limit = dt.date.today() - dt.timedelta(days=ARCHIVE_AFTER_DAYS)
    keep, moved = [], []
    for line in text.splitlines():
        m = re.match(r"^\s*[-*] \[x\].*?(\d{4}-\d{2}-\d{2})", line, re.I)
        if m and dt.date.fromisoformat(m.group(1)) < limit:
            moved.append(line.strip())
        else:
            keep.append(line)
    if moved and fix:
        write_text(todo, "\n".join(keep) + "\n")
        arch = base / "archiv" / "Todos-erledigt.md"
        old = read_text(arch) or ("---\ntype: archiv\nsummary: Erledigte Todos, aus Todos.md verschoben\n---\n\n"
                                  "# Erledigte Todos\n")
        write_text(arch, old.rstrip("\n") + "\n" + "\n".join(moved) + "\n")
    return moved


def check_project(vault: Path, slug: str, fix: bool) -> list[str]:
    base = project_dir(vault, slug)
    issues: list[str] = []
    if not base.is_dir():
        return [f"Projektordner fehlt: {base}"]
    stems, paths = all_note_names(vault)
    limit = dt.date.today() - dt.timedelta(days=ARCHIVE_AFTER_DAYS)

    for p in iter_notes(base):
        rel = p.relative_to(base).as_posix()
        meta, body = note_meta(p)
        if rel.startswith("sessions/") or p.name == "Index.md":
            pass
        else:
            missing = [k for k in ("type", "summary", "updated") if not meta.get(k)]
            if missing:
                issues.append(f"{rel}: Frontmatter fehlt {', '.join(missing)}")
        for target in WIKILINK_RE.findall(body):
            t = target.strip().lower().removesuffix(".md")
            if t not in stems and t not in paths:
                issues.append(f"{rel}: toter Link [[{target.strip()}]]")
        status = str(meta.get("status", "")).lower()
        sub = rel.split("/", 1)[0]
        upd = str(meta.get("updated", ""))
        old = bool(re.match(r"\d{4}-\d{2}-\d{2}$", upd)) and dt.date.fromisoformat(upd) < limit
        if sub == "entscheidungen" and status in {"ersetzt", "verworfen"}:
            if fix:
                archive_note(vault, slug, p)
                issues.append(f"{rel}: archiviert (status {status})")
            else:
                issues.append(f"{rel}: status {status} -> archivieren")
        elif sub == "features" and status in {"fertig", "verworfen"} and (old or not upd):
            if fix:
                archive_note(vault, slug, p)
                issues.append(f"{rel}: archiviert (Feature {status})")
            else:
                issues.append(f"{rel}: Feature {status} seit >{ARCHIVE_AFTER_DAYS} Tagen -> archivieren")

    _, stand = parse_frontmatter(read_text(base / "Stand.md"))
    if len(stand) > STAND_MAX_CHARS:
        issues.append(f"Stand.md hat {len(stand)} Zeichen (Ziel < {STAND_MAX_CHARS}) - kürzen")
    moved = move_done_todos(vault, slug, fix)
    if moved:
        issues.append(f"Todos.md: {len(moved)} erledigte Todos {'archiviert' if fix else 'älter als ' + str(ARCHIVE_AFTER_DAYS) + ' Tage'}")
    return issues


# --------------------------------------------------------------------------- Suche

STOP = set("""der die das und oder nicht ein eine einer eines einem einen ist sind war wird wurde werden
mit für für von zu im in am an auf aus bei nach über über unter wie was wer wo warum wieso weshalb dass
es er sie wir ihr ich du man auch noch nur schon so dann denn als wenn aber doch mal hat haben hatte
the a an and or not is are was were be to of in on at for with from by as it this that what why how
""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[\wäöüß]+", text.lower()) if len(t) > 1 and t not in STOP]


def search(vault: Path, query: str, slug: str | None, all_projects: bool,
           include_archive: bool, limit: int) -> list[dict]:
    q = tokens(query)
    if not q:
        return []
    bases = [vault] if all_projects or not slug else [project_dir(vault, slug), vault / "global"]
    results = []
    for base in bases:
        if not base.is_dir():
            continue
        for p in iter_notes(base, include_archive=include_archive):
            rel = p.relative_to(vault).as_posix()
            if rel.startswith(SYSTEM_DIR + "/") or p.name == "Index.md":
                continue
            meta, body = note_meta(p)
            title = title_of(p, body) + " " + p.stem
            head = " ".join(re.findall(r"^#+\s+(.+)$", body, re.M))
            meta_txt = " ".join(str(v) for k, v in meta.items() if k in ("summary", "tags", "bereich", "status"))
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
            status = str(meta.get("status", "")).lower()
            if status in DONE_STATUS or "/archiv/" in "/" + rel:
                score *= 0.6
            upd = str(meta.get("updated", ""))
            if re.match(r"\d{4}-\d{2}-\d{2}$", upd):
                age = (dt.date.today() - dt.date.fromisoformat(upd)).days
                score *= 1.25 if age <= 30 else (1.0 if age <= 180 else 0.85)
            snippet = ""
            for line in body.splitlines():
                if any(t in line.lower() for t in q):
                    snippet = line.strip()[:160]
                    break
            results.append({"score": round(score, 2), "path": rel, "status": status,
                            "summary": str(meta.get("summary", "")), "snippet": snippet})
    results.sort(key=lambda r: -r["score"])
    return results[:limit]


# --------------------------------------------------------------------------- Befehle

def gh_credential_helper(user: str) -> str:
    """Git-Credential-Helper, der das Token eines bestimmten gh-Kontos liefert.
    So nutzt nur der Vault dieses Konto - das global aktive gh-Konto bleibt unberuehrt."""
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
        # deshalb fuer dieses Repo ebenfalls ueberschreiben.
        run_git(["config", "--replace-all", "credential.https://github.com.helper", ""], vault)
        run_git(["config", "--add", "credential.https://github.com.helper", gh_credential_helper(a.gh_user)], vault)
    for d in (SYSTEM_DIR, PROJECTS_DIR, "global"):
        (vault / d).mkdir(exist_ok=True)
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
    for keep in (vault / PROJECTS_DIR, registry_dir(vault)):
        keep.mkdir(parents=True, exist_ok=True)
        if not any(keep.iterdir()):
            write_text(keep / ".gitkeep", "")
    if not (vault / "README.md").exists():
        write_text(vault / "README.md", render_template("vault-README.md", date=today()))
    if not (vault / "global" / "Regeln.md").exists():
        write_text(vault / "global" / "Regeln.md", render_template("Regeln.md", date=today()))
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
    files = {f"{slug}.md": "Projekt.md", "Stand.md": "Stand.md", "Plan.md": "Plan.md", "Todos.md": "Todos.md"}
    for target, tpl in files.items():
        if not (base / target).exists():
            write_text(base / target, render_template(tpl, **values))
    for d in NOTE_DIRS:
        (base / d).mkdir(parents=True, exist_ok=True)
    write_index(vault, slug)
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


def resolve_slug(vault: Path, given: str | None, cwd: str | None = None) -> str:
    if given:
        return given
    slug, _, _ = detect_project(vault, Path(cwd or os.getcwd()))
    if not slug:
        sys.exit("Kein Projekt für diesen Ordner - Slug angeben.")
    return slug


def cmd_index(a) -> None:
    vault = vault_root()
    slug = resolve_slug(vault, a.slug)
    write_index(vault, slug)
    print(f"Index aktualisiert: {project_dir(vault, slug) / 'Index.md'}")


def cmd_search(a) -> None:
    vault = vault_root()
    slug, _, _ = detect_project(vault, Path(os.getcwd()))
    res = search(vault, a.query, slug, a.all, a.archiv, a.n)
    if not res:
        print("Keine Treffer." + ("" if a.archiv else " Tipp: --archiv oder --all"))
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
    slugs = [a.slug] if a.slug else ([resolve_slug(vault, None)] if not a.all else
                                     list(load_registry(vault)["projects"]))
    total = 0
    for slug in slugs:
        issues = check_project(vault, slug, a.fix)
        if a.fix:
            write_index(vault, slug)
        print(f"## {slug}: {len(issues)} Punkte")
        for i in issues:
            print(f"- {i}")
        total += len(issues)
    if total == 0:
        print("Alles in Ordnung.")


def cmd_archive(a) -> None:
    vault = vault_root()
    note = Path(a.file).resolve()
    try:
        rel = note.relative_to((vault / PROJECTS_DIR).resolve())
    except ValueError:
        sys.exit("Datei liegt nicht in einem Projektordner des Vaults.")
    slug = rel.parts[0]
    target = archive_note(vault, slug, note)
    write_index(vault, slug)
    print(f"Archiviert: {target}")


def cmd_sync(a) -> None:
    vault = vault_root()
    msg = sync_pull(vault) if a.direction == "pull" else sync_push(vault, "manuell")
    print(msg or "ok")


# --------------------------------------------------------------------------- Hooks

def hook_payload() -> dict:
    # Claude Code schickt UTF-8; unter Windows wäre sys.stdin cp1252 -> Umlaute in Pfaden kaputt.
    try:
        raw = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
        return json.loads(raw or "{}")
    except (json.JSONDecodeError, OSError, AttributeError):
        return {}


def hook_session_start(payload: dict) -> None:
    vault = vault_root(required=False)
    if not vault:
        ctx = ("# code-brain ist installiert, aber noch nicht eingerichtet\n"
               "Frage den Nutzer einmal nach dem privaten Git-Repo für das Memory und dem lokalen "
               "Vault-Ordner, dann richte es ein (Anleitung im Skill `code-brain`, Abschnitt Einrichtung):\n"
               f'`{cli_cmd()} init --vault "<ordner>" --remote "<git-url>"`')
    else:
        cwd = Path(payload.get("cwd") or os.getcwd())
        note = sync_pull(vault) if payload.get("source", "startup") in ("startup", "clear", "") else ""
        slug, _, _ = detect_project(vault, cwd)
        if slug:
            try:
                check_project(vault, slug, fix=True)   # Veraltetes still ins Archiv
                write_index(vault, slug)
            except Exception as e:  # noqa: BLE001 - Pflege darf den Start nie verhindern
                print(f"code-brain Pflege: {e}", file=sys.stderr)
        sid = payload.get("session_id", "")
        st = load_state(vault, sid)
        st.update({"slug": slug, "code_fp": code_fingerprint(cwd),
                   "vault_fp": vault_fingerprint(vault, slug),
                   "doc_prompts": count_prompts(payload.get("transcript_path", ""))})
        save_state(vault, sid, st)
        prune_states(vault)
        earlier = read_text(last_sync_file(vault)).strip()
        note = " ".join(x for x in (note, f"Letzter Sync: {earlier}" if earlier else "") if x)
        ctx = build_context(vault, cwd, note)
    emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}})


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


def block(reason: str) -> None:
    emit({"decision": "block", "reason": reason})


def hook_stop(payload: dict) -> None:
    vault = vault_root(required=False)
    if not vault:
        return
    cwd = Path(payload.get("cwd") or os.getcwd())
    sid = payload.get("session_id", "")
    active = bool(payload.get("stop_hook_active"))
    slug, _, _ = detect_project(vault, cwd)
    st = load_state(vault, sid)
    code_fp = code_fingerprint(cwd)
    vault_fp = vault_fingerprint(vault, slug)
    prompts = count_prompts(payload.get("transcript_path", ""))

    def settle() -> None:
        if slug:
            auto_session_line(vault, slug)
        st.update({"slug": slug, "code_fp": code_fp, "vault_fp": vault_fingerprint(vault, slug),
                   "doc_prompts": prompts})
        save_state(vault, sid, st)
        if slug:
            write_index(vault, slug)
        msg = sync_push(vault, slug or "global")
        remember_sync(vault, msg)
        if msg.startswith("Commit gestoppt") and not active:
            block(f"code-brain: {msg}")

    if not slug:
        if st.get("vault_fp") != vault_fp:
            settle()          # Vault wurde trotzdem geändert (z. B. globale Regeln)
            return
        start_fp = st.get("code_fp")
        if (not active and start_fp and code_fp and code_fp != start_fp
                and not st.get("asked_new") and not is_generic_dir(cwd)):
            st["asked_new"] = True       # nur einmal pro Session fragen
            save_state(vault, sid, st)
            block("code-brain: In diesem Ordner wird Code geändert, aber es gibt noch kein Projekt im Vault. "
                  f'Lege es jetzt an (`{cli_cmd()} new --name "<Projektname>" --cwd "{cwd}"`) oder ordne den '
                  "Ordner mit `link <slug>` einem bestehenden Projekt zu, und dokumentiere den bisherigen Stand. "
                  "Gehört die Arbeit bewusst zu keinem Projekt, beende ohne Ausgabe.")
        return
    if active or st.get("vault_fp") != vault_fp or not st:
        settle()
        return

    base = project_dir(vault, slug)
    session_note = base / "sessions" / f"{today()}.md"
    if st.get("code_fp") and code_fp and code_fp != st.get("code_fp"):
        reason = (
            f"code-brain: Der Code im Projekt `{slug}` hat sich geändert, die Projektnotizen noch nicht. "
            f"Aktualisiere jetzt den Vault in `{base}` - so, als wäre es die letzte Übergabe: "
            "`Stand.md` (Ist-Stand, nächster Schritt), betroffene Feature-Notiz und Todos, neue oder "
            f"überholte Entscheidungen, Wissen; kurzer Eintrag in `{session_note}`. "
            "Keine Rückfrage, danach nur eine kurze Zeile ausgeben."
        )
        block(reason)
        return
    if prompts - int(st.get("doc_prompts", 0)) >= NUDGE_AFTER_PROMPTS:
        reason = (
            f"code-brain: Seit {NUDGE_AFTER_PROMPTS} Nachrichten keine Doku im Projekt `{slug}`. Prüfe kurz, "
            "ob seitdem Entscheidungen, Pläne, Todos oder neues Wissen entstanden sind, und trage sie in den "
            f"Vault (`{base}`) ein. Gibt es nichts, beende ohne Ausgabe."
        )
        st["doc_prompts"] = prompts      # nur einmal erinnern
        save_state(vault, sid, st)
        block(reason)
        return
    settle()


def changed_vault_notes(vault: Path, slug: str) -> list[str]:
    """Seit dem letzten Vault-Commit geaenderte Notizen des Projekts (relativ zum Projektordner)."""
    if not vault_has_git(vault):
        return []
    _, out = run_git(["status", "--porcelain", "-z", "--untracked-files=all", "--",
                      f"{PROJECTS_DIR}/{slug}"], vault)
    prefix = f"{PROJECTS_DIR}/{slug}/"
    files = []
    entries = out.split("\0")
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        if e[0] in "RC":
            i += 1                      # Umbenennung: Quellpfad folgt als eigener Eintrag
        if "D" in e[:2]:
            continue                    # gelöschte/archivierte Notizen nicht verlinken
        path = e[3:]
        if path.startswith(prefix) and path.endswith(".md"):
            files.append(path[len(prefix):])
    return files


def auto_session_line(vault: Path, slug: str) -> None:
    """Verlauf ohne Tokenkosten: hat Claude in dieser Antwort keine Session-Zeile geschrieben,
    haengt der Hook eine an, die auf die geaenderten Notizen verlinkt."""
    changed = changed_vault_notes(vault, slug)
    today_rel = f"sessions/{today()}.md"
    notes = [c for c in changed if c != "Index.md" and not c.startswith(("sessions/", "archiv/"))]
    if not notes or today_rel in changed:
        return
    links = ", ".join(f"[[{PROJECTS_DIR}/{slug}/{n[:-3]}|{Path(n).stem}]]" for n in notes[:8])
    more = f" (+{len(notes) - 8})" if len(notes) > 8 else ""
    p = project_dir(vault, slug) / today_rel
    text = read_text(p) or f"---\ntype: session\nsummary: Verlauf {today()}\n---\n\n# {today()}\n"
    write_text(p, text.rstrip("\n") + f"\n- {now_hm()} aktualisiert: {links}{more}\n")


def hook_session_end(payload: dict) -> None:
    vault = vault_root(required=False)
    if not vault:
        return
    slug, _, _ = detect_project(vault, Path(payload.get("cwd") or os.getcwd()))
    if slug:
        auto_session_line(vault, slug)
        write_index(vault, slug)
    remember_sync(vault, sync_push(vault, (slug or "global") + " session-ende"))


def hook_post_write(payload: dict) -> None:
    """Direktes Feedback, wenn eine Vault-Notiz nicht dem Format entspricht (blockiert nichts)."""
    vault = vault_root(required=False)
    if not vault:
        return
    ti = payload.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("path") or ""
    if not fp.lower().endswith(".md"):
        return
    note = Path(fp)
    try:
        rel = note.resolve().relative_to(vault.resolve()).as_posix()
    except (ValueError, OSError):
        return
    if rel.startswith((SYSTEM_DIR + "/", ".")) or note.name in ("Index.md", "README.md"):
        return
    text = read_text(note)
    problems = []
    meta, body = parse_frontmatter(text)
    in_project = rel.startswith(PROJECTS_DIR + "/")
    if in_project and "/sessions/" not in rel:
        missing = [k for k in ("type", "summary", "updated") if not meta.get(k)]
        if missing:
            problems.append(f"Frontmatter fehlt: {', '.join(missing)} (summary = eine Zeile für den Index)")
    for tag in as_list(meta.get("tags")):
        if not re.fullmatch(r"[\w/-]*[A-Za-z_/-][\w/-]*", str(tag)):
            problems.append(f"ungültiger Tag `{tag}` (nur Buchstaben, Ziffern, _ - /, ohne #)")
    if SECRET_RE.search(text):
        problems.append("enthält etwas, das wie ein Passwort/Schlüssel aussieht - durch Verweis ersetzen "
                        "(sonst stoppt der Vault-Commit)")
    if problems:
        block(f"code-brain: `{rel}` korrigieren: " + "; ".join(problems))


def cmd_hook(a) -> None:
    payload = hook_payload()
    try:
        {"session-start": hook_session_start, "stop": hook_stop,
         "session-end": hook_session_end, "post-write": hook_post_write}[a.event](payload)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 - ein Hook darf die Session nie blockieren
        print(f"code-brain Hook-Fehler ({a.event}): {e}", file=sys.stderr)


# --------------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="vault.py", description="code-brain Projekt-Memory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("--vault", required=True); p.add_argument("--remote")
    p.add_argument("--gh-user", help="gh-Konto fuer den Vault-Zugriff, falls nicht das aktive Konto")
    p.set_defaults(func=cmd_init)
    p = sub.add_parser("status"); p.add_argument("--cwd"); p.set_defaults(func=cmd_status)
    p = sub.add_parser("new"); p.add_argument("--name", required=True); p.add_argument("--slug")
    p.add_argument("--cwd"); p.set_defaults(func=cmd_new)
    p = sub.add_parser("link"); p.add_argument("slug"); p.add_argument("--cwd"); p.set_defaults(func=cmd_link)
    p = sub.add_parser("context"); p.add_argument("--cwd"); p.set_defaults(func=cmd_context)
    p = sub.add_parser("index"); p.add_argument("slug", nargs="?"); p.set_defaults(func=cmd_index)
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--all", action="store_true")
    p.add_argument("--archiv", action="store_true"); p.add_argument("-n", type=int, default=8)
    p.set_defaults(func=cmd_search)
    p = sub.add_parser("check"); p.add_argument("slug", nargs="?"); p.add_argument("--fix", action="store_true")
    p.add_argument("--all", action="store_true"); p.set_defaults(func=cmd_check)
    p = sub.add_parser("archive"); p.add_argument("file"); p.set_defaults(func=cmd_archive)
    p = sub.add_parser("sync"); p.add_argument("direction", choices=["pull", "push"]); p.set_defaults(func=cmd_sync)
    p = sub.add_parser("hook")
    p.add_argument("event", choices=["session-start", "stop", "session-end", "post-write"])
    p.set_defaults(func=cmd_hook)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
