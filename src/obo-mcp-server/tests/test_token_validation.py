import time
import unittest
from unittest.mock import Mock, patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import obo


class TokenValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.untrusted_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )

    def setUp(self):
        self.enterContext(
            patch.multiple(
                obo,
                AUTH_DISABLED=False,
                TENANT_ID="synthetic-tenant",
                OBO_API_CLIENT_ID="synthetic-server",
                _ISSUER_V2="https://issuer.example/v2.0",
                ALLOWED_CLIENT_APP_IDS=frozenset({"synthetic-client"}),
            )
        )
        jwks = Mock()
        jwks.get_signing_key_from_jwt.return_value.key = self.signing_key.public_key()
        self.enterContext(patch.object(obo, "_jwks_client", return_value=jwks))
        self.claims = {
            "aud": "api://synthetic-server",
            "iss": "https://issuer.example/v2.0",
            "tid": "synthetic-tenant",
            "azp": "synthetic-client",
            "ver": "2.0",
            "scp": "access_as_user",
            "exp": int(time.time()) + 300,
        }

    def token(self, claims, key=None):
        return jwt.encode(claims, key or self.signing_key, algorithm="RS256")

    def test_valid_signed_delegated_token(self):
        self.assertEqual(obo.validate_user_token(self.token(self.claims)), self.claims)

    def test_invalid_claims_are_rejected(self):
        invalid_claims = [
            {"aud": "different-api"},
            {"iss": "https://wrong-issuer.example/v2.0"},
            {"tid": "different-tenant"},
            {"azp": "different-client"},
            {"ver": "1.0"},
            {"scp": "unrelated_scope"},
            {"roles": ["application-role"]},
            {"exp": int(time.time()) - 60},
        ]
        for change in invalid_claims:
            with (
                self.subTest(change=change),
                self.assertRaises(obo.TokenValidationError),
            ):
                obo.validate_user_token(self.token(self.claims | change))

    def test_missing_required_claims_are_rejected(self):
        for name in ("exp", "aud", "iss"):
            claims = {key: value for key, value in self.claims.items() if key != name}
            with self.subTest(claim=name), self.assertRaises(obo.TokenValidationError):
                obo.validate_user_token(self.token(claims))

    def test_untrusted_signature_is_rejected(self):
        with self.assertRaises(obo.TokenValidationError):
            obo.validate_user_token(self.token(self.claims, self.untrusted_key))


if __name__ == "__main__":
    unittest.main()
