# Security policy

## Before running

- Copy `.env.example` to `.env` and add local credentials there only.
- Never commit or upload `.env`, API keys, JWT secrets, database passwords, task dumps, browser exports or real retail documents.
- The checked-in PDF and evaluation data are synthetic demonstration materials.

## Reporting a vulnerability

Do not open an issue containing a credential, reproduction data from a real retailer, or an exploitable endpoint. For this portfolio project, contact the repository owner privately and rotate any exposed credential immediately.

## Scope

StoreFlow is a local Docker Compose demonstration prototype. Its SSRF controls, JWT/RBAC and audit features illustrate application boundaries; they are not a claim of a production security certification or service-level agreement.
