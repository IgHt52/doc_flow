# -*- coding: utf-8 -*-
"""API для моста pywebview <-> интерфейс. Все методы возвращают JSON-совместимые данные."""
import functools
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

import db
import generator as gen
from config import (APP_VERSION, BACKUPS_DIR, CLIENTS_DIR, DATA_DIR, DB_PATH,
                    ROOT, STATUS_ORDER, TEMPLATES_DIR, WP_STATUSES)

CLIENT_FIELDS = ['name_full', 'name_short', 'inn', 'kpp', 'ogrn', 'legal_address',
                 'actual_address', 'email', 'phone', 'bank_account', 'bank_name',
                 'corr_account', 'bik', 'director_position', 'director_position_gen',
                 'director_fio', 'director_fio_gen', 'director_fio_short',
                 'director_basis', 'folder', 'note']

CONTRACT_FIELDS = ['number', 'date_iso', 'city', 'work_type', 'subject', 'year',
                   'price', 'prepay_pct', 'service_term', 'stage1_start', 'stage1_end',
                   'stage2_start', 'stage2_end', 'opinion_deadline',
                   'originals_deadline', 'status']

CONTACT_FIELDS = ['role', 'fio', 'position', 'phone', 'email']


def api_method(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            return {'error': f'{type(e).__name__}: {e}'}
    return wrapper


def open_path(path):
    path = str(path)
    if sys.platform == 'win32':
        os.startfile(path)  # noqa: S606
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    else:
        subprocess.Popen(['xdg-open', path])


def _as_int(data, key, default=None):
    v = data.get(key)
    if v in (None, ''):
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class Api:
    def __init__(self):
        self._window = None
        db.init_db()

    def set_window(self, window):
        self._window = window

    # ---------- общая информация ----------

    @api_method
    def app_info(self):
        return {
            'version': APP_VERSION,
            'root': str(ROOT),
            'db': str(DB_PATH),
            'clients_dir': str(CLIENTS_DIR),
            'templates_dir': str(TEMPLATES_DIR),
            'status_order': STATUS_ORDER,
            'counts': {
                'clients': db.q1('SELECT COUNT(*) c FROM clients')['c'],
                'contracts': db.q1('SELECT COUNT(*) c FROM contracts')['c'],
                'documents': db.q1('SELECT COUNT(*) c FROM documents')['c'],
            },
        }

    # ---------- клиенты ----------

    @api_method
    def clients_list(self):
        clients = db.q('SELECT * FROM clients ORDER BY name_short COLLATE NOCASE')
        result = []
        role_pref = {'Контактное лицо': 0, 'Руководитель': 1, 'Гл. бухгалтер': 2}
        for c in clients:
            contacts = db.q('SELECT * FROM contacts WHERE client_id=?', (c['id'],))
            contacts.sort(key=lambda x: role_pref.get(x['role'], 9))
            contact = contacts[0] if contacts else None
            contract = db.q1(
                "SELECT * FROM contracts WHERE client_id=? "
                "ORDER BY (status='действует') DESC, date_iso DESC LIMIT 1", (c['id'],))
            doc = None
            if contract:
                doc = db.q1(
                    "SELECT d.* FROM documents d JOIN document_types t ON t.id=d.type_id "
                    "WHERE d.contract_id=? AND t.code='dogovor' ORDER BY d.id DESC LIMIT 1",
                    (contract['id'],))
            if contract is None:
                badge = {'status': 'нет договора', 'cls': 'gray'}
            elif doc:
                badge = {'status': doc['status'], 'cls': doc['status']}
            else:
                badge = {'status': 'не создан', 'cls': 'gray'}
            result.append({
                'id': c['id'],
                'name_short': c['name_short'] or c['name_full'],
                'name_full': c['name_full'],
                'inn': c['inn'],
                'work_type': contract['work_type'] if contract else '',
                'contact_fio': contact['fio'] if contact else (c['director_fio'] or ''),
                'contact_role': contact['role'] if contact else 'Руководитель',
                'phone': (contact['phone'] if contact and contact['phone'] else c['phone']),
                'email': (contact['email'] if contact and contact['email'] else c['email']),
                'badge': badge,
                'folder': c['folder'],
            })
        return result

    @api_method
    def client_get(self, cid):
        client = db.q1('SELECT * FROM clients WHERE id=?', (cid,))
        if not client:
            return {'error': 'Клиент не найден'}
        contacts = db.q('SELECT * FROM contacts WHERE client_id=? ORDER BY id', (cid,))
        contracts = db.q('SELECT * FROM contracts WHERE client_id=? '
                         'ORDER BY date_iso DESC, id DESC', (cid,))
        return {'client': client, 'contacts': contacts, 'contracts': contracts}

    @api_method
    def client_save(self, data):
        data = data or {}
        name_full = (data.get('name_full') or '').strip()
        name_short = (data.get('name_short') or '').strip()
        if not name_full and not name_short:
            return {'error': 'Укажите наименование клиента'}
        cid = _as_int(data, 'id')
        values = {f: (data.get(f) or '').strip() if isinstance(data.get(f), str)
                  else (data.get(f) if data.get(f) is not None else '')
                  for f in CLIENT_FIELDS}
        if not values.get('folder'):
            values['folder'] = gen.sanitize_folder(name_short or name_full)
        if cid:
            sets = ', '.join(f'{f}=?' for f in CLIENT_FIELDS)
            db.ex(f'UPDATE clients SET {sets} WHERE id=?',
                  tuple(values[f] for f in CLIENT_FIELDS) + (cid,))
        else:
            cols = ', '.join(CLIENT_FIELDS)
            ph = ', '.join('?' * len(CLIENT_FIELDS))
            cid = db.ex(f'INSERT INTO clients({cols}, created_at) VALUES({ph}, ?)',
                        tuple(values[f] for f in CLIENT_FIELDS) + (db.now(),))
        folder_path = gen.client_folder_path(values)
        folder_path.mkdir(parents=True, exist_ok=True)
        return {'ok': True, 'id': cid, 'folder': str(folder_path)}

    @api_method
    def client_open_folder(self, cid):
        c = db.q1('SELECT * FROM clients WHERE id=?', (cid,))
        if not c:
            return {'error': 'Клиент не найден'}
        p = gen.client_folder_path(c)
        p.mkdir(parents=True, exist_ok=True)
        open_path(p)
        return {'ok': True}

    # ---------- контакты ----------

    @api_method
    def contact_save(self, data):
        data = data or {}
        client_id = _as_int(data, 'client_id')
        if not client_id:
            return {'error': 'Не указан клиент'}
        if not (data.get('fio') or '').strip():
            return {'error': 'Укажите ФИО контакта'}
        cid = _as_int(data, 'id')
        values = {f: (data.get(f) or '').strip() for f in CONTACT_FIELDS}
        if cid:
            sets = ', '.join(f'{f}=?' for f in CONTACT_FIELDS)
            db.ex(f'UPDATE contacts SET {sets} WHERE id=? AND client_id=?',
                  tuple(values[f] for f in CONTACT_FIELDS) + (cid, client_id))
        else:
            cols = ', '.join(['client_id'] + CONTACT_FIELDS)
            ph = ', '.join('?' * (len(CONTACT_FIELDS) + 1))
            cid = db.ex(f'INSERT INTO contacts({cols}) VALUES({ph})',
                        (client_id,) + tuple(values[f] for f in CONTACT_FIELDS))
        return {'ok': True, 'id': cid}

    @api_method
    def contact_delete(self, contact_id):
        db.ex('DELETE FROM contacts WHERE id=?', (contact_id,))
        return {'ok': True}

    # ---------- договоры ----------

    @api_method
    def contract_get(self, contract_id):
        return db.q1('SELECT * FROM contracts WHERE id=?', (contract_id,)) or \
            {'error': 'Договор не найден'}

    @api_method
    def contract_save(self, data):
        data = data or {}
        client_id = _as_int(data, 'client_id')
        if not client_id:
            return {'error': 'Не указан клиент'}
        cid = _as_int(data, 'id')
        values = {}
        for f in CONTRACT_FIELDS:
            v = data.get(f)
            values[f] = v if v is not None else ''
        values['year'] = _as_int(data, 'year',
                                 gen.parse_iso(values.get('date_iso')).year
                                 if gen.parse_iso(values.get('date_iso')) else datetime.now().year)
        try:
            values['price'] = float(values.get('price') or 0)
        except (TypeError, ValueError):
            values['price'] = 0.0
        try:
            values['prepay_pct'] = float(values.get('prepay_pct') or 0)
        except (TypeError, ValueError):
            values['prepay_pct'] = 0.0
        if not str(values.get('service_term') or '').strip():
            values['service_term'] = '20'
        else:
            values['service_term'] = str(values['service_term']).strip()
        # originals_deadline: автоматически за 5 рабочих дней до выдачи АЗ, если не задан
        if not str(values.get('originals_deadline') or '').strip() \
                and str(values.get('opinion_deadline') or '').strip():
            values['originals_deadline'] = gen.minus_workdays(values['opinion_deadline'], 5)
        if not (values.get('subject') or '').strip():
            wt = values.get('work_type') or 'Аудит'
            values['subject'] = (f'{wt} за {values["year"]} год' if wt == 'Аудит'
                                 else f'{wt} ({values["year"]})')
        if not (values.get('number') or '').strip():
            values['number'] = self._suggest_number(values['year'])
            db.bump_seq_from_number(values['number'])
        if cid:
            sets = ', '.join(f'{f}=?' for f in CONTRACT_FIELDS)
            db.ex(f'UPDATE contracts SET {sets} WHERE id=?',
                  tuple(values[f] for f in CONTRACT_FIELDS) + (cid,))
        else:
            cols = ', '.join(['client_id'] + CONTRACT_FIELDS)
            ph = ', '.join('?' * (len(CONTRACT_FIELDS) + 1))
            cid = db.ex(f'INSERT INTO contracts({cols}, created_at) VALUES({ph}, ?)',
                        (client_id,) + tuple(values[f] for f in CONTRACT_FIELDS) + (db.now(),))
            if (values.get('city') or '') == '':
                db.ex('UPDATE contracts SET city=? WHERE id=?',
                      (db.setting('city_default', 'Красноярск'), cid))
        return {'ok': True, 'id': cid, 'number': values['number']}

    def _suggest_number(self, year):
        mask = db.setting('num_mask', '{seq}/{yy}-А')
        try:
            seq = int(db.setting('seq_next', '1'))
        except ValueError:
            seq = 1
        yy = str(year or datetime.now().year)[-2:]
        return mask.replace('{seq}', str(seq)).replace('{yy}', yy)

    @api_method
    def suggest_number(self, year=None):
        return self._suggest_number(year)

    # ---------- склонение ФИО / сроки ----------

    @api_method
    def inflect_fio(self, fio):
        import declension
        return declension.inflect(fio)

    @api_method
    def calc_originals_deadline(self, opinion_iso):
        """Дата оригиналов отчётности = 5 рабочих дней до выдачи АЗ."""
        return gen.minus_workdays(opinion_iso, 5)

    @api_method
    def contracts_all(self):
        return db.q("""SELECT ct.id, ct.number, ct.date_iso, ct.year, ct.status,
                              ct.client_id, c.name_short client_name, c.name_full
                       FROM contracts ct JOIN clients c ON c.id = ct.client_id
                       ORDER BY c.name_short COLLATE NOCASE, ct.date_iso DESC""")

    # ---------- рабочие документы (таблица готовности) ----------

    @api_method
    def workpapers_list(self, contract_id=None):
        sql = """SELECT w.*, ct.number contract_number, ct.year contract_year,
                        c.name_short client_name, c.id client_id
                 FROM workpapers w
                 LEFT JOIN contracts ct ON ct.id = w.contract_id
                 LEFT JOIN clients c ON c.id = ct.client_id"""
        params = ()
        if contract_id:
            sql += ' WHERE w.contract_id=?'
            params = (contract_id,)
        sql += " ORDER BY (w.status='принят'), w.due_iso='', w.due_iso, w.id"
        return db.q(sql, params)

    @api_method
    def workpaper_save(self, data):
        data = data or {}
        title = (data.get('title') or '').strip()
        if not title:
            return {'error': 'Укажите название рабочего документа'}
        contract_id = _as_int(data, 'contract_id')
        if not contract_id:
            return {'error': 'Выберите договор'}
        status = (data.get('status') or 'не начат').strip()
        if status not in WP_STATUSES:
            status = 'не начат'
        wid = _as_int(data, 'id')
        vals = (contract_id, title,
                (data.get('assistant') or '').strip(),
                (data.get('reviewer') or '').strip(),
                (data.get('due_iso') or '').strip(),
                status, (data.get('note') or '').strip())
        now = db.now()
        if wid:
            db.ex('UPDATE workpapers SET contract_id=?, title=?, assistant=?, reviewer=?, '
                  'due_iso=?, status=?, note=?, updated_at=? WHERE id=?',
                  vals + (now, wid))
        else:
            wid = db.ex('INSERT INTO workpapers(contract_id,title,assistant,reviewer,'
                        'due_iso,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                        vals + (now, now))
        return {'ok': True, 'id': wid}

    @api_method
    def workpaper_set_status(self, wp_id, status):
        if status not in WP_STATUSES:
            return {'error': f'Неизвестный статус: {status}'}
        r = db.ex('UPDATE workpapers SET status=?, updated_at=? WHERE id=?',
                  (status, db.now(), wp_id))
        return {'ok': True}

    @api_method
    def workpaper_delete(self, wp_id):
        db.ex('DELETE FROM workpapers WHERE id=?', (wp_id,))
        return {'ok': True}

    @api_method
    def workpaper_people(self):
        a = [r['assistant'] for r in
             db.q("SELECT DISTINCT assistant FROM workpapers WHERE assistant != '' ORDER BY assistant")]
        rv = [r['reviewer'] for r in
              db.q("SELECT DISTINCT reviewer FROM workpapers WHERE reviewer != '' ORDER BY reviewer")]
        return {'assistants': a, 'reviewers': rv}

    # ---------- типы документов / шаблоны ----------

    @api_method
    def doctypes_list(self):
        return db.q('SELECT * FROM document_types ORDER BY sort, id')

    @api_method
    def doctype_save(self, data):
        data = data or {}
        did = _as_int(data, 'id')
        code = (data.get('code') or '').strip()
        name = (data.get('name') or '').strip()
        if not name:
            return {'error': 'Укажите название типа документа'}
        if not code:
            code = re.sub(r'[^a-z0-9_]', '_', name.lower()) or f'type{datetime.now().timestamp():.0f}'
        template = (data.get('template') or '').strip()
        mask = (data.get('filename_mask') or '').strip() or '{type} {number} от {date}.docx'
        sort = _as_int(data, 'sort', 100)
        is_active = 1 if data.get('is_active') in (1, '1', True, 'true', 'on') else 0
        if is_active and template and not (TEMPLATES_DIR / template).exists():
            return {'error': f'Файл шаблона не найден: {template}'}
        if did:
            db.ex('UPDATE document_types SET code=?, name=?, template=?, filename_mask=?, '
                  'sort=?, is_active=? WHERE id=?',
                  (code, name, template, mask, sort, is_active, did))
        else:
            dup = db.q1('SELECT id FROM document_types WHERE code=?', (code,))
            if dup:
                return {'error': f'Тип с кодом «{code}» уже существует'}
            did = db.ex('INSERT INTO document_types(code,name,template,filename_mask,sort,is_active)'
                        ' VALUES(?,?,?,?,?,?)', (code, name, template, mask, sort, is_active))
        return {'ok': True, 'id': did}

    @api_method
    def pick_template_file(self):
        """Файловый диалог: выбрать .docx и скопировать в папку шаблонов."""
        if not self._window:
            return {'error': 'Окно приложения не инициализировано'}
        import webview
        res = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=False,
            file_types=('Файлы Word (*.docx)', 'Все файлы (*.*)'))
        if not res:
            return {'cancelled': True}
        src = Path(res[0] if isinstance(res, (list, tuple)) else res)
        if not src.exists():
            return {'error': f'Файл не найден: {src}'}
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        dst = TEMPLATES_DIR / src.name
        shutil.copy2(str(src), str(dst))
        return {'ok': True, 'name': src.name, 'path': str(dst)}

    # ---------- документы ----------

    @api_method
    def contract_documents(self, contract_id):
        types = db.q('SELECT * FROM document_types ORDER BY sort, id')
        docs = {d['type_id']: d for d in
                db.q('SELECT * FROM documents WHERE contract_id=?', (contract_id,))}
        rows = []
        for t in types:
            d = docs.get(t['id'])
            row = {'type': t, 'doc': None}
            if d:
                row['doc'] = d
                idx = STATUS_ORDER.index(d['status']) if d['status'] in STATUS_ORDER else -1
                row['next_status'] = STATUS_ORDER[idx + 1] if 0 <= idx < len(STATUS_ORDER) - 1 else None
            rows.append(row)
        return rows

    @api_method
    def doc_generate(self, contract_id, type_id):
        contract = db.q1('SELECT * FROM contracts WHERE id=?', (contract_id,))
        if not contract:
            return {'error': 'Договор не найден'}
        client = db.q1('SELECT * FROM clients WHERE id=?', (contract['client_id'],))
        if not client:
            return {'error': 'Клиент не найден'}
        dtype = db.q1('SELECT * FROM document_types WHERE id=?', (type_id,))
        if not dtype:
            return {'error': 'Тип документа не найден'}
        if not dtype['is_active']:
            return {'error': f'Тип «{dtype["name"]}» отключён или шаблон не загружен'}
        settings = db.settings_dict()

        # авто-номер, если договор ещё без номера
        if not (contract['number'] or '').strip():
            contract['number'] = self._suggest_number(contract['year'])
            db.ex('UPDATE contracts SET number=? WHERE id=?', (contract['number'], contract['id']))

        out = gen.render_document(client, contract, dtype, settings)
        try:
            rel = str(out.relative_to(ROOT))
        except ValueError:
            rel = str(out)

        now = db.now()
        existing = db.q1('SELECT id FROM documents WHERE contract_id=? AND type_id=?',
                         (contract_id, type_id))
        if existing:
            doc_id = existing['id']
            db.ex('UPDATE documents SET number=?, date_iso=?, status=?, file_path=?, '
                  'params_json=?, updated_at=?, updated_by=? WHERE id=?',
                  (contract['number'], contract['date_iso'], 'готов', rel,
                   json.dumps(dict(contract), ensure_ascii=False, default=str),
                   now, 'Администратор', doc_id))
            note = 'перегенерирован из шаблона'
        else:
            doc_id = db.ex('INSERT INTO documents(contract_id,type_id,number,date_iso,status,'
                           'file_path,params_json,created_at,updated_at,updated_by) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (contract_id, type_id, contract['number'], contract['date_iso'],
                            'готов', rel,
                            json.dumps(dict(contract), ensure_ascii=False, default=str),
                            now, now, 'Администратор'))
            note = 'создан из шаблона'
        db.ex('INSERT INTO status_history(document_id,status,note,ts,author) VALUES(?,?,?,?,?)',
              (doc_id, 'готов', note, now, 'Администратор'))
        db.bump_seq_from_number(contract['number'])
        return {'ok': True, 'doc_id': doc_id, 'file': str(out), 'name': out.name,
                'number': contract['number'],
                'date_display': gen.fmt_dot(contract['date_iso'])}

    @api_method
    def doc_set_status(self, doc_id, status):
        if status not in STATUS_ORDER:
            return {'error': f'Неизвестный статус: {status}'}
        d = db.q1('SELECT * FROM documents WHERE id=?', (doc_id,))
        if not d:
            return {'error': 'Документ не найден'}
        now = db.now()
        db.ex('UPDATE documents SET status=?, updated_at=?, updated_by=? WHERE id=?',
              (status, now, 'Администратор', doc_id))
        db.ex('INSERT INTO status_history(document_id,status,note,ts,author) VALUES(?,?,?,?,?)',
              (doc_id, status, f'статус изменён с «{d["status"]}»', now, 'Администратор'))
        return {'ok': True}

    @api_method
    def doc_history(self, doc_id):
        return db.q('SELECT * FROM status_history WHERE document_id=? ORDER BY id DESC', (doc_id,))

    @api_method
    def doc_open(self, doc_id):
        d = db.q1('SELECT * FROM documents WHERE id=?', (doc_id,))
        if not d or not d['file_path']:
            return {'error': 'Файл документа не найден в базе'}
        p = ROOT / d['file_path']
        if not p.exists():
            return {'error': f'Файл не найден на диске: {p}'}
        open_path(p)
        return {'ok': True}

    @api_method
    def doc_open_folder(self, doc_id):
        d = db.q1('SELECT * FROM documents WHERE id=?', (doc_id,))
        if not d or not d['file_path']:
            return {'error': 'Файл документа не найден в базе'}
        p = (ROOT / d['file_path']).parent
        if not p.exists():
            return {'error': f'Папка не найдена: {p}'}
        open_path(p)
        return {'ok': True}

    # ---------- матрица ----------

    @api_method
    def matrix(self, year=None):
        types = db.q('SELECT * FROM document_types ORDER BY sort, id')
        rows = db.q("""SELECT c.id client_id, c.name_short, c.name_full,
                              ct.id contract_id, ct.number, ct.date_iso, ct.year,
                              ct.work_type, ct.status contract_status
                       FROM contracts ct JOIN clients c ON c.id = ct.client_id
                       ORDER BY c.name_short COLLATE NOCASE, ct.date_iso DESC""")
        docs = {}
        for d in db.q('SELECT id, contract_id, type_id, status, file_path, updated_at FROM documents'):
            docs.setdefault(d['contract_id'], {})[d['type_id']] = d
        years = sorted({r['year'] for r in rows if r['year']}, reverse=True)
        out_rows = []
        for r in rows:
            if year and str(year) != 'all' and int(year) != r['year']:
                continue
            cells = {}
            for t in types:
                d = docs.get(r['contract_id'], {}).get(t['id'])
                cells[t['id']] = ({'status': d['status'], 'doc_id': d['id'], 'has_file': bool(d['file_path'])}
                                  if d else {'status': 'не создан', 'doc_id': None, 'has_file': False})
            out_rows.append({
                'client_id': r['client_id'],
                'client_name': r['name_short'] or r['name_full'],
                'contract_id': r['contract_id'],
                'number': r['number'], 'year': r['year'],
                'work_type': r['work_type'], 'contract_status': r['contract_status'],
                'cells': cells,
            })
        # клиенты без договоров — отдельной строкой
        if not year or str(year) == 'all':
            with_contracts = {r['client_id'] for r in rows}
            for c in db.q('SELECT id, name_short, name_full FROM clients'):
                if c['id'] not in with_contracts:
                    out_rows.append({
                        'client_id': c['id'], 'client_name': c['name_short'] or c['name_full'],
                        'contract_id': None, 'number': '', 'year': None,
                        'work_type': '', 'contract_status': '',
                        'cells': {t['id']: {'status': 'не создан', 'doc_id': None, 'has_file': False}
                                  for t in types},
                    })
        return {'years': years, 'types': types, 'rows': out_rows}

    # ---------- репозиторий ----------

    @api_method
    def repo_tree(self):
        CLIENTS_DIR.mkdir(parents=True, exist_ok=True)
        tree = []
        for d in sorted(CLIENTS_DIR.iterdir(), key=lambda x: x.name.lower()):
            if not d.is_dir():
                continue
            kids = sorted([s.name for s in d.iterdir() if s.is_dir()], reverse=True)
            tree.append({'name': d.name, 'rel': d.name, 'children': kids})
        return tree

    @api_method
    def repo_files(self, rel=''):
        base = (CLIENTS_DIR / (rel or '')).resolve()
        try:
            base.relative_to(CLIENTS_DIR.resolve())
        except ValueError:
            return {'error': 'Недопустимый путь'}
        if not base.exists():
            return {'error': 'Папка не найдена'}
        files = []
        for f in sorted(base.iterdir(), key=lambda x: x.name.lower()):
            if f.is_file():
                st = f.stat()
                files.append({
                    'name': f.name,
                    'rel': str(f.relative_to(CLIENTS_DIR)),
                    'size_kb': max(1, round(st.st_size / 1024)),
                    'mtime': datetime.fromtimestamp(st.st_mtime).strftime('%d.%m.%Y %H:%M'),
                })
        return {'rel': rel, 'files': files}

    @api_method
    def repo_open(self, rel=''):
        base = (CLIENTS_DIR / (rel or '')).resolve()
        try:
            base.relative_to(CLIENTS_DIR.resolve())
        except ValueError:
            return {'error': 'Недопустимый путь'}
        base.mkdir(parents=True, exist_ok=True)
        open_path(base)
        return {'ok': True}

    @api_method
    def repo_open_file(self, rel):
        p = (CLIENTS_DIR / (rel or '')).resolve()
        try:
            p.relative_to(CLIENTS_DIR.resolve())
        except ValueError:
            return {'error': 'Недопустимый путь'}
        if not p.exists():
            return {'error': 'Файл не найден'}
        open_path(p)
        return {'ok': True}

    # ---------- настройки ----------

    @api_method
    def settings_get(self):
        s = db.settings_dict()
        s['_templates_dir'] = str(TEMPLATES_DIR)
        return s

    @api_method
    def settings_save(self, data):
        data = data or {}
        allowed = set(db.DEFAULT_SETTINGS.keys())
        saved = 0
        for k, v in data.items():
            if k in allowed:
                db.set_setting(k, v if v is not None else '')
                saved += 1
        return {'ok': True, 'saved': saved}

    @api_method
    def templates_open_folder(self):
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        open_path(TEMPLATES_DIR)
        return {'ok': True}

    # ---------- бэкап ----------

    @api_method
    def backup_now(self):
        import sqlite3
        ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        dst = BACKUPS_DIR / ts
        dst.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(str(DB_PATH))
        out = sqlite3.connect(str(dst / 'docflow.db'))
        with out:
            src.backup(out)
        out.close()
        src.close()
        if CLIENTS_DIR.exists():
            shutil.copytree(str(CLIENTS_DIR), str(dst / 'clients'), dirs_exist_ok=True)
        if TEMPLATES_DIR.exists():
            shutil.copytree(str(TEMPLATES_DIR), str(dst / 'templates'), dirs_exist_ok=True)
        size = sum(f.stat().st_size for f in dst.rglob('*') if f.is_file())
        return {'ok': True, 'path': str(dst), 'size_mb': round(size / 1024 / 1024, 1)}
