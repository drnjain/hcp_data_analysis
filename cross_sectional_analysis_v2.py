#!/usr/bin/env python3
"""
Interactive cross-sectional anatomical analysis pipeline (v2 -- adds
midbrain / basal-forebrain volumes).

Same as cross_sectional_analysis.py (cortical thickness + myelin,
Schaefer-400 parcellated; wmparc-based regional volumes for every
FreeSurfer-segmented structure), PLUS volumes for three structures FreeSurfer
does NOT segment -- Ventral Tegmental Area (VTA), Substantia Nigra (SNc/SNr),
and Nucleus Basalis (NbM) -- pulled from the HCPex atlas instead, the same
one run_fc_pipeline_v3.py uses for these regions in the FC pipeline.

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

Output:
  Analysed_data/<subject>/<session>/anat/
    cortical_thickness_schaefer400.csv   (region, thickness_mm)
    myelin_schaefer400.csv               (region, myelin_ratio)
    subcortical_volumes_wmparc.csv       (label_id, region, voxel_count, volume_mm3)
    midbrain_basalforebrain_volumes_hcpex.csv
      (region, label_id, standard_voxel_count, standard_volume_mm3,
       native_voxel_count, native_volume_mm3)
    subcortical_volumes_key_structures.png
    midbrain_basalforebrain_volumes_hcpex.png
    cortical_thickness_myelin_summary.png
    manifest.txt
  Own run-count log: Analysed_data/analysis_log_anat_v2.json (separate from
  v1's analysis_log_anat.json and the FC pipeline's logs).

Requires:
  Everything cross_sectional_analysis.py requires, plus HCPex_2mm.nii +
  HCPex_LookUpTable.txt under <raw root>/atlases/ (already present from
  run_fc_pipeline_v3.py), and FSL's applywarp on PATH (checked via
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
# not hardcoded IDs, since v3 already established the LUT is the source of truth.
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
    required = ["schaefer400_tianS1.dlabel.nii", "HCPex_2mm.nii", "HCPex_LookUpTable.txt"]
    if all((candidate / f).exists() for f in required):
        return candidate
    if all((FALLBACK_ATLASES / f).exists() for f in required):
        print(f"  (no atlases/ folder under {raw_root} — using {FALLBACK_ATLASES})")
        return FALLBACK_ATLASES
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
    """id -> name, HCPex_LookUpTable.txt format (same as run_fc_pipeline_v3.py's
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


def parcellate_cortical_dscalar(dscalar_path, vertex_lut, label_names):
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


def save_named_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for name, value in rows:
            writer.writerow([name, f"{value:.6f}"])


def save_volume_csv(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["label_id", "region", "voxel_count", "volume_mm3"])
        for lid, name, cnt, vol in rows:
            writer.writerow([lid, name, cnt, f"{vol:.3f}"])


def save_midbrain_bf_csv(path, name_to_id, standard_rows, native_rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "region", "label_id",
            "standard_voxel_count", "standard_volume_mm3",
            "native_voxel_count", "native_volume_mm3",
        ])
        for name in sorted(name_to_id, key=lambda n: name_to_id[n]):
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


def write_manifest(out_dir, subject_name, session_name, thickness_path, myelin_path,
                    wmparc_path, atlas_path, lut_path, voxel_vol_mm3,
                    hcpex_path, warp_field_path, ref_native_path, native_voxel_vol_mm3):
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
    (out_dir / "manifest.txt").write_text(text)


def main():
    raw_root = find_data_root()
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

    atlases_dir = find_atlases_dir(raw_root)
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
    thickness_rows = parcellate_cortical_dscalar(thickness_path, vertex_lut, label_names)
    save_named_csv(out_dir / "cortical_thickness_schaefer400.csv", ["region", "thickness_mm"], thickness_rows)

    print("  Parcellating myelin map / T1w-T2w ratio (Schaefer-400)...")
    myelin_rows = parcellate_cortical_dscalar(myelin_path, vertex_lut, label_names)
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
    write_manifest(out_dir, subject.name, session.name, thickness_path, myelin_path,
                    wmparc_path, atlas_path, lut_path, voxel_vol_mm3,
                    hcpex_path, warp_field_path, ref_native_path, native_voxel_vol)

    record_analysis(analysed_root, subject.name, session.name)

    print(f"\nAll analyses saved to {out_dir}")
    print("  cortical_thickness_schaefer400.csv, myelin_schaefer400.csv,")
    print("  subcortical_volumes_wmparc.csv, midbrain_basalforebrain_volumes_hcpex.csv,")
    print("  subcortical_volumes_key_structures.png, midbrain_basalforebrain_volumes_hcpex.png,")
    print("  cortical_thickness_myelin_summary.png, manifest.txt")


if __name__ == "__main__":
    main()
