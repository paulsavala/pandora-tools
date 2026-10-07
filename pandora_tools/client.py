"""PandoraClient: browse, download and load data from the PGN API.

API: https://api.pandonia-global-network.org (spec at /openapi.json). The
browsing hierarchy is location -> instrument -> spectrometer -> level -> files.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import time
import warnings
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

import pandas as pd
import requests

from .cache import atomic_write, resolve_cache_dir
from .frame import PandoraFrame, concat
from .parser import KNOWN_PRODUCTS
from .reader import read

BASE_URL = "https://api.pandonia-global-network.org/v1"
DateLike = Union[str, dt.date, dt.datetime, pd.Timestamp]


def _as_date(d: DateLike) -> dt.date:
    """UTC calendar date of ``d`` (time-zone-aware values are converted to UTC first)."""
    ts = pd.Timestamp(d)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return ts.date()


def _parse_day(value, filename) -> Optional[dt.date]:
    for v in (value, _date_from_filename(filename or "")):
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        s = str(v).strip()
        for fmt in ("%Y%m%d", "%Y-%m-%d"):
            try:
                return dt.datetime.strptime(s[:10] if "-" in s else s[:8], fmt).date()
            except ValueError:
                pass
    return None


def _date_from_filename(name: str) -> Optional[str]:
    m = re.search(r"_(\d{8})_L", name)
    return m.group(1) if m else None


def _code_from_filename(name: str) -> Optional[str]:
    m = re.search(r"_L\w*?_([a-z]+\d+)", name)
    return m.group(1) if m else None


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class PandoraClient:
    """Access PGN Pandora data.

    >>> client = PandoraClient()                     # cache folder chosen automatically
    >>> client.find_locations("Austin")
    >>> client.instruments("AustinTX")
    >>> client.products("AustinTX", 257, "2026-08-01", "2026-08-31")
    >>> no2, hcho = client.fetch("AustinTX", 257, "2026-08-28", products=["rnvh3", "rfuh5"])

    Parameters
    ----------
    cache_dir : where downloaded files are kept. Default: chosen automatically
        (see ``pandora_tools.cache``).
    tz : time zone for the ``local_time`` column (None to skip).
    mask_missing : convert documented missing-value codes to NaN.
    verbose : print short progress messages.
    """

    def __init__(
        self,
        cache_dir: Optional[os.PathLike] = None,
        tz: Optional[str] = "America/Chicago",
        mask_missing: bool = True,
        verbose: bool = True,
        base_url: str = BASE_URL,
        timeout: float = 60,
        retries: int = 3,
    ):
        self.cache_dir, self.cache_source = resolve_cache_dir(cache_dir)
        self.tz = tz
        self.mask_missing = mask_missing
        self.verbose = verbose
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self._locations: Optional[pd.DataFrame] = None

    def __repr__(self):
        return f"PandoraClient(cache_dir='{self.cache_dir}' [{self.cache_source}], tz={self.tz!r})"

    def _log(self, msg: str):
        if self.verbose:
            print(msg)

    # ---------------------------------------------------------------- HTTP
    def _get(self, path: str, params=None) -> requests.Response:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last = None
        for attempt in range(self.retries):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last = e
            else:
                if r.status_code == 200:
                    return r
                if r.status_code == 404:
                    raise LookupError(f"Not found: {r.url} ({r.text[:200]})")
                if r.status_code < 500:
                    raise RuntimeError(f"PGN API error {r.status_code} for {r.url}: {r.text[:300]}")
                last = RuntimeError(f"PGN API error {r.status_code} for {r.url}")
            time.sleep(1.5 * (attempt + 1))
        raise ConnectionError(f"Could not reach the PGN API after {self.retries} tries: {last}")

    def _get_json(self, path: str, params=None):
        return self._get(path, params).json()

    # ------------------------------------------------------------ browsing
    def locations(self, refresh: bool = False) -> pd.DataFrame:
        """All official PGN locations with coordinates."""
        if self._locations is None or refresh:
            df = pd.DataFrame(self._get_json("files/locations"))
            self._locations = df.sort_values("name").reset_index(drop=True)
        return self._locations.copy()

    def find_locations(
        self,
        query: Optional[str] = None,
        near: Optional[Tuple[float, float]] = None,
        radius_km: Optional[float] = None,
    ) -> pd.DataFrame:
        """Search locations by name text and/or distance.

        >>> client.find_locations("TX")                          # name contains 'TX'
        >>> client.find_locations(near=(30.27, -97.74), radius_km=300)   # around Austin
        """
        df = self.locations()
        if query:
            q = query.lower()
            mask = df["name"].str.lower().str.contains(q, regex=False) | \
                df["long_name"].fillna("").str.lower().str.contains(q, regex=False) | \
                df["aliases"].map(lambda a: any(q in str(x).lower() for x in (a or [])))
            df = df[mask]
        if near is not None:
            lat, lon = near
            df = df.assign(distance_km=[_haversine_km(lat, lon, a, b) for a, b in zip(df["lat"], df["lon"])])
            if radius_km is not None:
                df = df[df["distance_km"] <= radius_km]
            df = df.sort_values("distance_km")
        return df.reset_index(drop=True)

    def resolve_location(self, name: str) -> str:
        """Official location name for ``name`` (case-insensitive; accepts old aliases)."""
        df = self.locations()
        n = name.lower()
        hit = df[df["name"].str.lower() == n]
        if hit.empty:
            hit = df[df["aliases"].map(lambda a: n in [str(x).lower() for x in (a or [])])]
        if hit.empty:
            close = self.find_locations(name[:4])["name"].tolist()[:10]
            raise LookupError(f"Unknown location {name!r}. Did you mean one of {close}?")
        return hit.iloc[0]["name"]

    def instruments(self, location: str) -> List[int]:
        """Pandora instrument numbers at a location."""
        loc = self.resolve_location(location)
        return [int(x["pan_id"]) for x in self._get_json(f"files/{loc}")]

    def spectrometers(self, location: str, instrument: int) -> List[int]:
        loc = self.resolve_location(location)
        return [int(x["spectrometer"]) for x in self._get_json(f"files/{loc}/{int(instrument)}")]

    def levels(self, location: str, instrument: int, spectrometer: int = 1) -> List[str]:
        loc = self.resolve_location(location)
        return [x["level"] for x in self._get_json(f"files/{loc}/{int(instrument)}/{int(spectrometer)}")]

    def list_files(
        self,
        location: str,
        instrument: int,
        start: DateLike,
        end: Optional[DateLike] = None,
        product: Optional[str] = None,
        spectrometer: int = 1,
        level: str = "L2",
        latest_only: bool = True,
    ) -> pd.DataFrame:
        """Files available for a date range (UTC dates, ``end`` inclusive).

        ``product`` is a product code such as 'rnvh3'; None lists all products.
        With ``latest_only`` only the most recently modified file per product
        and day is kept (in case PGN has reissued a day).
        """
        loc = self.resolve_location(location)
        d0 = _as_date(start)
        d1 = _as_date(end) if end is not None else d0
        params = {"start": f"{d0.isoformat()}T00:00:00Z", "end": f"{d1.isoformat()}T23:59:59Z"}
        if product:
            params["code"] = product
        recs = self._get_json(f"files/{loc}/{int(instrument)}/{int(spectrometer)}/{level}", params)
        df = pd.DataFrame(recs)
        if df.empty:
            return pd.DataFrame(columns=["filename", "product", "date", "location", "instrument",
                                         "spectrometer", "size", "modified_time"])
        codes = df["metadata_code"] if "metadata_code" in df else pd.Series([None] * len(df))
        df["product"] = [c if c else _code_from_filename(f) for c, f in zip(codes, df["filename"])]
        dates = df["metadata_date"] if "metadata_date" in df else pd.Series([None] * len(df))
        df["date"] = [_parse_day(d, f) for d, f in zip(dates, df["filename"])]
        undated = df["date"].isna()
        if undated.any():
            warnings.warn(f"Could not determine the date of {int(undated.sum())} file(s); skipping: "
                          f"{list(df.loc[undated, 'filename'])[:5]}")
            df = df[~undated]
        df["location"], df["instrument"], df["spectrometer"] = loc, int(instrument), int(spectrometer)
        df = df[(df["date"] >= d0) & (df["date"] <= d1)]
        if product:
            df = df[df["product"] == product]
        if latest_only and "modified_time" in df:
            df = (df.sort_values("modified_time")
                    .drop_duplicates(["product", "date"], keep="last"))
        front = ["filename", "product", "date", "location", "instrument", "spectrometer"]
        rest = [c for c in df.columns if c not in front]
        return df[front + rest].sort_values(["product", "date"]).reset_index(drop=True)

    def products(self, location: str, instrument: int, start: DateLike,
                 end: Optional[DateLike] = None, spectrometer: int = 1) -> pd.DataFrame:
        """Which L2 products exist for an instrument in a date range."""
        files = self.list_files(location, instrument, start, end, spectrometer=spectrometer)
        if files.empty:
            return pd.DataFrame(columns=["product", "description", "n_days", "first_date", "last_date"])
        g = files.groupby("product")["date"].agg(n_days="nunique", first_date="min", last_date="max")
        g = g.reset_index()
        g.insert(1, "description", g["product"].map(KNOWN_PRODUCTS).fillna(""))
        return g

    def catalog(
        self,
        locations: Union[str, Sequence[str]],
        start: DateLike,
        end: Optional[DateLike] = None,
        level: str = "L2",
    ) -> pd.DataFrame:
        """What data exists: location x instrument x spectrometer x product.

        ``locations`` is one name or a list (e.g. from ``find_locations``). This
        makes several API calls per location, so keep the list focused.
        """
        if isinstance(locations, str):
            locations = [locations]
        rows = []
        for loc in locations:
            loc = self.resolve_location(loc)
            for inst in self.instruments(loc):
                for spec in self.spectrometers(loc, inst):
                    try:
                        files = self.list_files(loc, inst, start, end, spectrometer=spec, level=level)
                    except LookupError:
                        continue
                    if files.empty:
                        continue
                    g = files.groupby("product")["date"].agg(n_days="nunique", first_date="min",
                                                             last_date="max").reset_index()
                    g.insert(0, "location", loc)
                    g.insert(1, "instrument", inst)
                    g.insert(2, "spectrometer", spec)
                    rows.append(g)
        if not rows:
            return pd.DataFrame(columns=["location", "instrument", "spectrometer", "product",
                                         "n_days", "first_date", "last_date"])
        out = pd.concat(rows, ignore_index=True)
        out.insert(4, "description", out["product"].map(KNOWN_PRODUCTS).fillna(""))
        return out

    # ------------------------------------------------------------ download
    def local_path(self, filename: str, location: Optional[str] = None, level: str = "L2") -> Path:
        if location is None:
            m = re.match(r"Pandora\d+s\d+_([^_]+)_", filename)
            location = m.group(1) if m else "unknown"
        return self.cache_dir / level / location / filename

    def download(self, files: Union[pd.DataFrame, Iterable[str], str], refresh: bool = False) -> List[Path]:
        """Download files (a ``list_files`` result or filenames) into the cache.

        Files already in the cache are not downloaded again unless ``refresh=True``.
        """
        if isinstance(files, str):
            names = [files]
        elif isinstance(files, pd.DataFrame):
            names = list(files["filename"])
        else:
            names = list(files)
        paths, n_new = [], 0
        for name in names:
            path = self.local_path(name)
            if refresh or not path.exists():
                r = self._get(f"download/{name}")
                content = r.content
                if b"Column 1:" not in content[:20000]:
                    raise RuntimeError(f"Download of {name} did not return a PGN data file: "
                                       f"{content[:200]!r}")
                atomic_write(path, content)
                n_new += 1
            paths.append(path)
        self._log(f"{len(paths)} file(s): {n_new} downloaded, {len(paths) - n_new} from cache "
                  f"({self.cache_dir})")
        return paths

    # --------------------------------------------------------------- fetch
    def fetch(
        self,
        location: str,
        instrument: int,
        start: DateLike,
        end: Optional[DateLike] = None,
        products: Union[str, Sequence[str]] = ("rnvh3", "rfuh5"),
        spectrometer: int = 1,
        refresh: bool = False,
    ) -> Union[PandoraFrame, Tuple[PandoraFrame, ...]]:
        """List, download (cached) and read data in one step.

        Dates are UTC days and ``end`` is inclusive (``end=None`` means just
        ``start``). Note a local (CDT) day spans two UTC days, so to cover a full
        local day ask for that day and the next one, then filter on ``local_time``.

        Returns one PandoraFrame if ``products`` is a single code, otherwise a
        tuple in the same order:

        >>> hcho = client.fetch("AustinTX", 257, "2026-08-28", products="rfuh5")
        >>> no2, hcho = client.fetch("AustinTX", 257, "2026-08-28", products=["rnvh3", "rfuh5"])
        """
        single = isinstance(products, str)
        codes = [products] if single else list(products)
        all_files = self.list_files(location, instrument, start, end, spectrometer=spectrometer)
        frames = []
        for code in codes:
            files = all_files[all_files["product"] == code]
            if files.empty:
                avail = sorted(all_files["product"].unique()) if not all_files.empty else []
                raise LookupError(
                    f"No {code!r} files for {location} instrument {instrument} between {start} and "
                    f"{end or start}. Products available in that range: {avail or 'none'}"
                )
            self._log(f"{code}: {len(files)} day(s)")
            paths = self.download(files, refresh=refresh)
            frames.append(read(paths, tz=self.tz, mask_missing=self.mask_missing))
        return frames[0] if single else tuple(frames)

    def read(self, path, **kwargs) -> PandoraFrame:
        """Read local file(s) with this client's settings (see :func:`pandora_tools.read`)."""
        kwargs.setdefault("tz", self.tz)
        kwargs.setdefault("mask_missing", self.mask_missing)
        return read(path, **kwargs)
