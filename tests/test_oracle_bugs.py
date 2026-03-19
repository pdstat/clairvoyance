"""Tests that expose and verify fixes for oracle.py bugs."""

import unittest

import aiounittest

from clairvoyance import oracle
from clairvoyance.entities.oracle import FuzzingContext
from tests.conftest import setup_test_context


class TestGeneralSkipComma(unittest.TestCase):
    """Bug 1: Missing comma caused Enum+Int pattern concatenation."""

    def test_general_skip_has_six_patterns(self) -> None:
        self.assertEqual(len(oracle.GENERAL_SKIP), 6)

    def test_int_error_skipped_independently(self) -> None:
        got = oracle.get_valid_fields(
            "Int cannot represent non-integer value: 7"
        )
        self.assertEqual(got, set())

    def test_enum_error_skipped_independently(self) -> None:
        got = oracle.get_valid_fields(
            'Enum "Status" cannot represent non-enum value: 7'
        )
        self.assertEqual(got, set())


class TestDictErrorMessage(aiounittest.AsyncTestCase):
    """Bug 2: dict error message caused UnboundLocalError."""

    async def test_dict_error_message_field_returns_none(self) -> None:
        """With the fix, dict messages are skipped (no UnboundLocalError).
        FIELD context returns None with a warning instead of raising."""
        setup_test_context(responses=[
            {"errors": [{"message": {"some": "dict"}}]},
        ])
        result = await oracle.probe_typeref(
            ["query { test }"],
            FuzzingContext.FIELD,
        )
        self.assertIsNone(result)

    async def test_dict_error_message_arg_returns_none(self) -> None:
        setup_test_context(responses=[
            {"errors": [{"message": {"some": "dict"}}]},
        ])
        result = await oracle.probe_typeref(
            ["query { test }"],
            FuzzingContext.ARGUMENT,
        )
        self.assertIsNone(result)


class TestEmptyResponse(aiounittest.AsyncTestCase):
    """Bug 3: empty response from post() caused KeyError on 'errors'."""

    async def test_empty_response_no_crash(self) -> None:
        setup_test_context(
            responses=[{"errors": []}],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(
            ["field1", "field2"],
            "query { FUZZ }",
        )
        self.assertEqual(result, set())


class TestAddTypePreservesKind(unittest.TestCase):
    """Bug 4: add_type hardcoded OBJECT, ignoring actual kind."""

    def test_scalar_kind_preserved(self) -> None:
        from clairvoyance.graphql import Schema

        schema = Schema(query_type="Query")
        schema.add_type("Status", "ENUM")
        self.assertEqual(schema.types["Status"].kind, "ENUM")

    def test_add_type_does_not_overwrite(self) -> None:
        from clairvoyance.graphql import Schema

        schema = Schema(query_type="Query")
        schema.add_type("MyType", "ENUM")
        schema.add_type("MyType", "OBJECT")
        self.assertEqual(schema.types["MyType"].kind, "ENUM")


class TestFetchRootTypenames(aiounittest.AsyncTestCase):
    """Bug 6: data without __typename caused KeyError."""

    async def test_data_without_typename_no_crash(self) -> None:
        setup_test_context(responses=[
            {"data": {"something": "else"}},
            {"data": {"__typename": "Mutation"}},
            {"errors": [{"message": "not allowed"}]},
        ])
        result = await oracle.fetch_root_typenames()
        self.assertIsNone(result["queryType"])
        self.assertEqual(result["mutationType"], "Mutation")
        self.assertIsNone(result["subscriptionType"])

    async def test_data_with_null_response(self) -> None:
        setup_test_context(responses=[
            {"data": None},
            {"data": None},
            {"data": None},
        ])
        result = await oracle.fetch_root_typenames()
        self.assertIsNone(result["queryType"])
        self.assertIsNone(result["mutationType"])
        self.assertIsNone(result["subscriptionType"])


class TestMalformedErrors(aiounittest.AsyncTestCase):
    """Oracle functions must not crash on string errors or dict messages."""

    async def test_probe_valid_fields_string_error(self) -> None:
        setup_test_context(
            responses=[{"errors": ["some string error"]}],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(
            ["field1"],
            "query { FUZZ }",
        )
        # String error is skipped; field1 stays in the set
        self.assertEqual(result, {"field1"})

    async def test_probe_valid_fields_dict_message(self) -> None:
        setup_test_context(
            responses=[{"errors": [{"message": {"nested": "dict"}}]}],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(
            ["field1"],
            "query { FUZZ }",
        )
        self.assertEqual(result, {"field1"})

    async def test_probe_valid_fields_mixed_errors(self) -> None:
        setup_test_context(
            responses=[{"errors": [
                "a bare string",
                {"message": {"nested": "dict"}},
                {"message": 'Cannot query field "field1" on type "Query". Did you mean "users"?'},
            ]}],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(
            ["field1"],
            "query { FUZZ }",
        )
        self.assertIn("users", result)

    async def test_probe_valid_args_string_error(self) -> None:
        setup_test_context(responses=[
            {"errors": ["some string error"]},
        ])
        result = await oracle.probe_valid_args(
            "users",
            ["arg1"],
            "query { FUZZ }",
        )
        self.assertEqual(result, {"arg1"})

    async def test_probe_valid_args_dict_message(self) -> None:
        setup_test_context(responses=[
            {"errors": [{"message": {"nested": "dict"}}]},
        ])
        result = await oracle.probe_valid_args(
            "users",
            ["arg1"],
            "query { FUZZ }",
        )
        self.assertEqual(result, {"arg1"})

    async def test_probe_typename_string_error(self) -> None:
        setup_test_context(responses=[
            {"errors": ["some string error"]},
        ])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "Query")

    async def test_probe_typename_dict_message(self) -> None:
        setup_test_context(responses=[
            {"errors": [{"message": {"nested": "dict"}}]},
        ])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "Query")

    async def test_probe_typename_mixed_with_valid(self) -> None:
        setup_test_context(responses=[
            {"errors": [
                "bare string",
                {"message": {"nested": "dict"}},
                {"message": 'Cannot query field "IAmWrongField" on type "Mutation".'},
            ]},
        ])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "Mutation")

    async def test_probe_typeref_string_error(self) -> None:
        setup_test_context(responses=[
            {"errors": ["some string error"]},
        ])
        result = await oracle.probe_typeref(
            ["query { test }"],
            FuzzingContext.ARGUMENT,
        )
        self.assertIsNone(result)

    async def test_probe_valid_fields_error_missing_message_key(self) -> None:
        setup_test_context(
            responses=[{"errors": [{"extensions": {"code": "INTERNAL"}}]}],
            bucket_size=64,
        )
        result = await oracle.probe_valid_fields(
            ["field1"],
            "query { FUZZ }",
        )
        self.assertEqual(result, {"field1"})

    async def test_probe_valid_args_error_missing_message_key(self) -> None:
        setup_test_context(responses=[
            {"errors": [{"extensions": {"code": "INTERNAL"}}]},
        ])
        result = await oracle.probe_valid_args(
            "users",
            ["arg1"],
            "query { FUZZ }",
        )
        self.assertEqual(result, {"arg1"})

    async def test_probe_typename_error_missing_message_key(self) -> None:
        setup_test_context(responses=[
            {"errors": [{"extensions": {"code": "INTERNAL"}}]},
        ])
        result = await oracle.probe_typename("query { FUZZ }")
        self.assertEqual(result, "Query")


if __name__ == "__main__":
    unittest.main()
