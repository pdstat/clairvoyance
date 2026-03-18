# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Clairvoyance is a Python security tool that reconstructs GraphQL API schemas when introspection is disabled. It works by sending crafted queries and parsing error messages to discover fields, arguments, types, and their relationships through regex-based heuristics.

## Build & Development

**Package manager:** Poetry (not pip/uv)

```bash
poetry install                    # Install dependencies
poetry run pytest tests/          # Run all unit tests
poetry run pytest tests/oracle_test.py::TestGetValidFields::test_single_suggestion  # Single test
poetry run python -m unittest tests/system.py  # System tests (requires Apollo server)
```

**Linting (CI runs all of these):**
```bash
poetry run isort -m 9 --line-length 160 clairvoyance tests --gitignore --check-only
poetry run pylint clairvoyance tests
poetry run docformatter --wrap-summaries 160 --wrap-descriptions 160 -cr clairvoyance tests
poetry run black --check clairvoyance tests
poetry run mypy clairvoyance tests
```

**System tests** require a local Apollo Server at `http://localhost:4000`:
```bash
cd tests/apollo-server && npm ci && node src/index.js &
```

## Architecture

### Core Loop (`cli.py:blind_introspection`)

The tool iterates until the full schema is mapped:
1. Discover root typenames (query, mutation, subscription) via `__typename`
2. Fuzz fields by sending wordlist items in batches ("buckets" of 64)
3. For each discovered field, probe its return type and arguments
4. Find types that have no fields yet, build a document path from root to that type, and repeat

### Key Modules

- **`oracle.py`** — Core fuzzing logic. Contains regex patterns that parse GraphQL error messages to extract field names, argument names, and type references. The regexes handle multiple GraphQL server implementations (Apollo, graphql-js, etc.) with varying error message formats.
- **`graphql.py`** — Schema data model (`Schema`, `Type`, `Field`, `TypeRef`, `InputValue`). Handles JSON serialization matching the introspection format. `Schema.get_path_from_root()` does DFS to find a document path to unexplored types.
- **`client.py`** — Async HTTP client using aiohttp with semaphore-based concurrency control, retry logic, and optional exponential backoff.
- **`config.py`** — Holds `bucket_size` (number of fields per request, default 64).
- **`entities/context.py`** — Module-level `ContextVar` singletons for `Config`, `Client`, and `Logger`. Accessed via `config()`, `client()`, `log()` callables.

### How Field Discovery Works (`oracle.py`)

Error messages from GraphQL servers leak information:
- "Cannot query field X on type Y" → X is invalid, but Y is the type name
- "Did you mean X?" → X is a valid field (suggestion-based discovery)
- "Field X of type Y must have a selection of subfields" → X exists, Y is its type
- "Field X argument Y of type Z is required" → argument Y exists with type Z

The regex dictionaries (`_FIELD_REGEXES`, `_ARG_REGEXES`, `_TYPEREF_REGEXES`) categorize error messages into SKIP, VALID_FIELD, SINGLE/DOUBLE/MULTI_SUGGESTION patterns.

### Output Format

Schema output is JSON matching GraphQL introspection format (`{"data": {"__schema": {...}}}`), compatible with GraphQL Voyager, InQL, and graphql-path-enum.
