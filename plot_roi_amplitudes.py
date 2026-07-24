#!/usr/bin/env python3
"""
Interactive box plots of ROI amplitudes, restricted to subjects listed in a
group CSV file, split by visit (V1-V4).

Can be run from any directory. You must point it at two files:

  GROUP file: the CSV/Excel sheet that lists which subjects (and which of
      their visits) belong to your study group. Its first column must hold
      subject-visit IDs like 'SUBJECT_V1'.

  DATA file: the CSV/Excel sheet that holds the amplitude values of the
      different ROIs for every subject/visit. Its first column must contain
      subject-visit IDs matching the GROUP file, and its first row must
      contain the ROI names (one ROI per column, from column 2 onward).

Examples:
    python3 plot_roi_amplitudes.py \\
        --group /path/to/group5_age_gt50_at_v1_and_moca_gt21_persession.csv \\
        --data /path/to/rfMRI_REST_FullAmplitudes.csv

    python3 plot_roi_amplitudes.py -g group5.csv -d rfMRI_REST_FullAmplitudes.csv -o ./figures

Run with -h/--help to see this message again.

You will be shown the list of available ROIs and asked to pick which ones
to plot (comma-separated names or numbers). Up to 3 ROIs are drawn together
on one set of axes (grouped by ROI, boxes colored by visit); more than 3
ROIs produces one separate figure per ROI. Every figure is saved as both
.svg (vector) and .png in the output directory.
"""

import argparse
import os
import sys

import matplotlib.pyplot as plt
import pandas as pd

VISITS = ["V1", "V2", "V3", "V4"]
MAX_ROIS_COMBINED = 3


def parse_args():
    parser = argparse.ArgumentParser(
        description="Box plots of ROI amplitudes by visit, for subjects listed in a group CSV file.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-g", "--group", required=True,
        help="Path to the GROUP CSV/Excel sheet: the file that lists all subject names "
             "along with their visits (i.e. which subjects/visits belong to your study "
             "group). Its first column must hold subject-visit IDs, e.g. 'SUBJECT_V1' "
             "(e.g. group5_age_gt50_at_v1_and_moca_gt21_persession.csv).",
    )
    parser.add_argument(
        "-d", "--data", required=True,
        help="Path to the DATA CSV/Excel sheet: the file where the amplitudes of the "
             "different ROIs are stored for every subject. First column must contain "
             "subject-visit IDs matching the GROUP file; first row must contain the ROI "
             "names, one per column from column 2 onward "
             "(e.g. rfMRI_REST_FullAmplitudes.csv).",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Directory to save the output figures into. Created if it doesn't exist. "
             "Defaults to the current working directory if not given.",
    )
    return parser.parse_args()


def die(message):
    sys.exit(f"Error: {message}\nRun with -h/--help for usage information.")


def load_group_subjects(group_path):
    """Read the group CSV file and return the set of relevant subject-visit
    identifiers (e.g. 'HCA6000030_V1') taken from its first column."""
    if not os.path.isfile(group_path):
        die(f"group file not found: {group_path}")

    try:
        group_df = pd.read_csv(group_path)
    except Exception as exc:
        die(f"could not read group file '{group_path}': {exc}")

    if group_df.empty or group_df.shape[1] == 0:
        die(f"group file '{group_path}' has no readable columns.")

    id_col = group_df.columns[0]
    subject_ids = set(group_df[id_col].astype(str))
    print(f"Loaded {len(subject_ids)} subject-visit entries from {os.path.basename(group_path)}")
    return subject_ids


def load_amplitudes(subject_ids, data_path):
    """Read the amplitudes CSV and keep only rows whose subject id is in
    subject_ids. Returns a DataFrame with a 'visit' column plus one column
    per ROI, and the list of ROI column names."""
    if not os.path.isfile(data_path):
        die(f"data file not found: {data_path}")

    try:
        df = pd.read_csv(data_path)
    except Exception as exc:
        die(f"could not read data file '{data_path}': {exc}")

    if df.empty or df.shape[1] < 2:
        die(f"data file '{data_path}' does not look like an amplitudes CSV (need a subject-ID column plus ROI columns).")

    id_col = df.columns[0]
    df = df.rename(columns={id_col: "subject_visit"})
    df = df[df["subject_visit"].astype(str).isin(subject_ids)].copy()

    if df.empty:
        die("no matching subjects found between the group file and the data file. "
            "Check that the ID formats match (e.g. 'SUBJECT_V1') in both files.")

    df["visit"] = df["subject_visit"].astype(str).str.extract(r"_(V\d+)$")
    roi_columns = [c for c in df.columns if c not in ("subject_visit", "visit")]
    print(f"Matched {len(df)} rows across visits {sorted(df['visit'].dropna().unique())}")
    return df, roi_columns


def choose_rois(roi_columns):
    """Print all ROI names with an index, then ask the user to choose."""
    print("\nAvailable ROIs:")
    for i, roi in enumerate(roi_columns, start=1):
        print(f"  {i:3d}. {roi}")

    print(
        "\nEnter the ROIs you want to plot, separated by commas.\n"
        "You may use ROI names or their index numbers (e.g. '3,7,12' or 'R_V1_ROI,R_V4_ROI')."
    )
    raw = input("ROIs: ").strip()
    if not raw:
        die("no ROIs entered.")

    tokens = [t.strip() for t in raw.split(",") if t.strip()]
    name_lookup = {roi: roi for roi in roi_columns}
    selected = []
    for tok in tokens:
        if tok in name_lookup:
            selected.append(tok)
        elif tok.isdigit() and 1 <= int(tok) <= len(roi_columns):
            selected.append(roi_columns[int(tok) - 1])
        else:
            print(f"  Warning: '{tok}' not recognized, skipping.")

    # de-duplicate while preserving order
    seen = set()
    selected = [r for r in selected if not (r in seen or seen.add(r))]

    if not selected:
        die("no valid ROIs selected.")

    print(f"\nSelected {len(selected)} ROI(s): {', '.join(selected)}")
    return selected


def visit_data_for_roi(df, roi):
    """Return a list of arrays (one per visit in VISITS) of non-NaN
    amplitude values for the given ROI."""
    return [
        df.loc[df["visit"] == v, roi].dropna().values
        for v in VISITS
    ]


def save_figure(fig, output_dir, filename_stem):
    os.makedirs(output_dir, exist_ok=True)
    svg_path = os.path.join(output_dir, f"{filename_stem}.svg")
    png_path = os.path.join(output_dir, f"{filename_stem}.png")
    fig.savefig(svg_path, format="svg", bbox_inches="tight")
    fig.savefig(png_path, format="png", dpi=300, bbox_inches="tight")
    print(f"  Saved {svg_path}")
    print(f"  Saved {png_path}")


def plot_combined(df, rois, output_dir):
    """Single axes, grouped boxes: for each ROI, 4 adjacent boxes (V1-V4)."""
    fig, ax = plt.subplots(figsize=(2.2 * len(rois) * len(VISITS) / 2, 6))

    n_visits = len(VISITS)
    group_width = n_visits + 1  # extra gap between ROI groups
    colors = plt.cm.tab10.colors

    positions = []
    box_data = []
    box_colors = []
    for g, roi in enumerate(rois):
        data = visit_data_for_roi(df, roi)
        for v_idx, values in enumerate(data):
            positions.append(g * group_width + v_idx)
            box_data.append(values)
            box_colors.append(colors[v_idx % len(colors)])

    bp = ax.boxplot(box_data, positions=positions, widths=0.7, patch_artist=True)
    for patch, color in zip(bp["boxes"], box_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)

    # x-axis: group centers labeled with ROI names
    group_centers = [g * group_width + (n_visits - 1) / 2 for g in range(len(rois))]
    ax.set_xticks(group_centers)
    ax.set_xticklabels(rois, rotation=20, ha="right")
    ax.set_ylabel("Amplitude")
    ax.set_title("ROI Amplitudes by Visit")

    # legend for visits
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor=colors[i % len(colors)], alpha=0.7)
        for i in range(n_visits)
    ]
    ax.legend(handles, VISITS, title="Visit", loc="best")

    fig.tight_layout()
    save_figure(fig, output_dir, "combined_" + "_".join(rois))
    plt.close(fig)


def plot_separate(df, rois, output_dir):
    """One figure per ROI, boxplots for V1-V4 on a single axes."""
    colors = plt.cm.tab10.colors
    for roi in rois:
        data = visit_data_for_roi(df, roi)
        fig, ax = plt.subplots(figsize=(4, 6))
        bp = ax.boxplot(data, positions=range(len(VISITS)), widths=0.6, patch_artist=True)
        for patch, color in zip(bp["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)
        ax.set_xticks(range(len(VISITS)))
        ax.set_xticklabels(VISITS)
        ax.set_ylabel("Amplitude")
        ax.set_title(roi)
        fig.tight_layout()
        save_figure(fig, output_dir, roi)
        plt.close(fig)


def main():
    args = parse_args()
    output_dir = args.output if args.output else os.getcwd()

    subject_ids = load_group_subjects(args.group)
    df, roi_columns = load_amplitudes(subject_ids, args.data)
    rois = choose_rois(roi_columns)

    if len(rois) <= MAX_ROIS_COMBINED:
        plot_combined(df, rois, output_dir)
    else:
        plot_separate(df, rois, output_dir)

    print(f"\nDone. Figures saved in: {os.path.abspath(output_dir)}")


if __name__ == "__main__":
    main()
