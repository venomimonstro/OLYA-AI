# Yandex ID login for X1

X1 uses Yandex OAuth 2.0 / Yandex ID. It never asks for a Yandex Mail password and never stores the Yandex access token.

## Owner setup

1. Create a Web application in Yandex OAuth.
2. Grant only the Yandex ID permissions needed by X1: basic account information and email (`login:info`, `login:email`).
3. Set the callback URI exactly to the value shown in **X1 Admin → Integrations → Вход через Яндекс**. In production it has the form:

   `https://YOUR-DOMAIN/v1/auth/yandex/callback`

4. Copy the OAuth Client ID and Client Secret from Yandex.
5. In X1 open `/admin/integrations`, set the public HTTPS base URL, Client ID and Client Secret, then enable **Разрешить «Войти через Яндекс»** and save.
6. Confirm that the card says **Яндекс ID: готов**. The Login and Register pages will then expose the Yandex button automatically.

Disabling the toggle immediately prevents new Yandex OAuth starts and callbacks from completing. Existing local X1 sessions remain local sessions and should be revoked with Logout / Logout all if required.

## Security contract

- Authorization Code flow with PKCE S256.
- Cryptographically random `state`; the browser cookie and server-side hashed state must match.
- OAuth state is single-use and expires after 10 minutes.
- Client Secret is encrypted at rest and is never returned by the admin API.
- PKCE verifier is encrypted at rest.
- Yandex access tokens and refresh tokens are not persisted.
- User info is requested with `Authorization: OAuth <token>`.
- Returned Yandex `client_id` must match the configured OAuth application.
- Existing X1 account is linked by the verified Yandex email; otherwise a new local account is created.
- The Yandex-authenticated email is marked verified in X1.
- Suspended X1 users cannot regain access through Yandex.
- OAuth entry is protected by global and per-IP rate limits.

## Production acceptance

Before public launch, test on the real HTTPS domain:

1. Login with a new Yandex account.
2. Login with a Yandex account whose email already has an X1 account.
3. Cancel consent at Yandex and confirm X1 does not create a session.
4. Reuse the same callback URL and confirm it is rejected as replayed/used.
5. Start login, disable Yandex in Admin, return from Yandex and confirm login fails closed.
6. Verify an inactive/suspended local user cannot sign in through the matching Yandex account.
7. Verify the callback never exposes either the Yandex token or X1 session token in the URL/history/referrer.
8. Verify the Yandex button disappears after the owner disables the integration.
9. Verify the `yandex_login_started` and `yandex_login_success` Metrika goals without sending email or chat content.

For separate test and production environments, use separate Yandex OAuth applications and separate secrets.
