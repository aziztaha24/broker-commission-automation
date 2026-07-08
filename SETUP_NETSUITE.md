# NetSuite setup guide — one-time admin work

Goal: let the commission app authenticate to NetSuite (no stored
passwords) and pull the saved-search data itself via SuiteQL.
Time: roughly an hour. Requires an Administrator role.

## 1. Enable the features
Setup > Company > Enable Features > SuiteCloud tab:
- tick **REST Web Services**
- tick **OAuth 2.0**
Save.

## 2. Create an Integration Record
Setup > Integration > Manage Integrations > New:
- Name: `Broker Commission App`
- Untick "TBA: Authorization Flow" and "Authorization Code Grant"
- Tick **Client Credentials (Machine to Machine) Grant**
- Scope: tick **REST Web Services**
- Save.
- **Copy the Client ID shown once on the confirmation page** — it is never
  shown again. (Client Secret is not needed for this flow.)

## 3. Create the certificate key pair
On any machine with OpenSSL (Git Bash on Windows works):

    openssl req -new -x509 -newkey rsa:4096 -keyout private.pem \
        -out certificate.pem -nodes -days 730 -subj "/CN=broker-comm-app"

Keep `private.pem` secret. `certificate.pem` gets uploaded to NetSuite.

## 4. Map the certificate
Setup > Integration > OAuth 2.0 Client Credentials (M2M) Setup > Create New:
- Entity: the integration user (see step 5 — create that role/user first
  if needed)
- Role: the role from step 5
- Application: `Broker Commission App`
- Certificate: upload `certificate.pem`
- Save, then **copy the Certificate ID** from the list view.

## 5. Role & user
Create a minimal role (Setup > Users/Roles > Manage Roles > New):
- Name: `Broker Commission Integration`
- Permissions > Setup: **REST Web Services** (Full),
  **Log in using OAuth 2.0 Access Tokens** (Full),
  **SuiteAnalytics Workbook** (Edit) — required for SuiteQL
- Permissions > Lists: Vendors (View), Customers (View),
  Opportunities/Transactions as applicable (View)
Assign this role to a dedicated user (or an existing service user).

## 6. Put the secrets in Streamlit
Streamlit Cloud > your app > Settings > Secrets — add:

    NS_ACCOUNT_ID = "11672327_SB1"        # your account id (this example
                                          # is the sandbox; production has
                                          # no _SB1 suffix)
    NS_CLIENT_ID  = "<from step 2>"
    NS_CERT_ID    = "<from step 4>"
    NS_PRIVATE_KEY = """
    -----BEGIN PRIVATE KEY-----
    ...contents of private.pem...
    -----END PRIVATE KEY-----
    """

## 7. Field IDs for the opportunities query
Open the opportunities saved search (Edit > Results tab) and note the
field IDs of: group number, deal primary broker, primary commission,
co-primary broker + commission, general agent + commission, managing GA
+ commission. Custom fields look like `custbody_...` / `custentity_...`.
Send these IDs back so the OPPORTUNITIES_SQL placeholders in
`engine/netsuite.py` can be filled with the real names.

Same for the monthly source saved search if the source pull should also
be automated (recommended — then no exports at all).

## Notes
- Sandbox vs production are separate: integration record, certificate,
  and secrets must be redone in production when you switch.
- Certificates expire (730 days with the command above) — diary a renewal.
- If token requests return `invalid_grant`, the usual causes are: wrong
  Certificate ID in the `kid` header, account id format (use underscores
  in NS_ACCOUNT_ID, e.g. `1234567_SB1`), or the entity/role mapping in
  step 4 not matching.
