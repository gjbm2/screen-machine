"""Guard: no code names finsbury's own things, and no code reads the shared show.yaml without the site's layer.

finsbury is one deployment. Its screens, rooms, lamps, blinds, sensors, kiosk and device addresses live in
deployments/finsbury/ and reach code through smconfig. Written into code, one works on PC22 and quietly does the wrong
thing anywhere else. PC22's echo_arcs and overtone_beats (28 Oct, c19fcbbd) did that; at f1cfd927 this guard flags
echo_arcs.py:547 and 549 ("south-screen", "north-screen") and overtone_beats.py:123 ("south", "north"). The values are
read from deployments/finsbury through smconfig, so a screen or lamp added there is guarded at once. A value another
shipped deployment also has (the "_recent" bucket) is generic, not finsbury's.

Scanned: code (Python, TypeScript, JavaScript, HTML, Jinja templates, shell) outside deployments/, tests, docs,
media-server/, experiments/ and any tools/ directory. Comments and docstrings may say anything. A value with a digit,
dot, dash or space in it ("north-screen", "hue.ne", "Drawing room", an address) is flagged anywhere else in a file. A
plain word ("north", "devtest", "cloud") is flagged only as a whole string literal, because a word can be everyday
English. Not scanned, and knowingly left: data files, such as finsbury's own schedule templates in
routes/scheduler/scripts/ (_cloud-core.json, _ns-screens.json, _screen-routine.json export to its "drawing-room"
group; a template is copied into a schedule, and a site's own templates name its own groups).

The show.yaml check: config/room/show.yaml is only the shared base. Code reads it through a layered reader
(smconfig.load_layered_yaml, show_service's config.read_show), which puts deployments/<id>/show.yaml over it; a raw
read misses the site's senders, sensors and appliances.

ALLOWED lists the exceptions, each with its reason. Keep it short: a new site value in code belongs in the deployment.
"""
from __future__ import annotations

import ast
import ipaddress
import re
from pathlib import Path

import smconfig

REPO = smconfig.REPO_ROOT
SITE = "finsbury"                           # the site whose values must not be in code
OTHERS = ("example", "laptop")              # a value one of these also has is generic

SCAN_DIRS = ("room_service", "show_service", "stream_service", "dot_service", "piano_render", "routes", "lightmask",
             "utils", "scripts", "smconfig", "src")
SCAN_FILES = ("app.py", "config.py", "room_in_app.py", "overlay_ws_server.py", "connection_registry.py",
              "update_buckets.py", "start.sh", "restart-flask.sh", "index.html", "vite.config.ts")
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".html", ".j2", ".sh"}
EXTENSIONLESS = {"scripts/ctl"}             # shell scripts without a suffix
EXEMPT_PARTS = {"tests", "__pycache__", "node_modules", "golden", "runs", "tools"}   # tools: dev tools anywhere

# Plain words that are finsbury ids but also this codebase's own words, never looked for.
NOT_SITE_WORDS: dict[str, str] = {
    "lab": "a display page's role (kiosk, viewer, lab) and the console's lab-made scenes; the Lab room's screen "
           "(devtest) is still looked for",
    "Lab": "the console's word for lab-made scenes",
}

# file -> (finsbury values it may name, why). Keep it short: a site value in code belongs in the deployment.
ALLOWED: dict[str, tuple[set[str], str]] = {
    "show_service/space.py": ({"north", "south"}, "the compass points a room map's front can face (FRONTS)"),
    "show_service/api.py": ({"north", "south"}, "the compass points a room map's front can face (FRONTS)"),
    "src/pages/Lights/lib.ts": ({"north", "south"}, "the compass points a room map's front can face (FRONTS)"),
    "src/pages/Lights/RoomMap.tsx": ({"north", "south"}, "the compass points on the room map"),
    "room_service/dmx/patch.py": ({"screen.north"}, "an example id in an error message"),
    "stream_service/sessions.py": ({"screen.north", "drawing-room"}, "example ids in error messages"),
    "room_service/dmx/fake_artnet.py": ({"screen.north", "screen.south"},
                                        "show_synth's synthetic song names its two fixtures so"),
    "routes/data/alexa-undo-redo.txt.j2": ({"north-screen", "south-screen"},
                                           "worked examples in finsbury's Alexa prompt for the LLM"),
    "smconfig/legacy.py": ({"devtest", "screen.north"}, "the migration's declared edit to finsbury's own file"),
    "scripts/manual/manual_ws_overlay_client.py": ({"devtest"}, "a hand-run test client aimed at the test screen"),
}

# Raw reads of the shared show.yaml that stay, with why.
SHOW_RAW_ALLOWED: dict[str, str] = {
    "show_service/library.py": "the fallback when smconfig can't be imported (never in a checkout); reads tests.piano, "
                               "a shared key",
    "scripts/migrate_deployment.py": "proves the shared base no longer holds a site key",
}


# -- finsbury's values, from deployments/finsbury --------------------------------------------------------------------

def _ids(v) -> list[str]:
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _read(dep, *parts):
    p = dep.path(*parts)
    try:
        return smconfig.read_data(p) if p.is_file() else None
    except (OSError, ValueError):
        return None


def site_values(dep_id: str) -> dict[str, str]:
    """{value: what it is} for one deployment: its screens, rooms (ids and names), room lights, blinds and sensors,
    fixtures, Hue lamps, RF devices, light sensors, kiosk (host, screens, base_url host), wording site_address and
    every device address in its files."""
    dep = smconfig.load(dep_id)
    out: dict[str, str] = {}

    def add(v, what):
        if isinstance(v, str) and v.strip():
            out.setdefault(v.strip(), what)

    for d in smconfig.destinations(dep):
        add(d["id"], "screen")
    for rid, r in smconfig.rooms(dep).items():
        add(rid, "room")
        add(r.get("name"), "room name")
        for key in ("lights", "blinds", "sensors"):
            for x in _ids(r.get(key)):
                if key != "lights" or "." in x:          # a bare name in lights is a whole subsystem ("light")
                    add(x, f"room {key[:-1]}")
    fx = _read(dep, "lights", "fixtures.yaml") or {}
    for fid, f in (fx.get("fixtures") or {}).items():
        add(fid, "fixture")
        if isinstance(f, dict):
            add(f.get("light"), "Hue light")
            add(f.get("zone"), "fixture zone")
    patch = _read(dep, "lights", "patch.yaml") or {}
    for d in patch.get("devices") or []:
        if isinstance(d, dict):
            add(d.get("id"), "patch device")
    for lamp in ((patch.get("hue") or {}).get("lamps") or {}).items() if isinstance(patch.get("hue"), dict) else []:
        add(lamp[0], "Hue lamp")
        if isinstance(lamp[1], dict):
            add(lamp[1].get("light"), "Hue light")
    blinds = _read(dep, "room", "rf", "blinds.json") or {}
    for ch in (blinds.get("blinds") or {}):
        add(ch, "blind")
    for s in ((_read(dep, "intensity.json") or {}).get("sensor_mappings") or {}):
        add(s, "light sensor")
    k = dep.kiosk
    add(k.get("host"), "kiosk host")
    m = re.match(r"^[a-z]+://([^/:]+)", str(k.get("base_url") or ""))
    if m:
        add(m.group(1), "kiosk base_url host")
    for s in (k.get("screens") or {}).values():
        if isinstance(s, dict):
            add(s.get("name"), "kiosk screen name")
    add(dep.words("site_address", ""), "site address")
    for p in sorted(dep.dir.rglob("*")):
        if p.is_file() and p.suffix in (".json", ".yaml", ".yml", ".env"):
            for ip in re.findall(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])", p.read_text(errors="replace")):
                try:
                    a = ipaddress.IPv4Address(ip)
                except ValueError:
                    continue
                if not (a.is_loopback or a.is_unspecified):
                    add(ip, f"device address ({p.relative_to(dep.dir)})")
    return out


def finsbury_values() -> dict[str, str]:
    generic: set[str] = set(NOT_SITE_WORDS)
    for other in OTHERS:
        if other in smconfig.available():
            generic |= set(site_values(other))
    return {v: what for v, what in site_values(SITE).items() if v not in generic}


def _is_word(v: str) -> bool:
    return v.isalpha()


# -- the code, comments and docstrings blanked -----------------------------------------------------------------------

def _files():
    for f in SCAN_FILES:
        if (REPO / f).is_file():
            yield REPO / f
    for d in SCAN_DIRS:
        for p in sorted((REPO / d).rglob("*")):
            rel = p.relative_to(REPO)
            if not p.is_file() or EXEMPT_PARTS & set(rel.parts):
                continue
            if p.suffix not in CODE_SUFFIXES and rel.as_posix() not in EXTENSIONLESS:
                continue
            n = p.name
            if n.startswith("test_") or n == "conftest.py" or re.search(r"\.(test|spec)\.[jt]sx?$", n):
                continue
            yield p


def _python(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """(the string literals that aren't docstrings, the same as code pieces): [(line, text)]. A string that is a
    statement on its own (a docstring, or a block of prose between statements) is a comment."""
    tree = ast.parse(text)
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    lits = sorted((n.lineno, n.value) for n in ast.walk(tree)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs)
    return lits, lits


def _c_like(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """JS/TS/HTML/Jinja: (string literals, the text with comments blanked, by line). A small lexer: '' and "" strings
    end at the line's end, `templates` nest ${...}; // and /* */ comments, <!-- --> and {# #} are blanked."""
    text = re.sub(r"<!--.*?-->|\{#.*?#\}", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    out, lits = [], []
    i, n, line = 0, len(text), 1
    stack = ["code"]                          # code | tpl (inside a template literal); code entries count braces
    depth = [0]
    while i < n:
        c = text[i]
        state = stack[-1]
        if state == "tpl":
            j, start, s = i, line, []
            while j < n and text[j] != "`":
                if text[j] == "\\":
                    s.append(text[j:j + 2]); j += 2; continue
                if text.startswith("${", j):
                    break
                if text[j] == "\n":
                    line += 1
                s.append(text[j]); j += 1
            piece = "".join(s)
            lits.append((start, piece))
            out.append(piece)
            if j < n and text[j] == "`":
                stack.pop(); depth.pop(); out.append("`"); i = j + 1
            elif j < n:
                stack.append("code"); depth.append(0); out.append("${"); i = j + 2
            else:
                i = j
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j; continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            chunk = text[i:j]
            line += chunk.count("\n")
            out.append(re.sub(r"[^\n]", " ", chunk)); i = j; continue
        if c in "'\"":
            j, s = i + 1, []
            while j < n and text[j] != c and text[j] != "\n":
                if text[j] == "\\" and j + 1 < n and text[j + 1] != "\n":
                    s.append(text[j:j + 2]); j += 2; continue
                s.append(text[j]); j += 1
            piece = "".join(s)
            lits.append((line, piece))
            out.append(c + piece + (c if j < n and text[j] == c else ""))
            i = j + 1 if j < n and text[j] == c else j
            continue
        if c == "`":
            stack.append("tpl"); depth.append(0); out.append("`"); i += 1; continue
        if c == "{" and len(stack) > 1:
            depth[-1] += 1
        if c == "}" and len(stack) > 1:
            if depth[-1] == 0:
                stack.pop(); depth.pop(); out.append("}"); i += 1; continue
            depth[-1] -= 1
        if c == "\n":
            line += 1
        out.append(c); i += 1
    code = "".join(out)
    return lits, list(enumerate(code.split("\n"), start=1))


def _shell(text: str) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Shell: no string literals checked (a plain word isn't looked for); the lines with their # comments blanked."""
    lines = []
    for no, ln in enumerate(text.split("\n"), start=1):
        q, cut = None, len(ln)
        for k, ch in enumerate(ln):
            if q:
                if ch == q:
                    q = None
            elif ch in "'\"":
                q = ch
            elif ch == "#" and (k == 0 or ln[k - 1] in " \t;"):
                cut = k
                break
        lines.append((no, ln[:cut]))
    return [], lines


def _scan(path: Path, text: str):
    if path.suffix == ".py":
        return _python(text)
    if path.suffix == ".sh" or path.suffix == "":
        return _shell(text)
    return _c_like(text)


def _pattern(vs) -> re.Pattern:
    """Any of ``vs`` as a whole id: not part of a longer name, word or address."""
    alts = "|".join(re.escape(v) for v in sorted(vs, key=len, reverse=True))
    return re.compile(r"(?<![A-Za-z0-9_.-])(" + alts + r")(?![A-Za-z0-9_-]|\.[A-Za-z0-9])")


def find_site_values(paths, values: dict[str, str]) -> list[str]:
    """Every occurrence of a value in the code of ``paths`` (repo-relative lines), allowlist aside."""
    words = {v for v in values if _is_word(v)}
    pat = _pattern([v for v in values if v not in words]) if len(words) < len(values) else None
    quoted = re.compile("[\"'`](" + "|".join(map(re.escape, words)) + ")[\"'`]") if words else None
    hits = []
    for p in paths:
        rel = p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else str(p)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not ((pat and pat.search(text)) or (quoted and quoted.search(text))):
            continue                                  # nothing to find: skip the parse
        try:
            lits, code = _scan(p, text)
        except SyntaxError:
            continue
        seen, allowed = set(), (ALLOWED.get(rel) or (set(), ""))[0]
        for no, s in lits:
            if s in words and s not in allowed and (no, s) not in seen:
                seen.add((no, s))
                hits.append(f"{rel}:{no}: {s!r} ({values[s]})")
        for no, s in code:
            for m in (pat.finditer(s) if pat else ()):
                v = m.group(1)
                if v not in allowed and (no, v) not in seen:
                    seen.add((no, v))
                    hits.append(f"{rel}:{no}: {v!r} ({values[v]})")
    return hits


# -- raw reads of the shared show.yaml -------------------------------------------------------------------------------

_LAYERED = {"load_layered_yaml", "read_show"}
_READS = {"read_text", "read_bytes", "open", "safe_load", "load", "read_data"}


def _shared_show_expr(node: ast.AST) -> bool:
    """config/room/show.yaml spelled out: X / "room" / "show.yaml", CONFIG_ROOM_DIR / "show.yaml",
    shared("room", "show.yaml"), or a string naming config/room/show.yaml."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) \
            and node.right.value == "show.yaml":
        return "room" in ast.unparse(node.left).lower()
    if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "shared":
        return [getattr(a, "value", None) for a in node.args] == ["room", "show.yaml"]
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.rstrip("/").endswith("config/room/show.yaml") and " " not in node.value
    return False


def find_raw_show_reads(paths) -> list[str]:
    """Each read (read_text/open/yaml.safe_load/...) of the shared show.yaml, or of a name bound to it, that isn't
    inside a layered reader's call."""
    hits = []
    for p in paths:
        if p.suffix != ".py":
            continue
        rel = p.relative_to(REPO).as_posix() if p.is_relative_to(REPO) else str(p)
        try:
            text = p.read_text(encoding="utf-8")
            if "show.yaml" not in text and "SHOW_FILE" not in text:
                continue                              # names neither: skip the parse
            tree = ast.parse(text)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        names = {"SHOW_FILE"} if rel.startswith("show_service/") else set()    # contract.SHOW_FILE
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _shared_show_expr(node.value):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}

        def is_show(e: ast.AST) -> bool:
            return _shared_show_expr(e) or (isinstance(e, ast.Name) and e.id in names) or \
                (isinstance(e, ast.Attribute) and e.attr in names)

        parents: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for ch in ast.iter_child_nodes(node):
                parents[id(ch)] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "attr", getattr(fn, "id", ""))
            if name not in _READS:
                continue
            target = fn.value if isinstance(fn, ast.Attribute) and name in ("read_text", "read_bytes", "open") \
                and not (isinstance(fn.value, ast.Name) and fn.value.id in ("os", "io", "builtins")) else None
            args = list(node.args) + [k.value for k in node.keywords]
            if not ((target is not None and is_show(target)) or any(is_show(a) for a in args)):
                continue
            cur, layered = node, False
            while id(cur) in parents:
                cur = parents[id(cur)]
                if isinstance(cur, ast.Call) and getattr(cur.func, "attr", getattr(cur.func, "id", "")) in _LAYERED:
                    layered = True
                    break
            if not layered and rel not in SHOW_RAW_ALLOWED:
                hits.append(f"{rel}:{node.lineno}: reads the shared show.yaml without the site's layer")
    return hits


# -- the tests -------------------------------------------------------------------------------------------------------

def test_finsbury_values_are_collected_from_its_deployment():
    v = finsbury_values()
    for want in ("north-screen", "south-screen", "devtest", "drawing-room", "Drawing room", "hue.ne", "screen.north",
                 "living-room", "north", "south", "192.168.1.92"):
        assert want in v, want
    assert "_recent" not in v                                        # example and laptop have it too: generic


def test_no_finsbury_values_in_code():
    hits = find_site_values(list(_files()), finsbury_values())
    assert not hits, ("finsbury's own values in code (read them from the deployment through smconfig, or add an "
                      "ALLOWED entry with its reason):\n" + "\n".join(hits))


def test_every_allowlist_entry_is_still_needed():
    values = finsbury_values()
    for rel, (vs, why) in ALLOWED.items():
        for v in sorted(vs):
            assert v in values, f"ALLOWED {rel} {v!r}: not one of finsbury's values any more"
            hits = _without_allowlist(lambda: find_site_values([REPO / rel], {v: values[v]}))
            assert hits, f"ALLOWED {rel} {v!r} ({why}): no longer there, drop it"
    for w in NOT_SITE_WORDS:
        assert w in site_values(SITE), f"NOT_SITE_WORDS {w!r}: not one of finsbury's values any more"
    saved = dict(SHOW_RAW_ALLOWED)
    SHOW_RAW_ALLOWED.clear()
    try:
        for rel, why in saved.items():
            assert find_raw_show_reads([REPO / rel]), f"SHOW_RAW_ALLOWED {rel} ({why}): no raw read left, drop it"
    finally:
        SHOW_RAW_ALLOWED.update(saved)


def _without_allowlist(fn):
    saved = dict(ALLOWED)
    ALLOWED.clear()
    try:
        return fn()
    finally:
        ALLOWED.update(saved)


def test_the_guard_catches_a_site_value(tmp_path):
    """PC22's two effects (c19fcbbd) as they were: finsbury's kiosk screen names and screen ids."""
    p = tmp_path / "fx.py"
    p.write_text('"""south-screen in a docstring is fine."""\nPAIR_SPAN = {"south": (27.5, 600.0)}  # north is fine\n'
                 'pair = screen in ("south-screen", "north-screen")\n')
    t = tmp_path / "fx.ts"
    t.write_text('// north-screen in a comment\nconst s = "devtest";  /* hue.ne\n 192.168.1.92 */\n'
                 'const u = `http://${host("192.168.1.92")}/display/${"south-screen"}`;\nconst r = "cloudy";\n'
                 '<p>Drawing room</p>\n')
    got = {h.split(": ", 1)[0].rsplit("/", 1)[-1] + " " + h.split(": ", 1)[1].split(" (")[0]
           for h in find_site_values([p, t], finsbury_values())}
    assert got == {"fx.py:2 'south'", "fx.py:3 'south-screen'", "fx.py:3 'north-screen'", "fx.ts:2 'devtest'",
                   "fx.ts:4 '192.168.1.92'", "fx.ts:4 'south-screen'", "fx.ts:6 'Drawing room'"}


def test_no_raw_reads_of_the_shared_show_yaml():
    hits = find_raw_show_reads(list(_files()))
    assert not hits, "\n".join(hits)


def test_the_show_yaml_check_catches_a_raw_read(tmp_path):
    p = tmp_path / "x.py"
    p.write_text('import yaml, smconfig\nfrom pathlib import Path\nSHOW = Path("config") / "room" / "show.yaml"\n'
                 'a = yaml.safe_load(SHOW.read_text())\n'
                 'b = smconfig.load_layered_yaml(SHOW, smconfig.path("show.yaml"))\n'
                 'c = yaml.safe_load(open(smconfig.shared("room", "show.yaml")))\n'
                 'd = yaml.safe_load(smconfig.path("show.yaml").read_text())\n')
    assert [h.split(": ")[0].rsplit(":", 1)[1] for h in find_raw_show_reads([p])] == ["4", "6"]
