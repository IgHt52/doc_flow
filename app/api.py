# -*- coding: utf-8 -*-
"""API для моста pywebview <-> интерфейс. Сессии пользователей, журнал, календарь."""
import csv
import functools
import json
import math
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

import db
import backup as backup_service
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

EVENT_STATUSES = ['открыта', 'выполнена']
CONTRACT_STATUSES = ['действует', 'завершён', 'расторгнут']


def api_method(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except Exception as e:
            traceback.print_exc()
            return {'error': f'{type(e).__name__}: {e}'}
    return wrapper


def require_user(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self._user:
            return {'error': 'Требуется вход в приложение', 'auth': True}
        return fn(self, *args, **kwargs)
    return wrapper


def require_admin(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self._user:
            return {'error': 'Требуется вход в приложение', 'auth': True}
        if self._user.get('role') != 'admin':
            return {'error': 'Требуются права администратора'}
        return fn(self, *args, **kwargs)
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


def _user_maps():
    """Справочник пользователей: ID, логин и отображаемое имя."""
    rows = db.q('SELECT id, login, name FROM users')
    by_id = {r['id']: (r['name'] or r['login']) for r in rows}
    by_value = {}
    for r in rows:
        for value in (r['login'], r['name']):
            value = (value or '').strip()
            if value:
                by_value.setdefault(value, set()).add(r['id'])
    return by_id, by_value


def _resolve_user(value, by_id, by_value):
    """Принимает user.id, логин или имя и возвращает (id, имя)."""
    raw = str(value or '').strip()
    if not raw:
        return None, ''
    try:
        uid = int(raw)
        if uid in by_id:
            return uid, by_id[uid]
    except (TypeError, ValueError):
        pass
    ids = by_value.get(raw, set())
    if len(ids) == 1:
        uid = next(iter(ids))
        return uid, by_id[uid]
    return None, raw


def _event_display_rows(rows):
    by_id, _ = _user_maps()
    for row in rows:
        if row.get('assignee_id') in by_id:
            row['assignee'] = by_id[row['assignee_id']]
        if row.get('created_by_id') in by_id:
            row['created_by'] = by_id[row['created_by_id']]
        ids = []
        for value in str(row.get('participant_ids') or '').split(','):
            try:
                uid = int(value.strip())
            except (TypeError, ValueError):
                continue
            if uid in by_id:
                ids.append(uid)
        if ids:
            row['participants'] = ', '.join(by_id[uid] for uid in ids)
    return rows


def _workpaper_display_rows(rows):
    by_id, _ = _user_maps()
    for row in rows:
        if row.get('assistant_id') in by_id:
            row['assistant'] = by_id[row['assistant_id']]
        if row.get('reviewer_id') in by_id:
            row['reviewer'] = by_id[row['reviewer_id']]
    return rows


class Api:
    def __init__(self):
        self._window = None
        self._user = None
        db.init_db()
        self._restore_session()

    def set_window(self, window):
        self._window = window

    def _restore_session(self):
        u = db.remembered_user()
        if u:
            self._user = {'id': u['id'], 'login': u['login'],
                          'name': u['name'] or u['login'], 'role': u['role']}
        else:
            db.clear_remembered_user()

    # ================= сессия / авторизация =================

    def _name(self):
        if not self._user:
            return 'Не авторизован'
        return self._user.get('name') or self._user.get('login') or '?'

    def _log(self, action, obj='', details=''):
        db.log_action(self._name(), action, obj, details)

    @api_method
    def check_session(self):
        if self._user:
            return {'logged_in': True, **self._user}
        return {'logged_in': False}

    @api_method
    def login(self, user_login, password):
        u = db.q1('SELECT * FROM users WHERE login=?', ((user_login or '').strip(),))
        if not u or not int(u['is_active']) or not db.verify_password(password or '', u['password_hash']):
            db.log_action((user_login or '?').strip(), 'login_failed', '',
                          'Неверный логин или пароль' + ('' if (u and int(u['is_active'])) else ' (или пользователь отключён)'))
            return {'error': 'Неверный логин или пароль'}
        self._user = {'id': u['id'], 'login': u['login'],
                      'name': u['name'] or u['login'], 'role': u['role']}
        db.ex('UPDATE users SET last_login=? WHERE id=?', (db.now(), u['id']))
        db.remember_user(u['id'])
        self._log('login', '', f"Вход в приложение (роль: {u['role']})")
        return {'logged_in': True, **self._user}

    @api_method
    def register(self, user_login, name, password, password2):
        """Самостоятельная регистрация обычной пользовательской учётной записи."""
        login = (user_login or '').strip()
        display_name = (name or '').strip() or login
        password = password or ''
        password2 = password2 or ''

        if not re.fullmatch(r'[A-Za-zА-Яа-яЁё0-9_.@+-]{3,64}', login):
            return {'error': 'Логин: от 3 до 64 символов, только буквы, цифры и . _ @ + -'}
        if len(password) < 4:
            return {'error': 'Пароль должен быть не короче 4 символов'}
        if password != password2:
            return {'error': 'Пароли не совпадают'}
        if db.q1('SELECT id FROM users WHERE login=?', (login,)):
            return {'error': f'Логин «{login}» уже занят'}

        # Встроенный admin не заменяем и не удаляем: это резервная
        # администраторская учётная запись для владельцев приложения.
        db.ex('INSERT INTO users(login,name,password_hash,role,is_active,created_at) '
              'VALUES(?,?,?,?,1,?)',
              (login, display_name, db.hash_password(password), 'user', db.now()))
        role = 'user'
        details = 'Самостоятельная регистрация пользователя'

        db.log_action(login, 'register', login, details)
        return {'ok': True, 'login': login, 'name': display_name, 'role': role}

    @api_method
    def logout(self):
        if self._user:
            self._log('logout')
        db.clear_remembered_user()
        self._user = None
        return {'ok': True}

    @api_method
    @require_user
    def change_password(self, old_password, new_password):
        u = db.q1('SELECT * FROM users WHERE id=?', (self._user['id'],))
        if not u or not db.verify_password(old_password or '', u['password_hash']):
            return {'error': 'Текущий пароль указан неверно'}
        if len(new_password or '') < 4:
            return {'error': 'Новый пароль должен быть не короче 4 символов'}
        db.ex('UPDATE users SET password_hash=? WHERE id=?',
              (db.hash_password(new_password), self._user['id']))
        self._log('change_password', self._user['login'], 'Пароль изменён')
        return {'ok': True}

    @api_method
    @require_user
    def users_brief(self):
        """Активные пользователи для выпадашек (ответственные, участники)."""
        return [{'id': u['id'], 'name': u['name'] or u['login'], 'login': u['login']}
                for u in db.q('SELECT * FROM users WHERE is_active=1 ORDER BY name COLLATE NOCASE')]

    # ================= пользователи (админ) =================

    @api_method
    @require_admin
    def users_list(self):
        return db.q('SELECT id, login, name, role, is_active, created_at, last_login '
                    'FROM users ORDER BY (role != "admin"), name COLLATE NOCASE')

    @api_method
    @require_admin
    def user_save(self, data):
        data = data or {}
        uid = _as_int(data, 'id')
        login = (data.get('login') or '').strip()
        name = (data.get('name') or '').strip()
        password = data.get('password') or ''
        role = 'admin' if data.get('role') == 'admin' else 'user'
        is_active = 0 if data.get('is_active') in (0, '0', False, 'false') else 1
        if not login:
            return {'error': 'Укажите логин'}
        if uid:
            cur = db.q1('SELECT * FROM users WHERE id=?', (uid,))
            if not cur:
                return {'error': 'Пользователь не найден'}
            # защита от самоблокировки
            if cur['id'] == self._user['id']:
                if not is_active:
                    return {'error': 'Нельзя отключить собственную учётную запись'}
                if role != 'admin':
                    return {'error': 'Нельзя понизить собственную роль'}
            dup = db.q1('SELECT id FROM users WHERE login=? AND id != ?', (login, uid))
            if dup:
                return {'error': f'Логин «{login}» уже занят'}
            db.ex('UPDATE users SET login=?, name=?, role=?, is_active=? WHERE id=?',
                  (login, name or login, role, is_active, uid))
            if password:
                if len(password) < 4:
                    return {'error': 'Пароль должен быть не короче 4 символов'}
                db.ex('UPDATE users SET password_hash=? WHERE id=?',
                      (db.hash_password(password), uid))
            self._log('user_update', login,
                      f"Изменён пользователь: роль={role}, активен={'да' if is_active else 'нет'}"
                      + (', пароль сброшен' if password else ''))
        else:
            if db.q1('SELECT id FROM users WHERE login=?', (login,)):
                return {'error': f'Логин «{login}» уже занят'}
            if len(password) < 4:
                return {'error': 'Пароль должен быть не короче 4 символов'}
            uid = db.ex('INSERT INTO users(login,name,password_hash,role,is_active,created_at)'
                        ' VALUES(?,?,?,?,?,?)',
                        (login, name or login, db.hash_password(password), role,
                         is_active, db.now()))
            self._log('user_create', login, f'Создан пользователь: роль={role}')
        return {'ok': True, 'id': uid}

    # ================= журнал (админ) =================

    @api_method
    @require_admin
    def audit_list(self, limit=500, user=''):
        sql = ('SELECT id, ts, user, action, object, details FROM audit_log')
        params = []
        if user:
            sql += ' WHERE user=?'
            params.append(user)
        sql += ' ORDER BY id DESC LIMIT ?'
        params.append(int(limit or 500))
        rows = db.q(sql, tuple(params))
        users = [r['user'] for r in db.q('SELECT DISTINCT user FROM audit_log ORDER BY user')]
        return {'rows': rows, 'users': users}

    @api_method
    @require_admin
    def audit_export_csv(self):
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime('%Y-%m-%d_%H%M%S')
        out = BACKUPS_DIR / f'журнал_{ts}.csv'
        rows = db.q('SELECT ts, user, action, object, details FROM audit_log ORDER BY id')
        with open(out, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f, delimiter=';')
            w.writerow(['Дата/время', 'Пользователь', 'Действие', 'Объект', 'Подробности'])
            for r in rows:
                w.writerow([r['ts'], r['user'], r['action'], r['object'], r['details']])
        self._log('audit_export', '', str(out))
        try:
            open_path(out)
        except Exception:
            pass
        return {'ok': True, 'path': str(out), 'rows': len(rows)}

    # ================= общая информация =================

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

    # ================= клиенты =================

    @api_method
    @require_user
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
    @require_user
    def client_get(self, cid):
        client = db.q1('SELECT * FROM clients WHERE id=?', (cid,))
        if not client:
            return {'error': 'Клиент не найден'}
        contacts = db.q('SELECT * FROM contacts WHERE client_id=? ORDER BY id', (cid,))
        contracts = db.q('SELECT * FROM contracts WHERE client_id=? '
                         'ORDER BY date_iso DESC, id DESC', (cid,))
        return {'client': client, 'contacts': contacts, 'contracts': contracts}

    @api_method
    @require_user
    def client_save(self, data):
        data = data or {}
        name_full = (data.get('name_full') or '').strip()
        name_short = (data.get('name_short') or '').strip()
        if not name_full and not name_short:
            return {'error': 'Укажите наименование клиента'}
        cid = _as_int(data, 'id')
        current = None
        if cid:
            current = db.q1('SELECT * FROM clients WHERE id=?', (cid,))
            if not current:
                return {'error': 'Клиент не найден'}
        values = {f: (data.get(f) or '').strip() if isinstance(data.get(f), str)
                  else (data.get(f) if data.get(f) is not None else '')
                  for f in CLIENT_FIELDS}
        # Папка — технический идентификатор. После создания клиента
        # сохраняем её, чтобы переименование компании не потеряло файлы.
        if current and current.get('folder'):
            values['folder'] = current['folder']
        values['folder'] = gen.normalize_folder(values.get('folder'), name_short or name_full)
        display = values.get('name_short') or name_full
        if cid:
            sets = ', '.join(f'{f}=?' for f in CLIENT_FIELDS)
            db.ex(f'UPDATE clients SET {sets} WHERE id=?',
                  tuple(values[f] for f in CLIENT_FIELDS) + (cid,))
            self._log('client_update', display, f'Изменён клиент (id={cid})')
        else:
            cols = ', '.join(CLIENT_FIELDS)
            ph = ', '.join('?' * len(CLIENT_FIELDS))
            cid = db.ex(f'INSERT INTO clients({cols}, created_at) VALUES({ph}, ?)',
                        tuple(values[f] for f in CLIENT_FIELDS) + (db.now(),))
            self._log('client_create', display, f'Создан клиент (id={cid})')
        folder_path = gen.client_folder_path(values)
        folder_path.mkdir(parents=True, exist_ok=True)
        return {'ok': True, 'id': cid, 'folder': str(folder_path)}

    @api_method
    @require_user
    def client_open_folder(self, cid):
        c = db.q1('SELECT * FROM clients WHERE id=?', (cid,))
        if not c:
            return {'error': 'Клиент не найден'}
        p = gen.client_folder_path(c)
        p.mkdir(parents=True, exist_ok=True)
        open_path(p)
        return {'ok': True}

    # ================= контакты =================

    @api_method
    @require_user
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
            self._log('contact_update', values['fio'], f'Изменён контакт (id={cid})')
        else:
            cols = ', '.join(['client_id'] + CONTACT_FIELDS)
            ph = ', '.join('?' * (len(CONTACT_FIELDS) + 1))
            cid = db.ex(f'INSERT INTO contacts({cols}) VALUES({ph})',
                        (client_id,) + tuple(values[f] for f in CONTACT_FIELDS))
            self._log('contact_create', values['fio'], f'Добавлен контакт ({values["role"]})')
        return {'ok': True, 'id': cid}

    @api_method
    @require_user
    def contact_delete(self, contact_id):
        c = db.q1('SELECT * FROM contacts WHERE id=?', (contact_id,))
        db.ex('DELETE FROM contacts WHERE id=?', (contact_id,))
        self._log('contact_delete', (c or {}).get('fio', ''), f'Удалён контакт (id={contact_id})')
        return {'ok': True}

    # ================= договоры =================

    @api_method
    @require_user
    def contract_get(self, contract_id):
        return db.q1('SELECT * FROM contracts WHERE id=?', (contract_id,)) or \
            {'error': 'Договор не найден'}

    @api_method
    @require_user
    def contract_save(self, data):
        data = data or {}
        client_id = _as_int(data, 'client_id')
        if not client_id:
            return {'error': 'Не указан клиент'}
        cid = _as_int(data, 'id')
        current = None
        if cid:
            current = db.q1('SELECT * FROM contracts WHERE id=?', (cid,))
            if not current:
                return {'error': 'Договор не найден'}
            # Договор нельзя незаметно перенести к другому клиенту.
            client_id = current['client_id']
        if not db.q1('SELECT id FROM clients WHERE id=?', (client_id,)):
            return {'error': 'Клиент не найден'}
        values = {}
        for f in CONTRACT_FIELDS:
            v = data.get(f)
            values[f] = v if v is not None else ''

        for field, label in (
            ('date_iso', 'дату договора'),
            ('stage1_start', 'начало этапа 1'), ('stage1_end', 'окончание этапа 1'),
            ('stage2_start', 'начало этапа 2'), ('stage2_end', 'окончание этапа 2'),
            ('opinion_deadline', 'дату выдачи заключения'),
            ('originals_deadline', 'дату оригиналов отчётности'),
        ):
            if str(values.get(field) or '').strip() and not gen.parse_iso(values[field]):
                return {'error': f'Проверьте {label}: дата указана неверно'}

        values['year'] = _as_int(data, 'year',
                                 gen.parse_iso(values.get('date_iso')).year
                                 if gen.parse_iso(values.get('date_iso')) else datetime.now().year)
        if values['year'] < 1900 or values['year'] > 2200:
            return {'error': 'Период должен быть в пределах 1900–2200 года'}
        try:
            values['price'] = float(values.get('price') or 0)
        except (TypeError, ValueError):
            return {'error': 'Стоимость должна быть числом'}
        if not math.isfinite(values['price']) or values['price'] < 0:
            return {'error': 'Стоимость не может быть отрицательной'}
        try:
            values['prepay_pct'] = float(values.get('prepay_pct') or 0)
        except (TypeError, ValueError):
            return {'error': 'Предоплата должна быть числом от 0 до 100'}
        if not math.isfinite(values['prepay_pct']) or not 0 <= values['prepay_pct'] <= 100:
            return {'error': 'Предоплата должна быть в пределах от 0 до 100%'}
        if not str(values.get('service_term') or '').strip():
            values['service_term'] = '20'
        else:
            values['service_term'] = str(values['service_term']).strip()
            if not re.fullmatch(r'\d+', values['service_term']) or int(values['service_term']) <= 0:
                return {'error': 'Срок оказания услуг должен быть положительным числом рабочих дней'}
        values['status'] = values.get('status') or 'действует'
        if values['status'] not in CONTRACT_STATUSES:
            return {'error': f'Неизвестный статус договора: {values["status"]}'}
        # originals_deadline: автоматически за 5 рабочих дней до выдачи АЗ, если не задан
        if not str(values.get('originals_deadline') or '').strip() \
                and str(values.get('opinion_deadline') or '').strip():
            values['originals_deadline'] = gen.minus_workdays(values['opinion_deadline'], 5)
        if not (values.get('subject') or '').strip():
            wt = values.get('work_type') or 'Аудит'
            values['subject'] = (f'{wt} за {values["year"]} год' if wt == 'Аудит'
                                 else f'{wt} ({values["year"]})')
        new_number = not (values.get('number') or '').strip()
        if new_number:
            values['number'] = self._suggest_number(values['year'])
            db.bump_seq_from_number(values['number'])
        duplicate = db.q1('SELECT id FROM contracts WHERE number=? AND number != "" '
                          'AND id != ?', (values['number'], cid or -1))
        if duplicate:
            return {'error': f'Договор с номером «{values["number"]}» уже существует'}

        if current and (current['number'], current['date_iso'], current['year']) != \
                (values['number'], values['date_iso'], values['year']):
            existing_doc = db.q1('SELECT id FROM documents WHERE contract_id=? LIMIT 1', (cid,))
            if existing_doc:
                return {'error': 'Номер, дата и период договора нельзя менять после создания документов: это может разорвать связи с файлами'}
        if cid:
            sets = ', '.join(f'{f}=?' for f in CONTRACT_FIELDS)
            db.ex(f'UPDATE contracts SET {sets} WHERE id=?',
                  tuple(values[f] for f in CONTRACT_FIELDS) + (cid,))
            self._log('contract_update', values['number'],
                      f'Изменён договор (id={cid}, {gen.fmt_dot(values.get("date_iso"))})')
        else:
            cols = ', '.join(['client_id'] + CONTRACT_FIELDS)
            ph = ', '.join('?' * (len(CONTRACT_FIELDS) + 1))
            cid = db.ex(f'INSERT INTO contracts({cols}, created_at) VALUES({ph}, ?)',
                        (client_id,) + tuple(values[f] for f in CONTRACT_FIELDS) + (db.now(),))
            if (values.get('city') or '') == '':
                db.ex('UPDATE contracts SET city=? WHERE id=?',
                      (db.setting('city_default', 'Красноярск'), cid))
            self._log('contract_create', values['number'],
                      f'Создан договор (id={cid}, {gen.fmt_dot(values.get("date_iso"))}'
                      + ', номер автоматически' if new_number else ')')
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
    @require_user
    def suggest_number(self, year=None):
        return self._suggest_number(year)

    # ================= склонение ФИО / сроки =================

    @api_method
    @require_user
    def inflect_fio(self, fio):
        import declension
        return declension.inflect(fio)

    @api_method
    @require_user
    def calc_originals_deadline(self, opinion_iso):
        """Дата оригиналов отчётности = 5 рабочих дней до выдачи АЗ."""
        return gen.minus_workdays(opinion_iso, 5)

    @api_method
    @require_user
    def contracts_all(self):
        return db.q("""SELECT ct.id, ct.number, ct.date_iso, ct.year, ct.status,
                              ct.client_id, c.name_short client_name, c.name_full
                       FROM contracts ct JOIN clients c ON c.id = ct.client_id
                       ORDER BY c.name_short COLLATE NOCASE, ct.date_iso DESC""")

    # ================= типы документов / шаблоны =================

    @api_method
    @require_user
    def doctypes_list(self):
        return db.q('SELECT * FROM document_types ORDER BY sort, id')

    @api_method
    @require_user
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
            self._log('doctype_update', name, f'Изменён тип документа (id={did})')
        else:
            dup = db.q1('SELECT id FROM document_types WHERE code=?', (code,))
            if dup:
                return {'error': f'Тип с кодом «{code}» уже существует'}
            did = db.ex('INSERT INTO document_types(code,name,template,filename_mask,sort,is_active)'
                        ' VALUES(?,?,?,?,?,?)', (code, name, template, mask, sort, is_active))
            self._log('doctype_create', name, f'Создан тип документа (id={did})')
        return {'ok': True, 'id': did}

    @api_method
    @require_user
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
        self._log('template_upload', src.name, 'Файл шаблона скопирован в app\\templates')
        return {'ok': True, 'name': src.name, 'path': str(dst)}

    # ================= документы =================

    @api_method
    @require_user
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
    @require_user
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

        if not (contract['number'] or '').strip():
            contract['number'] = self._suggest_number(contract['year'])
            db.ex('UPDATE contracts SET number=? WHERE id=?', (contract['number'], contract['id']))

        out = gen.render_document(client, contract, dtype, settings)
        try:
            rel = str(out.relative_to(ROOT))
        except ValueError:
            rel = str(out)

        now = db.now()
        author = self._name()
        existing = db.q1('SELECT id FROM documents WHERE contract_id=? AND type_id=?',
                         (contract_id, type_id))
        if existing:
            doc_id = existing['id']
            db.ex('UPDATE documents SET number=?, date_iso=?, status=?, file_path=?, '
                  'params_json=?, updated_at=?, updated_by=? WHERE id=?',
                  (contract['number'], contract['date_iso'], 'готов', rel,
                   json.dumps(dict(contract), ensure_ascii=False, default=str),
                   now, author, doc_id))
            note = 'перегенерирован из шаблона'
        else:
            doc_id = db.ex('INSERT INTO documents(contract_id,type_id,number,date_iso,status,'
                           'file_path,params_json,created_at,updated_at,updated_by) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (contract_id, type_id, contract['number'], contract['date_iso'],
                            'готов', rel,
                            json.dumps(dict(contract), ensure_ascii=False, default=str),
                            now, now, author))
            note = 'создан из шаблона'
        db.ex('INSERT INTO status_history(document_id,status,note,ts,author) VALUES(?,?,?,?,?)',
              (doc_id, 'готов', note, now, author))
        db.bump_seq_from_number(contract['number'])
        self._log('doc_generate', f'{dtype["name"]} {contract["number"]}',
                  f'{note}: {rel}')
        return {'ok': True, 'doc_id': doc_id, 'file': str(out), 'name': out.name,
                'number': contract['number'],
                'date_display': gen.fmt_dot(contract['date_iso'])}

    @api_method
    @require_user
    def doc_set_status(self, doc_id, status):
        if status not in STATUS_ORDER:
            return {'error': f'Неизвестный статус: {status}'}
        d = db.q1('SELECT * FROM documents WHERE id=?', (doc_id,))
        if not d:
            return {'error': 'Документ не найден'}
        now = db.now()
        author = self._name()
        db.ex('UPDATE documents SET status=?, updated_at=?, updated_by=? WHERE id=?',
              (status, now, author, doc_id))
        db.ex('INSERT INTO status_history(document_id,status,note,ts,author) VALUES(?,?,?,?,?)',
              (doc_id, status, f'статус изменён с «{d["status"]}»', now, author))
        self._log('doc_set_status', f'{d["number"]}',
                  f'Статус документа (id={doc_id}): «{d["status"]}» → «{status}»')
        return {'ok': True}

    @api_method
    @require_user
    def doc_history(self, doc_id):
        return db.q('SELECT * FROM status_history WHERE document_id=? ORDER BY id DESC', (doc_id,))

    @api_method
    @require_user
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
    @require_user
    def doc_open_folder(self, doc_id):
        d = db.q1('SELECT * FROM documents WHERE id=?', (doc_id,))
        if not d or not d['file_path']:
            return {'error': 'Файл документа не найден в базе'}
        p = (ROOT / d['file_path']).parent
        if not p.exists():
            return {'error': f'Папка не найдена: {p}'}
        open_path(p)
        return {'ok': True}

    # ================= матрица =================

    @api_method
    @require_user
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

    # ================= рабочие документы =================

    @api_method
    @require_user
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
        return _workpaper_display_rows(db.q(sql, params))

    @api_method
    @require_user
    def workpaper_save(self, data):
        data = data or {}
        title = (data.get('title') or '').strip()
        if not title:
            return {'error': 'Укажите название рабочего документа'}
        contract_id = _as_int(data, 'contract_id')
        if not contract_id:
            return {'error': 'Выберите договор'}
        if not db.q1('SELECT id FROM contracts WHERE id=?', (contract_id,)):
            return {'error': 'Выбранный договор не найден'}
        status = (data.get('status') or 'не начат').strip()
        if status not in WP_STATUSES:
            status = 'не начат'
        wid = _as_int(data, 'id')
        if data.get('due_iso') and not gen.parse_iso(data.get('due_iso')):
            return {'error': 'Плановый срок указан неверно'}
        if wid and not db.q1('SELECT id FROM workpapers WHERE id=?', (wid,)):
            return {'error': 'Рабочий документ не найден'}
        by_id, by_value = _user_maps()
        assistant_id, assistant = _resolve_user(data.get('assistant'), by_id, by_value)
        reviewer_id, reviewer = _resolve_user(data.get('reviewer'), by_id, by_value)
        vals = (contract_id, title, assistant, reviewer, assistant_id, reviewer_id,
                (data.get('due_iso') or '').strip(), status, (data.get('note') or '').strip())
        now = db.now()
        if wid:
            db.ex('UPDATE workpapers SET contract_id=?, title=?, assistant=?, reviewer=?, '
                  'assistant_id=?, reviewer_id=?, due_iso=?, status=?, note=?, updated_at=? WHERE id=?',
                  vals + (now, wid))
            self._log('workpaper_update', title, f'Изменён рабочий документ (id={wid})')
        else:
            wid = db.ex('INSERT INTO workpapers(contract_id,title,assistant,reviewer,'
                        'assistant_id,reviewer_id,due_iso,status,note,created_at,updated_at) '
                        'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                        vals + (now, now))
            self._log('workpaper_create', title,
                      f'Добавлен рабочий документ: {vals[2] or "—"} / контроль {vals[3] or "—"}')
        return {'ok': True, 'id': wid}

    @api_method
    @require_user
    def workpaper_set_status(self, wp_id, status):
        if status not in WP_STATUSES:
            return {'error': f'Неизвестный статус: {status}'}
        w = db.q1('SELECT title FROM workpapers WHERE id=?', (wp_id,))
        db.ex('UPDATE workpapers SET status=?, updated_at=? WHERE id=?',
              (status, db.now(), wp_id))
        self._log('workpaper_status', (w or {}).get('title', ''), f'Статус РД (id={wp_id}): {status}')
        return {'ok': True}

    @api_method
    @require_user
    def workpaper_delete(self, wp_id):
        w = db.q1('SELECT title FROM workpapers WHERE id=?', (wp_id,))
        db.ex('DELETE FROM workpapers WHERE id=?', (wp_id,))
        self._log('workpaper_delete', (w or {}).get('title', ''), f'Удалён РД (id={wp_id})')
        return {'ok': True}

    @api_method
    @require_user
    def workpaper_people(self):
        rows = _workpaper_display_rows(
            db.q('SELECT assistant, reviewer, assistant_id, reviewer_id FROM workpapers'))
        a = sorted({r['assistant'] for r in rows if r.get('assistant')})
        rv = sorted({r['reviewer'] for r in rows if r.get('reviewer')})
        return {'assistants': a, 'reviewers': rv}

    # ================= календарь-планер =================

    @api_method
    @require_user
    def events_list(self):
        return _event_display_rows(db.q('SELECT * FROM events ORDER BY date_iso, due_time, id'))

    @api_method
    @require_user
    def event_save(self, data):
        data = data or {}
        title = (data.get('title') or '').strip()
        if not title:
            return {'error': 'Укажите название задачи'}
        date_iso = (data.get('date_iso') or '').strip()
        if not gen.parse_iso(date_iso):
            return {'error': 'Укажите дату задачи'}
        due_time = (data.get('due_time') or '').strip()
        if due_time and (not re.fullmatch(r'\d{2}:\d{2}', due_time) or
                         not 0 <= int(due_time[:2]) <= 23 or not 0 <= int(due_time[3:]) <= 59):
            return {'error': 'Время задачи указано неверно'}
        status = (data.get('status') or 'открыта').strip()
        if status not in EVENT_STATUSES:
            status = 'открыта'
        eid = _as_int(data, 'id')
        current = None
        if eid:
            current = db.q1('SELECT * FROM events WHERE id=?', (eid,))
            if not current:
                return {'error': 'Задача не найдена'}
            is_admin = self._user.get('role') == 'admin'
            is_author = (current.get('created_by_id') == self._user.get('id') or
                         (not current.get('created_by_id') and current.get('created_by') == self._name()))
            if not is_admin and not is_author:
                return {'error': 'Изменить задачу может только её автор или администратор'}

        by_id, by_value = _user_maps()
        assignee_id, assignee = _resolve_user(data.get('assignee'), by_id, by_value)
        participant_ids = []
        participant_names = []
        for part in (data.get('participants') or '').split(','):
            uid, display = _resolve_user(part, by_id, by_value)
            if display:
                participant_names.append(display)
            if uid and uid not in participant_ids:
                participant_ids.append(uid)
        vals = (title,
                (data.get('description') or '').strip(),
                date_iso,
                due_time,
                assignee,
                ', '.join(participant_names),
                assignee_id,
                ','.join(str(uid) for uid in participant_ids),
                status)
        now = db.now()
        if eid:
            db.ex('UPDATE events SET title=?, description=?, date_iso=?, due_time=?, '
                  'assignee=?, participants=?, assignee_id=?, participant_ids=?, '
                  'status=?, updated_at=? WHERE id=?',
                  vals + (now, eid))
            self._log('event_update', title, f'Изменена задача на {gen.fmt_dot(date_iso)} (id={eid})')
        else:
            eid = db.ex('INSERT INTO events(title,description,date_iso,due_time,assignee,'
                        'participants,assignee_id,participant_ids,status,created_by,'
                        'created_by_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        vals + (self._name(), self._user['id'], now, now))
            self._log('event_create', title,
                      f'Добавлена задача на {gen.fmt_dot(date_iso)}'
                      + (f', время {due_time}' if due_time else '')
                      + (f', ответственный {assignee}' if assignee else ''))
        return {'ok': True, 'id': eid}

    @api_method
    @require_user
    def event_set_status(self, event_id, status):
        if status not in EVENT_STATUSES:
            return {'error': f'Неизвестный статус: {status}'}
        e = db.q1('SELECT title, date_iso FROM events WHERE id=?', (event_id,))
        if not e:
            return {'error': 'Задача не найдена'}
        db.ex('UPDATE events SET status=?, updated_at=? WHERE id=?', (status, db.now(), event_id))
        self._log('event_status', e['title'], f'Статус задачи (id={event_id}): {status}')
        return {'ok': True}

    @api_method
    @require_user
    def event_delete(self, event_id):
        e = db.q1('SELECT * FROM events WHERE id=?', (event_id,))
        if not e:
            return {'error': 'Задача не найдена'}
        is_author = (e.get('created_by_id') == self._user.get('id') or
                     (not e.get('created_by_id') and e.get('created_by') == self._name()))
        if self._user.get('role') != 'admin' and not is_author:
            return {'error': 'Удалить задачу может только её автор или администратор'}
        db.ex('DELETE FROM events WHERE id=?', (event_id,))
        self._log('event_delete', e['title'], f'Удалена задача (id={event_id})')
        return {'ok': True}

    # ================= репозиторий =================

    @api_method
    @require_user
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
    @require_user
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
    @require_user
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
    @require_user
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

    # ================= настройки =================

    @api_method
    @require_user
    def settings_get(self):
        s = db.settings_dict()
        s['_templates_dir'] = str(TEMPLATES_DIR)
        return s

    @api_method
    @require_user
    def settings_save(self, data):
        data = data or {}
        allowed = set(db.DEFAULT_SETTINGS.keys())
        saved = 0
        for k, v in data.items():
            if k in allowed:
                db.set_setting(k, v if v is not None else '')
                saved += 1
        self._log('settings_save', '', f'Сохранено настроек: {saved}')
        return {'ok': True, 'saved': saved}

    @api_method
    @require_user
    def templates_open_folder(self):
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        open_path(TEMPLATES_DIR)
        return {'ok': True}

    # ================= бэкап =================

    @api_method
    @require_user
    def backup_now(self):
        result = backup_service.create_backup()
        self._log('backup', '', f'Создан и проверен бэкап: {result["path"]} '
                  f'({result["size_mb"]} МБ)')
        return {'ok': True, **result}
