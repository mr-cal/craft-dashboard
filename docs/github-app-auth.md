# GitHub App Authentication Setup for Bot Automation

This guide explains how to set up a dedicated GitHub App to replace classic personal access tokens for machine/bot automation across `mr-cal/craft-dashboard` and `mr-cal/vps-infra`.

---

## 1. Why a GitHub App?

1. **Security Isolation & Repository Scoping**:
   Unlike classic PATs which grant broad access, or fine-grained PATs which cannot access repositories where the token owner is an outside collaborator, a GitHub App can be installed strictly on **only select repositories** (`craft-dashboard` and `vps-infra`).
   Even if an agent has access to this app's credentials, it is physically impossible to access or push to any other current or future repositories (e.g., `<project-b>`).
2. **Short-Lived Ephemeral Tokens**:
   The app mints 1-hour installation tokens on demand. There are no expiring personal PATs to renew manually.
3. **Bot Identity & Attribution**:
   Commits are attributed to `mr-cal-bot` using existing git configuration while authenticated via the app's installation token.

---

## 2. Registering the GitHub App

1. Log in to GitHub as `mr-cal` and navigate to:
   **Settings → Developer settings → GitHub Apps → [New GitHub App](https://github.com/settings/apps/new)**
2. Fill in the App Details:
   - **GitHub App name**: `mr-cal-bot-craft` (or a name of your choice)
   - **Homepage URL**: `https://github.com/mr-cal/craft-dashboard`
3. Webhook:
   - **Active**: **Uncheck** / Disable (no webhook URL needed).
4. Repository Permissions:
   - **Contents**: `Access: Read and write` (to push commits and tags)
   - **Workflows**: `Access: Read and write` (to update GitHub Actions workflow files)
   - **Metadata**: `Access: Read-only` (selected automatically)
5. Where can this GitHub App be installed?
   - Select **"Only on this account"**.
6. Click **Create GitHub App**.

---

## 3. Generate Private Key and Note App ID

1. On the app settings page, locate the **App ID** near the top (e.g., `1234567`). Note this value.
2. Scroll down to **Private keys** and click **Generate a private key**.
3. A `.pem` file will automatically download (e.g. `mr-cal-bot-craft.2026-09-28.private-key.pem`).
4. Save this file to a secure, uncommitted location (e.g., `~/.config/github-apps/mr-cal-bot-craft.pem` or inside the repo as `.github-app-key.pem` which is ignored by git). Ensure file permissions are restricted:
   ```bash
   chmod 0600 ~/.config/github-apps/mr-cal-bot-craft.pem
   ```

---

## 4. Install the App on Selected Repositories

1. In the left sidebar of the GitHub App settings, click **Install App**.
2. Click **Install** next to the `mr-cal` account.
3. Choose **"Only select repositories"** and select:
   - `craft-dashboard`
   - `vps-infra`
4. Click **Install**.
5. Once installed, look at the URL in your browser:
   `https://github.com/settings/installations/<INSTALLATION_ID>`
   Note the numeric `<INSTALLATION_ID>` (e.g., `89101112`).

---

## 5. Configure `.env.llm`

In `/home/callahan.kovacs@canonical.com/dev/craft/craft-dashboard/.env.llm` (and optionally `vps-infra/.env.llm`), add:

```bash
GITHUB_APP_ID=1234567
GITHUB_APP_INSTALLATION_ID=89101112
GITHUB_APP_PRIVATE_KEY_PATH=/home/callahan.kovacs@canonical.com/.config/github-apps/mr-cal-bot-craft.pem
```

---

## 6. How Agents Use the Token

The repository provides `scripts/mint_bot_token.py` to mint 1-hour ephemeral tokens on demand with automatic local caching.

```bash
# Push to craft-dashboard:
git push "$(uv run scripts/mint_bot_token.py --print-remote-url --repo craft-dashboard)" main

# Push to vps-infra:
git push "$(uv run scripts/mint_bot_token.py --print-remote-url --repo vps-infra)" main
```
