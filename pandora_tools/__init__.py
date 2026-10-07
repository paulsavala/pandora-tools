"""Tools for NASA / Pandonia Global Network (PGN) Pandora L2 data.

    import pandora_tools as pdt
    client = pdt.PandoraClient()
    no2, hcho = client.fetch("AustinTX", 257, "2026-08-28", products=["rnvh3", "rfuh5"])
"""
from .cache import resolve_cache_dir
from .client import PandoraClient
from .frame import PandoraFrame, QUALITY_FLAG_MEANINGS, concat, merge_products
from .parser import KNOWN_PRODUCTS, SPECIES
from .reader import read, read_bytes

__version__ = "0.1.0"
__all__ = ["PandoraClient", "PandoraFrame", "read", "read_bytes", "concat", "merge_products",
           "resolve_cache_dir", "KNOWN_PRODUCTS", "SPECIES", "QUALITY_FLAG_MEANINGS"]
