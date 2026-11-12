"""Read advisories in official PyPI release metadata for every locked dependency."""
import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def check(pin):
    name, version = pin.split("==")
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=20) as response:
        data = json.load(response)
    return {"name": name, "version": version, "vulnerabilities": data.get("vulnerabilities", [])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output/python-audit.json"))
    args = parser.parse_args()
    pins = [line.strip() for line in Path("requirements-lock.txt").read_text().splitlines() if "==" in line]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, pins))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
    vulnerable = [r for r in results if r["vulnerabilities"]]
    print(json.dumps({"packages_checked": len(results), "packages_with_advisories": len(vulnerable),
                      "findings": [{"name": r["name"], "version": r["version"],
                                    "fixed_in": [v.get("fixed_in") for v in r["vulnerabilities"]]} for r in vulnerable]},
                     indent=2))
    raise SystemExit(1 if vulnerable else 0)


if __name__ == "__main__":
    main()
