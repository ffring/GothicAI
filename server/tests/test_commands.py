from gothic_ai.commands import FORMS, parse


def kind(text: str, names: tuple[str, ...] = ()) -> str | None:
    order = parse(text, lambda w: w in names)
    return order.kind if order else None


def test_the_forms_of_an_order_in_what_the_hero_says_around_them():
    assert kind("Стой!") == "halt"
    assert kind("Стой здесь.") == "wait"
    assert kind("За мной!") == "follow"
    assert kind("Горн, за мной!", ("горн",)) == "follow"
    assert kind("Эй, ребята, отступаем, живо!") == "retreat"
    assert kind("В атаку!") == "attack"
    assert kind("Ко мне!") == "come"
    assert kind("Оружие к бою!") == "draw"
    assert kind("Убери оружие.") == "sheathe"
    assert kind("Свободна.") == "dismiss"
    # said again and again
    assert kind("Стой, стой!") == "halt"
    assert kind("Назад! Назад! Назад!") == "retreat"
    # as speech recognition may write it: run together, a letter off in a long form
    assert kind("Замной!") == "follow"
    assert kind("Оружия к бою!") == "draw"
    assert kind("Иди по своим делом.") == "dismiss"


def test_words_of_an_order_that_are_not_one():
    for text in (
        "Не стой столбом, помоги мне.",
        "Сколько это стоит?",
        "Сюда?",  # a question
        "Иди за мной в город.",  # more to it: the conversational model's
        "Он крикнул мне: стой!",
        "Убил его.",
        "Стою.",
        "Иду.",
        "Стой, стой, а где Лестер?",
        "Горн, за мной!",  # Горн is nobody around: a word too many
        "",
    ):
        assert kind(text) is None, text


def test_every_form_is_its_order():
    for k, forms in FORMS.items():
        for form in forms:
            assert kind(form[0].upper() + form[1:] + "!") == k, form
