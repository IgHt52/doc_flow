# -*- coding: utf-8 -*-
"""Числа прописью по-русски (для сумм в договорах)."""

ONES = ['', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять']
ONES_F = ['', 'одна', 'две', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять']
TEENS = ['десять', 'одиннадцать', 'двенадцать', 'тринадцать', 'четырнадцать',
         'пятнадцать', 'шестнадцать', 'семнадцать', 'восемнадцать', 'девятнадцать']
TENS = ['', '', 'двадцать', 'тридцать', 'сорок', 'пятьдесят',
        'шестьдесят', 'семьдесят', 'восемьдесят', 'девяносто']
HUNDREDS = ['', 'сто', 'двести', 'триста', 'четыреста',
            'пятьсот', 'шестьсот', 'семьсот', 'восемьсот', 'девятьсот']


def _plural(n, f1, f2, f5):
    n10, n100 = n % 10, n % 100
    if n10 == 1 and n100 != 11:
        return f1
    if 2 <= n10 <= 4 and not (12 <= n100 <= 14):
        return f2
    return f5


def _triplet(n, fem):
    words = []
    h, rem = divmod(n, 100)
    if h:
        words.append(HUNDREDS[h])
    if rem >= 20:
        t, o = divmod(rem, 10)
        words.append(TENS[t])
        if o:
            words.append((ONES_F if fem else ONES)[o])
    elif rem >= 10:
        words.append(TEENS[rem - 10])
    elif rem > 0:
        words.append((ONES_F if fem else ONES)[rem])
    return words


def num2words_ru(n):
    """Целое число прописью, строчными. 0 -> 'ноль'."""
    n = int(round(float(n)))
    if n < 0:
        return 'минус ' + num2words_ru(-n)
    if n == 0:
        return 'ноль'
    words = []
    b, r = divmod(n, 10 ** 9)
    m, r = divmod(r, 10 ** 6)
    t, r = divmod(r, 10 ** 3)
    if b:
        words += _triplet(b, False) + [_plural(b, 'миллиард', 'миллиарда', 'миллиардов')]
    if m:
        words += _triplet(m, False) + [_plural(m, 'миллион', 'миллиона', 'миллионов')]
    if t:
        words += _triplet(t, True) + [_plural(t, 'тысяча', 'тысячи', 'тысяч')]
    if r:
        words += _triplet(r, False)
    return ' '.join(words)


def money_fmt(n):
    """345000 -> '345 000'."""
    n = int(round(float(n)))
    return f'{n:,}'.replace(',', ' ')


if __name__ == '__main__':
    tests = {
        345000: 'триста сорок пять тысяч',
        172500: 'сто семьдесят две тысячи пятьсот',
        500000: 'пятьсот тысяч',
        21000: 'двадцать одна тысяча',
        1234567: 'один миллион двести тридцать четыре тысячи пятьсот шестьдесят семь',
        0: 'ноль',
        100: 'сто',
        2: 'два',
    }
    ok = True
    for k, v in tests.items():
        got = num2words_ru(k)
        mark = 'OK ' if got == v else '!! '
        if got != v:
            ok = False
        print(f"{mark}{k} -> {got!r} (ожидалось {v!r})")
    print('ALL OK' if ok else 'FAILURES')
