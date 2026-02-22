Phase 2 Execution Plan — Public Search

  Files to create / modify

  ┌────────┬────────────────────────────────────┐
  │ Action │                File                │
  ├────────┼────────────────────────────────────┤
  │ CREATE │ src/living_codex/search.py         │
  ├────────┼────────────────────────────────────┤
  │ CREATE │ src/living_codex/formatter.py      │
  ├────────┼────────────────────────────────────┤
  │ CREATE │ src/living_codex/commands/codex.py │
  ├────────┼────────────────────────────────────┤
  │ MODIFY │ src/living_codex/bot.py            │
  ├────────┼────────────────────────────────────┤
  │ CREATE │ scripts/seed.py                    │
  ├────────┼────────────────────────────────────┤
  │ CREATE │ tests/test_search.py               │
  ├────────┼────────────────────────────────────┤
  │ CREATE │ tests/test_formatter.py            │
  └────────┴────────────────────────────────────┘

  Scope: medium — ~300 lines of code + ~150 lines of tests.

  ---
  File-by-file design

  1. search.py

  The search runs in two passes against the DB, merges results, deduplicates, then classifies by score:

  query
    ├─ Pass 1: score against all entity names   (rapidfuzz WRatio, case-fold both sides)
    ├─ Pass 2: score against all aliases        (same scorer)
    ├─ Merge: keep best score per entity_id     (an entity matched by both name + alias counts once)
    ├─ Classify:
    │   ≥ 70   → SearchResult(match=DIRECT, entity)
    │   40–69  → SearchResult(match=CANDIDATES, [entity…] max 5, sorted by score desc)
    │   < 40   → SearchResult(match=NONE)
    └─ Return SearchResult

  Key type — SearchResult dataclass (not Pydantic, no serialisation needed):
  @dataclass
  class SearchResult:
      kind: Literal["direct", "candidates", "none"]
      entity: EntityRow | None          # set when kind=="direct"
      candidates: list[CandidateRow]    # set when kind=="candidates"

  EntityRow and CandidateRow are TypedDicts wrapping aiosqlite rows — avoids passing raw Row objects (which
  aren't picklable and are harder to test).

  2. formatter.py

  Three pure functions — no Discord imports, fully testable with dicts:

  def build_entity_embed(entity: dict, *, is_gm: bool = False) -> discord.Embed
  def build_candidates_select(candidates: list[dict]) -> discord.ui.Select
  def build_full_detail_view(entity: dict) -> discord.ui.View   # "View Full" button

  3-Bullet Rule layout inside the embed description:
  • 🟢 Active  |  NPC
  • A ruthless baron.
  • Seen in: Armour Astir

  Status emoji map: Active→🟢, Grounded/Inactive→🔴, Dead/Destroyed→💀, everything else→⬜.

  Hard cap: the description field must be ≤ 500 chars. Any truncation gets … (truncated) appended, not just
  a raw cut.

  The is_gm flag is the Phase 4 hook — formatter already accepts it but does nothing with it in Phase 2
  (private content is never passed in at this phase).

  3. commands/codex.py — Cog pattern

  Critical structural decision: the current bot.py defines codex_group at module level and attaches /ping to
   it. Phase 2 moves all of this into a proper commands.Cog. The Cog owns the app_commands.Group, which
  solves the bot needing DB access inside command callbacks cleanly (via self.bot.codex_db).

  class CodexCommands(commands.Cog):
      def __init__(self, bot: LivingCodex): ...

      codex = app_commands.Group(name="codex", description="...")

      @codex.command(name="ping")
      async def ping(self, interaction): ...

      @codex.command(name="check")
      async def check(self, interaction, query: str): ...

  The check flow:
  1. Call search(db, query) → SearchResult
  2. kind=="direct" → build embed → await interaction.response.send_message(embed=..., ephemeral=True)
  3. kind=="candidates" → build select menu wrapped in a View → await
  interaction.response.send_message(content="Did you mean…?", view=..., ephemeral=True)
  4. kind=="none" → await interaction.response.send_message("No results found for …", ephemeral=True)

  The select menu callback fires a follow-up via await interaction.response.send_message(embed=...,
  ephemeral=True) — not followup.send (the select interaction is a fresh interaction, not a deferred one).

  4. bot.py modification

  Remove the module-level codex_group and standalone ping. Add Cog loading in setup_hook:

  async def setup_hook(self) -> None:
      await self.codex_db.connect()
      from living_codex.commands.codex import CodexCommands
      await self.add_cog(CodexCommands(self))
      guild = discord.Object(id=self.config.discord_guild_id)
      await self.tree.sync(guild=guild)

  sync() without copy_global_to is intentional — guild-scoped commands register in seconds vs 1 hour for
  global.

  5. scripts/seed.py

  Idempotent standalone script (not a test fixture). Uses asyncio.run(), loads CodexConfig, calls
  CodexDB.connect(), inserts only if entities don't already exist (INSERT OR IGNORE). Printed output
  confirms what was inserted vs skipped. Usage: python scripts/seed.py.

  ---
  Test matrix

  test_search.py — all against seeded_db fixture:

  ┌───────────────────────┬──────────────────────────────────┬───────────────┬─────────────────────┐
  │         Test          │              Input               │ Expected kind │   Expected entity   │
  ├───────────────────────┼──────────────────────────────────┼───────────────┼─────────────────────┤
  │ exact alias match     │ "Sky Pirates"                    │ direct        │ The 4th Fleet       │
  ├───────────────────────┼──────────────────────────────────┼───────────────┼─────────────────────┤
  │ fuzzy name match      │ "Vrecks"                         │ direct        │ Baron Vrax          │
  ├───────────────────────┼──────────────────────────────────┼───────────────┼─────────────────────┤
  │ noise / no match      │ "Banana"                         │ none          │ —                   │
  ├───────────────────────┼──────────────────────────────────┼───────────────┼─────────────────────┤
  │ ambiguous name prefix │ "Baron"                          │ candidates    │ [Vrax, Kora]        │
  ├───────────────────────┼──────────────────────────────────┼───────────────┼─────────────────────┤
  │ case insensitive      │ "baron vrax"                     │ direct        │ Baron Vrax          │
  ├───────────────────────┼──────────────────────────────────┼───────────────┼─────────────────────┤
  │ empty string          │ ""                               │ none          │ —                   │
  ├───────────────────────┼──────────────────────────────────┼───────────────┼─────────────────────┤
  │ deduplicated entity   │ entity matched by name AND alias │ direct (once) │ entity appears once │
  └───────────────────────┴──────────────────────────────────┴───────────────┴─────────────────────┘

  test_formatter.py — pure unit tests (no DB, no Discord client):

  ┌───────────────────────────┬────────────────────────────────────────────────────────┐
  │           Test            │                       Assertion                        │
  ├───────────────────────────┼────────────────────────────────────────────────────────┤
  │ 3-bullet structure        │ Description contains exactly 3 • bullets               │
  ├───────────────────────────┼────────────────────────────────────────────────────────┤
  │ Status emoji active       │ 🟢 present for status_label="Active"                   │
  ├───────────────────────────┼────────────────────────────────────────────────────────┤
  │ Status emoji grounded     │ 🔴 present for status_label="Grounded"                 │
  ├───────────────────────────┼────────────────────────────────────────────────────────┤
  │ Status emoji dead         │ 💀 present for status_label="Dead"                     │
  ├───────────────────────────┼────────────────────────────────────────────────────────┤
  │ 500-char hard cap         │ len(embed.description) <= 500                          │
  ├───────────────────────────┼────────────────────────────────────────────────────────┤
  │ Truncation marker         │ Long public desc → description ends with … (truncated) │
  ├───────────────────────────┼────────────────────────────────────────────────────────┤
  │ Embed title = entity name │ embed.title == "Baron Vrax"                            │
  └───────────────────────────┴────────────────────────────────────────────────────────┘

  ---
  Battle-Test — Identified Landmines

  These are the gaps I found that would cause silent failures or bugs if not addressed:

  1. rapidfuzz score scale is 0–100, not 0–1.
  The plan spec says thresholds 0.7 / 0.4. That's the 0–1 convention. rapidfuzz fuzz.WRatio and
  process.extract return integers 0–100. Use 70 / 40 in code. I'll add a comment citing the spec.

  2. bot.py has a group collision.
  The current bot.py defines codex_group = app_commands.Group(name="codex", ...) at module level and
  registers it in setup_hook. If the Cog also defines a group named "codex", Discord raises a
  CommandAlreadyRegistered error at startup. The fix: remove the module-level group from bot.py entirely,
  let the Cog own it.

  3. Select menu values must be strings ≤ 100 chars.
  Discord's SelectOption(value=...) enforces this. Entity names (e.g. "The 4th Fleet") are safe, but if we
  ever use entity IDs they must be str(id). Store the entity's id (integer → string) as the value, not the
  name, to survive renames.

  4. Ephemeral select menu + component callback interaction.
  The select callback receives a fresh discord.Interaction. Calling interaction.response.send_message() is
  correct. But the original message (with the select menu) must be edited to show the result, or the select
  will still show as "pending" to Discord. The clean approach: in the select callback, send the embed with
  interaction.response.send_message(embed=..., ephemeral=True) — Discord handles dismissing the menu
  automatically because the interaction is acknowledged.

  5. seeded_db already exists in conftest.py — seed.py must be independent.
  tests/conftest.py already has the seeded_db fixture with Baron Vrax, 4th Fleet, etc. The scripts/seed.py
  is a separate standalone script for populating the live DB (and for demo purposes). It should not import
  from conftest.py. It replicates the data insertion logic independently.

  6. formatter.py imports discord — that breaks pure unit tests.
  discord.Embed and discord.ui.View require a running Discord client state in some versions. The safe
  pattern: instantiate discord.Embed() normally (it doesn't require a client) but keep discord.ui.View
  construction deferred. Tests for formatter can import discord safely since discord.py doesn't require an
  event loop just to construct Embeds and Views — but the test file needs @pytest.mark.asyncio removed
  (formatter is sync).

  7. "Baron" → ambiguous — must return BOTH Vrax and Kora.
  fuzz.WRatio("Baron", "Baron Vrax") → ~77. fuzz.WRatio("Baron", "Baroness Kora") → ~72. Both exceed 70, so
  both would be classified as direct. The spec says this should yield a select menu (candidates). The logic
  must be: if more than one entity scores ≥ 70, downgrade to candidates. Add this rule explicitly to
  search.py.

  8. The "View Full" button requires a second interaction response.
  The spec says: "View Full" → expanded detail (ephemeral follow-up). In discord.py View, a Button callback
  receives its own Interaction. await interaction.response.send_message(embed=full_embed, ephemeral=True)
  works. The view timeout should be set (e.g., 300 seconds) — timeout=None is reserved for the Phase 4
  Mission Reports that need to survive restarts.

  ---
  Execution order

  Step 1: search.py              (pure async, testable without Discord)
  Step 2: test_search.py         (verify thresholds + dedup rule #7 above)
           → MILESTONE CHECKPOINT before continuing
  Step 3: formatter.py           (pure sync, testable without Discord)
  Step 4: test_formatter.py      (verify 3-bullet structure, caps, emojis)
           → MILESTONE CHECKPOINT
  Step 5: commands/codex.py      (Cog + check command + select menu View)
  Step 6: bot.py modification    (remove module-level group, load Cog)
  Step 7: scripts/seed.py        (standalone data loader)
  Step 8: Manual smoke test      (7 verify cases from plan spec)

  ---
  Ready to proceed? The most critical call-out before I write any code: do you want to proceed with the Cog
  refactor of bot.py, or would you prefer to keep the current module-level group structure and instead build
   check as a new command on the existing group? The Cog pattern is cleaner long-term but it's a real change
   to Phase 1's bot.py.