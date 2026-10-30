# -*- coding: utf-8 -*-
"""Надёжное резервное копирование и восстановление DocFlow."""
import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from config import (APP_VERSION, BACKUPS_DIR, CLIENTS_DIR, DATA_DIR, DB_PATH,
                    TEMPLATES_DIR, ensure_dirs)
import db


BACKUP_RETENTION_DAYS = 30


def _timestamp():
    return datetime.now().strftime('%Y-%m-%d_%H%M%S')


def _new_backup_dir():
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    base = BACKUPS_DIR / _timestamp()
    candidate = base
    n = 2
    while candidate.exists():
        candidate = BACKUPS_DIR / f'{base.name}_{n:02d}'
        n += 1
    candidate.mkdir(parents=True)
    return candidate


def _copy_tree(src, dst):
    src = Path(src)
    if src.exists():
        shutil.copytree(str(src), str(dst), dirs_exist_ok=True)


def _backup_sqlite(dst):
    if not DB_PATH.exists():
        db.init_db()
    src = sqlite3.connect(str(DB_PATH))
    out = sqlite3.connect(str(dst))
    try:
        with out:
            src.backup(out)
    finally:
        out.close()
        src.close()


def verify_backup(path):
    """Проверяет, что в копии есть база и она не повреждена."""
    path = Path(path)
    db_file = path / 'docflow.db'
    if not db_file.exists():
        return {'ok': False, 'error': 'В копии отсутствует docflow.db'}
    try:
        c = sqlite3.connect(f'file:{db_file.resolve().as_posix()}?mode=ro', uri=True)
        result = c.execute('PRAGMA integrity_check').fetchone()[0]
        c.close()
        if result != 'ok':
            return {'ok': False, 'error': f'Проверка SQLite завершилась: {result}'}
    except Exception as exc:
        return {'ok': False, 'error': f'Не удалось проверить базу: {exc}'}
    if not (path / 'clients').exists():
        return {'ok': False, 'error': 'В копии отсутствует папка clients'}
    if not (path / 'templates').exists():
        return {'ok': False, 'error': 'В копии отсутствует папка templates'}
    return {'ok': True}


def rotate_backups(days=BACKUP_RETENTION_DAYS):
    """Удаляет только старые каталоги резервных копий."""
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now() - timedelta(days=days)
    removed = []
    root = BACKUPS_DIR.resolve()
    for item in BACKUPS_DIR.iterdir():
        if not item.is_dir() or item.name.startswith('.'):
            continue
        if datetime.fromtimestamp(item.stat().st_mtime) >= cutoff:
            continue
        resolved = item.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        shutil.rmtree(resolved)
        removed.append(str(resolved))
    return removed


def create_backup(rotate=True):
    """Создаёт согласованную копию базы, документов и шаблонов."""
    ensure_dirs()
    dst = _new_backup_dir()
    try:
        _backup_sqlite(dst / 'docflow.db')
        _copy_tree(CLIENTS_DIR, dst / 'clients')
        _copy_tree(TEMPLATES_DIR, dst / 'templates')
        manifest = {
            'format': 1,
            'app_version': APP_VERSION,
            'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'contents': ['docflow.db', 'clients', 'templates'],
        }
        (dst / 'manifest.json').write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        checked = verify_backup(dst)
        if not checked['ok']:
            raise RuntimeError(checked['error'])
    except Exception:
        shutil.rmtree(dst, ignore_errors=True)
        raise

    removed = rotate_backups() if rotate else []
    size = sum(f.stat().st_size for f in dst.rglob('*') if f.is_file())
    return {
        'path': str(dst),
        'size_mb': round(size / 1024 / 1024, 1),
        'removed': len(removed),
        'verified': True,
    }


def list_backups():
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    result = []
    for item in BACKUPS_DIR.iterdir():
        if not item.is_dir() or not (item / 'docflow.db').exists():
            continue
        checked = verify_backup(item)
        result.append({
            'name': item.name,
            'path': str(item),
            'mtime': datetime.fromtimestamp(item.stat().st_mtime).strftime('%d.%m.%Y %H:%M'),
            'valid': checked['ok'],
            'error': checked.get('error', ''),
        })
    return sorted(result, key=lambda x: x['mtime'], reverse=True)


def _inside_backups(path):
    root = BACKUPS_DIR.resolve()
    candidate = Path(path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError('Путь резервной копии находится вне папки backups')
    if candidate == root or not candidate.is_dir():
        raise ValueError('Каталог резервной копии не найден')
    return candidate


def restore_backup(path):
    """Восстанавливает выбранную копию. Вызывать только при закрытом DocFlow."""
    ensure_dirs()
    source = _inside_backups(path)
    checked = verify_backup(source)
    if not checked['ok']:
        raise ValueError(checked['error'])

    db_file = DATA_DIR / 'docflow.db'
    for suffix in ('-wal', '-shm'):
        stale = Path(str(db_file) + suffix)
        if stale.exists():
            try:
                stale.unlink()
            except PermissionError as exc:
                raise RuntimeError(
                    'База данных занята. Полностью закройте DocFlow и повторите восстановление.') from exc
    shutil.copy2(str(source / 'docflow.db'), str(db_file))

    if CLIENTS_DIR.exists():
        shutil.rmtree(CLIENTS_DIR)
    if TEMPLATES_DIR.exists():
        shutil.rmtree(TEMPLATES_DIR)
    shutil.copytree(str(source / 'clients'), str(CLIENTS_DIR), dirs_exist_ok=True)
    shutil.copytree(str(source / 'templates'), str(TEMPLATES_DIR), dirs_exist_ok=True)
    return {'ok': True, 'path': str(source)}


def _interactive_restore():
    backups = list_backups()
    if not backups:
        print('Резервных копий пока нет.')
        return 1
    print('Доступные резервные копии:')
    for i, item in enumerate(backups, 1):
        mark = 'OK' if item['valid'] else 'ОШИБКА'
        print(f'{i}. {item["name"]} · {item["mtime"]} · {mark}')
    try:
        choice = int(input('Введите номер копии для восстановления (0 — отмена): '))
    except ValueError:
        print('Нужно ввести число.')
        return 1
    if choice == 0:
        print('Восстановление отменено.')
        return 0
    if not 1 <= choice <= len(backups) or not backups[choice - 1]['valid']:
        print('Выбрана недействительная копия.')
        return 1
    selected = backups[choice - 1]
    print('\nВНИМАНИЕ: DocFlow должен быть полностью закрыт.')
    print('Текущие база, клиенты и шаблоны будут заменены выбранной копией.')
    if input('Для продолжения введите ВОССТАНОВИТЬ: ').strip() != 'ВОССТАНОВИТЬ':
        print('Восстановление отменено.')
        return 0
    print('Сначала создаю аварийную копию текущего состояния…')
    emergency = create_backup(rotate=False)
    print('Аварийная копия:', emergency['path'])
    restore_backup(selected['path'])
    print('Восстановление завершено. Можно снова запускать DocFlow.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Резервное копирование DocFlow')
    parser.add_argument('command', nargs='?', choices=('create', 'list', 'restore'), default='create')
    parser.add_argument('path', nargs='?')
    parser.add_argument('--silent', action='store_true')
    args = parser.parse_args()

    if args.command == 'create':
        result = create_backup()
        print(f'Backup created: {result["path"]} ({result["size_mb"]} МБ), verified')
        if result['removed']:
            print(f'Удалено старых копий: {result["removed"]}')
        return 0
    if args.command == 'list':
        for item in list_backups():
            print(f'{item["name"]} | {item["mtime"]} | {"OK" if item["valid"] else item["error"]}')
        return 0
    if args.path:
        print('Для выбора копии без риска используйте restore.bat.')
        return 1
    return _interactive_restore()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'ОШИБКА: {exc}')
        raise SystemExit(1)
