---
name: clairvoyance
description: Expert guidance for clairvoyance GraphQL schema reconstruction during penetration testing, including blind introspection, checkpoint-based resumable scans, wordlist selection, and result analysis
---

# Clairvoyance - GraphQL Schema Reconstruction Skill

## Overview
Clairvoyance is a Python security tool that reconstructs GraphQL API schemas when introspection is disabled. It sends crafted queries and parses error messages to discover fields, arguments, types, and their relationships. Useful during pentests when `__schema` introspection queries are blocked.

## Installation
```bash
# From PyPI
pip install clairvoyance

# From source
git clone https://github.com/nikitastupin/clairvoyance.git
cd clairvoyance
poetry install
```

## Core Concepts

### How It Works
GraphQL servers leak schema information through error messages:
- `"Cannot query field X on type Y"` -- X is invalid, but Y is the type name
- `"Did you mean X?"` -- X is a valid field (suggestion-based discovery)
- `"Field X of type Y must have a selection of subfields"` -- X exists, Y is its return type
- `"Field X argument Y of type Z is required"` -- argument Y exists with type Z

Clairvoyance exploits these error messages by sending wordlist-based queries in batches (default 64 per request), iterating until the full schema is mapped.

### The Blind Introspection Loop
1. Discover root typenames (Query, Mutation, Subscription) via `__typename`
2. Fuzz fields by sending wordlist items in batches ("buckets" of 64)
3. For each discovered field, probe its return type and arguments
4. Find types that have no fields yet, build a document path from root, and repeat

## Integration with gqlextractor

Clairvoyance works best as part of a pipeline with `gqlextractor`. Run gqlextractor first to extract GraphQL field names from JavaScript bundles, then use those as a targeted wordlist for clairvoyance.

### Shared Output Directory

Both tools should share the same root output directory per target. The combined directory structure:

```
<output-dir>/
  operations/          # gqlextractor: extracted .graphql operation files
  requests/            # gqlextractor: JSON request bodies for replay
  wordlist/
    unique-fields.txt  # gqlextractor: unique field names (USE AS CLAIRVOYANCE WORDLIST)
  field-paths/         # gqlextractor: field path analysis
  schema/
    schema.json        # clairvoyance: reconstructed schema (introspection format)
    checkpoint.json    # clairvoyance: checkpoint file for resumable scans
```

### Recommended Workflow

**Step 1 -- Run gqlextractor first** to extract field names from the target's JavaScript:
```bash
# Extract from JS files found via recon (waymore, xnlinkfinder, etc.)
gqlextractor --input-urls=js_urls.txt --output-directory=./target-graphql --output-mode=all --search-field=id

# Or from local JS bundles
gqlextractor --input-directory=./js-files --output-directory=./target-graphql --output-mode=operations --output-mode=fields
```

**Step 2 -- Run clairvoyance** using gqlextractor's field wordlist:
```bash
python -m clairvoyance \
  -w ./target-graphql/wordlist/unique-fields.txt \
  --checkpoint ./target-graphql/schema/checkpoint.json \
  -o ./target-graphql/schema/schema.json \
  https://target.com/graphql
```

**Step 3 -- Feed schema back into gqlextractor** for field path analysis:
```bash
gqlextractor --input-schema=./target-graphql/schema/schema.json \
  --search-field=password \
  --output-directory=./target-graphql
```

### Why gqlextractor Should Run First

- gqlextractor's `wordlist/unique-fields.txt` contains actual field names used by the target application, extracted from its own JavaScript bundles
- This targeted wordlist is far more effective than generic wordlists -- fewer requests, higher hit rate, less chance of rate limiting
- If gqlextractor hasn't been run yet, **suggest running it first** before clairvoyance
- If no JS sources are available, fall back to generic wordlists (built-in, SecLists)

### Detecting Existing gqlextractor Output

Before running clairvoyance, check if the output directory already has gqlextractor results:
```bash
# Check for existing gqlextractor wordlist
ls <output-dir>/wordlist/unique-fields.txt

# Check for existing operations
ls <output-dir>/operations/*.graphql
```

If `wordlist/unique-fields.txt` exists, always use it as the `-w` argument. You can combine it with the built-in wordlist for broader coverage:
```bash
cat <output-dir>/wordlist/unique-fields.txt <clairvoyance-install>/wordlist.txt | sort -u > /tmp/combined.txt
python -m clairvoyance -w /tmp/combined.txt ...
```

## Common Use Cases

### 1. Basic Schema Discovery
```bash
# Default wordlist, output to stdout
python -m clairvoyance https://target.com/graphql

# With custom wordlist and output file
python -m clairvoyance -w wordlist.txt -o schema.json https://target.com/graphql

# With authentication headers
python -m clairvoyance -H "Authorization: Bearer TOKEN" -o schema.json https://target.com/graphql

# Multiple headers
python -m clairvoyance -H "Authorization: Bearer TOKEN" -H "X-API-Key: KEY123" https://target.com/graphql
```

### 2. Full Pipeline with gqlextractor (Preferred)
```bash
OUTPUT=./target-graphql
mkdir -p "$OUTPUT/schema"

# Phase 1: Extract fields from JS
gqlextractor --input-urls=js_urls.txt --output-directory="$OUTPUT" --output-mode=fields --output-mode=operations

# Phase 2: Blind introspection with extracted wordlist
python -m clairvoyance \
  -w "$OUTPUT/wordlist/unique-fields.txt" \
  --checkpoint "$OUTPUT/schema/checkpoint.json" \
  -o "$OUTPUT/schema/schema.json" \
  -p slow \
  https://target.com/graphql

# Phase 3: Analyze discovered schema
gqlextractor --input-schema="$OUTPUT/schema/schema.json" \
  --search-field=email --output-directory="$OUTPUT"
```

### 3. Resumable Scans with Checkpoints
Large schemas can take a long time. Use `--checkpoint` to save progress and resume if interrupted:
```bash
# Start a scan with checkpointing (creates file if it doesn't exist)
python -m clairvoyance --checkpoint scan.checkpoint -w wordlist.txt -o schema.json https://target.com/graphql

# If interrupted (Ctrl+C, network failure, etc.), re-run the same command to resume
python -m clairvoyance --checkpoint scan.checkpoint -w wordlist.txt -o schema.json https://target.com/graphql
```

The checkpoint file saves:
- Full schema discovered so far
- Set of types already explored (the `ignored` set)
- Current iteration counter
- Target URL (warns if it differs on resume)

**Note:** `--checkpoint` is mutually exclusive with `-i/--input-schema`.

### 4. Speed Profiles
```bash
# Fast mode (default) - high concurrency, quick results
python -m clairvoyance https://target.com/graphql

# Slow mode - single worker, retries, backoff (for rate-limited targets)
python -m clairvoyance -p slow https://target.com/graphql

# Custom concurrency and retry settings
python -m clairvoyance -c 10 -m 20 -b 3 https://target.com/graphql
```

### 5. Through a Proxy
```bash
# Route through Burp Suite or Caido
python -m clairvoyance -x http://127.0.0.1:8080 https://target.com/graphql

# Disable SSL verification (common with proxy interception)
python -m clairvoyance -x http://127.0.0.1:8080 -k https://target.com/graphql
```

### 6. Building on Partial Schema
```bash
# Start from an existing partial schema (e.g., from a previous run or manual discovery)
python -m clairvoyance -i partial_schema.json -o full_schema.json https://target.com/graphql

# Start from a specific document (target a known type)
python -m clairvoyance -d "query { user { FUZZ } }" https://target.com/graphql
```

## CLI Reference

| Flag | Description |
|------|-------------|
| `-w <file>` | Custom wordlist (one word per line) |
| `-o <file>` | Output file for JSON schema |
| `-i <file>` | Input partial schema to supplement |
| `--checkpoint <file>` | Checkpoint file for resumable scans |
| `-d <string>` | Starting document (default: `query { FUZZ }`) |
| `-H <header>` | HTTP header (repeatable, format: `Key: Value`) |
| `-c <int>` | Number of concurrent requests |
| `-p slow\|fast` | Speed profile |
| `-x <url>` | Proxy URL |
| `-k` | Disable SSL verification |
| `-m <int>` | Max retries per request |
| `-b <int>` | Exponential backoff factor |
| `-wv` | Validate wordlist against GraphQL name regex |
| `--progress` | Show progress bar |
| `-v` | Verbose/debug logging |

## Output Format

Output is JSON matching the GraphQL introspection format:
```json
{
  "data": {
    "__schema": {
      "queryType": {"name": "Query"},
      "mutationType": {"name": "Mutation"},
      "subscriptionType": null,
      "directives": [],
      "types": [...]
    }
  }
}
```

Compatible with:
- **GraphQL Voyager** -- visual schema explorer
- **InQL** -- Burp Suite extension for GraphQL testing
- **graphql-path-enum** -- find paths to specific types
- **gqlextractor** -- feed schema back in for field path analysis

## Wordlist Strategy

### Priority Order for Wordlists
1. **gqlextractor output** (`<output-dir>/wordlist/unique-fields.txt`) -- best option, target-specific field names extracted from the application's own JavaScript
2. **Combined wordlist** -- merge gqlextractor fields with clairvoyance's built-in wordlist for broader coverage
3. **Built-in**: Clairvoyance ships with a default wordlist covering common GraphQL field names
4. **SecLists**: `SecLists/Discovery/Web-Content/graphql.txt`
5. **Manual extraction**: `grep -oP '[a-zA-Z_][a-zA-Z0-9_]+' app.js | sort -u`

### Wordlist Tips
- GraphQL field names follow the regex `[_A-Za-z][_0-9A-Za-z]*`
- Use `-wv` to auto-filter invalid names
- Larger wordlists increase scan time linearly (batched in groups of 64)
- De-duplication is automatic

## Analyzing Results

### Using the Schema
```bash
# Pretty-print the schema
cat schema.json | python -m json.tool

# Count discovered types
cat schema.json | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['data']['__schema']['types']), 'types found')"

# List all type names
cat schema.json | python -c "import json,sys; d=json.load(sys.stdin); [print(t['name']) for t in d['data']['__schema']['types']]"

# Find types with interesting names
cat schema.json | python -c "import json,sys; d=json.load(sys.stdin); [print(t['name']) for t in d['data']['__schema']['types'] if any(k in t['name'].lower() for k in ['admin','secret','internal','debug','token','auth','password'])]"
```

### Visualize with GraphQL Voyager
1. Go to https://graphql-kit.com/graphql-voyager/
2. Click "Change Schema" -> "Introspection" tab
3. Paste the contents of your schema.json
4. Explore the schema visually

### Feed into InQL (Burp Suite)
1. Open Burp Suite with InQL extension
2. Load the schema.json file
3. InQL generates queries for every discovered field and mutation

## Troubleshooting

### No Fields Discovered
- The server may not return suggestion-based errors (not all implementations do)
- Try a larger/different wordlist
- Check if the endpoint requires authentication (`-H "Authorization: ..."`)
- Use verbose mode (`-v`) to see raw error messages

### Scan Takes Too Long
- Use a smaller, targeted wordlist (gqlextractor's `unique-fields.txt` is ideal)
- Increase concurrency (`-c 20`)
- Use `--checkpoint` so you can resume if interrupted
- Check if the server is rate-limiting (switch to `-p slow`)

### Getting Rate Limited or Blocked
- Use slow profile: `-p slow`
- Reduce concurrency: `-c 1`
- Add backoff: `-b 3`
- Route through a proxy to monitor responses: `-x http://127.0.0.1:8080`

### Checkpoint Issues
- Checkpoint URL differs from current URL: this is a warning only, the scan continues
- Checkpoint already complete: all types were explored, nothing to resume
- Corrupted checkpoint: delete the file and restart

## Notes for Claude

When helping users with clairvoyance:

### gqlextractor Integration (IMPORTANT)
1. **Always check for existing gqlextractor output first.** Before running clairvoyance, look for `<output-dir>/wordlist/unique-fields.txt`. If it exists, use it as the `-w` argument.
2. **If gqlextractor hasn't been run yet, suggest running it first.** The user should extract fields from JS bundles before running clairvoyance blind introspection. Only skip this if there are no JS sources available for the target.
3. **Save clairvoyance output into the gqlextractor directory structure.** Schema goes in `<output-dir>/schema/schema.json`, checkpoint in `<output-dir>/schema/checkpoint.json`. Create the `schema/` subdirectory if needed.
4. **After clairvoyance completes, suggest feeding the schema back into gqlextractor** for field path analysis (`--input-schema`).

### General Guidance
5. Always suggest `--checkpoint` for large or long-running scans
6. Recommend `-o` pointing to the shared output directory's `schema/schema.json`
7. For authenticated targets, show the `-H` flag with proper header format
8. Suggest `-p slow` when users report errors or rate limiting
9. When analyzing discovered schemas, look for:
   - Mutations that modify sensitive data (admin operations, password resets)
   - Types with names suggesting internal/debug functionality
   - Fields that accept user IDs or tokens (IDOR candidates)
   - Input types that might be vulnerable to injection
10. Suggest GraphQL Voyager for visual exploration of discovered schemas
11. For pentesting reports, document: target URL, wordlist used, types/fields discovered, and any sensitive operations found
12. The `--checkpoint` flag is mutually exclusive with `-i/--input-schema`
13. The default wordlist is built into the package at `clairvoyance/wordlist.txt`
