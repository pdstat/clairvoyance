"""Broader unit test coverage for oracle.py functions."""

import logging
import unittest

import aiounittest

from clairvoyance import graphql, oracle
from clairvoyance.entities.oracle import FuzzingContext
from tests.conftest import setup_test_context


class TestGetValidFieldsEdgeCases(unittest.TestCase):
    def test_skip_cannot_query_field(self) -> None:
        got = oracle.get_valid_fields(
            'Cannot query field "badField" on type "Query".'
        )
        self.assertEqual(got, set())

    def test_valid_field_must_have_sub_selection(self) -> None:
        got = oracle.get_valid_fields(
            'Field "users" of type "User" must have a sub selection.'
        )
        self.assertEqual(got, {"users"})

    def test_unknown_message_returns_empty(self) -> None:
        setup_test_context()
        got = oracle.get_valid_fields("This is totally unexpected gibberish.")
        self.assertEqual(got, set())

    def test_required_arg_error_skipped(self) -> None:
        got = oracle.get_valid_fields(
            'Field "node" argument "id" of type "ID!" is required, but it was not provided.'
        )
        self.assertEqual(got, set())

    def test_skip_inline_fragment_suggestion(self) -> None:
        got = oracle.get_valid_fields(
            'Cannot query field "x" on type "Union". Did you mean to use an inline fragment on "TypeA"?'
        )
        self.assertEqual(got, set())

    def test_skip_double_inline_fragment(self) -> None:
        got = oracle.get_valid_fields(
            'Cannot query field "x" on type "Union". Did you mean to use an inline fragment on "TypeA" or "TypeB"?'
        )
        self.assertEqual(got, set())

    def test_string_cannot_represent(self) -> None:
        got = oracle.get_valid_fields(
            "String cannot represent a non string value: 7"
        )
        self.assertEqual(got, set())

    def test_float_cannot_represent(self) -> None:
        got = oracle.get_valid_fields(
            'Float cannot represent a non numeric value: "hello"'
        )
        self.assertEqual(got, set())

    def test_id_cannot_represent(self) -> None:
        got = oracle.get_valid_fields(
            "ID cannot represent a non-string and non-integer value: true"
        )
        self.assertEqual(got, set())

    def test_not_authorized(self) -> None:
        got = oracle.get_valid_fields("Not authorized")
        self.assertEqual(got, set())

    def test_no_subfields_error(self) -> None:
        got = oracle.get_valid_fields(
            'Field "name" must not have a selection since type "String" has no subfields.'
        )
        self.assertEqual(got, set())

    def test_must_not_have_sub_selection(self) -> None:
        got = oracle.get_valid_fields(
            'Field "name" of type "String" must not have a sub selection.'
        )
        self.assertEqual(got, set())


class TestGetValidArgsEdgeCases(unittest.TestCase):
    def test_skip_unknown_argument(self) -> None:
        got = oracle.get_valid_args(
            'Unknown argument "badArg" on field "someField".'
        )
        self.assertEqual(got, set())

    def test_skip_required_arg(self) -> None:
        got = oracle.get_valid_args(
            'Field "node" argument "id" of type "ID!" is required, but it was not provided.'
        )
        self.assertEqual(got, set())

    def test_skip_unknown_arg_with_type(self) -> None:
        got = oracle.get_valid_args(
            'Unknown argument "badArg" on field "someField" of type "Query".'
        )
        self.assertEqual(got, set())

    def test_unknown_message_returns_empty(self) -> None:
        setup_test_context()
        got = oracle.get_valid_args("Totally unexpected message here.")
        self.assertEqual(got, set())


class TestGetTypeRefEdgeCases(unittest.TestCase):
    def test_list_type_with_brackets(self) -> None:
        got = oracle.get_typeref(
            'Field "items" of type "[Item!]!" must have a selection of subfields. Did you mean "items { ... }"?',
            FuzzingContext.FIELD,
        )
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "Item")
        self.assertTrue(got.is_list)
        self.assertTrue(got.non_null_item)
        self.assertTrue(got.non_null)

    def test_scalar_field(self) -> None:
        got = oracle.get_typeref(
            'Field "age" must not have a selection since type "Int" has no subfields.',
            FuzzingContext.FIELD,
        )
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "Int")
        self.assertEqual(got.kind, "SCALAR")

    def test_general_skip_for_field(self) -> None:
        got = oracle.get_typeref(
            "Int cannot represent non-integer value: 7",
            FuzzingContext.FIELD,
        )
        self.assertIsNone(got)

    def test_general_skip_for_arg(self) -> None:
        got = oracle.get_typeref(
            "String cannot represent a non string value: 7",
            FuzzingContext.ARGUMENT,
        )
        self.assertIsNone(got)

    def test_expected_type_for_argument(self) -> None:
        got = oracle.get_typeref(
            "Expected type Int!, found 7.",
            FuzzingContext.ARGUMENT,
        )
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "Int")
        self.assertEqual(got.kind, "SCALAR")
        self.assertTrue(got.non_null)


class TestProbeValidFields(aiounittest.AsyncTestCase):
    async def test_field_discovery(self) -> None:
        setup_test_context(
            responses=[
                {"errors": [
                    {"message": 'Cannot query field "badField" on type "Query". Did you mean "users"?'},
                    {"message": 'Cannot query field "otherBad" on type "Query".'},
                ]},
            ],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(
            ["badField", "otherBad"],
            "query { FUZZ }",
        )
        self.assertIn("users", result)

    async def test_empty_wordlist(self) -> None:
        setup_test_context(responses=[])
        result = await oracle.probe_valid_fields([], "query { FUZZ }")
        self.assertEqual(result, set())

    async def test_no_subfields_returns_empty(self) -> None:
        setup_test_context(
            responses=[
                {"errors": [
                    {"message": 'Field "x" must not have a selection since type "String" has no subfields.'},
                ]},
            ],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(
            ["x"],
            "query { FUZZ }",
        )
        self.assertEqual(result, set())


class TestProbeValidArgs(aiounittest.AsyncTestCase):
    async def test_arg_discovery(self) -> None:
        setup_test_context(responses=[
            {"errors": [
                {"message": 'Unknown argument "badArg" on field "users" of type "Query". Did you mean "limit"?'},
            ]},
        ])
        result = await oracle.probe_valid_args(
            "users",
            ["badArg"],
            "query { FUZZ }",
        )
        self.assertIn("limit", result)

    async def test_no_error_response(self) -> None:
        setup_test_context(responses=[
            {"data": {"users": []}},
        ])
        result = await oracle.probe_valid_args(
            "users",
            ["arg1"],
            "query { FUZZ }",
        )
        self.assertEqual(result, {"arg1"})


class TestFetchRootTypenamesNormal(aiounittest.AsyncTestCase):
    async def test_normal_operation(self) -> None:
        setup_test_context(responses=[
            {"data": {"__typename": "Query"}},
            {"data": {"__typename": "Mutation"}},
            {"errors": [{"message": "subscription not supported"}]},
        ])
        result = await oracle.fetch_root_typenames()
        self.assertEqual(result["queryType"], "Query")
        self.assertEqual(result["mutationType"], "Mutation")
        self.assertIsNone(result["subscriptionType"])


class TestProbeTypename(aiounittest.AsyncTestCase):
    async def test_normal_typename(self) -> None:
        setup_test_context(responses=[
            {"errors": [
                {"message": 'Cannot query field "IAmWrongField" on type "Query".'},
            ]},
        ])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "Query")

    async def test_no_errors_returns_default(self) -> None:
        setup_test_context(responses=[
            {"data": {"something": True}},
        ])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "Query")

    async def test_unknown_errors_returns_default(self) -> None:
        setup_test_context(responses=[
            {"errors": [{"message": "something unexpected"}]},
        ])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "Query")


class TestNormalizeErrorMessage(unittest.TestCase):
    def test_strips_redacted_suffix(self) -> None:
        raw = 'Cannot query field "x" on type "Query". Did you mean "y"? <[REDACTED]>'
        got = oracle.normalize_error_message(raw)
        self.assertEqual(got, 'Cannot query field "x" on type "Query". Did you mean "y"?')

    def test_strips_filtered_suffix(self) -> None:
        raw = 'Field "x" of type "Y" must have a selection of subfields. [FILTERED]'
        got = oracle.normalize_error_message(raw)
        self.assertEqual(got, 'Field "x" of type "Y" must have a selection of subfields.')

    def test_strips_removed_suffix(self) -> None:
        raw = 'Cannot query field "x" on type "Query". [REMOVED]'
        got = oracle.normalize_error_message(raw)
        self.assertEqual(got, 'Cannot query field "x" on type "Query".')

    def test_preserves_clean_message(self) -> None:
        raw = 'Cannot query field "x" on type "Query". Did you mean "y"?'
        got = oracle.normalize_error_message(raw)
        self.assertEqual(got, raw)

    def test_strips_with_extra_whitespace(self) -> None:
        raw = 'Field "x" of type "Y" must have a selection of subfields.   <[REDACTED]>'
        got = oracle.normalize_error_message(raw)
        self.assertEqual(got, 'Field "x" of type "Y" must have a selection of subfields.')

    def test_empty_string(self) -> None:
        self.assertEqual(oracle.normalize_error_message(""), "")

    def test_only_suffix(self) -> None:
        self.assertEqual(oracle.normalize_error_message("<[REDACTED]>"), "")


class TestRedactedSuffixIntegration(unittest.TestCase):
    """Verify that sanitized error messages still produce correct results."""

    def test_get_valid_fields_with_redacted_suffix_unnormalized(self) -> None:
        """Without normalization, fullmatch fails on redacted suffix."""
        setup_test_context()
        got = oracle.get_valid_fields(
            'Cannot query field "x" on type "Query". Did you mean "users"? <[REDACTED]>'
        )
        self.assertEqual(got, set())

    def test_get_valid_fields_with_redacted_suffix_normalized(self) -> None:
        """With normalization applied, the suggestion is extracted."""
        msg = oracle.normalize_error_message(
            'Cannot query field "x" on type "Query". Did you mean "users"? <[REDACTED]>'
        )
        got = oracle.get_valid_fields(msg)
        self.assertEqual(got, {"users"})

    def test_get_valid_fields_subfields_without_suggestion(self) -> None:
        got = oracle.get_valid_fields(
            'Field "items" of type "Item" must have a selection of subfields.'
        )
        self.assertEqual(got, {"items"})

    def test_get_typeref_subfields_without_suggestion(self) -> None:
        got = oracle.get_typeref(
            'Field "items" of type "Item" must have a selection of subfields.',
            FuzzingContext.FIELD,
        )
        self.assertIsNotNone(got)
        self.assertEqual(got.name, "Item")
        self.assertEqual(got.kind, "OBJECT")


class TestPerFieldProgressLogging(aiounittest.AsyncTestCase):
    """Verify per-field INFO logging and phase announcements."""

    async def test_clairvoyance_logs_phase_and_progress(self) -> None:
        subfield_msg = 'Field "users" of type "User" must have a selection of subfields. Did you mean "users { ... }"?'
        setup_test_context(
            responses=[
                # fetch_root_typenames: 3 calls
                {"data": {"__typename": "Query"}},
                {"errors": [{"message": "no mutation"}]},
                {"errors": [{"message": "no subscription"}]},
                # probe_typename
                {"errors": [{"message": 'Cannot query field "IAmWrongField" on type "Query".'}]},
                # probe_valid_fields (1 bucket with 1 word)
                {"errors": [{"message": subfield_msg}]},
                # probe_field_type sends 2 documents concurrently
                {"errors": [{"message": subfield_msg}]},
                {"errors": [{"message": subfield_msg}]},
                # probe_args sends 1 bucket (wordlist reused)
                {"errors": [{"message": 'Unknown argument "users" on field "users" of type "Query".'}]},
            ],
            bucket_size=64,
        )
        logger = logging.getLogger("clairvoyance.test")
        with self.assertLogs(logger, level="INFO") as cm:
            await oracle.clairvoyance(
                wordlist=["users"],
                input_document="query { FUZZ }",
            )

        log_text = "\n".join(cm.output)
        self.assertIn("Probing 1 fields on Query", log_text)
        self.assertIn("[1/1] Query.users: type=User (OBJECT), args=0", log_text)


class TestRedactedSuffixAsync(aiounittest.AsyncTestCase):
    """Verify normalization works in async probe functions."""

    async def test_probe_valid_fields_redacted(self) -> None:
        setup_test_context(
            responses=[{"errors": [
                {"message": 'Cannot query field "x" on type "Query". Did you mean "users"? <[REDACTED]>'},
            ]}],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(["x"], "query { FUZZ }")
        self.assertIn("users", result)

    async def test_probe_valid_fields_filtered(self) -> None:
        setup_test_context(
            responses=[{"errors": [
                {"message": 'Cannot query field "x" on type "Query". Did you mean "users"? [FILTERED]'},
            ]}],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(["x"], "query { FUZZ }")
        self.assertIn("users", result)

    async def test_probe_valid_args_redacted(self) -> None:
        setup_test_context(responses=[{"errors": [
            {"message": 'Unknown argument "x" on field "users" of type "Query". Did you mean "limit"? <[REDACTED]>'},
        ]}])
        result = await oracle.probe_valid_args("users", ["x"], "query { FUZZ }")
        self.assertIn("limit", result)

    async def test_probe_typename_redacted(self) -> None:
        setup_test_context(responses=[{"errors": [
            {"message": 'Cannot query field "IAmWrongField" on type "CustomQuery". <[REDACTED]>'},
        ]}])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "CustomQuery")

    async def test_probe_typename_filtered(self) -> None:
        setup_test_context(responses=[{"errors": [
            {"message": 'Cannot query field "IAmWrongField" on type "RootQuery". [FILTERED]'},
        ]}])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "RootQuery")

    async def test_probe_typeref_redacted(self) -> None:
        setup_test_context(responses=[{"errors": [
            {"message": 'Field "items" of type "Item" must have a selection of subfields. <[REDACTED]>'},
        ]}])
        result = await oracle.probe_typeref(
            ["query { items }"],
            FuzzingContext.FIELD,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "Item")
        self.assertEqual(result.kind, "OBJECT")


if __name__ == "__main__":
    unittest.main()
