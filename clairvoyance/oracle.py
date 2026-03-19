# pylint: disable=anomalous-backslash-in-string, line-too-long

import asyncio
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from clairvoyance import graphql
from clairvoyance.entities import GraphQLPrimitive
from clairvoyance.entities.context import client, config, log
from clairvoyance.entities.errors import EndpointError
from clairvoyance.entities.oracle import FuzzingContext
from clairvoyance.utils import ProgressTracker, _format_duration, track

_SANITIZATION_SUFFIXES = re.compile(
    r"\s*(?:<\[REDACTED\]>|\[FILTERED\]|\[REMOVED\])$"
)

# Batch arg type probing regexes — extract type AND sentinel value
_EXPECTED_TYPE_RE = re.compile(
    r"""Expected type (?P<typeref>[_0-9A-Za-z.\[\]!]+), """
    r"""found (?P<found>.+)\."""
)
_CANNOT_REPRESENT_RE = re.compile(
    r"""(?P<scalar>String|Int|Float|ID|Boolean) cannot """
    r"""represent[^:]*: (?P<found>.+)"""
)


def normalize_error_message(raw: str) -> str:
    """Strip known server-side sanitization suffixes from error messages."""
    return _SANITIZATION_SUFFIXES.sub("", raw)

# yapf: disable

MAIN_REGEX = r"""[_0-9A-Za-z\.\[\]!]+"""
REQUIRED_BUT_NOT_PROVIDED = r"""required(, but it was not provided| but not provided)?\."""

_FIELD_REGEXES = {
    'SKIP': [
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] must not have a selection since type ['"]""" + MAIN_REGEX + r"""['"] has no subfields\.""",
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] of type ['"]""" + MAIN_REGEX + r"""['"] must not have a sub selection\.""",
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] argument ['"]""" + MAIN_REGEX + r"""['"] of type ['"]""" + MAIN_REGEX + r"""['"] is """ + REQUIRED_BUT_NOT_PROVIDED,
        r"""Cannot query field ['"]""" + MAIN_REGEX + r"""['"] on type ['"]""" + MAIN_REGEX + r"""['"]\.""",
        r"""Cannot query field ['"]""" + MAIN_REGEX + r"""['"] on type ['"](""" + MAIN_REGEX + r""")['"]\. Did you mean to use an inline fragment on ['"]""" + MAIN_REGEX + r"""['"]\?""",
        r"""Cannot query field ['"]""" + MAIN_REGEX + r"""['"] on type ['"](""" + MAIN_REGEX + r""")['"]\. Did you mean to use an inline fragment on ['"]""" + MAIN_REGEX + r"""['"] or ['"]""" + MAIN_REGEX + r"""['"]\?""",
        r"""Cannot query field ['"]""" + MAIN_REGEX + r"""['"] on type ['"](""" + MAIN_REGEX + r""")['"]\. Did you mean to use an inline fragment on (['"]""" + MAIN_REGEX + r"""['"],? )+(or ['"]""" + MAIN_REGEX + r"""['"])?\?"""
    ],
    'VALID_FIELD': [
        r"""Field ['"](?P<field>""" + MAIN_REGEX + r""")['"] of type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] must have a selection of subfields\. Did you mean ['"]""" + MAIN_REGEX + r"""( \{ \.\.\. \})?['"]\?""",
        r"""Field ['"](?P<field>""" + MAIN_REGEX + r""")['"] of type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] must have a sub selection\.""",
        r"""Field ['"](?P<field>""" + MAIN_REGEX + r""")['"] of type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] must have a selection of subfields\.""",
    ],
    'SINGLE_SUGGESTION': [
        r"""Cannot query field ['"](""" + MAIN_REGEX + r""")['"] on type ['"]""" + MAIN_REGEX + r"""['"]\. Did you mean ['"](?P<field>""" + MAIN_REGEX + r""")['"]\?"""
    ],
    'DOUBLE_SUGGESTION': [
        r"""Cannot query field ['"]""" + MAIN_REGEX + r"""['"] on type ['"]""" + MAIN_REGEX + r"""['"]\. Did you mean ['"](?P<one>""" + MAIN_REGEX + r""")['"] or ['"](?P<two>""" + MAIN_REGEX + r""")['"]\?"""
    ],
    'MULTI_SUGGESTION': [
        r"""Cannot query field ['"](""" + MAIN_REGEX + r""")['"] on type ['"]""" + MAIN_REGEX + r"""['"]\. Did you mean (?P<multi>(['"]""" + MAIN_REGEX + r"""['"],? )+)(or ['"](?P<last>""" + MAIN_REGEX + r""")['"])?\?"""
    ],
}

_ARG_REGEXES = {
    'SKIP': [
        r"""Unknown argument ['"]""" + MAIN_REGEX + r"""['"] on field ['"]""" + MAIN_REGEX + r"""['"]\.""",
        r"""Unknown argument ['"]""" + MAIN_REGEX + r"""['"] on field ['"]""" + MAIN_REGEX + r"""['"] of type ['"]""" + MAIN_REGEX + r"""['"]\.""",
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] of type ['"]""" + MAIN_REGEX + r"""['"] must have a selection of subfields\. Did you mean ['"]""" + MAIN_REGEX + r"""( \{ \.\.\. \})?['"]\?""",
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] argument ['"]""" + MAIN_REGEX + r"""['"] of type ['"]""" + MAIN_REGEX + r"""['"] is """ + REQUIRED_BUT_NOT_PROVIDED,
    ],
    'SINGLE_SUGGESTION': [
        r"""Unknown argument ['"]""" + MAIN_REGEX + r"""['"] on field ['"]""" + MAIN_REGEX + r"""['"] of type ['"]""" + MAIN_REGEX + r"""['"]\. Did you mean ['"](?P<arg>""" + MAIN_REGEX + r""")['"]\?""",
        r"""Unknown argument ['"]""" + MAIN_REGEX + r"""['"] on field ['"]""" + MAIN_REGEX + r"""['"]\. Did you mean ['"](?P<arg>""" + MAIN_REGEX + r""")['"]\?"""
    ],
    'DOUBLE_SUGGESTION': [
        r"""Unknown argument ['"]""" + MAIN_REGEX + r"""['"] on field ['"]""" + MAIN_REGEX + r"""['"]( of type ['"]""" + MAIN_REGEX + r"""['"])?\. Did you mean ['"](?P<first>""" + MAIN_REGEX + r""")['"] or ['"](?P<second>""" + MAIN_REGEX + r""")['"]\?"""
    ],
    'MULTI_SUGGESTION': [
        r"""Unknown argument ['"]""" + MAIN_REGEX + r"""['"] on field ['"]""" + MAIN_REGEX + r"""['"]\. Did you mean (?P<multi>(['"]""" + MAIN_REGEX + r"""['"],? )+)(or ['"](?P<last>""" + MAIN_REGEX + r""")['"])?\?""",
        r"""Unknown argument ['"]""" + MAIN_REGEX + r"""['"] on field ['"]""" + MAIN_REGEX + r"""['"] of type ['"]""" + MAIN_REGEX + r"""['"]\. Did you mean (?P<multi>(['"]""" + MAIN_REGEX + r"""['"],? )+)(or ['"](?P<last>""" + MAIN_REGEX + r""")['"])?\?"""
    ],
}

_TYPEREF_REGEXES = {
    'FIELD': [
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] of type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] must have a selection of subfields\. Did you mean ['"]""" + MAIN_REGEX + r"""( \{ \.\.\. \})?['"]\?""",
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] of type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] must have a selection of subfields\.""",
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] must not have a selection since type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] has no subfields\.""",
        # NOTE: "Cannot query field X on type Y" patterns were removed here.
        # That error means field X is INVALID on type Y — it does NOT reveal
        # the field's return type. Matching it produced false positives where
        # invalid fields were added with type=ParentType (e.g. type=Query).
        # probe_typename uses its own _WRONG_TYPENAME regexes for this pattern.
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] of type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] must not have a sub selection\.""",
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] of type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] must have a sub selection\.""",
    ],
    'ARG': [
        r"""Field ['"]""" + MAIN_REGEX + r"""['"] argument ['"]""" + MAIN_REGEX + r"""['"] of type ['"](?P<typeref>""" + MAIN_REGEX + r""")['"] is """ + REQUIRED_BUT_NOT_PROVIDED,
        r"""Expected type (?P<typeref>""" + MAIN_REGEX + r"""), found .+\.""",
    ],
}

WRONG_FIELD_EXAMPLE = 'IAmWrongField'

_WRONG_TYPENAME = [
    r"""Cannot query field ['"]""" + WRONG_FIELD_EXAMPLE + r"""['"] on type ['"](?P<typename>""" + MAIN_REGEX + r""")['"].""",
    r"""Field ['"]""" + MAIN_REGEX + r"""['"] must not have a selection since type ['"](?P<typename>""" + MAIN_REGEX + r""")['"] has no subfields.""",
    r"""Field ['"]""" + MAIN_REGEX + r"""['"] of type ['"](?P<typename>""" + MAIN_REGEX + r""")['"] must not have a sub selection.""",
]

_GENERAL_SKIP = [
    r"""String cannot represent a non string value: .+""",
    r"""Float cannot represent a non numeric value: .+""",
    r"""ID cannot represent a non-string and non-integer value: .+""",
    r"""Enum ['"]""" + MAIN_REGEX + r"""['"] cannot represent non-enum value: .+""",
    r"""Int cannot represent non-integer value: .+""",
    r"""Not authorized""",
]

# yapf: enable

# Compiling all regexes for performance
FIELD_REGEXES = {k: [re.compile(r) for r in v] for k, v in _FIELD_REGEXES.items()}
ARG_REGEXES = {k: [re.compile(r) for r in v] for k, v in _ARG_REGEXES.items()}
TYPEREF_REGEXES = {k: [re.compile(r) for r in v] for k, v in _TYPEREF_REGEXES.items()}
WRONG_TYPENAME = [re.compile(r) for r in _WRONG_TYPENAME]
GENERAL_SKIP = [re.compile(r) for r in _GENERAL_SKIP]


# pylint: disable=too-many-branches
def get_valid_fields(error_message: str) -> Set[str]:
    """Fetching valid fields using regex heuristics."""

    valid_fields: Set[str] = set()

    for regex in FIELD_REGEXES["SKIP"] + GENERAL_SKIP:
        if regex.fullmatch(error_message):
            return valid_fields

    for regex in FIELD_REGEXES["VALID_FIELD"]:
        match = regex.fullmatch(error_message)
        if match:
            valid_fields.add(match.group("field"))
            return valid_fields

    for regex in FIELD_REGEXES["SINGLE_SUGGESTION"]:
        match = regex.fullmatch(error_message)
        if match:
            valid_fields.add(match.group("field"))
            return valid_fields

    for regex in FIELD_REGEXES["DOUBLE_SUGGESTION"]:
        match = regex.fullmatch(error_message)
        if match:
            valid_fields.add(match.group("one"))
            valid_fields.add(match.group("two"))
            return valid_fields

    for regex in FIELD_REGEXES["MULTI_SUGGESTION"]:
        match = regex.fullmatch(error_message)
        if match:

            for m in match.group("multi").split(", "):
                if m:
                    valid_fields.add(m.strip("'\" "))
            if match.group("last"):
                valid_fields.add(match.group("last"))

            return valid_fields

    log().debug(f"Unknown error message for `valid_field`: '{error_message}'")

    return valid_fields


async def probe_valid_fields(
    wordlist: List[str],
    input_document: str,
) -> Set[str]:
    """Sending a wordlist to check for valid fields.

    Args:
        wordlist: The words that would leads to discovery.
        config: The config for the graphql client.
        input_document: The base document.

    Returns:
        A set of discovered valid fields.
    """

    async def __probation(i: int) -> Set[str]:
        bucket = wordlist[i : i + config().bucket_size]
        valid_fields = set(bucket)
        document = input_document.replace("FUZZ", " ".join(bucket))

        start_time = time.time()
        response = await client().post(document)
        total_time = time.time() - start_time

        errors = response.get("errors", [])
        if not errors:
            return set()

        log().debug(
            f"Sent {len(bucket)} fields, received {len(errors)} errors in {round(total_time, 2)} seconds"
        )

        for error in errors:
            if isinstance(error, str) or not isinstance(error.get("message"), str):
                continue

            error_message = normalize_error_message(error["message"])

            if (
                "must not have a selection since type" in error_message
                and "has no subfields" in error_message
            ) or "must not have a sub selection" in error_message:
                return set()

            # ! LEGACY CODE please keep
            # First remove field if it produced an 'Cannot query field' error
            match = re.search(
                r"""Cannot query field [\'"](?P<invalid_field>[_A-Za-z][_0-9A-Za-z]*)[\'"]""",
                error_message,
            )
            if match:
                valid_fields.discard(match.group("invalid_field"))

            # Second obtain field suggestions from error message
            valid_fields |= get_valid_fields(error_message)

        return valid_fields

    # Create task list
    tasks: List[asyncio.Task] = []
    for i in range(0, len(wordlist), config().bucket_size):
        tasks.append(asyncio.create_task(__probation(i)))

    # Process results
    valid_fields = set()
    progress = ProgressTracker(
        total=len(tasks),
        phase="Field discovery",
        logger=log(),
    )
    for task in track(
        asyncio.as_completed(tasks),
        description=f"Sending {len(tasks)} fields",
        total=len(tasks),
    ):
        result = await task
        valid_fields.update(result)
        progress.advance()

    progress.finish()
    return valid_fields


async def probe_valid_args(
    field: str,
    wordlist: List[str],
    input_document: str,
) -> Set[str]:
    """Sends the wordlist as arguments and deduces its type from the error msgs received."""

    valid_args = set(wordlist)

    document = input_document.replace(
        "FUZZ", f'{field}({", ".join([w + ": 7" for w in wordlist])})'
    )

    response = await client().post(document=document)

    if "errors" not in response:
        return valid_args

    errors = response["errors"]
    for error in errors:
        if isinstance(error, str) or not isinstance(error.get("message"), str):
            continue

        error_message = normalize_error_message(error["message"])

        if (
            "must not have a selection since type" in error_message
            and "has no subfields" in error_message
        ) or "must not have a sub selection" in error_message:
            return set()

        # First remove arg if it produced an 'Unknown argument' error
        match = re.search(
            r"""Unknown argument ['"](?P<invalid_arg>[_A-Za-z][_0-9A-Za-z]*)['"] on field ['"][_A-Za-z][_0-9A-Za-z\.]*['"]""",
            error_message,
        )
        if match:
            valid_args.discard(match.group("invalid_arg"))

        duplicate_arg_regex = r"""There can be only one argument named ["'](?P<arg>[_0-9a-zA-Z\.\[\]!]*)["']\.?"""
        if re.fullmatch(duplicate_arg_regex, error_message):
            match = re.fullmatch(duplicate_arg_regex, error_message)
            valid_args.discard(match.group("arg"))  # type: ignore
            continue

        # Second obtain args suggestions from error message
        valid_args |= get_valid_args(error_message)

    return valid_args


async def probe_args(
    field: str,
    wordlist: List[str],
    input_document: str,
    typename: str = "",
) -> Set[str]:
    """Wrapper function for deducing the arg types."""

    tasks: List[asyncio.Task] = []
    for i in range(0, len(wordlist), config().bucket_size):
        bucket = wordlist[i : i + config().bucket_size]
        tasks.append(
            asyncio.create_task(probe_valid_args(field, bucket, input_document))
        )

    num_buckets = len(tasks)
    prefix = f"{typename}.{field}" if typename else field
    log().info(f"  Probing args of {prefix} ({num_buckets} buckets)...")

    valid_args: Set[str] = set()

    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        result = await coro
        valid_args |= result
        if i == num_buckets or (num_buckets > 4 and i % max(1, num_buckets // 4) == 0):
            log().info(f"  Arg discovery for {prefix}: {i}/{num_buckets} buckets")

    return valid_args


def get_valid_args(error_message: str) -> Set[str]:
    """Get the type of an arg using regex."""

    valid_args = set()

    for regex in ARG_REGEXES["SKIP"] + GENERAL_SKIP:
        if re.fullmatch(regex, error_message):
            return set()

    for regex in ARG_REGEXES["SINGLE_SUGGESTION"]:
        match = re.fullmatch(regex, error_message)
        if match:
            valid_args.add(match.group("arg"))

    for regex in ARG_REGEXES["DOUBLE_SUGGESTION"]:
        match = re.fullmatch(regex, error_message)
        if match:
            valid_args.add(match.group("first"))
            valid_args.add(match.group("second"))

    for regex in ARG_REGEXES["MULTI_SUGGESTION"]:
        match = re.fullmatch(regex, error_message)
        if match:
            for m in match.group("multi").split(", "):
                if m:
                    valid_args.add(m.strip("'\" "))

            if match.group("last"):
                valid_args.add(match.group("last"))

    if not valid_args:
        log().debug(f"Unknown error message for `valid_args`: '{error_message}'")

    return valid_args


def get_typeref(
    error_message: str,
    context: FuzzingContext,
) -> Optional[graphql.TypeRef]:
    """Using predefined regex deduce the type of a field."""

    def __extract_matching_fields(
        error_message: str,
        context: FuzzingContext,
    ) -> Optional[re.Match]:

        if context == FuzzingContext.FIELD:
            # in the case of a field
            for regex in TYPEREF_REGEXES["ARG"] + GENERAL_SKIP:
                if re.fullmatch(regex, error_message):
                    return None

            for regex in TYPEREF_REGEXES["FIELD"]:
                match = re.fullmatch(regex, error_message)
                if match:
                    return match

        elif context == FuzzingContext.ARGUMENT:
            # in the case of an argument
            # we drop the following messages
            for regex in TYPEREF_REGEXES["FIELD"] + GENERAL_SKIP:
                if re.fullmatch(regex, error_message):
                    return None
            # if not dropped, we try to extract the type
            for regex in TYPEREF_REGEXES["ARG"]:
                match = re.fullmatch(regex, error_message)
                if match:
                    return match

        log().debug(
            f"Unknown error message for `typeref` with context `{context.value}`: '{error_message}'"
        )
        return None

    match = __extract_matching_fields(error_message, context)

    if match:
        tk = match.group("typeref")

        name = tk.replace("!", "").replace("[", "").replace("]", "")
        kind = ""
        if name in GraphQLPrimitive:
            kind = "SCALAR"
        elif context == FuzzingContext.FIELD:
            kind = "OBJECT"
        elif context == FuzzingContext.ARGUMENT:
            kind = "INPUT_OBJECT"
            name = (
                name.removesuffix("Input") + "Input"
            )  # Make sure `Input` is always once at the end
        else:
            log().debug(f"Unknown kind for `typeref`: '{error_message}'")
            return None

        is_list = bool("[" in tk and "]" in tk)
        non_null_item = bool(is_list and "!]" in tk)
        non_null = tk.endswith("!")

        return graphql.TypeRef(
            name=name,
            kind=kind,
            is_list=is_list,
            non_null_item=non_null_item,
            non_null=non_null,
        )

    return None


async def probe_typeref(
    documents: List[str],
    context: FuzzingContext,
) -> Optional[graphql.TypeRef]:
    """Sending a document to attain errors in order to deduce the type of fields."""

    async def __probation(document: str) -> Optional[graphql.TypeRef]:
        """Send a document to attempt discovering a typeref."""

        response = await client().post(document)
        for error in response.get("errors", []):
            if isinstance(error, str) or not isinstance(error.get("message"), str):
                continue

            msg = normalize_error_message(error["message"])
            typeref = get_typeref(
                msg,
                context,
            )
            log().debug(f'get_typeref("{msg}", "{context}") -> {typeref}')
            if typeref:
                return typeref

        return None

    tasks: List[asyncio.Task] = []
    for document in documents:
        tasks.append(asyncio.create_task(__probation(document)))

    typeref: Optional[graphql.TypeRef] = None
    results = await asyncio.gather(*tasks)
    for result in results:
        if result:
            typeref = result

    if not typeref and context != FuzzingContext.ARGUMENT:
        log().warning(
            f"Could not determine type for {documents[0]}. "
            f"Skipping field (unknown type)."
        )

    return typeref


async def probe_field_type(
    field: str,
    input_document: str,
) -> Optional[graphql.TypeRef]:
    """Wrapper function for sending the queries to deduce the field type."""

    documents = [
        input_document.replace("FUZZ", f"{field}"),
        input_document.replace("FUZZ", f"{field} {{ lol }}"),
    ]

    return await probe_typeref(documents, FuzzingContext.FIELD)


async def probe_arg_typeref(
    field: str,
    arg: str,
    input_document: str,
) -> Optional[graphql.TypeRef]:
    """Wrapper function to deduce the type of an arg."""

    documents = [
        input_document.replace("FUZZ", f"{field}({arg}: 42)"),
        input_document.replace("FUZZ", f"{field}({arg}: {{}})"),
        input_document.replace("FUZZ", f"{field}({arg[:-1]}: 42)"),
        input_document.replace("FUZZ", f'{field}({arg}: "42")'),
        input_document.replace("FUZZ", f"{field}({arg}: false)"),
    ]

    return await probe_typeref(documents, FuzzingContext.ARGUMENT)


async def probe_arg_typerefs_batch(
    field: str,
    arg_names: List[str],
    input_document: str,
) -> Dict[str, Optional[graphql.TypeRef]]:
    """Batch-probe arg types using unique sentinel values.

    Sends 1-2 requests instead of N*5, mapping error messages back
    to specific args via unique integer/string sentinels in the values.
    Falls back to individual probing for unresolved args.
    """
    results: Dict[str, Optional[graphql.TypeRef]] = {}
    unresolved = list(arg_names)

    # Batch 1: Int sentinels (7001, 7002, ...)
    if unresolved:
        results, unresolved = await _batch_probe_args(
            field, unresolved, input_document,
            value_fn=lambda i: str(7001 + i),
            sentinel_fn=lambda i: str(7001 + i),
        )

    # Batch 2: String sentinels for remaining args
    if unresolved:
        batch2, unresolved = await _batch_probe_args(
            field, unresolved, input_document,
            value_fn=lambda i: f'"t{7001 + i}"',
            sentinel_fn=lambda i: f'"t{7001 + i}"',
        )
        results.update(batch2)

    # Fallback: individual probing for still-unresolved args
    for arg in unresolved:
        typeref = await probe_arg_typeref(field, arg, input_document)
        results[arg] = typeref

    return results


async def _batch_probe_args(
    field: str,
    arg_names: List[str],
    input_document: str,
    value_fn: Any,
    sentinel_fn: Any,
) -> Tuple[Dict[str, Optional[graphql.TypeRef]], List[str]]:
    """Send one batch request and extract arg types from errors."""
    sentinel_to_arg: Dict[str, str] = {}
    arg_parts = []
    for i, arg in enumerate(arg_names):
        sentinel = sentinel_fn(i)
        sentinel_to_arg[sentinel] = arg
        arg_parts.append(f"{arg}: {value_fn(i)}")

    doc = input_document.replace(
        "FUZZ", f'{field}({", ".join(arg_parts)})'
    )
    response = await client().post(doc)

    resolved: Dict[str, Optional[graphql.TypeRef]] = {}
    for error in response.get("errors", []):
        if isinstance(error, str):
            continue
        if not isinstance(error.get("message"), str):
            continue

        msg = normalize_error_message(error["message"])

        # "Expected type X, found <sentinel>."
        match = _EXPECTED_TYPE_RE.fullmatch(msg)
        if match:
            found = match.group("found")
            arg = sentinel_to_arg.get(found)
            if arg and arg not in resolved:
                typeref = get_typeref(msg, FuzzingContext.ARGUMENT)
                if typeref:
                    resolved[arg] = typeref
            continue

        # "String cannot represent a non string value: <sentinel>"
        match = _CANNOT_REPRESENT_RE.fullmatch(msg)
        if match:
            scalar = match.group("scalar")
            found = match.group("found")
            arg = sentinel_to_arg.get(found)
            if arg and arg not in resolved:
                resolved[arg] = graphql.TypeRef(
                    name=scalar, kind="SCALAR",
                )
            continue

        # "Field X argument Y of type Z is required..."
        typeref = get_typeref(msg, FuzzingContext.ARGUMENT)
        if typeref:
            # Extract arg name from "required but not provided"
            req_match = re.search(
                r"""argument ['"](?P<arg>[_A-Za-z][_0-9A-Za-z]*)['"]""",
                msg,
            )
            if req_match:
                arg = req_match.group("arg")
                if arg in sentinel_to_arg.values() and arg not in resolved:
                    resolved[arg] = typeref

    unresolved = [a for a in arg_names if a not in resolved]
    return resolved, unresolved


async def probe_typename(input_document: str) -> str:

    document = input_document.replace("FUZZ", WRONG_FIELD_EXAMPLE)

    response = await client().post(document=document)
    if "errors" not in response:
        log().warning(
            f"""Unable to get typename from {document}.
                      Field Suggestion might not be enabled on this endpoint. Using default "Query"""
        )
        return "Query"

    errors = response["errors"]

    match = None
    for regex in WRONG_TYPENAME:
        for error in errors:
            if isinstance(error, str) or not isinstance(error.get("message"), str):
                continue
            match = re.fullmatch(regex, normalize_error_message(error["message"]))
            if match:
                break
        if match:
            break

    if not match:
        log().debug(
            f"""Unkwon error in `probe_typename`: "{errors}" does not match any known regexes.
                    Field Suggestion might not be enabled on this endpoint. Using default "Query"""
        )
        return "Query"

    return match.group("typename").replace("[", "").replace("]", "").replace("!", "")


async def fetch_root_typenames() -> Dict[str, Optional[str]]:
    documents: Dict[str, str] = {
        "queryType": "query { __typename }",
        "mutationType": "mutation { __typename }",
        "subscriptionType": "subscription { __typename }",
    }
    typenames: Dict[str, Optional[str]] = {
        "queryType": None,
        "mutationType": None,
        "subscriptionType": None,
    }

    for name, document in track(
        documents.items(), description="Fetching root typenames"
    ):
        response = await client().post(document=document)

        data = response.get("data") or {}
        if "__typename" in data:
            typenames[name] = data["__typename"]

    log().debug(f"Root typenames are: {typenames}")
    return typenames


async def clairvoyance(
    wordlist: List[str],
    input_document: str,
    input_schema: Optional[Dict[str, Any]] = None,
    on_field_complete: Optional[Any] = None,
) -> str:
    """Run one iteration of schema discovery.

    Args:
        on_field_complete: Optional callback(schema_json: str) called
            after each field is fully explored, for incremental checkpoints.
    """

    log().debug(f"input_document = {input_document}")

    if not input_schema:
        root_typenames = await fetch_root_typenames()
        schema = graphql.Schema(
            query_type=root_typenames["queryType"],
            mutation_type=root_typenames["mutationType"],
            subscription_type=root_typenames["subscriptionType"],
        )
    else:
        schema = graphql.Schema(schema=input_schema)

    typename = await probe_typename(input_document)
    log().debug(f"__typename = {typename}")
    schema.add_type(typename, "OBJECT")

    # Ensure the root type reference is set so checkpoints preserve it.
    # fetch_root_typenames may have failed (e.g. server requires auth for
    # __typename), but probe_typename discovered the name via error messages.
    if input_document.lstrip().startswith("query"):
        if not schema._schema["queryType"]:
            schema._schema["queryType"] = {"name": typename}
    elif input_document.lstrip().startswith("mutation"):
        if not schema._schema["mutationType"]:
            schema._schema["mutationType"] = {"name": typename}
    elif input_document.lstrip().startswith("subscription"):
        if not schema._schema["subscriptionType"]:
            schema._schema["subscriptionType"] = {"name": typename}

    valid_fields = await probe_valid_fields(
        wordlist,
        input_document,
    )

    existing_fields = set()
    if typename in schema.types:
        existing_fields = {f.name for f in schema.types[typename].fields}
    new_fields = valid_fields - existing_fields
    if existing_fields & valid_fields:
        skipped = existing_fields & valid_fields
        log().info(
            f"Skipping {len(skipped)} already-explored fields on "
            f"{typename}: {sorted(skipped)}"
        )

    log().info(f"Probing {len(new_fields)} fields on {typename}...")
    log().debug(f"{typename}.fields = {new_fields}")

    # Phase 1: Probe all field types (fast — 2 requests per field).
    # Save each to schema immediately so checkpoints capture them.
    type_tasks: Dict[str, asyncio.Task] = {}
    for field_name in new_fields:
        type_tasks[field_name] = asyncio.create_task(
            probe_field_type(field_name, input_document)
        )

    new_field_objects: List[graphql.Field] = []
    type_progress = ProgressTracker(
        total=len(type_tasks),
        phase=f"Type probing {typename}",
        logger=log(),
    )
    for coro in track(
        asyncio.as_completed(list(type_tasks.values())),
        description=f"Probing {len(type_tasks)} field types",
        total=len(type_tasks),
    ):
        typeref = await coro
        # Find which field this result belongs to
        done_name = ""
        for name, task in type_tasks.items():
            if task.done() and name not in existing_fields:
                try:
                    if task.result() is typeref:
                        done_name = name
                        break
                except Exception:
                    pass
        if not done_name:
            continue

        existing_fields.add(done_name)
        type_progress.advance()

        if typeref is None:
            log().info(
                f"  {typename}.{done_name}: type=unknown (skipped)"
            )
            continue

        field = graphql.Field(done_name, typeref)
        new_field_objects.append(field)
        schema.types[typename].fields.append(field)
        schema.add_type(field.type.name, field.type.kind)

        log().info(
            f"  {typename}.{done_name}: type={typeref.name} ({typeref.kind})"
        )

        if on_field_complete:
            on_field_complete(repr(schema))

    type_progress.finish()

    # Phase 2: Probe args for non-scalar fields (slow — many requests).
    non_scalar = [
        f for f in new_field_objects
        if f.type.name not in GraphQLPrimitive
    ]
    if non_scalar:
        log().info(
            f"Probing args for {len(non_scalar)} non-scalar fields "
            f"on {typename}..."
        )
    arg_progress = ProgressTracker(
        total=len(non_scalar),
        phase=f"Arg probing {typename}",
        logger=log(),
    )
    for field in non_scalar:
        arg_names = await probe_args(
            field.name, wordlist, input_document, typename=typename,
        )
        arg_list = sorted(arg_names)
        log().info(
            f"  {typename}.{field.name}: found {len(arg_names)} args "
            f"{arg_list}"
        )

        if arg_names:
            log().info(
                f"  Batch-probing {len(arg_list)} arg types for "
                f"{typename}.{field.name}..."
            )
            type_map = await probe_arg_typerefs_batch(
                field.name, arg_list, input_document,
            )
            for arg_name in arg_list:
                arg_typeref = type_map.get(arg_name)
                if not arg_typeref:
                    log().info(
                        f"    {arg_name} -> unknown (skipped)"
                    )
                    continue
                log().info(
                    f"    {arg_name} -> {arg_typeref.name} "
                    f"({arg_typeref.kind})"
                )
                arg = graphql.InputValue(arg_name, arg_typeref)
                field.args.append(arg)
                schema.add_type(arg.type.name, "INPUT_OBJECT")

        arg_progress.advance()
        eta_str = (
            _format_duration(arg_progress.eta)
            if arg_progress.completed > 1
            else "calculating"
        )
        log().info(
            f"[{arg_progress.completed}/{len(non_scalar)}] "
            f"{typename}.{field.name}: args={len(field.args)} "
            f"[~{eta_str} total remaining]"
        )

        if on_field_complete:
            on_field_complete(repr(schema))

    if non_scalar:
        arg_progress.finish()

    return repr(schema)
