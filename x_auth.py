#!/usr/bin/env python3
"""Collega un account X al bot (OAuth 1.0a con PIN). Uso: python3 x_auth.py

1. Incolla API Key e API Key Secret dell'app X (non vengono mostrati).
2. Apri il link che compare MENTRE sei connesso su x.com con l'account da collegare,
   clicca "Authorize app" e copia il PIN.
3. Lo script mette negli appunti, uno alla volta, Access Token e Access Token Secret
   da incollare nei secrets di GitHub. Non li stampa mai a schermo.
"""
import getpass
import re
import subprocess
import urllib.request
from urllib.parse import parse_qsl, urlencode

import oauth1

API = "https://api.x.com"


def clean(s):
    return re.sub(r"\x1b\[20[01]~|\s", "", s)


def call(url, key, secret, token="", token_secret="", extra=None, params=None):
    header = oauth1.auth_header("POST", url, key, secret, token, token_secret, extra_oauth=extra, params=params)
    data = urlencode(params or {}).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Authorization": header})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return dict(parse_qsl(r.read().decode()))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Errore da X ({e.code}): {e.read().decode()[:300]}")


def copy(value):
    subprocess.run(["pbcopy"], input=value.encode(), check=True)


key = clean(getpass.getpass("API Key (Consumer Key): "))
secret = clean(getpass.getpass("API Key Secret: "))
req = call(f"{API}/oauth/request_token", key, secret, extra={"oauth_callback": "oob"})
print("\n1) Apri questo link nel browser, connesso con l'account X da collegare:")
print(f"   {API}/oauth/authorize?oauth_token={req['oauth_token']}")
pin = clean(input("\n2) Scrivi qui il PIN che ti mostra X e premi Invio: "))
acc = call(f"{API}/oauth/access_token", key, secret, req["oauth_token"], req["oauth_token_secret"],
           params={"oauth_verifier": pin})
name = acc.get("screen_name", "?")
print(f"\nCollegato: @{name}")
copy(acc["oauth_token"])
input(f"3) Access Token copiato negli appunti: incollalo nel secret X_TOKEN_<ACCOUNT> e premi Invio qui... ")
copy(acc["oauth_token_secret"])
input("4) Access Token Secret copiato: incollalo nel secret X_TOKEN_SECRET_<ACCOUNT> e premi Invio qui... ")
copy("")
print("Fatto. Appunti svuotati.")
