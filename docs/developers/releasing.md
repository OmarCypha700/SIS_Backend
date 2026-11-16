# Versions and releases

Internal: for the development team.

## One version for the product

SIS uses **semantic versioning**, `MAJOR.MINOR.PATCH`. The backend and the frontend always carry the
**same** number and are released together, because each release of one is tested with the same release
of the other.

| Where | What |
|---|---|
| Backend `VERSION` | The number. Read into `settings.SIS_VERSION`, the API documentation, and `server_version` on `/api/v1/auth/me/` (signed-in users only) |
| Frontend `package.json` | The same number, built into the portal as `NEXT_PUBLIC_APP_VERSION` |
| The portal | The account menu and *My account* show "Version 1.0.0". If the backend runs a different release they show "1.0.0 (server 1.0.1)": a sign that one half was not updated |
| User guide | The header and footer of `public/docs/index.html` |
| Git | A tag `vX.Y.Z` on both repositories |
| `CHANGELOG.md` | One entry per release |

`python scripts/set_version.py X.Y.Z` sets the number in all of these places except Git and the
changelog.

## Which number to change

| Bump | When | Examples |
|---|---|---|
| **Patch** `1.0.1` | Fixes only; nothing for users or operators to do | A slow PDF fixed, a wrong label corrected |
| **Minor** `1.1.0` | New features or settings that work without anyone acting, including migrations that run automatically | Signed-in devices, a new report, a new optional setting |
| **Major** `2.0.0` | Institutions or operators **must** act, or something is removed or behaves differently | A new required setting, an irreversible data change, a removed feature, an API change that breaks an integration |

Change the number **when you release**, never on every commit. Between releases the code keeps the last
released number (or the release candidate being tested).

**Release candidates:** before a version is final, release `X.Y.Z-rc.1`, `-rc.2` and so on to a test or
pilot portal. When a candidate passes, release the same code as `X.Y.Z`.

## Releasing

1. **Freeze:** only fixes for this release from now on.
2. **Test:** backend tests, end-to-end tests, migrations check, API schema, `check --deploy`,
   `pip-audit`, `npm audit`, lint ([testing.md](testing.md)). Run on PostgreSQL when migrations changed,
   and always before a major or first production release.
3. **Set the number:** `python scripts/set_version.py X.Y.Z`.
4. **Write the changelog entry** in `CHANGELOG.md`: date, what changed in plain words, **update steps**
   (new settings, migrations, anything operators must do) and the check results.
5. **Update the user guide** (`public/docs/index.html`) if anything people see has changed.
6. **Commit both repositories**, then tag both with the same annotated tag:
   ```bash
   git tag -a vX.Y.Z -m "SIS X.Y.Z"
   git push origin vX.Y.Z
   ```
7. **Deploy from the tag** to a test portal, smoke test, then to institutions
   ([../operations/maintenance.md](../operations/maintenance.md), "Updating"). Each institution's
   portal shows its version in the account menu, so you always know what it runs.
8. **Republish the shared copy** of the user guide, if you share it outside the portals.

## Fixing a release already in use

Branch from the tag, fix, and release the next patch:
```bash
git switch -c fix/1.0.x v1.0.0
# fix, test, set_version.py 1.0.1, changelog, commit
git tag -a v1.0.1 -m "SIS 1.0.1"
```
Bring the fix back into the main branch as well, so the next minor release includes it.

## Supported versions

Keep every institution on the latest minor release of the current major version. Security fixes are
released as patches of that line; an institution on an older line is updated rather than patched.
