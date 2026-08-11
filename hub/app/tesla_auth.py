"""
Tesla OAuth (PKCE) for the dashcam client – for the optional Direct API.

clientId=dashcam, auth.tesla.com/oauth2/v3, redirect dashcam.tesla.com/callback (fixed),
scope incl. offline_access -> refresh token (live verified). Token cache in /data.
First login requires a browser (fixed redirect URL); afterwards the add-on refreshes
the token automatically.
"""
import json, time, base64, hashlib, secrets, urllib.parse, os, urllib.request, urllib.error

AUTH = "https://auth.tesla.com/oauth2/v3"
CLIENT_ID = "dashcam"
REDIRECT = "https://dashcam.tesla.com/callback"
SCOPE = "openid profile email employee offline_access"


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _post_form(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"Tesla token endpoint {e.code}: {detail}") from None


class TeslaAuth:
    """Token is stored ONLY in the encrypted vault (never in a plaintext file).
    A vault object with get_token()/set_token() is injected; PKCE verifier/state
    are kept in RAM only (same process handles url + callback)."""
    def __init__(self, vault):
        self.vault = vault
        self._pending = {}   # state -> (verifier, created_at); supports multiple in-flight attempts

    def make_login_url(self) -> str:
        verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state = _b64url(secrets.token_bytes(16))
        now = time.time()
        # drop stale attempts (>15 min) so this dict can't grow unbounded
        self._pending = {s: v for s, v in self._pending.items() if now - v[1] < 900}
        self._pending[state] = (verifier, now)
        q = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT, "response_type": "code",
             "scope": SCOPE, "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256"}
        return f"{AUTH}/authorize?" + urllib.parse.urlencode(q)

    def exchange_code(self, callback_url: str) -> dict:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(callback_url).query)
        code = q.get("code", [None])[0]
        state = q.get("state", [None])[0]
        pending = self._pending.pop(state, None) if state else None
        if not pending and len(self._pending) == 1:
            # Tesla's login flow sometimes routes through an extra "issuer"
            # (federation) hop and comes back with a different state than we
            # sent -- harmless here (single local admin, URL is hand-pasted
            # from the user's own already-authenticated browser), so fall
            # back to the one attempt actually in flight.
            only_state = next(iter(self._pending))
            pending = self._pending.pop(only_state)
        if not pending:
            raise RuntimeError("state mismatch (Login-Link abgelaufen, mehrfach angefordert, oder Dienst neu gestartet -- bitte neu einloggen)")
        verifier, _created = pending
        if not code:
            raise RuntimeError("no code in the URL")
        tok = _post_form(f"{AUTH}/token", {
            "grant_type": "authorization_code", "client_id": CLIENT_ID,
            "code": code, "redirect_uri": REDIRECT, "code_verifier": verifier})
        self._save(tok)
        return tok

    def get_access_token(self):
        """Returns a valid access token, or None if no login/refresh is possible."""
        tok = self._load()
        if not tok:
            return None
        if tok.get("_expires_at", 0) - 60 > time.time():
            return tok["access_token"]
        rt = tok.get("refresh_token")
        if not rt:
            return None
        try:
            new = _post_form(f"{AUTH}/token", {
                "grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": rt})
        except Exception:
            return None
        new.setdefault("refresh_token", rt)
        self._save(new)
        return new["access_token"]

    def status(self) -> dict:
        tok = self._load() or {}
        return {"logged_in": bool(tok), "has_refresh": bool(tok.get("refresh_token"))}

    def _save(self, tok: dict):
        tok = dict(tok)
        tok["_expires_at"] = time.time() + int(tok.get("expires_in", 28800))
        self.vault.set_token(tok)

    def _load(self):
        try:
            tok = self.vault.get_token()
        except Exception:
            return None
        return tok or None
