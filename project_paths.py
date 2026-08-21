#!/usr/bin/env python3
"""Shared, persisted location of the three folders every v2 script needs.

Before this module the three were resolved independently and mostly implicitly:
the raw data root was prompted for on every run and never remembered, the
results tree was *hardcoded* as `<raw root>/../Analysed_data` in seven separate
scripts, and the atlases folder was `<raw root>/atlases` with a
`/Volumes/njainmpi/...` fallback baked into each file. That works only while all
three live in the same place on the same machine.

Now all three are asked for once, saved as JSON, and offered back as defaults on
every later run:

    Raw data root    where sub-*/ses-*/ live.               Must already exist.
    Results root     where Analysed_data output is written. Created if missing.
    Atlases folder   the shared atlas files.                Must hold the files
                     the calling script needs.

The file is `aabc_paths.json`, next to the scripts, overridable with
$AABC_PATHS_FILE (used by the test harness so a run can't disturb the real one).
It sits with the code rather than under ~/.config deliberately: it is a property
of this analysis project, is meant to be readable and hand-editable, and the
browser app reads the same file, so there is exactly one answer to "where is the
data" no matter which entry point asked.

Prompting only ever happens on a tty. Under the browser app's job subprocess, or
any other non-interactive caller, an unresolvable path is a clear exit rather
than a prompt nothing can answer.
"""
import glob
import json
import os
import sys
from pathlib import Path

CONFIG_PATH = Path(os.environ.get(
    "AABC_PATHS_FILE", Path(__file__).resolve().parent / "aabc_paths.json"))

# Where the paths lived before this module existed. Read once, to seed the new
# file, so an existing setup does not have to be re-entered by hand.
LEGACY_APP_CONFIG = Path(os.environ.get(
    "AABC_APP_STATE_DIR", Path.home() / ".config" / "aabc_analysis_app")) / "config.json"

# Kept for the scripts and the app's environment probe, which still import it.
FALLBACK_ATLASES = Path("/Volumes/njainmpi/Project3_Aging/Raw_Data/atlases")

# What each pipeline needs out of the atlases folder. The folder is stored once;
# which files must be in it depends on who is asking, so callers pass their own.
FC_ATLAS_FILES = ("HCPex_2mm.nii", "HCPex_LookUpTable.txt")
ANAT_ATLAS_FILES = ("schaefer400_tianS1.dlabel.nii", "HCPex_2mm.nii", "HCPex_LookUpTable.txt")

KEYS = ("raw_root", "analysed_root", "atlases_dir")
LABELS = {
    "raw_root": "Raw data root",
    "analysed_root": "Results root",
    "atlases_dir": "Atlases folder",
}
HINTS = {
    "raw_root": "contains sub-*/ses-*/",
    "analysed_root": "where results are written (created if missing)",
    "atlases_dir": "contains HCPex_2mm.nii etc.",
}




def active_keys(need_atlases=True):
    """Which paths a given caller actually has to have.

    group_analysis_cross_sectional.py reads only the results tree and the
    demographics CSV -- it never opens an atlas. Demanding an atlases folder
    there would block a perfectly valid run on a machine that only holds
    results, so callers say whether they need one."""
    return KEYS if need_atlases else tuple(k for k in KEYS if k != "atlases_dir")


# --------------------------------------------------------------------------
# the file itself
# --------------------------------------------------------------------------

def load():
    """The saved paths as a plain dict of str. Never raises: an unreadable or
    corrupt file is treated as 'nothing saved yet', because refusing to start
    over a malformed cache would be worse than asking the three questions."""
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            if isinstance(data, dict):
                return {k: str(v) for k, v in data.items() if k in KEYS and v}
        except (json.JSONDecodeError, OSError):
            pass
    return _seed_from_legacy()


def _seed_from_legacy():
    """One-time migration: the browser app has been storing `raw_root` in its own
    config since 2026-07-29. If that is all we have, start from it rather than
    making the user re-enter a path they already chose."""
    try:
        if LEGACY_APP_CONFIG.exists():
            data = json.loads(LEGACY_APP_CONFIG.read_text())
            raw = data.get("raw_root")
            if raw:
                return {"raw_root": str(raw)}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def save(config):
    """Write the three paths back. Only the known keys are kept, so a hand-added
    stray key cannot silently become part of the schema."""
    clean = {k: str(config[k]) for k in KEYS if config.get(k)}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(clean, indent=2) + "\n")
    return clean


def get_path(key):
    """One saved path as a Path, or None if unset. Existence is NOT checked here
    -- callers that only want to know what is configured (the browser app's
    status panels) must be able to tell 'not set' from 'set but unreachable'."""
    value = load().get(key)
    return Path(value).expanduser() if value else None


# --------------------------------------------------------------------------
# defaults -- what the old hardcoded behaviour used to do
# --------------------------------------------------------------------------

def default_analysed_root(raw_root):
    """The pre-existing rule: Analysed_data sits next to, not inside, the raw
    root. Still the suggested default, so anyone who just presses Enter through
    the prompts lands exactly where every previous run wrote."""
    return Path(raw_root).parent / "Analysed_data"


def default_atlases_dir(raw_root, required_files):
    """`<raw root>/atlases`, else the network fallback, else None -- the same
    order find_atlases_dir() used, minus the hard exit."""
    if raw_root:
        candidate = Path(raw_root) / "atlases"
        if _has_atlas_files(candidate, required_files):
            return candidate
    if _has_atlas_files(FALLBACK_ATLASES, required_files):
        return FALLBACK_ATLASES
    return None


def _has_atlas_files(directory, required_files):
    return bool(directory) and all((Path(directory) / f).exists() for f in required_files)


def missing_atlas_files(directory, required_files):
    return [f for f in required_files if not (Path(directory) / f).exists()]


# --------------------------------------------------------------------------
# validation + display
# --------------------------------------------------------------------------

def check(key, value, required_atlas_files):
    """(ok, note) for one path. `note` is short enough to sit in the status
    column of the summary table and explains itself when not ok."""
    if not value:
        return False, "not set"
    path = Path(value).expanduser()
    if key == "raw_root":
        if not path.is_dir():
            return False, "not a directory"
        if not any(path.glob("sub-*")):
            return True, "ok — no sub-* folders yet"
        return True, "ok"
    if key == "analysed_root":
        if path.is_dir():
            return True, "ok"
        if path.exists():
            return False, "exists but is not a directory"
        return True, "will be created"
    if key == "atlases_dir":
        if not path.is_dir():
            return False, "not a directory"
        missing = missing_atlas_files(path, required_atlas_files)
        if missing:
            return False, f"missing {', '.join(missing)}"
        return True, "ok"
    return bool(path.exists()), "ok" if path.exists() else "missing"


def summary_lines(config, required_atlas_files, need_atlases=True):
    keys = active_keys(need_atlases)
    width = max(len(LABELS[k]) for k in keys)
    lines = []
    for key in keys:
        value = config.get(key)
        ok, note = check(key, value, required_atlas_files)
        mark = "✓" if ok else "✗"
        lines.append(f"  {mark} {LABELS[key]:<{width}} : {value or '—'}   [{note}]")
    return lines


def print_summary(config, required_atlas_files, header="Paths in use:", need_atlases=True):
    print(header)
    for line in summary_lines(config, required_atlas_files, need_atlases):
        print(line)


# --------------------------------------------------------------------------
# prompting
# --------------------------------------------------------------------------

def _completer(text, state):
    """Tab-complete directory names. Only directories are offered, because all
    three prompts want a folder -- completing files would only ever produce an
    answer that then fails validation.

    Glob-based rather than iterdir-based so `~` works: the glob runs on the
    expanded path and the result is folded back to `~/...` for display, which is
    what the user typed and what gets saved."""
    expanded = os.path.expanduser(text)
    matches = [m + os.sep for m in glob.glob(expanded + "*") if os.path.isdir(m)]
    if text.startswith("~"):
        home = os.path.expanduser("~")
        matches = [("~" + m[len(home):]) if m.startswith(home) else m for m in matches]
    try:
        return sorted(matches)[state]
    except IndexError:
        return None


class _completion:
    """Tab-completion for the directory prompts, fully restored on the way out
    so an importing script's own readline setup is left exactly as it was.

    The binding is backend-dependent and getting it wrong fails silently: macOS
    ships **libedit** (`readline.backend == 'editline'`), where the GNU
    incantation `tab: complete` does nothing at all and the tab key just inserts
    a tab. The same check already guards enable_path_completion() in the
    pipeline scripts."""

    def __enter__(self):
        try:
            import readline
            self._readline = readline
            self._previous_completer = readline.get_completer()
            try:
                self._previous_delims = readline.get_completer_delims()
            except Exception:
                self._previous_delims = None
            readline.set_completer(_completer)
            readline.set_completer_delims(" \t\n")
            if "libedit" in (readline.__doc__ or ""):
                readline.parse_and_bind("bind ^I rl_complete")
            else:
                readline.parse_and_bind("tab: complete")
        except Exception:
            self._readline = None
        return self

    def __exit__(self, *exc):
        rl = getattr(self, "_readline", None)
        if rl:
            rl.set_completer(self._previous_completer)
            if self._previous_delims is not None:
                rl.set_completer_delims(self._previous_delims)
        return False


def _ask(key, current, required_atlas_files):
    """Prompt for one path until it validates. Enter keeps `current` when there
    is one, so confirming an existing setup is three keystrokes."""
    label = LABELS[key]
    hint = HINTS[key]
    while True:
        suffix = f" [{current}]" if current else ""
        raw = input(f"  {label} ({hint}){suffix}: ").strip()
        if not raw and current:
            raw = str(current)
        if not raw:
            print("    A path is required.")
            continue
        candidate = Path(raw).expanduser()
        ok, note = check(key, candidate, required_atlas_files)
        if ok:
            return candidate
        print(f"    '{candidate}' — {note}, try again.")


def _prompt_all(config, required_atlas_files, need_atlases=True):
    """Ask for each needed path, seeding results/atlases from the raw root once
    it is known, so a standard layout needs one real answer and two Enters."""
    updated = dict(config)
    with _completion():
        updated["raw_root"] = str(_ask("raw_root", config.get("raw_root"), required_atlas_files))

        analysed_default = config.get("analysed_root") or default_analysed_root(updated["raw_root"])
        updated["analysed_root"] = str(_ask("analysed_root", analysed_default, required_atlas_files))

        if need_atlases:
            atlas_default = config.get("atlases_dir") or default_atlases_dir(
                updated["raw_root"], required_atlas_files)
            updated["atlases_dir"] = str(_ask("atlases_dir", atlas_default, required_atlas_files))
    return updated


# --------------------------------------------------------------------------
# the entry point every script calls
# --------------------------------------------------------------------------

def resolve(required_atlas_files, raw_root=None, analysed_root=None, atlases_dir=None,
            confirm=True, header="== Paths ==", need_atlases=True):
    """Settle all three paths and return them as (raw_root, analysed_root,
    atlases_dir), saving whatever was decided.

    Explicit arguments (a CLI flag) always win and are saved, so
    `--raw-root /other/place` both runs there and is remembered.

    Otherwise: nothing saved yet -> ask for all three. Something saved and every
    path still valid -> show them and take a y/n. Something saved but a path has
    gone bad (an unmounted volume, a moved atlas folder) -> say which, and go
    straight to the prompts rather than offering a broken default as 'y'.

    Off a tty nothing is ever asked: the saved or derived values are used, and a
    path that cannot be resolved is a clear exit. That is the path the browser
    app's job subprocess takes.
    """
    saved = load()
    overrides = {"raw_root": raw_root, "analysed_root": analysed_root, "atlases_dir": atlases_dir}
    given = {k: str(Path(v).expanduser()) for k, v in overrides.items() if v}

    config = {**saved, **given}
    # Fill anything still unset from the old implicit rules, so a first run with
    # only --raw-root behaves exactly like every run before this module existed.
    if config.get("raw_root"):
        config.setdefault("analysed_root", str(default_analysed_root(config["raw_root"])))
        if need_atlases and not config.get("atlases_dir"):
            derived = default_atlases_dir(config["raw_root"], required_atlas_files)
            if derived:
                config["atlases_dir"] = str(derived)

    keys = active_keys(need_atlases)
    problems = [k for k in keys if not check(k, config.get(k), required_atlas_files)[0]]
    interactive = sys.stdin.isatty() and sys.stdout.isatty()

    if not interactive:
        if problems:
            print_summary(config, required_atlas_files, header="Paths:", need_atlases=need_atlases)
            sys.exit(
                "Cannot resolve: " + ", ".join(LABELS[k] for k in problems) +
                f".\nRun one of the interactive scripts to set them, or edit {CONFIG_PATH}."
            )
        return _finish(config, given, saved, need_atlases)

    print(f"\n{header}")
    if not saved and not given:
        print("No saved paths yet — set them once and they'll be remembered.\n")
        config = _prompt_all(config, required_atlas_files, need_atlases)
    elif problems:
        print_summary(config, required_atlas_files,
                      header="Saved paths — some need attention:" if saved else "Some paths need attention:",
                      need_atlases=need_atlases)
        print()
        config = _prompt_all(config, required_atlas_files, need_atlases)
    elif confirm and not given:
        print_summary(config, required_atlas_files, need_atlases=need_atlases)
        answer = input("\nUse these paths? [Y/n]: ").strip().lower()
        if answer in ("n", "no"):
            print()
            config = _prompt_all(config, required_atlas_files, need_atlases)
    else:
        print_summary(config, required_atlas_files, need_atlases=need_atlases)

    return _finish(config, given, saved, need_atlases)


def _finish(config, given, saved, need_atlases=True):
    """Persist if anything changed, create the results root, hand back Paths.

    `atlases_dir` comes back None for a caller that does not need one and has
    none saved -- returning the triple regardless keeps every call site's
    unpacking identical."""
    if {k: config.get(k) for k in KEYS} != {k: saved.get(k) for k in KEYS}:
        save(config)
        if given:
            print(f"  (saved to {CONFIG_PATH.name})")

    raw = Path(config["raw_root"]).expanduser()
    analysed = Path(config["analysed_root"]).expanduser()
    atlases = Path(config["atlases_dir"]).expanduser() if config.get("atlases_dir") else None
    analysed.mkdir(parents=True, exist_ok=True)
    return raw, analysed, atlases
