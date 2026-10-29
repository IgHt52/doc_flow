# -*- coding: utf-8 -*-
"""База данных SQLite: схема, сиды, хелперы. Пользователи, сессии, журнал, планер."""
import hashlib
import secrets
import sqlite3
from datetime import datetime

from config import ADMIN_LOGIN, ADMIN_PASSWORD, DB_PATH, ensure_dirs, TEMPLATES_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name_full TEXT DEFAULT '', name_short TEXT DEFAULT '',
  inn TEXT DEFAULT '', kpp TEXT DEFAULT '', ogrn TEXT DEFAULT '',
  legal_address TEXT DEFAULT '', actual_address TEXT DEFAULT '',
  email TEXT DEFAULT '', phone TEXT DEFAULT '',
  bank_account TEXT DEFAULT '', bank_name TEXT DEFAULT '',
  corr_account TEXT DEFAULT '', bik TEXT DEFAULT '',
  director_position TEXT DEFAULT 'Генеральный директор',
  director_position_gen TEXT DEFAULT 'Генерального директора',
  director_fio TEXT DEFAULT '', director_fio_gen TEXT DEFAULT '',
  director_fio_short TEXT DEFAULT '',
  director_basis TEXT DEFAULT 'Устава',
  folder TEXT DEFAULT '', note TEXT DEFAULT '',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL,
  role TEXT DEFAULT 'Контактное лицо', fio TEXT DEFAULT '',
  position TEXT DEFAULT '', phone TEXT DEFAULT '', email TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS contracts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_id INTEGER NOT NULL,
  number TEXT DEFAULT '', date_iso TEXT DEFAULT '', city TEXT DEFAULT '',
  work_type TEXT DEFAULT 'Аудит', subject TEXT DEFAULT '',
  year INTEGER, price REAL DEFAULT 0, prepay_pct REAL DEFAULT 50,
  service_term TEXT DEFAULT '20',
  stage1_start TEXT DEFAULT '', stage1_end TEXT DEFAULT '',
  stage2_start TEXT DEFAULT '', stage2_end TEXT DEFAULT '',
  opinion_deadline TEXT DEFAULT '', originals_deadline TEXT DEFAULT '',
  status TEXT DEFAULT 'действует', created_at TEXT
);
CREATE TABLE IF NOT EXISTS workpapers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract_id INTEGER, title TEXT DEFAULT '',
  assistant TEXT DEFAULT '', reviewer TEXT DEFAULT '',
  due_iso TEXT DEFAULT '', status TEXT DEFAULT 'не начат',
  note TEXT DEFAULT '', created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wp_contract ON workpapers(contract_id);
CREATE TABLE IF NOT EXISTS document_types(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE, name TEXT, template TEXT,
  filename_mask TEXT DEFAULT '{type} {number} от {date}.docx',
  sort INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  contract_id INTEGER NOT NULL, type_id INTEGER NOT NULL,
  number TEXT DEFAULT '', date_iso TEXT DEFAULT '',
  status TEXT DEFAULT 'готов', file_path TEXT DEFAULT '',
  params_json TEXT DEFAULT '{}',
  created_at TEXT, updated_at TEXT, updated_by TEXT DEFAULT 'Администратор'
);
CREATE TABLE IF NOT EXISTS status_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER, status TEXT, note TEXT DEFAULT '',
  ts TEXT, author TEXT DEFAULT 'Администратор'
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_contracts_client ON contracts(client_id);
CREATE INDEX IF NOT EXISTS idx_docs_contract ON documents(contract_id);
CREATE INDEX IF NOT EXISTS idx_contacts_client ON contacts(client_id);

-- ===== пользователи, сессии, журнал, планер (v1.2) =====
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  login TEXT UNIQUE NOT NULL,
  name TEXT DEFAULT '',
  password_hash TEXT NOT NULL,
  role TEXT DEFAULT 'user',            -- 'admin' | 'user'
  is_active INTEGER DEFAULT 1,
  note TEXT DEFAULT '',
  created_at TEXT, last_login TEXT
);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created_at TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS audit_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, user_id INTEGER, login TEXT, action TEXT,
  entity TEXT DEFAULT '', entity_id TEXT DEFAULT '',
  details TEXT DEFAULT '', ip TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  date_iso TEXT DEFAULT '',            -- дата задачи YYYY-MM-DD
  start_time TEXT DEFAULT '',          -- HH:MM ('' — целый день)
  end_time TEXT DEFAULT '',            -- HH:MM / дедлайн в течение дня
  deadline_iso TEXT DEFAULT '',        -- дедлайн (дата), если отличается от даты
  responsible TEXT DEFAULT '',         -- ответственный (логин или ФИО)
  status TEXT DEFAULT 'план',          -- план | в работе | готово | отменена
  priority TEXT DEFAULT 'обычный',     -- низкий | обычный | высокий
  client_id INTEGER, contract_id INTEGER,
  color TEXT DEFAULT '',
  created_by TEXT DEFAULT '', updated_by TEXT DEFAULT '',
  created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS task_participants(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL, login TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_date ON tasks(date_iso);
CREATE INDEX IF NOT EXISTS idx_task_part_task ON task_participants(task_id);
"""

DEFAULT_SETTINGS = {
    'executor_name_full': 'Общество с ограниченной ответственностью «Сибаудит инициатива»',
    'executor_name_short': 'ООО «САИ»',
    'executor_sro_ornz': '12006011935',
    'executor_legal_address': '660028, г. Красноярск, ул. Баумана, д. 3, пом. 43',
    'executor_inn': '2463105364',
    'executor_kpp': '246301001',
    'executor_bank_account': '40702810431710003008',
    'executor_bank_name': 'в Красноярском отделении № 8646 ПАО Сбербанк, г. Красноярск',
    'executor_corr_account': '30101810800000000627',
    'executor_bik': '040407627',
    'executor_signer_position': 'Заместитель генерального директора',
    'executor_signer_position_gen': 'Заместителя генерального директора',
    'executor_signer_fio': 'Огородников Сергей Алексеевич',
    'executor_signer_fio_gen': 'Огородникова Сергея Алексеевича',
    'executor_signer_fio_short': 'С.А. Огородников',
    'executor_signer_basis': 'Доверенности № 205 от 14.10.2025 г.',
    'executor_insurance': 'в ООО «Страховая Компания «Согласие» (Полис страхования профессиональной ответственности аудиторов № 2012043-1283782/26 ПОА от 04.06.2026, действует до 18.06.2027)',
    'city_default': 'Красноярск',
    'num_mask': '{seq}/{yy}-А',
    'seq_next': '193',
}

DEFAULT_DOCTYPES = [
    ('dogovor', 'Договор', 'Договор_аудит.docx', 'Договор {number} от {date}.docx', 1, 1),
    ('kp', 'Коммерческое предложение', '', 'КП {number} от {date}.docx', 2, 0),
    ('zapros', 'Запрос документов', '', 'Запрос {number} от {date}.docx', 3, 0),
    ('pismo_predst', 'Письмо-представление', '', 'Письмо-представление {number} от {date}.docx', 4, 0),
    ('zaklyuchenie', 'Аудиторское заключение', '', 'Аудиторское заключение {number} от {date}.docx', 5, 0),
    ('akt', 'Акт об оказании услуг', '', 'Акт {number} от {date}.docx', 6, 0),
]


def now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ---------- пароли и пользователи ----------

PBKDF2_ITERATIONS = 120000


def hash_password(password):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'),
                             bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f'pbkdf2${PBKDF2_ITERATIONS}${salt}${dk.hex()}'


def verify_password(password, stored):
    try:
        _, iters, salt, hex_dk = str(stored).split('$')
        dk = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'),
                                 bytes.fromhex(salt), int(iters))
        return secrets.compare_digest(dk.hex(), hex_dk)
    except (ValueError, TypeError):
        return False


def user_by_login(login):
    return q1('SELECT * FROM users WHERE lower(login)=lower(?)', ((login or '').strip(),))


def user_by_id(uid):
    return q1('SELECT * FROM users WHERE id=?', (uid,))


def create_session(user_id):
    token = secrets.token_urlsafe(32)
    ex('INSERT INTO sessions(token,user_id,created_at,last_seen) VALUES(?,?,?,?)',
       (token, user_id, now(), now()))
    return token


def session_user(token):
    if not token:
        return None
    s = q1('SELECT u.* FROM sessions ss JOIN users u ON u.id=ss.user_id WHERE ss.token=?',
           (token,))
    if s and not s['is_active']:
        return None
    if token:
        ex('UPDATE sessions SET last_seen=? WHERE token=?', (now(), token))
    return s


def drop_sessions(user_id):
    ex('DELETE FROM sessions WHERE user_id=?', (user_id,))


def audit(user_id, login, action, entity='', entity_id='', details=''):
    """Запись в журнал посещения/действий приложения."""
    ex('INSERT INTO audit_log(ts,user_id,login,action,entity,entity_id,details)'
       ' VALUES(?,?,?,?,?,?,?)',
       (now(), user_id, login or '', action, entity or '', str(entity_id or ''),
        str(details or '')[:500]))


def ensure_admin(c):
    """Создаёт/чинит учётную запись администратора."""
    row = c.execute('SELECT id FROM users WHERE lower(login)=lower(?)',
                    (ADMIN_LOGIN,)).fetchone()
    if not row:
        c.execute('INSERT INTO users(login,name,password_hash,role,is_active,created_at)'\
                  ' VALUES(?,?,?,?,1,?)',
                  (ADMIN_LOGIN, 'Администратор', hash_password(ADMIN_PASSWORD),
                   'admin', now()))
    else:
        # если пароль админа был испорчен — не трогаем; роль всегда admin
        c.execute("UPDATE users SET role='admin', is_active=1 WHERE id=?", (row['id'],))


def conn():
    ensure_dirs()
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def q(sql, params=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def q1(sql, params=()):
    rows = q(sql, params)
    return rows[0] if rows else None


def ex(sql, params=()):
    with conn() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def init_db():
    ensure_dirs()
    with conn() as c:
        c.executescript(SCHEMA)
        # настройки — только отсутствующие
        have = {r['key'] for r in c.execute('SELECT key FROM settings')}
        for k, v in DEFAULT_SETTINGS.items():
            if k not in have:
                c.execute('INSERT INTO settings(key, value) VALUES(?,?)', (k, v))
        # типы документов — только отсутствующие коды
        have_t = {r['code'] for r in c.execute('SELECT code FROM document_types')}
        for code, name, tpl, mask, sort, act in DEFAULT_DOCTYPES:
            if code not in have_t:
                c.execute(
                    'INSERT INTO document_types(code,name,template,filename_mask,sort,is_active)'
                    ' VALUES(?,?,?,?,?,?)', (code, name, tpl, mask, sort, act))
        _migrate(c)
        ensure_admin(c)
        c.commit()


def _migrate(c):
    """Миграции базы при обновлении приложения."""
    # v1.1: колонка service_term в договорах
    cols = {r[1] for r in c.execute('PRAGMA table_info(contracts)')}
    if 'service_term' not in cols:
        c.execute("ALTER TABLE contracts ADD COLUMN service_term TEXT DEFAULT '20'")
        c.execute("UPDATE contracts SET service_term='20' WHERE service_term IS NULL OR service_term=''")
    # v1.1: шаблон договора переименован (без «2 этапа»)
    row = c.execute("SELECT id, template FROM document_types WHERE code='dogovor'").fetchone()
    if row and row['template'] == 'Договор_аудит_2этапа.docx' \
            and (TEMPLATES_DIR / 'Договор_аудит.docx').exists():
        c.execute("UPDATE document_types SET template='Договор_аудит.docx' WHERE id=?", (row['id'],))
    # v1.1: акты «этап 1/2» -> единый «Акт об оказании услуг» (если старыми не пользовались)
    used = c.execute(
        "SELECT COUNT(*) n FROM documents d JOIN document_types t ON t.id=d.type_id "
        "WHERE t.code IN ('akt1','akt2')").fetchone()['n']
    has_tpl = c.execute(
        "SELECT COUNT(*) n FROM document_types WHERE code IN ('akt1','akt2') "
        "AND template IS NOT NULL AND template != ''").fetchone()['n']
    if used == 0 and has_tpl == 0 and \
            c.execute("SELECT 1 FROM document_types WHERE code IN ('akt1','akt2')").fetchone():
        c.execute("DELETE FROM document_types WHERE code IN ('akt1','akt2')")
        if not c.execute("SELECT 1 FROM document_types WHERE code='akt'").fetchone():
            c.execute("INSERT INTO document_types(code,name,template,filename_mask,sort,is_active)"
                      " VALUES('akt','Акт об оказании услуг','','Акт {number} от {date}.docx',6,0)")


def settings_dict():
    return {r['key']: r['value'] for r in q('SELECT key, value FROM settings')}


def setting(key, default=''):
    v = q1('SELECT value FROM settings WHERE key=?', (key,))
    return v['value'] if v else default


def set_setting(key, value):
    ex('INSERT INTO settings(key,value) VALUES(?,?) '
       'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))


def bump_seq_from_number(number):
    """Если номер договора содержит seq больше текущего seq_next-1 — поднять счётчик."""
    import re
    m = re.match(r'^\s*(\d+)', number or '')
    if not m:
        return
    seq = int(m.group(1))
    try:
        cur = int(setting('seq_next', '1'))
    except ValueError:
        cur = 1
    if seq >= cur:
        set_setting('seq_next', seq + 1)


if __name__ == '__main__':
    init_db()
    print('DB init ok:', DB_PATH)
    print('settings keys:', len(settings_dict()))
    print('doctypes:', [r['name'] for r in q('SELECT name FROM document_types ORDER BY sort')])
