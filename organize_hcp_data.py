#!/usr/bin/env python3
"""
Organize HCP Aging "Recommended" zip packages into a
sub-<subject>/ses-<visit>/{anat,func,concat}/ hierarchy.

Each zip extracts to a single top-level wrapper folder (<subject>_<visit>_MR/);
its entire contents are moved as-is into the modality folder that matches the
zip type (Structural -> anat, RestFmri -> func, ConcatFmri -> concat).

Usage:
    python organize_hcp_data.py --input /path/to/zip_folder --output /path/to/organized_root
    python organize_hcp_data.py --input /path/to/zip_folder --output /path/to/organized_root --dry-run
"""

import argparse
import csv
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

ZIP_NAME_RE = re.compile(
    r"^(?P<subject>HCA\d+)_(?P<visit>V\d+)_MR_(?P<modality>[A-Za-z]+?)Recommended.*\.zip$",
    re.IGNORECASE,
)

MODALITY_FOLDER = {
    "structural": "anat",
    "restfmri": "func",
    "concatfmri": "concat",
}

MANIFEST_FIELDS = [
    "subject", "visit", "modality", "target_folder",
    "zip_filename", "status", "target_path", "timestamp",
]


def parse_zip_filename(filename: str):
    m = ZIP_NAME_RE.match(filename)
    if not m:
        return None
    subject = m.group("subject")
    visit = m.group("visit")
    modality_raw = m.group("modality")
    target_folder = MODALITY_FOLDER.get(modality_raw.lower())
    return subject, visit, modality_raw, target_folder


def extract_zip(zip_path: Path, staging_dir: Path):
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging_dir)


def resolve_content_root(staging_dir: Path) -> Path:
    """If extraction produced exactly one top-level wrapper folder, return it.
    Otherwise return staging_dir itself."""
    entries = [p for p in staging_dir.iterdir() if p.name != ".DS_Store"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return staging_dir


def merge_tree(src: Path, dst: Path, conflicts: list):
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name == ".DS_Store":
            continue
        target = dst / item.name
        if item.is_dir():
            merge_tree(item, target, conflicts)
        elif target.exists():
            if target.stat().st_size != item.stat().st_size:
                conflicts.append(str(target))
            # identical or conflicting: never overwrite, just leave existing file
        else:
            shutil.move(str(item), str(target))


def already_extracted(target_dir: Path) -> bool:
    if not target_dir.exists():
        return False
    return any(p.name != ".DS_Store" for p in target_dir.iterdir())


def process_zip(zip_path: Path, output_root: Path, archive_dir: Path,
                 manifest_rows: list, conflicts: list, dry_run: bool):
    timestamp = datetime.now().isoformat(timespec="seconds")
    parsed = parse_zip_filename(zip_path.name)

    if not parsed:
        manifest_rows.append({
            "subject": "", "visit": "", "modality": "", "target_folder": "",
            "zip_filename": zip_path.name, "status": "unrecognized_filename",
            "target_path": "", "timestamp": timestamp,
        })
        print(f"[SKIP] Unrecognized filename pattern: {zip_path.name}")
        return

    subject, visit, modality_raw, target_folder = parsed

    if target_folder is None:
        manifest_rows.append({
            "subject": subject, "visit": visit, "modality": modality_raw,
            "target_folder": "", "zip_filename": zip_path.name,
            "status": "unknown_modality", "target_path": "", "timestamp": timestamp,
        })
        print(f"[SKIP] Unknown modality '{modality_raw}' in {zip_path.name}")
        return

    target_dir = output_root / f"sub-{subject}" / f"ses-{visit}" / target_folder

    if already_extracted(target_dir):
        manifest_rows.append({
            "subject": subject, "visit": visit, "modality": modality_raw,
            "target_folder": target_folder, "zip_filename": zip_path.name,
            "status": "skipped_already_extracted", "target_path": str(target_dir),
            "timestamp": timestamp,
        })
        print(f"[SKIP] Already extracted: {target_dir}")
        return

    print(f"[{'DRY-RUN' if dry_run else 'INFO'}] {zip_path.name} -> {target_dir}")

    if dry_run:
        manifest_rows.append({
            "subject": subject, "visit": visit, "modality": modality_raw,
            "target_folder": target_folder, "zip_filename": zip_path.name,
            "status": "dry_run_would_extract", "target_path": str(target_dir),
            "timestamp": timestamp,
        })
        return

    with tempfile.TemporaryDirectory(dir=output_root) as tmp:
        staging_dir = Path(tmp)
        try:
            extract_zip(zip_path, staging_dir)
        except zipfile.BadZipFile:
            manifest_rows.append({
                "subject": subject, "visit": visit, "modality": modality_raw,
                "target_folder": target_folder, "zip_filename": zip_path.name,
                "status": "error_bad_zip", "target_path": "", "timestamp": timestamp,
            })
            print(f"[ERROR] Bad zip file: {zip_path.name}")
            return

        content_root = resolve_content_root(staging_dir)
        merge_tree(content_root, target_dir, conflicts)

    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(zip_path), str(archive_dir / zip_path.name))

    md5_path = zip_path.with_suffix(zip_path.suffix + ".md5")
    if md5_path.exists():
        shutil.move(str(md5_path), str(archive_dir / md5_path.name))

    manifest_rows.append({
        "subject": subject, "visit": visit, "modality": modality_raw,
        "target_folder": target_folder, "zip_filename": zip_path.name,
        "status": "extracted", "target_path": str(target_dir), "timestamp": timestamp,
    })


def main():
    parser = argparse.ArgumentParser(
        description="Organize HCP Aging Recommended zip packages into sub-/ses-/anat|func|concat hierarchy."
    )
    parser.add_argument("--input", required=True, type=Path, help="Folder containing the .zip files (required)")
    parser.add_argument("--output", required=True, type=Path, help="Root folder for organized data + manifest (required)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without extracting or moving anything")
    parser.add_argument("--subject", help="Only process zips for this subject ID (e.g. HCA6072156)")
    args = parser.parse_args()

    input_dir: Path = args.input.expanduser().resolve()
    output_root: Path = args.output.expanduser().resolve()

    if not input_dir.is_dir():
        sys.exit(f"Input path does not exist or is not a directory: {input_dir}")

    output_root.mkdir(parents=True, exist_ok=True)
    archive_dir = output_root / "archive"

    zip_files = sorted(input_dir.glob("*.zip"))
    if args.subject:
        zip_files = [p for p in zip_files if p.name.startswith(args.subject)]
    if not zip_files:
        sys.exit(f"No .zip files found in {input_dir}" + (f" for subject {args.subject}" if args.subject else ""))

    manifest_rows = []
    conflicts = []

    for zip_path in zip_files:
        process_zip(zip_path, output_root, archive_dir, manifest_rows, conflicts, args.dry_run)

    manifest_path = output_root / "manifest.csv"
    file_exists = manifest_path.exists()
    with open(manifest_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(manifest_rows)

    if conflicts:
        conflicts_path = output_root / "conflicts.log"
        with open(conflicts_path, "a") as f:
            for c in conflicts:
                f.write(c + "\n")
        print(f"\n[WARNING] {len(conflicts)} file conflicts logged to {conflicts_path}")

    extracted = sum(1 for r in manifest_rows if r["status"] == "extracted")
    skipped = sum(1 for r in manifest_rows if r["status"].startswith("skipped"))
    would = sum(1 for r in manifest_rows if r["status"] == "dry_run_would_extract")
    errors = sum(
        1 for r in manifest_rows
        if r["status"].startswith("error") or r["status"] in ("unrecognized_filename", "unknown_modality")
    )

    print(f"\nDone. extracted={extracted} skipped={skipped} dry_run_would_extract={would} errors={errors}")
    print(f"Manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
