# -*- coding: utf-8 -*-
"""API для моста pywebview <-> интерфейс. Все методы возвращают JSON-совместимые данные.

v1.2: многопользовательский режим (логин/пароль, админ, журнал действий)
и общий планер (календарь задач).
"""
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

TASK_STATUSES = ['план', 'в работе', 'готово', 'отменена']
TASK_PRIORITIES = ['низкий', 'обычный', 'высокий']
TASK_FIELDS = ['title', 'description', 'date_iso', 'start_time', 'end_time',
               'deadline_iso', 'responsible', 'status', 'priority',
               'client_id', 'contract_id', 'color']


class AuthError(Exception):
    """Не авторизован / нет прав."""


def api_method(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except AuthError as e:
            return {'error': str(e) or 'Требуется вход в систему'}
        except Exception as e:
            traceback.print_exc()
            return {'error': f'{type(e).__name__}: {e}'}
    return wrapper


def needs_auth(fn):
    """Метод доступен только вошедшему пользователю."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self.user:
            raise AuthError('Требуется вход в систему')
        return fn(self, *args, **kwargs)
    return wrapper


def admin_only(fn):
    """Метод доступен только администратору."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self.user:
            raise AuthError('Требуется вход в систему')
        if self.user['role'] != 'admin':
            raise AuthError('Раздел доступен только администратору')
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


class Api:
    def __init__(self):
        self._window = None
        self._token = None      # токен текущей сессии (frontend -> backend)
        self.user = None        # строка из таблицы users
        db.init_db()

    def set_window(self, window):
        self._window = window

    # ---------- авторизация / пользователи / журнал ----------

    def _who(self):
        """Кто выполняет действие — для граф 'кто' и журнала."""
        if self.user:
            return self.user['login']
        return 'неизвестный'

    def _display_name(self):
        """Отображаемое имя текущего пользователя (ФИО или логин)."""
        if not self.user:
            return 'неизвестный'
        return self.user['name'] or self.user['login']

    def _log(self, action, entity='', entity_id='', details=''):
        if not self.user:
            return
        try:
            db.audit(self.user['id'], self.user['login'], action,
                     entity, entity_id, details)
        except Exception:
            traceback.print_exc()

    def set_session(self, token):
        """Frontend передаёт токен сессии при каждом вызове."""
        tok = (token or '').strip() if isinstance(token, str) else ''
        self._token = tok or None
        self.user = db.session_user(self._token) if self._token else None

    @api_method
    def login(self, data):
        data = data or {}
        if isinstance(data, str):
            data = {'login': data, 'password': ''}
        login = str(data.get('login') or '').strip().lower()
        password = str(data.get('password') or '')
        user = db.user_by_login(login)
        if not user or not db.verify_password(password, user['password_hash']):
            return {'error': 'Неверный логин или пароль'}
        if not user['is_active']:
            return {'error': 'Учётная запись отключена. Обратитесь к администратору.'}
        self.user = user
        token = db.create_session(user['id'])
        self._token = token
        db.ex('UPDATE users SET last_login=? WHERE id=?', (db.now(), user['id']))
        db.audit(user['id'], user['login'], 'login', 'session', '', 'вход в приложение')
        return {'ok': True, 'token': token, 'user': self._user_public(user)}

    @api_method
    def logout(self):
        if self.user and self._token:
            db.audit(self.user['id'], self.user['login'], 'logout', 'session', '', 'выход')
            db.ex('DELETE FROM sessions WHERE token=?', (self._token,))
        self._token = None
        self.user = None
        return {'ok': True}

    def _user_public(self, u):
        return {'id': u['id'], 'login': u['login'], 'name': u['name'],
                'role': u['role'], 'is_admin': u['role'] == 'admin'}

    @api_method
    def session_get(self, token=None):
        """Проверка сохранённой сессии при запуске окна."""
        if token is not None:
            self.set_session(token)
        if not self.user:
            return {'authed': False}
        return {'authed': True, 'user': self._user_public(self.user),
                'token': self._token}

    @api_method
    def change_own_password(self, data):
        data = data or {}
        if not self.user:
            return {'error': 'Требуется вход в систему'}
        old = str(data.get('old') or '')
        new = str(data.get('new') or '')
        if not db.verify_password(old, self.user['password_hash']):
            return {'error': 'Текущий пароль указан неверно'}
        if len(new) < 4:
            return {'error': 'Новый пароль слишком короткий (минимум 4 символа)'}
        db.ex('UPDATE users SET password_hash=? WHERE id=?',
              (db.hash_password(new), self.user['id']))
        self._log('password_change_own', 'user', self.user['id'], 'смена собственного пароля')
        return {'ok': True}

    @needs_auth
    def users_options(self):
        """Список активных пользователей — для планера (ответственный/участники)."""
        rows = db.q("SELECT login, name FROM users WHERE is_active=1 ORDER BY name COLLATE NOCASE")
        return [{'v': r['login'], 'l': (r['name'] or r['login']) + ' (' + r['login'] + ')'}
                for r in rows]

    @admin_only
    def users_list(self):
        rows = db.q("""SELECT u.id, u.login, u.name, u.role, u.is_active, u.note,
                              u.created_at, u.last_login,
                              (SELECT MAX(ts) FROM audit_log a WHERE a.user_id=u.id) last_action,
                              (SELECT COUNT(*) FROM audit_log a WHERE a.user_id=u.id) actions,
                              (SELECT COUNT(*) FROM tasks t WHERE t.responsible=u.login) open_tasks
                       FROM users u ORDER BY (u.role='admin') DESC, u.login""")
        online = {r['user_id'] for r in db.q(
            "SELECT user_id FROM sessions WHERE last_seen >= datetime('now','localtime','-30 minutes')")}
        for r in rows:
            r['online'] = r['id'] in online
        return rows

    @admin_only
    def user_save(self, data):
        data = data or {}
        login = str(data.get('login') or '').strip().lower()
        name = str(data.get('name') or '').strip()
        password = str(data.get('password') or '')
        role = 'admin' if str(data.get('role') or '') == 'admin' else 'user'
        note = str(data.get('note') or '').strip()
        uid = _as_int(data, 'id')
        if not re.match(r'^[a-z0-9._-]{2,32}$', login):
            return {'error': 'Логин: 2–32 символа, латиница, цифры, точка, прочерк'}
        dup = db.q1('SELECT id FROM users WHERE lower(login)=? AND id!=?', (login, uid or -1))
        if dup:
            return {'error': f'Логин «{login}» уже занят'}
        if uid:
            u = db.user_by_id(uid)
            if not u:
                return {'error': 'Пользователь не найден'}
            if u['login'] == 'admin' and login != 'admin':
                return {'error': 'Логин администратора менять нельзя'}
            sets = 'login=?, name=?, role=?, note=?'
            vals = [login, name, role, note]
            if password:
                if len(password) < 4:
                    return {'error': 'Пароль слишком короткий (минимум 4 символа)'}
                sets += ', password_hash=?'
                vals.append(db.hash_password(password))
            vals.append(uid)
            db.ex(f'UPDATE users SET {sets} WHERE id=?', tuple(vals))
            if password or role != u['role']:
                db.drop_sessions(uid)  # завершить активные сессии
            self._log('user_update', 'user', uid, f'{login}')
            return {'ok': True, 'id': uid}
        if len(password) < 4:
            return {'error': 'Задайте пароль (минимум 4 символа)'}
        uid = db.ex('INSERT INTO users(login,name,password_hash,role,is_active,note,created_at)'
                    ' VALUES(?,?,?,?,1,?,?)',
                    (login, name, db.hash_password(password), role, note, db.now()))
        self._log('user_create', 'user', uid, f'{login} ({name})')
        return {'ok': True, 'id': uid}

    @admin_only
    def user_set_active(self, uid, active):
        u = db.user_by_id(uid)
        if not u:
            return {'error': 'Пользователь не найден'}
        if u['login'] == 'admin':
            return {'error': 'Администратора нельзя отключить'}
        val = 1 if active in (1, '1', True, 'true', 'on') else 0
        db.ex('UPDATE users SET is_active=? WHERE id=?', (val, uid))
        if not val:
            db.drop_sessions(uid)
        self._log('user_block' if not val else 'user_unblock', 'user', uid, u['login'])
        return {'ok': True}

    @admin_only
    def user_reset_password(self, uid, password):
        u = db.user_by_id(uid)
        if not u:
            return {'error': 'Пользователь не найден'}
        password = str(password or '')
        if len(password) < 4:
            return {'error': 'Пароль слишком короткий (минимум 4 символа)'}
        db.ex('UPDATE users SET password_hash=? WHERE id=?', (db.hash_password(password), uid))
        db.drop_sessions(uid)
        self._log('user_reset_password', 'user', uid, u['login'])
        return {'ok': True}

    @admin_only
    def user_delete(self, uid):
        u = db.user_by_id(uid)
        if not u:
            return {'error': 'Пользователь не найден'}
        if u['login'] == 'admin':
            return {'error': 'Нельзя удалить администратора'}
        db.ex('DELETE FROM users WHERE id=?', (uid,))
        db.drop_sessions(uid)
        self._log('user_delete', 'user', uid, u['login'])
        return {'ok': True}

    @admin_only
    def audit_list(self, data=None):
        data = data or {}
        login = str(data.get('login') or '').strip()
        date_from = str(data.get('date_from') or '').strip()
        date_to = str(data.get('date_to') or '').strip()
        q_text = str(data.get('q') or '').strip().lower()
        limit = min(_as_int(data, 'limit', 500) or 500, 5000)
        sql = 'SELECT * FROM audit_log WHERE 1=1'
        params = []
        if login:
            sql += ' AND login=?'
            params.append(login)
        if date_from:
            sql += " AND ts >= ?"
            params.append(date_from + ' 00:00:00')
        if date_to:
            sql += " AND ts <= ?"
            params.append(date_to + ' 23:59:59')
        sql += ' ORDER BY id DESC LIMIT ?'
        params.append(limit * 3)
        rows = db.q(sql, tuple(params))
        if q_text:
            rows = [r for r in rows if q_text in
                    f"{r['ts']} {r['login']} {r['action']} {r['entity']} {r['details']}".lower()]
        return rows[:limit]

    @admin_only
    def audit_visits(self, days=30):
        """Посещения: входы/выходы по дням и пользователям."""
        days = _as_int({'d': days}, 'd', 30) or 30
        return db.q(f"""SELECT login, substr(ts,1,10) day,
                               SUM(action='login') logins,
                               SUM(action='logout') logouts,
                               COUNT(*) actions
                        FROM audit_log
                        WHERE ts >= datetime('now','localtime','-{int(days)} days')
                        GROUP BY login, day ORDER BY day DESC, login""")

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
            'task_statuses': TASK_STATUSES,
            'task_priorities': TASK_PRIORITIES,
            'counts': {
                'clients': db.q1('SELECT COUNT(*) c FROM clients')['c'],
                'contracts': db.q1('SELECT COUNT(*) c FROM contracts')['c'],
                'documents': db.q1('SELECT COUNT(*) c FROM documents')['c'],
            },
        }

    # ---------- планер (календарь задач) ----------

    def _task_row(self, tid):
        t = db.q1('SELECT * FROM tasks WHERE id=?', (tid,))
        if t:
            t['participants'] = [r['login'] for r in db.q(
                'SELECT login FROM task_participants WHERE task_id=? ORDER BY id', (tid,))]
        return t

    @api_method
    @needs_auth
    def tasks_list(self, data=None):
        """Список задач за период + участники + имена пользователей."""
        data = data or {}
        date_from = str(data.get('from') or '')[:10]
        date_to = str(data.get('to') or '')[:10]
        sql = """SELECT t.*, c.name_short client_name, ct.number contract_number
                 FROM tasks t
                 LEFT JOIN clients c ON c.id = t.client_id
                 LEFT JOIN contracts ct ON ct.id = t.contract_id WHERE 1=1"""
        params = []
        if date_from:
            sql += ' AND (t.date_iso >= ? OR t.deadline_iso >= ?)'
            params += [date_from, date_from]
        if date_to:
            sql += ' AND (t.date_iso <= ? OR IFNULL(t.deadline_iso,\'\') <= ?)'
            params += [date_to, date_to]
        rows = db.q(sql + ' ORDER BY t.date_iso, t.start_time, t.id', tuple(params))
        names = {r['login']: (r['name'] or r['login']) for r in
                 db.q('SELECT login, name FROM users')}
        for t in rows:
            t['participants'] = [r['login'] for r in db.q(
                'SELECT login FROM task_participants WHERE task_id=? ORDER BY id', (t['id'],))]
            t['responsible_name'] = names.get(t['responsible'], t['responsible'])
            t['created_by_name'] = names.get(t['created_by'], t['created_by'])
            t['participant_names'] = [names.get(p, p) for p in t['participants']]
        return rows

    @api_method
    @needs_auth
    def task_get(self, tid):
        t = self._task_row(tid)
        return t or {'error': 'Задача не найдена'}

    @api_method
    @needs_auth
    def task_save(self, data):
        data = data or {}
        title = str(data.get('title') or '').strip()
        if not title:
            return {'error': 'Укажите название задачи'}
        date_iso = str(data.get('date_iso') or '').strip()[:10]
        if date_iso and not re.match(r'^\d{4}-\d{2}-\d{2}$', date_iso):
            return {'error': 'Некорректная дата задачи'}
        status = str(data.get('status') or 'план').strip()
        if status not in TASK_STATUSES:
            status = 'план'
        priority = str(data.get('priority') or 'обычный').strip()
        if priority not in TASK_PRIORITIES:
            priority = 'обычный'
        for key in ('start_time', 'end_time'):
            v = str(data.get(key) or '').strip()
            if v and not re.match(r'^\d{1,2}:\d{2}', v):
                return {'error': f'Некорректное время в поле «{key}»'}
        vals = {}
        for f in TASK_FIELDS:
            v = data.get(f)
            if f in ('client_id', 'contract_id'):
                vals[f] = _as_int(data, f)
            else:
                vals[f] = str(v if v is not None else '').strip()
        vals['status'] = status
        vals['priority'] = priority
        participants = data.get('participants') or []
        if isinstance(participants, str):
            participants = [p.strip() for p in participants.split(',') if p.strip()]
        now = db.now()
        tid = _as_int(data, 'id')
        if tid:
            old = self._task_row(tid)
            if not old:
                return {'error': 'Задача не найдена'}
            sets = ', '.join(f'{f}=?' for f in TASK_FIELDS)
            db.ex(f'UPDATE tasks SET {sets}, updated_at=?, updated_by=? WHERE id=?',
                  tuple(vals[f] for f in TASK_FIELDS) + (now, self._display_name(), tid))
            self._log('task_update', 'task', tid, title)
        else:
            cols = ', '.join(TASK_FIELDS)
            ph = ', '.join('?' * len(TASK_FIELDS))
            tid = db.ex(
                f'INSERT INTO tasks({cols}, created_by, updated_by, created_at, updated_at)'
                f' VALUES({ph},?,?,?,?)',
                tuple(vals[f] for f in TASK_FIELDS)
                + (self._display_name(), self._display_name(), now, now))
            self._log('task_create', 'task', tid, title)
        db.ex('DELETE FROM task_participants WHERE task_id=?', (tid,))
        seen = set()
        for p in participants:
            p = str(p).strip()
            if p and p not in seen:
                seen.add(p)
                db.ex('INSERT INTO task_participants(task_id,login) VALUES(?,?)', (tid, p))
        return {'ok': True, 'id': tid, 'task': self._task_row(tid)}

    @api_method
    @needs_auth
    def task_set_status(self, tid, status):
        if status not in TASK_STATUSES:
            return {'error': f'Неизвестный статус задачи: {status}'}
        t = db.q1('SELECT title FROM tasks WHERE id=?', (tid,))
        if not t:
            return {'error': 'Задача не найдена'}
        db.ex('UPDATE tasks SET status=?, updated_at=?, updated_by=? WHERE id=?',
              (status, db.now(), self._display_name(), tid))
        self._log('task_status', 'task', tid, f'{t["title"]} -> {status}')
        return {'ok': True}

    @api_method
    @needs_auth
    def task_delete(self, tid):
        t = db.q1('SELECT title FROM tasks WHERE id=?', (tid,))
        if not t:
            return {'error': 'Задача не найдена'}
        db.ex('DELETE FROM task_participants WHERE task_id=?', (tid,))
        db.ex('DELETE FROM tasks WHERE id=?', (tid,))
        self._log('task_delete', 'task', tid, t['title'])
        return {'ok': True}

    # ---------- клиенты ----------

    @api_method
    @needs_auth
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
    @needs_auth
    def client_get(self, cid):
        client = db.q1('SELECT * FROM clients WHERE id=?', (cid,))
        if not client:
            return {'error': 'Клиент не найден'}
        contacts = db.q('SELECT * FROM contacts WHERE client_id=? ORDER BY id', (cid,))
        contracts = db.q('SELECT * FROM contracts WHERE client_id=? '
                         'ORDER BY date_iso DESC, id DESC', (cid,))
        return {'client': client, 'contacts': contacts, 'contracts': contracts}

    @api_method
    @needs_auth
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
            self._log('client_update', 'client', cid, name_short or name_full)
        else:
            cols = ', '.join(CLIENT_FIELDS)
            ph = ', '.join('?' * len(CLIENT_FIELDS))
            cid = db.ex(f'INSERT INTO clients({cols}, created_at) VALUES({ph}, ?)',
                        tuple(values[f] for f in CLIENT_FIELDS) + (db.now(),))
            self._log('client_create', 'client', cid, name_short or name_full)
        folder_path = gen.client_folder_path(values)
        folder_path.mkdir(parents=True, exist_ok=True)
        return {'ok': True, 'id': cid, 'folder': str(folder_path)}

    @api_method
    @needs_auth
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
    @needs_auth
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
            self._log('contact_update', 'contact', cid, values.get('fio'))
        else:
            cols = ', '.join(['client_id'] + CONTACT_FIELDS)
            ph = ', '.join('?' * (len(CONTACT_FIELDS) + 1))
            cid = db.ex(f'INSERT INTO contacts({cols}) VALUES({ph})',
                        (client_id,) + tuple(values[f] for f in CONTACT_FIELDS))
            self._log('contact_create', 'contact', cid, values.get('fio'))
        return {'ok': True, 'id': cid}

    @api_method
    @needs_auth
    def contact_delete(self, contact_id):
        c = db.q1('SELECT * FROM contacts WHERE id=?', (contact_id,))
        db.ex('DELETE FROM contacts WHERE id=?', (contact_id,))
        self._log('contact_delete', 'contact', contact_id, (c or {}).get('fio', ''))
        return {'ok': True}

    # ---------- договоры ----------

    @api_method
    @needs_auth
    def contract_get(self, contract_id):
        return db.q1('SELECT * FROM contracts WHERE id=?', (contract_id,)) or \
            {'error': 'Договор не найден'}

    @api_method
    @needs_auth
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
            self._log('contract_update', 'contract', cid, values['number'])
        else:
            cols = ', '.join(['client_id'] + CONTRACT_FIELDS)
            ph = ', '.join('?' * (len(CONTRACT_FIELDS) + 1))
            cid = db.ex(f'INSERT INTO contracts({cols}, created_at) VALUES({ph}, ?)',
                        (client_id,) + tuple(values[f] for f in CONTRACT_FIELDS) + (db.now(),))
            self._log('contract_create', 'contract', cid, values['number'])
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
    @needs_auth
    def suggest_number(self, year=None):
        return self._suggest_number(year)

    # ---------- склонение ФИО / сроки ----------

    @api_method
    @needs_auth
    def inflect_fio(self, fio):
        import declension
        return declension.inflect(fio)

    @api_method
    @needs_auth
    def calc_originals_deadline(self, opinion_iso):
        """Дата оригиналов отчётности = 5 рабочих дней до выдачи АЗ."""
        return gen.minus_workdays(opinion_iso, 5)

    @api_method
    @needs_auth
    def contracts_all(self):
        return db.q("""SELECT ct.id, ct.number, ct.date_iso, ct.year, ct.status,
                              ct.client_id, c.name_short client_name, c.name_full
                       FROM contracts ct JOIN clients c ON c.id = ct.client_id
                       ORDER BY c.name_short COLLATE NOCASE, ct.date_iso DESC""")

    # ---------- рабочие документы (таблица готовности) ----------

    @api_method
    @needs_auth
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
    @needs_auth
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
            self._log('workpaper_update', 'workpaper', wid, title)
        else:
            wid = db.ex('INSERT INTO workpapers(contract_id,title,assistant,reviewer,'
                        'due_iso,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
                        vals + (now, now))
            self._log('workpaper_create', 'workpaper', wid, title)
        return {'ok': True, 'id': wid}

    @api_method
    @needs_auth
    def workpaper_set_status(self, wp_id, status):
        if status not in WP_STATUSES:
            return {'error': f'Неизвестный статус: {status}'}
        r = db.ex('UPDATE workpapers SET status=?, updated_at=? WHERE id=?',
                  (status, db.now(), wp_id))
        self._log('workpaper_status', 'workpaper', wp_id, status)
        return {'ok': True}

    @api_method
    @needs_auth
    def workpaper_delete(self, wp_id):
        w = db.q1('SELECT title FROM workpapers WHERE id=?', (wp_id,))
        db.ex('DELETE FROM workpapers WHERE id=?', (wp_id,))
        self._log('workpaper_delete', 'workpaper', wp_id, (w or {}).get('title', ''))
        return {'ok': True}

    @api_method
    @needs_auth
    def workpaper_people(self):
        a = [r['assistant'] for r in
             db.q("SELECT DISTINCT assistant FROM workpapers WHERE assistant != '' ORDER BY assistant")]
        rv = [r['reviewer'] for r in
              db.q("SELECT DISTINCT reviewer FROM workpapers WHERE reviewer != '' ORDER BY reviewer")]
        return {'assistants': a, 'reviewers': rv}

    # ---------- типы документов / шаблоны ----------

    @api_method
    @needs_auth
    def doctypes_list(self):
        return db.q('SELECT * FROM document_types ORDER BY sort, id')

    @api_method
    @needs_auth
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
            self._log('doctype_update', 'doctype', did, name)
        else:
            dup = db.q1('SELECT id FROM document_types WHERE code=?', (code,))
            if dup:
                return {'error': f'Тип с кодом «{code}» уже существует'}
            did = db.ex('INSERT INTO document_types(code,name,template,filename_mask,sort,is_active)'
                        ' VALUES(?,?,?,?,?,?)', (code, name, template, mask, sort, is_active))
            self._log('doctype_create', 'doctype', did, name)
        return {'ok': True, 'id': did}

    @api_method
    @needs_auth
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
    @needs_auth
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
    @needs_auth
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
        who = self._display_name()

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
                   now, who, doc_id))
            note = 'перегенерирован из шаблона'
        else:
            doc_id = db.ex('INSERT INTO documents(contract_id,type_id,number,date_iso,status,'
                           'file_path,params_json,created_at,updated_at,updated_by) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (contract_id, type_id, contract['number'], contract['date_iso'],
                            'готов', rel,
                            json.dumps(dict(contract), ensure_ascii=False, default=str),
                            now, now, who))
            note = 'создан из шаблона'
        db.ex('INSERT INTO status_history(document_id,status,note,ts,author) VALUES(?,?,?,?,?)',
              (doc_id, 'готов', note, now, who))
        db.bump_seq_from_number(contract['number'])
        self._log('doc_generate', 'document', doc_id,
                  f'{dtype["name"]} · {out.name}')
        return {'ok': True, 'doc_id': doc_id, 'file': str(out), 'name': out.name,
                'number': contract['number'],
                'date_display': gen.fmt_dot(contract['date_iso'])}

    @api_method
    @needs_auth
    def doc_set_status(self, doc_id, status):
        if status not in STATUS_ORDER:
            return {'error': f'Неизвестный статус: {status}'}
        d = db.q1('SELECT * FROM documents WHERE id=?', (doc_id,))
        if not d:
            return {'error': 'Документ не найден'}
        now = db.now()
        who = self._display_name()
        db.ex('UPDATE documents SET status=?, updated_at=?, updated_by=? WHERE id=?',
              (status, now, who, doc_id))
        db.ex('INSERT INTO status_history(document_id,status,note,ts,author) VALUES(?,?,?,?,?)',
              (doc_id, status, f'статус изменён с «{d["status"]}»', now, who))
        self._log('doc_status', 'document', doc_id, f'{d["status"]} -> {status}')
        return {'ok': True}

    @api_method
    @needs_auth
    def doc_history(self, doc_id):
        return db.q('SELECT * FROM status_history WHERE document_id=? ORDER BY id DESC', (doc_id,))

    @api_method
    @needs_auth
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
    @needs_auth
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
    @needs_auth
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
    @needs_auth
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
    @needs_auth
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
    @needs_auth
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
    @needs_auth
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
    @needs_auth
    def settings_get(self):
        s = db.settings_dict()
        s['_templates_dir'] = str(TEMPLATES_DIR)
        return s

    @api_method
    @needs_auth
    def settings_save(self, data):
        data = data or {}
        allowed = set(db.DEFAULT_SETTINGS.keys())
        saved = 0
        for k, v in data.items():
            if k in allowed:
                db.set_setting(k, v if v is not None else '')
                saved += 1
        self._log('settings_save', 'settings', '', f'полей: {saved}')
        return {'ok': True, 'saved': saved}

    @api_method
    @needs_auth
    def templates_open_folder(self):
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        open_path(TEMPLATES_DIR)
        return {'ok': True}

    # ---------- бэкап ----------

    @api_method
    @needs_auth
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
        self._log('backup', 'backup', ts, f'{round(size / 1024 / 1024, 1)} МБ')
        return {'ok': True, 'path': str(dst), 'size_mb': round(size / 1024 / 1024, 1)}
