#!/usr/bin/env python3
"""
Batch (non-interactive) driver for cross_sectional_analysis_v2.py.

Runs the same per-session anatomical analysis -- cortical thickness/myelin
(Schaefer-400), wmparc subcortical volumes, and HCPex midbrain/basal-forebrain
volumes (standard + native space) -- over every sub-*/ses-* session found
under a raw data root, instead of prompting interactively for one at a time.

Every extraction/plotting/logging function is imported unchanged from
cross_sectional_analysis_v2.py -- only the interactive subject/session picker
and single-session main() are replaced with a loop, per-session error
isolation (one bad session doesn't abort the batch), and a skip-if-already-
done check. Work that is identical for every subject (FreeSurfer/HCPex LUTs,
the cortex vertex LUT, and the HCPex standard-space volumes -- fixed by
construction, see cross_sectional_analysis_v2.py's own docstring) is computed
once up front rather than per session.

Usage:
  python3 cross_sectional_analysis_batch_v2.py [raw_data_root] [--force]
      [--subjects sub-A,sub-B]

  raw_data_root   path containing sub-*/ses-*/... . If omitted, prompts
                   interactively with the same tab-completion as the
                   single-session script.
  --force          re-run sessions that already have anat/manifest.txt
                   (default: skip them)
  --subjects       comma-separated subject folder names to restrict to
                   (default: every sub-* found under the root)

Output: identical to the interactive script, per session --
  Analysed_data/<subject>/<session>/anat/{cortical_thickness_schaefer400.csv,
  myelin_schaefer400.csv, subcortical_volumes_wmparc.csv,
  midbrain_basalforebrain_volumes_hcpex.csv, *.png, manifest.txt}
  Shared run-count log: Analysed_data/analysis_log_anat_v2.json (same file
  and schema the interactive script writes to).
"""
import argparse
import sys
import tempfile
import traceback
from pathlib import Path

import cross_sectional_analysis_v2 as csa
import re
import region_grouping


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


def required_files(session):
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


def process_session(subject, session, analysed_root, shared, force, grouping_spec=None):
    out_dir = analysed_root / subject.name / session.name / "anat"
    if (out_dir / "manifest.txt").exists() and not force:
        return "skipped", None

    paths, missing = required_files(session)
    if missing:
        return "missing", missing

    out_dir.mkdir(parents=True, exist_ok=True)

    thickness_rows, thickness_counts = csa.parcellate_cortical_dscalar(
        paths["thickness_path"], shared["vertex_lut"], shared["label_names"], return_counts=True)
    csa.save_named_csv(out_dir / "cortical_thickness_schaefer400.csv",
                        ["region", "thickness_mm"], thickness_rows)

    myelin_rows, myelin_counts = csa.parcellate_cortical_dscalar(
        paths["myelin_path"], shared["vertex_lut"], shared["label_names"], return_counts=True)
    csa.save_named_csv(out_dir / "myelin_schaefer400.csv", ["region", "myelin_ratio"], myelin_rows)

    volume_rows, voxel_vol_mm3 = csa.extract_subcortical_volumes(paths["wmparc_path"], shared["fs_lut"])
    csa.save_volume_csv(out_dir / "subcortical_volumes_wmparc.csv", volume_rows)

    with tempfile.TemporaryDirectory() as tmp_dir:
        native_hcpex_path = csa.warp_hcpex_to_native(
            shared["applywarp_bin"], shared["hcpex_path"], paths["warp_field_path"],
            paths["ref_native_path"], tmp_dir)
        native_rows, native_voxel_vol = csa.extract_native_space_hcpex_volumes(
            native_hcpex_path, shared["name_to_id"])
    csa.save_midbrain_bf_csv(out_dir / "midbrain_basalforebrain_volumes_hcpex.csv",
                              shared["name_to_id"], shared["standard_rows"], native_rows)

    csa.plot_key_volumes(volume_rows, out_dir, subject.name, session.name)
    csa.plot_midbrain_bf_volumes(shared["name_to_id"], shared["standard_rows"], native_rows,
                                  out_dir, subject.name, session.name)
    csa.plot_thickness_myelin_summary(thickness_rows, myelin_rows, out_dir, subject.name, session.name)
    csa.write_manifest(out_dir, subject.name, session.name, paths["thickness_path"], paths["myelin_path"],
                        paths["wmparc_path"], shared["atlas_path"], shared["lut_path"], voxel_vol_mm3,
                        shared["hcpex_path"], paths["warp_field_path"], paths["ref_native_path"],
                        native_voxel_vol)

    csa.save_hemisphere_measures(
        out_dir, thickness_rows, myelin_rows, volume_rows,
        shared["name_to_id"], shared["standard_rows"], native_rows)
    csa.save_combined_measures(
        out_dir, region_grouping.DEFAULT_SPEC, subject.name, session.name,
        thickness_rows, thickness_counts, myelin_rows, myelin_counts,
        volume_rows, shared["name_to_id"], shared["standard_rows"], native_rows)

    # --groups adds a FURTHER grouping beside the four fixed views. Given its own
    # tag so it cannot overwrite the left/right-combined view above.
    if grouping_spec is not None and not region_grouping.is_plain_lr(
            region_grouping.for_names(grouping_spec, [n for n, _v in thickness_rows])):
        tag = re.sub(r"\W+", "_", str(grouping_spec))[:24].strip("_") or "custom"
        csa.save_combined_measures(
            out_dir, grouping_spec, subject.name, session.name,
            thickness_rows, thickness_counts, myelin_rows, myelin_counts,
            volume_rows, shared["name_to_id"], shared["standard_rows"], native_rows,
            tag=f"{tag}_combined")

    csa.record_analysis(analysed_root, subject.name, session.name)
    return "done", None


def main():
    ap = argparse.ArgumentParser(description="Batch-run cross_sectional_analysis_v2.py over every session.")
    ap.add_argument("raw_root", nargs="?", help="path to raw data root (contains sub-*/ses-*/...)")
    ap.add_argument("--force", action="store_true", help="re-run sessions that already have anat/manifest.txt")
    ap.add_argument("--subjects", help="comma-separated subject folder names to restrict to")
    ap.add_argument("--groups", default=region_grouping.DEFAULT_SPEC,
                     help="ALSO write composite-region copies of every measure beside the per-parcel output (*_combined.csv, default: lr; 'none' to skip): "
                          + region_grouping.BUILTIN_HELP)
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

    atlases_dir = csa.find_atlases_dir(raw_root)
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

    shared = {
        "atlas_path": atlas_path, "hcpex_path": hcpex_path, "lut_path": lut_path,
        "fs_lut": fs_lut, "applywarp_bin": applywarp_bin,
        "vertex_lut": vertex_lut, "label_names": label_names,
        "name_to_id": name_to_id, "standard_rows": standard_rows,
    }

    print(f"Found {len(pairs)} session(s) under {raw_root}\n")

    results = {"done": [], "skipped": [], "missing": [], "failed": []}
    for subject, session in pairs:
        label = f"{subject.name}/{session.name}"
        try:
            status, detail = process_session(subject, session, analysed_root, shared, args.force,
                                              args.groups)
        except Exception as exc:
            print(f"[FAILED]  {label} -- {exc}")
            traceback.print_exc()
            results["failed"].append((label, str(exc)))
            continue

        if status == "done":
            print(f"[done]    {label}")
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
