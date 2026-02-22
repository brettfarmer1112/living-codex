"""Tests for the lore_docs table and helpers."""

import pytest

from living_codex.database import CodexDB, EXPECTED_TABLES, EXPECTED_INDEXES


@pytest.mark.asyncio
async def test_lore_docs_table_exists(db: CodexDB):
    """lore_docs table should exist after schema init."""
    cursor = await db.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='lore_docs'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert "lore_docs" in EXPECTED_TABLES
    assert "idx_lore_docs_campaign_id" in EXPECTED_INDEXES


@pytest.mark.asyncio
async def test_insert_and_retrieve_lore_doc(db: CodexDB):
    """insert_lore_doc stores a doc; get_all_lore_docs retrieves it."""
    cid = await db.get_or_create_campaign("Test Campaign")
    doc_id = await db.insert_lore_doc(cid, "The Old Gods", "Lore about the old gods.")
    assert isinstance(doc_id, int)

    docs = await db.get_all_lore_docs(cid)
    assert len(docs) == 1
    assert docs[0]["title"] == "The Old Gods"
    assert docs[0]["content"] == "Lore about the old gods."
    assert docs[0]["source"] == "discord_upload"


@pytest.mark.asyncio
async def test_lore_docs_scoped_to_campaign(db: CodexDB):
    """get_all_lore_docs only returns docs for the specified campaign."""
    cid_a = await db.get_or_create_campaign("Campaign A")
    cid_b = await db.get_or_create_campaign("Campaign B")

    await db.insert_lore_doc(cid_a, "Doc A", "Content A")
    await db.insert_lore_doc(cid_b, "Doc B", "Content B")

    docs_a = await db.get_all_lore_docs(cid_a)
    docs_b = await db.get_all_lore_docs(cid_b)

    assert len(docs_a) == 1
    assert docs_a[0]["title"] == "Doc A"
    assert len(docs_b) == 1
    assert docs_b[0]["title"] == "Doc B"


@pytest.mark.asyncio
async def test_get_all_lore_docs_empty(db: CodexDB):
    """get_all_lore_docs returns empty list when no docs exist."""
    cid = await db.get_or_create_campaign("Empty Campaign")
    docs = await db.get_all_lore_docs(cid)
    assert docs == []
