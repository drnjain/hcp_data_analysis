#!/usr/bin/env python3
"""
Batch (non-interactive) driver for run_fc_pipeline_v2.py (HCPex-only FC
pipeline).

Runs the same five analyses ("standard", "graph_vta", "graph_sn",
"triangle_vta", "triangle_sn") over every sub-*/ses-* session found under a
raw data root, instead of prompting interactively for one subject/session/
parcel-selection at a time.

Every extraction/analysis/plotting/logging function is imported unchanged
from run_fc_pipeline_v2.py -- only the interactive subject/session/parcel
pickers and single-session main() are replaced with a loop, a single
parcel-selection made once up front (applied identically to every session --
the 426 HCPex parcel names are fixed by the atlas, not subject-specific),
per-session error isolation, and a skip-if-already-done check. Per-subject
timeseries caching (Analysed_data/<subject>/timeseries_hcpex_<session>.csv)
works exactly as in the interactive script.

Usage:
  python3 run_fc_pipeline_batch_v2.py [raw_data_root] [--force]
      [--subjects sub-A,sub-B] [--parcels all] [--groups lr]

  (omitting --parcels prompts interactively once, before the batch starts)

  raw_data_root   path containing sub-*/ses-*/... . If omitted, prompts
                   interactively with the same tab-completion as the
                   single-session script.
  --force          re-run sessions that already have a standard FC matrix
                   (default: skip them)
  --subjects       comma-separated subject folder names to restrict to
                   (default: every sub-* found under the root)
  --parcels        which parcels go into the "standard" FC matrix, applied
                   identically to every session in the batch. If omitted,
                   prompts interactively ONCE using the same atlas-group card
                   picker as the single-session script. Pass a value to skip
                   the prompt: 'all', a bare group letter ('B'), specific
                   labels/ranges ('A1, B2, B4', 'A1-10')

Output: identical to the interactive script, per session --
  Analysed_data/<subject>/<session>/func/{fc_matrix_corr_<suffix>.csv,
  fc_matrix_fisherz_<suffix>.csv, fc_matrix_<suffix>.png/.svg,
  region_names_<suffix>.txt} for each of the five suffixes.
  Shared run-count log: Analysed_data/analysis_log_v3.json (same file and
  schema the interactive script writes to).
"""
import argparse
import sys
import traceback
from pathlib import Path

import project_paths
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


def resolve_parcel_selection(parcels_arg, hcpex_labels):
    """Parcel names/order are fixed by the atlas label file, identical for
    every subject/session -- so the selection only needs to be parsed once,
    reusing the interactive script's own card-selection parser for identical
    syntax support ('all', 'B', 'A1, B2, B4', 'A1-10')."""
    all_names = hcpex_all_names(hcpex_labels)
    labeled = fcp._group_labeled_entries(all_names)
    label_to_entry = {lbl: (idx, name) for items in labeled.values() for (lbl, idx, name) in items}

    selected_labels = fcp._parse_card_selection(parcels_arg, label_to_entry, labeled)
    if selected_labels is None:
        sys.exit(f"Could not parse --parcels selection: {parcels_arg!r}")
    if len(selected_labels) < 2:
        sys.exit("--parcels selection must include at least 2 parcels for a correlation matrix")
    return [label_to_entry[lbl][1] for lbl in selected_labels]


def hcpex_all_names(hcpex_labels):
    idx_to_name = fcp.load_hcpex_labels(hcpex_labels)
    return [idx_to_name[i] for i in sorted(idx_to_name)]


def process_session(subject, session, analysed_root, hcpex_vol, hcpex_labels, standard_selection,
                    force, grouping=None):
    vol_bold = session / fcp.VOL_BOLD_REL
    if not vol_bold.exists():
        return "missing", [fcp.VOL_BOLD_REL]

    subject_dir = analysed_root / subject.name
    out_dir = subject_dir / session.name / "func"
    if (out_dir / "fc_matrix_corr_standard_hcpex.csv").exists() and not force:
        return "skipped", None

    out_dir.mkdir(parents=True, exist_ok=True)

    ts_path = fcp.timeseries_cache_path(subject_dir, session.name)
    all_ts, all_names = fcp.load_or_extract_all_sources(ts_path, vol_bold, hcpex_vol, hcpex_labels)
    triangle_ts, triangle_names = fcp.build_triangle_ts(all_ts, all_names)

    fcp.run_standard_maps(all_ts, all_names, standard_selection, out_dir, "standard_hcpex",
                          subject.name, session.name,
                          extra_grouping=None if fcp.is_plain_lr(grouping) else grouping)
    fcp.run_and_save_analysis(all_ts, all_names, fcp.GRAPH_VTA, out_dir, "graph_vta_hcpex",
                               subject.name, session.name, use_graph_plot=True)
    fcp.run_and_save_analysis(all_ts, all_names, fcp.GRAPH_SN, out_dir, "graph_sn_hcpex",
                               subject.name, session.name, use_graph_plot=True)
    fcp.run_and_save_analysis(triangle_ts, triangle_names, fcp.TRIANGLE_VTA, out_dir, "triangle_vta_hcpex",
                               subject.name, session.name, use_graph_plot=True)
    fcp.run_and_save_analysis(triangle_ts, triangle_names, fcp.TRIANGLE_SN, out_dir, "triangle_sn_hcpex",
                               subject.name, session.name, use_graph_plot=True)


    fcp.record_analysis(analysed_root, subject.name, session.name)
    return "done", None


def main():
    ap = argparse.ArgumentParser(description="Batch-run run_fc_pipeline_v2.py over every session.")
    ap.add_argument("raw_root", nargs="?", help="path to raw data root (contains sub-*/ses-*/...)")
    ap.add_argument("--force", action="store_true", help="re-run sessions that already have a standard FC matrix")
    ap.add_argument("--subjects", help="comma-separated subject folder names to restrict to")
    ap.add_argument("--analysed-root", help="where results are written (default: saved path, else <raw root>/../Analysed_data)")
    ap.add_argument("--atlases-dir", help="folder holding the shared atlas files (default: saved path, else <raw root>/atlases)")
    ap.add_argument("--parcels", default=None,
                     help="parcel selection for the 'standard' FC matrix (default: prompt interactively)")
    ap.add_argument("--groups", default=region_grouping.DEFAULT_SPEC,
                     help="ALSO run the standard FC matrix over composite regions, saved beside the "
                          "per-parcel output as 'standard_hcpex_combined' (default: lr, 'none' to skip): "
                          + region_grouping.BUILTIN_HELP)
    args = ap.parse_args()

    # Validate --groups before ANY work: a typo should cost a second, not a
    # session's extraction (or a whole batch's).
    try:
        groups_desc = region_grouping.validate_spec(args.groups)
    except ValueError as exc:
        sys.exit(f"--groups: {exc}")

    raw_root, analysed_root, atlases_dir = project_paths.resolve(
        project_paths.FC_ATLAS_FILES, raw_root=args.raw_root,
        analysed_root=args.analysed_root, atlases_dir=args.atlases_dir)
    subject_filter = set(s.strip() for s in args.subjects.split(",")) if args.subjects else None

    pairs = find_sessions(raw_root, subject_filter)
    if not pairs:
        sys.exit(f"No sub-*/ses-* sessions found under {raw_root}")

    hcpex_vol = atlases_dir / "HCPex_2mm.nii"
    hcpex_labels = atlases_dir / "HCPex_LookUpTable.txt"

    print(f"Found {len(pairs)} session(s) under {raw_root}")

    if args.parcels is None:
        print("\n== Standard FC matrix -- pick parcels (applied to every session in this batch) ==")
        standard_selection = fcp.choose_parcels(hcpex_all_names(hcpex_labels))
        print(f"\nStandard FC matrix: {len(standard_selection)} parcels (interactive selection)\n")
    else:
        standard_selection = resolve_parcel_selection(args.parcels, hcpex_labels)
        print(f"Standard FC matrix: {len(standard_selection)} parcels (--parcels {args.parcels!r})\n")

    grouping = None
    if args.groups:
        try:
            grouping = region_grouping.load(args.groups, standard_selection)
        except ValueError as exc:
            sys.exit(f"--groups: {exc}")
    # load() returns None for '--groups none' -- the flag is a non-empty string
    # either way, so the opt-out has to be detected from the result, not from
    # the raw argument being truthy.
    if grouping is not None:
        errors, warnings = grouping.validate(standard_selection)
        for w in warnings:
            print(f"  [warning] {w}")
        if errors:
            sys.exit("--groups: " + "; ".join(errors))
        print(f"Combining: {grouping.source} -- {len(standard_selection)} parcels become "
              f"{len(grouping.resolve(standard_selection))} region(s), saved as *_standard_hcpex_combined")
        for note in grouping.notes:
            print(f"  NOTE: {note}")
        print()

    results = {"done": [], "skipped": [], "missing": [], "failed": []}
    for subject, session in pairs:
        label = f"{subject.name}/{session.name}"
        try:
            status, detail = process_session(subject, session, analysed_root, hcpex_vol, hcpex_labels,
                                              standard_selection, args.force, grouping)
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
