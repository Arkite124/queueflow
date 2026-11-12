"""Record the resolved, workspace-local Python dependency set."""
from importlib.metadata import distributions
from pathlib import Path

root = Path(__file__).resolve().parents[1]
packages = sorted(f"{d.metadata['Name']}=={d.version}" for d in distributions(path=[str(root / ".deps")]))
(root / "requirements-lock.txt").write_text(
    "# Resolved and verified with CPython 3.13.7 on Windows; includes development dependencies.\n"
    + "\n".join(packages) + "\n", encoding="utf-8")
print(f"Recorded {len(packages)} exact dependency versions")
