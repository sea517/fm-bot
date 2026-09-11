"""
Generate Oliver's Slack user token (xoxp-... or rotated xoxe.xoxp-1-...).

Localhost redirects require PKCE ("Must use PKCE to redirect to a non-web URI").

Usage:
  1. Slack app -> Basic Information: copy Client ID and Client Secret
  2. OAuth & Permissions -> Redirect URLs, add exactly:
       http://127.0.0.1:8765/callback
     then Save URLs
  3. User Token Scopes must include: chat:write
  4. Run while logged into Slack as Oliver:

       PYTHONPATH=. python3 -m src.slack_user_token \\
         --client-id YOUR_CLIENT_ID \\
         --client-secret YOUR_CLIENT_SECRET

  5. Browser opens -> Allow as Oliver -> paste printed values into .env
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

HTTP_REDIRECT_URI = "http://127.0.0.1:8765/callback"


def _is_user_access_token(token: str) -> bool:
    return token.startswith("xoxp-") or token.startswith("xoxe.xoxp-")


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for S256 PKCE."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _exchange(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict:
    response = requests.post(
        "https://slack.com/api/oauth.v2.access",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Get Slack user token for Oliver")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret", required=True)
    parser.add_argument(
        "--redirect-uri",
        default=HTTP_REDIRECT_URI,
        help=f"Must match a Redirect URL in the Slack app (default {HTTP_REDIRECT_URI})",
    )
    args = parser.parse_args()

    code_verifier, code_challenge = _pkce_pair()

    params = {
        "client_id": args.client_id,
        "user_scope": "chat:write",
        "redirect_uri": args.redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = "https://slack.com/oauth/v2/authorize?" + urllib.parse.urlencode(
        params
    )

    result: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            if "code" in qs:
                result["code"] = qs["code"][0]
                body = b"<html><body><h3>OK - return to the terminal.</h3></body></html>"
                self.send_response(200)
            else:
                err = qs.get("error", ["unknown"])[0]
                result["error"] = err
                body = f"<html><body><h3>Error: {err}</h3></body></html>".encode()
                self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, fmt: str, *args) -> None:  # noqa: A003
            return

    netloc = urllib.parse.urlparse(args.redirect_uri).netloc
    _host, _, port_path = netloc.partition(":")
    port_i = int(
        port_path or ("443" if args.redirect_uri.startswith("https") else "80")
    )
    server = HTTPServer(("127.0.0.1", port_i), Handler)

    print("1. Redirect URL in Slack must be exactly:")
    print(f"   {args.redirect_uri}")
    print("2. User Token Scopes must include: chat:write")
    print("3. Open this URL while logged into Slack as Oliver:\n")
    print(authorize_url)
    print("\nWaiting for browser callback...")
    webbrowser.open(authorize_url)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    if not done.wait(timeout=300):
        raise SystemExit("Timed out waiting for OAuth callback")

    if result.get("error"):
        raise SystemExit(f"Slack OAuth error: {result['error']}")

    payload = _exchange(
        args.client_id,
        args.client_secret,
        result["code"],
        args.redirect_uri,
        code_verifier,
    )
    if not payload.get("ok"):
        raise SystemExit(f"oauth.v2.access failed: {json.dumps(payload, indent=2)}")

    authed = payload.get("authed_user") or {}
    user_token = authed.get("access_token") or ""
    refresh_token = authed.get("refresh_token") or ""
    if not _is_user_access_token(user_token):
        raise SystemExit(
            "No user token in response. Check User Token Scopes include chat:write "
            f"and you clicked Allow as Oliver.\nFull response:\n{json.dumps(payload, indent=2)}"
        )

    print("\nSuccess - paste these into .env:\n")
    print(f"SLACK_USER_TOKEN={user_token}")
    if refresh_token:
        print(f"SLACK_USER_REFRESH_TOKEN={refresh_token}")
        print(f"SLACK_CLIENT_ID={args.client_id}")
        print(f"SLACK_CLIENT_SECRET={args.client_secret}")
        print(
            "\n(Token rotation is ON — access token expires in "
            f"{authed.get('expires_in', '?')}s; refresh creds keep Oliver posting.)"
        )
    print(f"\n(Authorized Slack user id: {authed.get('id')})")
    print("Then restart chat-loop. Assessment messages will post as Oliver.")


if __name__ == "__main__":
    main()
