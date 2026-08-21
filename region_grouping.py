#!/usr/bin/env python3
"""Combining parcels into composite brain regions, shared by every script here.

Why one module: the same question -- "treat left and right as one region", or
"treat SNpc+SNpr as one SN" -- comes up in the FC pipeline, the anatomical
extraction, and the group statistics, over three different naming conventions
(HCPex, Schaefer-400, FreeSurfer/wmparc). Implementing it three times would
guarantee three slightly different answers to "which parcels are a pair".

This generalises what run_fc_pipeline_v2.py already did for its triangle
analyses (TRIANGLE_COMBINE_MAP + build_triangle_ts): the same idea, driven by a
grouping the caller supplies rather than a fixed map.

------------------------------------------------------------------ how to use

A grouping is specified as one of:
  None / "none"    no combining
  "lr"             every left/right pair the naming convention exposes
  <path>.json      an explicit grouping file, e.g.
                     {
                       "combine": {
                         "VTA": ["Ventral_tegmenta_area_L", "Ventral_tegmenta_area_R"],
                         "SN":  ["Substantia_nigra_pars_compacta_L", "Substantia_nigra_pars_compacta_R",
                                 "Substantia_nigra_pars_reticulata_L", "Substantia_nigra_pars_reticulata_R"]
                       },
                       "keep_ungrouped": true
                     }

  grouping = region_grouping.load("lr", all_names)
  ts2, names2 = region_grouping.combine_timeseries(ts, all_names, grouping)

--------------------------------------------------- how values are aggregated

The rule depends on what the number *is*, which is why callers pass it
explicitly rather than the module guessing:

  volumes / voxel counts     SUM      two hemispheres of one structure occupy
                                      the sum of their volumes, never the mean
  cortical thickness, myelin WEIGHTED MEAN by vertex count -- the plain mean of
                                      two parcel means is only correct when both
                                      parcels have the same number of vertices,
                                      which Schaefer parcels do not
  BOLD timeseries            MEAN     averaged sample by sample, then correlated
                                      (identical to build_triangle_ts)

Combining timeseries *before* correlating is deliberate and is not the same as
averaging the two correlations afterwards; the first treats the pair as one
region, the second averages two separate measurements of different regions.
"""
import json
import re
from collections import OrderedDict
from pathlib import Path

import numpy as np

# (compiled pattern, template for the combined name). The first match wins, so
# the more specific prefixes (ctx-lh-, wm-lh-) come before the generic ones.
HEMI_RULES = [
    # FreeSurfer / wmparc
    (re.compile(r"^ctx-(?P<hemi>lh|rh)-(?P<rest>.+)$"), "ctx-{rest}"),
    (re.compile(r"^wm-(?P<hemi>lh|rh)-(?P<rest>.+)$"), "wm-{rest}"),
    (re.compile(r"^(?P<hemi>Left|Right)-(?P<rest>.+)$"), "{rest}"),
    # Schaefer-400 ("7Networks_LH_Vis_1") and any other <prefix>_LH_/<prefix>_RH_
    (re.compile(r"^(?P<pre>.*?)_?(?P<hemi>LH|RH)_(?P<rest>.+)$"), "{pre}_{rest}"),
    # HCPex ("Ventral_tegmenta_area_L")
    (re.compile(r"^(?P<rest>.+)_(?P<hemi>L|R)$"), "{rest}"),
]

_LEFT = {"l", "lh", "left"}


def split_hemisphere(name):
    """'Left-Hippocampus' -> ('Hippocampus', 'L'). Returns (name, None) when the
    name carries no hemisphere marker (e.g. 'Brain-Stem', '3rd-Ventricle')."""
    for pattern, template in HEMI_RULES:
        m = pattern.match(name)
        if not m:
            continue
        parts = m.groupdict()
        hemi = "L" if parts["hemi"].lower() in _LEFT else "R"
        base = template.format(**{k: v for k, v in parts.items() if k != "hemi"})
        base = base.strip("_").replace("__", "_")
        if not base:
            continue
        return base, hemi
    return name, None


INDEX_SUFFIX_RE = re.compile(r"^(?P<stem>.+?)_(?P<index>\d+)$")


class Grouping:
    """A resolved set of composite regions over a specific parcel list."""

    def __init__(self, combine, keep_ungrouped=True, source="custom", notes=None, spec=None):
        self.combine = OrderedDict(combine)
        self.keep_ungrouped = keep_ungrouped
        self.source = source
        # how the caller asked for this grouping ('lr', 'component', a file
        # path): built-ins are name-space dependent and must be rebuilt per
        # measure, explicit files are not
        self.spec = spec
        # caveats worth printing and recording in a run manifest -- e.g. that a
        # pairing rests on an assumption the atlas does not guarantee
        self.notes = list(notes or [])

    def __bool__(self):
        return bool(self.combine)

    @property
    def n_groups(self):
        return len(self.combine)

    def members(self):
        return {m for members in self.combine.values() for m in members}

    def validate(self, names):
        """Returns (errors, warnings) against the parcel list actually present."""
        errors, warnings = [], []
        available = set(names)
        seen = {}
        for combined, members in self.combine.items():
            if not members:
                errors.append(f"group '{combined}' has no members")
            if combined in available and combined not in members:
                errors.append(
                    f"group name '{combined}' collides with an existing parcel of the same name")
            for m in members:
                if m not in available:
                    warnings.append(f"group '{combined}': parcel '{m}' is not in this atlas -- ignored")
                if m in seen and seen[m] != combined:
                    errors.append(f"parcel '{m}' is claimed by both '{seen[m]}' and '{combined}'")
                seen[m] = combined
        return errors, warnings

    def resolve(self, names):
        """Drop members that aren't present, drop groups left empty, and return
        the output ordering: each composite takes the position of its first
        member, so combined output keeps the atlas's own ordering."""
        available = set(names)
        position = {n: i for i, n in enumerate(names)}
        resolved, order = OrderedDict(), []
        for combined, members in self.combine.items():
            present = [m for m in members if m in available]
            if not present:
                continue
            resolved[combined] = present
            order.append((min(position[m] for m in present), combined, present))

        grouped = {m for members in resolved.values() for m in members}
        if self.keep_ungrouped:
            for n in names:
                if n not in grouped:
                    order.append((position[n], n, [n]))
        order.sort(key=lambda t: t[0])
        return [(name, members) for _pos, name, members in order]

    def to_dict(self):
        return {"spec": self.spec, "combine": {k: list(v) for k, v in self.combine.items()},
                "keep_ungrouped": self.keep_ungrouped, "source": self.source,
                "notes": list(self.notes)}

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        return path

    def describe(self, names=None):
        """One block of text for a run manifest -- what was merged into what."""
        lines = [f"Region grouping: {self.source}",
                 f"  composite regions: {self.n_groups}",
                 f"  parcels not in any group: {'kept as-is' if self.keep_ungrouped else 'dropped'}"]
        for note in self.notes:
            lines.append(f"  NOTE: {note}")
        pairs = self.resolve(names) if names is not None else [(k, v) for k, v in self.combine.items()]
        for combined, members in pairs:
            if len(members) > 1:
                lines.append(f"    {combined} = {' + '.join(members)}")
        return "\n".join(lines)


def _asymmetric_index_stems(names):
    """Stems whose index sets differ between the hemispheres.

    This is the test for whether an index-numbered name can be paired across
    hemispheres at all. HCP-MMP/HCPex passes it (every area exists once per
    hemisphere with the same number); Schaefer-400 fails it for most network
    components, which is what makes index pairing unsafe there.
    """
    per_stem = {}
    for name in names:
        base, hemi = split_hemisphere(name)
        if hemi is None:
            continue
        m = INDEX_SUFFIX_RE.match(base)
        if not m:
            continue
        entry = per_stem.setdefault(m.group("stem").lower(), {"L": set(), "R": set()})
        entry[hemi].add(m.group("index"))
    return {stem for stem, sides in per_stem.items() if sides["L"] != sides["R"]}


def auto_bilateral(names, keep_ungrouped=True, index_pairs=False):
    """Every left/right pair the naming convention exposes, as one region each.

    Two deliberate refusals:

    * A base with only one hemisphere present is left alone -- merging it into a
      'combined' region of one member would relabel a unilateral parcel as
      though it were bilateral.

    * A base ending in a number (Schaefer-400's '7Networks_LH_Vis_1') is NOT
      paired by default. Those indices run per hemisphere and are not homologous
      across them: in this dataset's Schaefer-400, 23 of 28 network components
      have different parcel counts in the two hemispheres, and some exist in one
      hemisphere only (Default_PFC is LH-only with 24 parcels; Default_PFCdPFCm
      is RH-only with 13). Pairing 'LH_Vis_1' with 'RH_Vis_1' would silently
      average two different pieces of cortex. Pass index_pairs=True to accept
      that assumption anyway, or use component_groups() for a homology-safe
      cortical grouping.

    Base matching is case-insensitive, because HCPex ships one genuine pair
    spelled inconsistently ('Posterior_OFC_Complex_L' / 'posterior_OFC_Complex_R').
    """
    by_base, display = OrderedDict(), {}
    for name in names:
        base, hemi = split_hemisphere(name)
        if hemi is None:
            continue
        key = base.lower()
        by_base.setdefault(key, []).append(name)
        display.setdefault(key, base)

    asymmetric_stems = _asymmetric_index_stems(names)

    combine, skipped_index, skipped_stems = OrderedDict(), [], set()
    for key, members in by_base.items():
        if len(members) < 2:
            continue
        base = display[key]
        m = INDEX_SUFFIX_RE.match(base)
        # An index-numbered base is only paired when its stem carries the SAME
        # index set in both hemispheres. That holds for symmetric parcellations
        # (HCP-MMP: 180 areas mirrored) and fails for Schaefer-400, where the
        # index is a within-hemisphere counter.
        if m and not index_pairs and m.group("stem").lower() in asymmetric_stems:
            skipped_index.append(base)
            skipped_stems.add(m.group("stem"))
            continue
        combine[base] = members

    notes = []
    source = "lr (automatic left/right pairs)"
    if skipped_index:
        notes.append(
            f"{len(skipped_index)} index-numbered parcel(s) were NOT paired, from "
            f"{len(skipped_stems)} group(s) whose index sets differ between hemispheres "
            f"(e.g. {', '.join(sorted(skipped_stems)[:3])}): there the index is a "
            "within-hemisphere counter, so parcel N left is not the homologue of parcel N "
            "right. Use 'lr-index' to pair them anyway, or 'component' to merge whole "
            "components instead.")
    if index_pairs:
        source = "lr-index (left/right pairs, including index-numbered parcels)"
        if asymmetric_stems:
            notes.append(
                f"Index-numbered parcels were paired by their number, including "
                f"{len(asymmetric_stems)} group(s) whose two hemispheres hold different "
                "index sets. For those, parcel N left is NOT the homologue of parcel N "
                "right -- this grouping asserts a correspondence the parcellation does "
                "not define.")
    return Grouping(combine, keep_ungrouped=keep_ungrouped, source=source, notes=notes,
                    spec="lr-index" if index_pairs else "lr")


def component_groups(names, keep_ungrouped=True, bilateral=True):
    """Homology-safe grouping for index-numbered parcellations: merge every
    parcel sharing a stem, dropping the index.

    For Schaefer-400 this yields one region per network component --
    '7Networks_Vis' from all LH_Vis_* and RH_Vis_* parcels -- which is defined
    by the parcellation itself rather than by an index correspondence that does
    not hold. With bilateral=False the hemispheres stay separate
    ('7Networks_LH_Vis'), which is the safer choice when hemispheric asymmetry
    is part of the question.
    """
    by_stem, display = OrderedDict(), {}
    for name in names:
        base, hemi = split_hemisphere(name)
        target = base if (bilateral and hemi is not None) else name
        m = INDEX_SUFFIX_RE.match(target)
        if not m:
            continue
        stem = m.group("stem")
        key = stem.lower()
        by_stem.setdefault(key, []).append(name)
        display.setdefault(key, stem)

    combine = OrderedDict((display[k], v) for k, v in by_stem.items() if len(v) > 1)
    return Grouping(
        combine, keep_ungrouped=keep_ungrouped,
        source=f"component ({'bilateral' if bilateral else 'per hemisphere'} network components)",
        notes=["Parcels were merged by network component, not by index correspondence."],
        spec="component" if bilateral else "component-hemi")


# Every script defaults to this: a run produces the per-parcel results AND the
# left/right-combined copies, because wanting both is the common case and the
# combined files are written alongside, never instead of, the per-parcel ones.
# Pass 'none' to skip the extra pass.
DEFAULT_SPEC = "lr"

BUILTIN_GROUPINGS = {
    "lr": lambda names: auto_bilateral(names),
    "lr-index": lambda names: auto_bilateral(names, index_pairs=True),
    "component": lambda names: component_groups(names, bilateral=True),
    "component-hemi": lambda names: component_groups(names, bilateral=False),
}

BUILTIN_HELP = (
    "none | lr (left/right pairs) | lr-index (also pair index-numbered parcels -- "
    "assumes cross-hemisphere index homology) | component (merge network components, "
    "both hemispheres) | component-hemi (merge network components, hemispheres kept "
    "separate) | PATH to a grouping JSON"
)


def load(spec, names=None, keep_ungrouped=None):
    """spec: None/'none' -> None; a built-in keyword; else a JSON file path.

    `names` is required for 'lr' (the pairs come from the parcel list itself).
    Raises ValueError with a readable message; callers decide whether that is a
    sys.exit or an HTTP 400.
    """
    if spec is None or (isinstance(spec, str) and spec.strip().lower() in ("", "none")):
        return None
    if isinstance(spec, Grouping):
        return spec
    if isinstance(spec, dict):
        return from_dict(spec, source="inline")
    keyword = spec.strip().lower() if isinstance(spec, str) else None
    if keyword in BUILTIN_GROUPINGS:
        if names is None:
            raise ValueError(f"the '{keyword}' grouping needs the parcel list to build itself")
        builder = BUILTIN_GROUPINGS[keyword]
        grouping = builder(names)
        if keep_ungrouped is not None:
            grouping.keep_ungrouped = keep_ungrouped
        return grouping

    path = Path(spec).expanduser()
    if not path.is_file():
        raise ValueError(f"grouping '{spec}' is neither 'lr', 'none', nor an existing file")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    grouping = from_dict(data, source=str(path))
    grouping.spec = str(path)
    if keep_ungrouped is not None:
        grouping.keep_ungrouped = keep_ungrouped
    return grouping


def validate_spec(spec):
    """Check a grouping spec WITHOUT a parcel list, for use at startup.

    The built-in groupings can only be built once the parcel names are known,
    which in a batch driver is after the first session's extraction -- far too
    late to discover a typo. This checks everything that can be checked early:
    the keyword is known, or the file exists and parses. Returns a short
    description for the log; raises ValueError on anything unusable.
    """
    if spec is None or (isinstance(spec, str) and spec.strip().lower() in ("", "none")):
        return "none (per-parcel results only)"
    if isinstance(spec, Grouping):
        return spec.source
    if isinstance(spec, str) and spec.strip().lower() in BUILTIN_GROUPINGS:
        return spec.strip().lower()
    path = Path(str(spec)).expanduser()
    if not path.is_file():
        raise ValueError(
            f"'{spec}' is not one of {sorted(BUILTIN_GROUPINGS)}, 'none', or an existing file")
    grouping = load(path)          # parses and type-checks the JSON
    return f"{path} ({grouping.n_groups} group(s))"


def for_names(spec, names):
    """Resolve a grouping against a specific parcel list.

    A built-in keyword means different things in different name spaces -- 'lr'
    over Schaefer parcels is not the same set of pairs as 'lr' over wmparc
    labels -- so it is rebuilt from `names`. An explicit grouping (file or
    inline) already names its members, and resolve() drops the ones absent
    here, so it is reused unchanged.
    """
    if isinstance(spec, Grouping):
        if spec.spec in BUILTIN_GROUPINGS:
            return BUILTIN_GROUPINGS[spec.spec](names)
        return spec
    return load(spec, names)


def combined_suffix(grouping):
    """Filename tag for a grouped run, so combined output never overwrites the
    per-parcel output it was derived from."""
    return "combined" if grouping else ""


def from_dict(data, source="custom"):
    if not isinstance(data, dict) or "combine" not in data:
        raise ValueError("a grouping file must be an object with a 'combine' key")
    combine = data["combine"]
    if not isinstance(combine, dict):
        raise ValueError("'combine' must be an object of {combined_name: [member parcels]}")
    cleaned = OrderedDict()
    for key, members in combine.items():
        if isinstance(members, str):
            members = [members]
        if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
            raise ValueError(f"group '{key}': members must be a list of parcel names")
        cleaned[key] = members
    return Grouping(cleaned, keep_ungrouped=bool(data.get("keep_ungrouped", True)), source=source,
                    notes=list(data.get("notes") or []), spec=source)


# ------------------------------------------------------------- aggregation

def combine_timeseries(ts, names, grouping):
    """(T x N) timeseries -> (T x M) with each composite the sample-by-sample
    mean of its members. Same operation as build_triangle_ts, generalised."""
    if not grouping:
        return ts, list(names)
    index = {n: i for i, n in enumerate(names)}
    columns, out_names = [], []
    for combined, members in grouping.resolve(names):
        cols = [index[m] for m in members]
        columns.append(ts[:, cols].mean(axis=1) if len(cols) > 1 else ts[:, cols[0]])
        out_names.append(combined)
    return np.stack(columns, axis=1), out_names


def combine_values(values, grouping, rule="sum", weights=None):
    """values: {name: number} or [(name, number)]. Returns [(name, number)].

    rule: 'sum' for volumes/counts, 'mean' for a plain average, 'weighted_mean'
    for per-parcel means that need their sample sizes (pass `weights` as
    {name: vertex_count}).
    """
    if isinstance(values, dict):
        items = list(values.items())
    else:
        items = list(values)
    lookup = dict(items)
    names = [n for n, _v in items]
    if not grouping:
        return items

    if rule == "weighted_mean" and not weights:
        raise ValueError("rule='weighted_mean' needs weights={name: count}")

    out = []
    for combined, members in grouping.resolve(names):
        vals = [lookup[m] for m in members if lookup.get(m) is not None]
        if not vals:
            out.append((combined, None))
            continue
        if len(vals) == 1:
            out.append((combined, vals[0]))
        elif rule == "sum":
            out.append((combined, float(sum(vals))))
        elif rule == "mean":
            out.append((combined, float(sum(vals)) / len(vals)))
        elif rule == "weighted_mean":
            ws = [float(weights.get(m, 0)) for m in members if lookup.get(m) is not None]
            total = sum(ws)
            if total <= 0:  # no vertex counts available -- fall back, and say so
                out.append((combined, float(sum(vals)) / len(vals)))
            else:
                out.append((combined, float(sum(v * w for v, w in zip(vals, ws)) / total)))
        else:
            raise ValueError(f"unknown combining rule '{rule}'")
    return out


def combine_weights(weights, grouping):
    """Weights (vertex/voxel counts) always sum when their parcels merge."""
    return dict(combine_values(weights, grouping, rule="sum"))


# ---------------------------------------------------------- interactive use

def order_by_region_pairs(selected_names):
    """Reorder a selection so each region's left and right sit together, left
    first: Hippocampus_L, Hippocampus_R, SNc_L, SNc_R, ...

    Without this the matrix follows HCPex label order, which puts every left
    parcel first and every right parcel 300+ labels later (Hippocampus_L is 80,
    Hippocampus_R is 260) -- so a region's two halves land at opposite corners
    of the heatmap and cannot be compared by eye.

    Regions appear in the order they were FIRST selected, so the user's own
    ordering still drives the layout. A region with only one hemisphere picked
    keeps its place; an unlateralised structure (Brain-Stem) does too."""
    order, groups = [], {}
    for name in selected_names:
        base, hemi = split_hemisphere(name)
        key = base.lower() if hemi else f"\0{name}"  # HCPex ships one case-mismatched pair
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((hemi, name))

    out = []
    for key in order:
        members = groups[key]
        # L before R; anything unlateralised keeps its relative order after them
        out.extend(n for h, n in sorted(
            members, key=lambda hn: {"L": 0, "R": 1}.get(hn[0], 2)))
    return out


def is_plain_lr(grouping):
    """True when a Grouping (or spec) is just the built-in left/right rule.

    Callers write the L/R-combined view unconditionally, so a user-supplied
    grouping that IS plain lr would only duplicate it. Matches on the first
    token of Grouping.source, which reads 'lr (automatic left/right pairs)'
    rather than a bare 'lr'. 'lr-index' is deliberately NOT plain lr -- it
    asserts index pairings the atlas does not define."""
    if grouping is None:
        return False
    source = str(getattr(grouping, "source", grouping)).strip().lower()
    return source.split()[0] == "lr" if source else False


def prompt_grouping(names, prompt_fn=input):
    """Terminal picker used by the interactive scripts. Returns a Grouping or
    None. Kept here so every script asks the question the same way."""
    auto = auto_bilateral(names)
    auto_idx = auto_bilateral(names, index_pairs=True)
    comp = component_groups(names)

    print(f"\nAlso write left/right-combined copies? ({len(names)} parcels available)")
    print(f"  The per-parcel results are written either way -- this only adds a second,")
    print(f"  combined set of outputs beside them.")
    print(f"  [l] left/right pairs (DEFAULT)     -> {len(auto.resolve(names))} regions "
          f"({auto.n_groups} pairs merged)")
    print(f"  [n] none -- per-parcel results only")
    if auto_idx.n_groups > auto.n_groups:
        print(f"  [i] left/right incl. index-numbered -> {len(auto_idx.resolve(names))} regions "
              f"({auto_idx.n_groups} pairs merged)  [assumes index homology]")
    if comp.n_groups:
        print(f"  [c] network components (both hemispheres) -> {len(comp.resolve(names))} regions "
              f"({comp.n_groups} components merged)")
    print(f"  [f] load a grouping file (JSON)")
    for note in auto.notes:
        print(f"  note: {note}")

    while True:
        choice = (prompt_fn("Choice [l]: ") or "l").strip().lower()
        if choice in ("n", "no", "none"):
            return None
        chosen = None
        if choice in ("", "l", "lr"):
            chosen = auto
        elif choice in ("i", "lr-index"):
            chosen = auto_idx
        elif choice in ("c", "component"):
            chosen = comp
        elif choice in ("f", "file"):
            path = (prompt_fn("  Path to grouping JSON: ") or "").strip()
            try:
                chosen = load(path, names)
            except ValueError as exc:
                print(f"  {exc}")
                continue
        if chosen is None:
            print("  Enter n, l, i, c or f.")
            continue
        if not chosen.n_groups:
            print("  That grouping merges nothing in this parcel list, try again.")
            continue
        errors, warnings = chosen.validate(names)
        for w in warnings:
            print(f"  [warning] {w}")
        if errors:
            for e in errors:
                print(f"  [error] {e}")
            continue
        print(f"  -> {chosen.source}: {len(names)} parcels become "
              f"{len(chosen.resolve(names))} regions")
        return chosen
