#!/usr/bin/env python3
"""
Organize HCP Aging "Recommended" zip packages into a
sub-<subject>/ses-<visit>/{anat,func,concat}/ hierarchy.

Each zip extracts to a single top-level wrapper folder (<subject>_<visit>_MR/);
its entire contents are moved as-is into the modality folder that matches the
zip type (Structural -> anat, RestFmri -> func, ConcatFmri -> concat).

A damaged zip never aborts the run: the failure is recorded in run_log.json
(and optionally run_log.xlsx) and processing continues with the next file.

Usage:
    python organize_hcp_data.py --input /path/to/zip_folder --output /path/to/organized_root
    python organize_hcp_data.py --input /path/to/zip_folder --output /path/to/organized_root --dry-run
    python organize_hcp_data.py --input . --output /path/to/organized_root --verify-md5
"""

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
import zlib
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

# Written into a target folder whose merge did not finish, so a later run
# retries it instead of mistaking the partial contents for a good extraction.
INCOMPLETE_MARKER = ".extraction_incomplete"

MD5_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$")


def parse_zip_filename(filename: str):
    m = ZIP_NAME_RE.match(filename)
    if not m:
        return None
    subject = m.group("subject")
    visit = m.group("visit")
    modality_raw = m.group("modality")
    target_folder = MODALITY_FOLDER.get(modality_raw.lower())
    return subject, visit, modality_raw, target_folder


def compute_md5(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def read_expected_md5(zip_path: Path):
    """Return the checksum recorded in the sibling .md5 sidecar, or None."""
    md5_path = zip_path.with_suffix(zip_path.suffix + ".md5")
    if not md5_path.exists():
        return None
    try:
        for token in md5_path.read_text(errors="replace").split():
            if MD5_HEX_RE.match(token):
                return token.lower()
    except OSError:
        return None
    return None


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
    if (target_dir / INCOMPLETE_MARKER).exists():
        return False
    return any(
        p.name not in (".DS_Store", INCOMPLETE_MARKER) for p in target_dir.iterdir()
    )


def error_type_name(exc: BaseException) -> str:
    """Fully-qualified exception name; zlib's is bare 'error' without the module."""
    cls = type(exc)
    module = getattr(cls, "__module__", "")
    return f"{module}.{cls.__name__}" if module and module != "builtins" else cls.__name__


def classify_error(exc: BaseException) -> str:
    """Map an extraction failure onto a manifest status."""
    if isinstance(exc, zipfile.BadZipFile):
        return "error_bad_zip"
    if isinstance(exc, (zlib.error, EOFError)):
        return "error_corrupt_data"
    if isinstance(exc, OSError):
        return "error_io"
    return "error_unexpected"


class RunLog:
    """Accumulates one record per zip and flushes to disk after each one, so an
    interrupted run still leaves a complete record of everything it touched."""

    def __init__(self, log_dir: Path, want_excel: bool):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = log_dir / "manifest.csv"
        self.json_path = log_dir / "run_log.json"
        self.excel_path = log_dir / "run_log.xlsx"
        self.want_excel = want_excel
        self.records = []

        manifest_exists = self.manifest_path.exists()
        self._fh = open(self.manifest_path, "a", newline="")
        self._writer = csv.DictWriter(self._fh, fieldnames=MANIFEST_FIELDS)
        if not manifest_exists:
            self._writer.writeheader()
            self._fh.flush()

        self._previous = []
        if self.json_path.exists():
            try:
                loaded = json.loads(self.json_path.read_text())
                if isinstance(loaded, list):
                    self._previous = loaded
            except (json.JSONDecodeError, OSError):
                # Keep a damaged log rather than silently discarding it.
                backup = self.json_path.with_suffix(".json.corrupt")
                try:
                    self.json_path.replace(backup)
                    print(f"[WARNING] Unreadable run log preserved as {backup}")
                except OSError:
                    pass

    def add(self, **record):
        record.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
        self.records.append(record)

        self._writer.writerow({k: record.get(k, "") for k in MANIFEST_FIELDS})
        self._fh.flush()

        try:
            self.json_path.write_text(
                json.dumps(self._previous + self.records, indent=2)
            )
        except OSError as exc:
            print(f"[WARNING] Could not write {self.json_path}: {exc}")

    def close(self):
        self._fh.close()
        if not self.want_excel or not self.records:
            return
        try:
            import pandas as pd
        except ImportError:
            print("[WARNING] --excel needs pandas; skipping run_log.xlsx")
            return
        try:
            frame = pd.DataFrame(self._previous + self.records)
            with pd.ExcelWriter(self.excel_path, engine="openpyxl") as writer:
                frame.to_excel(writer, sheet_name="all_records", index=False)
                problems = frame[frame["status"].str.startswith("error", na=False)]
                if not problems.empty:
                    problems.to_excel(writer, sheet_name="problems", index=False)
            print(f"Excel log written to {self.excel_path}")
        except ImportError:
            print("[WARNING] --excel needs openpyxl (pip install openpyxl); "
                  "skipping run_log.xlsx")
        except Exception as exc:
            print(f"[WARNING] Could not write {self.excel_path}: {exc}")


def process_zip(zip_path: Path, output_root: Path, archive_dir: Path,
                log: RunLog, conflicts: list, dry_run: bool, verify_md5: bool):
    parsed = parse_zip_filename(zip_path.name)

    try:
        size_bytes = zip_path.stat().st_size
    except OSError:
        size_bytes = None

    base = {
        "zip_filename": zip_path.name,
        "zip_path": str(zip_path),
        "zip_size_bytes": size_bytes,
        "subject": "", "visit": "", "modality": "", "target_folder": "",
        "target_path": "",
    }

    if not parsed:
        log.add(**base, status="unrecognized_filename")
        print(f"[SKIP] Unrecognized filename pattern: {zip_path.name}")
        return

    subject, visit, modality_raw, target_folder = parsed
    base.update(subject=subject, visit=visit, modality=modality_raw)

    if target_folder is None:
        log.add(**base, status="unknown_modality")
        print(f"[SKIP] Unknown modality '{modality_raw}' in {zip_path.name}")
        return

    target_dir = output_root / f"sub-{subject}" / f"ses-{visit}" / target_folder
    base.update(target_folder=target_folder, target_path=str(target_dir))

    if already_extracted(target_dir):
        log.add(**base, status="skipped_already_extracted")
        print(f"[SKIP] Already extracted: {target_dir}")
        return

    if verify_md5:
        expected = read_expected_md5(zip_path)
        if expected is None:
            base["md5_expected"] = None
            base["md5_actual"] = None
            print(f"[WARN] No .md5 sidecar for {zip_path.name}; skipping checksum")
        else:
            print(f"[INFO] Verifying checksum: {zip_path.name}")
            try:
                actual = compute_md5(zip_path)
            except OSError as exc:
                log.add(**base, status="error_io", error_type=error_type_name(exc),
                        error_message=str(exc), stage="md5")
                print(f"[ERROR] Could not read {zip_path.name}: {exc}")
                return
            base["md5_expected"] = expected
            base["md5_actual"] = actual
            if actual != expected:
                log.add(**base, status="error_md5_mismatch", stage="md5",
                        error_type="ChecksumMismatch",
                        error_message=f"expected {expected}, got {actual}")
                print(f"[ERROR] Checksum mismatch (corrupt download): {zip_path.name}")
                print(f"        expected {expected}")
                print(f"        actual   {actual}")
                return

    print(f"[{'DRY-RUN' if dry_run else 'INFO'}] {zip_path.name} -> {target_dir}")

    if dry_run:
        log.add(**base, status="dry_run_would_extract")
        return

    with tempfile.TemporaryDirectory(dir=output_root) as tmp:
        staging_dir = Path(tmp)
        try:
            extract_zip(zip_path, staging_dir)
        except Exception as exc:
            status = classify_error(exc)
            log.add(**base, status=status, stage="extract",
                    error_type=error_type_name(exc), error_message=str(exc))
            print(f"[ERROR] {status} while extracting {zip_path.name}: "
                  f"{error_type_name(exc)}: {exc}")
            print("        Skipping this file and continuing.")
            return

        content_root = resolve_content_root(staging_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        marker = target_dir / INCOMPLETE_MARKER
        try:
            marker.touch()
        except OSError:
            marker = None
        try:
            merge_tree(content_root, target_dir, conflicts)
        except Exception as exc:
            log.add(**base, status=classify_error(exc), stage="merge",
                    error_type=error_type_name(exc), error_message=str(exc))
            print(f"[ERROR] Failed to move files into {target_dir}: "
                  f"{error_type_name(exc)}: {exc}")
            print("        Target left flagged incomplete; rerun to retry.")
            return
        if marker is not None:
            marker.unlink(missing_ok=True)

    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(zip_path), str(archive_dir / zip_path.name))
        md5_path = zip_path.with_suffix(zip_path.suffix + ".md5")
        if md5_path.exists():
            shutil.move(str(md5_path), str(archive_dir / md5_path.name))
    except OSError as exc:
        log.add(**base, status="extracted_archive_failed", stage="archive",
                error_type=error_type_name(exc), error_message=str(exc))
        print(f"[WARNING] Extracted, but could not archive {zip_path.name}: {exc}")
        return

    log.add(**base, status="extracted")


def main():
    parser = argparse.ArgumentParser(
        description="Organize HCP Aging Recommended zip packages into sub-/ses-/anat|func|concat hierarchy."
    )
    parser.add_argument("--input", required=True, type=Path, help="Folder containing the .zip files (required)")
    parser.add_argument("--output", required=True, type=Path, help="Root folder for organized data + manifest (required)")
    parser.add_argument("--dry-run", action="store_true", help="Report what would happen without extracting or moving anything")
    parser.add_argument("--subject", help="Only process zips for this subject ID (e.g. HCA6072156)")
    parser.add_argument("--verify-md5", action="store_true",
                        help="Check each zip against its .md5 sidecar before extracting "
                             "(catches corrupt downloads up front; reads every byte, so it is slow)")
    parser.add_argument("--log-dir", type=Path, default=None,
                        help="Where to write manifest.csv / run_log.json / run_log.xlsx "
                             "(default: the --output folder)")
    parser.add_argument("--excel", action="store_true",
                        help="Also write run_log.xlsx alongside run_log.json (needs pandas + openpyxl)")
    args = parser.parse_args()

    input_dir: Path = args.input.expanduser().resolve()
    output_root: Path = args.output.expanduser().resolve()

    if not input_dir.is_dir():
        sys.exit(f"Input path does not exist or is not a directory: {input_dir}")

    output_root.mkdir(parents=True, exist_ok=True)
    archive_dir = output_root / "archive"

    # "._*" entries are macOS AppleDouble sidecars created on filesystems with no
    # native resource forks (e.g. exFAT). They are not real packages.
    zip_files = sorted(p for p in input_dir.glob("*.zip") if not p.name.startswith("._"))
    if args.subject:
        zip_files = [p for p in zip_files if p.name.startswith(args.subject)]
    if not zip_files:
        sys.exit(f"No .zip files found in {input_dir}" + (f" for subject {args.subject}" if args.subject else ""))

    log_dir = (args.log_dir.expanduser().resolve() if args.log_dir else output_root)
    log = RunLog(log_dir, want_excel=args.excel)
    conflicts = []

    try:
        for zip_path in zip_files:
            process_zip(zip_path, output_root, archive_dir, log,
                        conflicts, args.dry_run, args.verify_md5)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Stopping early; records so far are already on disk.")
    finally:
        log.close()

    if conflicts:
        conflicts_path = log_dir / "conflicts.log"
        with open(conflicts_path, "a") as f:
            for c in conflicts:
                f.write(c + "\n")
        print(f"\n[WARNING] {len(conflicts)} file conflicts logged to {conflicts_path}")

    rows = log.records
    extracted = sum(1 for r in rows if r["status"] == "extracted")
    skipped = sum(1 for r in rows if r["status"].startswith("skipped"))
    would = sum(1 for r in rows if r["status"] == "dry_run_would_extract")
    failures = [r for r in rows if r["status"].startswith("error")
                or r["status"] in ("unrecognized_filename", "unknown_modality")]

    print(f"\nDone. extracted={extracted} skipped={skipped} "
          f"dry_run_would_extract={would} errors={len(failures)}")
    print(f"Manifest written to {log.manifest_path}")
    print(f"Full record written to {log.json_path}")

    if failures:
        print(f"\n{len(failures)} file(s) need attention:")
        for r in failures:
            detail = r.get("error_message") or r["status"]
            print(f"  - {r['zip_filename']}: {r['status']} ({detail})")


if __name__ == "__main__":
    main()
