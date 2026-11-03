"""Replay a fixed prompt set against the running backend and report latency.

Every request starts a new conversation, so each prompt goes to the model with
the same (empty) history. Time to first token and total time are measured
twice: here, from sending the POST to the first token event and to the done
event, and by the backend, from its request to the model server (the done
event carries those). The difference is what the backend and the HTTP hops add.

    python bench/latency.py --base-url http://localhost:8000 --repeats 3 \
        --label ollama --out results/latency/ollama-run1.json
"""

import argparse
import asyncio
import datetime
import json
import platform
import time
from pathlib import Path

import httpx
from stats import summarize

ROOT = Path(__file__).resolve().parents[1]


def shown_path(path: str) -> str:
    """A path as it should appear in a result file: relative to the repo, never absolute."""
    resolved = Path(path).resolve()
    return resolved.relative_to(ROOT).as_posix() if resolved.is_relative_to(ROOT) else resolved.name


async def one_request(client: httpx.AsyncClient, base_url: str, prompt: str) -> dict:
    started = time.perf_counter()
    first_token = done_at = None
    done: dict | None = None
    event = None
    async with client.stream("POST", f"{base_url}/api/chat", json={"message": prompt}) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
                if event == "token" and first_token is None:
                    first_token = time.perf_counter()
                elif event == "done":
                    done, done_at = data, time.perf_counter()
                elif event == "error":
                    raise RuntimeError(f"the backend reported an error: {data['error']}")
    if done is None or first_token is None or done_at is None:
        raise RuntimeError("the stream ended without a token and a done event")
    return {
        "client_ttft_ms": (first_token - started) * 1000,
        "client_total_ms": (done_at - started) * 1000,
        "server_ttft_ms": done["ttft_ms"],
        "server_total_ms": done["total_ms"],
        "output_tokens": done["output_tokens"],
    }


async def run(args: argparse.Namespace) -> dict:
    prompts = json.loads(Path(args.prompts).read_text(encoding="utf-8"))
    limits = httpx.Limits(max_connections=max(args.concurrency, 1) + 1)
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), limits=limits) as client:
        health = (await client.get(f"{args.base_url}/api/health")).json()
        for prompt in prompts[: args.warmup]:
            await one_request(client, args.base_url, prompt)

        gate = asyncio.Semaphore(args.concurrency)
        jobs = [(repeat, index, prompt) for repeat in range(args.repeats) for index, prompt in enumerate(prompts)]

        async def timed(repeat: int, index: int, prompt: str) -> dict:
            async with gate:
                sample = await one_request(client, args.base_url, prompt)
            return {"repeat": repeat, "prompt_index": index, **sample}

        started_at = datetime.datetime.now(datetime.UTC)
        wall_started = time.perf_counter()
        samples = await asyncio.gather(*(timed(*job) for job in jobs))
        wall_s = time.perf_counter() - wall_started

    def column(name: str) -> list[float]:
        return [s[name] for s in samples]

    overhead = [s["client_ttft_ms"] - s["server_ttft_ms"] for s in samples]
    return {
        "label": args.label,
        "started_at": started_at.isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "backend": health,
        "model_server": args.model_server,
        "machine": args.machine,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "prompts": {"file": shown_path(args.prompts), "count": len(prompts)},
        "repeats": args.repeats,
        "warmup_requests": args.warmup,
        "concurrency": args.concurrency,
        "wall_s": wall_s,
        "summary": {
            "client_ttft_ms": summarize(column("client_ttft_ms")),
            "client_total_ms": summarize(column("client_total_ms")),
            "server_ttft_ms": summarize(column("server_ttft_ms")),
            "server_total_ms": summarize(column("server_total_ms")),
            "ttft_overhead_ms": summarize(overhead),
            "output_tokens": summarize(column("output_tokens")),
        },
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--prompts", default=str(Path(__file__).with_name("prompts.json")))
    parser.add_argument("--repeats", type=int, default=3, help="times through the prompt set")
    parser.add_argument("--warmup", type=int, default=1, help="requests sent first and not recorded")
    parser.add_argument("--concurrency", type=int, default=1, help="requests in flight at once")
    parser.add_argument("--label", required=True, help="groups runs of the same setup in the summary")
    parser.add_argument("--model-server", default="", help="what served the model, for the record")
    parser.add_argument("--machine", default="", help="the hardware, for the record")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    result = asyncio.run(run(args))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    s = result["summary"]
    print(
        f"{args.label}: n={s['client_ttft_ms']['n']} "
        f"ttft p50 {s['client_ttft_ms']['p50']:.1f} ms p95 {s['client_ttft_ms']['p95']:.1f} ms, "
        f"total p50 {s['client_total_ms']['p50']:.1f} ms p95 {s['client_total_ms']['p95']:.1f} ms -> {out}"
    )


if __name__ == "__main__":
    main()
