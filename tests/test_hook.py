import json
import subprocess
from pathlib import Path

import pytest

from hooks import draft_trigger

HOOKS = Path(__file__).resolve().parent.parent / "hooks"

# The acceptance set from the spec: 10 draft requests, 10 unrelated prompts.
DRAFT_REQUESTS = [
    "напиши ей что я опоздаю на полчаса",
    "ответь ему: да, давай завтра",
    "что ответить на это?\n\nПривет, когда будет готово?",
    "набросай черновик ответа клиенту про сроки",
    "сделай драфт письма в поддержку",
    "draft a reply to Sam about the deploy",
    "write back to her that I'll be late",
    "what should I say to this",
    "напиши Пете что созвон переносится",
    "humanize this: We are pleased to inform you that the release is live.",
]
UNRELATED = [
    "напиши тест для функции parse",
    "напиши функцию сортировки на python",
    "почему падает этот тест",
    "ответь на вопрос: что такое GIL",
    "draft PR for this branch",
    "создай draft release на гитхабе",
    "напиши сообщение об ошибке для валидации email",
    "what does this reply handler do",
    "как работает UserPromptSubmit хук",
    "напиши ответ API в формате json",
]
MORE_DRAFTS = [
    "как ей лучше ответить",
    "reply to this message politely",
    "убери ИИ-стиль из текста",
    "переделай по-человечески",
    "накидай сообщение в слак про релиз",
    "черкни Маше что я на месте",
    "respond to this email",
    "write a short message to the team",
    "сформулируй письмо арендодателю",
    "Напиши ему коротко, что всё ок",
]
MORE_UNRELATED = [
    "напиши Python скрипт для бэкапа",
    "почини парсер ответа сервера",
    "write a function to reply to webhooks",
    "напиши сообщение для коммита",
    "напиши постгрес миграцию",
    "сохрани черновик в базе",
    "draft-07 json schema validation",
    "что делает функция answer()",
    "ответь коротко: сколько будет 2+2",
    "напиши имя функции нормально",
    "напиши импорт для модуля",
    "",
]
REVIEW_UNRELATED = [
    "напиши обработчик сообщений для телеграм бота",
    "напиши очередь сообщений на redis",
    "напиши функцию отправки письма через SMTP",
    "напиши бота в телеграм",
    "напиши пост запрос на fastapi",
    "напиши отправку в канал на Go",
    "напиши ответ в формате JSON",
    "напиши парсер сообщений из логов",
    "напиши сообщение для логов при ошибке",
    "назови переменные по-человечески",
    "как ответить 404 из хендлера express",
    "напиши ему тесты",
    "how do I write to Postgres from Go",
    "write to Kafka topic",
    "Reply to Slack events with a 200 within 3 seconds",
    "implement write back caching",
    "use humanize to format file sizes",
    "make the bot reply to this command",
    "the handler should reply to them with 200",
    "what should I write in the README",
    "write a message queue consumer",
    "draft a message schema",
    "сохрани черновик сообщения в бд",
    "перепиши этот код по-человечески",
    "write a reply function for the chat bot",
    "how do I reply to a message in discord.py",
    "напиши сообщение коммита коротко",
    "ответь на вопрос из задачи",
    "что ответить пользователю при ошибке 500 в API",
    "напиши ей конфиг для nginx",
    "draft an email template in jinja",
    "how should I answer this interview question about Big O",
    "напиши текст ошибки для формы",
    "reply to this PR comment with a fix in code",
]
REVIEW_DRAFTS = [
    "Напиши Маше что я опоздаю",
    "Ответь Пете что всё ок",
    "Reply to John and say I'm late",
    "Write to Anna that the meeting moved",
    "Отпиши Ване, что буду в 5",
    "напиши коллеге что я заболел",
    "Помоги ответить клиенту",
    "Answer this email from my landlord",
    "Help me answer my boss, he wants the report today",
    "How should I respond to my manager?",
    "скинь текст что ответить Диме",
    "напиши смс маме что задержусь",
    "как вежливо отказать клиенту",
    "reply to my landlord saying no",
    "can you write an email to my professor",
    "напиши Ольге Петровне что документы готовы",
    "write back to Tom",
    "набросай пост в канал про релиз",
    "Ответь от моего имени в Telegram:\n\nпривет как ты",
    "напиши от моего имени в слак что я заболел",
    "Reply as me on Slack:\n\nany update?",
    "answer this as me",
    "Write to the team in Slack that staging is down tonight",
    "write to everyone in the channel that the demo moved",
    "tell the team that I am out today",
]


@pytest.mark.parametrize("prompt", DRAFT_REQUESTS + MORE_DRAFTS + REVIEW_DRAFTS)
def test_draft_requests_trigger(prompt: str) -> None:
    assert draft_trigger.wants_message(prompt)


@pytest.mark.parametrize("prompt", UNRELATED + MORE_UNRELATED + REVIEW_UNRELATED)
def test_unrelated_prompts_do_not(prompt: str) -> None:
    assert not draft_trigger.wants_message(prompt)


def _run(stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(HOOKS / "draft_trigger.sh")], input=stdin, capture_output=True, text=True
    )


def test_wrapper_emits_context() -> None:
    result = _run(json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "ответь ему"}))
    assert result.returncode == 0
    out = json.loads(result.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "UserPromptSubmit"
    assert "humanize" in out["additionalContext"]


@pytest.mark.parametrize(
    "stdin", [json.dumps({"prompt": "почему падает тест"}), "not json", "[]", json.dumps({})]
)
def test_wrapper_silent(stdin: str) -> None:
    result = _run(stdin)
    assert result.returncode == 0
    assert result.stdout == ""


def test_wrapper_never_blocks_when_script_missing(tmp_path: Path) -> None:
    wrapper = tmp_path / "draft_trigger.sh"
    wrapper.write_text((HOOKS / "draft_trigger.sh").read_text())
    result = subprocess.run(["bash", str(wrapper)], input="{}", capture_output=True, text=True)
    assert result.returncode == 0
