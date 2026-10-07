"""PandoraFrame: a pandas DataFrame that also carries the PGN file metadata."""

from __future__ import annotations

import re
import warnings
from typing import Dict, Iterable, List, Optional, Sequence, Union

import numpy as np
import pandas as pd

from .parser import SPECIES, STATION_COLUMNS, species_abbr

# L2 quality flags: 0/1/2 = assured high/medium/low, 10/11/12 = not-assured
# high/medium/low, 20/21/22 = unusable high/medium/low.
QUALITY_LEVELS = {"high": 0, "medium": 1, "low": 2}
QUALITY_FLAG_MEANINGS = {
    0: "assured high quality", 1: "assured medium quality", 2: "assured low quality",
    10: "not-assured high quality", 11: "not-assured medium quality", 12: "not-assured low quality",
    20: "unusable high quality", 21: "unusable medium quality", 22: "unusable low quality",
}

JOIN_KEYS = ["location", "instrument", "spectrometer"]
PROFILE_KEYS = ["utc_time", "location", "instrument", "spectrometer"]


class PandoraFrame(pd.DataFrame):
    """A pandas DataFrame of Pandora L2 measurements, plus the file metadata.

    Everything that works on a DataFrame works here. On top of that:

    =====================  =====================================================
    ``df.location``        site short name (or list of names if several)
    ``df.stations``        one-row-per-station summary (site, instrument, time span)
    ``df.meta``            header metadata (``key: value`` lines of the file)
    ``df.headers``         header metadata for every source file, by filename
    ``df.columns_info``    catalog of columns: units, description, codes, ...
    ``df.column_info(c)``  full description of one column
    ``df.find_columns(s)`` search the catalog by text / regex
    ``df.profiles``        layer profiles (long format) for the rows in ``df``
    ``df.filter_quality``  keep rows by the L2 quality flag of a species
    ``df.decode_bits(c)``  explain a ``*_bits`` quality column
    =====================  =====================================================

    Slicing, filtering, ``.copy()``, ``.query()`` and similar keep the metadata.
    ``pd.concat`` / ``pd.merge`` do not; use :func:`pandora_tools.concat` and
    :func:`pandora_tools.merge_products` instead. (The per-row station columns
    survive either way, so plain pandas still gives correct data.)
    """

    _metadata = ["_headers", "_catalogs", "_profiles"]

    # class-level defaults so attribute access works on frames produced by
    # pandas operations that drop the metadata
    _headers: Optional[Dict[str, Dict[str, str]]] = None
    _catalogs: Optional[Dict[str, pd.DataFrame]] = None
    _profiles: Optional[pd.DataFrame] = None

    @property
    def _constructor(self):
        return PandoraFrame

    # ------------------------------------------------------------------ helpers
    def _attach(self, headers, catalogs, profiles) -> "PandoraFrame":
        object.__setattr__(self, "_headers", headers)
        object.__setattr__(self, "_catalogs", catalogs)
        object.__setattr__(self, "_profiles", profiles)
        return self

    def _require_meta(self):
        if not self._headers:
            raise AttributeError(
                "This frame has no Pandora metadata attached (it probably came from pd.concat / "
                "pd.merge). Use pandora_tools.concat(...) or pandora_tools.merge_products(...) to "
                "combine frames while keeping the metadata."
            )

    # --------------------------------------------------------------- metadata
    @property
    def headers(self) -> Dict[str, Dict[str, str]]:
        """Header metadata of every source file, keyed by filename."""
        self._require_meta()
        return dict(self._headers)

    @property
    def source_files(self) -> List[str]:
        return list(self._headers or {})

    @property
    def meta(self) -> Dict[str, object]:
        """Header metadata.

        For a single file: the file's ``key: value`` header as a dict. For several
        files: values that agree across files are plain values; values that differ
        become ``{filename: value}``.
        """
        self._require_meta()
        hs = self._headers
        if len(hs) == 1:
            return dict(next(iter(hs.values())))
        keys: List[str] = []
        for h in hs.values():
            keys += [k for k in h if k not in keys]
        out: Dict[str, object] = {}
        for k in keys:
            vals = {f: h.get(k) for f, h in hs.items()}
            uniq = set(vals.values())
            out[k] = uniq.pop() if len(uniq) == 1 else vals
        return out

    @property
    def location(self) -> Union[str, List[str], None]:
        """Site short name, e.g. 'AustinTX' (a list if the frame spans several sites).

        Note: ``df['location']`` is still the per-row column.
        """
        if "location" not in self.columns:
            return None
        locs = list(pd.unique(self["location"].dropna()))
        return locs[0] if len(locs) == 1 else locs

    @property
    def stations(self) -> pd.DataFrame:
        """Summary of the stations/products in this frame with their time span."""
        keys = [c for c in ["location", "instrument", "spectrometer", "product",
                            "latitude", "longitude", "altitude"] if c in self.columns]
        if not keys or "utc_time" not in self.columns:
            return pd.DataFrame()
        g = pd.DataFrame(self).groupby(keys, dropna=False)["utc_time"]
        return g.agg(first="min", last="max", n_rows="size").reset_index()

    @property
    def columns_info(self) -> pd.DataFrame:
        """Column catalog: short name, units, kind, species, codes, description.

        ``column_number`` (the position in the original file) is only shown when
        the frame comes from a single file, since it can differ between files.
        """
        self._require_meta()
        cats = self._catalogs or {}
        if len(cats) == 1:
            cat = next(iter(cats.values())).copy()
        else:
            cat = pd.concat(list(cats.values()), ignore_index=True)
            cat = cat.drop(columns="column_number").drop_duplicates("name").reset_index(drop=True)
        return cat

    def column_info(self, name: str) -> pd.Series:
        """Everything known about one column (units, description, codes, ...)."""
        cat = self.columns_info
        hit = cat[cat["name"] == name]
        if hit.empty:
            close = self.find_columns(name.replace("_", " "))
            hint = f" Similar: {list(close['name'])}" if len(close) else ""
            raise KeyError(f"No column named {name!r}.{hint}")
        return hit.iloc[0]

    def find_columns(self, pattern: str) -> pd.DataFrame:
        """Search column names and descriptions (case-insensitive regex).

        >>> df.find_columns("tropospheric")
        """
        cat = self.columns_info
        rx = re.compile(pattern, re.IGNORECASE)
        mask = cat["name"].str.contains(rx) | cat["description"].str.contains(rx)
        cols = [c for c in ["name", "column_number", "units", "label"] if c in cat]
        return cat.loc[mask, cols].reset_index(drop=True)

    @property
    def species(self) -> List[str]:
        """Species with an L2 quality flag in this frame, e.g. ['h2o', 'no2']."""
        return [c[:-3] for c in self.columns if c.endswith("_qa") and c not in ("l1_qa", "l2fit_qa")]

    # --------------------------------------------------------------- profiles
    @property
    def profiles(self) -> Optional[pd.DataFrame]:
        """Vertical layer profiles (long format) for the measurements in this frame.

        One row per (measurement, species, layer) with ``bottom_height``,
        ``top_height`` (km) and ``partial_col``. Rows are limited to the
        measurements still present in this frame, so filtering the frame (e.g.
        with ``filter_quality``) filters the profiles too.
        """
        prof = self._profiles
        if prof is None or prof.empty:
            return prof
        flat = pd.DataFrame(self)
        idx_names = [n for n in flat.index.names if n in PROFILE_KEYS and n not in flat.columns]
        if idx_names:
            flat = flat.reset_index(level=idx_names)
        if "utc_time" not in flat.columns:
            warnings.warn("This frame has no utc_time column or index level, so profiles cannot be "
                          "matched to its rows; returning all profiles.")
            return prof
        keys = [k for k in PROFILE_KEYS if k in flat.columns and k in prof.columns]
        present = pd.MultiIndex.from_frame(flat[keys].drop_duplicates())
        idx = pd.MultiIndex.from_frame(prof[keys])
        return prof[idx.isin(present)].reset_index(drop=True)

    # ---------------------------------------------------------------- quality
    def filter_quality(
        self,
        species: Optional[str] = None,
        keep: Sequence[str] = ("high", "medium"),
        assured_only: bool = False,
    ) -> "PandoraFrame":
        """Keep rows whose L2 quality flag for ``species`` is in ``keep``.

        Parameters
        ----------
        species : 'no2', 'hcho', 'h2o', 'nitrogen dioxide', ... Optional when the
            frame has only one species.
        keep : quality levels to keep, any of 'high', 'medium', 'low'.
        assured_only : if True keep only *assured* flags (0/1/2); otherwise
            not-assured ones (10/11/12) are kept too. 'Unusable' (20+) is never kept.

        The default (high + medium, assured or not) keeps flags 0, 1, 10, 11.
        """
        sp = self._resolve_species(species)
        col = f"{sp}_qa"
        bad = set(keep) - set(QUALITY_LEVELS)
        if bad:
            raise ValueError(f"Unknown quality level(s) {bad}; use 'high', 'medium', 'low'.")
        codes = [QUALITY_LEVELS[k] for k in keep]
        if not assured_only:
            codes += [c + 10 for c in codes]
        return self[self[col].isin(codes)]

    def _resolve_species(self, species: Optional[str]) -> str:
        available = self.species
        if species is None:
            if len(available) == 1:
                return available[0]
            raise ValueError(f"This frame has several species {available}; pass species=...")
        sp = species_abbr(species) or species.lower()
        if sp not in available:
            raise ValueError(f"No quality flag for {species!r} in this frame; available: {available}")
        return sp

    def decode_bits(self, column: str) -> pd.Series:
        """Translate a ``*_bits`` quality column into lists of reasons per row."""
        info = self.column_info(column)
        if info["kind"] != "bitmask":
            raise ValueError(f"{column!r} is not a bitmask column")
        meanings = {int(k): v for k, v in info["codes"].items()}

        def decode(v):
            if pd.isna(v):
                return []
            v = int(v)
            return [meanings.get(i, f"bit {i}") for i in range(v.bit_length()) if v >> i & 1]

        return self[column].map(decode).rename(f"{column}_reasons")

    # ------------------------------------------------------------------- time
    def with_local_time(self, tz: str) -> "PandoraFrame":
        """Return a copy with ``local_time`` (re)computed for time zone ``tz``."""
        out = self.copy()
        lt = out["utc_time"].dt.tz_convert(tz)
        if "local_time" in out.columns:
            out["local_time"] = lt
        else:
            out.insert(out.columns.get_loc("utc_time") + 1, "local_time", lt)
        return out


# ---------------------------------------------------------------------------
# Combining frames
# ---------------------------------------------------------------------------

def _merge_dicts(frames, attr):
    out = {}
    for f in frames:
        out.update(getattr(f, attr, None) or {})
    return out


def _concat_profiles(frames):
    profs = [f._profiles for f in frames if getattr(f, "_profiles", None) is not None
             and not f._profiles.empty]
    return pd.concat(profs, ignore_index=True) if profs else None


def concat(frames: Iterable[pd.DataFrame], ignore_index: bool = True, sort_time: bool = True,
           **kwargs) -> PandoraFrame:
    """Stack Pandora frames (several days, sites or instruments), keeping metadata.

    Use this instead of ``pd.concat``. Rows are sorted by time unless
    ``sort_time=False``.
    """
    frames = [f for f in frames if f is not None]
    if not frames:
        return PandoraFrame()
    out = PandoraFrame(pd.concat([pd.DataFrame(f) for f in frames], ignore_index=ignore_index, **kwargs))
    if sort_time and "utc_time" in out.columns:
        srt = pd.DataFrame(out).sort_values("utc_time", kind="stable")
        out = PandoraFrame(srt.reset_index(drop=True) if ignore_index else srt)
    return out._attach(_merge_dicts(frames, "_headers"), _merge_dicts(frames, "_catalogs"),
                       _concat_profiles(frames))


def _product_label(df: pd.DataFrame, fallback: str) -> str:
    if "product" in df.columns:
        p = list(pd.unique(df["product"].dropna()))
        if len(p) == 1:
            return str(p[0])
    return fallback


def merge_products(
    left: PandoraFrame,
    right: PandoraFrame,
    tolerance: Optional[Union[str, pd.Timedelta]] = None,
    how: str = "inner",
    report: bool = True,
) -> PandoraFrame:
    """Line up two products from the same instrument (e.g. NO2 and HCHO) by time.

    Parameters
    ----------
    tolerance : None for exact timestamp matches (the default; sky-scan products
        from the same instrument share timestamps). Otherwise e.g. ``'2min'``:
        each left row is matched to the nearest right row within the tolerance.
    how : 'inner' (only matched rows) or 'left' (keep all left rows).
    report : print how many rows matched.

    Columns that exist in both frames (geometry, fit residuals, L1 flags,
    ``source_file``, ...) get a product suffix, e.g. ``rms_rnvh3`` and
    ``rms_rfuh5``. Species columns such as ``no2_trop_col`` and ``hcho_trop_col``
    are unique already and keep their names. Station columns are kept once, and
    ``product`` becomes e.g. ``'rfuh5+rnvh3'``.
    """
    if how not in ("inner", "left"):
        raise ValueError("how must be 'inner' or 'left'")
    pl, pr = _product_label(left, "left"), _product_label(right, "right")
    if pl == pr:
        pl, pr = f"{pl}_left", f"{pr}_right"
    keys = ["utc_time"] + [k for k in JOIN_KEYS if k in left.columns and k in right.columns]
    shared_station = {"local_time", "latitude", "longitude", "altitude", "product"}

    L, R = pd.DataFrame(left).copy(), pd.DataFrame(right).copy()
    R = R.drop(columns=[c for c in shared_station if c in R.columns])
    overlap = (set(L.columns) & set(R.columns)) - set(keys)
    ren_l = {c: f"{c}_{pl}" for c in overlap}
    ren_r = {c: f"{c}_{pr}" for c in overlap}
    L, R = L.rename(columns=ren_l), R.rename(columns=ren_r)

    if tolerance is None:
        merged = pd.merge(L, R, on=keys, how=how)
    else:
        tol = pd.Timedelta(tolerance)
        by = keys[1:]
        R[f"utc_time_{pr}"] = R["utc_time"]
        L, R = L[L["utc_time"].notna()], R[R["utc_time"].notna()]
        merged = pd.merge_asof(
            L.sort_values("utc_time"), R.sort_values("utc_time"), on="utc_time",
            by=by or None, tolerance=tol, direction="nearest",
        )
        if how == "inner":
            merged = merged[merged[f"utc_time_{pr}"].notna()]
    merged = merged.reset_index(drop=True)
    if "product" in merged.columns:
        merged["product"] = f"{pl}+{pr}"

    if report:
        print(f"merge_products: {len(merged)} rows "
              f"({len(left)} {pl} rows, {len(right)} {pr} rows"
              f"{'' if tolerance is None else f', tolerance {tolerance}'})")
        if len(merged) == 0:
            print("  No matches. Both frames must come from the same location/instrument/"
                  "spectrometer; if timestamps differ slightly, try tolerance='2min'.")

    def renamed_catalog(cat, ren):
        cat = cat.copy()
        cat["name"] = cat["name"].replace(ren)
        return cat

    catalogs = {}
    for f, ren in ((left, ren_l), (right, ren_r)):
        for fname, cat in (f._catalogs or {}).items():
            catalogs[fname] = renamed_catalog(cat, ren)

    out = PandoraFrame(merged)
    return out._attach(_merge_dicts([left, right], "_headers"), catalogs,
                       _concat_profiles([left, right]))
