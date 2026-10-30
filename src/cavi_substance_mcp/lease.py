"""Application-local edit ownership shared by independent MCP clients."""

import secrets
import threading
import time
from contextlib import contextmanager


class Lease:
    def __init__(self):
        self.lock = threading.RLock()
        self.token = None
        self.owner = None
        self.project = None
        self.expires = 0.0

    def status(self, project):
        return {
            "active": self.token is not None and time.monotonic() < self.expires,
            "owner": self.owner,
            "project_id": project,
            "claimed_project_id": self.project,
            "expires_in": max(0.0, self.expires - time.monotonic()),
        }

    def require(self, token, project):
        if (
            not isinstance(token, str)
            or self.token is None
            or not secrets.compare_digest(token, self.token)
        ):
            raise RuntimeError("A valid lease_id is required; inspect and claim the session")
        if time.monotonic() >= self.expires:
            raise RuntimeError("The edit lease expired; inspect and claim the session again")
        if project != self.project:
            raise RuntimeError("The project changed; release the lease and inspect before claiming")

    def control(self, params, project):
        with self.lock:
            project = project() if callable(project) else project
            action = params.get("action")
            if action == "inspect":
                return self.status(project)
            if action == "release":
                token = params.get("lease_id")
                if (
                    not isinstance(token, str)
                    or self.token is None
                    or not secrets.compare_digest(token, self.token)
                ):
                    raise RuntimeError("A matching lease_id is required to release ownership")
                self.token, self.owner, self.project, self.expires = None, None, None, 0.0
                return self.status(project)
            if action not in {"claim", "renew"}:
                raise ValueError("Unknown session action")
            ttl = params.get("ttl_seconds", 600)
            if type(ttl) is not int or not 30 <= ttl <= 3600:
                raise ValueError("Lease duration must be an integer from 30 to 3600 seconds")
            if action == "renew":
                self.require(params.get("lease_id"), project)
            else:
                owner = params.get("owner")
                if not isinstance(owner, str) or not owner.strip() or len(owner) > 128:
                    raise ValueError("Owner must be a nonempty label of at most 128 characters")
                if params.get("expected_project") != project:
                    raise RuntimeError("The inspected project changed; inspect the session again")
                if self.token is not None and time.monotonic() < self.expires:
                    raise RuntimeError(f"Application is owned by {self.owner}; wait for release")
                self.token, self.owner, self.project = secrets.token_urlsafe(32), owner, project
            self.expires = time.monotonic() + ttl
            result = self.status(project)
            if action == "claim":
                result["lease_id"] = self.token
            return result

    @contextmanager
    def edit(self, token, identity):
        with self.lock:
            self.require(token, identity())
            yield
            # Owned saves and project transitions may change the project identity.
            self.project = identity()
