import hashlib
import os
import unittest
import warnings
from unittest.mock import patch

from utils.request_signing import generate_sign


class RequestSigningTests(unittest.TestCase):
    def test_default_retains_existing_h5_signature(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(generate_sign('123', 'token', '{}'),
                             hashlib.md5(b'token&123&34839810&{}').hexdigest())

    def test_hmac_matches_independent_openssl_vector(self):
        # openssl dgst -sha1 -hmac token, with the UTF-8 message and no newline.
        with patch.dict(os.environ, {'XIANYU_SIGN_ALGORITHM': 'hmac-sha1'}):
            with self.assertWarnsRegex(RuntimeWarning, 'unverified'):
                result = generate_sign('1700000000000', 'token', '{"标题":"商品"}')
            self.assertEqual(result, '6f24e9585ecbf01296aa9af4677ec1541785370a')

    def test_hmac_preserves_json_bytes_and_uses_current_token(self):
        with patch.dict(os.environ, {'XIANYU_SIGN_ALGORITHM': 'hmac-sha1'}):
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', RuntimeWarning)
                original = generate_sign('123', 'first', '{"a":1}')
                self.assertNotEqual(original, generate_sign('123', 'first', '{"a": 1}'))
                self.assertNotEqual(original, generate_sign('123', 'renewed', '{"a":1}'))

    def test_invalid_configuration_does_not_fallback_or_leak_value(self):
        with patch.dict(os.environ, {'XIANYU_SIGN_ALGORITHM': 'accidental-secret'}):
            with self.assertRaises(ValueError) as caught:
                generate_sign('123', 'private-token', '{}')
            self.assertNotIn('accidental-secret', str(caught.exception))
            self.assertNotIn('private-token', str(caught.exception))
        with patch.dict(os.environ, {'XIANYU_SIGN_ALGORITHM': 'hmac-sha1'}):
            with self.assertRaisesRegex(ValueError, 'nonempty token'):
                generate_sign('123', '', '{}')
