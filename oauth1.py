"""Minimal OAuth 1.0a (HMAC-SHA1) signing for the X API, standard library only."""
import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlsplit, parse_qsl


def _enc(s):
    return quote(str(s), safe="~-._")


def auth_header(method, url, consumer_key, consumer_secret, token="", token_secret="", extra_oauth=None, params=None):
    """Return the Authorization header value. `params` = query/form params that are part of the signature
    (JSON bodies are NOT signed)."""
    oauth = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
    }
    if token:
        oauth["oauth_token"] = token
    oauth.update(extra_oauth or {})
    parts = urlsplit(url)
    base_url = f"{parts.scheme}://{parts.netloc}{parts.path}"
    all_params = list(oauth.items()) + list(parse_qsl(parts.query)) + list((params or {}).items())
    norm = "&".join(f"{k}={v}" for k, v in sorted((_enc(k), _enc(v)) for k, v in all_params))
    base = "&".join([method.upper(), _enc(base_url), _enc(norm)])
    key = f"{_enc(consumer_secret)}&{_enc(token_secret)}"
    sig = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{_enc(k)}="{_enc(v)}"' for k, v in sorted(oauth.items()))
