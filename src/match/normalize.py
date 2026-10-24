"""Company-name cleanup shared by matching and the entity table.

Same approach as Founder Radar's raised_filter._norm, plus diacritics and the
startup suffixes (AI, Labs) that Form D filers often drop or add.
"""
import re
import unicodedata

SUFFIXES = r"\b(inc|incorporated|llc|corp|corporation|co|company|ltd|limited|lp|pbc|plc|ai|labs|hq)\b"


def normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"\(CIK \d+\)", "", name, flags=re.I).lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(SUFFIXES, " ", name)
    return " ".join(name.split())
