from urllib.parse import quote, urlsplit
from uuid import UUID

import requests

from odoo import fields, models
from odoo.exceptions import AccessError, UserError

SCOPES = "offline_access User.Read Mail.Read"
GRAPH = "https://graph.microsoft.com/v1.0"


class ResUsers(models.Model):
    _inherit = "res.users"

    daadit_day_mail_refresh_token = fields.Char(copy=False, groups="base.group_system")
    daadit_day_mail_account_id = fields.Char(copy=False, groups="base.group_system")


class MyDayGraph(models.AbstractModel):
    _name = "daadit.my.day.graph"
    _description = "Microsoft 365 mail for My Day"

    def _configuration(self):
        user = self.env.user
        if not user.has_group("base.group_user"):
            raise AccessError(self.env._("Sign in as an internal employee."))
        get = self.env["ir.config_parameter"].sudo().get_param
        prefix = "daadit_my_day_microsoft."
        allowed_ids = (get(prefix + "allowed_user_ids") or "").split(",")
        if str(user.id) not in {value.strip() for value in allowed_ids}:
            raise AccessError(self.env._("Microsoft 365 is not enabled for your Odoo account."))
        try:
            tenant = str(UUID(get(prefix + "tenant_id") or ""))
            client_id = str(UUID(get(prefix + "client_id") or ""))
        except ValueError:
            raise UserError(self.env._(
                "The Microsoft 365 app registration is not configured yet."
            )) from None
        client_secret = get(prefix + "client_secret")
        redirect_uri = get(prefix + "redirect_uri") or ""
        parsed = urlsplit(redirect_uri)
        if not client_secret or parsed.scheme != "https" or not parsed.hostname or (
            parsed.path != "/my-day/microsoft/callback" or parsed.query or parsed.fragment
        ):
            raise UserError(self.env._("The Microsoft 365 app registration is not configured yet."))
        return {
            "authority": f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0",
            "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri,
        }

    def _request_json(self, method, url, **kwargs):
        try:
            response = requests.request(method, url, timeout=15, allow_redirects=False, **kwargs)
            if response.status_code == 429:
                raise UserError(self.env._("Microsoft 365 is busy. Try again later."))
            if response.status_code in (400, 401, 403):
                raise UserError(self.env._(
                    "Microsoft 365 denied access. Check your account connection."
                ))
            if response.status_code != 200:
                raise UserError(self.env._("Microsoft 365 is temporarily unavailable."))
            return response.json()
        except (requests.RequestException, ValueError):
            raise UserError(self.env._("Microsoft 365 is temporarily unavailable.")) from None

    def _token(self, config, **data):
        return self._request_json("POST", config["authority"] + "/token", data={
            **data, "client_id": config["client_id"], "client_secret": config["client_secret"],
            "scope": SCOPES,
        })

    def _get(self, token, path, params):
        return self._request_json("GET", GRAPH + path, params=params, headers={
            "Authorization": f"Bearer {token}",
            "Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"',
        })

    def _connect(self, code, verifier):
        config = self._configuration()
        tokens = self._token(
            config, grant_type="authorization_code", code=code,
            redirect_uri=config["redirect_uri"], code_verifier=verifier,
        )
        if not tokens.get("refresh_token") or not tokens.get("access_token"):
            raise UserError(self.env._("Microsoft 365 did not grant the required permissions."))
        account = self._get(tokens["access_token"], "/me", {
            "$select": "id,mail,userPrincipalName",
        })
        user = self.env.user
        email = (user.email or "").strip().casefold()
        addresses = {
            (account.get(field) or "").strip().casefold() for field in ("mail", "userPrincipalName")
        }
        if not email or email not in addresses or not account.get("id"):
            raise AccessError(self.env._(
                "Choose the Microsoft account that matches your Odoo email address."
            ))
        user.sudo().write({
            "daadit_day_mail_refresh_token": tokens["refresh_token"],
            "daadit_day_mail_account_id": account["id"],
        })

    def _access_token(self):
        config = self._configuration()
        user = self.env.user.sudo()
        if not user.daadit_day_mail_refresh_token:
            raise UserError(self.env._("Connect your own Microsoft 365 account first."))
        tokens = self._token(config, grant_type="refresh_token",
                             refresh_token=user.daadit_day_mail_refresh_token)
        if not tokens.get("access_token"):
            raise UserError(self.env._("Reconnect your Microsoft 365 account."))
        if tokens.get("refresh_token"):
            user.daadit_day_mail_refresh_token = tokens["refresh_token"]
        return tokens["access_token"]

    def _inbox(self):
        return self._get(self._access_token(), "/me/mailFolders/inbox/messages", {
            "$select": "id,conversationId,subject,from,receivedDateTime,bodyPreview",
            "$orderby": "receivedDateTime desc",
            "$top": "50",
        }).get("value", [])

    def _message(self, message_id):
        return self._get(self._access_token(), "/me/messages/" + quote(message_id, safe=""), {
            "$select": "id,conversationId,subject,body",
        })
