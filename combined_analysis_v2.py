#!/usr/bin/env python3
"""
Interactive combined pipeline: organize_hcp_data.py (data organization) +
cross_sectional_analysis_v2.py (anatomical) + run_fc_pipeline_v2.py
(functional connectivity, HCPex-only) for one subject/session, picked once.

The two analysis scripts prompt for the raw data root and a subject/session
independently -- running them back to back means picking the same
subject/session twice and validating atlas files twice. This script imports
all three as modules and reuses their extraction/analysis/plotting/logging
functions unchanged: a data-organization step first (same input/output
prompts as organize_hcp_data.py -- see Part 0 below -- so freshly downloaded
zip packages don't need a separate manual run before analysis), one
subject/session picker (tile counts combine both analysis scripts' logs),
one set of required-file/atlas checks upfront, then both analyses run in
sequence into their normal output locations.

Part 0 -- data organization (organize_hcp_data.py): same input/output shape
as the standalone script's required --input/--output -- prompts for the
zip-package folder (input) and the raw data root to organize into (output),
in that order. The output path does not need to already exist (created via
mkdir if missing, same as organize_hcp_data.py) and becomes raw_root for
Parts 1-2 below -- no separate "raw data root" prompt. Runs on every
invocation (matching organize_hcp_data.py's required arguments -- not
skippable), but unlike the standalone script it does NOT abort if the input
folder happens to have zero new zip files right now; it just notes that and
continues into the anat/func analysis using the given output as raw_root,
since a routine re-analysis run with no new data to organize shouldn't be
blocked. Same behavior as organize_hcp_data.py otherwise, including moving
processed zips into <raw root>/archive/ and appending to
<raw root>/manifest.csv -- see organize_hcp_data.py.

Part 1 -- cross-sectional anatomical analysis (v2): cortical thickness +
myelin (Schaefer-400), wmparc regional volumes, and VTA/SN/Nucleus Basalis
volumes via HCPex (standard + native space). See cross_sectional_analysis_v2.py.

Part 2 -- functional connectivity pipeline (v2, HCPex-only): all 426 HCPex
parcels, five fixed analyses (standard, graph_vta, graph_sn, triangle_vta,
triangle_sn). See run_fc_pipeline_v2.py.

Each analysis part writes to its own normal output location and records into
its own normal run-count log (analysis_log_anat_v2.json / analysis_log_v3.json)
-- NOT a third combined log -- so a session analysed here shows up
already-analysed if you later open either single-purpose interactive script
on it.

Standard command:
  python3 combined_analysis_v2.py

Output:
  <raw root>/sub-*/ses-*/...        (from the organize step -- pre-existing
  <raw root>/archive/, manifest.csv  sessions untouched, new zips extracted)
  Analysed_data/<subject>/<session>/anat/  (see cross_sectional_analysis_v2.py)
  Analysed_data/<subject>/<session>/func/  (see run_fc_pipeline_v2.py)

Requires:
  Union of all three scripts' requirements: schaefer400_tianS1.dlabel.nii,
  HCPex_2mm.nii, HCPex_LookUpTable.txt under <raw root>/atlases/,
  FreeSurferColorLUT.txt, and FSL's applywarp on PATH.
"""
import sys
import tempfile
import traceback
from pathlib import Path

import cross_sectional_analysis_v2 as csa
import project_paths
import region_grouping
import organize_hcp_data as ohd
import run_fc_pipeline_v2 as fcp


def combined_subject_total(anat_log, fc_log, subject_name):
    return csa.subject_total(anat_log, subject_name) + fcp.subject_total(fc_log, subject_name)


def combined_session_count(anat_log, fc_log, subject_name, session_name):
    return (anat_log.get(subject_name, {}).get(session_name, 0)
            + fc_log.get(subject_name, {}).get(session_name, 0))


def prompt_organize_input_dir():
    """Same semantics as organize_hcp_data.py's required --input: a folder
    of HCP zip packages. Must already exist."""
    csa.enable_path_completion()
    try:
        while True:
            raw = input("Input: path to folder of HCP zip packages to organize (Tab to autocomplete): ").strip()
            if not raw:
                print("  Nothing entered, try again.")
                continue
            path = Path(raw).expanduser()
            if path.is_dir():
                return path
            print(f"  '{path}' is not a directory, try again.")
    finally:
        csa.disable_completion()


def prompt_organize_output_dir():
    """Same semantics as organize_hcp_data.py's required --output: the raw
    data root to organize into (sub-*/ses-*/...). Does not need to already
    exist -- organize_hcp_data.py itself creates it via mkdir if missing --
    and this becomes raw_root for the rest of the pipeline."""
    csa.enable_path_completion()
    try:
        while True:
            raw = input(
                "Output: path to raw data root to organize into "
                "(contains or will contain sub-*/ses-*/..., Tab to autocomplete): "
            ).strip()
            if not raw:
                print("  Nothing entered, try again.")
                continue
            return Path(raw).expanduser()
    finally:
        csa.disable_completion()


def run_organize_step(zip_input_dir, raw_root):
    """Reuses organize_hcp_data.py's own per-zip extraction/logging logic
    unchanged (process_zip, RunLog) -- same behavior as running
    organize_hcp_data.py directly with --input=zip_input_dir --output=raw_root.

    Since 2026-08-19 process_zip() isolates per-zip failures itself: a corrupt,
    truncated or unreadable zip is classified, recorded and skipped rather than
    raised, so the combined run always reaches the subject/session picker and
    the anat/func analysis. The outer try/except is kept as a backstop for
    anything process_zip() does not classify. A failure happens during
    extract_zip()'s staging-directory extraction, before merge_tree() ever
    touches the real target folder, so the zip is safe to retry on a later run
    -- nothing partial is left in <raw root>/sub-*/ses-*/.

    Records go to <raw root>/manifest.csv (8-column, unchanged) and
    <raw root>/run_log.json (full detail), flushed after every zip."""
    archive_dir = raw_root / "archive"
    # "._*" are macOS AppleDouble sidecars (exFAT/network volumes), not packages.
    zip_files = sorted(p for p in zip_input_dir.glob("*.zip")
                       if not p.name.startswith("._"))
    if not zip_files:
        print(f"  No .zip files found in {zip_input_dir} -- nothing to organize.")
        return

    log = ohd.RunLog(raw_root, want_excel=False)
    conflicts = []
    try:
        for zip_path in zip_files:
            try:
                ohd.process_zip(zip_path, raw_root, archive_dir, log, conflicts,
                                dry_run=False, verify_md5=False)
            except Exception as exc:
                print(f"  [ERROR] {zip_path.name} -- {exc}")
                traceback.print_exc()
                parsed = ohd.parse_zip_filename(zip_path.name)
                subject, visit, modality_raw, target_folder = parsed if parsed else ("", "", "", "")
                log.add(subject=subject, visit=visit, modality=modality_raw,
                        target_folder=target_folder or "", zip_filename=zip_path.name,
                        zip_path=str(zip_path), status="error_exception",
                        target_path="", stage="process_zip",
                        error_type=ohd.error_type_name(exc), error_message=str(exc))
    finally:
        log.close()

    manifest_rows = log.records
    manifest_path = log.manifest_path

    if conflicts:
        conflicts_path = raw_root / "conflicts.log"
        with open(conflicts_path, "a") as f:
            for c in conflicts:
                f.write(c + "\n")
        print(f"  [WARNING] {len(conflicts)} file conflicts logged to {conflicts_path}")

    extracted = sum(1 for r in manifest_rows if r["status"] == "extracted")
    skipped = sum(1 for r in manifest_rows if r["status"].startswith("skipped"))
    errors = sum(
        1 for r in manifest_rows
        if r["status"].startswith("error") or r["status"] in ("unrecognized_filename", "unknown_modality")
    )
    print(f"  extracted={extracted} skipped={skipped} errors={errors}")
    print(f"  Manifest written to {manifest_path}")
    print(f"  Full record written to {log.json_path}")

    failures = [r for r in manifest_rows
                if r["status"].startswith("error")
                or r["status"] in ("unrecognized_filename", "unknown_modality")]
    if failures:
        print(f"  {len(failures)} file(s) need attention:")
        for r in failures:
            print(f"    - {r['zip_filename']}: {r['status']}"
                  f" ({r.get('error_message') or r['status']})")


def main():
    print("== [1/3] Data organization (organize_hcp_data.py) ==")
    zip_input_dir = prompt_organize_input_dir()
    raw_root = prompt_organize_output_dir()
    raw_root.mkdir(parents=True, exist_ok=True)

    print(f"  Organizing zip packages from {zip_input_dir} into {raw_root}...")
    run_organize_step(zip_input_dir, raw_root)

    # Part 0 already asked where the organized data should land, so the raw root
    # is settled -- resolve() takes it as given and only settles the other two,
    # saving all three so the standalone scripts see the same locations.
    raw_root, analysed_root, atlases_dir = project_paths.resolve(
        project_paths.ANAT_ATLAS_FILES, raw_root=raw_root)
    anat_log = csa.load_analysis_log(analysed_root)
    fc_log = fcp.load_analysis_log(analysed_root)

    subjects = sorted(p for p in raw_root.glob("sub-*") if p.is_dir())
    if not subjects:
        sys.exit(f"No sub-* folders found under {raw_root}")
    subject_counts = [combined_subject_total(anat_log, fc_log, p.name) for p in subjects]
    subject = csa.choose_tile(subjects, subject_counts, "Subjects found:", formatter=lambda p: p.name)

    sessions = sorted(p for p in subject.glob("ses-*") if p.is_dir())
    if not sessions:
        sys.exit(f"No ses-* folders found under {subject}")
    if len(sessions) == 1:
        session = sessions[0]
        print(f"\nOnly one session found: {session.name} — using it.")
    else:
        session_counts = [combined_session_count(anat_log, fc_log, subject.name, p.name) for p in sessions]
        session = csa.choose_tile(sessions, session_counts, "Sessions found:", formatter=lambda p: p.name)

    # ---- validate every file both analyses need, upfront ----
    fsavg_dir = session / csa.FSAVG32K_REL
    wmparc_path = session / csa.WMPARC_REL
    ref_native_path = session / csa.T1W_ACPC_REL
    warp_field_path = session / csa.WARP_STD2ACPC_REL
    thickness_matches = sorted(fsavg_dir.glob("*.corrThickness_MSMAll.32k_fs_LR.dscalar.nii"))
    myelin_matches = sorted(fsavg_dir.glob("*.MyelinMap_BC_MSMAll.32k_fs_LR.dscalar.nii"))
    vol_bold = session / fcp.VOL_BOLD_REL

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
    if not vol_bold.exists():
        missing.append(fcp.VOL_BOLD_REL)
    if missing:
        sys.exit(f"Missing expected outputs under {session}\n  need:\n    " + "\n    ".join(missing))

    thickness_path = thickness_matches[0]
    myelin_path = myelin_matches[0]

    atlas_path = atlases_dir / "schaefer400_tianS1.dlabel.nii"
    hcpex_path = atlases_dir / "HCPex_2mm.nii"
    hcpex_lut_path = atlases_dir / "HCPex_LookUpTable.txt"
    lut_path = csa.find_freesurfer_lut()
    fs_lut = csa.load_freesurfer_lut(lut_path)
    hcpex_lut = csa.load_hcpex_lut(hcpex_lut_path)
    applywarp_bin = csa.find_applywarp()

    # ================= PART 1: cross-sectional anatomical analysis (v2) =================
    print(f"\n== [2/3] Cross-sectional anatomical analysis (v2) for {subject.name}/{session.name} ==")
    anat_out_dir = analysed_root / subject.name / session.name / "anat"
    anat_out_dir.mkdir(parents=True, exist_ok=True)

    vertex_lut, label_names = csa.build_cortex_vertex_lut(atlas_path)

    print("  Parcellating cortical thickness (Schaefer-400)...")
    thickness_rows, thickness_counts = csa.parcellate_cortical_dscalar(
        thickness_path, vertex_lut, label_names, return_counts=True)
    csa.save_named_csv(anat_out_dir / "cortical_thickness_schaefer400.csv", ["region", "thickness_mm"], thickness_rows)

    print("  Parcellating myelin map / T1w-T2w ratio (Schaefer-400)...")
    myelin_rows, myelin_counts = csa.parcellate_cortical_dscalar(
        myelin_path, vertex_lut, label_names, return_counts=True)
    csa.save_named_csv(anat_out_dir / "myelin_schaefer400.csv", ["region", "myelin_ratio"], myelin_rows)

    print("  Computing subcortical/regional volumes (wmparc)...")
    volume_rows, voxel_vol_mm3 = csa.extract_subcortical_volumes(wmparc_path, fs_lut)
    csa.save_volume_csv(anat_out_dir / "subcortical_volumes_wmparc.csv", volume_rows)

    print("  Computing midbrain/basal-forebrain volumes (VTA, SN, NbM -- HCPex)...")
    name_to_id = csa.midbrain_bf_label_ids(hcpex_lut)
    standard_rows, _std_voxel_vol = csa.extract_standard_space_hcpex_volumes(hcpex_path, name_to_id)
    with tempfile.TemporaryDirectory() as tmp_dir:
        native_hcpex_path = csa.warp_hcpex_to_native(applywarp_bin, hcpex_path, warp_field_path,
                                                       ref_native_path, tmp_dir)
        native_rows, native_voxel_vol = csa.extract_native_space_hcpex_volumes(native_hcpex_path, name_to_id)
    csa.save_midbrain_bf_csv(anat_out_dir / "midbrain_basalforebrain_volumes_hcpex.csv",
                              name_to_id, standard_rows, native_rows)

    csa.plot_key_volumes(volume_rows, anat_out_dir, subject.name, session.name)
    csa.plot_midbrain_bf_volumes(name_to_id, standard_rows, native_rows, anat_out_dir, subject.name, session.name)
    csa.plot_thickness_myelin_summary(thickness_rows, myelin_rows, anat_out_dir, subject.name, session.name)
    hemi_files = csa.save_hemisphere_measures(
        anat_out_dir, thickness_rows, myelin_rows, volume_rows,
        name_to_id, standard_rows, native_rows)
    print(f"  Hemisphere copies written: {len(hemi_files)} file(s)")
    _files, summaries = csa.save_combined_measures(
        anat_out_dir, region_grouping.DEFAULT_SPEC, subject.name, session.name,
        thickness_rows, thickness_counts, myelin_rows, myelin_counts,
        volume_rows, name_to_id, standard_rows, native_rows)
    print("  Combined-region copies written:")
    for line in summaries:
        print(f"    {line}")

    csa.write_manifest(anat_out_dir, subject.name, session.name, thickness_path, myelin_path,
                        wmparc_path, atlas_path, lut_path, voxel_vol_mm3,
                        hcpex_path, warp_field_path, ref_native_path, native_voxel_vol)

    csa.record_analysis(analysed_root, subject.name, session.name)
    print(f"  Saved to {anat_out_dir}")

    # ================= PART 2: functional connectivity pipeline (v2, HCPex-only) =================
    print(f"\n== [3/3] Functional connectivity pipeline (v2, HCPex-only) for {subject.name}/{session.name} ==")
    subject_dir = analysed_root / subject.name
    func_out_dir = subject_dir / session.name / "func"
    func_out_dir.mkdir(parents=True, exist_ok=True)

    ts_path = fcp.timeseries_cache_path(subject_dir, session.name)
    all_ts, all_names = fcp.load_or_extract_all_sources(ts_path, vol_bold, hcpex_path, hcpex_lut_path)
    print(f"  {len(all_names)} parcels available in total")

    print("\n  -- Standard FC matrix -- pick parcels --")
    standard_selection = fcp.choose_parcels(all_names)

    # No grouping question: anatomical and functional results are both written
    # as the same four fixed views (left / right / all / L+R combined).


    print(f"\n  Computing all five analyses for {subject.name}/{session.name}...")
    fcp.run_standard_maps(all_ts, all_names, standard_selection, func_out_dir, "standard_hcpex",
                          subject.name, session.name)
    fcp.run_fixed_analyses(all_ts, all_names, standard_selection, func_out_dir,
                           subject.name, session.name)

    fcp.record_analysis(analysed_root, subject.name, session.name)
    print(f"  Saved to {func_out_dir}")

    print(f"\nDone.")
    print(f"  Anatomical output:  {anat_out_dir}")
    print(f"  Functional output:  {func_out_dir}")


if __name__ == "__main__":
    main()
