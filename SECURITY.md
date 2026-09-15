# Security Policy

## Supported Versions

SignBridge is currently under active development. Security updates are provided for the latest version available on the `main` branch.

| Version                   | Supported          |
| ------------------------- | ------------------ |
| `main`                    | :white_check_mark: |
| Older versions / releases | :x:                |

Because SignBridge is primarily developed as an open-source project and does not currently maintain multiple stable release branches, security fixes are generally applied to the latest development version.

## Reporting a Vulnerability

If you discover a security vulnerability in SignBridge, please **do not open a public GitHub issue** containing sensitive security details.

Instead, report the vulnerability privately through GitHub's **Security Advisories** feature:

**Repository → Security → Advisories → Report a vulnerability**

When reporting a vulnerability, please include:

* A clear description of the vulnerability.
* The affected component or file.
* Steps to reproduce the issue.
* The potential security impact.
* Any relevant logs, screenshots, or proof-of-concept code.
* A suggested mitigation or fix, if available.

Please avoid including passwords, API keys, access tokens, personal information, or other sensitive credentials in the report.

## What to Expect

After receiving a vulnerability report:

1. The report will be reviewed to determine whether it affects SignBridge.
2. Additional information may be requested if the issue cannot be reproduced or assessed from the initial report.
3. Valid vulnerabilities will be prioritized based on their severity and potential impact.
4. Security fixes will be developed and tested before being merged.
5. Where appropriate, affected users and contributors will be informed through the repository's security advisory mechanism.

We aim to acknowledge security reports within **14 days** and provide an initial assessment as soon as reasonably possible.

## Disclosure

Please allow reasonable time for a vulnerability to be investigated and fixed before publicly disclosing the details.

Once a fix is available, the vulnerability may be disclosed through a GitHub Security Advisory when appropriate.

## Scope

This policy applies to security vulnerabilities in the SignBridge source code, backend services, frontend application, CI/CD workflows, and other components maintained within this repository.

Third-party dependencies should also be reported when their use creates a security vulnerability specifically affecting SignBridge.

## Security Practices

SignBridge uses automated security and dependency tooling where applicable, including:

* GitHub CodeQL for static code analysis.
* GitHub Dependency Review for pull-request dependency changes.
* Dependabot for dependency update proposals.
* Automated backend and frontend tests through GitHub Actions.

Contributors should avoid committing secrets, API keys, credentials, or other sensitive configuration to the repository. Use environment variables and the provided `.env.example` configuration where applicable.
