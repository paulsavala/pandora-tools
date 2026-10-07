"""Read local PGN L2 files into PandoraFrames."""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Iterable, Optional, Union

from .frame import PandoraFrame, concat
from .parser import parse_file

PathLike = Union[str, os.PathLike]


def _from_parsed(p) -> PandoraFrame:
    df = PandoraFrame(p.data)
    name = p.station["source_file"]
    header = dict(p.header.meta)
    return df._attach({name: header}, {name: p.catalog}, p.profiles)


def read(
    path: Union[PathLike, Iterable[PathLike]],
    tz: Optional[str] = "America/Chicago",
    mask_missing: bool = True,
) -> PandoraFrame:
    """Read one or more PGN L2 files.

    Parameters
    ----------
    path : a file path, a glob pattern (``'data/*rnvh3*.txt'``), or a list of paths.
        Several files are stacked with :func:`pandora_tools.concat`.
    tz : time zone for the ``local_time`` column (None to skip it).
    mask_missing : replace each column's documented "missing" codes
        (e.g. ``-9e99 = retrieval not successful``) with NaN. Set False for raw values.

    Different products can be read together, but their columns differ; usually
    read one product at a time and combine with :func:`merge_products`.
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
    frames = [_from_parsed(parse_file(x, tz=tz, mask_missing=mask_missing)) for x in paths]
    return frames[0] if len(frames) == 1 else concat(frames)


def read_bytes(content: bytes, filename: str, tz: Optional[str] = "America/Chicago",
               mask_missing: bool = True) -> PandoraFrame:
    """Parse file content already in memory (e.g. straight from a download)."""
    return _from_parsed(parse_file(content, name=filename, tz=tz, mask_missing=mask_missing))
