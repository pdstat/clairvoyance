"""Broader unit test coverage for graphql.py."""

import json
import logging
import unittest

from clairvoyance.graphql import Field, InputValue, Schema, Type, TypeRef
from clairvoyance.entities.context import logger_ctx
from clairvoyance.entities.primitives import GraphQLKind

logger_ctx.set(logging.getLogger("clairvoyance.test"))


class TestSchemaAddType(unittest.TestCase):
    def test_add_type_idempotent(self) -> None:
        s = Schema(query_type="Query")
        s.add_type("Foo", "OBJECT")
        s.add_type("Foo", "OBJECT")
        self.assertEqual(s.types["Foo"].kind, "OBJECT")

    def test_add_type_does_not_overwrite_fields(self) -> None:
        s = Schema(query_type="Query")
        s.add_type("Foo", "OBJECT")
        tr = TypeRef("String", "SCALAR")
        s.types["Foo"].fields.append(Field("bar", tr))
        s.add_type("Foo", "OBJECT")
        self.assertEqual(len(s.types["Foo"].fields), 1)


class TestSchemaRepr(unittest.TestCase):
    def test_produces_valid_json(self) -> None:
        s = Schema(query_type="Query")
        output = repr(s)
        parsed = json.loads(output)
        self.assertIn("data", parsed)
        self.assertIn("__schema", parsed["data"])

    def test_roundtrip_parse(self) -> None:
        s = Schema(query_type="Query")
        tr = TypeRef("String", "SCALAR")
        s.types["Query"].fields.append(Field("hello", tr))
        output = repr(s)
        parsed = json.loads(output)
        s2 = Schema(schema=parsed)
        self.assertIn("Query", s2.types)
        self.assertEqual(len(s2.types["Query"].fields), 1)
        self.assertEqual(s2.types["Query"].fields[0].name, "hello")


class TestFieldCreation(unittest.TestCase):
    def test_requires_typeref(self) -> None:
        with self.assertRaises(ValueError):
            Field("test", None)

    def test_to_json_from_json_roundtrip(self) -> None:
        tr = TypeRef("Int", "SCALAR", non_null=True)
        f = Field("age", tr, args=[
            InputValue("min", TypeRef("Int", "SCALAR")),
        ])
        j = f.to_json()
        f2 = Field.from_json(j)
        self.assertEqual(f2.name, "age")
        self.assertEqual(f2.type.name, "Int")
        self.assertEqual(len(f2.args), 1)
        self.assertEqual(f2.args[0].name, "min")


class TestTypeRefValidation(unittest.TestCase):
    def test_non_null_item_requires_is_list(self) -> None:
        with self.assertRaises(ValueError):
            TypeRef("Foo", "OBJECT", is_list=False, non_null_item=True)

    def test_list_type_creation(self) -> None:
        tr = TypeRef("Foo", "OBJECT", is_list=True, non_null_item=True, non_null=True)
        self.assertTrue(tr.is_list)
        self.assertTrue(tr.non_null_item)
        self.assertTrue(tr.non_null)


class TestGetPathFromRoot(unittest.TestCase):
    def test_type_not_in_schema(self) -> None:
        s = Schema(query_type="Query")
        with self.assertRaises(ValueError):
            s.get_path_from_root("NonExistent")

    def test_disconnected_schema(self) -> None:
        s = Schema(query_type="Query")
        s.add_type("Orphan", "OBJECT")
        with self.assertRaises(ValueError):
            s.get_path_from_root("Orphan")

    def test_direct_child(self) -> None:
        s = Schema(query_type="Query")
        tr = TypeRef("User", "OBJECT")
        s.types["Query"].fields.append(Field("users", tr))
        s.add_type("User", "OBJECT")
        path = s.get_path_from_root("User")
        self.assertEqual(path, ["Query", "users"])

    def test_nested_path(self) -> None:
        s = Schema(query_type="Query")
        tr_user = TypeRef("User", "OBJECT")
        s.types["Query"].fields.append(Field("users", tr_user))
        s.add_type("User", "OBJECT")
        tr_addr = TypeRef("Address", "OBJECT")
        s.types["User"].fields.append(Field("address", tr_addr))
        s.add_type("Address", "OBJECT")
        path = s.get_path_from_root("Address")
        self.assertEqual(path, ["Query", "users", "address"])


class TestGetTypeWithoutFields(unittest.TestCase):
    def test_returns_empty_when_all_have_fields(self) -> None:
        s = Schema(query_type="Query")
        tr = TypeRef("String", "SCALAR")
        s.types["Query"].fields.append(Field("hello", tr))
        result = s.get_type_without_fields(ignored={"String", "ID"})
        self.assertEqual(result, "")

    def test_ignores_input_objects(self) -> None:
        s = Schema(query_type="Query")
        tr = TypeRef("String", "SCALAR")
        s.types["Query"].fields.append(Field("hello", tr))
        s.add_type("MyInput", "INPUT_OBJECT")
        result = s.get_type_without_fields(ignored={"String", "ID"})
        self.assertEqual(result, "")

    def test_finds_object_without_fields(self) -> None:
        s = Schema(query_type="Query")
        tr = TypeRef("User", "OBJECT")
        s.types["Query"].fields.append(Field("users", tr))
        s.add_type("User", "OBJECT")
        result = s.get_type_without_fields(ignored={"String", "ID"})
        self.assertEqual(result, "User")


class TestConvertPathToDocument(unittest.TestCase):
    def test_query_path(self) -> None:
        s = Schema(query_type="Query")
        path = ["Query", "users", "address"]
        got = s.convert_path_to_document(path)
        self.assertEqual(got, "query { users { address { FUZZ } } }")

    def test_mutation_path(self) -> None:
        s = Schema(mutation_type="Mutation")
        path = ["Mutation", "createUser"]
        got = s.convert_path_to_document(path)
        self.assertEqual(got, "mutation { createUser { FUZZ } }")


if __name__ == "__main__":
    unittest.main()
