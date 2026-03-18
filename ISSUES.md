# Issues found during Tesco xapi.tesco.com testing

## Issue 1: No early abort on repeated 403/401 responses

**Observed:** When the Bearer token expired mid-scan, clairvoyance continued
sending requests and received 403 responses with non-JSON bodies. It logged
hundreds of `JSON decode error` warnings but never stopped. In slow mode with
retries, this caused a 26+ minute hang with no useful output.

**Expected:** Clairvoyance should detect a pattern of auth failures (e.g. N
consecutive 403/401 responses) and abort early with a clear message like:

```
ERROR: Received 10 consecutive 403 responses. Token may have expired.
       Partial results saved to checkpoint. Re-run with a fresh token to resume.
```

**Where to fix:** `clairvoyance/client.py` or the response handling in
`oracle.py`. Add a consecutive-error counter that triggers abort after a
configurable threshold (default 10).

**Severity:** High — without this, the tool wastes time and gives no feedback
when auth expires. This is the #1 usability problem encountered.

**Status:** Fixed. `Client` now tracks consecutive 401/403 responses across all
requests. After a configurable threshold (default 10, set via
`max_consecutive_auth_errors` parameter), it raises `AuthError`.
`blind_introspection()` catches this, saves the checkpoint if configured, and
returns partial results with a clear error message. Any successful (non-401/403)
response resets the counter.

---

## Issue 2: Previous slow-mode hang (pre-patch)

**Observed:** Before the `normalize_error_message` and regex patches, the first
run in slow mode appeared to hang for 26+ minutes. The output file was never
updated after the initial progress bars. The process was alive and had active
network connections but produced no output.

**Root cause (likely):** A combination of:
1. Rich progress bars not flushing in non-TTY contexts
2. No INFO-level logging during field probing (all logging was DEBUG)
3. The token expired and slow-mode retries kicked in silently

The user-applied INFO logging patches (per-field progress, phase announcements)
fix points 1 and 2. Point 3 is addressed by Issue 1 above.

**Status:** Fixed. Per-field INFO logging, phase announcements, retry logging,
`--json-log` flag, and iteration summaries have been implemented. The
403-abort issue (point 3) is tracked separately in Issue 1.

---

## Issue 3: `fetch_root_typenames` returns None when server requires auth for `__typename`

**Observed:** The server returned an error for `query { __typename }` instead of
data, so `fetch_root_typenames` set `queryType=None`. This meant `Query` was
never added to `schema.types`. Later, `probe_typename` discovered it was `Query`
via error messages, but `schema.types["Query"]` raised `KeyError`.

**Fix applied:** Added `schema.add_type(typename, "OBJECT")` after
`probe_typename()` in the `clairvoyance()` function. This is idempotent and
safe.

**Status:** Fixed. `schema.add_type(typename, "OBJECT")` added after
`probe_typename()` in `oracle.py:clairvoyance()`.

---

## Issue 4: `fullmatch` fails on servers that sanitize error messages

**Observed:** The Tesco API appends `<[REDACTED]>` to error messages where
"Did you mean X?" suggestions would normally appear. Since all regex matching
uses `re.fullmatch()`, the trailing `<[REDACTED]>` causes every pattern to fail.

**Fix applied:** Added `normalize_error_message()` function that strips known
sanitization suffixes. Added a new VALID_FIELD regex for
`must have a selection of subfields.` without requiring "Did you mean".

**Status:** Fixed. Four inline `re.sub` calls replaced with a centralized
`normalize_error_message()` function that strips `<[REDACTED]>`, `[FILTERED]`,
and `[REMOVED]` suffixes. New VALID_FIELD and TYPEREF regex patterns added for
`must have a selection of subfields.` without "Did you mean". A more resilient
approach would be switching from `fullmatch` to `match` throughout, but this
requires careful review of every regex to prevent false positives.

---

## Issue 5: Akamai WAF blocks clairvoyance due to missing browser headers and rate limiting

**Observed:** The Tesco GraphQL endpoint sits behind Akamai CDN. Clairvoyance
gets blocked in two ways:

1. **Missing browser headers:** Akamai returns HTML 403 (`Access Denied`) when
   requests lack browser-like `User-Agent`, `Origin`, and `Referer` headers.
   Clairvoyance uses aiohttp's default Python User-Agent which triggers this.
   Adding these via `-H` fixes the initial block.

2. **Rate limiting / bot detection:** Even with browser headers, a burst of
   concurrent requests (default concurrency) triggers Akamai's bot detection.
   After the burst, the IP gets temporarily flagged and ALL subsequent requests
   return 403 — even single `curl` requests. The ban appears to last several
   minutes.

**The successful first run** (with the old token) worked because:
- It was the first time requests were sent from this IP
- Akamai hadn't yet built a bot profile for this client
- After 26+ minutes of retries, the IP was likely flagged

**What's needed to make clairvoyance work behind Akamai:**
1. Browser-like default headers (or at least don't use Python UA)
2. Rate limiting / request pacing (e.g. max N requests per second)
3. Cookie jar support — Akamai sets `ak_bmsc` cookies that must be sent back
   on subsequent requests, otherwise the client looks like a bot
4. Or route through a browser proxy (Caido) using `-x` flag

**Recommended approach for clairvoyance:**
- Add a `--rate-limit` flag (e.g. `5/s` = max 5 requests per second)
- Add cookie jar support (aiohttp `CookieJar`) to persist cookies across
  requests within a session — this is the most impactful change since WAFs
  like Akamai, Cloudflare, and Imperva all use cookie-based bot detection

**Workaround:** Route through Caido proxy with `-x http://localhost:<port>`.
Caido's proxy can add cookies/headers automatically. However, the Caido proxy
port needs to be accessible from the environment running clairvoyance.

**Status:** Fixed. All recommended mitigations implemented:
- Default browser User-Agent (overridable via `-H`)
- `--rate-limit <N>` flag for request pacing (e.g. `--rate-limit 5` = 5 req/s)
- Cookie jar enabled by default (aiohttp `CookieJar` persists `Set-Cookie`
  headers across requests within a session, including WAF cookies like
  `ak_bmsc`). Disable with `--no-cookies`.
- Retry logging at INFO level (see note below)

**Additional note on retry logging:** Fixed. All three retry paths (HTTP 5xx,
JSON decode error, connection error) now emit INFO-level logs with the format
`Retry {n}/{max} after HTTP {status} (backoff {delay}s)`. Logging fires
regardless of whether backoff is configured.

---

## Issue 6: `get_path_from_root` crash when no fields discovered

**Observed:** When all field probes fail (e.g. due to 403s), iteration 1
completes with 0 fields. The iteration loop then tries to find unexplored types
and calls `get_path_from_root('Query')`, which fails with:

```
ValueError: Could not find path from root to 'Query'
Current path: []
```

This happens because `Query` has no fields (they all failed to probe), so no
path from root exists.

**Expected:** Clairvoyance should detect "0 fields discovered" and either retry
the iteration or abort gracefully with the partial checkpoint saved.

**Status:** Fixed. `blind_introspection()` now catches `ValueError` from
`get_path_from_root()`, logs a warning explaining the likely cause (auth
expired or endpoint blocking), and breaks the loop returning partial results.

---

## Issue 7: Retry logging and `--json-log` don't emit output during type probing retries

**Observed:** Despite Issue 2 being marked fixed (retry logging, `--json-log`),
the process still produces no output after `[1/76]` when subsequent requests
get 403'd by Akamai. Tested with:
- `PYTHONUNBUFFERED=1`
- `python3 -u`
- `stdbuf -oL tee`
- `--json-log` flag

All produced the same result: output stops at `[1/76]` and nothing appears for
minutes despite the process being alive and using CPU. The retry logging either:
1. Is not being triggered in the `probe_field_type` / `probe_typeref` code path
2. Is being triggered but not flushed to stdout/stderr

**How to reproduce:**
```bash
PYTHONUNBUFFERED=1 python3 -u -m clairvoyance \
  -w wordlist.txt --rate-limit 3 -p slow -c 1 --json-log --progress \
  -H "Authorization: Bearer <token>" \
  https://xapi.tesco.com/ 2>&1 | stdbuf -oL tee /tmp/live.log
```
Wait 60 seconds after `[1/76]` appears — no further output.

**Expected:** Retry log lines like `Retry 1/3 after HTTP 403 (backoff 1.0s)`
should appear in the output between field completions.

**Likely root cause:** The retry logging may be using Python's logging module
which buffers via `StreamHandler`. Even with `PYTHONUNBUFFERED=1`, the logging
module has its own buffer. The fix is to add `handler.flush()` after each log
emit, or set the handler's stream to unbuffered, or use `print()` with
`flush=True` for the JSON log output.

**Status:** Fixed. Both `setup_logger` code paths now use `FlushingStreamHandler`,
a `StreamHandler` subclass that calls `self.flush()` after every `emit()`. This
ensures log lines are immediately visible even when stderr is piped to a file or
another process (non-TTY). The old `logging.basicConfig` path (which creates a
non-flushing `StreamHandler`) has been replaced.

---

## Issue 8: Log output only visible on process exit (not real-time)

**Observed:** Despite `FlushingStreamHandler`, log output is NOT visible in
real-time when captured by a subprocess pipe (e.g. Claude Code's background task
runner). All log lines are buffered and only appear when the process exits
(either normally or via SIGTERM).

**Confirmed by final run:** A run that completed in ~2.5 minutes (auth abort)
produced its full output on exit — retry logging, auth abort, everything worked.
But during the 63-minute run that was killed externally, only the first 9 lines
(written before the asyncio event loop started) were visible.

**Root cause:** The `FlushingStreamHandler.flush()` calls `self.stream.flush()`
which flushes Python's `StreamHandler` buffer, but when the output fd is a pipe
managed by an external process (e.g. Claude Code's task runner), the pipe itself
has a kernel buffer that doesn't get flushed by Python's `flush()`. Only process
exit triggers the pipe buffer to drain.

**Fix:** Use `os.write(sys.stderr.fileno(), ...)` for direct fd writes, or
set `sys.stderr = os.fdopen(sys.stderr.fileno(), 'w', buffering=1)` at startup
to force line-buffered mode at the fd level. Alternatively, write log lines to
a file in addition to stderr (file I/O flushes immediately with `open(...,
buffering=1)`).

**Workaround:** Run clairvoyance through a proxy (Caido `-x`) to monitor
request/response activity, since the log output won't be visible until exit.

---

## Issue 9: Akamai triggers 403 block within 2 minutes at higher concurrency

**Observed:** With `--rate-limit 10 -c 3` (no Bearer token needed), Akamai
blocked all requests after ~2 minutes of operation:
- 18:52:14 — scan started, 72 Query fields discovered
- 18:52:30 — `[1/72]` completed
- 18:54:38 — first 403 from Akamai (2 minutes 8 seconds into scan)
- 18:54:39 — 10 consecutive 403s → auth abort triggered, process exited

With `--rate-limit 5 -c 1` and a Bearer token through Caido proxy, the scan
ran for 63+ minutes before the token expired — Akamai never blocked it. This
means `-c 1 --rate-limit 5` (or lower) is the maximum safe throughput.

**Key finding:** The endpoint does NOT require a Bearer token for error-based
schema discovery. GraphQL validation errors (400) are returned without auth.
Only Akamai's rate limiting is the constraint.

**Recommended settings for this target:**
```
-c 1 --rate-limit 3 -x http://127.0.0.1:8080 -k
```
No `-H "Authorization: Bearer ..."` needed.

**Time estimate at safe rate:** 72 fields × ~5 requests × 0.33s = ~2 minutes
for type probing. But arg probing with 250 words per field adds ~72 × 4 buckets
× 0.33s = ~1.5 minutes per field with args. With ~30 object-type fields needing
arg probing: ~45 minutes for iteration 1. Subsequent iterations add more time
for nested types.

**Suggestions to speed up without triggering Akamai:**
1. **`--skip-args` flag** — skip argument discovery entirely on first pass.
   Discover all fields and types first, then run a second pass for args. This
   would reduce iteration 1 from ~45 min to ~5 min.
2. **Mid-iteration checkpoint** — save progress after each field so that if
   Akamai blocks or the process is interrupted, progress is preserved.
3. **Separate `--arg-wordlist`** — use a smaller dedicated wordlist for arg
   probing instead of reusing the full field wordlist (250 words). Common
   GraphQL arg names (id, input, first, last, after, before, where, filter,
   orderBy, limit, offset, skip, take) cover most cases with ~20 words.

---

## Testing notes

- Endpoint: `https://xapi.tesco.com/` (POST, GraphQL)
- Required headers: `Authorization: Bearer <jwt>`, `x-apikey`, `region: UK`,
  `language: en-GB`, `content-type: application/json`
- Additional headers needed for Akamai: `User-Agent` (browser), `Origin`,
  `Referer`
- Token lifetime: ~1 hour (JWT with `exp` claim)
- 75-80 root Query fields discovered from 250-word gqlextractor wordlist
- Server redacts "Did you mean" suggestions with literal `<[REDACTED]>` (only
  observed with `scope: "internal public"` tokens; `scope: "internal"` tokens
  return clean errors without `<[REDACTED]>`)
- Server returns 403 with empty body when token expires (not JSON)
- Akamai CDN blocks after burst of concurrent requests; bans last several
  minutes
