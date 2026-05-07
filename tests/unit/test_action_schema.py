import unittest
from praxile.json_utils import parse_json_value, parse_json_object, parse_jsonc_object, RobustJSONError

class TestActionSchema(unittest.TestCase):
    def test_parse_json_object(self):
        result = parse_json_object('{"action": "test"}')
        self.assertEqual(result["action"], "test")

    def test_parse_json_value_array(self):
        result = parse_json_value('["a", "b"]')
        self.assertEqual(result[0], "a")

    def test_parse_json_with_fences(self):
        text = "```json\n{\"action\": \"fenced\"}\n```"
        result = parse_json_object(text)
        self.assertEqual(result["action"], "fenced")

    def test_parse_json_with_trailing_commas(self):
        text = '{"action": "trailing", "list": [1, 2, ], }'
        result = parse_json_object(text)
        self.assertEqual(result["action"], "trailing")
        self.assertEqual(result["list"][1], 2)

    def test_parse_jsonc_with_comments(self):
        text = """
        {
            // comment
            "action": "jsonc", /* inline */
        }
        """
        result = parse_jsonc_object(text)
        self.assertEqual(result["action"], "jsonc")

    def test_invalid_json(self):
        with self.assertRaises(RobustJSONError):
            parse_json_object('{"action": "incomplete"')

if __name__ == "__main__":
    unittest.main()
