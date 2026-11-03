# chat-stream-postgres

A chat app that streams model replies to the browser token by token over server-sent events (SSE), stores every conversation in Postgres, and records how long each reply took to start and to finish.

I built it as a small, complete version of the path a chat product depends on: a React page, a FastAPI backend that relays the model's stream, and Postgres underneath. The backend talks to any server that speaks the OpenAI API. By default that is a local model in Ollama, so it runs with no API key and costs nothing. I measured latency through the whole stack on my laptop and timed the conversation read path with and without its index; the numbers and how I got them are further down.

## Run it

You need Docker, Python 3.12 or later (I used 3.13), Node 22 and [Ollama](https://ollama.com).

```sh
ollama pull llama3.2:3b
docker compose up -d --wait

cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:create_app --factory --port 8000
```

In a second terminal:

```sh
cd frontend
npm ci
npm run dev
```

Then open http://localhost:5173. Vite passes `/api` through to the backend on port 8000, so the page and the API share an origin. The backend creates its tables on startup. If port 5432 is taken, start Postgres with `PG_PORT=55432 docker compose up -d --wait` and set `DATABASE_URL=postgresql://chat:chat@localhost:55432/chat`.

### Settings

All settings are environment variables read once at startup (`backend/app/config.py`).

| variable | default | what it does |
| --- | --- | --- |
| `OPENAI_BASE_URL` | `http://localhost:11434/v1` | the model server; this default is Ollama's OpenAI-compatible API |
| `OPENAI_API_KEY` | `ollama` | sent as a bearer token; Ollama ignores it |
| `MODEL` | `llama3.2:3b` | model name passed to the server |
| `API_STYLE` | `responses` | `responses` (POST /responses) or `chat_completions` (POST /chat/completions) |
| `INSTRUCTIONS` | `You are a helpful assistant. Keep answers short.` | sent as `instructions`, or as the system message for chat completions |
| `MAX_OUTPUT_TOKENS` | `512` | cap on each reply |
| `TEMPERATURE` | unset | sent only when set |
| `HISTORY_MESSAGES` | `20` | how many earlier finished messages go to the model with each new one |
| `UPSTREAM_TIMEOUT_S` | `60` | longest wait for the next chunk from the model server |
| `DATABASE_URL` | `postgresql://chat:chat@localhost:5432/chat` | Postgres |

I have run it against Ollama 0.40.13 in both API styles. I have not run it against api.openai.com or any other hosted server.

## How a reply travels

1. The page POSTs `{conversation_id, message}` to `/api/chat`. The first message of a conversation has no id.
2. The backend stores the message in one transaction (creating the conversation on the first message) and starts the SSE response. Its first event, `meta`, carries the conversation id.
3. It reads the last 20 finished messages of the conversation and sends them to the model server with the instructions and the token cap. Ollama supports only the stateless form of the Responses API (no `previous_response_id`), so the history is read from Postgres on every turn. That also keeps the backend independent of which server it talks to.
4. Each text delta from the model server (`response.output_text.delta`, or `choices[0].delta.content` for chat completions) goes to the page as a `token` event, and the page appends it.
5. When the model server finishes, the backend stores the reply with its time to first token, total time and token count, and sends the same numbers in a `done` event. The page shows them under the reply.

| event | data |
| --- | --- |
| `meta` | `conversation_id`, `user_message_id` |
| `token` | `text` |
| `done` | `message_id`, `model`, `ttft_ms`, `total_ms`, `output_tokens` |
| `error` | `message_id`, `error` (the partial reply is stored with status `error`) |

Other routes: `GET /api/conversations` (the 50 most recently active), `GET /api/conversations/{id}/messages`, `GET /api/health`.

## Design choices

**SSE over a POST, read with `fetch`.** `EventSource` can only send a GET, which would put the message in the URL. The page reads the response body itself and parses SSE in `frontend/src/sse.ts`. Network chunks can split an event anywhere, so the parser keeps the unfinished tail, and holds back a CR at the end of a chunk in case the LF is in the next one. The text decoder runs in streaming mode so a character whose bytes are split across two chunks survives. The tests cut a stream at every position and check the events come out the same.

**httpx straight to the HTTP API, not an SDK.** The relay (`backend/app/upstream.py`) reads each SSE event from the model server as it arrives and reduces both API styles to two events, a text delta and the end of the reply. That keeps one code path for both styles, and lets the tests put `httpx.MockTransport` in place of the model server. The parser is also tested against two streams I captured from Ollama with curl (`backend/tests/data/`).

**No database connection is held while a reply streams.** A reply takes seconds, and holding a pooled connection for that long would cap concurrent replies at the pool size (10). The relay checks out a connection twice, briefly: once to read the history and once to store the reply. The fake-model run below with 30 replies in flight is there to show this.

**A reply is stored even when it does not finish.** If the user presses Stop or closes the tab, Starlette cancels the response, and the save runs in an `anyio` cancel scope shielded from that cancellation, so the reply is stored with status `cancelled` and the text that had arrived. A failure from the model server mid-reply is stored with status `error`. Neither is sent back to the model as history. Leaving the stream early also closes the HTTP connection to the model server, so it can stop generating. `backend/tests/test_cancel.py` covers both ways Starlette can end a response.

**The schema** (`backend/app/schema.sql`) has two tables. `messages` checks the role and status, that timings are not negative, that the first token does not come after the end, and that only replies carry a model name, timings or a non-complete status. Messages are ordered by their identity `id` rather than `created_at`, because `now()` is the transaction's start time, so two rows written in one transaction get the same value. The index on `(conversation_id, id)` serves both read paths, the conversation page and the history sent to the model. Postgres does not index a foreign key by itself, so the same index keeps `ON DELETE CASCADE` from scanning the whole table.

**What the timings mean.** The backend measures time to first token from just before it sends the request to the model server until the first text delta arrives, and total time until the model server's last event. These are the numbers stored with each reply and shown on the page. They leave out the hop between the browser and the backend; the measurement script below also times requests from the client side.

## Measurements

Everything here was measured on one laptop: 13th Gen Intel Core i7-13700HX (16 cores, 24 threads), NVIDIA GeForce RTX 4050 Laptop GPU (6141 MiB), 29.7 GB RAM, Windows 11, with Postgres 17.11 in Docker 28.4.0 and Ollama 0.40.13 serving llama3.2:3b (3.2B parameters, Q4_K_M). The client, the backend, the model and the database all ran on that machine. The result files in `results/` record the settings each run used, and the tables below are generated from them by `bench/summarize.py` (CI fails if they drift apart).

### Reply latency

`bench/latency.py` sends the 10 prompts in `bench/prompts.json` three times over (30 requests per run) after one warm-up request that is not recorded. Each request starts a new conversation, so every prompt goes to the model with the same empty history. Both backends ran with `MAX_OUTPUT_TOKENS=256` and `TEMPERATURE=0`. TTFT and total are measured by the client: from sending the POST to the first `token` event, and to the `done` event. The last column is the client's TTFT minus the backend's own TTFT for the same request, which is what the backend and the HTTP hops add before the first token. p50 and p95 are nearest-rank over the 30 requests, so p95 is the second slowest. I ran each setup three times.

The fake model (`bench/fake_upstream.py`) waits 50 ms before its first token and sends 64 tokens 10 ms apart, so the rows against it measure the backend and Postgres with no model in the way. Its pauses came out longer than asked on this machine: by the backend's own clock a fake reply took 1037 to 1047 ms (p50 per run) rather than the 680 ms it is paced for. That stretches the totals, not the time the backend adds. In the "30 in flight" rows all 30 requests of a run are sent at once to one backend process with a pool of 10 connections.

<!-- latency:start -->
| setup | run | requests | reply tokens p50 | TTFT p50 | TTFT p95 | total p50 | total p95 | added by backend to TTFT, p50 / p95 |
|---|---|---|---|---|---|---|---|---|
| llama3.2:3b on Ollama, 1 in flight | 1 | 30 | 52 | 353 ms | 368 ms | 1.01 s | 2.14 s | 9.0 ms / 12 ms |
| llama3.2:3b on Ollama, 1 in flight | 2 | 30 | 52 | 350 ms | 364 ms | 1.03 s | 2.15 s | 8.6 ms / 12 ms |
| llama3.2:3b on Ollama, 1 in flight | 3 | 30 | 52 | 349 ms | 361 ms | 1.00 s | 2.12 s | 8.4 ms / 9.7 ms |
| llama3.2:3b on Ollama, 1 in flight | **spread over 3 runs** | | 52 | 349 ms to 353 ms | 361 ms to 368 ms | 1.00 s to 1.03 s | 2.12 s to 2.15 s | 8.4 ms to 9.0 ms / 9.7 ms to 12 ms |
| fake model, 1 in flight | 1 | 30 | 64 | 70 ms | 75 ms | 1.05 s | 1.07 s | 7.9 ms / 11 ms |
| fake model, 1 in flight | 2 | 30 | 64 | 71 ms | 73 ms | 1.05 s | 1.06 s | 7.9 ms / 14 ms |
| fake model, 1 in flight | 3 | 30 | 64 | 71 ms | 73 ms | 1.05 s | 1.06 s | 7.8 ms / 10 ms |
| fake model, 1 in flight | **spread over 3 runs** | | 64 | 70 ms to 71 ms | 73 ms to 75 ms | 1.05 s | 1.06 s to 1.07 s | 7.8 ms to 7.9 ms / 10 ms to 14 ms |
| fake model, 30 in flight | 1 | 30 | 64 | 258 ms | 279 ms | 1.24 s | 1.26 s | 175 ms / 205 ms |
| fake model, 30 in flight | 2 | 30 | 64 | 165 ms | 208 ms | 1.15 s | 1.19 s | 92 ms / 142 ms |
| fake model, 30 in flight | 3 | 30 | 64 | 194 ms | 251 ms | 1.17 s | 1.22 s | 119 ms / 176 ms |
| fake model, 30 in flight | **spread over 3 runs** | | 64 | 165 ms to 258 ms | 208 ms to 279 ms | 1.15 s to 1.24 s | 1.19 s to 1.26 s | 92 ms to 175 ms / 142 ms to 205 ms |
<!-- latency:end -->

With one reply in flight the backend adds 8 to 9 ms before the first token, about the same against Ollama as against the fake model, so almost all of the 350 ms before Ollama's first token is spent waiting on the model server. Ollama's p50 time to first token moved by less than 5 ms across the three runs.

With 30 replies in flight, each fake reply still took about 1.04 s by the backend's clock, and the whole run of 30 took 1.19 to 1.27 s of wall time (against 31.5 s one at a time), so the 30 streams ran at the same time on 10 pool connections. Had each stream held a connection for its whole length, they would have gone in three waves of ten and the last would have finished after about 3 s. What did grow was the time the backend adds before the first token: 92 to 175 ms at p50, against 8 ms with one reply in flight, and it varied from run to run. I have not broken down where that time goes: the 30 first inserts share 10 connections, and the client, the backend and the fake server share one machine.

### Read path with and without the index

`bench/index_read_path.py` builds the app's tables in a separate `bench` schema and fills them with 1,000,000 messages: 20,000 conversations of 50 messages each, written in the order a busy service would write them (message 1 of every conversation, then message 2 of every conversation, and so on), so one conversation's rows are spread across the table. It then runs `EXPLAIN (ANALYZE, BUFFERS)` on the exact query the conversation page uses (`SELECT ... FROM messages WHERE conversation_id = $1 ORDER BY id`) for 50 sampled conversations, first with the index and then after dropping it, after one untimed pass of each to warm the cache. The times are Postgres's own execution times, without the network or sending the rows. Postgres ran with its default settings, including `shared_buffers` of 128MB, which is smaller than the table. Each run sampled different conversations (seeds 1 to 3).

<!-- index:start -->
| run | conversations timed | with index p50 | with index p95 | without p50 | without p95 | p50 ratio |
|---|---|---|---|---|---|---|
| 1 | 50 | 0.113 ms | 0.142 ms | 59.764 ms | 87.059 ms | 529x |
| 2 | 50 | 0.138 ms | 0.227 ms | 40.344 ms | 52.558 ms | 292x |
| 3 | 50 | 0.178 ms | 0.363 ms | 38.806 ms | 48.714 ms | 218x |
| **spread over 3 runs** | | 0.113 ms to 0.178 ms | 0.142 ms to 0.363 ms | 38.806 ms to 59.764 ms | 48.714 ms to 87.059 ms | 218x to 529x |

- With the index: plan `Sort > Bitmap Heap Scan > Bitmap Index Scan`; per query, p50 of 53 buffer blocks found in Postgres's cache and 0 read from outside it (8 kB each).
- Without it: plan `Gather Merge > Sort > Seq Scan`; per query, p50 of 16278 buffer blocks found in Postgres's cache and 18895 read from outside it (8 kB each).
- Table: 1,000,000 messages, 274 MB; index 30 MB.
<!-- index:end -->

With the index, Postgres finds a conversation's 50 rows through it, touching about 53 blocks, every one of them already in its own cache, and sorts them. Without it, every query reads the whole table with parallel workers, and about half of those blocks come from outside Postgres's cache (I did not measure whether from the operating system's cache or the disk). The ratio moves a lot between runs, 218x to 529x, because the times without the index do; run 1, the first after the table was filled, was the slowest.

### What these numbers do not show

They are single-machine numbers with the client on the same machine, so there is no real network. The model ran on a laptop GPU, and the first token from Ollama depends on whether the model is already loaded (the warm-up request absorbs that here). I did not measure under sustained load, with more than one backend process, or with long conversations, where the history sent to the model grows.

## Tests and CI

```sh
cd backend && python -m pytest -q          # needs the Postgres from docker compose
cd frontend && npm test && npm run typecheck
```

- `backend/tests/test_upstream.py`: both stream parsers against the captured Ollama streams and scripted ones, the request bodies, and HTTP errors, an unreachable server, a stream that ends early, and an event that is not JSON.
- `backend/tests/test_api.py`: the real app and Postgres with a scripted model server: tokens relayed in order, both messages stored with timings, timings that match the pauses in the script, history sent on a follow-up and capped, failures mid-reply, unknown conversations, input limits, the conversation list order.
- `backend/tests/test_cancel.py`: a reply cut off while waiting on the model and one closed at a yield are both stored as `cancelled`, and the stream to the model server is closed.
- `backend/tests/test_schema.py`: the constraints reject bad rows; deleting a conversation deletes its messages.
- `frontend/test/`: the SSE parser at every split point, a character split across chunks, and the page itself: tokens appearing one by one, the timings, Stop, the text staying in the box when the server refuses a message, and load errors with a retry.

GitHub Actions (`.github/workflows/ci.yml`) runs the backend tests against a Postgres 17 service container, checks the README tables against `results/`, and then starts the fake model server and two backends (one per API style) and sends the prompt set through each over real HTTP. No model runs in CI. A second job typechecks, tests and builds the front end.

## Layout

```
backend/app/       config, schema, Postgres queries, the model-server client, the SSE relay, routes
backend/tests/     pytest suite and the captured Ollama streams
frontend/src/      the React page, the SSE parser and the API client
frontend/test/     vitest suite
bench/             latency and index measurements, the fake model server, the README table generator
results/           the result files the README tables come from
```

## Limits

There are no accounts: anyone who can reach the backend can read every conversation, so it binds to localhost. There is no retry when the model server fails; the error is shown and stored. A conversation's title is the first 80 characters of its first message.

## Licence

MIT
