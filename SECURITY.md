# SulitShelf PH Security

## Supported version

Security fixes are applied to the latest release on the `main` branch. Upgrade
to the newest release before reporting an issue that may already be fixed.

## Report a vulnerability privately

Do not open a public issue for a suspected vulnerability. Use the repository's
private security-advisory feature when it is enabled, or contact the service
address configured by the operator as `SERVICE_CONTACT_EMAIL`.

Include the affected route or component, the impact, safe reproduction steps,
and the version shown by `/health/live`. Do not include real passwords, API
keys, payment details, receipt images, OAuth codes, or personal user data.

## Administrator trust boundary

Public password and OAuth registrations always create promoter accounts.
Administrator privileges are granted only through the trusted `seed-admin` or
`recover-admin` CLI commands. `ADMIN_EMAILS` supplies a default address to
those commands; an email match never promotes a web registration.

## Production requirements

SulitShelf refuses to start in production unless PostgreSQL, shared Redis,
Cloudinary, secure cookies, trusted proxy settings, canonical HTTPS URLs, and
independent application secrets are configured safely. Keep dependencies
pinned, run the test suite and `pip-audit` before release, and apply database
migrations through the idempotent `deploy-release` command.
