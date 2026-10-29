# -*- coding: utf-8 -*-
"""Склонение русских ФИО: родительный падеж + сокращение «И.Н. Колесников».

Порядок слов предполагается стандартный: Фамилия Имя Отчество
(порядок «Имя Отчество Фамилия» тоже распознаётся — по суффиксу отчества).
Эвристика покрывает большинство распространённых случаев; результат
всегда можно поправить вручную в форме.
"""
import re

VELAR = set('кгхжчшщ')          # после них пишется «и» вместо «ы»
CONSONANTS = set('бвгджзклмнпрстфхцчшщ')

IRREG_M = {'пётр': 'петра', 'петр': 'петра', 'павел': 'павла',
           'лёв': 'льва', 'лев': 'льва', 'мстислав': 'мстислава'}
IRREG_F = {'любовь': 'любви', 'зоя': 'зои'}


def _cap(orig, res):
    if orig[:1].isupper() and res:
        return res[0].upper() + res[1:]
    return res


def _a_to_yi(word):
    """-а -> -ы / -и (после шипящих и к/г/х)."""
    stem = word[:-1]
    return stem + ('и' if stem[-1:].lower() in VELAR else 'ы') if stem else word


def detect_gender(parts):
    """m / f — по отчеству, иначе по фамилии."""
    if len(parts) >= 3:
        p = parts[2].lower()
        if p.endswith(('ович', 'евич')) or (p.endswith('ич') and not p.endswith('на')):
            return 'm'
        if p.endswith(('овна', 'евна', 'ична', 'чна', 'минична')):
            return 'f'
    if len(parts) >= 2:
        s = parts[0].lower()
        if s.endswith(('ова', 'ева', 'ёва', 'ина', 'ына', 'ая', 'яя')):
            return 'f'
    return 'm'


def gen_first(name, gender):
    low = name.lower()
    if gender == 'f':
        if low in IRREG_F:
            return _cap(name, IRREG_F[low])
        if low.endswith('ь'):
            return _cap(name, low[:-1] + 'и')
        if low.endswith('я'):
            return _cap(name, low[:-1] + 'и')
        if low.endswith('а'):
            return _cap(name, _a_to_yi(low))
        return name
    # мужские
    if low in IRREG_M:
        return _cap(name, IRREG_M[low])
    if low.endswith('й'):
        return _cap(name, low[:-1] + 'я')
    if low.endswith('ь'):
        return _cap(name, low[:-1] + 'я')
    if low.endswith('я'):
        return _cap(name, low[:-1] + 'и')
    if low.endswith('а'):
        return _cap(name, _a_to_yi(low))
    if low.endswith(('о', 'у', 'и', 'э', 'ю')):
        return name
    if low[-1:] in CONSONANTS:
        return _cap(name, low + 'а')
    return name


def gen_last(surname, gender):
    low = surname.lower()
    if gender == 'f':
        if low.endswith(('ова', 'ева', 'ёва', 'ина', 'ына')):
            return surname[:-1] + 'ой'
        if low.endswith('иа'):
            return surname  # Гарсиа, Гулиа — не склоняются
        if low.endswith('ая'):
            return _cap(surname, low[:-2] + 'ой')
        if low.endswith('яя'):
            return _cap(surname, low[:-2] + 'ей')
        if low.endswith('ия'):
            return _cap(surname, low + 'и')
        if low.endswith('а'):
            return _cap(surname, _a_to_yi(low))
        if low.endswith('я'):
            return _cap(surname, low[:-1] + 'и')
        return surname  # согласные, ь, -о/-ых женские фамилии не склоняются
    # мужские
    if low.endswith(('ских', 'цких', 'ых', 'их')):
        return surname
    if low.endswith('иа'):
        return surname  # Гарсиа — не склоняется
    if low.endswith(('ский', 'цкий')):
        return _cap(surname, low[:-2] + 'ого')
    if low.endswith('ой'):
        return _cap(surname, low[:-1] + 'го')
    if low.endswith('ый'):
        return _cap(surname, low[:-1] + 'го')
    if low.endswith('й'):
        return _cap(surname, low[:-1] + 'я')
    if low.endswith(('ов', 'ев', 'ёв', 'ин', 'ын')):
        return surname + 'а'
    if low.endswith('а'):
        return _cap(surname, _a_to_yi(low))
    if low.endswith('я'):
        return _cap(surname, low[:-1] + 'и')
    if low.endswith('ь'):
        return _cap(surname, low[:-1] + 'я')
    if low.endswith(('о', 'у', 'и', 'э', 'ю')):
        return surname
    if low[-1:] in CONSONANTS:
        return _cap(surname, low + 'а')
    return surname


def gen_patronymic(p, gender):
    low = p.lower()
    if low.endswith('на'):                      # женские: Ивановна -> Ивановны
        return _cap(p, low[:-1] + 'ы')
    if low.endswith(('ович', 'евич', 'ич')):    # мужские: +а
        return p + 'а'
    return p


def is_patronymic(tok):
    low = tok.lower()
    return low.endswith(('ович', 'евич', 'ич', 'овна', 'евна', 'ична', 'чна'))


def inflect(fio):
    """ФИО -> {'gen', 'short', 'gender'}."""
    parts = [t for t in re.split(r'[\s]+', (fio or '').strip()) if t]
    if not parts:
        return {'gen': '', 'short': '', 'gender': ''}
    gender = detect_gender(parts)

    # определяем роли токенов
    if len(parts) == 3:
        if is_patronymic(parts[2]):
            last, first, patr = parts[0], parts[1], parts[2]
        elif is_patronymic(parts[1]):
            first, patr, last = parts[0], parts[1], parts[2]
        else:
            last, first, patr = parts[0], parts[1], parts[2]
        gen = ' '.join([gen_last(last, gender), gen_first(first, gender),
                        gen_patronymic(patr, gender)])
        short = f'{first[0]}.{patr[0]}. {last}'
    elif len(parts) == 2:
        last, first = parts[0], parts[1]
        gen = ' '.join([gen_last(last, gender), gen_first(first, gender)])
        short = f'{first[0]}. {last}'
    else:
        gen = gen_last(parts[0], gender)
        short = parts[0]
    return {'gen': gen, 'short': short, 'gender': gender}


if __name__ == '__main__':
    cases = [
        ('Колесников Игорь Николаевич', 'Колесникова Игоря Николаевича', 'И.Н. Колесников'),
        ('Огородников Сергей Алексеевич', 'Огородникова Сергея Алексеевича', 'С.А. Огородников'),
        ('Петрова Мария Сергеевна', 'Петровой Марии Сергеевны', 'М.С. Петрова'),
        ('Иванова Ольга Дмитриевна', 'Ивановой Ольги Дмитриевны', 'О.Д. Иванова'),
        ('Достоевский Фёдор Михайлович', 'Достоевского Фёдора Михайловича', 'Ф.М. Достоевский'),
        ('Черных Анна Викторовна', 'Черных Анны Викторовны', 'А.В. Черных'),
        ('Жук Василий Петрович', 'Жука Василия Петровича', 'В.П. Жук'),
        ('Любимов Павел Сергеевич', 'Любимова Павла Сергеевича', 'П.С. Любимов'),
        ('Смирнова Анна Ильинична', 'Смирновой Анны Ильиничны', 'А.И. Смирнова'),
        ('Пётр Ильич Гринько', 'Гринько Петра Ильича', 'П.И. Гринько'),  # обратный порядок
        ('Толстой Лев Николаевич', 'Толстого Льва Николаевича', 'Л.Н. Толстой'),
        ('Кузнецов Даниил Александрович', 'Кузнецова Даниила Александровича', 'Д.А. Кузнецов'),
        ('Гарсиа Мария', 'Гарсиа Марии', 'М. Гарсиа'),
        ('Никитина Екатерина Фоминична', 'Никитиной Екатерины Фоминичны', 'Е.Ф. Никитина'),
    ]
    fails = 0
    for fio, want_gen, want_short in cases:
        r = inflect(fio)
        ok_gen = r['gen'] == want_gen
        ok_short = want_short is None or r['short'] == want_short
        if not (ok_gen and ok_short):
            fails += 1
            print(f"!! {fio}\n   gen:   {r['gen']!r} (ожидалось {want_gen!r})\n"
                  f"   short: {r['short']!r} (ожидалось {want_short!r})")
        else:
            print(f"OK {fio} -> {r['gen']} | {r['short']}")
    print('FAILURES:', fails)
