# HCP Aging AABC Release 2 — Data Analysis Scripts

This is the single reference for this folder: how the data is organized, how
each script works, the exact command to run it, every output path, and every
requirement.

> **Note (2026-07-24):** README.txt's per-script command/output/requirements
> index has been fully merged into this file, and README.txt has been
> retired (its original content is preserved at `README.txt.bak` in this
> folder in case anything needs cross-checking). This file is now the sole
> authoritative reference for this folder.

> **Note (2026-07-23 rename):** the original `run_fc_pipeline.py` (CIFTI-cortex,
> 427-parcel, `.npy` output) and its batch counterpart `run_fc_pipeline_batch.py`
> have been removed. `run_fc_pipeline_v2.py` was renamed to `run_fc_pipeline_v1.py`,
> and `run_fc_pipeline_v3.py` was renamed to `run_fc_pipeline_v2.py` — filenames
> only, no code changes, so each script's internal run-count-log and
> cached-timeseries filenames still use their pre-rename names (called out where
> relevant below). `cross_sectional_analysis.py` was likewise renamed to
> `cross_sectional_analysis_v1.py`. `run_fc_pipeline_batch_v2.py` was rebuilt
> from scratch as a batch driver for the *current* `run_fc_pipeline_v2.py`
> (HCPex-only) — it is a different script than the name used to refer to. A new
> `cross_sectional_analysis_batch_v2.py` was also added.

Also present in this folder but not covered below: the one-off pptx-editing
scripts that built up `rsfMRI_processing_summary_with_anat_appendix.pptx`
slide by slide have been removed — their effects are already permanently
baked into that presentation, so nothing currently visible depends on them
(see `SESSION_SUMMARY.md` for what each one did). The two *reusable* pptx
builders (Section 6 below) were kept, since those regenerate their decks
from scratch and are meant to be re-run whenever a source script changes.

---

## Quick command reference

| Script | Command | Purpose |
|---|---|---|
| `organize_hcp_data.py` | `python3 organize_hcp_data.py --input DIR --output DIR` | Sort raw HCP zip downloads into `sub-*/ses-*/{anat,func,concat}/` |
| `plot_roi_amplitudes.py` | `python3 plot_roi_amplitudes.py --group CSV --data CSV` | Interactive box plots of ROI BOLD amplitudes by visit |
| `run_fc_pipeline_v1.py` | `python3 run_fc_pipeline_v1.py` | Interactive FC pipeline, 427 parcels (Schaefer + Tian-S1 + CIT168 + HCPex) |
| `run_fc_pipeline_v2.py` | `python3 run_fc_pipeline_v2.py` | Interactive FC pipeline, HCPex-only, 426 parcels |
| `run_fc_pipeline_batch_v2.py` | `python3 run_fc_pipeline_batch_v2.py /path/to/Raw_Data` | Non-interactive batch driver for `run_fc_pipeline_v2.py` (73 sessions currently) |
| `cross_sectional_analysis_v1.py` | `python3 cross_sectional_analysis_v1.py` | Cortical thickness/myelin (Schaefer-400) + wmparc regional volumes |
| `cross_sectional_analysis_v2.py` | `python3 cross_sectional_analysis_v2.py` | v1 + VTA/SN/Nucleus Basalis volumes via HCPex (standard + native space) |
| `cross_sectional_analysis_batch_v2.py` | `python3 cross_sectional_analysis_batch_v2.py /path/to/Raw_Data` | Non-interactive batch driver for `cross_sectional_analysis_v2.py` |
| `build_scripts_overview_presentation.py` | `python3 build_scripts_overview_presentation.py` | Rebuilds the standalone "Data Analysis Scripts Overview" pptx |
| `build_combined_presentation.py` | `python3 build_combined_presentation.py` | Merges the rsfMRI summary deck + Scripts Overview deck into one |

Full detail — including every flag, output file, and requirement — is in the
matching section below.

---

## 1. Data organization & hierarchy

### Raw data

```
Raw_Data/
├─ sub-<subject>/                    e.g. sub-HCA6002236
│   └─ ses-<visit>/                  e.g. ses-V3
│       ├─ anat/                     structural (T1w/T2w) preprocessing output
│       │   ├─ T1w/                  native (acpc) space anatomicals + FreeSurfer segmentation
│       │   ├─ MNINonLinear/         same anatomicals + surfaces, registered to MNI152
│       │   ├─ unprocessed/          raw acquisition metadata only
│       │   └─ ProcessingInfo/       QuNex + HCP pipeline job logs/scripts
│       ├─ func/                     resting-state fMRI (per-run + concatenated)
│       └─ concat/                   concatenated multi-run fMRI outputs
└─ atlases/                          shared atlas files (see below)
```

This hierarchy is built by `organize_hcp_data.py` (Section 2) from downloaded
HCP Aging "Recommended" zip packages, and is what every other script in this
folder expects as its raw-data root.

### Analysed data

```
Analysed_data/
├─ analysis_log*.json                one run-count log per pipeline (never shared across pipelines)
└─ <subject>/
    ├─ timeseries_<ses>.csv          run_fc_pipeline_v1.py's cached full-parcel timeseries
    ├─ timeseries_hcpex_<ses>.csv    run_fc_pipeline_v2.py's cached full-parcel timeseries
    └─ <session>/
        ├─ anat/                     cross_sectional_analysis_v1.py / _v2.py output
        └─ func/                     run_fc_pipeline_v1.py / _v2.py output
                                        (both share this one subfolder;
                                        filenames suffixed by analysis mode
                                        so nothing collides -- v1's suffixes
                                        are plain, e.g. _standard; v2's add
                                        _hcpex, e.g. _standard_hcpex)
```

`Analysed_data/` always sits *next to* (not inside) the raw data root, and its
`sub-*/ses-*` layout mirrors `Raw_Data/` exactly. The raw data tree itself is
never written to by any script.

### Atlases folder

Shared across every pipeline version, under `Raw_Data/atlases/` (with a
`/Volumes/njainmpi/Project3_Aging/Raw_Data/atlases/` fallback baked into every
script that needs it):

| File | Used by | Covers |
|---|---|---|
| `schaefer400_tianS1.dlabel.nii` | run_fc_pipeline_v1.py, cross-sectional (cortex) | 400 Schaefer cortex + 16 Tian-S1 subcortex, CIFTI |
| `CIT168_prob_func2mm.nii.gz` + `CIT168_labels.txt` | run_fc_pipeline_v1.py | 5 midbrain nuclei (SNc, SNr, VTA, PBP, RN), probabilistic |
| `HCPex_2mm.nii` + `HCPex_basal_forebrain_labels.txt` | run_fc_pipeline_v1.py | 6 basal-forebrain structures, short atlas codes |
| `HCPex_2mm.nii` + `HCPex_LookUpTable.txt` | run_fc_pipeline_v2.py, cross-sectional v2 | full 426-parcel atlas (360 cortex + 66 subcortical/midbrain/basal-forebrain), full descriptive names |
| `Tian_Subcortex_S1_3T.nii` + labels | run_fc_pipeline_v1.py | Official volumetric Tian-S1 release |

Note `HCPex_2mm.nii` itself is shared by both rows above — only the label
file differs (short atlas codes for v1's Group D vs. full descriptive names
for v2's all-parcel use).

---

## 2. `organize_hcp_data.py` — building the raw-data hierarchy

**What it does:** takes a folder of downloaded HCP Aging "Recommended" zip
packages and sorts them into the `sub-<subject>/ses-<visit>/{anat,func,concat}/`
hierarchy every other script depends on.

**How it works, step by step:**

1. **Parse the filename.** Each zip is named like
   `HCA6002236_V3_MR_StructuralRecommended....zip`. A regex
   (`ZIP_NAME_RE`) pulls out the subject ID, visit, and modality
   (`Structural` / `RestFmri` / `ConcatFmri`) directly from the filename — no
   need to open the zip to know where it goes.
2. **Route to a modality folder.** `Structural → anat`, `RestFmri → func`,
   `ConcatFmri → concat`, via the `MODALITY_FOLDER` lookup. Anything else is
   logged as `unknown_modality` and skipped.
3. **Skip work already done.** `already_extracted()` checks whether the
   target folder already has content; if so the zip is skipped rather than
   re-extracted.
4. **Extract to a scratch folder, then merge.** The zip is extracted to a
   temporary staging directory *inside* the output root (so the move in the
   next step is same-filesystem and cheap). `resolve_content_root()` detects
   the single wrapper folder every HCP zip extracts into (e.g.
   `HCA6002236_V3_MR/`) and skips past it. `merge_tree()` then recursively
   moves every file into the target `anat/func/concat` folder — files that
   already exist there are left alone (never overwritten), and any
   same-name-but-different-size collision is recorded in `conflicts.log`
   rather than silently resolved either way.
5. **Archive the zip.** Once merged, the zip (and its `.md5` sidecar, if
   present) is moved to `output_root/archive/` — processed zips don't linger
   next to unprocessed ones.
6. **Report.** Every zip processed (or skipped, or errored) gets one row
   appended to `output_root/manifest.csv` (subject, visit, modality, target
   folder, status, timestamp) — `manifest.csv` is append-only, so re-running
   the script against a folder with old and new zips mixed together produces
   one continuous audit trail rather than overwriting history.

**Flowchart:** `organize_hcp_data_flowchart.png` / `.svg` (source:
`organize_hcp_data_flowchart.dot`) — walks find → identify → route →
organize-or-skip → archive → report.

**Standard command:**
```bash
python3 organize_hcp_data.py --input /path/to/zip_folder --output /path/to/organized_root
```

**Useful flags:**
```bash
python3 organize_hcp_data.py --input /path/to/zip_folder --output /path/to/organized_root --dry-run
python3 organize_hcp_data.py --input /path/to/zip_folder --output /path/to/organized_root --subject HCA6072156
```
- `--dry-run` — report what would happen, without extracting/moving anything
- `--subject HCA6072156` — only process zips for one subject ID

**Output:**
- `output_root/sub-<subject>/ses-<visit>/{anat,func,concat}/` — the sorted hierarchy
- `output_root/manifest.csv` — append-only audit log of every zip processed
- `output_root/archive/` — processed zips (and `.md5` sidecars) moved here after merging
- `output_root/conflicts.log` — same-name/different-size collisions that were left unresolved

---

## 3. `plot_roi_amplitudes.py` — ROI amplitude box plots

Stands apart from the raw-data/`Analysed_data` pipeline chain above: it
doesn't read from `Raw_Data/` or write to `Analysed_data/` — its two inputs
are a group-membership CSV and a pre-computed amplitudes CSV.

**What it does:** draws interactive box plots of ROI BOLD amplitudes by
visit (V1–V4), for the subject/visit subset listed in a group CSV file.
Prompts you to pick which ROIs to plot after showing the list found in the
data file. Saves each figure as both `.svg` and `.png`.

**Standard command:**
```bash
python3 plot_roi_amplitudes.py --group /path/to/group5_age_gt50_at_v1_and_moca_gt21_persession.csv \
                                --data /path/to/rfMRI_REST_FullAmplitudes.csv
```

**Useful flags:**
- `-o` / `--output DIR` — directory to save figures into (default: current directory)
- `-h` / `--help` — full usage, including CSV format requirements

**Output:** one `.svg` and one `.png` per ROI plotted, saved to the output directory.

---

## 4. Cross-sectional anatomical analysis

Two script versions, both interactive (subject/session tile picker, same UI
as the FC pipeline scripts — see Section 5), both writing to
`Analysed_data/<subject>/<session>/anat/`. Plus one batch driver.

### v1 — `cross_sectional_analysis_v1.py`

(Renamed 2026-07-23 from `cross_sectional_analysis.py` — code untouched.)

Extracts the three structural measures suitable for between-subject
comparison:

- **Cortical thickness** and **myelin content** (T1w/T2w ratio) — both start
  as per-vertex CIFTI `dscalar.nii` maps (`corrThickness_MSMAll`,
  `MyelinMap_BC_MSMAll`) with no built-in regions, so they're parcellated to
  Schaefer-400 by matching `(structure, vertex_index)` between the atlas and
  the dscalar in pure Python/nibabel — no `wb_command` dependency, verified
  to match `wb_command -cifti-parcellate` to ~1e-8.
- **Subcortical/regional volumes** — every FreeSurfer label present in
  `wmparc.nii.gz`, volume = voxel count × voxel volume **read from the
  file's own affine** (this dataset's `wmparc` is 0.8mm isotropic =
  0.512 mm³/voxel, not the 2mm functional grid other atlases use — this
  needed no atlas at all, since `wmparc`/`aparc+aseg` are already
  FreeSurfer's own atlas-labeled segmentation output).

Labels come from `FreeSurferColorLUT.txt` (`$FREESURFER_HOME`, or
`/Applications/freesurfer/8.1.0/` as fallback).

**Standard command:**
```bash
python3 cross_sectional_analysis_v1.py
```

**Output** — `Analysed_data/<subject>/<session>/anat/`:
- `cortical_thickness_schaefer400.csv`, `myelin_schaefer400.csv`
- `subcortical_volumes_wmparc.csv`
- `subcortical_volumes_key_structures.png`, `cortical_thickness_myelin_summary.png`
- `manifest.txt` — source files + atlas/LUT used, for provenance
- Own run-count log: `Analysed_data/analysis_log_anat.json`

**Requires:** `schaefer400_tianS1.dlabel.nii` under `<raw root>/atlases/`
(already present from `run_fc_pipeline_v1.py`); `FreeSurferColorLUT.txt` via
`$FREESURFER_HOME` or `/Applications/freesurfer/8.1.0/`.

**Note:** no `aseg.stats` (or equivalent eTIV source) was found anywhere in
this dataset's structural output — `subcortical_volumes_wmparc.csv` reports
raw mm³, not head-size-normalized.

### v2 — `cross_sectional_analysis_v2.py`

Everything v1 does, plus volumes for three structures FreeSurfer's `wmparc`
doesn't segment at all: **VTA, Substantia Nigra (SNc/SNr), and Nucleus
Basalis** — pulled from the HCPex atlas instead (the same one
`run_fc_pipeline_v2.py` uses for these regions). `wmparc`/FreeSurfer stays
authoritative for anything it already covers — HCPex's own Hippocampus label
was spot-checked and reads ~half of wmparc's (cruder atlas boundary).

The key methodological wrinkle v2 had to solve: HCPex is a template atlas
fixed in standard MNI152 space. Counting voxels there directly gives every
subject the *identical* volume (same voxels, zero cross-subject variance —
useless for a cross-sectional comparison). So v2 computes **both**, saved
side by side in `midbrain_basalforebrain_volumes_hcpex.csv`:

- **standard-space volume** — raw voxel count in `HCPex_2mm.nii` (2mm
  voxels). Identical for every subject by construction; reference number
  only.
- **native-space volume** — HCPex warped into the subject's own T1w/acpc
  space via FSL `applywarp` (`--warp=MNINonLinear/xfms/standard2acpc_dc.nii.gz
  --ref=T1w/T1w_acpc_dc.nii.gz --interp=nn`), then counted at native voxel
  size. This is the value that actually varies by subject — the correct one
  for cross-sectional comparison.

**Standard command:**
```bash
python3 cross_sectional_analysis_v2.py
```

**Output** — `Analysed_data/<subject>/<session>/anat/`:
- Everything v1 outputs, plus:
- `midbrain_basalforebrain_volumes_hcpex.csv` — columns: `region, label_id,
  standard_voxel_count, standard_volume_mm3, native_voxel_count,
  native_volume_mm3`
- `midbrain_basalforebrain_volumes_hcpex.png`
- Own run-count log: `Analysed_data/analysis_log_anat_v2.json` (separate from v1's)

**Requires:** everything v1 requires, plus `HCPex_2mm.nii` +
`HCPex_LookUpTable.txt` under `<raw root>/atlases/` (already present from
`run_fc_pipeline_v2.py`), and FSL's `applywarp` on `PATH`.

**Note:** same eTIV caveat as v1 — all volumes reported are raw mm³.

### Batch variant — `cross_sectional_analysis_batch_v2.py`

(New 2026-07-23.) Non-interactive driver for `cross_sectional_analysis_v2.py`:
imports it as a module and reuses its extraction/plotting/logging functions
unchanged, replacing only the interactive subject/session picker with a loop
over every `sub-*/ses-*` session found under a raw data root. Work that's
identical for every subject — FreeSurfer/HCPex LUTs, the cortex vertex LUT,
and HCPex's standard-space volumes (fixed by construction) — is computed
once up front instead of per session. Sessions that already have
`anat/manifest.txt` are skipped unless `--force`; each session runs in its
own `try`/`except` so one failure doesn't abort the batch.

**Standard command:**
```bash
python3 cross_sectional_analysis_batch_v2.py /path/to/Raw_Data
```

**Useful flags:**
```bash
python3 cross_sectional_analysis_batch_v2.py /path/to/Raw_Data --force --subjects sub-HCA6002236,sub-HCA6072156
```
- (raw_data_root omitted) — falls back to the same interactive tab-completing
  path prompt as `cross_sectional_analysis_v2.py`
- `--force` — re-run sessions that already have `anat/manifest.txt`
- `--subjects sub-A,sub-B` — restrict to specific subject folder names

**Output** (per session, same as `cross_sectional_analysis_v2.py`) —
`Analysed_data/<subject>/<session>/anat/`:
`cortical_thickness_schaefer400.csv`, `myelin_schaefer400.csv`,
`subcortical_volumes_wmparc.csv`, `midbrain_basalforebrain_volumes_hcpex.csv`,
`subcortical_volumes_key_structures.png`,
`midbrain_basalforebrain_volumes_hcpex.png`,
`cortical_thickness_myelin_summary.png`, `manifest.txt`. Shares
`analysis_log_anat_v2.json` with `cross_sectional_analysis_v2.py`.

**Requires:** same atlas files + FSL `applywarp` as `cross_sectional_analysis_v2.py`.

### Flowchart

`cross_sectional_analysis_flowchart.png` / `.svg` — three parallel tracks
(cortical thickness+myelin, wmparc volumes, HCPex midbrain/basal-forebrain
volumes with the standard-vs-native split) converging on the saved output.

### v1 vs v2 — differences

| | v1 | v2 |
|---|---|---|
| Cortical thickness / myelin | ✓ Schaefer-400 | ✓ Schaefer-400 (unchanged) |
| wmparc regional volumes | ✓ every FreeSurfer label | ✓ every FreeSurfer label (unchanged) |
| VTA / SN / Nucleus Basalis volumes | — not covered | ✓ via HCPex, standard + native space |
| External dependency | nibabel only | + FSL `applywarp` on PATH |
| Extra output file | — | `midbrain_basalforebrain_volumes_hcpex.csv`, `midbrain_basalforebrain_volumes_hcpex.png` |
| Run-count log | `analysis_log_anat.json` | `analysis_log_anat_v2.json` (separate) |
| Batch driver | — none | `cross_sectional_analysis_batch_v2.py` |

Both share the eTIV caveat: no `aseg.stats` (or equivalent) exists anywhere
in this dataset's structural output, so all volumes are raw mm³, not
head-size-normalized.

---

## 5. Functional connectivity pipeline

Two script versions covering the **atlas architecture** axis (v1 → v2), plus
one batch driver covering the **interactive vs. batch** axis.

### v1 — `run_fc_pipeline_v1.py`

(Renamed 2026-07-23 from `run_fc_pipeline_v2.py` — code untouched. Its
internal run-count log is still literally named `analysis_log_v2.json` and
its timeseries cache is still `timeseries_<session>.csv`.)

The interactive pipeline. Parcellates the grayordinate dtseries into
400 Schaefer cortical + 16 Tian-S1 subcortical parcels via
`wb_command -cifti-parcellate` (**Group A/B**, CIFTI path), then separately
extracts 5 CIT168 midbrain nuclei via probability-weighted volumetric
extraction (**Group C**) and 6 HCPex basal-forebrain structures via mask-mean
volumetric extraction (**Group D**) — 427 parcels total. Group B, C, and D
all read the one volumetric BOLD file with the same mask-mean method; only
Group A (cortex) stays on the CIFTI/grayordinate + MSMAll path, since
surface-registration precision is the actual reason to prefer it there. Lets
you pick any subset across the four groups (e.g. `'B1, C3, D5-6'`) before
computing the Pearson + Fisher-z FC matrix and a labeled heatmap. Group A's
atlas card shows a human-readable label per parcel (e.g. "A107 L
SalVentAttn Medial #1") — display only, extraction/region_names.txt/matrix
axis labels still use the raw Schaefer name. Saved as plain CSV (not `.npy`)
into `Analysed_data/<subject>/<session>/func/`.

**No mode menu** — after picking a subject/session and which parcels to
include in the "standard" matrix, every run always computes the same fixed
five analyses (four of five have no choice to make anyway):

1. `standard` — FC matrix over whichever parcels you picked
2. `graph_vta` — fixed VTA + NbM(L) + NbM(R) + HC(L) + HC(R) graph, 5 nodes,
   left/right kept separate
3. `graph_sn` — fixed SNc + SNr + NbM(L) + NbM(R) + HC(L) + HC(R) graph,
   6 nodes, left/right kept separate
4. `triangle_vta` — same nodes as `graph_vta`, but left/right pairs averaged
   first → plain 3-node triangle (NbM, VTA, Hippocampus)
5. `triangle_sn` — same idea for SN (SNc+SNr averaged into one "SN" node) →
   plain 3-node triangle (NbM, SN, Hippocampus)

`graph_vta`/`graph_sn` render as a node-link graph (region name only, no
atlas/source text) instead of the square heatmap: each edge shows its
Pearson r, colored + thickness-scaled on the same red/blue diverging scale as
the heatmap. `triangle_vta`/`triangle_sn` use the same graph style, just with
the combined 3-node set.

**Standard command:**
```bash
python3 run_fc_pipeline_v1.py
```

**Output** — `Analysed_data/<subject>/<session>/func/`, for
`mode in {standard, graph_vta, graph_sn, triangle_vta, triangle_sn}`:
- `fc_matrix_corr_<mode>.csv`, `fc_matrix_fisherz_<mode>.csv`
- `region_names_<mode>.txt`
- `fc_matrix_<mode>.png` / `.svg`

All five save directly into that one `func/` subfolder (no per-mode
subfolder within it) as plain CSV, filename suffixed by which analysis
produced it so nothing collides. Full 427-parcel timeseries cached
per-subject at `Analysed_data/<subject>/timeseries_<session>.csv` (plain
CSV; delete to force a fresh extraction). Run-count log: `analysis_log_v2.json`.

**Requires:** atlas files under `<raw root>/atlases/` (or the
`/Volumes/njainmpi/...` fallback): `schaefer400_tianS1.dlabel.nii`,
`CIT168_prob_func2mm.nii.gz`, `CIT168_labels.txt`, `HCPex_2mm.nii`,
`HCPex_basal_forebrain_labels.txt`, `Tian_Subcortex_S1_3T.nii`,
`Tian_Subcortex_S1_labels.txt` (the official volumetric Tian-S1 atlas,
verified on the same 91x109x91 @ 2mm grid as CIT168/HCPex).

### v2 — `run_fc_pipeline_v2.py`

(Renamed 2026-07-23 from `run_fc_pipeline_v3.py` — code untouched. Its
internal run-count log is still literally named `analysis_log_v3.json` and
its timeseries cache is still `timeseries_hcpex_<session>.csv`.)

A different architecture, not just an extension: **every** parcel — cortex
included — comes from **one** atlas, HCPex (426 regions: 360 HCP-MMP1.0
cortex + 66 subcortical/midbrain/basal-forebrain), one file, one volumetric
mask-mean extraction method. This drops the CIFTI/MSMAll path entirely (the
tradeoff: losing MSMAll's surface-registration precision for cortex, in
exchange for one unified pipeline). Left/right are kept separate for the
`graph_*` modes because HCPex lateralizes VTA and SNpc/SNpr (unlike CIT168,
which v1 used). Every run always computes all five analyses for the chosen
session (standard, graph_vta, graph_sn, triangle_vta, triangle_sn), saved as
CSV into their own `Analysed_data/<subject>/<session>/func/` subfolder —
mirroring the `anat/` subfolder `cross_sectional_analysis_v2.py` uses —
filenames suffixed `_hcpex`.

**Standard command:**
```bash
python3 run_fc_pipeline_v2.py
```

**Output** — `Analysed_data/<subject>/<session>/func/`, for
`mode in {standard, graph_vta, graph_sn, triangle_vta, triangle_sn}`:
- `fc_matrix_corr_<mode>_hcpex.csv`, `fc_matrix_fisherz_<mode>_hcpex.csv`
- `region_names_<mode>_hcpex.txt`
- `fc_matrix_<mode>_hcpex.png` / `.svg`

Full 426-parcel timeseries cached per-subject at
`Analysed_data/<subject>/timeseries_hcpex_<session>.csv` (delete to force a
fresh extraction, e.g. after updating the atlas file).

**Requires:** atlas files under `<raw root>/atlases/` (or the
`/Volumes/njainmpi/...` fallback): `HCPex_2mm.nii`, `HCPex_LookUpTable.txt`
(full descriptive parcel names — not the short-code
`HCPex_basal_forebrain_labels.txt` that `run_fc_pipeline_v1.py`'s Group D
uses).

### Batch variant — `run_fc_pipeline_batch_v2.py`

(Rebuilt from scratch 2026-07-23 — this filename previously referred to a
different script, the batch counterpart of the old CIT168/Tian-S1 pipeline
now called v1. This is a new, unrelated script batching the *current*
`run_fc_pipeline_v2.py`, the HCPex-only pipeline.)

Non-interactive driver: imports `run_fc_pipeline_v2.py` as a module and
reuses its extraction/analysis/plotting/logging functions unchanged,
replacing only the interactive subject/session/parcel pickers with a loop
over every `sub-*/ses-*` session found under a raw data root. The
"standard" matrix's parcel selection is made **once** up front (same syntax
as the interactive card picker — `all`, a bare group letter, or
labels/ranges like `'A1, B2, B4'` / `'A1-10'`) and applied identically to
every session, since HCPex's 426 parcel names/order are fixed by the atlas,
not subject-specific. Sessions that already have a standard FC matrix are
skipped unless `--force`; each session runs in its own `try`/`except` so one
failure doesn't abort the batch; a done/skipped/missing/failed summary
prints at the end. Shares `analysis_log_v3.json` with the interactive
`run_fc_pipeline_v2.py`, so a session processed here shows up
already-analysed if you later open the interactive tool on it.

**Standard command:**
```bash
python3 run_fc_pipeline_batch_v2.py /path/to/Raw_Data
```

**Useful flags:**
```bash
python3 run_fc_pipeline_batch_v2.py /path/to/Raw_Data --force --parcels all --subjects sub-HCA6002236
```
- (raw_data_root omitted) — falls back to the same interactive
  tab-completing path prompt as `run_fc_pipeline_v2.py`
- `--force` — re-run sessions that already have `fc_matrix_corr_standard_hcpex.csv`
- `--subjects sub-A,sub-B` — restrict to specific subject folder names
- `--parcels all` — parcel selection for the "standard" matrix (default
  `all`; same syntax as the interactive card picker)

**Output** (per session, see `run_fc_pipeline_v2.py`) —
`Analysed_data/<subject>/<session>/func/`: `fc_matrix_corr_<mode>_hcpex.csv`,
`fc_matrix_fisherz_<mode>_hcpex.csv`, `region_names_<mode>_hcpex.txt`,
`fc_matrix_<mode>_hcpex.png` / `.svg` for
`mode in {standard, graph_vta, graph_sn, triangle_vta, triangle_sn}`.

**Requires:** same atlas files as `run_fc_pipeline_v2.py`.

**Caution:** this dataset currently has 73 sessions. Know the real scope
before launching a "quick test" — check `find Raw_Data -name "ses-*" | wc -l`
first, or use `--subjects` to restrict to a couple of subjects, since letting
it run against every session is very different from a smoke test.

There is no batch driver for `run_fc_pipeline_v1.py` in this folder (the one
that used to fill that role, batching the old CIT168/Tian-S1 pipeline, was
removed along with `run_fc_pipeline.py`).

### Flowcharts

- `fc_correlation_estimation_flowchart.png` / `.svg` — how
  `run_fc_pipeline_v1.py` turns a T×N regional-timeseries matrix into a
  Pearson correlation matrix (per-region extraction method, triangle-mode
  combining, Fisher-z clipping detail).
- `fc_pipeline_v1_flowchart.png` — the two-track (CIFTI cortex+subcortex /
  volumetric midbrain) architecture of the v1 pipeline end to end. No
  editable source; recovered from the existing presentation's embedded
  media after the standalone file went missing. Its title text still reads
  "Functional Connectivity Pipeline v2" internally, predating the
  2026-07-23 rename — it depicts what is now v1.

### Version differences — v1 vs v2

| | v1 | v2 |
|---|---|---|
| Cortex source | CIFTI (Schaefer-400) | Volumetric (HCPex) |
| Subcortex source | Volumetric (Tian-S1) | Volumetric (HCPex) |
| Midbrain source | Volumetric (CIT168) | Volumetric (HCPex) |
| Basal forebrain source | Volumetric (HCPex) | Volumetric (HCPex) |
| Total parcels | 427 | 426 |
| Number of atlas files needed | 4 | 1 |
| Analysis modes | 5 (fixed, no menu) | 5 (fixed, no menu) |
| Output format | CSV | CSV |
| Output subfolder | `func/`, filename suffix per mode | `func/`, `_hcpex` filename suffix |
| Run-count log | `analysis_log_v2.json` | `analysis_log_v3.json` |
| Cortex parcel display | human-readable labels (display only) | raw HCPex names |
| Batch driver | none in this folder | `run_fc_pipeline_batch_v2.py` |

### Interactive vs. batch

| | Interactive (`run_fc_pipeline_v1/v2.py`) | Batch (`run_fc_pipeline_batch_v2.py`) |
|---|---|---|
| Session selection | colored tile picker, one at a time | none — every `sub-*/ses-*` found |
| Parcel choice (no version has a mode menu) | prompted per session | asked once, applied to all |
| Progress tracking | live terminal, resumable | done/skipped/missing/failed summary |
| Coverage | v2 only (see above) | v2 only |

---

## 6. Presentation builders

Both are **reusable** — meant to be re-run whenever a source script's
behavior changes, not one-off scripts. Both do a fresh `Presentation()` each
run and overwrite their output file in place, so re-running any time is safe.

### `build_scripts_overview_presentation.py`

**What it does:** builds a standalone presentation, "Data Analysis Scripts
Overview" — companion to this README, independent of
`rsfMRI_processing_summary*.pptx` (which documents a specific session's
processing results, not the scripts themselves). Covers: data hierarchy (raw
+ `Analysed_data` + shared atlases), `organize_hcp_data.py` (what/how + its
flowchart), `cross_sectional_analysis_v1.py`/`_v2.py` (+ flowchart + a
v1-vs-v2 differences table + the `cross_sectional_analysis_batch_v2.py`
batch variant), `run_fc_pipeline_v1.py`/`_v2.py` + the
`run_fc_pipeline_batch_v2.py` batch variant (+ both FC flowcharts + a
v1-vs-v2 differences table), a "System Prerequisites" slide, and a "Correct
Sequence — Running the Scripts" slide.

**Standard command:**
```bash
python3 build_scripts_overview_presentation.py
```

**Output:** `presentations/Data_Analysis_Scripts_Overview.pptx` (in the
`presentations/` folder, one directory up from this scripts folder).

### `build_combined_presentation.py`

**What it does:** merges `rsfMRI_processing_summary_with_anat_appendix.pptx`
(39 slides) and `Data_Analysis_Scripts_Overview.pptx` into one sequential
deck: a new title + master contents, then each source deck's own content
(minus its own title/contents slides) under a "PART I" / "PART II" divider,
plus a "PART III · Data Statistics" section appended at the very end (4
pre-made figures from `results/`: `scan_sessions_5groups.png`,
`age_range_boxplot_5groups.png`, `interval_summary_table.png`,
`interval_summary_rangeplot.png`, covering the broader HCP-Aging AABC
cohort's scan-session counts, age distributions, and inter-visit intervals
across 5 demographic/cognitive filter groups).

`python-pptx` has no cross-presentation "copy slide" API, so `copy_slide()`
deep-copies each source slide's background + shape XML into a freshly-added
blank slide, re-relating any embedded images (rewriting each `<a:blip>`
`r:embed` rId to point at a newly-added image part in the destination
package — otherwise copied picture references would be broken links).

Ends with a full consistency pass so the result reads as one deck, not two
stitched together: every page-number badge across every slide is renumbered
to its true combined-deck position (single lookup per slide, not sequential
text replacement, which would cascade); the appendix-overview and
appendix-subsection index slides (which name slide numbers in their own body
text) are remapped via a single-pass regex substitution for the same reason;
and the Scripts-deck-style divider page badge (bottom-left) is repositioned
to match every other slide's bottom-right convention.

**Standard command:**
```bash
python3 build_combined_presentation.py
```

**Output:** `presentations/Combined_rsfMRI_and_Scripts_Overview.pptx` (in
the `presentations/` folder, one directory up from this scripts folder).
Reads its 4 Data Statistics PNGs from `results/`, and its two source decks
from `presentations/` (both one directory up from this scripts folder).
