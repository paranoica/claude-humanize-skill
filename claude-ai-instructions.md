# Line for claude.ai Instructions

claude.ai has no hooks, so this line in Settings → Your Claude → Instructions makes Claude
load the skill. Add it at the end; it does not conflict with other style rules, because
the skill itself says it takes priority for messages written as you.

Russian:

```
Когда я прошу написать, ответить кому-то или сделать черновик от моего имени, всегда используй скилл humanize и его lint. Готовый текст отдавай мне в чат. Отправлять через коннекторы (Telegram, Slack, почта) только когда я прямо скажу «отправь».
```

English:

```
When I ask you to write, reply to someone or draft a message as me, always use the humanize skill and its lint. Give me the finished text in the chat. Send it through a connector (Telegram, Slack, email) only when I explicitly say "send".
```
