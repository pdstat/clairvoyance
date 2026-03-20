# Clairvoyance

Obtain GraphQL API schema even if the introspection is disabled.

[![PyPI](https://img.shields.io/pypi/v/clairvoyance)](https://pypi.org/project/clairvoyance/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/clairvoyance)](https://pypi.org/project/clairvoyance/)
[![PyPI - Downloads](https://img.shields.io/pypi/dm/clairvoyance)](https://pypi.org/project/clairvoyance/)
[![GitHub](https://img.shields.io/github/license/nikitastupin/clairvoyance)](https://github.com/nikitastupin/clairvoyance/blob/main/LICENSE)

## Introduction

Some GraphQL APIs have disabled introspection. For example, [Apollo Server disables introspection automatically if the `NODE_ENV` environment variable is set to `production`](https://www.apollographql.com/docs/tutorial/schema/#explore-your-schema).

Clairvoyance helps to obtain GraphQL API schema even if the introspection is disabled. It produces schema in JSON format suitable for other tools like [GraphQL Voyager](https://github.com/APIs-guru/graphql-voyager), [InQL](https://github.com/doyensec/inql) or [graphql-path-enum](https://gitlab.com/dee-see/graphql-path-enum).

> **Note:** This is a fork of [nikitastupin/clairvoyance](https://github.com/nikitastupin/clairvoyance) with additional features including resumable checkpoint scans, rate limiting, proxy support, batch argument probing, auth/server error detection, progress tracking, and JSON logging.

## Getting Started

### pip

```bash
pip install clairvoyance
clairvoyance https://rickandmortyapi.com/graphql -o schema.json
# should take about 2 minutes
```

### docker

```bash
docker run --rm nikitastupin/clairvoyance --help
```

## Advanced Usage

### Speed profiles

Use `-p` to select a speed profile:

```bash
# Fast mode (default) - high concurrency for quick results
clairvoyance https://example.com/graphql

# Slow mode - single worker, retries, backoff (for rate-limited targets)
clairvoyance -p slow https://example.com/graphql
```

Slow mode sets concurrency to 1, max retries to 50, and backoff factor to 2.

### Rate limiting

Pace requests to avoid WAF or rate-limit blocks:

```bash
clairvoyance --rate-limit 5 https://example.com/graphql  # 5 requests/second
```

### Proxy and SSL

Route traffic through a proxy (e.g. Burp Suite or Caido) and optionally disable SSL verification:

```bash
clairvoyance -x http://127.0.0.1:8080 -k https://example.com/graphql
```

### Resumable scans with checkpoints

Large schemas can take a long time to reconstruct. Use `--checkpoint` to save progress after each iteration and resume if interrupted:

```bash
clairvoyance https://example.com/graphql -w wordlist.txt --checkpoint scan.checkpoint -o schema.json
```

If the scan is interrupted (Ctrl+C, network failure, auth expiry, etc.), re-run the same command to resume from where it left off. The checkpoint file stores the discovered schema, the set of already-explored types, and the current iteration counter.

`--checkpoint` is mutually exclusive with `-i/--input-schema`.

### Progress and logging

```bash
# Rich progress bar
clairvoyance --progress https://example.com/graphql

# JSON log output (one JSON object per line, for agent/machine consumption)
clairvoyance --json-log https://example.com/graphql

# Verbose/debug logging
clairvoyance -v https://example.com/graphql
```

### Cookie control

Cookies are persisted across requests by default. Disable this if needed:

```bash
clairvoyance --no-cookies https://example.com/graphql
```

### Wordlist validation

Filter wordlist entries against the GraphQL name regex (`[_A-Za-z][_0-9A-Za-z]*`):

```bash
clairvoyance -w wordlist.txt -wv https://example.com/graphql
```

### Which wordlist should I use?

There are at least three approaches:

- Use one of the [wordlists](https://github.com/Escape-Technologies/graphql-wordlist) collected by Escape Technologies
- Use general English words (e.g. [google-10000-english](https://github.com/first20hours/google-10000-english)).
- Create target specific wordlist by extracting all valid GraphQL names from application HTTP traffic, from mobile application static files, etc. Regex for GraphQL name is [`[_A-Za-z][_0-9A-Za-z]*`](http://spec.graphql.org/June2018/#sec-Names).

### Environment variables

```bash
LOG_FMT=`%(asctime)s \t%(levelname)s\t| %(message)s` # A string format for logging.
LOG_DATEFMT=`%Y-%m-%d %H:%M:%S` # A string format for logging date.
LOG_LEVEL=`INFO` # A string level for logging.
```

## CLI Reference

| Flag | Description |
|------|-------------|
| `-w <file>` | Custom wordlist (one word per line) |
| `-wv` | Validate wordlist against GraphQL name regex |
| `-o <file>` | Output file for JSON schema |
| `-i <file>` | Input partial schema to supplement |
| `-d <string>` | Starting document (default: `query { FUZZ }`) |
| `-H <header>` | HTTP header (repeatable, format: `Key: Value`) |
| `-c <int>` | Number of concurrent requests |
| `-p slow\|fast` | Speed profile (default: `fast`) |
| `-x <url>` | Proxy URL |
| `-k` | Disable SSL verification |
| `-m <int>` | Max retries per request |
| `-b <int>` | Exponential backoff factor (`0.5 * backoff**retries` seconds) |
| `--checkpoint <file>` | Checkpoint file for resumable scans |
| `--progress` | Show rich progress bar |
| `--rate-limit <float>` | Max requests per second |
| `--json-log` | Emit one JSON object per log line |
| `--no-cookies` | Disable cookie jar |
| `-v` | Verbose/debug logging |

## Support

In case of questions or issues with Clairvoyance please refer to [wiki](https://github.com/nikitastupin/clairvoyance/wiki) or [issues](https://github.com/nikitastupin/clairvoyance/issues). If this doesn't solve your problem feel free to open a [new issue](https://github.com/nikitastupin/clairvoyance/issues/new).

## Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change. For more information about tests, internal project structure and so on refer to our [contributing guide](.github/CONTRIBUTING.md).

## Documentation

You may find more details on how the tool works in the second half of the [GraphQL APIs from bug hunter's perspective by Nikita Stupin](https://youtu.be/nPB8o0cSnvM) talk.

## Contributors

Thanks to the contributors for their work.

- [nikitastupin](https://github.com/nikitastupin)
- [Escape](https://escape.tech) team
  - [iCarossio](https://github.com/iCarossio)
  - [Swan](https://github.com/c3b5aw)
  - [QuentinN42](https://github.com/QuentinN42)
  - [Nohehf](https://github.com/Nohehf)
- [i-tsaturov](https://github.com/i-tsaturov)
- [EONRaider](https://github.com/EONRaider)
- [noraj](https://github.com/noraj)
- [belane](https://github.com/belane)
