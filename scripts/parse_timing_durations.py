#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict
from pathlib import Path


def load_events(paths):
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield path, json.loads(line)
                except json.JSONDecodeError:
                    continue


def event_key(event):
    return event.get("event")


def duration_pairs(events):
    stack = defaultdict(list)
    durations = defaultdict(list)

    for _, ev in events:
        name = event_key(ev)
        if not name:
            continue
        ts = ev.get("mono") or ev.get("ts")
        if ts is None:
            continue
        actor = ev.get("actor", "unknown")
        key = (actor, name[:-6]) if name.endswith("_start") else (actor, name[:-4])
        if name.endswith("_start"):
            stack[key].append(ts)
        elif name.endswith("_end"):
            if stack[key]:
                start_ts = stack[key].pop()
                durations[key].append(ts - start_ts)

    return durations


def summarize(durations):
    summary = []
    for (actor, name), values in durations.items():
        if not values:
            continue
        values_sorted = sorted(values)
        total = sum(values_sorted)
        count = len(values_sorted)
        avg = total / count
        p50 = values_sorted[int(0.5 * (count - 1))]
        p90 = values_sorted[int(0.9 * (count - 1))]
        p99 = values_sorted[int(0.99 * (count - 1))] if count > 1 else values_sorted[0]
        summary.append(
            {
                "actor": actor,
                "name": name,
                "count": count,
                "total": total,
                "avg": avg,
                "p50": p50,
                "p90": p90,
                "p99": p99,
                "max": values_sorted[-1],
            }
        )
    return summary


def format_summary(summary, top):
    summary = sorted(summary, key=lambda x: x["total"], reverse=True)
    lines = []
    header = (
        "actor".ljust(12)
        + "event".ljust(32)
        + "count".rjust(8)
        + "total".rjust(12)
        + "avg".rjust(10)
        + "p50".rjust(10)
        + "p90".rjust(10)
        + "p99".rjust(10)
        + "max".rjust(10)
    )
    lines.append(header)
    lines.append("-" * len(header))
    for row in summary[:top]:
        lines.append(
            row["actor"].ljust(12)
            + row["name"].ljust(32)
            + str(row["count"]).rjust(8)
            + f"{row['total']:.3f}".rjust(12)
            + f"{row['avg']:.3f}".rjust(10)
            + f"{row['p50']:.3f}".rjust(10)
            + f"{row['p90']:.3f}".rjust(10)
            + f"{row['p99']:.3f}".rjust(10)
            + f"{row['max']:.3f}".rjust(10)
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Summarize timing durations from JSONL files.")
    parser.add_argument("paths", nargs="+", help="JSONL files or directories to parse")
    parser.add_argument("--top", type=int, default=20, help="Top N events by total time")
    args = parser.parse_args()

    jsonl_files = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            jsonl_files.extend(sorted(path.glob("*.jsonl")))
        else:
            jsonl_files.append(path)

    events = list(load_events(jsonl_files))
    durations = duration_pairs(events)
    summary = summarize(durations)
    print(format_summary(summary, args.top))


if __name__ == "__main__":
    main()
