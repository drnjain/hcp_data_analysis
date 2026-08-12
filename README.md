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

> **Note (2026-07-27):** `run_fc_pipeline_batch_v2.py`'s `--parcels` flag no
> longer silently defaults to `all` when omitted — it now prompts
> interactively once (same single-card parcel picker as the single-session
> script) before the batch starts, matching the interactive script's own
> behavior instead of silently running against every parcel. `combined_analysis_v2.py`
> was rebuilt and extended: it now also runs `organize_hcp_data.py`'s
> data-organization step first, using the same Input/Output prompts as that
> script's required `--input`/`--output` flags (see Section 6). A new
> `combined_analysis_batch_v2.py` was added — the non-interactive batch
> driver for `combined_analysis_v2.py`'s two analysis parts (organize step
> excluded).

> **Note (2026-07-29):** a new shared module, `region_grouping.py`, lets every
> v2 script combine parcels into composite regions (left+right pairs, network
> components, or a custom JSON). It is on by default: every run now writes the
> per-parcel results **and** a left/right-combined copy beside them, controlled
> by `--groups` on the batch drivers and `group_analysis_cross_sectional.py`, or
> by a prompt in the three interactive scripts. `--groups none` restores the old
> single-output behaviour. See Section 9, plus the per-script notes in Sections
> 4-7. `parcellate_cortical_dscalar()` gained an optional `return_counts`
> argument in the same pass (backwards compatible).

Also present in this folder but not covered below: the one-off pptx-editing
scripts that built up `rsfMRI_processing_summary_with_anat_appendix.pptx`
slide by slide have been removed — their effects are already permanently
baked into that presentation, so nothing currently visible depends on them
(see `SESSION_SUMMARY.md` for what each one did). The two *reusable* pptx
builders (Section 8 below) were kept, since those regenerate their decks
from scratch and are meant to be re-run whenever a source script changes.

### Python dependencies

```bash
pip install -r requirements.txt
```

Installs the third-party packages this folder's scripts need: `numpy`,
`pandas`, `matplotlib`, `nibabel`, `python-pptx`, `Pillow` (used across most
scripts), plus `scipy` (used only by `group_analysis_cross_sectional.py`, for
t/F-distribution p-values).
`requirements.txt` also lists — as comments, since they're not on PyPI —
the external command-line tools some scripts additionally require:
Connectome Workbench's `wb_command`, FSL's `applywarp`, and FreeSurfer (for
`FreeSurferColorLUT.txt`). See each script's own **Requires** note below for
which of those it needs.

---

## Quick command reference

| Script | Command | Purpose |
|---|---|---|
| `organize_hcp_data.py` | `python3 organize_hcp_data.py --input DIR --output DIR` | Sort raw HCP zip downloads into `sub-*/ses-*/{anat,func,concat}/` |
| `plot_roi_amplitudes.py` | `python3 plot_roi_amplitudes.py --group CSV --data CSV` | Interactive box plots of ROI BOLD amplitudes by visit |
| `run_fc_pipeline_v1.py` | `python3 run_fc_pipeline_v1.py` | Interactive FC pipeline, 427 parcels (Schaefer + Tian-S1 + CIT168 + HCPex) |
| `run_fc_pipeline_v2.py` | `python3 run_fc_pipeline_v2.py` | Interactive FC pipeline, HCPex-only, 426 parcels; also prompts to write a left/right-combined matrix |
| `run_fc_pipeline_batch_v2.py` | `python3 run_fc_pipeline_batch_v2.py /path/to/Raw_Data [--groups lr]` | Non-interactive batch driver for `run_fc_pipeline_v2.py` (73 sessions currently) |
| `cross_sectional_analysis_v1.py` | `python3 cross_sectional_analysis_v1.py` | Cortical thickness/myelin (Schaefer-400) + wmparc regional volumes |
| `cross_sectional_analysis_v2.py` | `python3 cross_sectional_analysis_v2.py` | v1 + VTA/SN/Nucleus Basalis volumes via HCPex (standard + native space), + a combined figure of any regions you pick; also prompts to write composite-region copies of every measure |
| `cross_sectional_analysis_batch_v2.py` | `python3 cross_sectional_analysis_batch_v2.py /path/to/Raw_Data [--groups lr]` | Non-interactive batch driver for `cross_sectional_analysis_v2.py` |
| `combined_analysis_v2.py` | `python3 combined_analysis_v2.py` | Organizes new zip data, then runs `cross_sectional_analysis_v2.py` + `run_fc_pipeline_v2.py` back to back for one subject/session |
| `combined_analysis_batch_v2.py` | `python3 combined_analysis_batch_v2.py /path/to/Raw_Data [--groups lr]` | Non-interactive batch driver for both analysis parts of `combined_analysis_v2.py` (organize step excluded) |
| `group_analysis_cross_sectional.py` | `python3 group_analysis_cross_sectional.py /path/to/Raw_Data [--groups lr]` | Group-level stats (age/sex/custom, FDR-corrected) on the extracted anatomical measures, one session per subject |
| `region_grouping.py` | imported, not run directly (`--groups` flag / prompt) | Combine parcels into composite regions (left+right, network components, or a custom JSON) — see Section 9 |
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
- `selected_regions_volumes.csv` + `selected_regions_volumes_log.png` +
  `selected_regions_volumes_linear.png` — the combined selected-region figure
  (see below)
- Own run-count log: `Analysed_data/analysis_log_anat_v2.json` (separate from v1's)

#### Combined selected-region figure (interactive)

(New 2026-07-28.) The two standard volume figures split the regions across
separate plots — `subcortical_volumes_key_structures.png` (wmparc) and
`midbrain_basalforebrain_volumes_hcpex.png` (HCPex). Both are still written
unchanged. **In addition**, after those are saved the script prompts for a set
of regions and plots them *all together on one axis*.

The selection pool is every region the run extracted, grouped for quick
picking:

| Group | Contents |
|---|---|
| `key` | The 7 key subcortical structures × L/R (wmparc) |
| `midbrain` | VTA, SNc, SNr, NbM × L/R (HCPex, native space) |
| `other` | Other non-cortical wmparc labels (Brain-Stem, CSF, …) |
| `cortex` | `ctx-*` cortical ribbon labels (wmparc) |
| `wm` | `wm-*` and unsegmented white-matter labels (wmparc) |

Selection accepts individual numbers (`1,5`), ranges (`10-14`), group names,
or `all` — combine any of them with commas. Pressing Enter takes the default,
`key + midbrain`, which is exactly the union of the two standard figures.

The same selection is rendered **twice**, as horizontal bars sorted
largest-first:

- `selected_regions_volumes_log.png` — log x-axis. Keeps small nuclei legible:
  VTA (~60 mm³) stays readable next to the thalamus (~7,700 mm³).
- `selected_regions_volumes_linear.png` — linear x-axis. Preserves true
  proportions, at the cost of the smallest nuclei being near-invisible.

Bars are colour-coded by source (teal = wmparc/FreeSurfer, orange = HCPex
warped to native). **All values on this figure are native-space mm³** so the
two atlas sources are directly comparable — the HCPex numbers used are the
warped native ones, never the standard-space ones. The chosen regions are also
written to `selected_regions_volumes.csv` (`region, source, group,
volume_mm3`) and listed in `manifest.txt`, so the figure is reproducible after
the fact.

A region with zero volume (possible if the warp loses a very small HCPex
structure) appears in the linear figure but cannot be drawn on a log axis; the
script prints a count when this happens.

**This step is interactive and lives only in `main()`** — the batch drivers
(`cross_sectional_analysis_batch_v2.py`, `combined_analysis_batch_v2.py`) call
the extraction/plotting functions directly and are unaffected, still writing
the three standard figures only.

**Requires:** everything v1 requires, plus `HCPex_2mm.nii` +
`HCPex_LookUpTable.txt` under `<raw root>/atlases/` (already present from
`run_fc_pipeline_v2.py`), and FSL's `applywarp` on `PATH`.

**Note:** same eTIV caveat as v1 — all volumes reported are raw mm³.

#### Composite-region copies (2026-07-29)

Every anatomical measure is written as the same **four fixed views** the FC
pipeline uses — there is no prompt, and nothing is overwritten:

| view | file suffix | contents |
|---|---|---|
| 1 | `_left` | left-hemisphere regions only |
| 2 | `_right` | right-hemisphere regions only |
| 3 | *(none)* | every region — the original file |
| 4 | `_combined` | left/right pairs merged into one region |

**Row order matches the FC matrices**: each region's left and right sit adjacent,
left first (`Left-Hippocampus, Right-Hippocampus, Left-Amygdala, …`), instead of
label order which puts every left first and its right partner far below. Applied
inside the CSV writers, so the interactive script, all three batch drivers and
the browser app inherit it. Unlateralised structures (Brain-Stem, CSF) follow
after. The *figures* are unaffected — they deliberately sort largest-first by
value. Downstream readers key on the `region` column, not row position, so
`group_analysis_cross_sectional.py` is unaffected.

Hemisphere is detected per naming convention via
`region_grouping.split_hemisphere()`, so each measure splits correctly in its
own name space — Schaefer `7Networks_LH_…`, wmparc `Left-…` / `ctx-lh-…`, HCPex
`…_L`. Structures with no hemisphere (Brain-Stem, CSF) appear in neither the
left nor the right file, only in views 3 and 4.

`--groups` on the batch variants still adds a **further** grouping
(`component`, a custom JSON) beside these four, written under its own tag so it
cannot overwrite view 4. The `_combined` files:

```
cortical_thickness_schaefer400_combined.csv    region, thickness_mm, vertex_count, n_parcels
myelin_schaefer400_combined.csv                region, myelin_ratio, vertex_count, n_parcels
subcortical_volumes_wmparc_combined.csv        region, voxel_count, volume_mm3, n_parcels, members
midbrain_basalforebrain_volumes_hcpex_combined.csv
                                               region, standard_volume_mm3, native_volume_mm3, n_parcels, members
combined_volumes_wmparc.png                    top 30 combined volumes, largest first
region_groups_anat_combined.json               exactly what was merged into what
```

Volumes are **summed**; cortical thickness and myelin take a **vertex-count-
weighted mean** (the plain mean of two parcel means is wrong when the parcels
differ in size). See Section 9 for the rules and the Schaefer caveat.

**API change:** `parcellate_cortical_dscalar()` gained
`return_counts=True`, which additionally returns `{region: vertex_count}` — the
weights the combining needs. The 3-argument call still behaves exactly as
before, so existing callers are unaffected.

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

**Output** (per session) — `Analysed_data/<subject>/<session>/anat/`:
`cortical_thickness_schaefer400.csv`, `myelin_schaefer400.csv`,
`subcortical_volumes_wmparc.csv`, `midbrain_basalforebrain_volumes_hcpex.csv`,
`subcortical_volumes_key_structures.png`,
`midbrain_basalforebrain_volumes_hcpex.png`,
`cortical_thickness_myelin_summary.png`, `manifest.txt`. Shares
`analysis_log_anat_v2.json` with `cross_sectional_analysis_v2.py`.

This is the same set the interactive script writes **except** the combined
selected-region outputs (`selected_regions_volumes.csv` /
`selected_regions_volumes_{log,linear}.png`) — that step needs an interactive
region choice, so it's deliberately not part of the batch path.

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
| Combined selected-region figure | — | ✓ interactive picker → `selected_regions_volumes_{log,linear}.png` + `.csv` |
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
filenames suffixed `_hcpex`. **The standard analysis is written as four
connectivity maps** (left only / right only / both / left+right combined) — see
below.

**Standard command:**
```bash
python3 run_fc_pipeline_v2.py
```

**Output** — `Analysed_data/<subject>/<session>/func/`, for
`mode in {standard, graph_vta, graph_sn, triangle_vta, triangle_sn}`:
- `fc_matrix_corr_<mode>_hcpex.csv`, `fc_matrix_fisherz_<mode>_hcpex.csv`
- `region_names_<mode>_hcpex.txt`
- `fc_matrix_<mode>_hcpex.png` / `.svg`

**The standard matrix is written as four maps**, whatever number of parcels you
pick. With a selection of N parcels:

| map | file suffix | contents |
|---|---|---|
| 1 | `standard_hcpex_left` | only the `_L` parcels of the selection |
| 2 | `standard_hcpex_right` | only the `_R` parcels |
| 3 | `standard_hcpex` | all N parcels, left and right side by side |
| 4 | `standard_hcpex_combined` | each L/R pair merged into one region (`Hippocampus_L` + `Hippocampus_R` → `Hippocampus`) |

**Region order.** All four maps are laid out so each region's left and right sit
adjacent, left first — `Hippocampus_L, Hippocampus_R, SNc_L, SNc_R, …` — with
regions in the order you first selected them. Without this the matrix follows
HCPex label order, which puts every left parcel first and its right counterpart
300+ labels later (`Hippocampus_L` is 80, `Hippocampus_R` is 260), so a region's
two halves land at opposite corners of the heatmap. Maps 1, 2 and 4 follow the
same region order, so the four can be read side by side.

Map 3 keeps the bare `standard_hcpex` name it has always had, so existing
outputs and skip-if-done checks are unaffected. **Map 4 is always produced** —
it no longer depends on the grouping prompt. Maps 1, 2 and 4 need at least 2
regions to correlate; if a selection cannot reach that (e.g. one parcel in a
hemisphere) that map is skipped with a printed reason and the others still run.
The graph/triangle analyses are unaffected.

Full 426-parcel timeseries cached per-subject at
`Analysed_data/<subject>/timeseries_hcpex_<session>.csv` (delete to force a
fresh extraction, e.g. after updating the atlas file).

**Requires:** atlas files under `<raw root>/atlases/` (or the
`/Volumes/njainmpi/...` fallback): `HCPex_2mm.nii`, `HCPex_LookUpTable.txt`
(full descriptive parcel names — not the short-code
`HCPex_basal_forebrain_labels.txt` that `run_fc_pipeline_v1.py`'s Group D
uses).

#### Combined-region FC matrix (2026-07-29)

**There is no grouping prompt** — the four maps above are fixed, so the question
was redundant and has been removed from every interactive script. `--groups` on
the batch drivers (and the app) survives only to add a **further** grouping
(`component`, `component-hemi`, a custom JSON) on top of the four, written under
its own suffix so it cannot overwrite map 4; passing `lr` is recognised as
already-covered and skipped rather than duplicated. The four fixed
graph/triangle analyses are untouched — the triangle ones already combine L/R by
construction.

The parcels are combined **first** (their timeseries averaged sample by sample)
and the correlation computed on the composites. That is deliberately not the
same as averaging the parcel-pair correlations afterwards: the first treats a
pair as one region, the second averages two measurements of different regions.

```
fc_matrix_corr_standard_hcpex_combined.csv      (+ _fisherz)
fc_matrix_standard_hcpex_combined.png/.svg      heatmap — same form as its parent analysis
fc_matrix_standard_hcpex_combined_graph.png/.svg
                                                node-link view, only when regions <= MAX_GRAPH_NODES (12)
region_names_standard_hcpex_combined.txt
region_groups_standard_hcpex_combined.json
```

### Batch variant — `run_fc_pipeline_batch_v2.py`

(Rebuilt from scratch 2026-07-23 — this filename previously referred to a
different script, the batch counterpart of the old CIT168/Tian-S1 pipeline
now called v1. This is a new, unrelated script batching the *current*
`run_fc_pipeline_v2.py`, the HCPex-only pipeline.)

Non-interactive driver: imports `run_fc_pipeline_v2.py` as a module and
reuses its extraction/analysis/plotting/logging functions unchanged,
replacing only the interactive subject/session/parcel pickers with a loop
over every `sub-*/ses-*` session found under a raw data root. The
"standard" matrix's parcel selection is made **once** up front and applied
identically to every session, since HCPex's 426 parcel names/order are fixed
by the atlas, not subject-specific. If `--parcels` is omitted, this prompts
interactively **once** using the same single-card parcel picker as the
single-session script (before the batch starts, not per session) — pass a
value (`all`, or HCPex label numbers/ranges like `'80, 260, 388'` /
`'1-10'`) to skip the prompt and run fully non-interactively. Sessions that already have a standard FC matrix are
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
- `--parcels all` — parcel selection for the "standard" matrix; if omitted,
  prompts interactively once (same single-card parcel picker as the
  single-session script) instead of defaulting silently to `all`

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

## 6. `combined_analysis_v2.py` — combined organize + anatomical + functional pipeline

Runs three things back to back for one subject/session: data organization
(`organize_hcp_data.py`), cross-sectional anatomical analysis
(`cross_sectional_analysis_v2.py`), and the FC pipeline (`run_fc_pipeline_v2.py`).
The two analysis scripts prompt for the raw data root and a subject/session
independently, so running them back to back for the same session means
picking the subject/session twice and validating atlas files twice — this
script imports all three as modules and reuses their
extraction/analysis/plotting/logging functions unchanged.

- **Part 0 — data organization:** same **Input**/**Output** prompts as
  `organize_hcp_data.py`'s required `--input`/`--output` flags, asked every
  run (not skippable) — Input is the folder of new HCP zip packages, Output
  is the raw data root to organize into. Output does not need to already
  exist (created via `mkdir` if missing, same as `organize_hcp_data.py`) and
  becomes the raw data root used by Parts 1–2 below — there's no separate
  "raw data root" prompt after this. Unlike the standalone script, this step
  does **not** abort if the input folder currently has zero new zip files —
  it just notes that and continues into the anatomical/functional analysis,
  since a routine re-analysis run with no new data to organize shouldn't be
  blocked. Each zip also runs in its own `try`/`except` — unlike the
  standalone script's own `main()` loop, a transient failure reading one zip
  (e.g. an I/O error off a network-mounted raw-data volume) is logged as an
  `error_exception` manifest row and skipped, rather than aborting the whole
  combined run before it ever reaches the subject/session picker or the
  analysis parts. The failure point (`extract_zip()`'s staging-directory
  extraction) is always before `merge_tree()` ever touches the real target
  folder, so a failed zip is safe to retry on a later run — nothing partial
  is left in `<raw root>/sub-*/ses-*/`. Otherwise reuses
  `organize_hcp_data.py`'s own `process_zip()` / `MANIFEST_FIELDS`
  unchanged, so extraction/archiving/manifest behavior matches running that
  script directly (Section 2).
- **Part 1 — cross-sectional anatomical analysis (v2):** cortical thickness +
  myelin (Schaefer-400), wmparc regional volumes, and VTA/SN/Nucleus Basalis
  volumes via HCPex (standard + native space). Identical to
  `cross_sectional_analysis_v2.py` (Section 4).
- **Part 2 — functional connectivity pipeline (v2, HCPex-only):** all 426
  HCPex parcels, the same five fixed analyses (standard, graph_vta, graph_sn,
  triangle_vta, triangle_sn). Identical to `run_fc_pipeline_v2.py` (Section 5).

After Part 0, one combined subject/session tile picker (tile counts sum both
analysis scripts' logs) and one upfront check for the union of both analysis
scripts' required files, then Parts 1–2 run in sequence into their normal
output locations. Each analysis part writes to its own normal output
location and records into its own normal run-count log
(`analysis_log_anat_v2.json` / `analysis_log_v3.json`) — deliberately
**not** a third combined log — so a session analysed here shows up
already-analysed if you later open either single-purpose interactive script
on it.

**Standard command:**
```bash
python3 combined_analysis_v2.py
```
Prompts, in order: Input (zip folder) → Output (raw data root) → subject →
session → "standard" FC parcel selection.

**Same four views as the standalone scripts.** Part 1 writes the four
anatomical views (`_left`, `_right`, plain, `_combined`) of every measure and
Part 2 writes the four connectivity maps, because both call the same shared
functions (`csa.save_hemisphere_measures` / `csa.save_combined_measures` and
`fcp.run_standard_maps`) rather than reimplementing them — so this script cannot
drift from `cross_sectional_analysis_v2.py` or `run_fc_pipeline_v2.py`.

**Output:**
- `<raw root>/sub-*/ses-*/...`, `<raw root>/archive/`, `<raw root>/manifest.csv` — from the organize step (Part 0)
- `Analysed_data/<subject>/<session>/anat/` — see `cross_sectional_analysis_v2.py` (Section 4)
- `Analysed_data/<subject>/<session>/func/` — see `run_fc_pipeline_v2.py` (Section 5)

**Requires:** the union of all three scripts' requirements —
`schaefer400_tianS1.dlabel.nii`, `HCPex_2mm.nii`, `HCPex_LookUpTable.txt`
under `<raw root>/atlases/`, `FreeSurferColorLUT.txt`, and FSL's `applywarp`
on `PATH` (`organize_hcp_data.py` itself needs nothing beyond the standard
library).

**Note:** not documented in either pptx deck (`Data_Analysis_Scripts_Overview.pptx`
or the combined deck) as of this writing — ask before adding it there if that's wanted.

#### Region grouping (2026-07-29)

One grouping question per run, asked after the parcel picker, applied to **both**
halves: the anatomical measures (in the Schaefer / wmparc / HCPex name spaces,
each resolved separately) and the standard FC matrix. The batch variant takes
`--groups` instead, default `lr`. Outputs are the union of the two lists in
Sections 4 and 5. See Section 9.

### Batch variant — `combined_analysis_batch_v2.py`

(New 2026-07-27.) Non-interactive driver for Parts 1–2 of
`combined_analysis_v2.py` — **not** Part 0; the organize step stays a
separate manual/interactive step (via `organize_hcp_data.py` directly, or
`combined_analysis_v2.py`'s Part 0) done before batch-processing a folder of
already-organized sessions. Imports `cross_sectional_analysis_v2.py` and
`run_fc_pipeline_v2.py` as modules and reuses their functions unchanged,
looping over every `sub-*/ses-*` session found under a raw data root.

Skip-if-already-done is tracked **per part, independently** — a session
where only one part has already been run (e.g. via the standalone batch
drivers) gets just the missing part filled in, not both redone; `--force`
reruns both. The required-files check is all-or-nothing for whichever
part(s) still need to run on a given session (same upfront-validation
philosophy as `combined_analysis_v2.py` itself). Subject-invariant work
(FreeSurfer/HCPex LUTs, the cortex vertex LUT, HCPex standard-space volumes,
the "standard" FC parcel selection) is computed once up front, same as both
standalone batch drivers. Writes to each part's own normal output location
and run-count log (`analysis_log_anat_v2.json` / `analysis_log_v3.json`) —
same as `combined_analysis_v2.py`, not a third combined log.

**Standard command:**
```bash
python3 combined_analysis_batch_v2.py /path/to/Raw_Data
```

**Useful flags:**
```bash
python3 combined_analysis_batch_v2.py /path/to/Raw_Data --force --parcels all --subjects sub-HCA6002236
```
- (raw_data_root omitted) — falls back to the same interactive
  tab-completing path prompt as `cross_sectional_analysis_v2.py`
- `--force` — re-run **both** parts for sessions that already have them
- `--subjects sub-A,sub-B` — restrict to specific subject folder names
- `--parcels` — parcel selection for the "standard" FC matrix; if omitted,
  prompts interactively once (same single-card parcel picker as the
  single-session script)

**Output** (per session, union of both parts) —
`Analysed_data/<subject>/<session>/anat/` (see `cross_sectional_analysis_v2.py`)
and `Analysed_data/<subject>/<session>/func/` (see `run_fc_pipeline_v2.py`).
Per-session status line reports which part(s) actually ran, e.g.
`[done] sub-X/ses-Y (anat: done, func: skipped)`.

**Requires:** same as `combined_analysis_v2.py`'s Parts 1–2 (the organize
step's requirements don't apply here, since this batch driver doesn't run
it).

**Note:** not yet run against real `Analysed_data` (only compile/import/
signature-verified) — like `combined_analysis_v2.py`, not documented in
either pptx deck as of this writing.

---

## 7. `group_analysis_cross_sectional.py` — group-level cross-sectional statistics

**What it does:** the missing statistics step after `cross_sectional_analysis_v2.py`
/ `_batch_v2.py` — those only extract per-session values into
`Analysed_data/<subject>/<session>/anat/*.csv`, with no group comparison at
all. This script reads those CSVs plus the demographics CSV (`AABC2_subjects_*.csv`,
matched by `id_event = "<subject>_<visit>"`, e.g. `HCA6002236_V1`), and runs a
per-region linear model with FDR correction.

**Cross-sectional design note:** every subject here has multiple sessions
(V1/V2/V3...). Pooling every session as an independent row would violate the
independence assumption a cross-sectional test needs (repeated sessions from
one subject are correlated). So this script always picks exactly **one**
session per subject first (`--session`, default `earliest`) — N = subjects,
not N = sessions. Using every session instead would be a longitudinal/mixed-
effects design, which this script deliberately does not do.

**Modes** (which variable is tested, and what it's adjusted for) — default is
all four built-ins, restrict with `--modes`:

| Mode | Tests | Adjusts for |
|---|---|---|
| `age_median` | 2-group split on `age_open` (median) | sex, site |
| `age_tertile` | 3-group split on `age_open` (tertiles) | sex, site |
| `age_continuous` | `age_open` linearly | sex, site |
| `sex` | sex (M/F) | age_open, site |
| `custom` (`--group-col COLUMN`) | any other demographics column | the other two of age/sex/site |

Per region: `value ~ group + covariates` (OLS; all regions of a measure
solved in one `lstsq` call). A 2-level (or continuous) group gives one
coefficient → two-sided t-test. A >2-level group (`age_tertile`, or a custom
column with >2 categories) gives multiple dummy columns → F-test vs. the
model with those columns dropped. P-values are FDR-corrected
(Benjamini-Hochberg) **within each (mode, measure) pair separately** — 400
cortical regions and ~6 midbrain/basal-forebrain regions are different
testing families and shouldn't share one correction. A region is dropped
from a measure if present for <90% of that mode's subjects.

**Standard commands:**
```bash
python3 group_analysis_cross_sectional.py /path/to/Raw_Data
python3 group_analysis_cross_sectional.py /path/to/Raw_Data --modes sex
python3 group_analysis_cross_sectional.py /path/to/Raw_Data --modes custom --group-col race
python3 group_analysis_cross_sectional.py /path/to/Raw_Data --session ses-V1
```
- `raw_root` — same raw-data root every other script uses; also where
  `AABC2_subjects_*.csv` is auto-discovered (newest match), or pass one
  explicitly with `--demographics`.
- `--modes` — comma-separated subset of `age_median,age_tertile,age_continuous,sex,custom`.
- `--session` — `earliest` (default), `latest`, or a literal session name (e.g. `ses-V1`).
- `--measures` — `all` (default), `cortical` (thickness+myelin), or `subcortical` (wmparc+midbrain/BF).
- `--alpha` — FDR q-value threshold (default 0.05).
- `--subjects` — comma-separated subject folder names to restrict to.

**Output** — `Analysed_data/group_analysis/<mode>/`:
- `<measure>_results.csv` — `region, n, df, stat_type, stat, beta, p, p_fdr, significant`
- `<measure>_manhattan.png` — `-log10(FDR p)` per region, dashed line at alpha
- `manifest.txt` — demographics file used, subjects included/excluded and why, covariates, per-measure region/significant counts

**Requires:** `scipy` (t/F-distribution p-values) — the one dependency this
script needs beyond the rest of the folder; see `requirements.txt`.

**Note on eTIV / head-size normalization:** same caveat as
`cross_sectional_analysis_v2.py` — no `aseg.stats` exists anywhere in this
dataset, so subcortical/midbrain volumes here are raw mm³. Age/sex group
differences in raw volume can be confounded by head size, and there's no
eTIV available in this dataset to correct for it.

---

### Region grouping — two passes per mode (2026-07-29)

`--groups` (default `lr`) makes the script run **every mode twice**: once with
one region per parcel, once over composite regions, into separate folders.

```
Analysed_data/group_analysis/<mode>/           one region per parcel
Analysed_data/group_analysis/<mode>_combined/  same modes over composite regions
                                                (+ <measure>_region_groups.json)
```

Each pass is FDR-corrected over **its own** region count — that is precisely
what combining buys you (fewer comparisons), and it means a `p_fdr` from
`<mode>/` is **not** directly comparable with one from `<mode>_combined/`. The
log labels each pass ("one region per parcel" / "composite regions") and the
mode manifest records the grouping and the rule used.

Combining here happens on the way into the model — the per-subject CSVs stay the
source of truth and nothing is re-extracted. Volumes sum; the cortical measures
use an **unweighted** mean, because the per-subject CSVs carry no vertex counts
(the per-session scripts in Section 4 do weight them). That difference is
recorded in the manifest.

`--groups none` restores the previous single-pass behaviour.
