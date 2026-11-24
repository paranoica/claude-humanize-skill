# claude-humanize-skill

Makes Claude write your messages the way you would write them, not the way an AI does.

Ask Claude to reply to someone, and it usually sends back something polite, long and obviously generated. This skill fixes that. Out of the box it writes plain human text: short, no dashes, no "I hope this finds you well", no markdown in a chat. Feed it your own chat history and it learns your style: how long your messages are, whether you use capitals and commas, your usual words, how you split one thought into several messages.

A skill is a folder of instructions and scripts that Claude loads when it needs them. This one also checks every draft with a script (`lint.py`) before showing it to you.

## Example

> Reply to my manager on Slack, she wrote: "Can you take the onboarding call with the new client tomorrow at 10?" I can, but I have to leave at 11.

```
yes I can take it
heads up though, I have to leave at 11 so I'll drop off then if it runs long
```

That is a real answer from the skill without a profile. If the reply is several messages, each comes in its own block so you can send them one by one.

## Install

**Claude Code**

```bash
git clone https://github.com/paranoica/claude-humanize-skill.git
cd claude-humanize-skill
./install.sh
```

Restart Claude Code. Then just ask: "reply to Anna, she wrote: ...", "что ответить на это", "draft a message to the team". A small hook spots these requests and makes Claude use the skill. `./install.sh --uninstall` removes everything.

**claude.ai**

1. Run `./package.sh`. It makes `dist/humanize.zip`.
2. Upload the zip in Settings → Capabilities → Skills. Code execution must be on.
3. Paste the line from [claude-ai-instructions.md](claude-ai-instructions.md) into Settings → Your Claude → Instructions.
4. Start a new chat.

## Make it sound like you

You need Python 3.12+ and an export of your chats (Telegram, Slack or plain text files). Nothing else to install.

```bash
python3 -m pipeline.ingest_telegram ~/Downloads/Telegram/result.json   # 1. import your chats
python3 -m pipeline.stats chats                                        # 2. see what you have
# 3. pick chats for each style in profiles/<you>/corpus.toml
python3 -m pipeline.build_profile --profile profiles/<you>             # 4. build the profile
./install.sh --profile <you>                                           # 5. install it
```

Step by step, with Slack, the config format and a blind test: [docs/profile.md](docs/profile.md).

## Does it work?

The author built a profile from their own chats, then looked at 20 pairs of replies, one real and one written by the skill, and tried to pick the real one. Random guessing gives 10 of 20. The first version was easy to spot: 17 of 20 right. After tuning, the author got 9 of 20, no better than a coin toss.

## Privacy

Your chats and your profile stay on your machine. They live in `data/` and `profiles/<you>/`, which git ignores, and a pre-commit hook blocks them even if you add them by hand. The claude.ai zip contains your profile, so do not share it.

## Development

```bash
uv run --no-project --with pytest pytest
```
