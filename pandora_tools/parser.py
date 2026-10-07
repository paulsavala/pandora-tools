"""Parse Pandonia Global Network (PGN) Pandora L2 text files.

A PGN L2 file has four parts:

1. ``key: value`` metadata lines (station, instrument, version, ...)
2. a dashed separator line
3. ``Column N: description [units], code=meaning, ...`` lines, possibly ending
   with ``From Column N: Optional results for higher layers ... (k columns per layer)``
4. a dashed separator line, then whitespace-delimited data rows

Column *numbers* differ between products (and between processing versions), but
the column *descriptions* are standardized. So columns are identified by their
description and given stable short names (e.g. ``no2_trop_col``) no matter where
they sit in the file.

Rows can have different lengths: profile products append a variable number of
vertical layers to each row. The fixed columns go into the main table and the
layers go into a separate long-format profile table.
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd

SEPARATOR_RE = re.compile(r"^-{20,}\s*$")

# Species names as they appear in PGN column descriptions -> short abbreviations
SPECIES = {
    "formaldehyde": "hcho",
    "nitrogen dioxide": "no2",
    "water vapor": "h2o",
    "ozone": "o3",
    "sulfur dioxide": "so2",
    "bromine monoxide": "bro",
    "glyoxal": "chocho",
    "nitrous acid": "hono",
    "iodine monoxide": "io",
    "chlorine dioxide": "oclo",
    "oxygen dimer": "o2o2",
}

# Product codes we have seen, with a plain-language description. They live in
# data/products.csv so anyone can add one in a spreadsheet editor.
# Unknown codes still work; they just have no description.
PRODUCTS_FILE = Path(__file__).parent / "data" / "products.csv"


def load_known_products(path: Union[str, os.PathLike] = PRODUCTS_FILE) -> Dict[str, str]:
    """Read the product-code descriptions table (``product,description`` CSV).

    Returns a dict such as ``{"rnvh3": "Nitrogen dioxide + water vapor, sky scans ..."}``.
    Raises ``ValueError`` naming the bad line if the file is malformed.
    """
    products: Dict[str, str] = {}
    # utf-8-sig: Excel adds a byte-order mark when saving as CSV
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = csv.reader(f)
        header = [h.strip().lower() for h in next(rows, [])]
        if header != ["product", "description"]:
            raise ValueError(f"{path}: the first line must be exactly 'product,description'.")
        for row in rows:
            line = rows.line_num
            cells = [c.strip() for c in row]
            if not any(cells):
                continue  # blank line
            if len(cells) != 2:
                raise ValueError(
                    f"{path}, line {line}: expected 2 cells (product, description), got {len(cells)}. "
                    "Put descriptions that contain commas in double quotes."
                )
            code, desc = cells
            if not re.fullmatch(r"[a-z]+\d+", code):
                raise ValueError(
                    f"{path}, line {line}: product code {code!r} should look like 'rnvh3' "
                    "(lowercase letters, then digits)."
                )
            if not desc:
                raise ValueError(f"{path}, line {line}: product {code!r} has no description.")
            if code in products:
                raise ValueError(f"{path}, line {line}: product {code!r} is listed twice.")
            products[code] = desc
    return products


KNOWN_PRODUCTS = load_known_products()

STATION_COLUMNS = [
    "location", "instrument", "spectrometer", "product",
    "latitude", "longitude", "altitude", "source_file",
]

_UNC_KIND = {
    "independent": "indep",
    "structured": "struct",
    "common": "common",
    "total": "total",
    "rms-based": "rms",
}

# (regex matched against the column label, name template). Order matters:
# more specific patterns come first. ``{sp}`` is the species abbreviation.
_NAME_RULES: List[Tuple[str, str]] = [
    (r"UT date and time for measurement center", "utc_time"),
    (r"Fractional days since 1-Jan-2000 UT midnight for measurement center", "days_since_2000"),
    (r"Effective duration of measurement", "duration"),
    (r"Solar zenith angle for measurement center", "sza"),
    (r"Solar azimuth for measurement center", "saa"),
    (r"Lunar zenith angle for measurement center", "lza"),
    (r"Lunar azimuth for measurement center", "laa"),
    (r"Pointing zenith angle for measurement center", "pza"),
    (r"Pointing azimuth for measurement center", "paa"),
    (r"rms of unweighted fitting residuals", "rms"),
    (r"Normalized rms of fitting residuals weighted with independent uncertainty", "rms_weighted_norm"),
    (r"Expected rms of unweighted fitting residuals based on independent uncertainty", "rms_expected"),
    (r"Expected normalized rms of weighted fitting residuals based on independent uncertainty",
     "rms_weighted_norm_expected"),
    (r"Climatological station pressure", "clim_pressure"),
    (r"Climatological station temperature", "clim_temperature"),
    (r"Climatological effective (?P<x>\w+) height", "clim_{x}_eff_height"),
    (r"Climatological surface (?P<x>\w+) concentration", "clim_{x}_surf_conc"),
    (r"Climatological total (?P<x>\w+) column", "clim_{x}_total_col"),
    (r"Data processing type index", "processing_type"),
    (r"Calibration file version", "calib_version"),
    (r"Calibration file validity starting date", "calib_start_date"),
    (r"Mean value of measured data inside fitting window", "mean_signal"),
    (r"Wavelength effective temperature", "wavelength_eff_temp"),
    (r"Estimated average residual stray light level", "stray_light"),
    (r"Retrieved wavelength shift from L1 data", "wl_shift_l1"),
    (r"Retrieved total wavelength shift", "wl_shift_total"),
    (r"Retrieved resolution change", "resolution_change"),
    (r"Integration time", "integration_time"),
    (r"Number of bright count cycles", "n_bright_cycles"),
    (r"Effective position of filterwheel #(?P<x>\d+)", "filterwheel_{x}"),
    (r"Atmospheric variability", "atm_variability"),
    (r"Estimated aerosol optical depth at (?P<x>starting|center|ending) wavelength of fitting window",
     "aod_{x}"),
    # quality flags
    (r"L1 data quality flag", "l1_qa"),
    (r"L2Fit data quality flag", "l2fit_qa"),
    (r"L2 data quality flag for (?P<sp>.+)", "{sp}_qa"),
    (r"Sum over 2\^i using those i, for which the corresponding L1 data quality parameter "
     r"exceeds the (?P<dq>DQ\d) limit", "l1_{dq}_bits"),
    (r"Sum over 2\^i using those i, for which the corresponding L2Fit data quality parameter "
     r"exceeds the (?P<dq>DQ\d) limit", "l2fit_{dq}_bits"),
    (r"Sum over 2\^i using those i, for which the corresponding L2 data quality parameter for "
     r"(?P<sp>.+?) exceeds the (?P<dq>DQ\d) limit", "{sp}_{dq}_bits"),
    # surface concentration
    (r"Independent uncertainty of (?P<sp>.+) surface concentration", "{sp}_surf_conc_unc"),
    (r"(?P<sp>.+) surface concentration index", "{sp}_surf_conc_index"),
    (r"(?P<sp>.+) surface concentration", "{sp}_surf_conc"),
    (r"(?P<sp>.+) heterogeneity flag", "{sp}_heterogeneity"),
    # tropospheric column
    (r"Independent uncertainty of (?P<sp>.+) tropospheric vertical column amount", "{sp}_trop_col_unc"),
    (r"Maximum horizontal distance for (?P<sp>.+) tropospheric column", "{sp}_trop_col_max_hdist"),
    (r"Maximum vertical distance for (?P<sp>.+) tropospheric column", "{sp}_trop_col_max_vdist"),
    (r"(?P<sp>.+) tropospheric vertical column amount", "{sp}_trop_col"),
    # stratospheric climatology
    (r"Uncertainty of climatological (?P<sp>.+) stratospheric column amount", "{sp}_strat_col_clim_unc"),
    (r"Climatological (?P<sp>.+) stratospheric column amount", "{sp}_strat_col_clim"),
    # total column (direct sun)
    (r"(?P<u>Independent|Structured|Common|Total|rms-based) uncertainty of (?P<sp>.+) total vertical "
     r"column amount", "{sp}_total_col_unc_{u}"),
    (r"(?P<sp>.+) total vertical column amount", "{sp}_total_col"),
    (r"(?P<u>Independent|Structured|Common|Total|rms-based) uncertainty of (?P<sp>.+) effective "
     r"temperature", "{sp}_eff_temp_unc_{u}"),
    (r"(?P<sp>.+) effective temperature", "{sp}_eff_temp"),
    (r"Uncertainty of direct (?P<sp>.+) air mass factor", "{sp}_amf_unc"),
    (r"Direct (?P<sp>.+) air mass factor", "{sp}_amf"),
    (r"Diffuse correction applied before fitting at effective fitting wavelength for (?P<sp>.+)",
     "{sp}_diffuse_corr"),
    # profile layers (layer 1 defines the per-layer block)
    (r"Top height of (?P<sp>.+) layer 1", "{sp}_layer_top"),
    (r"Partial (?P<sp>.+) vertical column amount in layer 1", "{sp}_layer_col"),
]
_NAME_RULES_COMPILED = [(re.compile(p, re.IGNORECASE), t) for p, t in _NAME_RULES]

_CODE_RE = re.compile(r"(?:^|,)\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)=")
_LABEL_CUT_RE = re.compile(r",\s*-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?=")
_CATEGORICAL_RE = re.compile(
    r"\bindex\b|heterogeneity flag|filterwheel|calibration file version|validity starting date",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text: str) -> str:
    text = text.lower()
    for full, abbr in SPECIES.items():
        text = text.replace(full, abbr)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text[:48].rstrip("_")


def species_abbr(text: Optional[str]) -> Optional[str]:
    """Map a species name ('Nitrogen dioxide', 'NO2', 'no2') to its abbreviation."""
    if text is None:
        return None
    t = text.strip().lower()
    if t in SPECIES:
        return SPECIES[t]
    if t in SPECIES.values():
        return t
    # unknown species: accept a short plain phrase only
    words = t.split()
    if 1 <= len(words) <= 3 and re.fullmatch(r"[a-z0-9 \-]+", t) and not (
        {"uncertainty", "of", "for", "the", "in"} & set(words)
    ):
        return slugify(t)
    return None


def parse_product_code(version: str) -> Dict[str, Optional[str]]:
    """Split a data file version such as 'rnvh3c2d20230110p1-8' or 'rfuh5p1-8'."""
    m = re.match(r"^(?P<code>[a-z]+\d+)(?:c(?P<cf>\d+)d(?P<cfdate>\d{8}))?(?P<proc>p\d+-\d+)?", version or "")
    if not m:
        return {"code": None, "cf_version": None, "cf_date": None, "processing": None}
    return {"code": m["code"], "cf_version": m["cf"], "cf_date": m["cfdate"], "processing": m["proc"]}


@dataclass
class ColumnInfo:
    number: int
    name: str
    label: str
    description: str
    units: Optional[str]
    kind: str  # time | numeric | flag | bitmask | categorical
    species: Optional[str]
    codes: Dict[float, str] = field(default_factory=dict)
    missing_codes: List[float] = field(default_factory=list)
    is_layer: bool = False


def _split_description(desc: str) -> Tuple[str, Optional[str], str]:
    """Return (label, units, rest) for a column description."""
    units = None
    m_units = re.search(r"\[([^\]]*)\]", desc)
    m_code = _LABEL_CUT_RE.search(desc)
    cut = len(desc)
    if m_units:
        cut = min(cut, m_units.start())
        units = m_units.group(1).strip()
    if m_code:
        cut = min(cut, m_code.start())
    label = desc[:cut].strip().rstrip(",").strip()
    # drop a trailing parenthetical note, e.g. "(same parameters as for DQ1)"
    label = re.sub(r"\s*\([^)]*\)\s*$", "", label)
    rest = desc[cut:]
    return label, units, rest


def _parse_codes(rest: str) -> Dict[float, str]:
    codes: Dict[float, str] = {}
    matches = list(_CODE_RE.finditer(rest))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(rest)
        meaning = rest[m.end():end].strip().strip(",").strip()
        try:
            codes[float(m.group(1))] = meaning
        except ValueError:
            pass
    return codes


def _name_for(label: str) -> Tuple[str, Optional[str]]:
    """Return (short_name, species_abbr) for a column label."""
    for rx, template in _NAME_RULES_COMPILED:
        m = rx.fullmatch(label)
        if not m:
            continue
        groups = m.groupdict()
        sp = None
        if "sp" in groups:
            sp = species_abbr(groups["sp"])
            if sp is None:
                continue
        fill = {"sp": sp}
        if groups.get("x"):
            x = groups["x"].lower()
            fill["x"] = {"starting": "start", "ending": "end"}.get(x, x)
        if groups.get("u"):
            fill["u"] = _UNC_KIND[groups["u"].lower()]
        if groups.get("dq"):
            fill["dq"] = groups["dq"].lower()
        return template.format(**fill), sp
    # fallback: slug of the label; try to detect a species mentioned in it
    sp = next((abbr for full, abbr in SPECIES.items() if full in label.lower()), None)
    return slugify(label) or "column", sp


def parse_column_line(number: int, desc: str) -> ColumnInfo:
    label, units, rest = _split_description(desc)
    name, sp = ("utc_time", None) if number == 1 else _name_for(label)
    codes = _parse_codes(rest)

    if number == 1:
        kind = "time"
    elif label.lower().startswith("sum over 2^i"):
        kind = "bitmask"
    elif "data quality flag" in label.lower():
        kind = "flag"
    elif _CATEGORICAL_RE.search(label):
        kind = "categorical"
    else:
        kind = "numeric"

    # Sentinel ("missing") codes: only for numeric measurement columns, and only
    # negative codes or |code| >= 999. Codes like '0=north' are real values.
    missing = []
    if kind == "numeric":
        missing = sorted(c for c in codes if c < 0 or abs(c) >= 999)
    elif kind == "categorical":
        # e.g. '-6=no surface concentration was retrieved ...' in index/heterogeneity columns
        missing = sorted(c for c in codes if c < 0)

    return ColumnInfo(
        number=number, name=name, label=label, description=desc, units=units,
        kind=kind, species=sp, codes=codes, missing_codes=missing,
        is_layer=bool(re.search(r"\blayer 1\b", label)),
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

@dataclass
class ParsedHeader:
    meta: Dict[str, str]
    columns: List[ColumnInfo]
    layer_start: Optional[int]       # 1-based column number where layer 2 starts
    layer_width: Optional[int]       # columns per layer
    data_start_line: int             # 0-based line index of first data row


def parse_header(lines: List[str]) -> ParsedHeader:
    seps = [i for i, line in enumerate(lines[:2000]) if SEPARATOR_RE.match(line)]
    if len(seps) < 2:
        raise ValueError(
            "This does not look like a PGN L2 file: expected two dashed separator lines "
            "(metadata / column descriptions / data)."
        )
    s1, s2 = seps[0], seps[1]

    meta: Dict[str, str] = {}
    for line in lines[:s1]:
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()

    columns: List[ColumnInfo] = []
    layer_start = layer_width = None
    for line in lines[s1 + 1:s2]:
        line = line.rstrip("\n")
        m = re.match(r"^Column (\d+):\s*(.*)$", line)
        if m:
            columns.append(parse_column_line(int(m.group(1)), m.group(2).strip()))
            continue
        m = re.match(r"^From Column (\d+):.*\((\d+) columns? per layer\)", line)
        if m:
            layer_start, layer_width = int(m.group(1)), int(m.group(2))

    # "(same parameters as for DQ1)" bitmask columns inherit the bit meanings
    last_bits: Dict[float, str] = {}
    for c in columns:
        if c.kind == "bitmask":
            if c.codes:
                last_bits = c.codes
            elif "same parameters" in c.description.lower():
                c.codes = dict(last_bits)

    # de-duplicate names
    seen: Dict[str, int] = {}
    for c in columns:
        if c.name in seen:
            seen[c.name] += 1
            c.name = f"{c.name}_{seen[c.name]}"
        else:
            seen[c.name] = 1

    return ParsedHeader(meta=meta, columns=columns, layer_start=layer_start,
                        layer_width=layer_width, data_start_line=s2 + 1)


def catalog_frame(columns: List[ColumnInfo]) -> pd.DataFrame:
    """Column catalog as a DataFrame (one row per column in the file)."""
    return pd.DataFrame(
        {
            "name": [c.name for c in columns],
            "column_number": [c.number for c in columns],
            "label": [c.label for c in columns],
            "units": [c.units for c in columns],
            "kind": [c.kind for c in columns],
            "species": [c.species for c in columns],
            "missing_codes": [c.missing_codes for c in columns],
            "codes": [c.codes for c in columns],
            "in_profile_table": [c.is_layer for c in columns],
            "description": [c.description for c in columns],
        }
    )


# ---------------------------------------------------------------------------
# Station metadata
# ---------------------------------------------------------------------------

def _to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def _to_int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def station_info(meta: Dict[str, str], filename: str) -> Dict[str, object]:
    version = meta.get("Data file version", "")
    prod = parse_product_code(version)
    instrument = _to_int(meta.get("Instrument number"))
    spectrometer = _to_int(meta.get("Spectrometer number"))
    location = meta.get("Short location name")
    # fall back to the filename: Pandora257s1_AustinTX_..._L2_<version>...
    m = re.match(r"Pandora(\d+)s(\d+)_([^_]+)_", os.path.basename(filename))
    if m:
        instrument = instrument if instrument is not None else int(m.group(1))
        spectrometer = spectrometer if spectrometer is not None else int(m.group(2))
        location = location or m.group(3)
    return {
        "location": location,
        "location_long_name": meta.get("Full location name"),
        "instrument": instrument,
        "spectrometer": spectrometer,
        "product": prod["code"],
        "product_description": KNOWN_PRODUCTS.get(prod["code"] or "", ""),
        "data_version": version,
        "processing_version": prod["processing"],
        "calibration_version": prod["cf_version"],
        "latitude": _to_float(meta.get("Location latitude [deg]")),
        "longitude": _to_float(meta.get("Location longitude [deg]")),
        "altitude": _to_float(meta.get("Location altitude [m]")),
        "doi": meta.get("DOI"),
        "file_generated": meta.get("File generation date"),
        "source_file": os.path.basename(filename),
    }


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _parse_times(s: pd.Series) -> pd.Series:
    t = pd.to_datetime(s, format="%Y%m%dT%H%M%S.%fZ", utc=True, errors="coerce")
    bad = t.isna() & s.notna()
    if bad.any():
        t2 = pd.to_datetime(s[bad], format="%Y%m%dT%H%M%SZ", utc=True, errors="coerce")
        t = t.copy()
        t[bad] = t2
    return t.astype("datetime64[ns, UTC]")


@dataclass
class ParsedFile:
    data: pd.DataFrame
    profiles: Optional[pd.DataFrame]
    header: ParsedHeader
    station: Dict[str, object]
    catalog: pd.DataFrame
    n_masked: Dict[str, int]


def parse_file(
    source: Union[str, os.PathLike, bytes],
    name: Optional[str] = None,
    tz: Optional[str] = None,
    mask_missing: bool = True,
) -> ParsedFile:
    """Parse a PGN L2 file into tables. Most users want :func:`pandora_tools.read`."""
    if isinstance(source, (bytes, bytearray)):
        text = _decode(bytes(source))
        filename = name or "<memory>"
    else:
        path = Path(source)
        text = _decode(path.read_bytes())
        filename = name or path.name

    lines = text.splitlines()
    header = parse_header(lines)
    station = station_info(header.meta, filename)

    data_lines = [ln for ln in lines[header.data_start_line:] if ln.strip()]
    columns = header.columns

    # Fixed columns are everything before the per-layer block.
    layer_cols = [c for c in columns if c.is_layer]
    if layer_cols:
        n_fixed = min(c.number for c in layer_cols) - 1
        layer_width = header.layer_width or len(layer_cols)
    else:
        n_fixed = len(columns)
        layer_width = 0

    widths = [len(ln.split()) for ln in data_lines]
    max_w = max(widths + [len(columns)]) if data_lines else len(columns)

    if data_lines:
        raw = pd.read_csv(
            io.StringIO("\n".join(data_lines)),
            sep=r"\s+", header=None, names=list(range(max_w)),
            dtype={0: str}, engine="c",
        )
    else:
        raw = pd.DataFrame(columns=list(range(max_w)))

    # ---- main table --------------------------------------------------------
    fixed_cols = [c for c in columns if c.number <= n_fixed]
    out = {}
    n_masked: Dict[str, int] = {}
    for c in fixed_cols:
        col = raw[c.number - 1]
        if c.kind == "time":
            out["utc_time"] = _parse_times(col.astype("string"))
            continue
        vals = pd.to_numeric(col, errors="coerce")
        if mask_missing and c.missing_codes:
            mask = vals.isin(c.missing_codes)
            n_masked[c.name] = int(mask.sum())
            vals = vals.mask(mask)
        if c.kind in ("flag", "bitmask", "categorical") and vals.notna().all() and len(vals):
            if (vals == vals.round()).all():
                vals = vals.astype("int64")
        out[c.name] = vals
    data = pd.DataFrame(out)
    if tz:
        data.insert(1, "local_time", data["utc_time"].dt.tz_convert(tz))

    for key in STATION_COLUMNS:
        data[key] = station[key]

    # ---- profile table -----------------------------------------------------
    profiles = None
    if layer_cols and layer_width:
        profiles = _build_profiles(raw, data, layer_cols, n_fixed, layer_width, max_w, mask_missing)

    return ParsedFile(
        data=data, profiles=profiles, header=header, station=station,
        catalog=catalog_frame(columns), n_masked=n_masked,
    )


def _build_profiles(raw, data, layer_cols, n_fixed, width, max_w, mask_missing) -> pd.DataFrame:
    n_layers = (max_w - n_fixed) // width
    if n_layers <= 0:
        return None
    block = raw.iloc[:, n_fixed:n_fixed + n_layers * width].apply(pd.to_numeric, errors="coerce")
    arr = np.array(block.to_numpy(dtype=float), copy=True).reshape(len(raw), n_layers, width)

    # group layer columns by species: each species has a top-height and a partial column
    by_species: Dict[str, Dict[str, int]] = {}
    for j, c in enumerate(sorted(layer_cols, key=lambda c: c.number)):
        q = "top_height" if c.name.endswith("_layer_top") else (
            "partial_col" if c.name.endswith("_layer_col") else c.name)
        by_species.setdefault(c.species or "unknown", {})[q] = j
        if mask_missing and c.missing_codes:
            sl = arr[:, :, j]
            sl[np.isin(sl, c.missing_codes)] = np.nan

    keys = data[["utc_time", "location", "instrument", "spectrometer", "product"]]
    frames = []
    for sp, qmap in by_species.items():
        present = ~np.all(np.isnan(arr[:, :, list(qmap.values())]), axis=2)  # (rows, layers)
        rows, layers = np.nonzero(present)
        if len(rows) == 0:
            continue
        f = keys.iloc[rows].reset_index(drop=True)
        f["species"] = sp
        f["layer"] = layers + 1
        for q, j in qmap.items():
            f[q] = arr[rows, layers, j]
        frames.append(f)
    if not frames:
        return None
    prof = pd.concat(frames, ignore_index=True)
    if "top_height" in prof:
        prof = prof.sort_values(["species", "utc_time", "layer"], kind="stable").reset_index(drop=True)
        prev = prof.groupby(["species", "utc_time", "location", "instrument"], sort=False)["top_height"].shift(1)
        prev = prev.where(prof["layer"] != 1, 0.0)  # layer 1 starts at the surface
        prof.insert(prof.columns.get_loc("top_height"), "bottom_height", prev)
    return prof
