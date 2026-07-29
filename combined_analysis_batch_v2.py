#!/usr/bin/env python3
"""
Batch (non-interactive) driver for combined_analysis_v2.py (anatomical +
functional connectivity, both v2/HCPex-only).

Runs both parts -- cross-sectional anatomical analysis (cortical
thickness/myelin, wmparc volumes, HCPex midbrain/basal-forebrain volumes)
and the FC pipeline (all five HCPex analyses) -- over every sub-*/ses-*
session found under a raw data root, instead of prompting interactively for
one subject/session/parcel-selection at a time.

Every extraction/analysis/plotting/logging function is imported unchanged
from cross_sectional_analysis_v2.py and run_fc_pipeline_v2.py -- only the
interactive subject/session/parcel pickers and single-session main() are
replaced with a loop, per-session error isolation, and a skip-if-already-
done check applied to EACH part independently (a session where only one
part has already been run -- e.g. via the standalone batch drivers -- has
just the missing part filled in, not both redone). Work that's identical
for every subject (FreeSurfer/HCPex LUTs, the cortex vertex LUT, HCPex
standard-space volumes, the parcel selection for the "standard" FC matrix)
is computed once up front rather than per session, same as both standalone
batch drivers.

Each part writes to its own normal output location and records into its own
normal run-count log (analysis_log_anat_v2.json / analysis_log_v3.json) --
NOT a third combined log -- same design choice combined_analysis_v2.py
makes, so a session batch-processed here shows up already-analysed if you
later open either single-purpose script (interactive or batch) on it.

Usage:
  python3 combined_analysis_batch_v2.py [raw_data_root] [--force]
      [--subjects sub-A,sub-B] [--parcels all]

  raw_data_root   path containing sub-*/ses-*/... . If omitted, prompts
                   interactively with the same tab-completion as the
                   single-session scripts.
  --force          re-run BOTH parts for sessions that already have them
                   (default: skip whichever part is already done)
  --subjects       comma-separated subject folder names to restrict to
                   (default: every sub-* found under the root)
  --parcels        which parcels go into the "standard" FC matrix, applied
                   identically to every session in the batch. If omitted,
                   prompts interactively ONCE using the same atlas-group
                   card picker as the single-session scripts. Pass a value
                   to skip the prompt: 'all', a bare group letter ('B'),
                   specific labels/ranges ('A1, B2, B4', 'A1-10')

Output: identical to the interactive script, per session --
  Analysed_data/<subject>/<session>/anat/  (see cross_sectional_analysis_v2.py)
  Analysed_data/<subject>/<session>/func/  (see run_fc_pipeline_v2.py)
  Shared run-count logs: Analysed_data/analysis_log_anat_v2.json and
  analysis_log_v3.json (same files/schemas the interactive scripts write to).

Requires:
  Union of both scripts' requirements: schaefer400_tianS1.dlabel.nii,
  HCPex_2mm.nii, HCPex_LookUpTable.txt under <raw root>/atlases/,
  FreeSurferColorLUT.txt, and FSL's applywarp on PATH -- checked once up
  front for every session that still needs at least one part run.
"""
import argparse
import sys
import tempfile
import traceback
from pathlib import Path

import cross_sectional_analysis_v2 as csa
import region_grouping
import run_fc_pipeline_v2 as fcp


def find_sessions(raw_root, subject_filter):
    subjects = sorted(p for p in raw_root.glob("sub-*") if p.is_dir())
    if subject_filter:
        subjects = [p for p in subjects if p.name in subject_filter]
        missing = subject_filter - {p.name for p in subjects}
        if missing:
            print(f"  (warning: requested subjects not found under {raw_root}: {sorted(missing)})")
    pairs = []
    for subject in subjects:
        for session in sorted(p for p in subject.glob("ses-*") if p.is_dir()):
            pairs.append((subject, session))
    return pairs


def hcpex_all_names(hcpex_lut_path):
    idx_to_name = fcp.load_hcpex_labels(hcpex_lut_path)
    return [idx_to_name[i] for i in sorted(idx_to_name)]


def resolve_parcel_selection(parcels_arg, hcpex_lut_path):
    """Same card-selection parser the interactive scripts use, applied once
    up front since HCPex's 426 parcel names are fixed by the atlas, not
    session-specific."""
    all_names = hcpex_all_names(hcpex_lut_path)
    labeled = fcp._group_labeled_entries(all_names)
    label_to_entry = {lbl: (idx, name) for items in labeled.values() for (lbl, idx, name) in items}

    selected_labels = fcp._parse_card_selection(parcels_arg, label_to_entry, labeled)
    if selected_labels is None:
        sys.exit(f"Could not parse --parcels selection: {parcels_arg!r}")
    if len(selected_labels) < 2:
        sys.exit("--parcels selection must include at least 2 parcels for a correlation matrix")
    return [label_to_entry[lbl][1] for lbl in selected_labels]


def required_anat_files(session):
    fsavg_dir = session / csa.FSAVG32K_REL
    thickness_matches = sorted(fsavg_dir.glob("*.corrThickness_MSMAll.32k_fs_LR.dscalar.nii"))
    myelin_matches = sorted(fsavg_dir.glob("*.MyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii"))
    wmparc_path = session / csa.WMPARC_REL
    ref_native_path = session / csa.T1W_ACPC_REL
    warp_field_path = session / csa.WARP_STD2ACPC_REL

    missing = []
    if not thickness_matches:
        missing.append(f"{csa.FSAVG32K_REL}/*.corrThickness_MSMAll.32k_fs_LR.dscalar.nii")
    if not myelin_matches:
        missing.append(f"{csa.FSAVG32K_REL}/*.MyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii")
    if not wmparc_path.exists():
        missing.append(csa.WMPARC_REL)
    if not ref_native_path.exists():
        missing.append(csa.T1W_ACPC_REL)
    if not warp_field_path.exists():
        missing.append(csa.WARP_STD2ACPC_REL)
    if missing:
        return None, missing

    return {
        "thickness_path": thickness_matches[0],
        "myelin_path": myelin_matches[0],
        "wmparc_path": wmparc_path,
        "ref_native_path": ref_native_path,
        "warp_field_path": warp_field_path,
    }, []


def run_anat(subject, session, analysed_root, anat_paths, shared, grouping=None):
    anat_out_dir = analysed_root / subject.name / session.name / "anat"
    anat_out_dir.mkdir(parents=True, exist_ok=True)

    thickness_rows, thickness_counts = csa.parcellate_cortical_dscalar(
        anat_paths["thickness_path"], shared["vertex_lut"], shared["label_names"], return_counts=True)
    csa.save_named_csv(anat_out_dir / "cortical_thickness_schaefer400.csv",
                        ["region", "thickness_mm"], thickness_rows)

    myelin_rows, myelin_counts = csa.parcellate_cortical_dscalar(
        anat_paths["myelin_path"], shared["vertex_lut"], shared["label_names"], return_counts=True)
    csa.save_named_csv(anat_out_dir / "myelin_schaefer400.csv", ["region", "myelin_ratio"], myelin_rows)

    volume_rows, voxel_vol_mm3 = csa.extract_subcortical_volumes(anat_paths["wmparc_path"], shared["fs_lut"])
    csa.save_volume_csv(anat_out_dir / "subcortical_volumes_wmparc.csv", volume_rows)

    with tempfile.TemporaryDirectory() as tmp_dir:
        native_hcpex_path = csa.warp_hcpex_to_native(
            shared["applywarp_bin"], shared["hcpex_path"], anat_paths["warp_field_path"],
            anat_paths["ref_native_path"], tmp_dir)
        native_rows, native_voxel_vol = csa.extract_native_space_hcpex_volumes(
            native_hcpex_path, shared["name_to_id"])
    csa.save_midbrain_bf_csv(anat_out_dir / "midbrain_basalforebrain_volumes_hcpex.csv",
                              shared["name_to_id"], shared["standard_rows"], native_rows)

    csa.plot_key_volumes(volume_rows, anat_out_dir, subject.name, session.name)
    csa.plot_midbrain_bf_volumes(shared["name_to_id"], shared["standard_rows"], native_rows,
                                  anat_out_dir, subject.name, session.name)
    csa.plot_thickness_myelin_summary(thickness_rows, myelin_rows, anat_out_dir, subject.name, session.name)
    csa.write_manifest(anat_out_dir, subject.name, session.name, anat_paths["thickness_path"], anat_paths["myelin_path"],
                        anat_paths["wmparc_path"], shared["atlas_path"], shared["lut_path"], voxel_vol_mm3,
                        shared["hcpex_path"], anat_paths["warp_field_path"], anat_paths["ref_native_path"],
                        native_voxel_vol)

    if grouping:
        csa.save_combined_measures(
            anat_out_dir, grouping, subject.name, session.name,
            thickness_rows, thickness_counts, myelin_rows, myelin_counts,
            volume_rows, shared["name_to_id"], shared["standard_rows"], native_rows)

    csa.record_analysis(analysed_root, subject.name, session.name)


def run_func(subject, session, analysed_root, vol_bold, shared, standard_selection, grouping=None):
    subject_dir = analysed_root / subject.name
    func_out_dir = subject_dir / session.name / "func"
    func_out_dir.mkdir(parents=True, exist_ok=True)

    ts_path = fcp.timeseries_cache_path(subject_dir, session.name)
    all_ts, all_names = fcp.load_or_extract_all_sources(
        ts_path, vol_bold, shared["hcpex_path"], shared["hcpex_lut_path"])
    triangle_ts, triangle_names = fcp.build_triangle_ts(all_ts, all_names)

    fcp.run_and_save_analysis(all_ts, all_names, standard_selection, func_out_dir, "standard_hcpex",
                               subject.name, session.name, use_graph_plot=False)
    fcp.run_and_save_analysis(all_ts, all_names, fcp.GRAPH_VTA, func_out_dir, "graph_vta_hcpex",
                               subject.name, session.name, use_graph_plot=True)
    fcp.run_and_save_analysis(all_ts, all_names, fcp.GRAPH_SN, func_out_dir, "graph_sn_hcpex",
                               subject.name, session.name, use_graph_plot=True)
    fcp.run_and_save_analysis(triangle_ts, triangle_names, fcp.TRIANGLE_VTA, func_out_dir, "triangle_vta_hcpex",
                               subject.name, session.name, use_graph_plot=True)
    fcp.run_and_save_analysis(triangle_ts, triangle_names, fcp.TRIANGLE_SN, func_out_dir, "triangle_sn_hcpex",
                               subject.name, session.name, use_graph_plot=True)

    if grouping:
        fcp.run_grouped_analysis(all_ts, all_names, standard_selection, grouping, func_out_dir,
                                  "standard_hcpex", subject.name, session.name)

    fcp.record_analysis(analysed_root, subject.name, session.name)


def process_session(subject, session, analysed_root, shared, standard_selection, force,
                    grouping=None):
    anat_out_dir = analysed_root / subject.name / session.name / "anat"
    func_out_dir = analysed_root / subject.name / session.name / "func"

    need_anat = force or not (anat_out_dir / "manifest.txt").exists()
    need_func = force or not (func_out_dir / "fc_matrix_corr_standard_hcpex.csv").exists()

    if not need_anat and not need_func:
        return "skipped", None

    # Union of whichever part(s) still need to run -- all-or-nothing for
    # this session, same as combined_analysis_v2.py's own upfront check.
    missing = []
    anat_paths = None
    if need_anat:
        anat_paths, anat_missing = required_anat_files(session)
        missing += anat_missing
    vol_bold = session / fcp.VOL_BOLD_REL
    if need_func and not vol_bold.exists():
        missing.append(fcp.VOL_BOLD_REL)
    if missing:
        return "missing", missing

    parts = {}
    if need_anat:
        run_anat(subject, session, analysed_root, anat_paths, shared, grouping)
        parts["anat"] = "done"
    else:
        parts["anat"] = "skipped"

    if need_func:
        run_func(subject, session, analysed_root, vol_bold, shared, standard_selection, grouping)
        parts["func"] = "done"
    else:
        parts["func"] = "skipped"

    return "done", parts


def main():
    ap = argparse.ArgumentParser(
        description="Batch-run combined_analysis_v2.py (anat + func) over every session.")
    ap.add_argument("raw_root", nargs="?", help="path to raw data root (contains sub-*/ses-*/...)")
    ap.add_argument("--force", action="store_true",
                     help="re-run both parts for sessions that already have them")
    ap.add_argument("--subjects", help="comma-separated subject folder names to restrict to")
    ap.add_argument("--groups", default=region_grouping.DEFAULT_SPEC,
                     help="ALSO write composite-region copies of the anatomical measures and an "
                          "extra combined FC matrix, beside the per-parcel output "
                          "(default: lr; 'none' to skip): " + region_grouping.BUILTIN_HELP)
    ap.add_argument("--parcels", default=None,
                     help="parcel selection for the 'standard' FC matrix (default: prompt interactively)")
    args = ap.parse_args()

    # Validate --groups before ANY work: a typo should cost a second, not a
    # session's extraction (or a whole batch's).
    try:
        groups_desc = region_grouping.validate_spec(args.groups)
    except ValueError as exc:
        sys.exit(f"--groups: {exc}")

    raw_root = Path(args.raw_root).expanduser() if args.raw_root else csa.find_data_root()
    if not raw_root.is_dir():
        sys.exit(f"'{raw_root}' is not a directory")
    subject_filter = set(s.strip() for s in args.subjects.split(",")) if args.subjects else None

    analysed_root = raw_root.parent / "Analysed_data"
    pairs = find_sessions(raw_root, subject_filter)
    if not pairs:
        sys.exit(f"No sub-*/ses-* sessions found under {raw_root}")

    atlases_dir = csa.find_atlases_dir(raw_root)  # requires all 3 files -- covers both analyses
    atlas_path = atlases_dir / "schaefer400_tianS1.dlabel.nii"
    hcpex_path = atlases_dir / "HCPex_2mm.nii"
    hcpex_lut_path = atlases_dir / "HCPex_LookUpTable.txt"
    lut_path = csa.find_freesurfer_lut()
    fs_lut = csa.load_freesurfer_lut(lut_path)
    hcpex_lut = csa.load_hcpex_lut(hcpex_lut_path)
    applywarp_bin = csa.find_applywarp()

    vertex_lut, label_names = csa.build_cortex_vertex_lut(atlas_path)
    name_to_id = csa.midbrain_bf_label_ids(hcpex_lut)
    standard_rows, _std_voxel_vol = csa.extract_standard_space_hcpex_volumes(hcpex_path, name_to_id)

    print(f"Found {len(pairs)} session(s) under {raw_root}")

    if args.parcels is None:
        print("\n== Standard FC matrix -- pick parcels (applied to every session in this batch) ==")
        standard_selection = fcp.choose_parcels(hcpex_all_names(hcpex_lut_path))
        print(f"\nStandard FC matrix: {len(standard_selection)} parcels (interactive selection)\n")
    else:
        standard_selection = resolve_parcel_selection(args.parcels, hcpex_lut_path)
        print(f"Standard FC matrix: {len(standard_selection)} parcels (--parcels {args.parcels!r})\n")

    shared = {
        "atlas_path": atlas_path, "hcpex_path": hcpex_path, "hcpex_lut_path": hcpex_lut_path,
        "lut_path": lut_path, "fs_lut": fs_lut, "applywarp_bin": applywarp_bin,
        "vertex_lut": vertex_lut, "label_names": label_names,
        "name_to_id": name_to_id, "standard_rows": standard_rows,
    }

    grouping = None
    if args.groups and str(args.groups).strip().lower() != "none":
        try:
            grouping = region_grouping.load(args.groups, standard_selection)
        except ValueError as exc:
            sys.exit(f"--groups: {exc}")
        errors, _warnings = grouping.validate(standard_selection)
        if errors:
            sys.exit("--groups: " + "; ".join(errors))
        print(f"Combining: {grouping.source} -- the FC selection's {len(standard_selection)} parcels "
              f"become {len(grouping.resolve(standard_selection))} region(s); anatomical measures are "
              f"combined in their own name spaces.\n")

    results = {"done": [], "skipped": [], "missing": [], "failed": []}
    for subject, session in pairs:
        label = f"{subject.name}/{session.name}"
        try:
            status, detail = process_session(subject, session, analysed_root, shared, standard_selection,
                                              args.force, grouping)
        except Exception as exc:
            print(f"[FAILED]  {label} -- {exc}")
            traceback.print_exc()
            results["failed"].append((label, str(exc)))
            continue

        if status == "done":
            parts_str = ", ".join(f"{k}: {v}" for k, v in detail.items())
            print(f"[done]    {label}  ({parts_str})")
        elif status == "skipped":
            print(f"[skip]    {label} (already analysed -- use --force to redo)")
        elif status == "missing":
            print(f"[missing] {label} -- missing: {', '.join(detail)}")
        results[status].append(label)

    print(f"\nDone: {len(results['done'])}  Skipped: {len(results['skipped'])}  "
          f"Missing files: {len(results['missing'])}  Failed: {len(results['failed'])}")
    if results["failed"]:
        print("Failed sessions:")
        for label, err in results["failed"]:
            print(f"  {label}: {err}")


if __name__ == "__main__":
    main()
