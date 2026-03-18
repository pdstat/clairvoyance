# Patches to clairvoyance/oracle.py

## Summary

Two categories of changes were made to `oracle.py` to handle GraphQL endpoints
that sanitize or truncate their error messages.

---

## Change 1: Strip `<[REDACTED]>` suffix from error messages

**Files:** `oracle.py` lines 191, 254, 429, 509

**What:** Added `re.sub(r"\s*<\[REDACTED\]>$", "", error["message"])` in four
places where error messages are read before regex matching.

**Why:** Some GraphQL servers (observed on Tesco's `xapi.tesco.com`) strip the
"Did you mean X?" suggestion from error messages and replace it with a literal
`<[REDACTED]>` string. Since clairvoyance uses `re.fullmatch()` throughout, this
trailing text causes every regex to fail -- even when the useful type information
is still present in the message.

**Concern -- is this too specific?** The `<[REDACTED]>` literal is specific to
one server implementation. Other servers might use different sanitization
patterns (e.g. truncating the message entirely, replacing with `[FILTERED]`,
or omitting the suggestion silently).

**Recommendation:** Replace the four `re.sub` calls with a single
`normalize_error_message()` function that handles known sanitization patterns
generically:

```python
_SANITIZATION_SUFFIXES = re.compile(
    r"\s*(?:<\[REDACTED\]>|\[FILTERED\]|\[REMOVED\])$"
)

def normalize_error_message(raw: str) -> str:
    """Strip known server-side sanitization suffixes from error messages.

    Many GraphQL implementations redact field suggestions from error
    messages as a security measure. This function removes those suffixes
    so the remaining structured error text can still be parsed by regex.
    """
    return _SANITIZATION_SUFFIXES.sub("", raw)
```

A broader alternative: instead of stripping known suffixes, change the regex
strategy from `fullmatch` to `match` (or `search`) so trailing garbage is
ignored entirely. This would be the most resilient approach but requires careful
review of every regex to ensure they don't accidentally match partial strings.

---

## Change 2: New regex for `must have a selection of subfields` without suggestion

**Files:** `oracle.py` `_FIELD_REGEXES['VALID_FIELD']` and
`_TYPEREF_REGEXES['FIELD']`

**What:** Added a new regex pattern:
```
Field "X" of type "Y" must have a selection of subfields.
```

The existing patterns only matched:
- `...must have a selection of subfields. Did you mean "X { ... }"?`
- `...must have a sub selection.`

**Why:** When a server redacts the "Did you mean" part, the error becomes
`Field "X" of type "Y" must have a selection of subfields.` which is a valid
structured error containing both the field name and type, but neither existing
pattern matched it.

**Concern -- is this too specific?** No, this one is generic. It handles any
server that returns this error without appending a suggestion. Even without
the `<[REDACTED]>` stripping, this pattern matches a real error format that
the original code simply didn't account for. This change should be upstreamed.

---

## Change 3: Ensure discovered typename is added to schema types

**File:** `oracle.py` line 612

**What:** Added `schema.add_type(typename, "OBJECT")` after `probe_typename()`.

**Why:** `fetch_root_typenames()` sends `query { __typename }` to discover the
root type name. If the server returns an error instead of data (e.g. because the
query requires arguments or auth), `queryType` is set to `None` and the `Query`
type is never added to `schema.types`. Later, `probe_typename()` discovers the
typename via error messages (e.g. `Cannot query field "X" on type "Query"`) but
the schema still doesn't have it, causing `KeyError: 'Query'` at line 640.

**Concern -- is this too specific?** No, this is a genuine bug. If
`fetch_root_typenames` fails to discover root types but `probe_typename`
succeeds, the schema is in an inconsistent state. `add_type` is idempotent
(no-op if the type already exists), so this is safe in all cases.

---

## Observability improvements needed

The main problem during this session was lack of feedback. Clairvoyance ran for
26+ minutes with no output beyond the initial progress bars. It was impossible
to tell whether it was making progress, stuck in a retry loop, or hung on a
network call.

### Problem areas

1. **Progress bars don't work in non-TTY contexts.** When output is piped to a
   file or captured by an agent, rich progress bars use carriage returns that
   overwrite previous output. The result is a single static line with no
   visible updates.

2. **No per-field logging at INFO level.** The `explore_field` function probes
   each field's type and arguments but only logs at DEBUG level. In slow mode
   with 76 fields, this means 20+ minutes of silence at the default log level.

3. **No periodic heartbeat.** There is no way to distinguish "working slowly"
   from "hung on a network call" without attaching a debugger.

4. **Retry/backoff is invisible.** When slow mode retries a failed request, no
   log message is emitted at INFO level. The user (or agent) has no idea
   retries are happening.

### Suggested improvements

**A. Structured per-field progress logging at INFO level**

Add an INFO log line each time a field is fully explored:

```python
# In the clairvoyance() main loop, after each task completes:
log().info(
    f"[{completed}/{total}] {typename}.{field.name}: "
    f"type={field.type.name} ({field.type.kind}), "
    f"args={len(field.args)}"
)
```

This gives a clear, parseable stream of progress that works in any context
(TTY, file, agent pipe).

**B. Phase announcements**

Log the start of each phase so it's clear what the tool is doing:

```python
log().info(f"Probing {len(valid_fields)} fields on {typename}...")
log().info(f"Probing arguments for {field_name}...")
log().info(f"Iteration {i} complete: {len(schema.types)} types discovered")
```

**C. Periodic heartbeat in slow mode**

When using `-p slow`, emit a heartbeat every N seconds if no other output has
been produced:

```python
log().info(f"Still working... {completed}/{total} fields explored, "
           f"{requests_sent} requests sent")
```

**D. Retry logging at INFO level**

When a request is retried due to rate limiting or errors:

```python
log().info(f"Retry {attempt}/{max_retries} for {document[:80]}... "
           f"(backoff {delay}s)")
```

**E. Summary at end of each iteration**

```python
log().info(
    f"Iteration {iteration} complete: "
    f"{len(new_types)} new types, "
    f"{len(new_fields)} new fields, "
    f"{len(schema.types)} total types"
)
```

### Agent-friendly output format

For agent consumption, consider a `--json-log` flag that emits one JSON object
per line:

```json
{"event":"field_discovered","typename":"Query","field":"buylist","type":"BuylistGroupType","kind":"OBJECT","args":2,"progress":"3/76","elapsed":45.2}
{"event":"retry","document":"query { buylist { FUZZ } }","attempt":2,"max":5,"backoff":3.0}
{"event":"iteration_complete","iteration":1,"new_types":12,"total_types":15,"total_fields":76}
```

This would let agents parse progress programmatically and detect hangs by
monitoring the time gap between events.
