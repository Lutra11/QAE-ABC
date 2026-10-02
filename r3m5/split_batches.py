#!/usr/bin/env python3
"""Split r3m5 batch manifests for GitHub Actions dispatch.

E3 wave1 (600 cases) exceeds what one workflow run can finish within the
6 h GitHub job limit (E2 evidence: ~2 h per 660 s case, plus ~40 min build).
Split it into two 300-case manifests so each dispatch covers one wave with
2 cases per shard (150 shards), matching the E2 "safe recipe" (1-2 cases
per shard, E2 17/20 shards succeeded with 2 cases/shard).
"""
import json
from pathlib import Path

BATCH_DIR = Path("r3m5/batches")


def load(name):
    data = json.loads((BATCH_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return data["cases"] if isinstance(data, dict) else data


def save(name, rows):
    BATCH_DIR.joinpath(f"{name}.json").write_text(
        json.dumps({"cases": rows}, indent=1), encoding="utf-8")
    print(f"{name}: {len(rows)} cases")


def main():
    wave1 = load("e3_wave1")
    assert len(wave1) == 600, f"expected 600, got {len(wave1)}"
    ids = [r["case_id"] for r in wave1]
    assert len(set(ids)) == 600, "duplicate case_ids in e3_wave1"
    # wave1a = first 300, wave1b = last 300; keep manifest order (E1_xxx_s0..s2 blocks)
    save("e3_wave1a", wave1[:300])
    save("e3_wave1b", wave1[300:])

    wave2 = load("e3_wave2")
    assert len(wave2) == 600, f"expected 600, got {len(wave2)}"
    ids2 = [r["case_id"] for r in wave2]
    assert len(set(ids2)) == 600, "duplicate case_ids in e3_wave2"
    save("e3_wave2a", wave2[:300])
    save("e3_wave2b", wave2[300:])


if __name__ == "__main__":
    main()
