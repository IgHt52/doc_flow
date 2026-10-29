# -*- coding: utf-8 -*-
"""Генерация .docx из шаблонов (docxtpl): контекст, даты, суммы, файлы."""
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from docxtpl import DocxTemplate

from config import CLIENTS_DIR, TEMPLATES_DIR
from num2words_ru import money_fmt, num2words_ru

MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля',
              'августа', 'сентября', 'октября', 'ноября', 'декабря']


# ---------- даты ----------

def parse_iso(s):
    """'2026-07-01' -> datetime; пусто/битое -> None."""
    if not s:
        return None
    s = str(s).strip()
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def fmt_dot(iso, suffix=''):
    d = parse_iso(iso)
    return d.strftime('%d.%m.%Y') + suffix if d else ''


def fmt_human(iso):
    """'2026-07-01' -> '01 июля 2026 г.'"""
    d = parse_iso(iso)
    if not d:
        return ''
    return f'{d.day:02d} {MONTHS_GEN[d.month - 1]} {d.year} г.'


def fmt_quoted(iso):
    """'2026-07-01' -> '«01» июля 2026'"""
    d = parse_iso(iso)
    if not d:
        return ''
    return f'«{d.day:02d}» {MONTHS_GEN[d.month - 1]} {d.year}'


def minus_workdays(iso, n):
    """Дата на n рабочих дней раньше iso (выходные = сб/вс). Возвращает ISO-строку."""
    d = parse_iso(iso)
    if not d:
        return ''
    cur = d
    left = int(n)
    while left > 0:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            left -= 1
    return cur.strftime('%Y-%m-%d')


def num_str(x):
    """50.0 -> '50', 12.5 -> '12.5'."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x or '')
    return str(int(f)) if f == int(f) else str(f)


# ---------- папки и имена файлов ----------

_BAD_CHARS = re.compile(r'[«»"\\/:*?<>|\r\n\t]+')


def sanitize_folder(name):
    s = _BAD_CHARS.sub('_', (name or '').strip())
    s = re.sub(r'\s+', '_', s)
    s = re.sub(r'_+', '_', s).strip('._')
    return s or 'Клиент'


def client_folder_path(client):
    folder = (client.get('folder') or '').strip() or sanitize_folder(
        client.get('name_short') or client.get('name_full'))
    return CLIENTS_DIR / folder


def safe_filename(s):
    s = _BAD_CHARS.sub('-', (s or '').strip())
    return re.sub(r'\s+', ' ', s).strip(' -') or 'документ'


def build_filename(mask, doctype, number, date_iso):
    name = mask or '{type} {number} от {date}.docx'
    out = name.replace('{type}', doctype.get('name', 'Документ'))
    out = out.replace('{number}', (number or '').replace('/', '-').replace('№', '').strip())
    out = out.replace('{date}', fmt_dot(date_iso))
    if not out.lower().endswith('.docx'):
        out += '.docx'
    return safe_filename(out)


# ---------- контекст шаблона ----------

def build_context(client, contract, settings):
    price = int(round(float(contract.get('price') or 0)))
    try:
        pct = float(contract.get('prepay_pct') or 0)
    except (TypeError, ValueError):
        pct = 0.0
    prepay = int(round(price * pct / 100.0))
    rest = price - prepay
    rest_pct = 100 - pct if pct else 0

    year = contract.get('year') or (parse_iso(contract.get('date_iso')) or datetime.now()).year
    try:
        year = int(year)
    except (TypeError, ValueError):
        year = datetime.now().year

    g = lambda k: settings.get(k, '')  # noqa: E731

    return {
        'client': {
            'name_full': client.get('name_full') or client.get('name_short') or '',
            'name_short': client.get('name_short') or client.get('name_full') or '',
            'inn': client.get('inn', ''), 'kpp': client.get('kpp', ''),
            'legal_address': client.get('legal_address', ''),
            'bank_account': client.get('bank_account', ''),
            'bank_name': client.get('bank_name', ''),
            'corr_account': client.get('corr_account', ''),
            'bik': client.get('bik', ''),
            'director_position': client.get('director_position', ''),
            'director_position_gen': client.get('director_position_gen', ''),
            'director_fio_gen': client.get('director_fio_gen', ''),
            'director_fio_short': client.get('director_fio_short', ''),
            'director_basis': client.get('director_basis', ''),
        },
        'contract': {
            'number': contract.get('number', ''),
            'city': contract.get('city') or g('city_default'),
            'date_human': fmt_human(contract.get('date_iso')),
            'date_quoted': fmt_quoted(contract.get('date_iso')),
            'date_short': fmt_dot(contract.get('date_iso')),
            'service_term': str(contract.get('service_term') or '20'),
            'price_fmt': money_fmt(price),
            'price_words': num2words_ru(price),
            'price_kop': '00',
            'prepay_fmt': money_fmt(prepay),
            'prepay_words': num2words_ru(prepay),
            'prepay_pct': num_str(pct),
            'rest_fmt': money_fmt(rest),
            'rest_words': num2words_ru(rest),
            'rest_pct': num_str(rest_pct),
            'opinion_deadline': fmt_dot(contract.get('opinion_deadline'), ' г.'),
            'originals_deadline': fmt_dot(contract.get('originals_deadline'), ' г.'),
        },
        'audit': {
            'year': str(year),
            'year_next': str(year + 1),
        },
        'stage1': {
            'start': fmt_dot(contract.get('stage1_start'), ' г.'),
            'end': fmt_dot(contract.get('stage1_end'), ' г.'),
        },
        'stage2': {
            'start': fmt_dot(contract.get('stage2_start'), ' г.'),
            'end': fmt_dot(contract.get('stage2_end'), ' г.'),
        },
        'executor': {
            'name_full': g('executor_name_full'),
            'name_short': g('executor_name_short'),
            'sro_ornz': g('executor_sro_ornz'),
            'legal_address': g('executor_legal_address'),
            'inn': g('executor_inn'), 'kpp': g('executor_kpp'),
            'bank_account': g('executor_bank_account'),
            'bank_name': g('executor_bank_name'),
            'corr_account': g('executor_corr_account'),
            'bik': g('executor_bik'),
            'signer_position': g('executor_signer_position'),
            'signer_position_gen': g('executor_signer_position_gen'),
            'signer_fio': g('executor_signer_fio'),
            'signer_fio_gen': g('executor_signer_fio_gen'),
            'signer_fio_short': g('executor_signer_fio_short'),
            'signer_basis': g('executor_signer_basis'),
            'insurance': g('executor_insurance'),
        },
    }


# ---------- генерация ----------

def render_document(client, contract, doctype, settings, dest_dir=None):
    """Рендерит шаблон и сохраняет файл. Возвращает Path."""
    tpl_file = TEMPLATES_DIR / (doctype.get('template') or '')
    if not doctype.get('template') or not tpl_file.exists():
        raise FileNotFoundError(
            f'Шаблон не найден: {tpl_file}. Загрузите .docx-шаблон в настройках.')

    ctx = build_context(client, contract, settings)
    tpl = DocxTemplate(str(tpl_file))
    tpl.render(ctx)

    if dest_dir is None:
        year = ctx['audit']['year']
        dest_dir = client_folder_path(client) / year
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    fname = build_filename(doctype.get('filename_mask'), doctype,
                           contract.get('number'), contract.get('date_iso'))
    out = dest_dir / fname
    tpl.save(str(out))
    return out
