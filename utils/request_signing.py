"""Shared H5 signing, with an explicitly experimental HMAC-SHA1 alternative.

The HMAC mapping below is a local experiment, not a verified seller-client
protocol. Changing a digest alone does not establish server compatibility.
"""
import hashlib
import hmac
import os
import warnings


def generate_sign(t: str, token: str, data: str) -> str:
    """Sign the exact UTF-8 payload; never reserialize ``data``.

    MD5 preserves the existing H5 contract. Experimental HMAC-SHA1 uses the
    token as key and the same token&t&appKey&data string as message, with a
    lowercase hexadecimal result. This mapping is NOT verified with Xianyu.
    """
    algorithm = os.environ.get('XIANYU_SIGN_ALGORITHM', 'md5').strip().lower()
    if algorithm not in ('md5', 'hmac-sha1'):
        raise ValueError('XIANYU_SIGN_ALGORITHM must be md5 or hmac-sha1')
    message = f'{token}&{t}&34839810&{data}'.encode('utf-8')
    if algorithm == 'md5':
        return hashlib.md5(message).hexdigest()
    if not token:
        raise ValueError('HMAC-SHA1 experimental mode requires a nonempty token')
    warnings.warn(
        'HMAC-SHA1 experimental signing is enabled; Xianyu compatibility is '
        'unverified and requests may fail authentication. Set '
        'XIANYU_SIGN_ALGORITHM=md5 to restore existing H5 signing.',
        RuntimeWarning,
    )
    return hmac.new(token.encode('utf-8'), message, hashlib.sha1).hexdigest()
