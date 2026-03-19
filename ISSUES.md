# Issues found during Tesco xapi.tesco.com testing

## Issue 1: No early abort on repeated 403/401 responses

**Status:** Fixed.

`Client` tracks consecutive 401/403 responses. After a configurable threshold
(default 10, `max_consecutive_auth_errors`), it raises `AuthError`.
`blind_introspection()` catches this, saves checkpoint, and returns partial
results. Any successful response resets the counter.

---

## Issue 2: Previous slow-mode hang (pre-patch)

**Status:** Fixed.

Per-field INFO logging, phase announcements, retry logging, `--json-log` flag,
and iteration summaries implemented. The 403-abort (Issue 1) prevents silent
hangs from expired tokens.

---

## Issue 3: `fetch_root_typenames` returns None when server requires auth for `__typename`

**Status:** Fixed.

`schema.add_type(typename, "OBJECT")` added after `probe_typename()` in
`oracle.py:clairvoyance()`. Additionally (Issue 12c fix), `probe_typename` now
sets `schema._schema["queryType"]` (or mutationType/subscriptionType) based on
the `input_document` prefix, so checkpoints preserve the root type reference.

---

## Issue 4: `fullmatch` fails on servers that sanitize error messages

**Status:** Fixed.

Centralized `normalize_error_message()` strips `<[REDACTED]>`, `[FILTERED]`,
and `[REMOVED]` suffixes before regex matching. All four async entry points
(`probe_valid_fields`, `probe_valid_args`, `probe_typeref`, `probe_typename`)
normalize before passing to regex functions. New VALID_FIELD and TYPEREF regex
patterns handle `must have a selection of subfields.` without "Did you mean".

---

## Issue 5: Akamai WAF blocks clairvoyance due to missing browser headers and rate limiting

**Status:** Fixed.

- Default browser User-Agent (overridable via `-H`)
- `--rate-limit <N>` flag for request pacing
- Cookie jar enabled by default (aiohttp `CookieJar` persists `Set-Cookie`
  headers including WAF cookies like `ak_bmsc`). Disable with `--no-cookies`.
- Retry logging at INFO level for all three retry paths (5xx, JSON decode
  error, connection error)

---

## Issue 6: `get_path_from_root` crash when no fields discovered

**Status:** Fixed.

`blind_introspection()` catches `ValueError` from `get_path_from_root()`, logs
a warning, and returns partial results.

---

## Issue 7: Retry logging and `--json-log` don't emit output during type probing retries

**Status:** Fixed.

Both `setup_logger` code paths use `FlushingStreamHandler`, a `StreamHandler`
subclass that writes directly via `os.write(fd)` to bypass Python-level
buffering. The old `logging.basicConfig` path has been replaced.

---

## Issue 8: Log output only visible on process exit (not real-time)

**Status:** Fixed.

`FlushingStreamHandler` uses `os.write(fd, data)` to bypass Python's stream
buffering. `setup_logger()` calls `_force_unbuffered_stderr()` which replaces
`sys.stderr` with an unbuffered `io.TextIOWrapper` via `os.dup` +
`write_through=True`. Falls back to `stream.write()` + `flush()` for non-fd
streams.

---

## Issue 9: Akamai triggers 403 block within 2 minutes at higher concurrency

**Status:** Operational guidance (not a code bug).

Safe settings: `-c 1 --rate-limit 3 -x http://127.0.0.1:8080 -k`. No Bearer
token needed for error-based schema discovery. The endpoint returns GraphQL
validation errors (400) without auth.

---

## Issue 10: No feedback during arg probing — appears hung to the user

**Status:** Fixed (Problems A, B, C all resolved).

- **Problem A (too noisy):** One log line per completed arg showing result
  (`Arg type 3/55: addressLine2 -> String (SCALAR)`), not per-request.
  `ProgressTracker` provides 30s periodic summaries for long arg lists.
- **Problem B (no ETAs):** `ProgressTracker` on arg type probing provides
  step-level ETA. Field completion lines show `[~Xm total remaining]`.
- **Problem C (rich progress interleaving):** `--json-log` disables rich
  progress bars. JSON log mode is for machine consumption; progress bars are
  for interactive terminals.

---

## Issue 11: 502 error causes infinite retry loop — no backoff, no abort

**Status:** Fixed (all 3 problems resolved).

1. **Retry counter bug:** `post()` restructured from recursion to a loop.
   The old recursive approach re-acquired the semaphore on each retry, causing
   deadlock with `concurrent_requests=1`. Now `_do_post()` runs inside the
   semaphore and returns `None` to signal retry; the loop in `post()` handles
   incrementing outside the semaphore.
2. **5xx abort threshold:** `ServerError` exception with `_track_server_error()`
   counter (mirrors auth error pattern). After `max_consecutive_server_errors`
   (default 10) consecutive 5xx responses, raises `ServerError`. Successful
   responses reset the counter.
3. **SIGINT handling:** `blind_introspection()` catches `KeyboardInterrupt`
   and `asyncio.CancelledError`, saves checkpoint, returns partial results.
   `cli()` wraps `asyncio.run()` in `try/except KeyboardInterrupt`.

---

## Issue 12: Checkpoint only saves at end of iteration — 5+ hours of progress lost

**Status:** Fixed (12a, 12b, 12c all resolved).

- **Incremental checkpoint:** `oracle.clairvoyance()` accepts `on_field_complete`
  callback that fires after each field. `blind_introspection()` passes a callback
  that saves the checkpoint file.
- **12a (wrong iteration number):** `iterations += 1` moved to after
  `oracle.clairvoyance()` returns, so mid-iteration checkpoints save the current
  iteration number, not the next.
- **12b (re-explores completed fields):** `clairvoyance()` compares discovered
  fields against `schema.types[typename].fields` and skips any already present.
- **12c (false "already complete"):** Resume always re-runs the saved iteration
  using the saved `input_document`, instead of checking `get_type_without_fields`.
  `probe_typename` sets `schema._schema["queryType"]` so checkpoints preserve
  the root type reference even when `fetch_root_typenames` fails.

---

## Issue 13: Discovered field types not saved until arg probing completes

**Status:** Fixed.

`clairvoyance()` now uses a two-phase approach:

1. **Phase 1 (Type probing):** All field types are probed concurrently (fast,
   ~2 requests per field). Each field is added to the schema with empty args
   and checkpoint is saved immediately. This captures all field names, return
   types, and type kinds within seconds.

2. **Phase 2 (Arg probing):** Non-scalar fields have their args probed
   sequentially. After each field's args are fully probed, checkpoint is saved
   again with the updated args.

The old `explore_field()` function (which was atomic — type + args all at once)
has been removed. With this fix, a 30-second type discovery phase saves all 70+
field types immediately. On resume, all type-discovered fields are skipped by
the 12b logic.

**Bonus fix:** `Schema.__repr__()` was mutating `self._schema["types"]` by
appending on every call, causing duplicate type entries in serialized JSON.
Fixed to build a new dict instead of mutating the internal state.

---

## Issue 14: Arg type probing is the primary speed bottleneck

**Observed:** With `-c 1 --rate-limit 3`, each field takes 10+ minutes because
arg type probing sends 2-5 requests PER discovered arg. With the 250-word
field wordlist reused for arg probing, a field that discovers 50 valid args
needs 100-250 requests just for arg types — at rate-limit 3, that's 30-80
seconds per field on arg types alone, multiplied by 75+ fields.

**Root cause:** `probe_arg_typeref()` calls `probe_typeref()` which sends up to
5 document variants per arg to determine its type:

```python
field(arg: 42)       # test Int
field(arg: {})       # test InputObject
field(arg[:-1]: 42)  # test truncated name
field(arg: "42")     # test String
field(arg: false)    # test Boolean
```

Each variant is a separate HTTP request. For 50 args, that's 250 requests.

### Fix A: Batch arg type probing

Instead of probing one arg at a time, send multiple args with different typed
values in a single query:

```graphql
query { field(arg1: 42, arg2: "str", arg3: false, arg4: {}, arg5: 42, ...) }
```

The server returns errors like `Expected type String, found 42` or
`Expected type Int, found "str"` for each arg that has the wrong type. A single
request with all discovered args can reveal most types simultaneously.

**Strategy:**
1. Send all args with value `42` (tests Int). Parse errors for each arg.
2. Args that returned `Expected type String` → confirmed String.
3. Args that returned `Expected type Boolean` → confirmed Boolean.
4. Args that returned `Expected type [SomeInput]` → confirmed InputObject.
5. Only args with ambiguous errors need individual follow-up probes.

This reduces arg type probing from N×5 requests to ~3-5 requests total per
field, regardless of how many args were discovered. A field with 50 args would
drop from 250 requests to ~5.

### Fix B: Common-type-first heuristic

Most GraphQL args are `String`, `Int`, `ID`, or `Boolean`. Instead of probing
each arg independently through all 5 document variants, use a cascading batch
approach:

1. **Batch 1:** Send all args as `42` (Int literal). Parse error messages:
   - `Expected type String` → mark as String
   - `Expected type Boolean` → mark as Boolean
   - `Expected type SomeInput` → mark as InputObject
   - No error for this arg → it accepts Int (or ID)
   - `Expected type ID` → mark as ID

2. **Batch 2:** Send remaining unresolved args as `"test"` (String literal).
   This resolves Int vs ID ambiguity and catches any String args that weren't
   caught in batch 1.

3. **Batch 3 (if needed):** Individual probes only for args that still have
   ambiguous types (rare).

Most args resolve in batch 1 or 2. Total: 2-3 requests per field instead of
N×5.

**Combined impact:** Fix A + Fix B together would reduce a 50-arg field from
~250 requests to ~3 requests. At rate-limit 3, that's ~1 second instead of
~80 seconds. A full 75-field iteration would drop from ~100 minutes to ~5
minutes for arg type probing.

**Status:** Fixed (Fix A + Fix B implemented).

`probe_arg_typerefs_batch()` sends 1-2 batch requests with unique integer
sentinel values (7001, 7002, ...) to map `Expected type X, found 700N.` errors
back to specific args. Three error patterns are matched:

1. `Expected type X, found <sentinel>.` — sentinel maps to arg, `X` is the type
2. `String cannot represent a non string value: <sentinel>` — sentinel maps to
   arg, scalar type extracted from message prefix
3. `Field "F" argument "A" of type "T" is required...` — arg name `A` and type
   `T` extracted directly from message

Batch 1 uses int sentinels. Unresolved args go to batch 2 with string sentinels
(`"t7001"`, `"t7002"`, ...). Any still-unresolved args fall back to individual
`probe_arg_typeref()` (5 requests per arg).

A 50-arg field drops from ~250 requests to ~2-3 requests in the common case.
`<[REDACTED]>` suffixes are handled via `normalize_error_message` before
matching.

---

## Issue 15: "Cannot query field" error incorrectly parsed as valid field with type

**Status:** Fixed.

**Root cause:** `_TYPEREF_REGEXES['FIELD']` included two `Cannot query field`
patterns that matched `Cannot query field "X" on type "Y".` and extracted `Y`
as the field's return type. But this error means field X is **invalid** on type
Y — the type name is the parent type, not the field's return type. This caused
invalid fields to be added with `type=Query (OBJECT)`, producing false positives
that doubled the field count and wasted requests on arg probing.

**Fix:** Removed both `Cannot query field` patterns from
`_TYPEREF_REGEXES['FIELD']`:
- `Cannot query field X on type Y.` (plain)
- `Cannot query field X on type Y. Did you mean ...?` (with suggestion)

These patterns are only useful for `probe_typename` which has its own
`_WRONG_TYPENAME` regexes. The remaining TYPEREF FIELD patterns (5 patterns)
all match errors that genuinely reveal a field's return type:
- `Field "X" of type "Y" must have a selection of subfields`
- `Field "X" must not have a selection since type "Y" has no subfields`
- `Field "X" of type "Y" must (not) have a sub selection`

Updated two existing tests (`test_field_regex_3`, `test_field_regex_4`) that
asserted the old (incorrect) behavior. Added 5 new tests verifying that
`Cannot query field` returns `None` while valid field patterns still work.

---

## Issue 16: `EndpointError` crash when field type can't be determined

**Observed:** After fixing Issue 15 (removing `Cannot query field` from typeref
regexes), clairvoyance crashes on field `expiry` after probing only 5 fields:

```
EndpointError: Unable to get TypeRef for ['query { expiry }',
'query { expiry { lol } }'] in context FuzzingContext.FIELD.
It is very likely that Field Suggestion is not fully enabled on this endpoint.
```

**Root cause:** `probe_typeref()` raises `EndpointError` when none of the
TYPEREF regexes match the server's error messages. Before Issue 15, the
`Cannot query field "X" on type "Y"` pattern would (incorrectly) match and
return `TypeRef(name="Query")`. Now it correctly returns None, but the code
treats "no typeref" as a fatal error.

Some valid fields (likely scalars like `expiry`) produce error messages that
don't match any remaining TYPEREF pattern. For example, the server might return
a generic error or a format clairvoyance doesn't recognize.

**Fix:** Instead of raising `EndpointError`, skip the field with a warning:

```python
# In probe_typeref(), replace the raise with:
if not typeref and context != FuzzingContext.ARGUMENT:
    log().warning(
        f"Could not determine type for {documents[0]}. "
        f"Skipping field (unknown type)."
    )
    return None

# In clairvoyance() Phase 1, handle None typeref:
if typeref is None:
    log().info(f"  {typename}.{field_name}: type=unknown (skipped)")
    continue  # don't add to schema, don't probe args
```

Fields with unknown types are simply omitted from the schema. This is safe
because:
- They might be false positives from field discovery
- Even if valid, we can't explore their subfields without knowing their type
- The field name is still in the wordlist for future iterations on other types

**Status:** Fixed.

`probe_typeref()` no longer raises `EndpointError` when no TYPEREF regex
matches. Instead it logs a warning and returns `None`. In `clairvoyance()`
Phase 1, `None` typerefs cause the field to be logged as `type=unknown
(skipped)` and omitted from the schema. The field is not added to the schema,
not probed for args, and not checkpointed. Updated existing test that expected
`EndpointError` to expect `None` return.

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
