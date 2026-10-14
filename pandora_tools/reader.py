"""Read local PGN L2 files and in-memory byte strings into PandoraFrames."""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Iterable, Optional, Union

from .frame import PandoraFrame, concat
from .parser import parse_file

PathLike = Union[str, os.PathLike]


def _from_parsed(p) -> PandoraFrame:
    """Construct a PandoraFrame from a parsed PGN L2 data object."""
    df = PandoraFrame(p.data)
    name = p.station["source_file"]
    header = dict(p.header.meta)
    return df._attach({name: header}, {name: p.catalog}, p.profiles)


def read(
    path: Union[PathLike, Iterable[PathLike]],
    tz: Optional[str] = "America/Chicago",
    mask_missing: bool = True,
) -> PandoraFrame:
    """Read one or more PGN L2 data files into a PandoraFrame.

    Parses single files, glob patterns, or lists of paths containing PGN L2
    data. Multi-file inputs are concatenated into a single time-series frame.
    All primary time series data are indexed with UTC timestamps.

    Parameters
    ----------
    path : str, Path, or iterable of str or Path
        A file path, glob pattern (e.g., `'data/*rnvh3*.txt'`), or list of paths
        to local PGN L2 files.
    tz : str or None, default 'America/Chicago'
        Target IANA time zone identifier used to compute the extra `local_time`
        column. If `None`, no `local_time` column is added.
    mask_missing : bool, default True
        If `True`, replaces product-specific missing-value codes (e.g., negative
        sentinels or magnitude >= 999 indicating failed retrievals) with `NaN`.
        Set to `False` to preserve unmasked raw numerical observations.

    Returns
    -------
    PandoraFrame
        A specialized `pandas.DataFrame` subclass containing daily UTC observation
        records, standard variable names, and attached file headers/catalogs.

    Raises
    ------
    FileNotFoundError
        If no files match the provided file path or glob pattern.
    ValueError
        If the file content fails header validation or L2 format rules.
    """
    if isinstance(path, (str, os.PathLike)):
        p = str(path)
        if os.path.exists(p) or not any(ch in p for ch in "*?["):
            paths = [p]
        else:
            paths = sorted(glob.glob(p))
        if not paths:
            raise FileNotFoundError(f"No files match {p!r}")
    else:
        paths = [str(x) for x in path]
    frames = [_from_parsed(parse_file(
        x, tz=tz, mask_missing=mask_missing)) for x in paths]
    return frames[0] if len(frames) == 1 else concat(frames)


def read_bytes(
    content: bytes,
    filename: str,
    tz: Optional[str] = "America/Chicago",
    mask_missing: bool = True,
) -> PandoraFrame:
    """Parse raw PGN L2 file content in memory into a PandoraFrame.

    Parameters
    ----------
    content : bytes
        Raw byte sequence containing the uncompressed PGN L2 text content.
    filename : str
        Source filename identifier used to track origin metadata inside the frame.
    tz : str or None, default 'America/Chicago'
        Target IANA time zone identifier used to compute the `local_time`
        column. If `None`, no `local_time` column is added.
    mask_missing : bool, default True
        If `True`, replaces documented missing-value sentinel codes with `NaN`.

    Returns
    -------
    PandoraFrame
        A specialized `pandas.DataFrame` subclass containing daily UTC observations.
    """
    return _from_parsed(parse_file(content, name=filename, tz=tz, mask_missing=mask_missing))
