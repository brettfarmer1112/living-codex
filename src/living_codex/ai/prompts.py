"""Prompt templates for Gemini transcription and Claude entity extraction.

Persona and rules are handled entirely by the system prompt (codex_rules.md).
These templates contain only task-specific instructions and data — no "you are" framing.
"""

# ---------------------------------------------------------------------------
# Gemini: audio transcription
# ---------------------------------------------------------------------------

TRANSCRIBE_SINGLE = """\
You are transcribing an RPG podcast recording.
Identify and label speakers when possible (e.g., "GM:", "Player:").
Output a clean, verbatim transcript with timestamps every 5 minutes.
Format: [HH:MM] Speaker: text
"""

TRANSCRIBE_SPEAKER = """\
You are transcribing audio from one speaker: {speaker_name}.
Output verbatim text. Do not add timestamps or speaker labels.
"""

# ---------------------------------------------------------------------------
# Claude: entity extraction
# ---------------------------------------------------------------------------

EXTRACT_ENTITIES = """\
Campaign: {campaign_name}

Known player characters (tag as type "PC", never "NPC"):
{known_pcs}

Extract all named entities from the transcript below that are narratively significant.
Return a JSON array. Each object must have:

  - name: string (canonical name)
  - type: one of ["NPC", "PC", "Faction", "Location", "Asset", "Clue"]
  - aliases: array of strings (nicknames, alternate names; empty array if none)
  - public_description: string (3–6 sentences — what players can know; lore, appearance, role)
  - private_description: string (GM secrets, unrevealed lore; empty string if none)
  - motivation: string (what this entity wants; most useful for NPCs; empty string if not applicable)
  - appearance: string (physical description; useful for NPCs and Locations; empty string if not applicable)
  - status_label: one of ["Active", "Inactive", "Dead", "Destroyed", "Unknown"]
  - first_appearance: string (transcript timestamp where entity is first mentioned, e.g. "[01:17]"; empty string if unclear)
  - relationships: array of objects, each with keys: target_name, rel_type, citation
  - events: array of objects describing things that happened to/by this entity in this session:
      - timestamp: transcript timestamp e.g. "[01:17]" (empty string if unclear)
      - description: concise one-sentence description of the event
      - visibility: "public" or "private"

Redact real names, emails, or personal data — replace with [REDACTED].
Return ONLY valid JSON. No markdown fences. No explanation text.

TRANSCRIPT:
{transcript}
"""

# ---------------------------------------------------------------------------
# Claude: session summary generation
# ---------------------------------------------------------------------------

SUMMARIZE_SESSION = """\
Campaign: {campaign_name} — Session {session_number}

Write a narrative summary of this session based on the transcript below.

Structure:
- Opening paragraph: set the scene, recap where things stood at start
- Bullet list of key events (what happened, who was involved)
- Prose paragraphs for the 2–3 most dramatic or narratively significant moments
- Closing paragraph: cliffhangers, unresolved threads, what's at stake next session

Style:
- Present tense throughout
- Refer to player characters by their character names
- Specific: name the people, places, and decisions
- Keep private GM information out — write only what the players experienced

TRANSCRIPT:
{transcript}
"""

# ---------------------------------------------------------------------------
# Claude: natural language query against all transcripts
# ---------------------------------------------------------------------------

QUERY_CODEX = """\
Campaign: {campaign_name}

Answer the following question using ALL the context sections provided below.
Draw on entity profiles, relationships, session summaries, lore documents, AND transcripts.
Cite your sources in the format "Session N, [HH:MM]" when referencing specific transcript moments.
If the information isn't in any of the provided context, say so directly.

QUESTION: {question}

ENTITY PROFILES:
{entities}

RELATIONSHIP MAP:
{relationships}

SESSION SUMMARIES:
{summaries}

LORE DOCUMENTS:
{lore_docs}

SESSION TRANSCRIPTS:
{transcripts}
"""
