"""OAuth for the gateway's HTTP transport.

claude.ai connectors authenticate via OAuth with Dynamic Client Registration — there's
no static-token field. Google itself doesn't do DCR, so we use FastMCP's GoogleProvider
(an OAuthProxy): it synthesizes a DCR-capable authorization server locally (exposing
/register, /authorize, /token, /auth/callback and the .well-known discovery docs) while
the real login proxies to Google. GoogleProvider has no email allowlist, so OwnerOnly
middleware enforces single-user access on the verified email.

Env (loaded by config.load_env at import):
  OAUTH_BASE_URL           public https origin, NO /mcp, no trailing slash
  GOOGLE_OAUTH_CLIENT_ID   from Google Cloud Console OAuth app
  GOOGLE_OAUTH_CLIENT_SECRET
  OAUTH_ALLOWED_EMAILS     comma-separated; only these verified emails may connect
"""

import os
import sys

from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import AccessToken, get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from . import config

# Claude's connector OAuth callbacks (claude.ai today, claude.com reserved). These are
# the client redirect URIs FastMCP allows — NOT what you register in Google (Google only
# ever redirects to our proxy's /auth/callback).
CLAUDE_REDIRECT_URIS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]
SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]


def allowed_emails() -> set[str]:
    raw = os.environ.get("OAUTH_ALLOWED_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _client_storage():
    """Persist DCR clients + tokens across gateway restarts (launchd restarts often).
    Best-effort: falls back to in-memory if the disk backend is unavailable."""
    try:
        from key_value.aio.stores.disk import DiskStore
        d = config.agent_home() / "state" / "oauth"
        d.mkdir(parents=True, exist_ok=True)
        return DiskStore(directory=str(d))
    except Exception as e:  # missing extra, permissions, etc. — degrade, don't crash
        print(f"[auth] client_storage disabled ({type(e).__name__}); "
              "re-login required after restarts", file=sys.stderr)
        return None


def build_google_auth() -> GoogleProvider:
    base_url = os.environ.get("OAUTH_BASE_URL", "").rstrip("/")
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    missing = [n for n, v in (
        ("OAUTH_BASE_URL", base_url),
        ("GOOGLE_OAUTH_CLIENT_ID", client_id),
        ("GOOGLE_OAUTH_CLIENT_SECRET", client_secret),
    ) if not v]
    if missing:
        sys.exit(f"OAuth misconfigured — set {', '.join(missing)} in .env "
                 "(or run with MCP_ALLOW_NO_AUTH=true for the dry-run test loop).")
    if "/mcp" in base_url or base_url.startswith("http://"):
        sys.exit(f"OAUTH_BASE_URL must be the bare public https origin (no /mcp): {base_url}")
    if not allowed_emails():
        sys.exit("Set OAUTH_ALLOWED_EMAILS (comma-separated) before exposing OAuth.")

    return GoogleProvider(
        client_id=client_id,
        client_secret=client_secret,
        base_url=base_url,
        # required == valid scopes sidesteps the Claude empty-scope -> invalid_scope bug
        # (PrefectHQ/fastmcp #1794).
        required_scopes=SCOPES,
        valid_scopes=SCOPES,
        allowed_client_redirect_uris=CLAUDE_REDIRECT_URIS,
        require_authorization_consent="remember",  # don't re-consent every login
        client_storage=_client_storage(),
    )


class OwnerOnly(Middleware):
    """Reject any authenticated identity whose verified email isn't on the allowlist.
    GoogleProvider authenticates ANY Google account; this is what makes it single-user."""

    def __init__(self, allowed: set[str]):
        self.allowed = allowed

    async def on_request(self, context: MiddlewareContext, call_next):
        token: AccessToken | None = get_access_token()
        if token is not None:  # None on public discovery/health hops — never gate those
            claims = token.claims or {}
            email = (claims.get("email") or "").lower()
            if not email or not claims.get("email_verified") or email not in self.allowed:
                raise PermissionError(f"Not authorized: {email or '<no verified email>'}")
        return await call_next(context)
