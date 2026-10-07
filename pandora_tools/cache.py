"""Where downloaded files are kept.

Downloaded files can always be fetched again from the PGN API, so the folder is
only a cache. It is chosen automatically, first match wins:

1. ``cache_dir=...`` passed to :class:`PandoraClient`
2. the ``PANDORA_DATA_DIR`` environment variable
3. a Google shared drive that contains a ``pandora_cache`` folder, i.e.
   ``/content/drive/Shareddrives/<any drive>/pandora_cache`` (Colab, Drive mounted).
   Shared-drive paths are the same for every member, so the team shares one cache.
4. ``/content/pandora_cache`` in Colab (temporary: cleared when the session ends)
5. ``~/.cache/pandora_tools`` on a regular computer
"""

from __future__ import annotations

import glob
import os
import sys
import tempfile
import uuid
import warnings
from pathlib import Path
from typing import Optional, Tuple

CACHE_FOLDER_NAME = "pandora_cache"
ENV_VAR = "PANDORA_DATA_DIR"
SHARED_DRIVES_ROOT = "/content/drive/Shareddrives"


def in_colab() -> bool:
    return "google.colab" in sys.modules or os.path.isdir("/content/sample_data")


def resolve_cache_dir(explicit: Optional[os.PathLike] = None) -> Tuple[Path, str]:
    """Return (cache folder, why it was chosen)."""
    if explicit:
        return Path(explicit).expanduser(), "cache_dir argument"
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env).expanduser(), f"{ENV_VAR} environment variable"
    shared = sorted(glob.glob(os.path.join(SHARED_DRIVES_ROOT, "*", CACHE_FOLDER_NAME)))
    if shared:
        if len(shared) > 1:
            warnings.warn(f"Several shared-drive caches found {shared}; using {shared[0]}. "
                          f"Set {ENV_VAR} or cache_dir= to choose.")
        return Path(shared[0]), "shared drive"
    if in_colab():
        return Path("/content") / CACHE_FOLDER_NAME, "Colab local disk (temporary)"
    return Path.home() / ".cache" / "pandora_tools", "local user cache"


def atomic_write(path: Path, content: bytes) -> None:
    """Write via a temporary file + rename, so a half-finished download is never
    seen by another user of a shared cache."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
    try:
        with open(tmp, "wb") as f:
            f.write(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
