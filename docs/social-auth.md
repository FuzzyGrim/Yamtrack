# Social Authentication

YamTrack supports a variety of authentication methods through [django-allauth](https://docs.allauth.org/en/dev/socialaccount/providers/index.html), allowing you to integrate with popular social providers and identity management systems.

## Getting Started

Social authentication in YamTrack is configured through environment variables. You'll need to:

1. Enable the social providers you want to use
2. Configure the provider-specific settings
3. Set up your provider to work with YamTrack

## Enabling Social Providers

Use the `SOCIAL_PROVIDERS` environment variable to specify which providers you want to enable:

```bash
SOCIAL_PROVIDERS=allauth.socialaccount.providers.openid_connect,allauth.socialaccount.providers.github
```

This example enables both OpenID Connect and GitHub authentication.

## Configuring Providers

Provider configuration is done through the `SOCIALACCOUNT_PROVIDERS` environment variable. This should be a JSON string containing all the necessary settings for your providers.

### Example: OpenID Connect with Authelia

```bash
SOCIAL_PROVIDERS=allauth.socialaccount.providers.openid_connect
SOCIALACCOUNT_PROVIDERS={"openid_connect":{"OAUTH_PKCE_ENABLED":true,"APPS":[{"provider_id":"authelia","name":"Authelia","client_id":"your-client-id","secret":"your-client-secret","settings":{"server_url":"https://authelia.yourdomain.com/.well-known/openid-configuration"}}]}}
```

### Example: GitHub

```bash
SOCIAL_PROVIDERS=allauth.socialaccount.providers.github
SOCIALACCOUNT_PROVIDERS={"github":{"SCOPE":["user","repo","read:org"]}}
```

## Provider-Specific Setup

### OpenID Connect (Authelia, Authentik, Keycloak, etc.)

1. In your OIDC provider, create a new application/client:
   - Set the redirect URI to: `https://yamtrack.yourdomain.com/accounts/oidc/[provider_id]/login/callback/`
   - Replace `[provider_id]` with the value you set in the `provider_id` field in your configuration
   - For example: `https://yamtrack.yourdomain.com/accounts/oidc/authelia/login/callback/`
   - Set `token_endpoint_auth_method` to `client_secret_post`.

2. Configure YamTrack with the client ID and secret from your provider

#### Authentik Example

In Authentik:

1. Create an OAuth2/OpenID Provider (under Applications/Providers) with these settings:
   - Name: Yamtrack
   - Redirect URI: `https://yamtrack.yourdomain.com/accounts/oidc/authentik/login/callback/`

2. In YamTrack, configure:

    ```bash
    SOCIAL_PROVIDERS=allauth.socialaccount.providers.openid_connect
    SOCIALACCOUNT_PROVIDERS={"openid_connect":{"OAUTH_PKCE_ENABLED":true,"APPS":[{"provider_id":"authentik","name":"Authentik","client_id":"<Client ID>","secret":"<Client Secret>","settings":{"server_url":"https://authentik.yourdomain.com/application/o/yamtrack/.well-known/openid-configuration"}}]}}
    ```

## Connecting Social Accounts to Existing Users

To add social authentication to an existing user:

1. Log in to YamTrack with your local username and password
2. Click the settings icon in the sidebar
3. Click "Accounts" in the settings menu
4. In the "Third-Party Connections" section, click "Manage Account Connections"
5. You'll see a list of available social providers - click the provider you want to link to your account

Once connected, you can use either your local credentials or the social provider to log in to your account.

## Additional Options

### Disable Local Authentication

If you want to use only social authentication and disable the traditional username/password login:

```bash
SOCIALACCOUNT_ONLY=True
```

### Redirect Login to SSO

To automatically redirect users from the login page to your SSO provider:

```bash
REDIRECT_LOGIN_TO_SSO=True
```

### Disable Registration

To prevent new users from registering (useful for private instances):

```bash
REGISTRATION=False
```
