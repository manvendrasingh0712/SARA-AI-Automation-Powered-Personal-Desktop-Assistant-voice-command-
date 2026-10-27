"""
Regression tests for secret-file classification (audit batch 6b(3), D17).

THE DEFECT
----------
policy_rules.is_sensitive_parts() special-cased env files by exact name only:

    if name == ".env" or name.startswith(".env."):

so `.env` and `.env.local` were sensitive, but the equally common
`<name>.env` convention was NOT. This repository ships exactly such a file --
`ll.env`, which `.gitignore` lists precisely because it holds real
configuration -- and the security policy classified it as an ordinary
document:

    find_file  query=".env"    -> blocked (sensitive_path)
    find_file  query="ll.env"  -> ALLOWED
    open_file  path="C:\\project\\ll.env" -> ALLOWED

Two layers both missed it. `_looks_like_path("ll.env")` is False (no drive,
no separator, no ".."), so the generic check never treated the bare name as a
path at all; and when a full path *was* supplied, `sensitive_path()` returned
False because no pattern matched.

THE FIX
-------
`_SECRET_FILE_PATTERNS` was widened. The repo's own `.gitignore` is the best
available statement of what this project treats as secret, and the policy
list was strictly narrower than it; each added pattern closes a gap between
the two, plus the standard plaintext-credential files.

Deliberately NOT matched: a directory or file simply named `env`, and names
like `environment.txt` -- see test_still_not_sensitive.
"""
from __future__ import annotations

import pytest

from sara.core.security import policy

# Names that must now be classified secret. Each entry notes why.
NOW_SENSITIVE = [
    # the gap that motivated this: `<name>.env`
    "ll.env",
    "prod.env",
    "secrets.env",
    "C:\\project\\ll.env",
    "/home/u/app/staging.env",
    # keystores / cookie jars / session dumps -- all in .gitignore already
    "release.keystore",
    "debug.jks",
    "export.cookies",
    "cookies.txt",
    "cookies_backup.txt",
    "session.json",
    "session_2026.json",
    # standard plaintext-credential files
    ".git-credentials",
    ".netrc",
    "_netrc",
    ".npmrc",
    ".pypirc",
    # SSH key types the original id_rsa*/id_ed25519* patterns missed
    "id_ecdsa",
    "id_ecdsa.pub",
    "id_dsa",
]

# Must stay ordinary. Mirrors the NOT_SENSITIVE list in
# test_security_policy_paths.py plus the near-misses this change could
# plausibly have over-matched.
STILL_NOT_SENSITIVE = [
    "environment.txt",
    "C:\\proj\\env",          # a DIRECTORY called env is not a secret
    "env",
    "envelope.docx",
    "monkey.txt",
    "keys.txt",
    "tokenizer.py",
    "token_notes.txt",
    "credentials.txt",
    "sessions.md",            # not session*.json
    "my_cookies_recipe.txt",  # not *.cookies / cookies*.txt
    "C:\\Users\\a\\Documents\\report.pdf",
    "C:\\Windows\\notepad.exe",
]


@pytest.mark.parametrize("path", NOW_SENSITIVE)
def test_secret_files_are_classified_sensitive(path: str) -> None:
    assert policy.is_sensitive_path(path), f"{path!r} should be treated as a secret"


@pytest.mark.parametrize("path", STILL_NOT_SENSITIVE)
def test_still_not_sensitive(path: str) -> None:
    """Over-matching would start refusing ordinary user files."""
    assert not policy.is_sensitive_path(path), f"{path!r} must stay an ordinary file"


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", "ll.env"])
def test_env_files_are_refused_end_to_end(name: str) -> None:
    """
    The classifier is only useful if the tool validator actually refuses the
    call. This is the layer the user would have felt.
    """
    result = policy.validate_args("find_file", {"query": name})
    assert not result.ok, f"find_file accepted {name!r}"
    assert result.reason == "sensitive_path"


def test_full_paths_to_env_files_are_refused() -> None:
    for path in ("C:\\project\\.env", "C:\\project\\ll.env"):
        result = policy.validate_args("open_file", {"path": path})
        assert not result.ok, f"open_file accepted {path!r}"
        assert result.reason == "sensitive_path"


def test_policy_covers_everything_gitignore_calls_secret() -> None:
    """
    Keeps the two lists honest about each other. If someone adds a new secret
    pattern to .gitignore, this points out that the runtime policy has not
    learned about it. Only the unambiguous secret globs are checked -- the
    gitignore also lists plenty of non-secret build noise.
    """
    from sara.core.security import policy_rules

    gitignore_secret_globs = [
        "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
        "*.cookies", "cookies*.txt", "session*.json",
        "credentials.json", "token.json", "client_secret*.json",
    ]
    patterns = set(policy_rules._SECRET_FILE_PATTERNS)

    def covered(glob: str) -> bool:
        if glob in patterns:
            return True
        # credentials.json is covered by credentials*.json, etc.
        stem = glob.replace("*", "")
        return any(p.replace("*", "") == stem for p in patterns)

    missing = [g for g in gitignore_secret_globs if not covered(g)]
    assert not missing, (
        f".gitignore treats these as secret but the security policy does not: {missing}"
    )
