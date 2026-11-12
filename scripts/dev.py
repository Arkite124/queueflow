"""Run a module using workspace-only dependencies, including embedded Windows Python."""
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / ".deps"), str(root)]
if len(sys.argv) < 2:
    raise SystemExit("Usage: python scripts/dev.py MODULE [arguments...]")
module = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_module(module, run_name="__main__", alter_sys=True)
