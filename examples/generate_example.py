"""Generates the fictional Telegram export used by the README walkthrough and the e2e test.

Everyone here is made up. Misha is a backend developer who writes to friends in short
lowercase bursts, to his team a bit more technically, and to a client in plain English.

Usage:
    python3 examples/generate_example.py   # writes examples/telegram/result.json
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OWNER_ID = 5550001
START = 1767261600  # 2026-01-01 10:00 UTC

FRIENDS = [
    ("го вечером в бар?", ["го", "только после 8 освобожусь"]),
    ("ты где пропал", ["да работа завалила", "на выходных отпишусь"]),
    ("смотрел новый сезон?", ["неа еще", "не спойлери"]),
    ("скинь фотки с субботы", ["щас", "вечером скину все норм"]),
    ("как ты вообще", ["норм", "устал немного но жив)"]),
    ("в субботу на дачу едем?", ["я за", "во сколько выезжаем"]),
    ("у тебя есть зарядка type-c?", ["есть", "занесу завтра"]),
    ("погнали в кино в пятницу", ["на что", "если не ужастик то го"]),
    ("помнишь как звали того бармена?", ["вроде дима", "но не уверен"]),
    ("ты на машине завтра?", ["да", "могу подкинуть если по пути"]),
    ("пиццу или суши?", ["пиццу", "суши в прошлый раз брали"]),
    ("ты видел что костя написал", ["видел)", "он как всегда"]),
    ("с днем рождения!!", ["спасибо)", "вечером отмечаем приходи"]),
    ("занят?", ["немного", "через полчаса наберу"]),
    ("кинь номер мастера по ремонту", ["щас найду", "<номер>"]),
    ("что подарить лене?", ["хз", "может сертификат какой нибудь"]),
    ("ты в зал сегодня?", ["сегодня нет", "завтра утром пойду"]),
    ("как съездил?", ["отлично", "море теплое еда вкусная", "фотки потом скину"]),
    ("слушай одолжи 2к до пятницы", ["ок", "скинул"]),
    ("ты где? мы уже тут", ["бегу", "5 минут"]),
    ("смотрел матч?", ["только второй тайм", "судья конечно жесть"]),
    ("пойдешь на концерт в марте?", ["если билеты еще есть то да"]),
    ("у тебя же был велик на продажу", ["был", "уже продал прости"]),
    ("напомни адрес", ["садовая 12", "домофон 34"]),
    ("как там кот", ["спит", "как обычно)"]),
    ("ты не против если я приду с аней", ["конечно нет", "приходите"]),
    ("какой фильм посоветуешь", ["посмотри прибытие", "если не видел"]),
    ("ну что как собеседование", ["вроде норм прошло", "жду ответа до пятницы"]),
    ("ты играешь сегодня?", ["в 10 зайду", "если не вырублюсь"]),
    ("слушай а ты не знаешь хорошего стоматолога", ["знаю", "щас контакт скину"]),
    ("скучно", ["приезжай", "чай есть"]),
    ("встретимся на неделе?", ["давай в четверг", "после работы"]),
    ("ты обиделся что ли", ["не", "просто занят был"]),
    ("куда поедем летом", ["я бы в грузию", "там вкусно и недорого"]),
    ("забыл у тебя куртку", ["видел", "привезу в субботу"]),
    ("как настроение", ["рабочее", "вечером будет лучше)"]),
    ("ну ты где там", ["в пробке", "минут 15 еще"]),
    ("смотри какой мем", ["ахах", "точно про нас"]),
    ("брат выручай нужна дрель", ["бери", "она в кладовке у родителей"]),
    ("ты спишь?", ["почти", "что случилось"]),
    ("в воскресенье футбол в 11", ["буду", "мяч возьму"]),
    ("напиши как доедешь", ["доехал", "все ок"]),
    ("что делаешь на новый год", ["пока не решили", "наверное у родителей"]),
    ("можно я у тебя переночую в пятницу", ["конечно", "только диван не раскладывается"]),
]

WORK = [
    ("кто может глянуть PR по оплатам?", ["я гляну", "после обеда"]),
    ("прод упал?", ["не", "это стейдж лежит", "уже поднимаю"]),
    ("миш ты задеплоил фикс?", ["еще нет", "тесты падают на миграции"]),
    ("что с задачей по уведомлениям", ["почти готово", "осталось ретраи доделать"]),
    ("созвон в 12 актуален?", ["да", "ссылку в календаре обновил"]),
    ("у кого доступ к логам кибаны", ["у меня", "что нужно найти"]),
    ("миграция на постгрес 16 когда", ["на следующей неделе", "сначала прогоню на стейдже"]),
    ("почему билд красный", ["линтер ругается", "щас поправлю"]),
    ("можешь ревьюнуть мой мр", ["кинь ссылку", "гляну до вечера"]),
    ("клиент жалуется на медленный поиск", ["видел", "там индекса нет на created_at", "добавлю"]),
    ("кто дежурит на выходных", ["я", "если что звоните"]),
    ("redis опять память жрет", ["ключи без ttl", "я завел задачу"]),
    ("оцени задачу по экспорту", ["дня 3", "если без xlsx то 2"]),
    ("ты в офисе сегодня?", ["нет", "из дома работаю"]),
    ("кто сломал сваггер", ["вроде я", "щас откачу"]),
    ("тесты флапают на ci", ["это тот тест с таймаутом", "я его на ретрай повесил пока"]),
    ("нужен доступ в sentry новому парню", ["скинь почту", "добавлю"]),
    ("задеплоили?", ["да", "в 15:40 выкатил"]),
    ("какая версия апи сейчас на проде", ["2.14", "2.15 завтра поедет"]),
    (
        "можешь объяснить как работает очередь вебхуков",
        [
            "там воркер забирает пачками по 50",
            "если ответ не 200 то ретрай с бэкоффом",
            "после 5 попыток в dead letter",
        ],
    ),
    ("ретро в пятницу переносим?", ["мне норм", "давай на понедельник"]),
    ("баг с дублями заказов воспроизвелся", ["ого", "кинь trace id", "гляну"]),
    ("кто ответит саппорту по api ключам", ["я отвечу"]),
    ("на стейдже старая схема бд", ["забыл миграцию прогнать", "щас"]),
    ("плиз апрувни", ["апрувнул"]),
    ("ты брал задачу по кэшу?", ["брал", "в процессе"]),
    ("сколько rps выдержит новый эндпоинт", ["на нагрузке было 800", "дальше упирается в бд"]),
    ("где документация по деплою", ["в вики раздел infra", "но она устарела немного"]),
    ("мы будем обновлять django?", ["да", "после релиза", "сейчас рискованно"]),
    ("у меня не поднимается докер компоуз", ["обнови env из примера", "там новый ключ добавился"]),
    ("кто последний трогал крон с отчетами", ["я", "что с ним"]),
    ("отчеты не пришли утром", ["понял", "крон упал по таймауту", "перезапустил вручную"]),
    ("сможешь завтра провести демо", ["могу", "только скиньте что показываем"]),
    ("что думаешь про переход на uv", ["я за", "у себя уже пользуюсь быстро"]),
    ("в логах куча 499", ["это клиенты отваливаются по таймауту", "надо нгинкс подкрутить"]),
    ("доброе утро! кто сегодня на релизе", ["доброе", "я"]),
    (
        "слушай а зачем тут транзакция",
        ["чтобы списание и запись в историю были вместе", "иначе бывали дыры"],
    ),
    ("мерджить можно?", ["можно", "только сквошни"]),
    ("кто знает пароль от тестового стенда", ["в 1password", "папка staging"]),
    ("тикет по безопасности от аудиторов", ["видел", "в четверг возьму"]),
    ("ну что там с хотфиксом", ["готов", "жду ревью"]),
    ("кофе?", ["го", "через 5 минут"]),
    ("бэкапы проверял?", ["вчера", "восстановление прошло за 12 минут"]),
]

CLIENT = [
    (
        "Hi Misha, any update on the export feature?",
        ["Hi Anna, it is almost done.", "I'll send a test link on Thursday."],
    ),
    (
        "Is the site down? I can't log in.",
        ["Checking now.", "Looks like the auth service restarted, should be fine in a minute."],
    ),
    (
        "Can we add a CSV option to the report?",
        ["Sure, that's a small change.", "I'll add it to next week's release."],
    ),
    (
        "How long would the mobile version take?",
        ["Hard to say without the designs.", "Roughly three to four weeks for the main screens."],
    ),
    ("Thanks for the quick fix!", ["No problem, glad it works."]),
    ("Could you send the invoice for March?", ["Yes, I'll send it today."]),
    (
        "We're seeing duplicate emails to customers.",
        ["Thanks for flagging.", "I found the cause, it was a retry bug. Fix goes out tonight."],
    ),
    (
        "Is it possible to change the logo on the login page?",
        ["Yes. Just send me the file in SVG if you have it."],
    ),
    (
        "Do you have time for a call tomorrow?",
        ["Tomorrow after 2pm works for me.", "Send me a slot that suits you."],
    ),
    (
        "The search is really slow today.",
        ["I see it too.", "Adding an index now, it should be faster in an hour."],
    ),
    (
        "What do we need to move to the new server?",
        ["Mostly just a maintenance window of about an hour.", "I'd do it on a Sunday morning."],
    ),
    (
        "Can users reset their password themselves?",
        ["Yes, there's a link on the login page.", "It sends an email with a code."],
    ),
    (
        "Did you get my email about the pricing page?",
        ["Got it, working on it.", "I'll show you a draft on Friday."],
    ),
    (
        "Our accountant asks for the API documentation.",
        ["Sure, here it is: <link>", "Let me know if anything is unclear."],
    ),
    ("Is our data backed up?", ["Yes, every night.", "We also tested a full restore last week."]),
    ("Can you add Anna Smith as an admin?", ["Done, she should get an invite email."]),
    (
        "We'd like to launch on the 15th. Realistic?",
        ["Realistic if the texts are ready by the 10th.", "The code side is on track."],
    ),
    (
        "The PDF export cuts off long names.",
        ["Good catch.", "I'll fix the layout, should be in tomorrow's build."],
    ),
    (
        "How much would a Slack integration cost?",
        [
            "Depends on what it needs to do.",
            "A simple notification bot would be about two days of work.",
        ],
    ),
    (
        "Could the dashboard show last month by default?",
        ["Yes, easy change.", "I'll switch it today."],
    ),
    (
        "I can't find the settings menu anymore.",
        ["It moved to the top right, under your name.", "Sorry, I should have mentioned that."],
    ),
    (
        "Is the new version live?",
        ["Yes, since this morning.", "Let me know if anything looks off."],
    ),
    ("Can we get a weekly report by email?", ["We can.", "Monday mornings work?"]),
    (
        "A customer says they were charged twice.",
        ["Send me their email and I'll check the logs.", "If it's real I'll refund right away."],
    ),
    (
        "Why does the app ask for location?",
        ["It's for the store finder.", "I can make it optional if you prefer."],
    ),
    ("Happy new year!", ["Happy new year Anna!", "Thanks for a great year of working together."]),
    (
        "Do you work on weekends if something breaks?",
        ["For real outages yes.", "Just call me, a message might get missed."],
    ),
    (
        "Can we export customer data for the GDPR request?",
        ["Yes, I'll prepare it today.", "Where should I send it?"],
    ),
    ("Is two-factor login possible?", ["It is.", "About a week of work including testing."]),
    (
        "The staging link doesn't open for me.",
        ["Try again now, I restarted it.", "It was out of memory."],
    ),
    ("How many users do we have now?", ["A bit over 3,200 active this month."]),
    ("Could you join the meeting with our designer?", ["Sure, when is it?"]),
    ("We need the release notes for the board meeting.", ["I'll write them up tonight."]),
    (
        "The checkout button looks broken on iPhone.",
        ["Checking on my phone now.", "Yes, I see it. It's a CSS issue, fixing it."],
    ),
    (
        "Can we pause the newsletter feature?",
        ["Done, it's turned off.", "Nothing is deleted, we can turn it back on any time."],
    ),
    (
        "What's the status of the payment provider switch?",
        ["Tests pass, waiting for their production keys.", "Once we get them it's a day of work."],
    ),
    ("Thanks for all the help this week.", ["Anytime, it was a fun one."]),
    (
        "Can we add a dark mode?",
        ["We can, but it touches every screen.", "I'd say a week. Worth it if users ask for it."],
    ),
    ("Is our SSL certificate expiring?", ["It renews automatically.", "Next renewal is in May."]),
    (
        "Could you check why the emails go to spam?",
        ["Yes. Probably the DNS records, I'll look today."],
    ),
    (
        "Who else has access to the admin panel?",
        ["You, Tom and me.", "I can send you the full list with last logins."],
    ),
    ("Is it ok if we postpone the call to Monday?", ["Monday is fine."]),
]


def _message(mid: int, ts: int, author: str, author_id: str, text: str) -> dict:
    return {
        "id": mid,
        "type": "message",
        "date": "",
        "date_unixtime": str(ts),
        "from": author,
        "from_id": author_id,
        "text": text,
        "text_entities": [{"type": "plain", "text": text}],
    }


def _chat(
    rng: random.Random,
    chat_id: int,
    name: str,
    chat_type: str,
    pairs: list,
    others: list[tuple[str, str]],
) -> dict:
    messages = []
    mid = 0
    ts = START + rng.randint(0, 3600)
    for _round in range(2):
        for incoming, replies in rng.sample(pairs, len(pairs)):
            ts += rng.randint(3 * 3600, 30 * 3600)
            author, author_id = rng.choice(others)
            mid += 1
            messages.append(_message(mid, ts, author, author_id, incoming))
            ts += rng.randint(30, 600)
            for reply in replies:
                mid += 1
                messages.append(_message(mid, ts, "Misha", f"user{OWNER_ID}", reply))
                ts += rng.randint(5, 60)
        pairs = pairs[: len(pairs) // 3]
    return {"name": name, "type": chat_type, "id": chat_id, "messages": messages}


def generate() -> dict:
    """Builds the whole export; the same seed always gives the same file."""
    rng = random.Random(7)
    half = len(FRIENDS) // 2
    chats = [
        _chat(rng, 101, "Костя", "personal_chat", FRIENDS[:half], [("Костя", "user101")]),
        _chat(rng, 102, "Лена", "personal_chat", FRIENDS[half:], [("Лена", "user102")]),
        _chat(
            rng,
            -201,
            "Backend team",
            "private_supergroup",
            WORK,
            [("Игорь", "user301"), ("Оля", "user302"), ("Сергей", "user303")],
        ),
        _chat(rng, 103, "Anna Becker", "personal_chat", CLIENT, [("Anna Becker", "user103")]),
    ]
    return {
        "about": "Fictional export for the humanize README walkthrough.",
        "personal_information": {"user_id": OWNER_ID, "first_name": "Misha"},
        "chats": {"about": "", "list": chats},
    }


def main() -> None:
    out = Path(__file__).resolve().parent / "telegram" / "result.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(generate(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
