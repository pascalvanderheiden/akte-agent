#!/usr/bin/env python3
"""GitHub Code Access phase; called only after sre-setup.sh verifies the core."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote, urlsplit

DEFAULT_REPOSITORY = "https://github.com/pascalvanderheiden/akte-agent"
AUDIENCE = "https://azuresre.dev"
RETRY_HELP = (
    "Open https://sre.azure.com, select this environment's agent, then Builder > "
    "Code Access (or Knowledge base > Add repository). Complete/renew authorization "
    "outside this hook; grant only repository Metadata/Contents read access. "
    "Rerun ./hooks/sre-setup.sh --environment <azd-environment>. "
    "For core-only mode set SRE_CONNECT_GITHUB=false."
)


class SetupResultError(Exception):
    def __init__(self, state: str, message: str):
        self.state = state
        self.message = message


def fail(message: str) -> NoReturn:
    raise SetupResultError("failed", message)


def pending(message: str) -> NoReturn:
    raise SetupResultError("pending", message)


def parse_json(raw: str) -> object:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError):
        fail("Malformed JSON response; raw diagnostics suppressed.")


def repository_url(value: object) -> tuple[str, str, str]:
    if not isinstance(value, str):
        fail("Repository URL must be a string.")
    match = re.fullmatch(
        r"https://(github\.com|[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.ghe\.com)"
        r"/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})/?",
        value,
        re.IGNORECASE,
    )
    if not match:
        fail("Use an HTTPS GitHub repository URL without credentials, port, query, fragment, or extra path.")
    host, owner, repo = match.groups()
    repo = repo.removesuffix(".git")
    if not repo or repo in (".", ".."):
        fail("Invalid repository name.")
    slug = f"{owner}/{repo}".lower()
    return f"https://{host.lower()}/{slug}", host.lower(), slug


def branch_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 255
        or value.startswith(("-", "/"))
        or value.endswith(("/", "."))
        or value == "@"
        or any(part.startswith(".") or part.endswith(".lock") or not part for part in value.split("/"))
        or any(bad in value for bad in ("..", "@{"))
        or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value)
    ):
        fail("Invalid SRE_GITHUB_BRANCH; supply a valid Git branch name, not a checkout expression.")
    return value


def bounded_integer(name: str, default: str, maximum: int, minimum: int = 0) -> int:
    value = os.environ.get(name, default)
    if not re.fullmatch(r"[0-9]{1,3}", value) or not minimum <= int(value) <= maximum:
        fail(f"{name} must be an integer {minimum}-{maximum}.")
    return int(value)


class GitHubSetup:
    def __init__(self, scratch: Path, pat: str):
        self.scratch = scratch
        self.pat = pat
        self.token = ""
        self.endpoint = ""
        self.attempts = bounded_integer("SRE_GITHUB_ATTEMPTS", "3", 10, 1)
        self.delay = bounded_integer("SRE_GITHUB_DELAY_SECONDS", "5", 30)

    def command(self, args: list[str], input_text: str = "") -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                args,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired:
            fail("CLI request timed out; retry from a network with access.")
        except (OSError, UnicodeError):
            fail("Cannot run required CLI or decode its response; raw diagnostics suppressed.")

    def wait(self, attempt: int) -> bool:
        if attempt + 1 < self.attempts:
            time.sleep(self.delay)
            return True
        return False

    def resolve_endpoint(self) -> None:
        for attempt in range(self.attempts):
            proc = self.command(
                [
                    "az",
                    "resource",
                    "show",
                    "--ids",
                    os.environ["SRE_AGENT_ID"],
                    "--subscription",
                    os.environ["AZURE_SUBSCRIPTION_ID"],
                    "--api-version",
                    os.environ["SRE_API_VERSION"],
                    "--query",
                    "properties.agentEndpoint",
                    "--output",
                    "json",
                    "--only-show-errors",
                ]
            )
            if proc.returncode:
                if re.search(
                    r"(?m)^\s*(?:ERROR: )?\((ResourceNotFound|TooManyRequests|ServiceUnavailable|GatewayTimeout)\)",
                    proc.stderr,
                ):
                    if self.wait(attempt):
                        continue
                    fail("ARM endpoint discovery exhausted bounded retries.")
                fail("ARM endpoint discovery failed; inspect resource-read permissions/network privately.")
            endpoint = parse_json(proc.stdout)
            if endpoint is None or endpoint == "":
                if self.wait(attempt):
                    continue
                pending("ARM has not published properties.agentEndpoint yet.")
            if not isinstance(endpoint, str):
                fail("Malformed ARM agentEndpoint.")
            # Trust only the ARM-returned HTTPS origin, never an override or guessed hostname.
            parsed = urlsplit(endpoint)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.netloc != parsed.hostname
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
                or re.search(r"[\s\x00-\x1f\x7f]", endpoint)
            ):
                fail("ARM agentEndpoint must be a credential-free HTTPS origin.")
            self.endpoint = endpoint.rstrip("/")
            return

    def acquire_token(self) -> None:
        proc = self.command(
            [
                "az",
                "account",
                "get-access-token",
                "--resource",
                AUDIENCE,
                "--subscription",
                os.environ["AZURE_SUBSCRIPTION_ID"],
                "--query",
                "accessToken",
                "--output",
                "json",
                "--only-show-errors",
            ]
        )
        if proc.returncode:
            if re.search(
                r"AADSTS(?:50076|50079|50158|53000|53003|65001|700082|70043)\b"
                r"|Please run ['\"]az login|Interactive authentication is needed|"
                r"(?m:^\s*(?:ERROR: )?\((?:InteractionRequired|AuthenticationRequired|AuthorizationFailed)\))",
                proc.stderr,
            ):
                raise SetupResultError(
                    "unavailable",
                    "SRE token unavailable. From an authorized session sign in yourself with "
                    'az login --scope "https://azuresre.dev/.default", select the matching subscription, and retry.',
                )
            fail("SRE token CLI failed unexpectedly; raw diagnostics suppressed.")
        token = parse_json(proc.stdout)
        if not isinstance(token, str) or not token or re.search(r"[\s\x00-\x1f\x7f]", token):
            fail("Malformed SRE token response; no request was sent.")
        self.token = token

    def request(self, method: str, url: str, *, bearer: str = "", body: object = None) -> tuple[int, object]:
        response = self.scratch / "response.json"
        request = self.scratch / "request.json"
        config = 'header = "Accept: application/json"\n'
        if bearer:
            config += "header = " + json.dumps(f"Authorization: Bearer {bearer}") + "\n"
        if body is not None:
            request.write_text(json.dumps(body), encoding="utf-8")
            config += 'header = "Content-Type: application/json"\n'
            config += "data-binary = " + json.dumps(f"@{request}") + "\n"
        try:
            for attempt in range(self.attempts):
                proc = self.command(
                    [
                        "curl",
                        "--disable",
                        "--silent",
                        "--show-error",
                        "--proto",
                        "=https",
                        "--connect-timeout",
                        "10",
                        "--max-time",
                        "30",
                        "--max-filesize",
                        "1048576",
                        "--request",
                        method,
                        "--url",
                        url,
                        "--config",
                        "-",
                        "--output",
                        str(response),
                        "--write-out",
                        "%{http_code}",
                    ],
                    config,
                )
                if proc.returncode:
                    if proc.returncode in (6, 7, 28):
                        if self.wait(attempt):
                            continue
                        fail("Network/DNS request failed after bounded retries.")
                    fail("HTTP client failed unexpectedly; raw diagnostics suppressed.")
                if not re.fullmatch(r"[1-5][0-9]{2}", proc.stdout):
                    fail("Malformed HTTP status from client.")
                status = int(proc.stdout)
                if status in (429, 502, 503, 504):
                    if self.wait(attempt):
                        continue
                    fail("Service readiness/rate limit exhausted bounded retries.")
                # Error bodies can contain credentials, arbitrary text, or HTML. Never echo/interpret them.
                if status >= 300 or status == 204:
                    return status, None
                return status, parse_json(response.read_text(encoding="utf-8"))
        finally:
            request.unlink(missing_ok=True)
            response.unlink(missing_ok=True)
        fail("No HTTP response.")

    def data_plane(self, method: str, path: str, body: object = None) -> object:
        status, data = self.request(method, self.endpoint + path, bearer=self.token, body=body)
        if status == 401:
            pending("SRE data-plane authorization expired or is missing; sign in outside the hook.")
        if status in (403, 501):
            raise SetupResultError(
                "unavailable", f"SRE API denied access or confirmed unsupported capability (HTTP {status})."
            )
        if status not in (200, 201, 204):
            fail(f"Unexpected SRE API HTTP {status}; no automatic permission or API fallback.")
        return data

    def domains(self) -> set[str]:
        result = self.data_plane("GET", "/api/v2/github/domains")
        if not isinstance(result, dict) or not isinstance(result.get("values"), list):
            fail("Malformed GitHub domains response.")
        domains = set()
        for item in result["values"]:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
                fail("Malformed GitHub domain entry.")
            domains.add(item["name"].lower().replace("_", "."))
        return domains

    def github(self, host: str, path: str) -> tuple[int, object]:
        # Only the deliberately supplied PAT may authenticate to GitHub, never an Azure/gh token.
        return self.request("GET", f"https://api.{host}{path}", bearer=self.pat if host == "github.com" else "")

    def setup(self) -> None:
        url, host, slug = repository_url(os.environ.get("SRE_GITHUB_REPOSITORY_URL") or DEFAULT_REPOSITORY)
        branch = os.environ.get("SRE_GITHUB_BRANCH") or ""
        if branch:
            branch = branch_name(branch)
        self.resolve_endpoint()
        self.acquire_token()
        domains = self.domains()
        if host not in domains:
            if host != "github.com":
                pending(
                    "This GitHub Enterprise host needs an existing BYO GitHub App authorization; PAT is unsupported."
                )
            if not self.pat:
                pending(
                    "No SRE-side authorization for the target GitHub domain. Public repos still require authorization."
                )
            self.data_plane("PUT", "/api/v2/github/domains/github_com", {"authType": "Pat", "pat": self.pat})
            if host not in self.domains():
                pending("PAT submitted but target-domain authorization has not propagated.")
        # An existing domain is only a candidate. Repo clone/readback below must prove it usable.
        if not branch:
            status, data = self.github(host, f"/repos/{slug}")
            if status in (401, 403, 404):
                pending("Default-branch discovery unavailable. Set SRE_GITHUB_BRANCH explicitly and rerun.")
            if status != 200:
                fail(f"Unexpected default-branch discovery HTTP {status}.")
            if not isinstance(data, dict) or "default_branch" not in data:
                fail("Malformed default-branch discovery response.")
            branch = branch_name(data["default_branch"])

        status, data = self.github(host, f"/repos/{slug}/branches/{quote(branch, safe='')}")
        expected_commit = ""
        if status == 200:
            if not isinstance(data, dict) or data.get("name") != branch or not isinstance(data.get("commit"), dict):
                fail("Malformed GitHub branch response.")
            expected_commit = data["commit"].get("sha", "")
            if not isinstance(expected_commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", expected_commit):
                fail("Malformed GitHub branch commit.")
        elif status not in (401, 403, 404):
            fail(f"Unexpected GitHub branch lookup HTTP {status}.")

        result = self.data_plane("GET", "/api/v2/repos")
        repos = result.get("value") if isinstance(result, dict) else result
        if not isinstance(repos, list) or (isinstance(result, dict) and result.get("nextLink")):
            fail("Malformed or paginated repository collection; refusing an incomplete registration check.")
        matches = []
        for repo in repos:
            if (
                not isinstance(repo, dict)
                or not isinstance(repo.get("name"), str)
                or not isinstance(repo.get("properties"), dict)
            ):
                fail("Malformed repository entry.")
            props = repo["properties"]
            # Other repositories are opaque and are never rewritten.
            if (
                props.get("type") == "GitHub"
                and isinstance(props.get("url"), str)
                and props["url"].rstrip("/").removesuffix(".git").lower() == url
                and props.get("branch") == branch
            ):
                matches.append(repo)
        if len(matches) > 1:
            fail("Duplicate target repository/branch registrations; resolve them manually before retrying.")
        name = (
            matches[0]["name"] if matches else "github-" + hashlib.sha256(f"{url}\n{branch}".encode()).hexdigest()[:24]
        )
        if not name or len(name) > 256:
            fail("Invalid repository registration name.")
        path = "/api/v2/repos/" + quote(name, safe="")
        if not matches:
            if any(repo["name"] == name for repo in repos):
                fail("Registration name already belongs to another repository; nothing overwritten.")
            self.data_plane(
                "PUT",
                path,
                {"name": name, "type": "CodeRepo", "properties": {"url": url, "type": "GitHub", "branch": branch}},
            )

        for attempt in range(self.attempts):
            repo = self.data_plane("GET", path)
            if (
                not isinstance(repo, dict)
                or repo.get("name") != name
                or repo.get("type") != "CodeRepo"
                or not isinstance(repo.get("properties"), dict)
            ):
                fail("Malformed repository readback.")
            props = repo["properties"]
            if (
                props.get("type") != "GitHub"
                or props.get("branch") != branch
                or repository_url(props.get("url"))[0] != url
            ):
                fail("Repository readback does not match the requested repository and branch.")
            clone = props.get("cloneStatus")
            commit = props.get("latestCommit")
            if (
                clone == "Ready"
                and isinstance(commit, str)
                and re.fullmatch(r"[0-9a-f]{40,64}", commit)
                and expected_commit
                and commit == expected_commit
            ):
                pending(
                    "Exact repository/branch registration and clone at the current GitHub commit verified. "
                    "A cached Ready clone does not prove current SRE authorization or a fresh source read. "
                    "In Code Access test the connection, then ask SRE to read a known file on this exact branch "
                    "and verify its current contents. No fresh-access API response contract is pinned."
                )
            if clone == "Failed":
                error = props.get("errorMessage", "")
                if isinstance(error, str) and re.search(
                    r"authentication|unauthorized|forbidden|credentials|token.*expir|"
                    r"repository not found|branch.*not found|permission denied|could not read Username",
                    error,
                    re.IGNORECASE,
                ):
                    pending(
                        "SRE could not clone the repository/branch. Renew expired auth or correct read permissions/branch in Code Access; existing domain configuration was preserved."
                    )
                fail(
                    "SRE reports an unexpected clone failure. Inspect Code Access privately; error payload suppressed."
                )
            if clone not in (None, "Ready", "Pending", "Cloning", "NotStarted", "InProgress"):
                fail("Unknown or malformed repository clone status.")
            if commit is not None and not isinstance(commit, str):
                fail("Malformed repository clone commit.")
            if clone == "Ready" and commit and not re.fullmatch(r"[0-9a-f]{40,64}", commit):
                fail("Malformed repository clone commit.")
            if not self.wait(attempt):
                pending(
                    "Repository registered, but branch clone/commit access is not verified yet; inspect Code Access and rerun."
                )


def main() -> int:
    # Remove credentials from child environments. Headers travel on stdin; PAT JSON is private and ephemeral.
    pat = os.environ.pop("SRE_GITHUB_PAT", "")
    os.umask(0o077)

    def interrupted(_signum: int, _frame: object) -> NoReturn:
        fail("Setup interrupted; private request files cleaned up.")

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, interrupted)
    try:
        if pat and re.search(r"[\s\x00-\x1f\x7f]", pat):
            fail("SRE_GITHUB_PAT contains invalid characters; credential not sent.")
        with tempfile.TemporaryDirectory(prefix="sre-github-") as directory:
            GitHubSetup(Path(directory), pat).setup()
        fail("GitHub setup ended without an explicit verification result.")
    except SetupResultError as result:
        print(f"GitHub {result.state}: {result.message}", file=sys.stderr)
        print(RETRY_HELP, file=sys.stderr)
        print(result.state)
        return 1 if result.state == "failed" else 0
    except (OSError, ValueError, KeyError, UnicodeError):
        print(
            "GitHub failed: local I/O, configuration, or response-contract error; raw diagnostics suppressed.",
            file=sys.stderr,
        )
        print("failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
