#!/usr/bin/env python3
"""
Group-level statistics for the cross-sectional anatomical pipeline
(cross_sectional_analysis_v2.py / _batch_v2.py).

Those scripts only extract per-subject/per-session values (cortical
thickness, myelin, wmparc subcortical volumes, HCPex midbrain/basal-forebrain
native-space volumes) into Analysed_data/<subject>/<session>/anat/*.csv --
they do no group comparison at all. This script is the missing statistics
step: it reads those per-session CSVs plus the demographics CSV (AABC2
subjects export, matched by id_event = "<subject>_<visit>", e.g.
"HCA6002236_V1"), builds one region x subject table per measure, and runs a
per-region linear model with FDR correction across regions.

Cross-sectional design note (important -- easy to get wrong): every subject
in this dataset has multiple sessions (V1/V2/V3...). Pooling every session as
an independent row would violate the independence assumption a standard
cross-sectional test needs (repeated sessions from the same subject are
correlated -- pseudoreplication). So this script picks exactly ONE session
per subject (earliest by default -- see --session) before doing any stats,
giving N = subjects, not N = sessions. If you want to use every session, that
is a longitudinal/repeated-measures design (mixed-effects model with subject
as a random effect) -- deliberately NOT what this script does.

Modes (which variable is tested, and what it's adjusted for):
  age_median      2-group split on age_open (median). Adjusts for sex + site.
  age_tertile     3-group split on age_open (tertiles). Adjusts for sex + site.
  age_continuous  age_open used as-is (linear). Adjusts for sex + site.
  sex             sex (M/F). Adjusts for age_open + site.
  custom          any other column via --group-col. Adjusts for age_open +
                  sex + site, minus whichever of those equals --group-col.
Default (no --modes given): age_median, age_tertile, age_continuous, sex.

Per region: fits y ~ group + covariates (OLS, one intercept-augmented design
matrix reused across all regions of a measure at once via a single lstsq
solve). A 2-level group (or a continuous one, e.g. age_continuous) gives one
coefficient -> two-sided t-test. A >2-level group (age_tertile, or a custom
column with >2 categories) gives multiple dummy columns -> F-test comparing
the full model to the model with those columns dropped. Region-wise p-values
are FDR-corrected (Benjamini-Hochberg) within each (mode, measure) pair
separately -- 400 cortical regions and ~6 midbrain/basal-forebrain regions
are different testing families and shouldn't share one correction.

Output, per mode:
  Analysed_data/group_analysis/<mode>/
    <measure>_results.csv   region, n, df, stat_type, stat, beta, p, p_fdr, significant
    <measure>_manhattan.png -log10(p_fdr) per region, dashed line at alpha
    manifest.txt            demographics file used, N included/excluded and
                             why, covariates, alpha, per-measure region counts

Standard commands:
  python3 group_analysis_cross_sectional.py /path/to/Raw_Data
  python3 group_analysis_cross_sectional.py /path/to/Raw_Data --modes sex
  python3 group_analysis_cross_sectional.py /path/to/Raw_Data --modes custom --group-col race
  python3 group_analysis_cross_sectional.py /path/to/Raw_Data --session ses-V1

Requires: numpy, pandas, matplotlib, scipy (t/F p-values -- the one new
dependency beyond what the rest of this folder uses; added to
requirements.txt).

Note on eTIV / head-size normalization: same caveat as cross_sectional_analysis_v2.py
-- no aseg.stats found anywhere in this dataset, so subcortical/midbrain
volumes here are raw mm^3, not head-size-normalized. Age/sex/site group
differences in raw volume can be confounded by head size; there's no eTIV
available in this dataset to correct for it.
"""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats as spstats

import cross_sectional_analysis_v2 as csa
import region_grouping

MEASURES = {
    "cortical_thickness": ("cortical_thickness_schaefer400.csv", "region", "thickness_mm"),
    "myelin": ("myelin_schaefer400.csv", "region", "myelin_ratio"),
    "subcortical_volumes": ("subcortical_volumes_wmparc.csv", "region", "volume_mm3"),
    "midbrain_basalforebrain": ("midbrain_basalforebrain_volumes_hcpex.csv", "region", "native_volume_mm3"),
}
# Aggregation rule per measure when regions are combined (see region_grouping).
# Volumes add; the cortical scalars are means and would need vertex counts to be
# weighted correctly -- the per-subject CSVs do not carry them, so a combined
# cortical measure here is the unweighted mean of its parcels and is reported as
# such.
MEASURE_COMBINE_RULE = {
    "cortical_thickness": "mean",
    "myelin": "mean",
    "subcortical_volumes": "sum",
    "midbrain_basalforebrain": "sum",
}

MEASURE_GROUPS = {
    "all": list(MEASURES),
    "cortical": ["cortical_thickness", "myelin"],
    "subcortical": ["subcortical_volumes", "midbrain_basalforebrain"],
}
BUILTIN_MODES = ["age_median", "age_tertile", "age_continuous", "sex"]
DEMOGRAPHIC_ID_COL = "id_event"
MIN_COVERAGE = 0.9  # a region must be present for >=90% of a mode/measure's subjects, else dropped


# ---------------------------------------------------------------- discovery

def find_demographics(raw_root, explicit):
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            sys.exit(f"--demographics '{path}' is not a file")
        return path
    matches = sorted(raw_root.glob("AABC2_subjects_*.csv"))
    if not matches:
        sys.exit(
            f"No 'AABC2_subjects_*.csv' demographics file found under {raw_root} "
            "-- pass one explicitly with --demographics"
        )
    return matches[-1]  # filenames are timestamped; last sorts newest


def load_demographics(path):
    demo = pd.read_csv(path, dtype=str, low_memory=False)
    if DEMOGRAPHIC_ID_COL not in demo.columns:
        sys.exit(f"'{path}' has no '{DEMOGRAPHIC_ID_COL}' column -- wrong file?")
    demo = demo.set_index(DEMOGRAPHIC_ID_COL)
    demo["age_open"] = pd.to_numeric(demo["age_open"], errors="coerce")
    return demo


def _session_sort_key(name):
    m = re.search(r"(\d+)$", name)
    return (int(m.group(1)) if m else 0, name)


def select_sessions(analysed_root, session_policy, subject_filter):
    """One (subject_dir_name, session_dir_name, anat_dir) per subject."""
    if not analysed_root.is_dir():
        sys.exit(f"'{analysed_root}' does not exist -- run the extraction pipeline first")
    subjects = sorted(p for p in analysed_root.glob("sub-*") if p.is_dir())
    if subject_filter:
        subjects = [p for p in subjects if p.name in subject_filter]

    literal_session = None
    if session_policy not in ("earliest", "latest"):
        literal_session = session_policy

    chosen, skipped_no_session = [], []
    for subject in subjects:
        sessions = sorted(
            (p for p in subject.glob("ses-*") if (p / "anat").is_dir()),
            key=lambda p: _session_sort_key(p.name),
        )
        if not sessions:
            skipped_no_session.append(subject.name)
            continue
        if literal_session:
            match = next((s for s in sessions if s.name == literal_session), None)
            if match is None:
                skipped_no_session.append(subject.name)
                continue
            session = match
        else:
            session = sessions[-1] if session_policy == "latest" else sessions[0]
        chosen.append((subject.name, session.name, session / "anat"))

    if skipped_no_session:
        print(f"  ({len(skipped_no_session)} subject(s) skipped -- no matching session with anat/ output: "
              f"{skipped_no_session[:10]}{' ...' if len(skipped_no_session) > 10 else ''})")
    return chosen


def build_cohort_table(sessions, demo):
    rows, unmatched = [], []
    for subject_name, session_name, anat_dir in sessions:
        event_id = f"{subject_name.removeprefix('sub-')}_{session_name.removeprefix('ses-')}"
        if event_id not in demo.index:
            unmatched.append(event_id)
            continue
        row = demo.loc[event_id]
        rows.append({
            "event_id": event_id, "subject": subject_name, "session": session_name,
            "anat_dir": anat_dir, "age_open": row.get("age_open"),
            "sex": row.get("sex"), "site": row.get("site"),
        })
    if unmatched:
        print(f"  ({len(unmatched)} session(s) skipped -- no demographics row for: "
              f"{unmatched[:10]}{' ...' if len(unmatched) > 10 else ''})")
    cohort = pd.DataFrame(rows).set_index("event_id")
    return cohort


# ---------------------------------------------------------------- measures

def combine_measure_table(wide, measure_key, grouping_spec):
    """Merge the region columns of a subject x region table.

    Done here rather than upstream so the group statistics can be run over
    composite regions without re-extracting anything: the per-parcel CSVs stay
    the source of truth and the combining happens on the way into the model.
    """
    if not grouping_spec or wide.empty:
        return wide, None
    grouping = region_grouping.for_names(grouping_spec, list(wide.columns))
    if not grouping:
        return wide, None
    rule = MEASURE_COMBINE_RULE.get(measure_key, "mean")
    data = {}
    for combined, members in grouping.resolve(list(wide.columns)):
        block = wide[members]
        data[combined] = block.sum(axis=1) if rule == "sum" else block.mean(axis=1)
    out = pd.DataFrame(data, index=wide.index)
    print(f"    [{measure_key}] combined {wide.shape[1]} regions -> {out.shape[1]} "
          f"({grouping.source}, rule={rule})")
    return out, grouping


def build_measure_table(measure_key, cohort_subset):
    """subject(event_id) x region wide table for one measure, restricted to
    the event_ids in cohort_subset. Rows for subjects missing the file are
    dropped (not NaN-filled) -- coverage filtering happens on columns next."""
    filename, region_col, value_col = MEASURES[measure_key]
    frames = {}
    for event_id, row in cohort_subset.iterrows():
        csv_path = row["anat_dir"] / filename
        if not csv_path.exists():
            continue
        df = pd.read_csv(csv_path)
        frames[event_id] = df.set_index(region_col)[value_col]
    if not frames:
        return pd.DataFrame()
    wide = pd.DataFrame(frames).T  # event_id rows, region columns
    coverage = wide.notna().mean(axis=0)
    dropped = coverage[coverage < MIN_COVERAGE].index.tolist()
    if dropped:
        print(f"    [{measure_key}] dropping {len(dropped)} region(s) present in <{MIN_COVERAGE:.0%} "
              f"of subjects: {dropped[:8]}{' ...' if len(dropped) > 8 else ''}")
    wide = wide.drop(columns=dropped)
    wide = wide.dropna(axis=0, how="any")  # subjects still missing a kept region
    return wide


# ---------------------------------------------------------------- modeling

def fit_ols(X, Y):
    """X: (n,p) design incl. intercept. Y: (n,k) one column per region.
    Returns beta (p,k), se (p,k), df (int), rss (k,)."""
    n, p = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    beta = XtX_inv @ X.T @ Y
    resid = Y - X @ beta
    df = n - p
    rss = np.sum(resid ** 2, axis=0)
    sigma2 = rss / df
    se = np.sqrt(np.outer(np.diag(XtX_inv), sigma2))
    return beta, se, df, rss


def bh_fdr(pvals):
    p = np.asarray(pvals, dtype=float)
    n = len(p)
    order = np.argsort(p)
    ranked = p[order]
    q = ranked * n / (np.arange(1, n + 1))
    q = np.minimum.accumulate(q[::-1])[::-1]
    q = np.clip(q, 0, 1)
    out = np.empty(n)
    out[order] = q
    return out


def run_region_tests(group_df, covariate_df, Y):
    """group_df: (n, g) columns to test (jointly if g>1). covariate_df:
    (n, c) columns to adjust for. Y: (n, k) region values.
    Returns dict of per-region arrays: stat_type, stat, beta (NaN if g>1), df, p."""
    n = len(Y)
    intercept = np.ones((n, 1))
    X_full = np.hstack([intercept, group_df.to_numpy(dtype=float), covariate_df.to_numpy(dtype=float)])
    g = group_df.shape[1]

    if g == 1:
        beta, se, df, _rss = fit_ols(X_full, Y.to_numpy(dtype=float))
        j = 1  # column 0 = intercept, column 1 = the single group column
        t = beta[j] / se[j]
        p = 2 * spstats.t.sf(np.abs(t), df)
        return {"stat_type": "t", "stat": t, "beta": beta[j], "df": np.full_like(t, df), "p": p}

    X_reduced = np.hstack([intercept, covariate_df.to_numpy(dtype=float)])
    _b_full, _se_full, df_full, rss_full = fit_ols(X_full, Y.to_numpy(dtype=float))
    _b_red, _se_red, _df_red, rss_reduced = fit_ols(X_reduced, Y.to_numpy(dtype=float))
    F = ((rss_reduced - rss_full) / g) / (rss_full / df_full)
    F = np.clip(F, 0, None)
    p = spstats.f.sf(F, g, df_full)
    return {"stat_type": "F", "stat": F, "beta": np.full(F.shape, np.nan), "df": np.full_like(F, df_full), "p": p}


# ---------------------------------------------------------------- mode setup

def build_group_and_covariates(mode, cohort, group_col=None):
    """Returns (group_df, covariate_df, label) of dummy/continuous columns,
    both indexed like `cohort`, dropping rows with any NaN in a used column."""
    needed = {"age_open", "sex", "site"}
    if mode == "custom":
        if not group_col:
            sys.exit("--modes custom requires --group-col COLUMN")
        if group_col not in cohort.columns:
            needed.add(group_col)

    usable = cohort.dropna(subset=[c for c in needed if c in cohort.columns] +
                            ([group_col] if mode == "custom" and group_col not in needed else []))

    if mode == "age_median":
        median = usable["age_open"].median()
        group = (usable["age_open"] >= median).astype(float).to_frame("age_ge_median")
        covariates = pd.concat([
            pd.get_dummies(usable["sex"], prefix="sex", drop_first=True, dtype=float),
            pd.get_dummies(usable["site"], prefix="site", drop_first=True, dtype=float),
        ], axis=1)
        label = f"age >= median ({median:g})"
    elif mode == "age_tertile":
        tertile = pd.qcut(usable["age_open"], 3, labels=["t1", "t2", "t3"])
        group = pd.get_dummies(tertile, prefix="age_tertile", drop_first=True, dtype=float)
        covariates = pd.concat([
            pd.get_dummies(usable["sex"], prefix="sex", drop_first=True, dtype=float),
            pd.get_dummies(usable["site"], prefix="site", drop_first=True, dtype=float),
        ], axis=1)
        label = "age tertiles (t1 ref)"
    elif mode == "age_continuous":
        group = usable[["age_open"]].astype(float)
        covariates = pd.concat([
            pd.get_dummies(usable["sex"], prefix="sex", drop_first=True, dtype=float),
            pd.get_dummies(usable["site"], prefix="site", drop_first=True, dtype=float),
        ], axis=1)
        label = "age_open (linear)"
    elif mode == "sex":
        group = pd.get_dummies(usable["sex"], prefix="sex", drop_first=True, dtype=float)
        covariates = pd.concat([
            usable[["age_open"]].astype(float),
            pd.get_dummies(usable["site"], prefix="site", drop_first=True, dtype=float),
        ], axis=1)
        label = "sex"
    elif mode == "custom":
        group = pd.get_dummies(usable[group_col], prefix=group_col, drop_first=True, dtype=float)
        cov_cols = [c for c in ("age_open", "sex", "site") if c != group_col]
        parts = []
        for c in cov_cols:
            if c == "age_open":
                parts.append(usable[["age_open"]].astype(float))
            else:
                parts.append(pd.get_dummies(usable[c], prefix=c, drop_first=True, dtype=float))
        covariates = pd.concat(parts, axis=1)
        label = group_col
    else:
        sys.exit(f"Unknown mode '{mode}'")

    if group.shape[1] == 0:
        sys.exit(f"Mode '{mode}': the group variable has <2 categories after dropping missing values -- can't test it")
    return group, covariates, label, usable.index


# ---------------------------------------------------------------- plotting

def plot_manhattan(regions, p_fdr, alpha, out_path, title):
    order = np.arange(len(regions))
    neglog = -np.log10(np.clip(p_fdr, 1e-300, 1))
    fig, ax = plt.subplots(figsize=(max(6, len(regions) * 0.12), 4))
    colors = ["#d62728" if v <= -np.log10(alpha) else "#7f7f7f" for v in neglog]
    ax.bar(order, neglog, color=colors, width=0.9)
    ax.axhline(-np.log10(alpha), color="black", linestyle="--", linewidth=1, label=f"FDR q={alpha}")
    ax.set_xticks(order)
    ax.set_xticklabels(regions, rotation=90, fontsize=5)
    ax.set_ylabel("-log10(FDR p)")
    ax.set_title(title, fontsize=10)
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- orchestration

def run_mode(mode, cohort, measures, alpha, out_root, group_col=None, grouping_spec=None,
             dir_suffix=""):
    group_df, covariate_df, label, usable_idx = build_group_and_covariates(mode, cohort, group_col)
    mode_name = mode if mode != "custom" else f"custom_{group_col}"
    mode_out = out_root / (mode_name + dir_suffix)
    mode_out.mkdir(parents=True, exist_ok=True)
    pass_label = "composite regions" if grouping_spec else "one region per parcel"
    print(f"\n== mode: {mode_name} ({label}) -- {pass_label} -- "
          f"{len(usable_idx)} subjects with complete covariates ==")

    manifest_lines = [
        f"mode: {mode_name}", f"tested variable: {label}",
        f"covariates: {list(covariate_df.columns)}", f"alpha (FDR): {alpha}",
        f"subjects with complete demographics for this mode: {len(usable_idx)}", "",
    ]

    for measure_key in measures:
        wide = build_measure_table(measure_key, cohort.loc[cohort.index.intersection(usable_idx)])
        wide, applied_grouping = combine_measure_table(wide, measure_key, grouping_spec)
        if wide.empty:
            print(f"  [{measure_key}] no data available -- skipping")
            manifest_lines.append(f"{measure_key}: skipped, no CSVs found")
            continue
        common = wide.index.intersection(usable_idx)
        wide = wide.loc[common]
        g = group_df.loc[common]
        c = covariate_df.loc[common]
        n, k = wide.shape
        min_n = n - (1 + g.shape[1] + c.shape[1])
        if min_n < 3:
            print(f"  [{measure_key}] only {n} subjects for {g.shape[1] + c.shape[1] + 1} design columns -- skipping (too few df)")
            manifest_lines.append(f"{measure_key}: skipped, insufficient df (n={n})")
            continue

        result = run_region_tests(g, c, wide)
        p_fdr = bh_fdr(result["p"])
        significant = p_fdr <= alpha
        results_df = pd.DataFrame({
            "region": wide.columns, "n": n, "df": result["df"].astype(int),
            "stat_type": result["stat_type"], "stat": result["stat"],
            "beta": result["beta"], "p": result["p"], "p_fdr": p_fdr,
            "significant": significant,
        }).sort_values("p_fdr")
        results_df.to_csv(mode_out / f"{measure_key}_results.csv", index=False)
        if applied_grouping is not None:
            (mode_out / f"{measure_key}_region_groups.json").write_text(
                json.dumps(applied_grouping.to_dict(), indent=2) + "\n")
        plot_manhattan(wide.columns.tolist(), p_fdr, alpha, mode_out / f"{measure_key}_manhattan.png",
                        f"{mode_name} / {measure_key} (n={n})")

        n_sig = int(significant.sum())
        print(f"  [{measure_key}] n={n}, {k} region(s) tested, {n_sig} significant at FDR q<={alpha}")
        if n_sig:
            top = results_df[results_df["significant"]].head(5)["region"].tolist()
            print(f"    top hits: {top}")
        manifest_lines.append(
            f"{measure_key}: n={n}, regions_tested={k}, significant={n_sig}"
            + (f", combined via {applied_grouping.source} "
               f"(rule={MEASURE_COMBINE_RULE.get(measure_key, 'mean')})" if applied_grouping else ""))

    (mode_out / "manifest.txt").write_text("\n".join(manifest_lines) + "\n")


def resolve_measures(arg):
    if arg in MEASURE_GROUPS:
        return MEASURE_GROUPS[arg]
    sys.exit(f"--measures must be one of {list(MEASURE_GROUPS)}")


def main():
    ap = argparse.ArgumentParser(description="Group-level cross-sectional statistics on extracted anatomical measures.")
    ap.add_argument("raw_root", nargs="?", help="path to raw data root (contains sub-*/ses-*/... and the demographics CSV)")
    ap.add_argument("--demographics", help="path to AABC2_subjects_*.csv (default: newest match under raw_root)")
    ap.add_argument("--modes", default=",".join(BUILTIN_MODES),
                    help=f"comma-separated: {BUILTIN_MODES + ['custom']} (default: all four built-ins)")
    ap.add_argument("--group-col", help="demographics column to test, required when --modes includes 'custom'")
    ap.add_argument("--session", default="earliest",
                    help="'earliest' (default), 'latest', or a literal session name e.g. ses-V1")
    ap.add_argument("--measures", default="all", choices=list(MEASURE_GROUPS), help="which extracted measures to test")
    ap.add_argument("--alpha", type=float, default=0.05, help="FDR q-value threshold (default 0.05)")
    ap.add_argument("--subjects", help="comma-separated subject folder names to restrict to")
    ap.add_argument("--groups", default=region_grouping.DEFAULT_SPEC,
                     help="ALSO run every mode over composite regions, into "
                          "<mode>_combined/ beside the per-region results (default: lr, "
                          "'none' to skip). Combining before testing also reduces the number "
                          "of comparisons the FDR correction is applied over, so the two "
                          "passes are corrected separately and are not directly comparable: "
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
    analysed_root = raw_root.parent / "Analysed_data"
    subject_filter = set(s.strip() for s in args.subjects.split(",")) if args.subjects else None
    modes = [m.strip() for m in args.modes.split(",")]

    demographics_path = find_demographics(raw_root, args.demographics)
    print(f"Demographics: {demographics_path}")
    demo = load_demographics(demographics_path)

    sessions = select_sessions(analysed_root, args.session, subject_filter)
    if not sessions:
        sys.exit("No sessions selected -- nothing to analyze")
    cohort = build_cohort_table(sessions, demo)
    if cohort.empty:
        sys.exit("No subjects matched between Analysed_data and the demographics CSV")
    print(f"Cohort: {len(cohort)} subjects (one session each, policy='{args.session}')")

    measures = resolve_measures(args.measures)
    out_root = analysed_root / "group_analysis"
    grouping_spec = None if str(args.groups).strip().lower() in ("", "none") else args.groups
    for mode in modes:
        # Pass 1: one region per parcel, exactly as before.
        run_mode(mode, cohort, measures, args.alpha, out_root, group_col=args.group_col)
        # Pass 2: the same test over composite regions, in its own folder. Each
        # pass gets its own FDR correction over its own region count -- that is
        # the point of combining, and it means a p_fdr from one is not
        # comparable with a p_fdr from the other.
        if grouping_spec:
            run_mode(mode, cohort, measures, args.alpha, out_root, group_col=args.group_col,
                     grouping_spec=grouping_spec, dir_suffix="_combined")

    print(f"\nDone. Results under {out_root}/<mode>/"
          + (f" and {out_root}/<mode>_combined/" if grouping_spec else ""))


if __name__ == "__main__":
    main()
