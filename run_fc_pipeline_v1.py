#!/usr/bin/env python3
"""
Interactive functional connectivity pipeline (v2 -- unified volumetric extraction
for subcortex/midbrain/basal forebrain).

Prompts for:
  - path to the raw data root (contains sub-*/ses-*/... HCP-processed sessions)
  - which subject to run, from those found under that root
  - which session, if the subject has more than one
  - which parcels to include in the "standard" FC matrix, out of all 427 available

Every run always computes five analyses for the chosen session -- no mode
menu, since four of the five have no choice to make:
  - "standard"     -- FC matrix over whatever parcels you pick above
  - "graph_vta"    -- fixed NbM <-> VTA <-> Hippocampus graph, left/right separate
  - "graph_sn"     -- fixed NbM <-> SN <-> Hippocampus graph, left/right separate
  - "triangle_vta" -- same 3 regions as graph_vta, but left/right pairs are
                       first averaged into one composite timeseries each, so
                       it's a plain 3-node triangle (NbM, VTA, Hippocampus)
  - "triangle_sn"  -- same idea for SN: SNc+SNr averaged into one "SN" node,
                       NbM and Hippocampus averaged the same way as above,
                       giving a plain 3-node triangle (NbM, SN, Hippocampus)
All five are saved into Analysed_data/<subject>/<session>/func/ (no
per-analysis subfolder), with each file name suffixed by which analysis
produced it (e.g. fc_matrix_corr_standard.csv vs. fc_matrix_corr_graph_vta.csv
vs. fc_matrix_corr_triangle_vta.csv), so nothing collides.

v1 (run_fc_pipeline.py) extracted cortex+subcortex together from the CIFTI
dtseries (grayordinate space) and midbrain+basal forebrain separately from the
volumetric BOLD (voxel space) -- four parcels living in two different spaces
with three different extraction methods. This version keeps cortex (Group A)
on the grayordinate path, since MSMAll surface registration is exactly why
HCP-processed cortex is preferable to a volumetric equivalent, but moves
Group B (Tian-S1 subcortex) onto the SAME volumetric BOLD file and mask-mean
method as Groups C and D -- Tian-S1's native distribution is volumetric
anyway, so nothing is lost doing this for subcortex specifically:
  - 400 cortical parcels                          Schaefer-400 (7 networks)
                                                     -- CIFTI dtseries, surface space
  - 16 subcortical structures incl. hippocampus   Tian-S1 (coarse scale)
                                                     -- volumetric BOLD, mask mean
  - 5 midbrain nuclei (SNc, SNr, VTA, PBP, RN)     CIT168 / Pauli et al. 2018
                                                     -- volumetric BOLD, probability-weighted
  - 6 basal forebrain structures (mammillary       HCPex / Huang & Rolls et al. 2022
    bodies, septal nucleus, nucleus basalis)         -- volumetric BOLD, mask mean

Groups B, C, and D now all come from one file (the volumetric BOLD) and one
family of methods (mask mean, with C additionally probability-weighted) --
only Group A still comes from a different file/space.

Results are written to a sibling "Analysed_data" tree next to the raw data
root, mirroring its sub-<subject>/ses-<session>/ layout, under a "func"
subfolder -- mirroring the anat/ subfolder cross_sectional_analysis_v2.py
uses for its own outputs. The raw data tree itself is never written to.

The full 427-parcel timeseries extraction (the slow part: wb_command plus
three volumetric mask/probability extractions) runs once per session and is
cached as a plain CSV at the subject level --
Analysed_data/<subject>/timeseries_<session>.csv -- named after the session
so each session's cache is unambiguous, and separate from the session's own
results folder since it's an input shared by every analysis rather than an
output of any one of them. Any later run against that same session loads the
cached CSV instead of re-extracting. Delete that file to force a fresh
extraction (e.g. after updating an atlas file).

All saved matrices/timeseries are plain CSV (openable in Excel, Numbers, or
`pandas.read_csv`) rather than NumPy's .npy binary format, which needs
`numpy.load(...)` and isn't human-readable.
"""
import csv
import glob
import json
import os
import re
import readline
import shutil
import subprocess
import sys
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import nibabel as nib

TIAN_ROIS = [
    "HIP-rh", "AMY-rh", "pTHA-rh", "aTHA-rh", "NAc-rh", "GP-rh", "PUT-rh", "CAU-rh",
    "HIP-lh", "AMY-lh", "pTHA-lh", "aTHA-lh", "NAc-lh", "GP-lh", "PUT-lh", "CAU-lh",
]
MIDBRAIN_ROIS = ["SNc", "SNr", "VTA", "PBP", "RN"]
BASAL_FOREBRAIN_ROIS = [
    "Mammillary_bodies-lh", "Mammillary_bodies-rh",
    "Septal_nucleus-lh", "Septal_nucleus-rh",
    "Nucleus_basalis-lh", "Nucleus_basalis-rh",
]
PROB_THRESHOLD = 0.05  # voxels below this weight are excluded from the weighted mean

# Analysis 2 / 3: fixed node sets for the graph plots, pulled straight from the
# raw (left/right-separate) parcel names -- no averaging of L/R or SNc/SNr.
GRAPH_VTA = ["VTA", "Nucleus_basalis-lh", "Nucleus_basalis-rh", "HIP-rh", "HIP-lh"]
GRAPH_SN = ["SNc", "Nucleus_basalis-lh", "Nucleus_basalis-rh", "SNr", "HIP-rh", "HIP-lh"]
GRAPH_DISPLAY = {  # node label only -- no atlas/source text on the diagram
    "Nucleus_basalis-lh": "NbM (L)",
    "Nucleus_basalis-rh": "NbM (R)",
    "VTA": "VTA",
    "SNc": "SNc",
    "SNr": "SNr",
    "HIP-lh": "HC (L)",
    "HIP-rh": "HC (R)",
    "NbM": "NbM",
    "Hippocampus": "HC",
    "SN": "SN",
}

# Analysis 4 / 5: simple 3-node triangles -- unlike GRAPH_VTA/GRAPH_SN above,
# left/right pairs (and SNc+SNr) are first averaged into one combined
# timeseries per composite region, so each triangle has exactly 3 nodes.
TRIANGLE_COMBINE_MAP = {
    "NbM": ["Nucleus_basalis-lh", "Nucleus_basalis-rh"],
    "Hippocampus": ["HIP-lh", "HIP-rh"],
    "SN": ["SNc", "SNr"],
}
TRIANGLE_VTA = ["NbM", "VTA", "Hippocampus"]
TRIANGLE_SN = ["NbM", "SN", "Hippocampus"]

ANALYSIS_LOG_NAME = "analysis_log_v2.json"  # separate from v1's log -- different pipeline, own run history
COUNT_COLOR_SCALE = 10  # subjects/sessions analysed this many times or more render fully green
COLOR_ENABLED = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"

DTSERIES_REL = "func/MNINonLinear/Results/rfMRI_REST/rfMRI_REST_Atlas_MSMAll_hp0_clean_rclean_tclean.dtseries.nii"
VOL_BOLD_REL = "func/MNINonLinear/Results/rfMRI_REST/rfMRI_REST_hp0_clean_rclean_tclean.nii.gz"

# used only if the raw data root itself has no atlases/ subfolder
FALLBACK_ATLASES = Path("/Volumes/njainmpi/Project3_Aging/Raw_Data/atlases")

ATLAS_GROUPS = {
    "A": "Schaefer-400 (cortex, 7 networks)",
    "B": "Tian-S1 (subcortex, incl. hippocampus)",
    "C": "CIT168 / Pauli 2018 (midbrain nuclei)",
    "D": "HCPex basal forebrain (Huang & Rolls et al. 2022)",
}

CARD_COLORS = {  # purely cosmetic per-group accent, unrelated to the analysis-count gradient
    "A": (90, 160, 250),
    "B": (190, 130, 250),
    "C": (90, 220, 190),
    "D": (230, 180, 90),
}


def classify(name):
    """Return the (group_letter, atlas_label) a parcel name belongs to."""
    if name.startswith("7Networks_"):
        return "A", ATLAS_GROUPS["A"]
    if name in MIDBRAIN_ROIS:
        return "C", ATLAS_GROUPS["C"]
    if name in BASAL_FOREBRAIN_ROIS:
        return "D", ATLAS_GROUPS["D"]
    return "B", ATLAS_GROUPS["B"]  # Tian-S1 short codes: HIP-lh, AMY-rh, pTHA-lh, ...


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
    if (candidate / "schaefer400_tianS1.dlabel.nii").exists():
        return candidate
    if (FALLBACK_ATLASES / "schaefer400_tianS1.dlabel.nii").exists():
        print(f"  (no atlases/ folder under {raw_root} — using {FALLBACK_ATLASES})")
        return FALLBACK_ATLASES
    sys.exit(
        "Could not locate atlas files (schaefer400_tianS1.dlabel.nii, "
        "Tian_Subcortex_S1_3T.nii, Tian_Subcortex_S1_labels.txt, "
        "CIT168_prob_func2mm.nii.gz, CIT168_labels.txt, HCPex_2mm.nii, "
        "HCPex_basal_forebrain_labels.txt) under either the data root or the "
        "fallback location."
    )


def parcellate_cortex(dtseries, atlas, out_dir):
    """wb_command -cifti-parcellate: dtseries -> 416-parcel ptseries, keeping only
    the 400 Schaefer cortical columns. Subcortex is dropped here on purpose --
    it now comes from the volumetric Tian-S1 atlas instead (see
    extract_deterministic_roi_timeseries), to keep B/C/D on one consistent
    volumetric extraction path while cortex stays on the grayordinate path."""
    ptseries = out_dir / "cortex.ptseries.nii"
    subprocess.run(
        [
            "wb_command", "-cifti-parcellate",
            str(dtseries), str(atlas), "COLUMN", str(ptseries),
            "-method", "MEAN",
        ],
        check=True,
    )
    img = nib.load(str(ptseries))
    data = img.get_fdata()  # time x parcels
    names = list(img.header.get_axis(1).name)
    ptseries.unlink()  # fully reproducible from the dtseries, don't keep it around

    cortex_idx = [i for i, n in enumerate(names) if n.startswith("7Networks_")]
    return data[:, cortex_idx], [names[i] for i in cortex_idx]


def extract_midbrain_timeseries(vol_bold, cit168_prob, cit168_labels):
    """Probability-weighted mean timeseries for each CIT168 midbrain nucleus."""
    labels = {}
    for line in cit168_labels.read_text().strip().splitlines():
        idx, name = line.split()
        labels[name] = int(idx)

    prob_img = nib.load(str(cit168_prob))
    prob_data = prob_img.get_fdata()  # x y z nucleus

    bold_img = nib.load(str(vol_bold))
    bold_data = bold_img.get_fdata(dtype=np.float32)  # x y z time

    if prob_data.shape[:3] != bold_data.shape[:3]:
        raise RuntimeError(
            f"Grid mismatch: CIT168 atlas is {prob_data.shape[:3]}, BOLD is {bold_data.shape[:3]}. "
            "The CIT168 atlas was pre-resampled to a specific 2mm grid -- this session isn't on it."
        )

    n_timepoints = bold_data.shape[-1]
    out = np.zeros((n_timepoints, len(MIDBRAIN_ROIS)), dtype=np.float64)

    for i, roi in enumerate(MIDBRAIN_ROIS):
        w = prob_data[..., labels[roi]].astype(np.float64)
        w[w < PROB_THRESHOLD] = 0.0
        if w.sum() == 0:
            raise RuntimeError(f"No voxels survive threshold for {roi}")
        w /= w.sum()
        out[:, i] = np.tensordot(bold_data, w, axes=([0, 1, 2], [0, 1, 2]))

    del bold_data  # free ~7GB before correlation step
    return out


def extract_deterministic_roi_timeseries(vol_bold, label_vol, labels_txt, roi_names):
    """Unweighted mean timeseries for each ROI in a deterministic (hard-labeled)
    volumetric atlas -- used for both Tian-S1 subcortex and HCPex basal forebrain,
    which (unlike CIT168) are single-integer-per-voxel parcellations, not a
    probability volume."""
    labels = {}
    for line in labels_txt.read_text().strip().splitlines():
        idx, name = line.split()
        labels[name] = int(idx)

    label_img = nib.load(str(label_vol))
    label_data = label_img.get_fdata()  # x y z

    bold_img = nib.load(str(vol_bold))
    bold_data = bold_img.get_fdata(dtype=np.float32)  # x y z time

    if label_data.shape[:3] != bold_data.shape[:3]:
        raise RuntimeError(
            f"Grid mismatch: {label_vol.name} is {label_data.shape[:3]}, BOLD is {bold_data.shape[:3]}. "
            f"{label_vol.name} was verified against CIT168's grid -- this session isn't on it."
        )

    n_timepoints = bold_data.shape[-1]
    out = np.zeros((n_timepoints, len(roi_names)), dtype=np.float64)

    for i, roi in enumerate(roi_names):
        mask = label_data == labels[roi]
        if not mask.any():
            raise RuntimeError(f"No voxels found for {roi}")
        out[:, i] = bold_data[mask].mean(axis=0)

    del bold_data  # free ~7GB before correlation step
    return out


def timeseries_cache_path(subject_dir, session_name):
    """Analysed_data/<subject>/timeseries_<session>.csv -- one cache file per
    session, named after the session, living at the subject level so it isn't
    tied to (or duplicated inside) any particular session's results folder."""
    return subject_dir / f"timeseries_{session_name}.csv"


def save_timeseries_csv(path, ts, names):
    """T x N timeseries as CSV: header row of parcel names, one row per
    timepoint -- opens directly in Excel/Numbers/pandas, unlike a .npy file
    (NumPy's binary array format, only readable via numpy.load())."""
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
    it opens as a readable labeled table in Excel/Numbers/pandas instead of
    needing numpy.load()."""
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([""] + list(names))
        for name, row in zip(names, matrix):
            writer.writerow([name] + [f"{v:.6f}" for v in row])


def load_or_extract_all_sources(ts_path, ptseries_dir, dtseries, cortex_atlas, vol_bold,
                                 tian_vol, tian_labels, cit168_prob, cit168_labels,
                                 hcpex_vol, hcpex_labels):
    """Load the full 427-parcel timeseries for this session from ts_path if a
    previous run already extracted it; otherwise run all four (slow) atlas
    extractions once and cache the result at ts_path. ptseries_dir is only
    where wb_command's intermediate ptseries.nii gets written before it's
    deleted -- unrelated to where the cache file itself lives. Delete ts_path
    manually to force re-extraction (e.g. after updating an atlas file)."""
    if ts_path.exists():
        print(f"  Found cached timeseries at {ts_path} -- skipping extraction.")
        return load_timeseries_csv(ts_path)

    ts_path.parent.mkdir(parents=True, exist_ok=True)
    ptseries_dir.mkdir(parents=True, exist_ok=True)

    cortex_ts, cortex_names = parcellate_cortex(dtseries, cortex_atlas, ptseries_dir)
    print(f"  {len(cortex_names)} cortex parcels x {cortex_ts.shape[0]} timepoints (CIFTI grayordinates)")

    subcortex_ts = extract_deterministic_roi_timeseries(vol_bold, tian_vol, tian_labels, TIAN_ROIS)
    print(f"  {len(TIAN_ROIS)} subcortical structures extracted (Tian-S1, volumetric mask mean)")

    midbrain_ts = extract_midbrain_timeseries(vol_bold, cit168_prob, cit168_labels)
    print(f"  {len(MIDBRAIN_ROIS)} midbrain nuclei extracted (CIT168, probability-weighted)")

    basal_forebrain_ts = extract_deterministic_roi_timeseries(vol_bold, hcpex_vol, hcpex_labels, BASAL_FOREBRAIN_ROIS)
    print(f"  {len(BASAL_FOREBRAIN_ROIS)} basal forebrain structures extracted (HCPex, volumetric mask mean)")

    all_ts = np.concatenate([cortex_ts, subcortex_ts, midbrain_ts, basal_forebrain_ts], axis=1)
    all_names = cortex_names + TIAN_ROIS + MIDBRAIN_ROIS + BASAL_FOREBRAIN_ROIS

    save_timeseries_csv(ts_path, all_ts, all_names)
    print(f"  Cached timeseries to {ts_path} for future runs.")

    return all_ts, all_names


def _ljust_fit(text, width):
    if len(text) > width:
        return text[: max(1, width - 1)] + "…"
    return text.ljust(width)


def _group_labeled_entries(all_names):
    """Group parcels by atlas letter, in original relative order, and give each
    a within-group label (A1, A2, ..., B1, ..., C1, ...). Returns
    {letter: [(label, original_index, name), ...]}."""
    groups = {letter: [] for letter in ATLAS_GROUPS}
    for i, name in enumerate(all_names):
        letter, _ = classify(name)
        groups[letter].append((i, name))
    return {
        letter: [(f"{letter}{k}", idx, name) for k, (idx, name) in enumerate(items, 1)]
        for letter, items in groups.items()
    }


# Schaefer-400 names (7Networks_<LH|RH>_<network>_[<subregion>_]<index>) are opaque
# on their own -- everything else (B/C/D short codes) is already readable, so only
# these get expanded for the terminal card. Source: CBIG Schaefer2018_LocalGlobal
# Parcellations/README.md (network/subregion abbreviation table).
_SCHAEFER_NETS = ["Vis", "SomMot", "DorsAttn", "SalVentAttn", "Limbic", "Cont", "Default"]
_SCHAEFER_PATTERN = re.compile(r"^7Networks_(LH|RH)_(" + "|".join(_SCHAEFER_NETS) + r")_(?:(.+)_)?(\d+)$")

_SCHAEFER_NETWORK_FULL = {
    "Vis": "Visual", "SomMot": "Somatomotor", "DorsAttn": "DorsAttn",
    "SalVentAttn": "SalVentAttn", "Limbic": "Limbic", "Cont": "Control", "Default": "Default",
}
_SCHAEFER_SUBREGION_FULL = {  # compact terminal-width forms, same source as the verbose artifact version
    "AntTemp": "Ant.Temporal", "Aud": "Auditory", "Cent": "Central",
    "Cinga": "Ant.Cingulate", "Cingm": "Mid-Cingulate", "Cingp": "Post.Cingulate",
    "ExStr": "Extrastriate", "ExStrInf": "Extrastriate-Inf", "ExStrSup": "Extrastriate-Sup",
    "FEF": "FrontalEyeFields", "FPole": "FrontalPole", "FrMed": "Frontal-Med",
    "FrOper": "Fr.Operculum", "IFG": "Inf.FrontalGyrus", "Ins": "Insula",
    "IPL": "Inf.ParietalLob", "IPS": "IntraparietalSulc", "OFC": "Orbitofrontal",
    "ParMed": "Parietal-Med", "ParOcc": "Parieto-Occ", "ParOper": "Parietal Operc",
    "pCun": "Precuneus", "pCunPCC": "Precuneus/PCC",
    "PFCd": "PFC-Dorsal", "PFCl": "PFC-Lateral", "PFCld": "PFC-LatDors", "PFClv": "PFC-LatVent",
    "PFCm": "PFC-Medial", "PFCmp": "PFC-MedPost", "PFCv": "PFC-Ventral",
    "PHC": "Parahippocamp", "PostC": "Postcentral", "PrC": "Precentral",
    "PrCd": "Precentral-D", "PrCv": "Precentral-V",
    "RSC": "Retrosplenial", "Rsp": "Retrosplenial", "S2": "S2",
    "SPL": "Sup.ParietalLob", "ST": "Sup.Temporal", "Striate": "Striate",
    "StriCal": "Striate-Calc", "Temp": "Temporal", "TempOcc": "Temporo-Occ",
    "TempPar": "Temporo-Par", "TempPole": "TemporalPole",
}
_SCHAEFER_GENERIC = {"Cing": "Cingulate", "Med": "Medial", "PFC": "PFC", "Par": "Parietal", "Post": "Posterior"}
_SCHAEFER_COMPOUNDS = {"FrOperIns": ["FrOper", "Ins"], "PFCdPFCm": ["PFCd", "PFCm"], "TempOccPar": ["TempOcc", "Par"]}


def _expand_schaefer_subregion(tag):
    if tag in _SCHAEFER_SUBREGION_FULL:
        return _SCHAEFER_SUBREGION_FULL[tag]
    if tag in _SCHAEFER_COMPOUNDS:
        return "/".join(_SCHAEFER_SUBREGION_FULL.get(p, _SCHAEFER_GENERIC.get(p, p)) for p in _SCHAEFER_COMPOUNDS[tag])
    return _SCHAEFER_GENERIC.get(tag, tag)


def display_name(name):
    """Human-readable label for the terminal card only -- extraction, region_names.txt,
    and matrix axis labels all keep using the raw atlas name regardless of this."""
    m = _SCHAEFER_PATTERN.match(name)
    if not m:
        return name  # B/C/D short codes are already readable
    hemi, net, sub, idx = m.group(1), m.group(2), m.group(3), m.group(4)
    hemi_letter = "L" if hemi == "LH" else "R"
    net_full = _SCHAEFER_NETWORK_FULL.get(net, net)
    if sub:
        return f"{hemi_letter} {net_full} {_expand_schaefer_subregion(sub)} #{idx}"
    return f"{hemi_letter} {net_full} #{idx}"


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
    'A1, B2, B4, C7', 'B', 'A1-10', or 'all'. Returns selected labels in the
    order typed (ranges/groups expand in ascending order), duplicates
    removed by first occurrence -- or None if unparseable."""
    if choice.strip().lower() == "all":
        return [lbl for letter in labeled for (lbl, _, _) in labeled[letter]]

    tokens = [t.strip() for t in choice.split(",") if t.strip()]
    if not tokens:
        return None

    letters = "".join(labeled)  # e.g. "ABCD" -- derived from ATLAS_GROUPS, never hardcoded
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
    the user pick specific parcels across groups by label, e.g. 'A1, B2, C7'.
    Returns the chosen names, in the order the user selected them."""
    labeled = _group_labeled_entries(all_names)
    label_to_entry = {lbl: (idx, name) for items in labeled.values() for (lbl, idx, name) in items}
    term_width = shutil.get_terminal_size(fallback=(100, 24)).columns

    print(f"\nAtlas groups available ({len(all_names)} parcels total):")
    for letter in ATLAS_GROUPS:
        _print_atlas_card(letter, labeled[letter], term_width)

    print(
        "Select parcels by label, comma-separated (e.g. 'A1, B2, B4, C7'). "
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
    """Average left/right (and SNc+SNr) pairs per TRIANGLE_COMBINE_MAP into
    one composite timeseries per region (NbM, Hippocampus, SN), and pass VTA
    through unchanged since it has no L/R pair. Returns (triangle_ts,
    triangle_names) with the same shape convention as (all_ts, all_names),
    ready to feed into run_and_save_analysis() with TRIANGLE_VTA/TRIANGLE_SN
    as the selection."""
    name_to_col = {n: i for i, n in enumerate(all_names)}
    columns, names = [], []
    for combined_name, parts in TRIANGLE_COMBINE_MAP.items():
        cols = [name_to_col[p] for p in parts]
        columns.append(all_ts[:, cols].mean(axis=1))
        names.append(combined_name)
    columns.append(all_ts[:, name_to_col["VTA"]])
    names.append("VTA")
    return np.stack(columns, axis=1), names


def run_and_save_analysis(all_ts, all_names, selected_names, out_dir, suffix,
                           subject_name, session_name, use_graph_plot):
    """Compute the FC matrix for selected_names and save every output into
    out_dir with suffix appended to the filename, so the standard analysis
    and both fixed graph analyses can share one folder without clobbering
    each other's files."""
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

    dtseries = session / DTSERIES_REL
    vol_bold = session / VOL_BOLD_REL
    if not dtseries.exists() or not vol_bold.exists():
        sys.exit(
            f"Missing expected resting-state output under {session}\n"
            f"  need: {DTSERIES_REL}\n"
            f"  and:  {VOL_BOLD_REL}"
        )

    atlases_dir = find_atlases_dir(raw_root)
    cortex_atlas = atlases_dir / "schaefer400_tianS1.dlabel.nii"
    tian_vol = atlases_dir / "Tian_Subcortex_S1_3T.nii"
    tian_labels = atlases_dir / "Tian_Subcortex_S1_labels.txt"
    cit168_prob = atlases_dir / "CIT168_prob_func2mm.nii.gz"
    cit168_labels = atlases_dir / "CIT168_labels.txt"
    hcpex_vol = atlases_dir / "HCPex_2mm.nii"
    hcpex_labels = atlases_dir / "HCPex_basal_forebrain_labels.txt"

    subject_dir = analysed_root / subject.name
    out_dir = subject_dir / session.name / "func"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n== Timeseries for {subject.name}/{session.name} ==")
    ts_path = timeseries_cache_path(subject_dir, session.name)
    all_ts, all_names = load_or_extract_all_sources(
        ts_path, out_dir, dtseries, cortex_atlas, vol_bold,
        tian_vol, tian_labels, cit168_prob, cit168_labels, hcpex_vol, hcpex_labels,
    )
    print(f"  {len(all_names)} parcels available in total")

    print("\n== Standard FC matrix -- pick parcels ==")
    standard_selection = choose_parcels(all_names)

    triangle_ts, triangle_names = build_triangle_ts(all_ts, all_names)

    print(f"\n== Computing all five analyses for {subject.name}/{session.name} ==")
    run_and_save_analysis(all_ts, all_names, standard_selection, out_dir, "standard",
                           subject.name, session.name, use_graph_plot=False)
    run_and_save_analysis(all_ts, all_names, GRAPH_VTA, out_dir, "graph_vta",
                           subject.name, session.name, use_graph_plot=True)
    run_and_save_analysis(all_ts, all_names, GRAPH_SN, out_dir, "graph_sn",
                           subject.name, session.name, use_graph_plot=True)
    run_and_save_analysis(triangle_ts, triangle_names, TRIANGLE_VTA, out_dir, "triangle_vta",
                           subject.name, session.name, use_graph_plot=True)
    run_and_save_analysis(triangle_ts, triangle_names, TRIANGLE_SN, out_dir, "triangle_sn",
                           subject.name, session.name, use_graph_plot=True)

    record_analysis(analysed_root, subject.name, session.name)

    print(f"\nAll analyses saved to {out_dir}")


if __name__ == "__main__":
    main()
