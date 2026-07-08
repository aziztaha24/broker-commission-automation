"""NetSuite client — OAuth 2.0 machine-to-machine + SuiteQL.

Pulls the four datasets (source lines, vendors, opportunities, customers)
directly from NetSuite, replacing the manual saved-search exports.

Secrets (Streamlit secrets.toml or environment):
    NS_ACCOUNT_ID       e.g. "11672327_SB1"  (underscores, uppercase — as it
                        appears in NetSuite's token endpoint docs; the URL
                        uses the hyphenated lowercase form automatically)
    NS_CLIENT_ID        from the Integration Record
    NS_CERT_ID          certificate ID shown in Setup > Integration >
                        OAuth 2.0 Client Credentials setup
    NS_PRIVATE_KEY      the PEM private key matching the uploaded certificate
                        (paste the whole -----BEGIN...----- block)

The SuiteQL for vendors/customers below is standard. The OPPORTUNITIES
query depends on your custom field IDs — fill the placeholders marked
TODO after checking the saved search definition (fields look like
custbody_... / custentity_... / custrecord_...).
"""
from __future__ import annotations
import time
import uuid

import pandas as pd
import requests

try:
    import jwt  # PyJWT + cryptography
except ImportError:  # pragma: no cover
    jwt = None

TOKEN_PATH = "/services/rest/auth/oauth2/v1/token"
SUITEQL_PATH = "/services/rest/query/v1/suiteql"


class NetSuiteError(Exception):
    pass


class NetSuiteClient:
    def __init__(self, account_id: str, client_id: str, cert_id: str,
                 private_key_pem: str):
        if jwt is None:
            raise NetSuiteError("PyJWT/cryptography not installed — "
                                "add 'pyjwt[crypto]' to requirements.txt")
        # account id: docs form '11672327_SB1' -> url form '11672327-sb1'
        self.account = account_id.strip()
        self.url_account = self.account.replace("_", "-").lower()
        self.base = f"https://{self.url_account}.suitetalk.api.netsuite.com"
        self.client_id = client_id.strip()
        self.cert_id = cert_id.strip()
        self.private_key = private_key_pem
        self._token: str | None = None
        self._token_exp = 0.0

    # ------------------------------------------------------------- auth
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        now = int(time.time())
        assertion = jwt.encode(
            {"iss": self.client_id,
             "scope": ["rest_webservices"],
             "aud": self.base + TOKEN_PATH,
             "iat": now, "exp": now + 3600,
             "jti": str(uuid.uuid4())},
            self.private_key,
            algorithm="PS256",
            headers={"kid": self.cert_id},
        )
        r = requests.post(
            self.base + TOKEN_PATH,
            data={"grant_type": "client_credentials",
                  "client_assertion_type":
                      "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
                  "client_assertion": assertion},
            timeout=30)
        if r.status_code != 200:
            raise NetSuiteError(f"Token request failed ({r.status_code}): "
                                f"{r.text[:400]}")
        data = r.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 3600))
        return self._token

    # ---------------------------------------------------------- suiteql
    def suiteql(self, query: str, page_size: int = 1000) -> pd.DataFrame:
        """Run a SuiteQL query, following pagination, return a DataFrame."""
        url = f"{self.base}{SUITEQL_PATH}?limit={page_size}"
        rows: list[dict] = []
        while url:
            r = requests.post(
                url,
                json={"q": query},
                headers={"Authorization": f"Bearer {self._get_token()}",
                         "Prefer": "transient",
                         "Content-Type": "application/json"},
                timeout=120)
            if r.status_code != 200:
                raise NetSuiteError(f"SuiteQL failed ({r.status_code}): "
                                    f"{r.text[:400]}")
            payload = r.json()
            rows.extend(payload.get("items", []))
            url = next((l["href"] for l in payload.get("links", [])
                        if l.get("rel") == "next"), None)
        df = pd.DataFrame(rows)
        return df.drop(columns=["links"], errors="ignore")


# ---------------------------------------------------------------- queries
# Standard-record queries — should work as-is (verify field names in your
# account with a quick test run; some accounts expose companyname vs
# altname etc.)
VENDORS_SQL = """
    SELECT id AS internal_id,
           COALESCE(companyname, entityid) AS name
    FROM vendor
    WHERE isinactive = 'F'
"""

CUSTOMERS_SQL = """
    SELECT entityid AS group_id,
           id       AS internal_group
    FROM customer
    WHERE entityid LIKE 'G-%'
"""

# TODO: replace the custom-field placeholders with the real IDs from the
# opportunities saved search (Edit > Results). Names/rates may live on the
# opportunity record (custbody_...) or the customer (custentity_...).
OPPORTUNITIES_SQL = """
    SELECT o.custbody_group_number             AS group_id,      -- TODO
           o.custbody_deal_primary_broker      AS primary_name,  -- TODO
           o.custbody_primary_commission       AS primary_rate,  -- TODO
           o.custbody_deal_co_primary          AS co_primary_name,   -- TODO
           o.custbody_co_primary_commission    AS co_primary_rate,   -- TODO
           o.custbody_deal_general_agent       AS ga_name,       -- TODO
           o.custbody_ga_commission            AS ga_rate,       -- TODO
           o.custbody_deal_managing_ga         AS mga_name,      -- TODO
           o.custbody_mga_commission           AS mga_rate       -- TODO
    FROM opportunity o
"""

# TODO: the source-lines query replicating the monthly source saved search.
# Provide that search's definition and this gets filled in the same way.
SOURCE_SQL = None


def pull_lookup_frames(client: NetSuiteClient):
    """Returns (vendors_df, opportunities_df, customers_df) with the same
    column names the file uploads use, so load_lookups() accepts either."""
    vendors = client.suiteql(VENDORS_SQL)
    customers = client.suiteql(CUSTOMERS_SQL)
    opportunities = client.suiteql(OPPORTUNITIES_SQL)
    return vendors, opportunities, customers
