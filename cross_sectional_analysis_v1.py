#!/usr/bin/env python3
"""
Interactive cross-sectional anatomical analysis pipeline.

Prompts for:
  - path to the raw data root (contains sub-*/ses-*/anat/... HCP-processed
    structural sessions)
  - which subject to run, from those found under that root
  - which session, if the subject has more than one

For the chosen session, extracts the three structural measures discussed for
cross-sectional (between-subject) comparison -- see the "Understanding
Anatomical Data" appendix in rsfMRI_processing_summary.pptx, Slides 26-27:

  - Cortical thickness   -- corrThickness_MSMAll (MNINonLinear/fsaverage_LR32k/*.dscalar.nii),
                             parcellated to Schaefer-400 cortex
  - Myelin content        -- MyelinMap_BC_MSMAll (same folder/format),
                             parcellated to Schaefer-400 cortex
  - Regional/subcortical volumes -- wmparc.nii.gz (MNINonLinear/), every
                             FreeSurfer label present, voxel count x voxel
                             volume (read from the file's own affine -- this
                             dataset's wmparc is 0.8mm iso = 0.512 mm^3/voxel,
                             NOT the 2mm functional grid the FC pipeline uses)

Cortical thickness/myelin need an atlas (raw BOLD-style vertex data has no
built-in regions); volumes don't, since wmparc/aparc+aseg are themselves
already FreeSurfer's atlas-labeled segmentation output (see Slide 27).

Cortical parcellation reuses schaefer400_tianS1.dlabel.nii (the same CIFTI
atlas v1/v2 use for Group A) -- no new atlas file needed. Matching is done
per-vertex in pure Python/nibabel (structure name + vertex index, matched
between the atlas's cortex rows and the dscalar's rows) rather than shelling
out to wb_command -cifti-parcellate, because that atlas file also carries 16
Tian-S1 subcortical (volume) parcels that a cortex-only dscalar has no
grayordinates for -- wb_command errors on the mismatch, per-vertex matching
in Python doesn't need to care. Verified to match wb_command's own
-cifti-parcellate output to ~1e-8 on a spot-checked parcel.

Volume labels come from FreeSurferColorLUT.txt (checked at $FREESURFER_HOME
first, then /Applications/freesurfer/8.1.0/ as a fallback) -- same
id-to-name lookup-table pattern as v3's HCPex_LookUpTable.txt.

Standard command:
  python3 cross_sectional_analysis.py

Output:
  Analysed_data/<subject>/<session>/anat/
    cortical_thickness_schaefer400.csv   (region, thickness_mm)
    myelin_schaefer400.csv               (region, myelin_ratio)
    subcortical_volumes_wmparc.csv       (label_id, region, voxel_count, volume_mm3)
    subcortical_volumes_key_structures.png
    cortical_thickness_myelin_summary.png
    manifest.txt                         (source files + atlas/LUT used, for provenance)
  Own run-count log: Analysed_data/analysis_log_anat.json (separate from the
  FC pipeline's analysis_log*.json files).

Requires:
  schaefer400_tianS1.dlabel.nii under <raw root>/atlases/ (or the
  /Volumes/njainmpi/... fallback) -- already present from v1/v2.
  FreeSurferColorLUT.txt via $FREESURFER_HOME or /Applications/freesurfer/8.1.0/.

Note on eTIV / head-size normalization:
  No aseg.stats (or equivalent) was found anywhere in this dataset's
  structural output when checked -- FreeSurfer's own eTIV isn't available.
  subcortical_volumes_wmparc.csv reports raw mm^3 only; normalize externally
  if needed for group comparison.
"""
import csv
import glob
import json
import os
import re
import shutil
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import nibabel as nib
import readline

ANALYSIS_LOG_NAME = "analysis_log_anat.json"  # separate from the FC pipeline's logs
COUNT_COLOR_SCALE = 10
COLOR_ENABLED = sys.stdout.isatty() and os.environ.get("TERM", "") != "dumb"

ANAT_MNI_REL = "anat/MNINonLinear"
FSAVG32K_REL = f"{ANAT_MNI_REL}/fsaverage_LR32k"
WMPARC_REL = f"{ANAT_MNI_REL}/wmparc.nii.gz"

FALLBACK_ATLASES = Path("/Volumes/njainmpi/Project3_Aging/Raw_Data/atlases")
FALLBACK_FS_LUT = Path("/Applications/freesurfer/8.1.0/FreeSurferColorLUT.txt")

CORTEX_STRUCTURES = {"CIFTI_STRUCTURE_CORTEX_LEFT", "CIFTI_STRUCTURE_CORTEX_RIGHT"}

# Left/Right FreeSurfer label IDs for the 7 structures highlighted in the
# "Key Subcortical Label IDs" table (Slide 27) -- used only to keep the
# summary plot readable; the CSV itself keeps every label found in wmparc.
KEY_SUBCORTICAL_IDS = {
    "Thalamus-Proper": (10, 49),
    "Caudate": (11, 50),
    "Putamen": (12, 51),
    "Pallidum": (13, 52),
    "Hippocampus": (17, 53),
    "Amygdala": (18, 54),
    "Accumbens-area": (26, 58),
}


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
    if (candidate / "schaefer400_tianS1.dlabel.nii").exists():
        return candidate
    if (FALLBACK_ATLASES / "schaefer400_tianS1.dlabel.nii").exists():
        print(f"  (no atlases/ folder under {raw_root} — using {FALLBACK_ATLASES})")
        return FALLBACK_ATLASES
    sys.exit(
        "Could not locate schaefer400_tianS1.dlabel.nii under either the data "
        "root or the fallback location -- it's required to parcellate cortical "
        "thickness/myelin."
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


def load_freesurfer_lut(lut_path):
    """id -> name, from FreeSurferColorLUT.txt: '<id> <name> <r> <g> <b> <a>'
    per line, '#'-comments and blank lines skipped. Same shape as
    HCPex_LookUpTable.txt's format in run_fc_pipeline_v3.py."""
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


def build_cortex_vertex_lut(atlas_path):
    """(structure, vertex_index) -> label_id for CORTEX_LEFT/RIGHT rows only
    (the atlas's 16 Tian-S1 subcortical parcels are volume rows, skipped),
    plus label_id -> name from the atlas's own label table."""
    img = nib.load(str(atlas_path))
    data = img.get_fdata()[0]
    bm_axis = img.header.get_axis(1)
    label_axis = img.header.get_axis(0)
    label_table = label_axis.label[0]  # {label_id: (name, rgba)}

    vertex_lut = {}
    for i, (structure, vtx) in enumerate(zip(bm_axis.name, bm_axis.vertex)):
        if structure in CORTEX_STRUCTURES:
            vertex_lut[(structure, int(vtx))] = int(data[i])

    label_names = {lid: name for lid, (name, _rgba) in label_table.items()}
    return vertex_lut, label_names


def parcellate_cortical_dscalar(dscalar_path, vertex_lut, label_names):
    """Per-vertex mean of dscalar_path's single map, grouped by the cortex
    label each (structure, vertex_index) maps to. Returns [(name, mean), ...]
    sorted by label_id ascending (Schaefer's own LH-then-RH network order)."""
    img = nib.load(str(dscalar_path))
    data = img.get_fdata()[0]
    bm_axis = img.header.get_axis(1)

    sums, counts = {}, {}
    for i, (structure, vtx) in enumerate(zip(bm_axis.name, bm_axis.vertex)):
        label_id = vertex_lut.get((structure, int(vtx)))
        if not label_id:  # None or 0 (unlabeled / medial wall)
            continue
        sums[label_id] = sums.get(label_id, 0.0) + data[i]
        counts[label_id] = counts.get(label_id, 0) + 1

    rows = [(label_names.get(lid, f"label_{lid}"), sums[lid] / counts[lid])
            for lid in sorted(sums)]
    return rows


def extract_subcortical_volumes(wmparc_path, fs_lut):
    """Every non-zero label present in wmparc: voxel count x voxel volume
    (read from the file's own affine, not assumed). Returns
    [(label_id, name, voxel_count, volume_mm3), ...] sorted by label_id."""
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
                    wmparc_path, atlas_path, lut_path, voxel_vol_mm3):
    text = (
        f"Cross-sectional anatomical analysis\n"
        f"Subject: {subject_name}\nSession: {session_name}\n\n"
        f"Cortical thickness source: {thickness_path}\n"
        f"Myelin source:             {myelin_path}\n"
        f"Volume source (wmparc):    {wmparc_path}\n"
        f"  voxel volume used:       {voxel_vol_mm3:.6f} mm^3 (read from file affine)\n"
        f"Cortical atlas:            {atlas_path}\n"
        f"Volume label lookup:       {lut_path}\n\n"
        f"No eTIV/aseg.stats found in this dataset's structural output -- "
        f"subcortical_volumes_wmparc.csv reports raw mm^3, not head-size-normalized.\n"
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
    thickness_matches = sorted(fsavg_dir.glob("*.corrThickness_MSMAll.32k_fs_LR.dscalar.nii"))
    myelin_matches = sorted(fsavg_dir.glob("*.MyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii"))

    if not thickness_matches or not myelin_matches or not wmparc_path.exists():
        sys.exit(
            f"Missing expected anatomical outputs under {session / 'anat'}\n"
            f"  need: {FSAVG32K_REL}/*.corrThickness_MSMAll.32k_fs_LR.dscalar.nii\n"
            f"        {FSAVG32K_REL}/*.MyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii\n"
            f"        {WMPARC_REL}"
        )
    thickness_path = thickness_matches[0]
    myelin_path = myelin_matches[0]

    atlases_dir = find_atlases_dir(raw_root)
    atlas_path = atlases_dir / "schaefer400_tianS1.dlabel.nii"
    lut_path = find_freesurfer_lut()
    fs_lut = load_freesurfer_lut(lut_path)

    out_dir = analysed_root / subject.name / session.name / "anat"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n== Cross-sectional anatomical analysis for {subject.name}/{session.name} ==")

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

    plot_key_volumes(volume_rows, out_dir, subject.name, session.name)
    plot_thickness_myelin_summary(thickness_rows, myelin_rows, out_dir, subject.name, session.name)
    write_manifest(out_dir, subject.name, session.name, thickness_path, myelin_path,
                    wmparc_path, atlas_path, lut_path, voxel_vol_mm3)

    record_analysis(analysed_root, subject.name, session.name)

    print(f"\nAll analyses saved to {out_dir}")
    print("  cortical_thickness_schaefer400.csv, myelin_schaefer400.csv, "
          "subcortical_volumes_wmparc.csv,")
    print("  subcortical_volumes_key_structures.png, "
          "cortical_thickness_myelin_summary.png, manifest.txt")


if __name__ == "__main__":
    main()
