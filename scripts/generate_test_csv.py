import argparse
import csv
from pathlib import Path


def generate(path, rows, seed=7):
    """Deterministic synthetic records; .test email domains contain no personal data."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "email", "created_at", "amount", "note"])
        for number in range(1, rows + 1):
            index = number + seed
            identifier = number - 1 if index % 17 == 0 else number
            email = "" if index % 29 == 0 else "invalid" if index % 31 == 0 else f"user{number}@example.test"
            created = "invalid-date" if index % 37 == 0 else "2026-01-01"
            writer.writerow([identifier, f" {email} " if email else "", created,
                             f"{number / 10:.1f}", "=1+1" if index % 41 == 0 else "synthetic"])
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, choices=[1000, 10000, 100000], default=10000)
    parser.add_argument("--output", type=Path, default=Path("output/sample.csv"))
    args = parser.parse_args()
    generate(args.output, args.rows)
    print(f"Generated {args.rows} synthetic rows: {args.output} ({args.output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
