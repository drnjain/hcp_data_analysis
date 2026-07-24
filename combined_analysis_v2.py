#!/usr/bin/env python3
"""
Interactive combined pipeline: cross_sectional_analysis_v2.py (anatomical)
+ run_fc_pipeline_v2.py (functional connectivity, HCPex-only) for one
subject/session, picked once.

Both source scripts prompt for the raw data root and a subject/session
independently -- running them back to back means picking the same
subject/session twice and validating atlas files twice. This script imports
both as modules and reuses their extraction/analysis/plotting/logging
functions unchanged: one subject/session picker (tile counts combine both
scripts' analysis logs), one set of required-file/atlas checks upfront, then
both analyses run in sequence into their normal output locations.

Part 1 -- cross-sectional anatomical analysis (v2): cortical thickness +
myelin (Schaefer-400), wmparc regional volumes, and VTA/SN/Nucleus Basalis
volumes via HCPex (standard + native space). See cross_sectional_analysis_v2.py.

Part 2 -- functional connectivity pipeline (v2, HCPex-only): all 426 HCPex
parcels, five fixed analyses (standard, graph_vta, graph_sn, triangle_vta,
triangle_sn). See run_fc_pipeline_v2.py.

Each part writes to its own normal output location and records into its own
normal run-count log (analysis_log_anat_v2.json / analysis_log_v3.json) --
NOT a third combined log -- so a session analysed here shows up
already-analysed if you later open either single-purpose interactive script
on it.

Standard command:
  python3 combined_analysis_v2.py

Output:
  Analysed_data/<subject>/<session>/anat/  (see cross_sectional_analysis_v2.py)
  Analysed_data/<subject>/<session>/func/  (see run_fc_pipeline_v2.py)

Requires:
  Union of both scripts' requirements: schaefer400_tianS1.dlabel.nii,
  HCPex_2mm.nii, HCPex_LookUpTable.txt under <raw root>/atlases/,
  FreeSurferColorLUT.txt, and FSL's applywarp on PATH.
"""
import sys
import tempfile

import cross_sectional_analysis_v2 as csa
import run_fc_pipeline_v2 as fcp


def combined_subject_total(anat_log, fc_log, subject_name):
    return csa.subject_total(anat_log, subject_name) + fcp.subject_total(fc_log, subject_name)


def combined_session_count(anat_log, fc_log, subject_name, session_name):
    return (anat_log.get(subject_name, {}).get(session_name, 0)
            + fc_log.get(subject_name, {}).get(session_name, 0))


def main():
    raw_root = csa.find_data_root()
    analysed_root = raw_root.parent / "Analysed_data"
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

    atlases_dir = csa.find_atlases_dir(raw_root)  # requires all 3 files -- covers both analyses
    atlas_path = atlases_dir / "schaefer400_tianS1.dlabel.nii"
    hcpex_path = atlases_dir / "HCPex_2mm.nii"
    hcpex_lut_path = atlases_dir / "HCPex_LookUpTable.txt"
    lut_path = csa.find_freesurfer_lut()
    fs_lut = csa.load_freesurfer_lut(lut_path)
    hcpex_lut = csa.load_hcpex_lut(hcpex_lut_path)
    applywarp_bin = csa.find_applywarp()

    # ================= PART 1: cross-sectional anatomical analysis (v2) =================
    print(f"\n== [1/2] Cross-sectional anatomical analysis (v2) for {subject.name}/{session.name} ==")
    anat_out_dir = analysed_root / subject.name / session.name / "anat"
    anat_out_dir.mkdir(parents=True, exist_ok=True)

    vertex_lut, label_names = csa.build_cortex_vertex_lut(atlas_path)

    print("  Parcellating cortical thickness (Schaefer-400)...")
    thickness_rows = csa.parcellate_cortical_dscalar(thickness_path, vertex_lut, label_names)
    csa.save_named_csv(anat_out_dir / "cortical_thickness_schaefer400.csv", ["region", "thickness_mm"], thickness_rows)

    print("  Parcellating myelin map / T1w-T2w ratio (Schaefer-400)...")
    myelin_rows = csa.parcellate_cortical_dscalar(myelin_path, vertex_lut, label_names)
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
    csa.write_manifest(anat_out_dir, subject.name, session.name, thickness_path, myelin_path,
                        wmparc_path, atlas_path, lut_path, voxel_vol_mm3,
                        hcpex_path, warp_field_path, ref_native_path, native_voxel_vol)

    csa.record_analysis(analysed_root, subject.name, session.name)
    print(f"  Saved to {anat_out_dir}")

    # ================= PART 2: functional connectivity pipeline (v2, HCPex-only) =================
    print(f"\n== [2/2] Functional connectivity pipeline (v2, HCPex-only) for {subject.name}/{session.name} ==")
    subject_dir = analysed_root / subject.name
    func_out_dir = subject_dir / session.name / "func"
    func_out_dir.mkdir(parents=True, exist_ok=True)

    ts_path = fcp.timeseries_cache_path(subject_dir, session.name)
    all_ts, all_names = fcp.load_or_extract_all_sources(ts_path, vol_bold, hcpex_path, hcpex_lut_path)
    print(f"  {len(all_names)} parcels available in total")

    print("\n  -- Standard FC matrix -- pick parcels --")
    standard_selection = fcp.choose_parcels(all_names)

    triangle_ts, triangle_names = fcp.build_triangle_ts(all_ts, all_names)

    print(f"\n  Computing all five analyses for {subject.name}/{session.name}...")
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

    fcp.record_analysis(analysed_root, subject.name, session.name)
    print(f"  Saved to {func_out_dir}")

    print(f"\nDone.")
    print(f"  Anatomical output:  {anat_out_dir}")
    print(f"  Functional output:  {func_out_dir}")


if __name__ == "__main__":
    main()
