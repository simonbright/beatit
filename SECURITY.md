# Security policy

## Reporting a vulnerability

If you discover a security issue, please report it privately rather than opening a public GitHub issue. Contact the repository owner through GitHub or the project page at [simonbrightman.com/projects/beatit/](https://simonbrightman.com/projects/beatit/).

## Sensitive data

- Never commit `.env`, API keys, passwords, or clinical documents.
- The `data/` directory is local runtime storage and must stay out of git.
- When deploying, set secrets only in your host environment (for example Render dashboard variables).

## Authentication

When `AUTH_USERNAME` and `AUTH_PASSWORD` are set, the app uses cookie-based sign-in. Use strong passwords and HTTPS in production.
