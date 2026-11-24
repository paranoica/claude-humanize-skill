# Building your voice profile

A profile is a folder of numbers and real examples taken from your own chats. The skill reads it to copy how you write. This page goes from a chat export to an installed profile.

You need Python 3.12+. Nothing to install: the pipeline uses the standard library only.

**1. Get your chats.** Any mix of:

- Telegram Desktop: Settings → Advanced → Export Telegram data, format JSON, media off. You get `result.json`.
- Slack: a workspace export zip. Standard exports hold public channels only; DMs need an owner export on a paid plan.
- Anything else: a `.jsonl` file with one `{"text": ..., "is_owner": true}` per line, or `.txt`/`.md` files with your own writing.

No export at hand? If Claude Code has a Telegram or Slack MCP server, ask Claude to dump the chats. Claude Code saves large MCP results to files, and `python3 -m pipeline.ingest_mcp telegram --manifest dump.json` reads them byte for byte; for Slack use `python3 -m pipeline.ingest_mcp slack --manifest ... --owner U123`. Do not let the model retype messages: punctuation and letter case are exactly what the profile measures.

**2. Convert them.** Everything lands in `data/`, which git ignores.

```bash
python3 -m pipeline.ingest_telegram ~/Downloads/Telegram/result.json
python3 -m pipeline.ingest_slack slack-export.zip --owner "Your Name"
python3 -m pipeline.ingest_text notes/*.md --owner-name "You"
```

**3. See what you have.** `python3 -m pipeline.stats chats` lists every chat with how many messages are yours and in which language.

**4. Choose registers.** People write differently to friends, colleagues and clients, so a profile has several registers. Create `profiles/<you>/corpus.toml`; each register says which chats it covers, a turn goes to the first register that matches, and chats that match nothing are left out. See [profiles/example/corpus.toml](../profiles/example/corpus.toml):

```toml
[exclude]
chats = ["Mom"]

[[register]]
name = "clients"
lang = "en"
description = "Clients, in English."
[register.match]
chats = ["Anna Becker"]

[[register]]
name = "friends"
lang = "ru"
[register.match]
chat_type = ["private"]
```

**5. Build.** `python3 -m pipeline.build_profile --profile profiles/<you>` writes, for each register:

- `facts.md`: your numbers as plain rules, in the register's language;
- `examples.md`: 30 to 50 real "incoming → your reply" pairs, with links, emails, phone numbers, keys and passwords masked;
- `pairs.jsonl`: up to 600 pairs that `similar.py` searches for replies to messages like the one being answered;
- an empty `guide.md`.

It also sets aside 20 pairs in `holdout.jsonl` for the blind test. `--reset-holdout` moves them to `holdout-history.jsonl`, and those are never held out again, so a new test never repeats what you already saw.

**6. Adjust.** Edit `profiles/<you>/settings.toml`: default platform, stop phrases, whether "ё", dashes, emoji or English contractions are allowed (`auto` decides from your stats). Fill each `guide.md` in your own words, or ask Claude to draft it from `facts.md` and `examples.md` and fix what it got wrong.

**7. Install** with `./install.sh --profile <you>` or `./package.sh --profile profiles/<you>`. After new exports, rebuild with step 5; your `guide.md` and the holdout are kept.

To try it on made-up data first: `examples/telegram/result.json` is a fake export of "Misha", who writes to friends in lowercase bursts and to a client in plain English.

```bash
python3 -m pipeline.ingest_telegram examples/telegram/result.json -o data/example/telegram.jsonl
python3 -m pipeline.build_profile --profile profiles/example --corpus data/example --bank-size 30 --holdout 9
echo "Привет! Да, конечно, скину вечером." | python3 skill/scripts/lint.py --profile profiles/example --register friends
```

The last command flags the commas, the capital letter and the final period: Misha would write "вечером скину".

## Blind test: does it sound like you?

```bash
python3 -m bench.run --profile profiles/<you> --label v1
python3 -m bench.report data/bench/v1 --profile profiles/<you>
```

Each run uses a throwaway HOME with only the skill, your profile (without the holdout) and the hook. The report lints every reply against your numbers and records whether the hook fired, the skill loaded and lint ran. `blind.html` is the page where you pick the real reply. Results stay in `data/bench/`.

## Where things are

| Path | What it is |
|---|---|
| `skill/SKILL.md` | the skill: pick a register, read its files, write, lint, output |
| `skill/references/` | anti-slop rules (RU, EN), platform formatting, facts discipline, default voice |
| `skill/scripts/lint.py` | the draft check; `textmetrics.py` and `profilelib.py` are shared with the pipeline |
| `skill/scripts/similar.py` | your real replies to messages like the one being answered |
| `pipeline/` | ingest (exports and MCP dumps), stats, profile build, packaging |
| `bench/` | the blind-test runner and report |
| `hooks/draft_trigger.py` | the Claude Code prompt hook |
| `profiles/example/` | the example profile; your own profiles are git-ignored |
