# -*- coding: utf-8 -*-
"""Конфигурация путей и констант DocFlow."""
import os
from pathlib import Path

APP_VERSION = '1.2.0'

def _root():
    env = os.environ.get('DOCFLOW_ROOT')
    if env:
        return Path(env)
    # .../DocFlow/app/config.py -> .../DocFlow
    return Path(__file__).resolve().parents[1]

ROOT = _root()
APP_DIR = ROOT / 'app'
DATA_DIR = ROOT / 'data'
CLIENTS_DIR = ROOT / 'clients'
BACKUPS_DIR = ROOT / 'backups'
TEMPLATES_DIR = APP_DIR / 'templates'
UI_DIR = APP_DIR / 'ui'
DB_PATH = DATA_DIR / 'docflow.db'

STATUS_ORDER = ['готов', 'подписан', 'отправлен', 'получен']
WP_STATUSES = ['не начат', 'в работе', 'на проверке', 'доработка', 'принят']

def ensure_dirs():
    for d in (DATA_DIR, CLIENTS_DIR, BACKUPS_DIR, TEMPLATES_DIR):
        d.mkdir(parents=True, exist_ok=True)
