"""Time the query that loads one conversation, with and without its index.

It builds the app's own tables in a separate schema ("bench"), so the app's
data is never touched, fills them with a million messages spread over twenty
thousand conversations, then runs EXPLAIN (ANALYZE) on the exact query in
app/db.py for a sample of conversations: once with messages_conversation_id_idx,
once after dropping it. The index is created again at the end.

    python bench/index_read_path.py --seed 1 --out results/index/run1.json
"""

import argparse
import asyncio
import datetime
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

import asyncpg
from stats import summarize

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.db import MESSAGE_COLUMNS, SCHEMA  # noqa: E402

READ_PATH = f"SELECT {MESSAGE_COLUMNS} FROM messages WHERE conversation_id = $1 ORDER BY id"
INDEX = "messages_conversation_id_idx"


async def seed(conn: asyncpg.Connection, conversations: int, per_conversation: int) -> None:
    await conn.execute("TRUNCATE messages, conversations RESTART IDENTITY")
    await conn.execute(
        "INSERT INTO conversations (title) SELECT 'conversation ' || g FROM generate_series(1, $1) g",
        conversations,
    )
    # Message m of every conversation is written before message m + 1 of any,
    # as when many conversations are active at once, so one conversation's
    # rows are spread across the table rather than stored side by side.
    await conn.execute(
        """
        INSERT INTO messages (conversation_id, role, content)
        SELECT c, CASE WHEN m % 2 = 0 THEN 'user' ELSE 'assistant' END,
               repeat(md5(c::text || ':' || m::text), 6)
        FROM generate_series(0, $2 - 1) m, generate_series(1, $1) c
        ORDER BY m, c
        """,
        conversations,
        per_conversation,
    )
    await conn.execute("VACUUM ANALYZE conversations")
    await conn.execute("VACUUM ANALYZE messages")


def plan_shape(plan: dict) -> str:
    """The plan's node types, parent first: 'Sort > Bitmap Heap Scan > Bitmap Index Scan'."""
    return " > ".join([plan["Node Type"], *(plan_shape(child) for child in plan.get("Plans", []))])


async def time_queries(conn: asyncpg.Connection, ids: list[int]) -> dict:
    for conversation_id in ids:  # warm the cache; not recorded
        await conn.fetch(READ_PATH, conversation_id)
    times, hit, read, shapes, rows = [], [], [], Counter(), Counter()
    for conversation_id in ids:
        raw = await conn.fetchval(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {READ_PATH}", conversation_id)
        plan = json.loads(raw)[0]
        times.append(plan["Execution Time"])
        # The top node's buffer counts include its children's.
        hit.append(plan["Plan"]["Shared Hit Blocks"])
        read.append(plan["Plan"]["Shared Read Blocks"])
        shapes[plan_shape(plan["Plan"])] += 1
        rows[plan["Plan"]["Actual Rows"]] += 1
    return {
        "execution_ms": summarize(times),
        "shared_hit_blocks": summarize(hit),
        "shared_read_blocks": summarize(read),
        "plans": dict(shapes),
        "rows_returned": dict(rows),
        "samples_ms": times,
    }


async def run(args: argparse.Namespace) -> dict:
    conn = await asyncpg.connect(args.database_url)
    try:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS bench")
        await conn.execute("SET search_path TO bench")
        await conn.execute(SCHEMA)
        expected = args.conversations * args.messages_per_conversation
        if await conn.fetchval("SELECT count(*) FROM messages") != expected:
            started = time.perf_counter()
            await seed(conn, args.conversations, args.messages_per_conversation)
            print(f"seeded {expected} messages in {time.perf_counter() - started:.1f} s")

        ids = random.Random(args.seed).sample(range(1, args.conversations + 1), args.sample)
        await conn.execute(f"CREATE INDEX IF NOT EXISTS {INDEX} ON messages (conversation_id, id)")
        with_index = await time_queries(conn, ids)

        await conn.execute(f"DROP INDEX {INDEX}")
        await conn.execute("ANALYZE messages")
        try:
            without_index = await time_queries(conn, ids)
        finally:
            started = time.perf_counter()
            await conn.execute(f"CREATE INDEX {INDEX} ON messages (conversation_id, id)")
            build_ms = (time.perf_counter() - started) * 1000
            await conn.execute("ANALYZE messages")

        settings = {
            name: await conn.fetchval(f"SHOW {name}")
            for name in ("shared_buffers", "work_mem", "max_parallel_workers_per_gather", "jit")
        }
        return {
            "started_at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            "server_version": await conn.fetchval("SHOW server_version"),
            "settings": settings,
            "query": READ_PATH,
            "conversations": args.conversations,
            "messages": await conn.fetchval("SELECT count(*) FROM messages"),
            "table_size": await conn.fetchval("SELECT pg_size_pretty(pg_table_size('messages'))"),
            "index_size": await conn.fetchval(f"SELECT pg_size_pretty(pg_relation_size('{INDEX}'))"),
            "index_build_ms": build_ms,
            "seed": args.seed,
            "sampled_conversations": args.sample,
            "machine": args.machine,
            "with_index": with_index,
            "without_index": without_index,
        }
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database-url", default="postgresql://chat:chat@localhost:5432/chat")
    parser.add_argument("--conversations", type=int, default=20_000)
    parser.add_argument("--messages-per-conversation", type=int, default=50)
    parser.add_argument("--sample", type=int, default=50, help="conversations timed per run")
    parser.add_argument("--seed", type=int, default=1, help="picks which conversations are sampled")
    parser.add_argument("--machine", default="", help="the hardware, for the record")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    result = asyncio.run(run(args))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    w, wo = result["with_index"]["execution_ms"], result["without_index"]["execution_ms"]
    print(
        f"with index p50 {w['p50']:.3f} ms p95 {w['p95']:.3f} ms; "
        f"without p50 {wo['p50']:.3f} ms p95 {wo['p95']:.3f} ms -> {out}"
    )


if __name__ == "__main__":
    main()
