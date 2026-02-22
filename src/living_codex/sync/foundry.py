"""Async HTTP client for the foundryvtt-rest-api module (by Ethck6).

Authentication: Authorization: {api_key} header on every request.
Retry strategy: connection errors → 3 attempts with 2s/4s backoff, then FoundryOfflineError.
5xx responses: raise FoundryOfflineError immediately (server-side, don't retry).
Folder API: degrades gracefully to None if the module doesn't support it.
"""

import asyncio
import hashlib
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class FoundryOfflineError(Exception):
    """Foundry is unreachable (connection failure or 5xx).

    Safe to enqueue for automatic retry.
    """


class FoundryConflictAbort(Exception):
    """Raised when the live Foundry content hash does not match the stored hash.

    Means a human edited the entry after the last sync — requires GM force-override.
    """


class FoundryClient:
    """Async REST client for Foundry VTT via foundryvtt-rest-api."""

    _RETRY_DELAYS = (2.0, 4.0)  # seconds between retries (3 total attempts)

    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            headers={"Authorization": api_key},
            timeout=timeout,
        )

    # ------------------------------------------------------------------
    # Low-level request helper with retry + error classification
    # ------------------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(len(self._RETRY_DELAYS) + 1):
            try:
                resp = await self._client.request(method, url, **kwargs)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt < len(self._RETRY_DELAYS):
                    delay = self._RETRY_DELAYS[attempt]
                    logger.warning(
                        "Foundry connection error (attempt %d): %s — retrying in %.0fs",
                        attempt + 1, exc, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise FoundryOfflineError(f"Foundry unreachable after retries: {exc}") from exc

            if resp.status_code >= 500:
                raise FoundryOfflineError(
                    f"Foundry server error {resp.status_code}: {resp.text[:200]}"
                )
            resp.raise_for_status()
            return resp.json()

        # Should not reach here, but satisfy type checker
        raise FoundryOfflineError(f"Foundry unreachable: {last_exc}")

    # ------------------------------------------------------------------
    # Journal API
    # ------------------------------------------------------------------

    async def list_journals(self) -> list[dict]:
        """Return all journal entries visible to the API key."""
        data = await self._request("GET", "/api/journal")
        return data if isinstance(data, list) else data.get("journals", [])

    async def get_journal(self, journal_id: str) -> dict:
        """Fetch a single journal entry by ID."""
        return await self._request("GET", f"/api/journal/{journal_id}")

    async def create_journal(
        self, name: str, content: str, folder_id: str | None = None
    ) -> dict:
        """Create a new journal entry. Returns the created journal dict."""
        body: dict = {"name": name, "content": content}
        if folder_id:
            body["folder"] = folder_id
        return await self._request("POST", "/api/journal", json=body)

    async def update_journal(self, journal_id: str, content: str) -> dict:
        """Update the content of an existing journal entry."""
        return await self._request(
            "PUT", f"/api/journal/{journal_id}", json={"content": content}
        )

    # ------------------------------------------------------------------
    # Folder API (graceful degradation)
    # ------------------------------------------------------------------

    async def get_or_create_folder(
        self, folder_name: str, parent_folder_id: str | None = None
    ) -> str | None:
        """Return the folder ID for *folder_name*, creating it if needed.

        Returns None if the REST API module doesn't support folder operations
        (older versions).  Entries will be created top-level in that case.
        """
        try:
            folders = await self._request("GET", "/api/folders")
            folder_list = folders if isinstance(folders, list) else folders.get("folders", [])

            for f in folder_list:
                if (
                    f.get("name") == folder_name
                    and f.get("type") in ("JournalEntry", "journal")
                    and (parent_folder_id is None or f.get("folder") == parent_folder_id)
                ):
                    return f["_id"]

            # Create it
            body: dict = {"name": folder_name, "type": "JournalEntry"}
            if parent_folder_id:
                body["folder"] = parent_folder_id
            result = await self._request("POST", "/api/folders", json=body)
            return result.get("_id")

        except (httpx.HTTPStatusError, KeyError) as exc:
            logger.warning("Foundry folder API unavailable (%s) — placing entries top-level.", exc)
            return None

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def hash_content(content: str) -> str:
        """Return a SHA-256 hex digest of *content* for change detection."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def close(self) -> None:
        await self._client.aclose()
