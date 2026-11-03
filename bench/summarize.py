"""Write the measurement tables in README.md from the result files, or check them.

    python bench/summarize.py          # rewrite the tables in README.md
    python bench/summarize.py --check  # exit 1 if README.md no longer matches results/

The README's numbers are never typed by hand: they come from here.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def ms(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f} s"
    if value >= 10:
        return f"{value:.0f} ms"
    return f"{value:.1f} ms"


def fine_ms(value: float) -> str:
    return f"{value:.3f} ms"


def spread(values: list[float], fmt) -> str:
    low, high = fmt(min(values)), fmt(max(values))
    return low if low == high else f"{low} to {high}"


def load(folder: str) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((ROOT / "results" / folder).glob("*.json"))]


def latency_table() -> str:
    runs_by_label: dict[str, list[dict]] = {}
    for run in load("latency"):
        runs_by_label.setdefault(run["label"], []).append(run)
    lines = [
        "| setup | run | requests | reply tokens p50 | TTFT p50 | TTFT p95 | total p50 | total p95 "
        "| added by backend to TTFT, p50 / p95 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for label, runs in runs_by_label.items():
        for number, run in enumerate(runs, 1):
            s = run["summary"]
            ttft, total, extra = s["client_ttft_ms"], s["client_total_ms"], s["ttft_overhead_ms"]
            lines.append(
                f"| {label} | {number} | {ttft['n']} | {s['output_tokens']['p50']:.0f} | {ms(ttft['p50'])} | "
                f"{ms(ttft['p95'])} | {ms(total['p50'])} | {ms(total['p95'])} | {ms(extra['p50'])} / {ms(extra['p95'])} |"
            )
        if len(runs) > 1:

            def across(metric: str, stat: str, fmt=ms) -> str:
                return spread([r["summary"][metric][stat] for r in runs], fmt)

            lines.append(
                f"| {label} | **spread over {len(runs)} runs** | | {across('output_tokens', 'p50', lambda v: f'{v:.0f}')} | "
                f"{across('client_ttft_ms', 'p50')} | {across('client_ttft_ms', 'p95')} | "
                f"{across('client_total_ms', 'p50')} | {across('client_total_ms', 'p95')} | "
                f"{across('ttft_overhead_ms', 'p50')} / {across('ttft_overhead_ms', 'p95')} |"
            )
    return "\n".join(lines)


def index_table() -> str:
    runs = load("index")
    lines = [
        "| run | conversations timed | with index p50 | with index p95 | without p50 | without p95 | p50 ratio |",
        "|---|---|---|---|---|---|---|",
    ]
    ratios = []
    for number, run in enumerate(runs, 1):
        w, wo = run["with_index"]["execution_ms"], run["without_index"]["execution_ms"]
        ratio = wo["p50"] / w["p50"]
        ratios.append(ratio)
        lines.append(
            f"| {number} | {w['n']} | {fine_ms(w['p50'])} | {fine_ms(w['p95'])} | "
            f"{fine_ms(wo['p50'])} | {fine_ms(wo['p95'])} | {ratio:.0f}x |"
        )
    if len(runs) > 1:

        def across(state: str, stat: str) -> str:
            return spread([r[state]["execution_ms"][stat] for r in runs], fine_ms)

        lines.append(
            f"| **spread over {len(runs)} runs** | | {across('with_index', 'p50')} | {across('with_index', 'p95')} | "
            f"{across('without_index', 'p50')} | {across('without_index', 'p95')} | "
            f"{min(ratios):.0f}x to {max(ratios):.0f}x |"
        )
    def blocks(state: str, kind: str) -> str:
        values = [r[state][kind]["p50"] for r in runs]
        low, high = min(values), max(values)
        return f"{low:.0f}" if low == high else f"{low:.0f} to {high:.0f}"

    lines.append("")
    for state, title in (("with_index", "With the index"), ("without_index", "Without it")):
        plans = sorted({shape for run in runs for shape in run[state]["plans"]})
        lines.append(
            f"- {title}: plan " + " or ".join(f"`{shape}`" for shape in plans)
            + f"; per query, p50 of {blocks(state, 'shared_hit_blocks')} buffer blocks found in Postgres's cache"
            + f" and {blocks(state, 'shared_read_blocks')} read from outside it (8 kB each)."
        )
    sizes = sorted({(r["messages"], r["table_size"], r["index_size"]) for r in runs})
    for messages, table_size, index_size in sizes:
        lines.append(f"- Table: {messages:,} messages, {table_size}; index {index_size}.")
    return "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    if text.count(start) != 1 or text.count(end) != 1:
        sys.exit(f"README.md must contain {start} and {end} exactly once each")
    head, rest = text.split(start)
    _, tail = rest.split(end)
    return f"{head}{start}\n{body}\n{end}{tail}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail instead of writing when the README is stale")
    args = parser.parse_args()

    current = README.read_text(encoding="utf-8")
    updated = replace_block(current, "latency", latency_table())
    updated = replace_block(updated, "index", index_table())
    if args.check:
        if updated != current:
            sys.exit("README.md tables do not match results/; run python bench/summarize.py")
        print("README.md tables match results/")
    else:
        README.write_text(updated, encoding="utf-8", newline="\n")
        print("wrote the tables in README.md")


if __name__ == "__main__":
    main()
