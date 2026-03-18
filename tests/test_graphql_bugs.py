"""Tests that expose and verify fix for graphql.py bugs."""

import unittest

from clairvoyance.graphql import Type
from clairvoyance.entities.primitives import GraphQLKind


class TestToJsonDoesNotMutate(unittest.TestCase):
    """Bug 5: Type.to_json() permanently appended dummy to self.fields."""

    def test_to_json_does_not_mutate_fields(self) -> None:
        t = Type(name="EmptyType", kind=GraphQLKind.OBJECT)
        self.assertEqual(len(t.fields), 0)

        t.to_json()
        self.assertEqual(len(t.fields), 0)

        t.to_json()
        self.assertEqual(len(t.fields), 0)

    def test_to_json_still_outputs_dummy(self) -> None:
        t = Type(name="EmptyType", kind=GraphQLKind.OBJECT)
        output = t.to_json()
        self.assertEqual(len(output["fields"]), 1)
        self.assertEqual(output["fields"][0]["name"], "dummy")

    def test_to_json_with_real_fields_no_dummy(self) -> None:
        from clairvoyance.graphql import Field, TypeRef

        tr = TypeRef(name="String", kind="SCALAR")
        f = Field("realField", tr)
        t = Type(name="HasFields", kind=GraphQLKind.OBJECT, fields=[f])
        output = t.to_json()
        self.assertEqual(len(output["fields"]), 1)
        self.assertEqual(output["fields"][0]["name"], "realField")


if __name__ == "__main__":
    unittest.main()
