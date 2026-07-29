#!/usr/bin/env python3
"""
Interactive functional connectivity pipeline (v3 -- HCPex-only).

Prompts for:
  - path to the raw data root (contains sub-*/ses-*/... HCP-processed sessions)
  - which subject to run, from those found under that root
  - which session, if the subject has more than one
  - which parcels to include in the "standard" FC matrix, out of all 426
    HCPex parcels

Every run always computes five analyses for the chosen session, same as v2:
  - "standard"     -- FC matrix over whatever parcels you pick above
  - "graph_vta"    -- fixed NbM <-> VTA <-> Hippocampus graph, left/right separate
  - "graph_sn"     -- fixed NbM <-> SN <-> Hippocampus graph, left/right separate
  - "triangle_vta" -- same 3 regions as graph_vta, but left/right pairs are
                       first averaged into one composite timeseries each, so
                       it's a plain 3-node triangle (NbM, VTA, Hippocampus)
  - "triangle_sn"  -- same idea for SN: SNpc+SNpr averaged into one "SN" node,
                       NbM and Hippocampus averaged the same way as above,
                       giving a plain 3-node triangle (NbM, SN, Hippocampus)
All five are saved into Analysed_data/<subject>/<session>/func/, suffixed with
"_hcpex" (e.g. fc_matrix_corr_standard_hcpex.csv) -- mirroring the anat/
subfolder cross_sectional_analysis_v2.py uses for its own outputs.

Unlike v1/v2, this version sources every single parcel -- cortex included --
from one atlas: HCPex (Huang & Rolls et al. 2022), 426 regions covering
HCP-MMP1.0 cortex (360) plus its subcortical/midbrain/basal-forebrain
extension (66): thalamic nuclei, striatum, amygdala, SNpc/SNpr/VTA,
mammillary bodies, septal nucleus, nucleus basalis, hippocampus.

HCPex ships as a plain volumetric NIfTI (deterministic, one integer label per
voxel) -- it exists specifically to bring the HCP-MMP1.0 cortical
parcellation (natively CIFTI/grayordinate, surface-registered) to volumetric
tools like SPM/FSL that can't read CIFTI. That means cortex here comes from
the volumetric BOLD via mask mean, same method/file as everything else in
this atlas -- NOT the CIFTI dtseries + MSMAll surface registration v1/v2 use
for cortex. One atlas, one file, one extraction method; the tradeoff is
losing MSMAll's surface-registration precision for the cortical parcels.

Results are written to a sibling "Analysed_data" tree next to the raw data
root, mirroring its sub-<subject>/ses-<session>/ layout, under a "func"
subfolder. The raw data tree itself is never written to.

The full 426-parcel timeseries extraction runs once per session and is
cached at the subject level as Analysed_data/<subject>/timeseries_hcpex_<ses
sion>.csv (separate from v2's timeseries_<session>.csv cache, since the
parcel sets are entirely different). Delete that file to force a fresh
extraction (e.g. after updating the atlas file).

All saved matrices/timeseries are plain CSV (openable in Excel, Numbers, or
`pandas.read_csv`) rather than NumPy's .npy binary format.
"""
import csv
import glob
import json
import os
import re
import shutil
import sys
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import nibabel as nib

import region_grouping
import readline

# The 66 base names (33/hemisphere, hemisphere suffix _L/_R stripped) HCPex
# adds on top of HCP-MMP1.0 cortex -- everything else in the atlas
# ("Primary_Visual_Cortex_L", "IFJa_R", ...) is cortex. Source:
# HCPex_LookUpTable.txt label indices 361-426.
SUBCORTICAL_BASE_NAMES = {
    "Thal_AnteroventralNucleus", "Thal_Central_medial", "Thal_Central_lateral",
    "Thal_Centralmedian", "Thal_Laterodorsal", "Thal_Lateral_Geniculate",
    "Thal_Lateral_Posterior", "Thal_Limitans_Suprageniculate",
    "Thal_Mediodorsolateral_parvocellular", "Thal_Mediodorsomedial_magnocellular",
    "Thal_Medial_Geniculate", "Thal_Reuniens", "Thal_Parafascicular",
    "Thal_Pulvinar_anterior", "Thal_Pulvinar_inferior", "Thal_Pulvinar_lateral",
    "Thal_Pulvinar_medial", "Thal_Ventral_Anterior", "Thal_Ventral_Lateral_Anterior",
    "Thal_Ventral_Lateral_Posterior", "Thal_Ventral_posterolateral",
    "Putamen", "Caudate", "Nucleus_Accumbens", "Globus_pallidus_externalis",
    "Globus_pallidus_internalis", "Amygdala", "Substantia_nigra_pars_compacta",
    "Substantia_nigra_pars_reticulata", "Ventral_tegmenta_area",
    "Mammillary_bodies", "Septal_nucleus", "Nuclei_basal",
}

ANALYSIS_LOG_NAME = "analysis_log_v3.json"  # separate from v1/v2's logs -- different pipeline, own run history
COUNT_COLOR_SCALE = 10  # subjects/sessions analysed this many times or more render fully green
COLOR_ENABLED = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"

VOL_BOLD_REL = "func/MNINonLinear/Results/rfMRI_REST/rfMRI_REST_hp0_clean_rclean_tclean.nii.gz"

# used only if the raw data root itself has no atlases/ subfolder
FALLBACK_ATLASES = Path("/Volumes/njainmpi/Project3_Aging/Raw_Data/atlases")

ATLAS_GROUPS = {
    "A": "HCP-MMP1.0 cortex (360 regions)",
    "B": "HCPex subcortical/midbrain/basal-forebrain extension (66 regions)",
}

CARD_COLORS = {  # purely cosmetic per-group accent, unrelated to the analysis-count gradient
    "A": (90, 160, 250),
    "B": (190, 130, 250),
}

# Analysis 2/3: fixed node sets for the graph plots, left/right kept separate
# (HCPex lateralizes VTA and SNpc/SNpr, unlike CIT168 -- no averaging here).
GRAPH_VTA = ["Ventral_tegmenta_area_L", "Ventral_tegmenta_area_R",
             "Nuclei_basal_L", "Nuclei_basal_R", "Hippocampus_L", "Hippocampus_R"]
GRAPH_SN = ["Substantia_nigra_pars_compacta_L", "Substantia_nigra_pars_compacta_R",
            "Substantia_nigra_pars_reticulata_L", "Substantia_nigra_pars_reticulata_R",
            "Nuclei_basal_L", "Nuclei_basal_R", "Hippocampus_L", "Hippocampus_R"]
GRAPH_DISPLAY = {  # node label only -- no atlas/source text on the diagram
    "Ventral_tegmenta_area_L": "VTA (L)", "Ventral_tegmenta_area_R": "VTA (R)",
    "Nuclei_basal_L": "NbM (L)", "Nuclei_basal_R": "NbM (R)",
    "Hippocampus_L": "HC (L)", "Hippocampus_R": "HC (R)",
    "Substantia_nigra_pars_compacta_L": "SNc (L)", "Substantia_nigra_pars_compacta_R": "SNc (R)",
    "Substantia_nigra_pars_reticulata_L": "SNr (L)", "Substantia_nigra_pars_reticulata_R": "SNr (R)",
    "NbM": "NbM", "Hippocampus": "HC", "SN": "SN", "VTA": "VTA",
}

# Analysis 4/5: simple 3-node triangles -- every composite region here is an
# average of its listed parts (left/right, and for SN also SNpc+SNpr).
TRIANGLE_COMBINE_MAP = {
    "NbM": ["Nuclei_basal_L", "Nuclei_basal_R"],
    "Hippocampus": ["Hippocampus_L", "Hippocampus_R"],
    "VTA": ["Ventral_tegmenta_area_L", "Ventral_tegmenta_area_R"],
    "SN": ["Substantia_nigra_pars_compacta_L", "Substantia_nigra_pars_compacta_R",
           "Substantia_nigra_pars_reticulata_L", "Substantia_nigra_pars_reticulata_R"],
}
TRIANGLE_VTA = ["NbM", "VTA", "Hippocampus"]
TRIANGLE_SN = ["NbM", "SN", "Hippocampus"]

# Above this many nodes the circular graph becomes an unreadable hairball, so the
# extra graph view of a combined analysis is only drawn for small region sets.
MAX_GRAPH_NODES = 12


def classify(name):
    """Return the (group_letter, atlas_label) a parcel name belongs to, based
    on the base name with the trailing '_L'/'_R' hemisphere suffix stripped."""
    base = name[:-2] if name.endswith(("_L", "_R")) else name
    if base in SUBCORTICAL_BASE_NAMES:
        return "B", ATLAS_GROUPS["B"]
    return "A", ATLAS_GROUPS["A"]


def prompt(msg):
    val = input(msg).strip()
    if not val:
        sys.exit("Nothing entered — aborting.")
    return val


def _path_completer(text, state):
    expanded = os.path.expanduser(text)
    matches = glob.glob(expanded + "*")
    matches = [m + os.sep if os.path.isdir(m) else m for m in matches]
    if text.startswith("~"):
        home = os.path.expanduser("~")
        matches = [("~" + m[len(home):]) if m.startswith(home) else m for m in matches]
    try:
        return matches[state]
    except IndexError:
        return None


def enable_path_completion():
    readline.set_completer_delims(" \t\n")
    readline.set_completer(_path_completer)
    if "libedit" in (readline.__doc__ or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")


def disable_completion():
    readline.set_completer(None)


def _log_path(analysed_root):
    return analysed_root / ANALYSIS_LOG_NAME


def load_analysis_log(analysed_root):
    """{subject_name: {session_name: times_analysed}}, or {} if no history yet."""
    path = _log_path(analysed_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_analysis_log(analysed_root, log):
    analysed_root.mkdir(parents=True, exist_ok=True)
    _log_path(analysed_root).write_text(json.dumps(log, indent=2, sort_keys=True))


def record_analysis(analysed_root, subject_name, session_name):
    """Increment the run counter for this subject/session after a successful pipeline run."""
    log = load_analysis_log(analysed_root)
    counts = log.setdefault(subject_name, {})
    counts[session_name] = counts.get(session_name, 0) + 1
    save_analysis_log(analysed_root, log)


def subject_total(log, subject_name):
    """Total analysis runs across all of this subject's sessions."""
    return sum(log.get(subject_name, {}).values())


def _gradient_rgb(count, max_count=COUNT_COLOR_SCALE):
    """0 times analysed -> red, max_count+ times -> green, interpolated through yellow."""
    t = max(0.0, min(1.0, count / max_count))
    if t <= 0.5:
        ratio = t / 0.5
        return 255, round(255 * ratio), 0
    ratio = (t - 0.5) / 0.5
    return round(255 * (1 - ratio)), 255, 0


def _style(text, rgb, bold=False):
    if not COLOR_ENABLED:
        return text
    r, g, b = rgb
    prefix = "\033[1;" if bold else "\033["
    return f"{prefix}38;2;{r};{g};{b}m{text}\033[0m"


def _fit(text, width):
    if len(text) > width:
        return text[: max(1, width - 1)] + "…"
    return text.center(width)


MIN_TILE_WIDTH = 8  # interior width below which a tile stops being legible


def choose_tile(items, counts, label, formatter=str):
    """Render items as a wrapped grid of colored tiles (color = analysis count on a
    red->yellow->green scale) and let the user pick one by number. Column/row count is
    recomputed from the terminal's current width every call, and tile width itself shrinks
    (truncating names with an ellipsis) rather than overflow the line and overlap rows."""
    names = [formatter(item) for item in items]
    n = len(items)
    count_strs = [f"({c}x)" for c in counts]
    ideal_w = max([len(nm) for nm in names] + [len(cs) for cs in count_strs] + [len(str(n)), 10]) + 2
    ideal_w = min(30, ideal_w)

    term_width = shutil.get_terminal_size(fallback=(100, 24)).columns
    # a tile's printed line is "│" + tile_w + "│" == tile_w + 2 chars wide
    tile_w = max(MIN_TILE_WIDTH, min(ideal_w, term_width - 2))
    per_tile_span = (tile_w + 2) + 1  # box width + 1-space gap between tiles
    tiles_per_row = max(1, term_width // per_tile_span)

    print(f"\n{label}")
    for row_start in range(0, n, tiles_per_row):
        row = list(range(row_start, min(row_start + tiles_per_row, n)))
        tops, nums, nms, cnts, bots = [], [], [], [], []
        for i in row:
            rgb = _gradient_rgb(counts[i])
            tops.append(_style("┌" + "─" * tile_w + "┐", rgb))
            nums.append(_style("│" + _fit(str(i + 1), tile_w) + "│", rgb, bold=True))
            nms.append(_style("│" + _fit(names[i], tile_w) + "│", rgb, bold=True))
            cnts.append(_style("│" + _fit(count_strs[i], tile_w) + "│", rgb))
            bots.append(_style("└" + "─" * tile_w + "┘", rgb))
        print(" ".join(tops))
        print(" ".join(nums))
        print(" ".join(nms))
        print(" ".join(cnts))
        print(" ".join(bots))
        print()

    while True:
        choice = prompt("Select (number): ")
        if choice.isdigit() and 1 <= int(choice) <= n:
            return items[int(choice) - 1]
        print("  Invalid choice, try again.")


def find_data_root():
    enable_path_completion()
    try:
        while True:
            raw = Path(prompt("Path to raw data root (contains sub-*/ses-*/..., Tab to autocomplete): ")).expanduser()
            if raw.is_dir():
                return raw
            print(f"  '{raw}' is not a directory, try again.")
    finally:
        disable_completion()


def find_atlases_dir(raw_root):
    candidate = raw_root / "atlases"
    if (candidate / "HCPex_2mm.nii").exists() and (candidate / "HCPex_LookUpTable.txt").exists():
        return candidate
    if (FALLBACK_ATLASES / "HCPex_2mm.nii").exists() and (FALLBACK_ATLASES / "HCPex_LookUpTable.txt").exists():
        print(f"  (no atlases/ folder under {raw_root} — using {FALLBACK_ATLASES})")
        return FALLBACK_ATLASES
    sys.exit(
        "Could not locate HCPex atlas files (HCPex_2mm.nii, HCPex_LookUpTable.txt) "
        "under either the data root or the fallback location."
    )


def load_hcpex_labels(labels_txt):
    """idx -> name (e.g. 'Hippocampus_L', 'Substantia_nigra_pars_compacta_R'),
    from HCPex's LookUpTable.txt -- a FreeSurfer/ITK-SNAP-style color LUT:
    '<idx> <Full_Descriptive_Name> <R> <G> <B> <alpha>' per line, plus a
    commented header and an index-0 'Unknown' background row we skip. Chosen
    over HCPex_2mm.txt's short atlas codes ('L V1', 'R SNpc') because these
    full descriptive names are more self-explanatory in output CSVs/plots."""
    labels = {}
    for line in labels_txt.read_text().strip().splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        idx = int(parts[0])
        if idx == 0:
            continue  # background/Unknown, not a real parcel
        labels[idx] = parts[1]
    return labels


def extract_hcpex_timeseries(vol_bold, hcpex_vol, hcpex_labels):
    """Unweighted mean timeseries for every HCPex parcel (cortex + subcortex +
    midbrain + basal forebrain, all from one deterministic label volume --
    mask mean, same method v1/v2 used for their hard-labeled atlases)."""
    idx_to_name = load_hcpex_labels(hcpex_labels)
    ordered_idx = sorted(idx_to_name)
    names = [idx_to_name[i] for i in ordered_idx]

    label_img = nib.load(str(hcpex_vol))
    label_data = label_img.get_fdata()  # x y z

    bold_img = nib.load(str(vol_bold))
    bold_data = bold_img.get_fdata(dtype=np.float32)  # x y z time

    if label_data.shape[:3] != bold_data.shape[:3]:
        raise RuntimeError(
            f"Grid mismatch: HCPex atlas is {label_data.shape[:3]}, BOLD is {bold_data.shape[:3]}. "
            "HCPex_2mm.nii was pre-resampled to a specific 2mm grid -- this session isn't on it."
        )

    n_timepoints = bold_data.shape[-1]
    out = np.zeros((n_timepoints, len(ordered_idx)), dtype=np.float64)

    for i, idx in enumerate(ordered_idx):
        mask = label_data == idx
        if not mask.any():
            raise RuntimeError(f"No voxels found for label index {idx} ({idx_to_name[idx]})")
        out[:, i] = bold_data[mask].mean(axis=0)

    del bold_data  # free ~7GB before correlation step
    return out, names


def timeseries_cache_path(subject_dir, session_name):
    """Analysed_data/<subject>/timeseries_hcpex_<session>.csv -- separate from
    v2's timeseries_<session>.csv cache, since the parcel sets don't match."""
    return subject_dir / f"timeseries_hcpex_{session_name}.csv"


def save_timeseries_csv(path, ts, names):
    """T x N timeseries as CSV: header row of parcel names, one row per
    timepoint -- opens directly in Excel/Numbers/pandas, unlike a .npy file."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(names)
        writer.writerows(ts.tolist())


def load_timeseries_csv(path):
    """Inverse of save_timeseries_csv() -- returns (ts, names)."""
    with open(path, newline="") as f:
        reader = csv.reader(f)
        names = next(reader)
        rows = [[float(v) for v in row] for row in reader]
    return np.array(rows, dtype=np.float64), names


def save_matrix_csv(path, matrix, names):
    """N x N matrix as CSV with a region-name header row and label column, so
    it opens as a readable labeled table in Excel/Numbers/pandas."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([""] + list(names))
        for name, row in zip(names, matrix):
            writer.writerow([name] + [f"{v:.6f}" for v in row])


def load_or_extract_all_sources(ts_path, vol_bold, hcpex_vol, hcpex_labels):
    """Load the full 426-parcel HCPex timeseries for this session from ts_path
    if a previous run already extracted it *with the current label file's
    names* -- otherwise extract fresh and (re)cache the result at ts_path.
    The names check matters because the label file has changed once already
    (short atlas codes like 'L V1' -> full LookUpTable names like
    'Primary_Visual_Cortex_L'); without it, an old cache would silently load
    under the new naming scheme and every parcel would misclassify into the
    wrong atlas group. Delete ts_path manually to force re-extraction too
    (e.g. after updating the atlas file itself, not just its labels)."""
    idx_to_name = load_hcpex_labels(hcpex_labels)
    expected_names = [idx_to_name[i] for i in sorted(idx_to_name)]

    if ts_path.exists():
        cached_ts, cached_names = load_timeseries_csv(ts_path)
        if cached_names == expected_names:
            print(f"  Found cached timeseries at {ts_path} -- skipping extraction.")
            return cached_ts, cached_names
        print(f"  Cached timeseries at {ts_path} uses different parcel names than the "
              "current label file (it was extracted before a labeling change) -- re-extracting.")

    ts_path.parent.mkdir(parents=True, exist_ok=True)

    all_ts, all_names = extract_hcpex_timeseries(vol_bold, hcpex_vol, hcpex_labels)
    print(f"  {len(all_names)} HCPex parcels x {all_ts.shape[0]} timepoints (volumetric, mask mean)")

    save_timeseries_csv(ts_path, all_ts, all_names)
    print(f"  Cached timeseries to {ts_path} for future runs.")

    return all_ts, all_names


def _ljust_fit(text, width):
    if len(text) > width:
        return text[: max(1, width - 1)] + "…"
    return text.ljust(width)


def _group_labeled_entries(all_names):
    """Group parcels by atlas letter, in original relative order, and give each
    a within-group label (A1, A2, ..., B1, ...). Returns
    {letter: [(label, original_index, name), ...]}."""
    groups = {letter: [] for letter in ATLAS_GROUPS}
    for i, name in enumerate(all_names):
        letter, _ = classify(name)
        groups[letter].append((i, name))
    return {
        letter: [(f"{letter}{k}", idx, name) for k, (idx, name) in enumerate(items, 1)]
        for letter, items in groups.items()
    }


def display_name(name):
    """Underscore-separated LookUpTable names ('Substantia_nigra_pars_compacta_L')
    read better with spaces on the terminal card -- extraction, region_names.txt,
    and matrix axis labels all keep using the raw underscored name regardless."""
    return name.replace("_", " ")


def _print_atlas_card(letter, entries, term_width):
    """Print one atlas group as a bordered card: a title bar over a multi-column
    listing of every parcel's label + display name, reflowed to the terminal width."""
    rgb = CARD_COLORS[letter]
    border_w = max(20, term_width - 2)

    title = f"{letter}: {ATLAS_GROUPS[letter]} — {len(entries)} parcels"
    label_w = max(len(lbl) for lbl, _, _ in entries)
    row_strs = [f"{lbl:<{label_w}} {display_name(name)}" for lbl, _, name in entries]
    entry_w = max(len(s) for s in row_strs)
    col_w = entry_w + 2
    n_cols = max(1, (border_w - 2) // col_w)
    n_rows = -(-len(row_strs) // n_cols)  # ceil div

    print(_style("┌" + "─" * border_w + "┐", rgb))
    print(_style("│ " + _ljust_fit(title, border_w - 2) + " │", rgb, bold=True))
    print(_style("├" + "─" * border_w + "┤", rgb))
    for r in range(n_rows):
        row_entries = row_strs[r * n_cols:(r + 1) * n_cols]
        content = "  ".join(e.ljust(entry_w) for e in row_entries)
        print(_style("│ " + _ljust_fit(content, border_w - 2) + " │", rgb))
    print(_style("└" + "─" * border_w + "┘", rgb))
    print()


def _parse_card_selection(choice, label_to_entry, labeled):
    """Parse 'all' / a bare group letter / label(s) / label range(s), e.g.
    'A1, B2, B4', 'B', 'A1-10', or 'all'. Returns selected labels in the
    order typed (ranges/groups expand in ascending order), duplicates
    removed by first occurrence -- or None if unparseable."""
    if choice.strip().lower() == "all":
        return [lbl for letter in labeled for (lbl, _, _) in labeled[letter]]

    tokens = [t.strip() for t in choice.split(",") if t.strip()]
    if not tokens:
        return None

    letters = "".join(labeled)  # e.g. "AB" -- derived from ATLAS_GROUPS, never hardcoded
    selected, seen = [], set()
    for tok in tokens:
        m = re.match(rf"^([{letters}{letters.lower()}])(\d+)?(?:-(\d+))?$", tok)
        if not m:
            return None
        letter = m.group(1).upper()
        if m.group(2) is None:
            labels = [lbl for (lbl, _, _) in labeled[letter]]
        else:
            lo = int(m.group(2))
            hi = int(m.group(3)) if m.group(3) else lo
            if lo > hi:
                return None
            labels = [f"{letter}{k}" for k in range(lo, hi + 1)]
        for lbl in labels:
            if lbl not in label_to_entry:
                return None
            if lbl not in seen:
                seen.add(lbl)
                selected.append(lbl)
    return selected


def choose_parcels(all_names):
    """Show each atlas group as its own card (label + name per parcel) and let
    the user pick specific parcels across groups by label, e.g. 'A1, B2'.
    Returns the chosen names, in the order the user selected them."""
    labeled = _group_labeled_entries(all_names)
    label_to_entry = {lbl: (idx, name) for items in labeled.values() for (lbl, idx, name) in items}
    term_width = shutil.get_terminal_size(fallback=(100, 24)).columns

    print(f"\nAtlas groups available ({len(all_names)} parcels total):")
    for letter in ATLAS_GROUPS:
        _print_atlas_card(letter, labeled[letter], term_width)

    print(
        "Select parcels by label, comma-separated (e.g. 'A1, B2, B4'). "
        "A bare group letter selects the whole group (e.g. 'B'), a range works "
        "within a group (e.g. 'A1-10'), or type 'all' for every parcel:"
    )
    while True:
        choice = prompt("Selection: ")
        selected_labels = _parse_card_selection(choice, label_to_entry, labeled)
        if selected_labels is None:
            print("  Could not parse that selection, try again.")
            continue
        if len(selected_labels) < 2:
            print("  Need at least 2 parcels for a correlation matrix, try again.")
            continue
        return [label_to_entry[lbl][1] for lbl in selected_labels]


def plot_fc_matrix(corr, names, out_dir, title, suffix):
    """Save the correlation matrix as a labeled heatmap, .png + .svg. suffix
    is appended to the filename so multiple analyses can share one folder."""
    n = len(names)
    fig_size = min(max(6, n * 0.28), 22)
    fig, ax = plt.subplots(figsize=(fig_size, fig_size))
    im = ax.imshow(corr, cmap="RdBu_r", vmin=-1, vmax=1, origin="lower")
    cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    cbar.set_label("Pearson r")

    if n <= 60:
        label_size = 9 if n <= 25 else max(4, 9 - (n - 25) * 0.15)
        ax.set_xticks(range(n)); ax.set_xticklabels(names, rotation=90, fontsize=label_size)
        ax.set_yticks(range(n)); ax.set_yticklabels(names, fontsize=label_size)
    else:
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_xlabel(f"{n} parcels (labels omitted -- too many to fit)")

    ax.set_title(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(out_dir / f"fc_matrix_{suffix}.png", dpi=200)
    fig.savefig(out_dir / f"fc_matrix_{suffix}.svg")
    plt.close(fig)


def plot_graph(corr, names, out_dir, title, suffix):
    """Analysis 2/3: a circular node-link graph -- one region per node
    (left/right kept separate, no averaging), edges as straight lines colored
    + thickness-scaled by pairwise Pearson r on the same red/blue diverging
    scale as plot_fc_matrix. No atlas/source text on the nodes, region name
    only."""
    n = len(names)
    positions = {}
    for i, name in enumerate(names):
        angle = np.pi / 2 - 2 * np.pi * i / n  # first node at top, clockwise
        positions[name] = (0.5 + 0.38 * np.cos(angle), 0.5 + 0.38 * np.sin(angle))

    fig, ax = plt.subplots(figsize=(7.5, 7))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1)
    ax.set_aspect("equal")
    ax.axis("off")

    cmap = plt.get_cmap("RdBu_r")
    norm = plt.Normalize(vmin=-1, vmax=1)

    for i, j in combinations(range(n), 2):
        x1, y1 = positions[names[i]]
        x2, y2 = positions[names[j]]
        r = corr[i, j]
        ax.plot([x1, x2], [y1, y2], color=cmap(norm(r)), linewidth=1.2 + abs(r) * 14,
                solid_capstyle="round", zorder=1)
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my, f"{r:.2f}", ha="center", va="center", fontsize=8, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.2", facecolor="white", edgecolor="none", alpha=0.9),
                zorder=3)

    for name in names:
        x, y = positions[name]
        ax.add_patch(plt.Circle((x, y), 0.09, facecolor="white", edgecolor="#8a939c",
                                 linewidth=1.5, zorder=2))
        label = GRAPH_DISPLAY.get(name, name.replace("_", " "))
        ax.text(x, y, label, ha="center", va="center", fontsize=9.5, fontweight="bold", zorder=4)

    sm = plt.cm.ScalarMappable(cmap=cmap, norm=norm)
    cbar = fig.colorbar(sm, ax=ax, fraction=0.045, pad=0.04, shrink=0.7)
    cbar.set_label("Pearson r")

    fig.suptitle(title, fontsize=11)  # figure-wide, not axes-centered -- room for the colorbar too
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(out_dir / f"fc_matrix_{suffix}.png", dpi=200)
    fig.savefig(out_dir / f"fc_matrix_{suffix}.svg")
    plt.close(fig)


def build_triangle_ts(all_ts, all_names):
    """Average each TRIANGLE_COMBINE_MAP entry's parts into one composite
    timeseries per region (NbM, Hippocampus, VTA, SN). Returns (triangle_ts,
    triangle_names) with the same shape convention as (all_ts, all_names),
    ready to feed into run_and_save_analysis() with TRIANGLE_VTA/TRIANGLE_SN
    as the selection."""
    name_to_col = {n: i for i, n in enumerate(all_names)}
    columns, names = [], []
    for combined_name, parts in TRIANGLE_COMBINE_MAP.items():
        cols = [name_to_col[p] for p in parts]
        columns.append(all_ts[:, cols].mean(axis=1))
        names.append(combined_name)
    return np.stack(columns, axis=1), names


def run_grouped_analysis(all_ts, all_names, selected_names, grouping, out_dir, suffix,
                          subject_name, session_name):
    """Same analysis as run_and_save_analysis, on composite regions.

    The parcels are combined FIRST (their timeseries averaged sample by sample)
    and the correlation computed on the composites -- treating a pair as one
    region. That is not the same as averaging the two parcels' correlations
    afterwards, which would average two measurements of different regions.

    Output goes to <suffix>_combined so it never overwrites the per-parcel run,
    and the grouping itself is written beside it for provenance.
    """
    # `grouping` may be a Grouping or the spec that produced it ('lr', a file
    # path): callers differ, and a built-in spec has to be resolved against THIS
    # parcel list anyway, so resolve it here rather than trusting every caller
    # to have done it. (cross_sectional_analysis_v2.save_combined_measures does
    # the same, which is why the anatomical half kept working when a batch
    # driver passed the raw string.)
    grouping = region_grouping.for_names(grouping, selected_names)
    if not grouping:
        print(f"  [{suffix}_combined] grouping merges nothing in this parcel set -- skipped")
        return None

    index = {n: i for i, n in enumerate(all_names)}
    sel_ts = all_ts[:, [index[n] for n in selected_names]]
    ts, names = region_grouping.combine_timeseries(sel_ts, selected_names, grouping)
    if len(names) < 2:
        print(f"  [{suffix}_combined] grouping leaves {len(names)} region(s) -- need 2, skipped")
        return None

    combined_suffix = f"{suffix}_combined"
    # The combined figure mirrors the analysis it came from: the standard matrix
    # is a heatmap, so its combined counterpart is a heatmap too, and the two can
    # be read side by side. (Choosing by node count instead made a 5-region
    # combination silently come out as a graph, with no heatmap to compare.)
    run_and_save_analysis(ts, names, names, out_dir, combined_suffix,
                          subject_name, session_name, use_graph_plot=False)

    # For a handful of regions the circular graph is genuinely easier to read
    # than a 5x5 heatmap, so write it as well rather than instead.
    if len(names) <= MAX_GRAPH_NODES:
        corr = np.corrcoef(ts, rowvar=False)
        np.fill_diagonal(corr, 1.0)
        plot_graph(corr, names, out_dir,
                   title=f"{subject_name} / {session_name} — Functional Connectivity, "
                         f"{combined_suffix} ({len(names)} regions)",
                   suffix=f"{combined_suffix}_graph")
        print(f"  [{combined_suffix}] also wrote fc_matrix_{combined_suffix}_graph.png/.svg")
    (out_dir / f"region_groups_{combined_suffix}.json").write_text(
        json.dumps(grouping.to_dict(), indent=2) + "\n")
    print(f"  [{combined_suffix}] {len(selected_names)} parcels -> {len(names)} composite region(s)"
          f" ({grouping.source})")
    for note in grouping.notes:
        print(f"    NOTE: {note}")
    return names


def run_and_save_analysis(all_ts, all_names, selected_names, out_dir, suffix,
                           subject_name, session_name, use_graph_plot):
    """Compute the FC matrix for selected_names and save every output into
    out_dir with suffix appended to the filename, so the standard analysis
    and both fixed graph analyses can share one folder without clobbering
    each other's files (or v1/v2's, thanks to the _hcpex suffix)."""
    name_to_idx = {n: i for i, n in enumerate(all_names)}
    cols = [name_to_idx[n] for n in selected_names]
    ts = all_ts[:, cols]

    corr = np.corrcoef(ts, rowvar=False)
    np.fill_diagonal(corr, 1.0)
    fisher_z = np.arctanh(np.clip(corr, -0.999999, 0.999999))

    save_matrix_csv(out_dir / f"fc_matrix_corr_{suffix}.csv", corr, selected_names)
    save_matrix_csv(out_dir / f"fc_matrix_fisherz_{suffix}.csv", fisher_z, selected_names)
    (out_dir / f"region_names_{suffix}.txt").write_text(
        "\n".join(f"{n}\t{classify(n)[1]}" for n in selected_names)
    )

    plot_title = f"{subject_name} / {session_name} — Functional Connectivity, {suffix} ({len(selected_names)} parcels)"
    if use_graph_plot:
        plot_graph(corr, selected_names, out_dir, title=plot_title, suffix=suffix)
    else:
        plot_fc_matrix(corr, selected_names, out_dir, title=plot_title, suffix=suffix)

    print(f"  [{suffix}] {len(selected_names)} parcels -> fc_matrix_corr_{suffix}.csv, "
          f"fc_matrix_fisherz_{suffix}.csv, fc_matrix_{suffix}.png/.svg, region_names_{suffix}.txt")


def main():
    raw_root = find_data_root()
    # Analysed_data mirrors sub-*/ses-*/ next to (not inside) the raw data root
    analysed_root = raw_root.parent / "Analysed_data"
    analysis_log = load_analysis_log(analysed_root)

    subjects = sorted(p for p in raw_root.glob("sub-*") if p.is_dir())
    if not subjects:
        sys.exit(f"No sub-* folders found under {raw_root}")
    subject_counts = [subject_total(analysis_log, p.name) for p in subjects]
    subject = choose_tile(subjects, subject_counts, "Subjects found:", formatter=lambda p: p.name)

    sessions = sorted(p for p in subject.glob("ses-*") if p.is_dir())
    if not sessions:
        sys.exit(f"No ses-* folders found under {subject}")
    if len(sessions) == 1:
        session = sessions[0]
        print(f"\nOnly one session found: {session.name} — using it.")
    else:
        session_counts = [analysis_log.get(subject.name, {}).get(p.name, 0) for p in sessions]
        session = choose_tile(sessions, session_counts, "Sessions found:", formatter=lambda p: p.name)

    vol_bold = session / VOL_BOLD_REL
    if not vol_bold.exists():
        sys.exit(f"Missing expected resting-state output under {session}\n  need: {VOL_BOLD_REL}")

    atlases_dir = find_atlases_dir(raw_root)
    hcpex_vol = atlases_dir / "HCPex_2mm.nii"
    hcpex_labels = atlases_dir / "HCPex_LookUpTable.txt"

    subject_dir = analysed_root / subject.name
    out_dir = subject_dir / session.name / "func"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n== Timeseries for {subject.name}/{session.name} (HCPex only) ==")
    ts_path = timeseries_cache_path(subject_dir, session.name)
    all_ts, all_names = load_or_extract_all_sources(ts_path, vol_bold, hcpex_vol, hcpex_labels)
    print(f"  {len(all_names)} parcels available in total")

    print("\n== Standard FC matrix -- pick parcels ==")
    standard_selection = choose_parcels(all_names)

    # Optional: treat groups of the selected parcels as single regions. The four
    # fixed graph/triangle analyses keep their own node sets -- the triangle
    # ones already combine L/R by construction.
    grouping = region_grouping.prompt_grouping(standard_selection)

    triangle_ts, triangle_names = build_triangle_ts(all_ts, all_names)

    print(f"\n== Computing all five analyses for {subject.name}/{session.name} ==")
    run_and_save_analysis(all_ts, all_names, standard_selection, out_dir, "standard_hcpex",
                           subject.name, session.name, use_graph_plot=False)
    run_and_save_analysis(all_ts, all_names, GRAPH_VTA, out_dir, "graph_vta_hcpex",
                           subject.name, session.name, use_graph_plot=True)
    run_and_save_analysis(all_ts, all_names, GRAPH_SN, out_dir, "graph_sn_hcpex",
                           subject.name, session.name, use_graph_plot=True)
    run_and_save_analysis(triangle_ts, triangle_names, TRIANGLE_VTA, out_dir, "triangle_vta_hcpex",
                           subject.name, session.name, use_graph_plot=True)
    run_and_save_analysis(triangle_ts, triangle_names, TRIANGLE_SN, out_dir, "triangle_sn_hcpex",
                           subject.name, session.name, use_graph_plot=True)

    if grouping:
        run_grouped_analysis(all_ts, all_names, standard_selection, grouping, out_dir,
                             "standard_hcpex", subject.name, session.name)

    record_analysis(analysed_root, subject.name, session.name)

    print(f"\nAll analyses saved to {out_dir}")


if __name__ == "__main__":
    main()
