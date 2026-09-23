import unittest
from unittest.mock import patch

from starlette.testclient import TestClient

import server


class MCPTransportTests(unittest.TestCase):
    def setUp(self):
        app = server.mcp.streamable_http_app(
            host="0.0.0.0", json_response=True, stateless_http=True
        )
        self.client = self.enterContext(TestClient(app))
        self.client.headers.update(
            {
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            }
        )

    def request(self, method, params, headers=None):
        response = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertNotIn("error", body)
        return body["result"]

    def test_initialize_preserves_server_identity(self):
        result = self.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "synthetic-test-client", "version": "1"},
            },
        )
        self.assertEqual(result["serverInfo"]["name"], "graph-obo")

    def test_tool_schema_never_exposes_token_or_context(self):
        result = self.request("tools/list", {})
        tool = next(
            tool for tool in result["tools"] if tool["name"] == "get_my_profile"
        )
        self.assertEqual(tool["inputSchema"].get("properties", {}), {})

    def test_missing_authorization_never_calls_graph(self):
        with patch.object(server, "fetch_my_profile") as fetch:
            result = self.request(
                "tools/call", {"name": "get_my_profile", "arguments": {}}
            )
        self.assertEqual(result["structuredContent"]["error"], "unauthorized")
        fetch.assert_not_called()

    def test_header_token_is_validated_and_photo_is_not_returned(self):
        profile = {"displayName": "Synthetic User", "photoDataUri": "synthetic-photo"}
        with (
            patch.object(server, "validate_user_token") as validate,
            patch.object(server, "fetch_my_profile", return_value=profile) as fetch,
        ):
            result = self.request(
                "tools/call",
                {"name": "get_my_profile", "arguments": {}},
                headers={"Authorization": "Bearer synthetic-test-token"},
            )
        validate.assert_called_once_with("synthetic-test-token")
        fetch.assert_called_once_with("synthetic-test-token")
        self.assertEqual(
            result["structuredContent"],
            {"displayName": "Synthetic User", "hasProfilePhoto": True},
        )

    def test_rejected_token_never_calls_graph(self):
        with (
            patch.object(
                server,
                "validate_user_token",
                side_effect=server.TokenValidationError("invalid"),
            ),
            patch.object(server, "fetch_my_profile") as fetch,
        ):
            result = self.request(
                "tools/call",
                {"name": "get_my_profile", "arguments": {}},
                headers={"Authorization": "Bearer synthetic-test-token"},
            )
        self.assertEqual(result["structuredContent"]["error"], "unauthorized")
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
