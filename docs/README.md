# SIS documentation

Copyright © 2026 Alfa Calix Enterprise. All rights reserved. Confidential: see [LICENSE](../LICENSE).

The documents are grouped by who reads them. Day-to-day use of the portal is covered by the
**SIS User Guide**, a web page for institutions, staff and students. Every portal serves it at
`https://<portal>/docs`, open without signing in, with the institution's name and logo. Its source is
the frontend's `public/docs/index.html`: update it with each release that changes what people see.

## Operations: for whoever installs and runs an institution's portal

| Document | Contents |
|---|---|
| [operations/deployment.md](operations/deployment.md) | Installing a production portal: PostgreSQL, backend, frontend, email, push, online payment, go-live checklist |
| [operations/configuration.md](operations/configuration.md) | Every server setting (environment variable) and what to set it to |
| [operations/maintenance.md](operations/maintenance.md) | Daily task, backups and restores, updates and rollback, monitoring, troubleshooting |
| [operations/demo-hosting.md](operations/demo-hosting.md) | Demonstration installs on free hosting with demo data, and their limits |

## Security and data protection

| Document | Audience | Contents |
|---|---|---|
| [security/overview.md](security/overview.md) | Institutions (management, IT, auditors) | How the portal protects accounts and data |
| [security/data-protection.md](security/data-protection.md) | Institutions and Alfa Calix Enterprise | Ghana Data Protection Act responsibilities, contract annex, student privacy notice, incident response |
| [security/design.md](security/design.md) | Internal only | Detailed security design, limits and controls. **Do not share outside the development team.** |

## Reference: how the rules work

For administrators, support staff and operators who need the detail behind a screen.

| Document | Contents |
|---|---|
| [reference/academic-regulations.md](reference/academic-regulations.md) | Resits, retakes, repeating a level, progression, probation and dismissal |
| [reference/fees-and-payments.md](reference/fees-and-payments.md) | Fee billing, the registration rule, late penalty, payments, bank-statement import |
| [reference/transcripts.md](reference/transcripts.md) | Official transcripts, verification, printed documents, signature and seal |
| [reference/notifications.md](reference/notifications.md) | In-app, push and email notifications and the installable app |
| [reference/tutor-assessment.md](reference/tutor-assessment.md) | Students assess tutors before seeing results: rules, anonymity, who sees what, reports |

## Developers: internal

| Document | Contents |
|---|---|
| [developers/architecture.md](developers/architecture.md) | Modules, request flow, configuration, deployment models |
| [developers/database-design.md](developers/database-design.md) | Entities, constraints, history rules, SQLite and PostgreSQL |
| [developers/api.md](developers/api.md) | API conventions and endpoint overview (the generated API docs are the full reference) |
| [developers/testing.md](developers/testing.md) | Backend and end-to-end tests, release checks |
| [developers/releasing.md](developers/releasing.md) | Version numbers, when to change them, and the release routine |
| [developers/sis-release-guide.pdf](developers/sis-release-guide.pdf) | Printable terminal guide: git tags, releasing, deploying from a tag, fixes, rollback, quick reference |

Release notes, current limitations and the roadmap are in [CHANGELOG.md](../CHANGELOG.md).

## What to share with whom

| Audience | Share |
|---|---|
| Institution management and staff | The SIS User Guide; `security/overview.md`; `security/data-protection.md` |
| Institution IT or hosting partner | The above, plus `operations/*` and `reference/*` |
| Development team | Everything, including `security/design.md` and `developers/*` |

The repositories are private. Share documents as files or exports, not by giving repository access.
