#!/usr/bin/env python3
"""
Interactive cross-sectional anatomical analysis pipeline (v2 -- adds
midbrain / basal-forebrain volumes).

Same as cross_sectional_analysis_v1.py (cortical thickness + myelin,
Schaefer-400 parcellated; wmparc-based regional volumes for every
FreeSurfer-segmented structure), PLUS volumes for three structures FreeSurfer
does NOT segment -- Ventral Tegmental Area (VTA), Substantia Nigra (SNc/SNr),
and Nucleus Basalis (NbM) -- pulled from the HCPex atlas instead, the same
one run_fc_pipeline_v2.py uses for these regions in the FC pipeline.

Why this needs its own step (and its own version number): HCPex is a
template atlas defined in standard MNI152 space -- the same space every
subject's MNINonLinear/ data was already warped into. Counting voxels
directly in that standard-space atlas gives every subject the SAME volume
(it's literally the same set of voxels) -- useless for a cross-sectional
comparison, which needs values that vary by subject.

So this version computes it two ways, both saved side by side:
  - standard-space volume  -- voxel count in HCPex_2mm.nii directly (2mm
                               voxels). Identical across all subjects by
                               construction -- a fixed reference number only.
  - native-space volume    -- HCPex_2mm.nii warped into this subject's own
                               native T1w/acpc space via FSL applywarp,
                               using their own inverse nonlinear warp field
                               (MNINonLinear/xfms/standard2acpc_dc.nii.gz),
                               nearest-neighbor interpolation (discrete
                               labels), then counted at native voxel size.
                               This is the one that actually varies by
                               subject and is the correct value to use for
                               cross-sectional comparison.
Native-space counting reuses T1w/T1w_acpc_dc.nii.gz as the --ref grid (same
0.512 mm^3/voxel resolution as wmparc -- verified identical shape/affine on
sub-HCA6002236/ses-V3).

Even with HCPex available, Hippocampus/Amygdala/Thalamus/etc. still come
from wmparc (FreeSurfer), not HCPex -- FreeSurfer's per-subject segmentation
is the better source where it exists; spot-checked HCPex's own Hippocampus
label against wmparc's on sub-HCA6002236/ses-V3 and it reads ~half the
volume (cruder atlas boundary), confirming wmparc should stay authoritative
for anything it already covers. HCPex is used here only for the three
regions wmparc has no label for at all.

Standard command:
  python3 cross_sectional_analysis_v2.py

Output (under the chosen results root -- see project_paths.py; it defaults to
a sibling Analysed_data/ next to the raw data root, as before):
  <results root>/<subject>/<session>/anat/
    cortical_thickness_schaefer400.csv   (region, thickness_mm)
    myelin_schaefer400.csv               (region, myelin_ratio)
    subcortical_volumes_wmparc.csv       (label_id, region, voxel_count, volume_mm3)
    midbrain_basalforebrain_volumes_hcpex.csv
      (region, label_id, standard_voxel_count, standard_volume_mm3,
       native_voxel_count, native_volume_mm3)
    selected_regions_volumes.csv           (region, source, group, volume_mm3)
    subcortical_volumes_key_structures.png
    midbrain_basalforebrain_volumes_hcpex.png
    cortical_thickness_myelin_summary.png
    selected_regions_volumes_log.png
    selected_regions_volumes_linear.png
    manifest.txt

After the three standard figures are written, the script prompts for a set of
regions -- any mix of wmparc labels (key subcortical, other non-cortical,
ctx-*, wm-*) and the HCPex midbrain/basal-forebrain parcels -- and plots them
all together on one axis, as horizontal bars sorted largest-first. Selection
accepts numbers (1,5), ranges (10-14), group names, or 'all'; pressing Enter
takes the default (key subcortical + midbrain/basal forebrain, i.e. the union
of the two standard volume figures). The same selection is rendered twice, on
a log axis and a linear one: log keeps small nuclei like VTA legible next to
the ~7,000 mm^3 thalamus, linear preserves true proportions. All values on
this figure are NATIVE-space mm^3 so the two atlas sources are comparable --
the HCPex numbers used are the warped native ones, never the standard-space
ones. This step is interactive and lives only in main(), so the batch drivers
(cross_sectional_analysis_batch_v2.py, combined_analysis_batch_v2.py) are
unaffected and still write the three standard figures only.
  Own run-count log: <results root>/analysis_log_anat_v2.json (separate from
  v1's analysis_log_anat.json and the FC pipeline's logs).

Requires:
  Everything cross_sectional_analysis_v1.py requires, plus HCPex_2mm.nii +
  HCPex_LookUpTable.txt in the chosen atlases folder (already present from
  run_fc_pipeline_v2.py), and FSL's applywarp on PATH (checked via
  shutil.which -- confirmed present at /Users/jain/fsl/share/fsl/bin/applywarp
  on this machine).

Note on eTIV / head-size normalization: same caveat as v1 -- no aseg.stats
found anywhere in this dataset; all volumes here are raw mm^3.
"""
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import nibabel as nib

import project_paths
import region_grouping
import readline

ANALYSIS_LOG_NAME = "analysis_log_anat_v2.json"  # separate from v1's and the FC pipeline's logs
COUNT_COLOR_SCALE = 10
COLOR_ENABLED = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"

ANAT_REL = "anat"
ANAT_MNI_REL = f"{ANAT_REL}/MNINonLinear"
FSAVG32K_REL = f"{ANAT_MNI_REL}/fsaverage_LR32k"
WMPARC_REL = f"{ANAT_MNI_REL}/wmparc.nii.gz"
T1W_ACPC_REL = f"{ANAT_REL}/T1w/T1w_acpc_dc.nii.gz"
WARP_STD2ACPC_REL = f"{ANAT_MNI_REL}/xfms/standard2acpc_dc.nii.gz"

FALLBACK_ATLASES = Path("/Volumes/njainmpi/Project3_Aging/Raw_Data/atlases")
FALLBACK_FS_LUT = Path("/Applications/freesurfer/8.1.0/FreeSurferColorLUT.txt")

CORTEX_STRUCTURES = {"CIFTI_STRUCTURE_CORTEX_LEFT", "CIFTI_STRUCTURE_CORTEX_RIGHT"}

KEY_SUBCORTICAL_IDS = {
    "Thalamus-Proper": (10, 49),
    "Caudate": (11, 50),
    "Putamen": (12, 51),
    "Pallidum": (13, 52),
    "Hippocampus": (17, 53),
    "Amygdala": (18, 54),
    "Accumbens-area": (26, 58),
}

# HCPex base names (hemisphere suffix stripped) for the 3 structures FreeSurfer
# doesn't segment -- matched against HCPex_LookUpTable.txt names dynamically,
# not hardcoded IDs, since run_fc_pipeline_v2.py already established the LUT is
# the source of truth.
MIDBRAIN_BF_BASENAMES = [
    "Substantia_nigra_pars_compacta",
    "Substantia_nigra_pars_reticulata",
    "Ventral_tegmenta_area",
    "Nuclei_basal",
]


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
    log = load_analysis_log(analysed_root)
    counts = log.setdefault(subject_name, {})
    counts[session_name] = counts.get(session_name, 0) + 1
    save_analysis_log(analysed_root, log)


def subject_total(log, subject_name):
    return sum(log.get(subject_name, {}).values())


def _gradient_rgb(count, max_count=COUNT_COLOR_SCALE):
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


MIN_TILE_WIDTH = 8


def choose_tile(items, counts, label, formatter=str):
    names = [formatter(item) for item in items]
    n = len(items)
    count_strs = [f"({c}x)" for c in counts]
    ideal_w = max([len(nm) for nm in names] + [len(cs) for cs in count_strs] + [len(str(n)), 10]) + 2
    ideal_w = min(30, ideal_w)

    term_width = shutil.get_terminal_size(fallback=(100, 24)).columns
    tile_w = max(MIN_TILE_WIDTH, min(ideal_w, term_width - 2))
    per_tile_span = (tile_w + 2) + 1
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


def build_region_pool(volume_rows, name_to_id, native_rows):
    """Every region available to plot in the combined figure, as a list of
    dicts: {name, group, volume_mm3, source}.

    Both sources contribute NATIVE-space mm^3 so the two are directly
    comparable on one axis -- wmparc is native by construction, and the HCPex
    numbers used here are the warped native ones, never the standard-space
    ones (which are identical for every subject; see this file's docstring)."""
    key_ids = {lid for pair in KEY_SUBCORTICAL_IDS.values() for lid in pair}

    pool = []
    for lid, name, _cnt, vol in volume_rows:
        if lid in key_ids:
            group = "key"
        elif name.startswith("ctx-"):
            group = "cortex"
        elif name.startswith("wm-") or "WhiteMatter" in name:
            group = "wm"
        else:
            group = "other"
        pool.append({"name": name, "group": group, "volume_mm3": vol, "source": "wmparc"})

    for name in sorted(name_to_id, key=lambda n: name_to_id[n]):
        pool.append({"name": name, "group": "midbrain", "volume_mm3": native_rows[name][2],
                      "source": "HCPex (native)"})
    return pool


GROUP_LABELS = [
    ("key", "Key subcortical (wmparc)"),
    ("midbrain", "Midbrain / basal forebrain (HCPex, native space)"),
    ("other", "Other non-cortical (wmparc)"),
    ("cortex", "Cortical ribbon, ctx-* (wmparc)"),
    ("wm", "White matter, wm-* (wmparc)"),
]
DEFAULT_REGION_GROUPS = ("key", "midbrain")


def _print_region_pool(pool):
    term_width = shutil.get_terminal_size(fallback=(100, 24)).columns
    name_w = max(len(r["name"]) for r in pool)
    col_w = 6 + name_w + 12
    n_cols = max(1, min(3, term_width // (col_w + 2)))

    for group, title in GROUP_LABELS:
        idxs = [i for i, r in enumerate(pool) if r["group"] == group]
        if not idxs:
            continue
        print(f"\n  [{group}] {title} — {len(idxs)} region(s)")
        rows = (len(idxs) + n_cols - 1) // n_cols
        for row in range(rows):
            cells = []
            for col in range(n_cols):
                pos = col * rows + row
                if pos >= len(idxs):
                    continue
                i = idxs[pos]
                cells.append(f"{i + 1:>4}. {pool[i]['name']:<{name_w}} "
                              f"{pool[i]['volume_mm3']:>9.0f}")
            print("    " + "  ".join(cells))


def parse_region_selection(text, pool):
    """'1,5,10-14,midbrain' -> list of pool indices, in pool order, deduped.
    Returns None (with a message printed) if anything is unparseable."""
    selected = set()
    for token in (t.strip() for t in text.split(",")):
        if not token:
            continue
        low = token.lower()
        if low == "all":
            selected.update(range(len(pool)))
        elif low in {g for g, _t in GROUP_LABELS}:
            selected.update(i for i, r in enumerate(pool) if r["group"] == low)
        elif "-" in token and all(p.strip().isdigit() for p in token.split("-", 1)):
            start, end = (int(p) for p in token.split("-", 1))
            if not (1 <= start <= end <= len(pool)):
                print(f"  Range '{token}' is out of bounds (1-{len(pool)}).")
                return None
            selected.update(range(start - 1, end))
        elif token.isdigit():
            num = int(token)
            if not 1 <= num <= len(pool):
                print(f"  '{token}' is out of bounds (1-{len(pool)}).")
                return None
            selected.add(num - 1)
        else:
            print(f"  Could not understand '{token}'.")
            return None
    return sorted(selected)


def choose_regions(pool):
    """Interactive multi-select over the region pool. Empty input accepts the
    default (key subcortical + midbrain/basal forebrain)."""
    _print_region_pool(pool)
    group_names = ", ".join(g for g, _t in GROUP_LABELS)
    print(f"\n  Select regions for the combined figure: numbers (1,5), ranges (10-14),")
    print(f"  group names ({group_names}), or 'all'. Combine with commas.")
    print(f"  Press Enter for the default ({' + '.join(DEFAULT_REGION_GROUPS)}).")

    while True:
        text = input("Regions: ").strip()
        if not text:
            chosen = [i for i, r in enumerate(pool) if r["group"] in DEFAULT_REGION_GROUPS]
        else:
            chosen = parse_region_selection(text, pool)
            if chosen is None:
                continue
        if not chosen:
            print("  Nothing selected, try again.")
            continue
        print(f"  {len(chosen)} region(s) selected.")
        return [pool[i] for i in chosen]


def find_data_root():
    """Kept for callers that want the raw root alone. The three paths are now
    resolved together by project_paths.resolve() -- this just reads back what
    that saved, and falls back to asking if nothing is saved yet."""
    saved = project_paths.get_path("raw_root")
    if saved and saved.is_dir():
        return saved
    raw_root, _, _ = project_paths.resolve(project_paths.ANAT_ATLAS_FILES)
    return raw_root


def find_atlases_dir(raw_root):
    """The saved atlases folder, else the historical `<raw root>/atlases` ->
    network-fallback order. Retained because webapp_studio imports it directly.
    Requires all three files -- this pipeline needs Schaefer as well as HCPex."""
    saved = project_paths.get_path("atlases_dir")
    if saved and project_paths._has_atlas_files(saved, project_paths.ANAT_ATLAS_FILES):
        return saved
    derived = project_paths.default_atlases_dir(raw_root, project_paths.ANAT_ATLAS_FILES)
    if derived:
        if derived != raw_root / "atlases":
            print(f"  (no atlases/ folder under {raw_root} — using {derived})")
        return derived
    sys.exit(
        "Could not locate schaefer400_tianS1.dlabel.nii / HCPex_2mm.nii / "
        "HCPex_LookUpTable.txt under either the data root or the fallback "
        "location -- all three are required."
    )


def find_freesurfer_lut():
    fs_home = os.environ.get("FREESURFER_HOME")
    candidates = []
    if fs_home:
        candidates.append(Path(fs_home) / "FreeSurferColorLUT.txt")
    candidates.append(FALLBACK_FS_LUT)
    for c in candidates:
        if c.exists():
            return c
    sys.exit(
        "Could not locate FreeSurferColorLUT.txt (checked $FREESURFER_HOME and "
        f"{FALLBACK_FS_LUT}) -- it's required to name wmparc's volume labels."
    )


def find_applywarp():
    path = shutil.which("applywarp")
    if not path:
        sys.exit(
            "Could not find FSL's applywarp on PATH -- it's required to warp "
            "HCPex into native space for the midbrain/basal-forebrain volumes."
        )
    return path


def load_freesurfer_lut(lut_path):
    labels = {}
    for line in lut_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        labels[int(parts[0])] = parts[1]
    return labels


def load_hcpex_lut(lut_path):
    """id -> name, HCPex_LookUpTable.txt format (same as run_fc_pipeline_v2.py's
    load_hcpex_labels): '<idx> <Name> <r> <g> <b> <a>' per line, index 0 skipped."""
    labels = {}
    for line in lut_path.read_text().strip().splitlines():
        if line.startswith("#"):
            continue
        parts = line.split()
        idx = int(parts[0])
        if idx == 0:
            continue
        labels[idx] = parts[1]
    return labels


def build_cortex_vertex_lut(atlas_path):
    img = nib.load(str(atlas_path))
    data = img.get_fdata()[0]
    bm_axis = img.header.get_axis(1)
    label_axis = img.header.get_axis(0)
    label_table = label_axis.label[0]

    vertex_lut = {}
    for i, (structure, vtx) in enumerate(zip(bm_axis.name, bm_axis.vertex)):
        if structure in CORTEX_STRUCTURES:
            vertex_lut[(structure, int(vtx))] = int(data[i])

    label_names = {lid: name for lid, (name, _rgba) in label_table.items()}
    return vertex_lut, label_names


def parcellate_cortical_dscalar(dscalar_path, vertex_lut, label_names, return_counts=False):
    """Mean of the dscalar over each cortical parcel.

    With return_counts=True also returns {region: vertex_count}. Those counts
    are what makes combining parcels correct: the mean of two parcel means is
    only the parcel-pair mean when both parcels have the same vertex count,
    which Schaefer parcels do not -- see region_grouping.combine_values.
    """
    img = nib.load(str(dscalar_path))
    data = img.get_fdata()[0]
    bm_axis = img.header.get_axis(1)

    sums, counts = {}, {}
    for i, (structure, vtx) in enumerate(zip(bm_axis.name, bm_axis.vertex)):
        label_id = vertex_lut.get((structure, int(vtx)))
        if not label_id:
            continue
        sums[label_id] = sums.get(label_id, 0.0) + data[i]
        counts[label_id] = counts.get(label_id, 0) + 1

    rows = [(label_names.get(lid, f"label_{lid}"), sums[lid] / counts[lid])
            for lid in sorted(sums)]
    if return_counts:
        vertex_counts = {label_names.get(lid, f"label_{lid}"): counts[lid] for lid in sorted(sums)}
        return rows, vertex_counts
    return rows


def extract_subcortical_volumes(wmparc_path, fs_lut):
    img = nib.load(str(wmparc_path))
    data = img.get_fdata().astype(int)
    voxel_vol_mm3 = abs(np.linalg.det(img.affine[:3, :3]))

    ids, counts = np.unique(data, return_counts=True)
    rows = []
    for lid, cnt in zip(ids, counts):
        lid = int(lid)
        if lid == 0:
            continue
        name = fs_lut.get(lid, f"Unknown_{lid}")
        rows.append((lid, name, int(cnt), float(cnt) * voxel_vol_mm3))
    rows.sort(key=lambda r: r[0])
    return rows, voxel_vol_mm3


def midbrain_bf_label_ids(hcpex_lut):
    """{name: label_id} for the 8 L/R midbrain/basal-forebrain parcels,
    derived from the LUT's own names rather than hardcoded IDs."""
    wanted_names = {f"{base}_{hemi}" for base in MIDBRAIN_BF_BASENAMES for hemi in ("L", "R")}
    name_to_id = {name: lid for lid, name in hcpex_lut.items() if name in wanted_names}
    missing = wanted_names - set(name_to_id)
    if missing:
        sys.exit(f"HCPex_LookUpTable.txt is missing expected labels: {sorted(missing)}")
    return name_to_id


def extract_standard_space_hcpex_volumes(hcpex_2mm_path, name_to_id):
    img = nib.load(str(hcpex_2mm_path))
    data = img.get_fdata().astype(int)
    voxel_vol_mm3 = abs(np.linalg.det(img.affine[:3, :3]))
    rows = {}
    for name, lid in name_to_id.items():
        cnt = int((data == lid).sum())
        rows[name] = (lid, cnt, cnt * voxel_vol_mm3)
    return rows, voxel_vol_mm3


def warp_hcpex_to_native(applywarp_bin, hcpex_2mm_path, warp_field_path, ref_native_path, tmp_dir):
    out_path = Path(tmp_dir) / "HCPex_native.nii.gz"
    cmd = [
        applywarp_bin,
        f"--in={hcpex_2mm_path}",
        f"--ref={ref_native_path}",
        f"--warp={warp_field_path}",
        f"--out={out_path}",
        "--interp=nn",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"applywarp failed:\n{result.stderr}")
    return out_path


def extract_native_space_hcpex_volumes(native_hcpex_path, name_to_id):
    img = nib.load(str(native_hcpex_path))
    data = img.get_fdata().astype(int)
    voxel_vol_mm3 = abs(np.linalg.det(img.affine[:3, :3]))
    rows = {}
    for name, lid in name_to_id.items():
        cnt = int((data == lid).sum())
        rows[name] = (lid, cnt, cnt * voxel_vol_mm3)
    return rows, voxel_vol_mm3


def order_rows(rows, name_at=0):
    """Reorder (name, ...) tuples so each region's left and right sit adjacent,
    left first -- the same layout the FC matrices use, so a CSV and a
    connectivity map list their regions in the same sequence.

    Applied inside the writers rather than at each call site, so the interactive
    script, all three batch drivers and the browser app inherit it without
    needing to remember. Ordering only; no row is added or dropped."""
    order = region_grouping.order_by_region_pairs([r[name_at] for r in rows])
    by_name = {}
    for r in rows:
        by_name.setdefault(r[name_at], []).append(r)
    out = []
    for name in order:
        out.append(by_name[name].pop(0))
    return out


def save_named_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for name, value in order_rows(rows):
            writer.writerow([name, f"{value:.6f}"])


def save_volume_csv(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["label_id", "region", "voxel_count", "volume_mm3"])
        for lid, name, cnt, vol in order_rows(rows, name_at=1):
            writer.writerow([lid, name, cnt, f"{vol:.3f}"])


def save_midbrain_bf_csv(path, name_to_id, standard_rows, native_rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "region", "label_id",
            "standard_voxel_count", "standard_volume_mm3",
            "native_voxel_count", "native_volume_mm3",
        ])
        # was sorted by label id, which puts every _L before its _R (VTA_L is 390,
        # VTA_R is 423) -- pair order keeps a structure's two halves together
        for name in region_grouping.order_by_region_pairs(list(name_to_id)):
            lid = name_to_id[name]
            _lid_s, cnt_s, vol_s = standard_rows[name]
            _lid_n, cnt_n, vol_n = native_rows[name]
            writer.writerow([name, lid, cnt_s, f"{vol_s:.3f}", cnt_n, f"{vol_n:.3f}"])


def plot_key_volumes(volume_rows, out_dir, subject_name, session_name):
    by_id = {lid: (name, vol) for lid, name, _cnt, vol in volume_rows}
    structures = [s for s in KEY_SUBCORTICAL_IDS if all(i in by_id for i in KEY_SUBCORTICAL_IDS[s])]
    left_vals = [by_id[KEY_SUBCORTICAL_IDS[s][0]][1] for s in structures]
    right_vals = [by_id[KEY_SUBCORTICAL_IDS[s][1]][1] for s in structures]

    x = np.arange(len(structures))
    width = 0.35
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(x - width / 2, left_vals, width, label="Left", color="#1B8A80")
    ax.bar(x + width / 2, right_vals, width, label="Right", color="#E89B3C")
    ax.set_xticks(x); ax.set_xticklabels(structures, rotation=30, ha="right")
    ax.set_ylabel("Volume (mm³)")
    ax.set_title(f"{subject_name} / {session_name} — Key Subcortical Volumes (wmparc)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "subcortical_volumes_key_structures.png", dpi=200)
    plt.close(fig)


def plot_midbrain_bf_volumes(name_to_id, standard_rows, native_rows, out_dir, subject_name, session_name):
    names = sorted(name_to_id, key=lambda n: name_to_id[n])
    std_vals = [standard_rows[n][2] for n in names]
    native_vals = [native_rows[n][2] for n in names]

    x = np.arange(len(names))
    width = 0.35
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.bar(x - width / 2, std_vals, width, label="Standard space (fixed, same every subject)", color="#A4A3A4")
    ax.bar(x + width / 2, native_vals, width, label="Native space (subject-specific)", color="#2EC4B6")
    ax.set_xticks(x); ax.set_xticklabels(names, rotation=30, ha="right")
    ax.set_ylabel("Volume (mm³)")
    ax.set_title(f"{subject_name} / {session_name} — Midbrain / Basal-Forebrain Volumes (HCPex)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_dir / "midbrain_basalforebrain_volumes_hcpex.png", dpi=200)
    plt.close(fig)


WMPARC_COLOR = "#1B8A80"
HCPEX_COLOR = "#E89B3C"


def save_selected_regions_csv(path, selected):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["region", "source", "group", "volume_mm3"])
        for r in sorted(selected, key=lambda r: r["volume_mm3"], reverse=True):
            writer.writerow([r["name"], r["source"], r["group"], f"{r['volume_mm3']:.3f}"])


def _plot_selected_regions_axis(selected, out_path, subject_name, session_name, log_scale):
    """One horizontal-bar figure of every selected region, largest at top."""
    rows = sorted(selected, key=lambda r: r["volume_mm3"], reverse=True)
    names = [r["name"] for r in rows]
    vals = [r["volume_mm3"] for r in rows]
    colors = [HCPEX_COLOR if r["source"].startswith("HCPex") else WMPARC_COLOR for r in rows]

    height = max(4.0, 0.28 * len(rows) + 1.8)
    fig, ax = plt.subplots(figsize=(11, height))
    y = np.arange(len(rows))[::-1]
    ax.barh(y, vals, color=colors, height=0.7)

    positive = [v for v in vals if v > 0]
    if log_scale:
        if not positive:
            plt.close(fig)
            return 0
        floor = min(positive) * 0.5
        ax.set_xscale("log")
        ax.set_xlim(left=floor, right=max(positive) * 2.0)
        scale_note = "log scale"
    else:
        ax.set_xlim(left=0, right=(max(vals) * 1.18) if positive else 1.0)
        scale_note = "linear scale"

    for yi, val in zip(y, vals):
        if val > 0:
            ax.text(val, yi, f" {val:,.0f}", va="center", fontsize=7)

    ax.set_yticks(y)
    ax.set_yticklabels(names, fontsize=7)
    ax.set_xlabel("Native-space volume (mm³)")
    ax.set_title(f"{subject_name} / {session_name} — Selected Regions "
                  f"({len(rows)} regions, {scale_note})")
    ax.legend(handles=[
        plt.Rectangle((0, 0), 1, 1, color=WMPARC_COLOR, label="wmparc (FreeSurfer)"),
        plt.Rectangle((0, 0), 1, 1, color=HCPEX_COLOR, label="HCPex, warped to native"),
    ], loc="lower right", fontsize=8)
    ax.grid(axis="x", alpha=0.3, linestyle=":")
    ax.set_axisbelow(True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=200)
    plt.close(fig)
    return len(vals) - len(positive)


def plot_selected_regions(selected, out_dir, subject_name, session_name):
    """Writes the combined selected-region figure twice -- log and linear.
    Returns the number of selected regions with zero volume (omitted from the
    log figure, which cannot represent them)."""
    _plot_selected_regions_axis(selected, out_dir / "selected_regions_volumes_linear.png",
                                 subject_name, session_name, log_scale=False)
    return _plot_selected_regions_axis(selected, out_dir / "selected_regions_volumes_log.png",
                                        subject_name, session_name, log_scale=True)


def plot_thickness_myelin_summary(thickness_rows, myelin_rows, out_dir, subject_name, session_name):
    thick_vals = [v for _n, v in thickness_rows]
    myelin_vals = [v for _n, v in myelin_rows]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].hist(thick_vals, bins=30, color="#2EC4B6", edgecolor="white")
    axes[0].set_title("Cortical thickness (400 parcels)")
    axes[0].set_xlabel("mm")
    axes[1].hist(myelin_vals, bins=30, color="#0B2545", edgecolor="white")
    axes[1].set_title("Myelin (T1w/T2w, BC) (400 parcels)")
    axes[1].set_xlabel("ratio")
    fig.suptitle(f"{subject_name} / {session_name} — Schaefer-400 Cortical Summary")
    fig.tight_layout()
    fig.savefig(out_dir / "cortical_thickness_myelin_summary.png", dpi=200)
    plt.close(fig)


def save_hemisphere_measures(out_dir, thickness_rows, myelin_rows, volume_rows,
                              name_to_id, standard_rows, native_rows):
    """Left-only and right-only copies of every anatomical measure -- maps 1 and 2
    of the same four-view scheme the FC pipeline uses:

        1. <measure>_left      only left-hemisphere regions
        2. <measure>_right     only right-hemisphere regions
        3. <measure>           every region        (written by the caller already)
        4. <measure>_combined  L/R pairs merged    (save_combined_measures)

    Hemisphere comes from region_grouping.split_hemisphere(), so each measure is
    split in its OWN naming convention -- Schaefer '7Networks_LH_...', wmparc
    'Left-...'/'ctx-lh-...', HCPex '..._L'. Structures with no hemisphere
    (Brain-Stem, CSF, 4th-Ventricle) appear in neither file, which is correct:
    they belong only to the all-regions and combined views.

    Returns the list of filenames written."""
    def hemi_of(name):
        return region_grouping.split_hemisphere(name)[1]

    written = []
    for side in ("L", "R"):
        tag = "left" if side == "L" else "right"

        for rows, header, filename in (
            (thickness_rows, ["region", "thickness_mm"], f"cortical_thickness_schaefer400_{tag}.csv"),
            (myelin_rows, ["region", "myelin_ratio"], f"myelin_schaefer400_{tag}.csv"),
        ):
            subset = [(n, v) for n, v in rows if hemi_of(n) == side]
            if not subset:
                continue
            save_named_csv(out_dir / filename, header, subset)
            written.append(filename)

        vol_subset = [r for r in volume_rows if hemi_of(r[1]) == side]
        if vol_subset:
            fn = f"subcortical_volumes_wmparc_{tag}.csv"
            save_volume_csv(out_dir / fn, vol_subset)
            written.append(fn)

        mb_subset = {n: i for n, i in name_to_id.items() if hemi_of(n) == side}
        if mb_subset:
            fn = f"midbrain_basalforebrain_volumes_hcpex_{tag}.csv"
            save_midbrain_bf_csv(out_dir / fn, mb_subset, standard_rows, native_rows)
            written.append(fn)

    return written


def save_combined_measures(out_dir, grouping_spec, subject_name, session_name,
                            thickness_rows, thickness_counts, myelin_rows, myelin_counts,
                            volume_rows, name_to_id, standard_rows, native_rows,
                            tag="combined"):
    """Write a composite-region copy of every anatomical measure.

    Each measure is grouped in its OWN name space -- Schaefer parcels for
    thickness/myelin, FreeSurfer labels for wmparc volumes, HCPex labels for the
    midbrain set -- so one spec ('lr', or a grouping file listing parcels from
    any of them) covers all three. Nothing here overwrites the per-parcel
    output: every file gets a _combined suffix.

    Aggregation follows what the number is: volumes and voxel counts SUM, while
    thickness and myelin take a vertex-count-weighted mean (see
    region_grouping's module docstring).

    `tag` names the output files. It defaults to "combined", which is view 4 of
    the fixed four (the built-in left/right rule). A caller passing some OTHER
    grouping (--groups component, a custom JSON) must pass its own tag, or it
    would write straight over that fixed view.
    """
    written, summaries = [], []

    # --- cortical scalars: weighted mean over vertices
    for key, rows, counts, header, filename in (
        ("cortical thickness", thickness_rows, thickness_counts, ["region", "thickness_mm"],
         f"cortical_thickness_schaefer400_{tag}.csv"),
        ("myelin", myelin_rows, myelin_counts, ["region", "myelin_ratio"],
         f"myelin_schaefer400_{tag}.csv"),
    ):
        names = [n for n, _v in rows]
        grouping = region_grouping.for_names(grouping_spec, names)
        if not grouping:
            continue
        combined = region_grouping.combine_values(rows, grouping, rule="weighted_mean", weights=counts)
        combined_counts = region_grouping.combine_weights(counts, grouping)
        path = out_dir / filename
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(header + ["vertex_count", "n_parcels"])
            members = dict(grouping.resolve(names))
            for name, value in order_rows(combined):
                writer.writerow([name, f"{value:.6f}", int(round(combined_counts.get(name, 0))),
                                 len(members.get(name, [name]))])
        written.append(path.name)
        summaries.append(f"{key}: {len(names)} parcels -> {len(combined)} regions")

    # --- volumes: sum
    vol_pairs = [(name, vol) for _lid, name, _cnt, vol in volume_rows]
    vox_counts = {name: cnt for _lid, name, cnt, _vol in volume_rows}
    grouping = region_grouping.for_names(grouping_spec, [n for n, _v in vol_pairs])
    if grouping:
        combined = region_grouping.combine_values(vol_pairs, grouping, rule="sum")
        combined_vox = region_grouping.combine_weights(vox_counts, grouping)
        members = dict(grouping.resolve([n for n, _v in vol_pairs]))
        path = out_dir / f"subcortical_volumes_wmparc_{tag}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["region", "voxel_count", "volume_mm3", "n_parcels", "members"])
            for name, value in order_rows(combined):
                parts = members.get(name, [name])
                writer.writerow([name, int(round(combined_vox.get(name, 0))), f"{value:.3f}",
                                 len(parts), ";".join(parts)])
        written.append(path.name)
        summaries.append(f"wmparc volumes: {len(vol_pairs)} labels -> {len(combined)} regions")

        # reuse the existing selected-region figure for the combined volumes --
        # same house style, log + linear, largest first
        top = sorted(combined, key=lambda r: r[1], reverse=True)[:30]
        selected = [{"name": n, "group": "key", "volume_mm3": v, "source": "wmparc"} for n, v in top]
        _plot_selected_regions_axis(selected, out_dir / "combined_volumes_wmparc.png",
                                     subject_name, session_name, log_scale=False)
        written.append("combined_volumes_wmparc.png")

    # --- midbrain / basal forebrain: sum, both spaces
    mb_names = sorted(name_to_id, key=lambda n: name_to_id[n])
    grouping = region_grouping.for_names(grouping_spec, mb_names)
    if grouping:
        native_pairs = [(n, native_rows[n][2]) for n in mb_names]
        standard_pairs = [(n, standard_rows[n][2]) for n in mb_names]
        native_combined = dict(region_grouping.combine_values(native_pairs, grouping, rule="sum"))
        standard_combined = dict(region_grouping.combine_values(standard_pairs, grouping, rule="sum"))
        members = dict(grouping.resolve(mb_names))
        path = out_dir / f"midbrain_basalforebrain_volumes_hcpex_{tag}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["region", "standard_volume_mm3", "native_volume_mm3", "n_parcels", "members"])
            for name in region_grouping.order_by_region_pairs(
                    [n for n, _m in grouping.resolve(mb_names)]):
                writer.writerow([name, f"{standard_combined[name]:.3f}", f"{native_combined[name]:.3f}",
                                 len(members.get(name, [name])), ";".join(members.get(name, [name]))])
        written.append(path.name)
        summaries.append(f"midbrain/basal forebrain: {len(mb_names)} labels -> {len(native_combined)} regions")

    if grouping:
        (out_dir / f"region_groups_anat_{tag}.json").write_text(
            json.dumps(grouping.to_dict(), indent=2) + "\n")
        written.append(f"region_groups_anat_{tag}.json")
    return written, summaries


def write_manifest(out_dir, subject_name, session_name, thickness_path, myelin_path,
                    wmparc_path, atlas_path, lut_path, voxel_vol_mm3,
                    hcpex_path, warp_field_path, ref_native_path, native_voxel_vol_mm3,
                    selected_regions=None):
    text = (
        f"Cross-sectional anatomical analysis (v2)\n"
        f"Subject: {subject_name}\nSession: {session_name}\n\n"
        f"Cortical thickness source: {thickness_path}\n"
        f"Myelin source:             {myelin_path}\n"
        f"Volume source (wmparc):    {wmparc_path}\n"
        f"  voxel volume used:       {voxel_vol_mm3:.6f} mm^3 (read from file affine)\n"
        f"Cortical atlas:            {atlas_path}\n"
        f"Volume label lookup:       {lut_path}\n\n"
        f"Midbrain/basal-forebrain (VTA, SN, NbM) source: {hcpex_path}\n"
        f"  Standard-space volume: raw voxel count in {hcpex_path.name} (2mm) -- "
        f"identical for every subject by construction, reference only.\n"
        f"  Native-space volume:   {hcpex_path.name} warped via FSL applywarp "
        f"(--warp={warp_field_path}, --ref={ref_native_path}, --interp=nn), "
        f"then counted at {native_voxel_vol_mm3:.6f} mm^3/voxel -- this is the "
        f"one that varies by subject and is the correct value for cross-sectional comparison.\n\n"
        f"No eTIV/aseg.stats found in this dataset's structural output -- all "
        f"volumes here are raw mm^3, not head-size-normalized.\n"
    )
    if selected_regions:
        names = ", ".join(r["name"] for r in selected_regions)
        text += (
            f"\nCombined selected-region figure "
            f"(selected_regions_volumes_{{log,linear}}.png, selected_regions_volumes.csv):\n"
            f"  {len(selected_regions)} region(s) chosen interactively this run, plotted "
            f"together on one axis in native-space mm^3 (wmparc values as-is; HCPex values "
            f"the warped native ones, never the standard-space ones).\n"
            f"  Regions: {names}\n"
        )
    (out_dir / "manifest.txt").write_text(text)


def main():
    # All three folders at once -- asked on the first run, confirmed on later
    # ones, saved to aabc_paths.json either way. The results root defaults to
    # the historical <raw root>/../Analysed_data but is no longer forced to it.
    raw_root, analysed_root, atlases_dir = project_paths.resolve(project_paths.ANAT_ATLAS_FILES)
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

    fsavg_dir = session / FSAVG32K_REL
    wmparc_path = session / WMPARC_REL
    ref_native_path = session / T1W_ACPC_REL
    warp_field_path = session / WARP_STD2ACPC_REL
    thickness_matches = sorted(fsavg_dir.glob("*.corrThickness_MSMAll.32k_fs_LR.dscalar.nii"))
    myelin_matches = sorted(fsavg_dir.glob("*.MyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii"))

    missing = []
    if not thickness_matches:
        missing.append(f"{FSAVG32K_REL}/*.corrThickness_MSMAll.32k_fs_LR.dscalar.nii")
    if not myelin_matches:
        missing.append(f"{FSAVG32K_REL}/*.MyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii")
    if not wmparc_path.exists():
        missing.append(WMPARC_REL)
    if not ref_native_path.exists():
        missing.append(T1W_ACPC_REL)
    if not warp_field_path.exists():
        missing.append(WARP_STD2ACPC_REL)
    if missing:
        sys.exit(f"Missing expected anatomical outputs under {session / 'anat'}\n  need:\n    " +
                  "\n    ".join(missing))

    thickness_path = thickness_matches[0]
    myelin_path = myelin_matches[0]

    atlas_path = atlases_dir / "schaefer400_tianS1.dlabel.nii"
    hcpex_path = atlases_dir / "HCPex_2mm.nii"
    hcpex_lut_path = atlases_dir / "HCPex_LookUpTable.txt"
    lut_path = find_freesurfer_lut()
    fs_lut = load_freesurfer_lut(lut_path)
    hcpex_lut = load_hcpex_lut(hcpex_lut_path)
    applywarp_bin = find_applywarp()

    out_dir = analysed_root / subject.name / session.name / "anat"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n== Cross-sectional anatomical analysis (v2) for {subject.name}/{session.name} ==")

    vertex_lut, label_names = build_cortex_vertex_lut(atlas_path)

    print("  Parcellating cortical thickness (Schaefer-400)...")
    thickness_rows, thickness_counts = parcellate_cortical_dscalar(
        thickness_path, vertex_lut, label_names, return_counts=True)
    save_named_csv(out_dir / "cortical_thickness_schaefer400.csv", ["region", "thickness_mm"], thickness_rows)

    print("  Parcellating myelin map / T1w-T2w ratio (Schaefer-400)...")
    myelin_rows, myelin_counts = parcellate_cortical_dscalar(
        myelin_path, vertex_lut, label_names, return_counts=True)
    save_named_csv(out_dir / "myelin_schaefer400.csv", ["region", "myelin_ratio"], myelin_rows)

    print("  Computing subcortical/regional volumes (wmparc)...")
    volume_rows, voxel_vol_mm3 = extract_subcortical_volumes(wmparc_path, fs_lut)
    save_volume_csv(out_dir / "subcortical_volumes_wmparc.csv", volume_rows)
    print(f"    {len(volume_rows)} labels found, voxel volume = {voxel_vol_mm3:.4f} mm^3")

    print("  Computing midbrain/basal-forebrain volumes (VTA, SN, NbM -- HCPex)...")
    name_to_id = midbrain_bf_label_ids(hcpex_lut)
    standard_rows, std_voxel_vol = extract_standard_space_hcpex_volumes(hcpex_path, name_to_id)
    with tempfile.TemporaryDirectory() as tmp_dir:
        native_hcpex_path = warp_hcpex_to_native(applywarp_bin, hcpex_path, warp_field_path,
                                                  ref_native_path, tmp_dir)
        native_rows, native_voxel_vol = extract_native_space_hcpex_volumes(native_hcpex_path, name_to_id)
    save_midbrain_bf_csv(out_dir / "midbrain_basalforebrain_volumes_hcpex.csv",
                          name_to_id, standard_rows, native_rows)
    print(f"    {len(name_to_id)} labels: standard-space voxel = {std_voxel_vol:.4f} mm^3, "
          f"native-space voxel = {native_voxel_vol:.4f} mm^3")

    plot_key_volumes(volume_rows, out_dir, subject.name, session.name)
    plot_midbrain_bf_volumes(name_to_id, standard_rows, native_rows, out_dir, subject.name, session.name)
    plot_thickness_myelin_summary(thickness_rows, myelin_rows, out_dir, subject.name, session.name)

    pool = build_region_pool(volume_rows, name_to_id, native_rows)
    selected = choose_regions(pool)
    save_selected_regions_csv(out_dir / "selected_regions_volumes.csv", selected)
    n_zero = plot_selected_regions(selected, out_dir, subject.name, session.name)
    if n_zero:
        print(f"    (note: {n_zero} selected region(s) have zero volume — shown in the "
              f"linear figure, omitted from the log one)")

    # Every measure is written as the same four views the FC pipeline uses:
    # left only, right only, all regions (already written above), and L/R
    # combined. No prompt -- these four are fixed. Applied across all three name
    # spaces (Schaefer, wmparc, HCPex), each split in its own convention.
    hemi_files = save_hemisphere_measures(
        out_dir, thickness_rows, myelin_rows, volume_rows,
        name_to_id, standard_rows, native_rows)
    print(f"  Hemisphere copies written: {len(hemi_files)} file(s)")

    combined_files, summaries = save_combined_measures(
        out_dir, region_grouping.DEFAULT_SPEC, subject.name, session.name,
        thickness_rows, thickness_counts, myelin_rows, myelin_counts,
        volume_rows, name_to_id, standard_rows, native_rows)
    print("  Combined-region copies written:")
    for line in summaries:
        print(f"    {line}")

    write_manifest(out_dir, subject.name, session.name, thickness_path, myelin_path,
                    wmparc_path, atlas_path, lut_path, voxel_vol_mm3,
                    hcpex_path, warp_field_path, ref_native_path, native_voxel_vol,
                    selected_regions=selected)

    record_analysis(analysed_root, subject.name, session.name)

    print(f"\nAll analyses saved to {out_dir}")
    print("  cortical_thickness_schaefer400.csv, myelin_schaefer400.csv,")
    print("  subcortical_volumes_wmparc.csv, midbrain_basalforebrain_volumes_hcpex.csv,")
    print("  selected_regions_volumes.csv,")
    print("  subcortical_volumes_key_structures.png, midbrain_basalforebrain_volumes_hcpex.png,")
    print("  cortical_thickness_myelin_summary.png,")
    print("  selected_regions_volumes_log.png, selected_regions_volumes_linear.png,")
    print("  manifest.txt")


if __name__ == "__main__":
    main()
