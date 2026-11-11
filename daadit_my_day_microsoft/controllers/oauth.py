import base64
import hashlib
import secrets
from time import time
from urllib.parse import urlencode

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from ..models.graph import SCOPES

SESSION_KEY = "daadit_my_day_microsoft_oauth"


class MyDayMailOAuth(http.Controller):
    @http.route("/my-day/microsoft/authorize", type="http", auth="user", methods=["GET"])
    def authorize(self):
        config = request.env["daadit.my.day.graph"]._configuration()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        request.session[SESSION_KEY] = {
            "state": state, "verifier": verifier, "uid": request.env.uid, "expires": time() + 600,
        }
        params = urlencode({
            "client_id": config["client_id"], "response_type": "code",
            "redirect_uri": config["redirect_uri"], "scope": SCOPES, "state": state,
            "code_challenge": challenge.decode().rstrip("="), "code_challenge_method": "S256",
            "prompt": "select_account",
        })
        return request.redirect(config["authority"] + "/authorize?" + params, local=False)

    @http.route("/my-day/microsoft/callback", type="http", auth="user", methods=["GET"])
    def callback(self, code=None, state=None, **_kwargs):
        flow = request.session.pop(SESSION_KEY, None)
        if (not flow or not state or not code or flow["uid"] != request.env.uid
                or flow["expires"] < time() or not secrets.compare_digest(flow["state"], state)):
            return request.make_response(
                request.env._("Microsoft 365 was not connected. Start the connection again."),
                status=400,
            )
        try:
            request.env["daadit.my.day.graph"]._connect(code, flow["verifier"])
        except (AccessError, UserError) as exc:
            return request.make_response(str(exc), headers=[("Content-Type", "text/plain")],
                                         status=400)
        action = request.env.ref("daadit_my_day_microsoft.action_import_mail")
        return request.redirect(f"/odoo/action-{action.id}")
