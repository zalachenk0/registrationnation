# ============================================================================
# File:         kiosk_auth_BV.py
# Written by:   BV (Blas Vargas)
# What it does: Staff sign-in: local master login, remote staff login and the
#               audit log.
# Team-written: Logic written by BV (Blas Vargas). Outside code is used only
#               through the third-party sources listed below.
# Third-party:  Flask; Werkzeug; SQLite (Python standard library).
# ============================================================================
"""Local master authentication, remote staff authentication, and durable audit sync."""
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError
import hashlib
import json
import os
import secrets
import sqlite3
import ssl
import threading
import time
import uuid

from flask import jsonify, request
from werkzeug.security import check_password_hash

COOKIE = 'rn_kiosk_session'


class AuthError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class KioskAuth:
    def __init__(self, root, settings=None):
        self.root = Path(root)
        self.settings = settings if settings is not None else os.environ
        self.database = self.root / 'runtime' / 'auth.sqlite3'
        self.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.sync_lock = threading.Lock()
        self.stop = threading.Event()
        with self.db() as connection:
            connection.executescript('''
            CREATE TABLE IF NOT EXISTS sessions (
              id_hash TEXT PRIMARY KEY, username TEXT NOT NULL, role TEXT NOT NULL,
              mode TEXT NOT NULL, remote_token TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS audit (
              id TEXT PRIMARY KEY, occurred REAL NOT NULL, username TEXT NOT NULL,
              outcome TEXT NOT NULL, detail TEXT NOT NULL, synced INTEGER NOT NULL DEFAULT 0);
            ''')
        os.chmod(self.database, 0o600)

    @contextmanager
    def db(self):
        with sqlite3.connect(self.database, timeout=15) as connection:
            connection.row_factory = sqlite3.Row
            yield connection

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def remote(self, endpoint, payload):
        url = self.settings.get('KIOSK_AUTH_URL', '').rstrip('/')
        key = self.settings.get('KIOSK_AUTH_KEY', '')
        ca = self.settings.get('KIOSK_AUTH_CA', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or len(key) < 32 or not ca:
            raise AuthError('Login server is not configured. Ask the administrator to check the kiosk settings.')
        cert = Path(ca)
        if not cert.is_absolute():
            cert = self.root / cert
        try:
            context = ssl.create_default_context(cafile=str(cert))
            opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())
            req = Request(url + '/v1/' + endpoint, data=json.dumps(payload).encode(), method='POST',
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
            with opener.open(req, timeout=8) as response:
                data = json.loads(response.read(65536))
            if not isinstance(data, dict):
                raise ValueError('Invalid authority response')
            return data
        except HTTPError as exc:
            if exc.code == 401:
                raise AuthError('Incorrect username or password, account disabled, or session expired.', 401) from None
            raise AuthError('Login server rejected the request. Ask the administrator to check the pairing settings.') from None
        except (OSError, ValueError):
            raise AuthError('Cannot reach the login server. Check that the computer is on and connected; the master login remains available.') from None

    def record(self, username, outcome, detail, synced=False):
        with self.db() as connection:
            connection.execute('INSERT INTO audit VALUES (?,?,?,?,?,?)',
                               (str(uuid.uuid4()), time.time(), username[:80], outcome, detail, int(synced)))

    def sync(self):
        if not self.sync_lock.acquire(blocking=False):
            return
        try:
            with self.db() as connection:
                events = [dict(row) for row in connection.execute('SELECT * FROM audit WHERE synced=0 ORDER BY occurred LIMIT 50')]
            if not events:
                return
            result = self.remote('audit', {'events': events})
            accepted = result.get('accepted', [])
            if isinstance(accepted, list):
                with self.db() as connection:
                    connection.executemany('UPDATE audit SET synced=1 WHERE id=?', [(event['id'],) for event in events if event['id'] in accepted])
        except AuthError:
            pass  # Keep the durable journal; a later sync retries without credentials.
        finally:
            self.sync_lock.release()

    def start_sync(self):
        def run():
            while not self.stop.wait(15):
                try:
                    self.sync()
                except sqlite3.Error:
                    pass
        thread = threading.Thread(target=run, daemon=True, name='login-audit-sync')
        thread.start()

    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 80 or len(password) > 1024:
            self.record('(invalid input)', 'denied', 'Invalid login input')
            raise AuthError('Enter a valid username and password.', 400)
        username = username.strip().casefold()
        with self.db() as connection:
            failures = connection.execute("SELECT count(*) FROM audit WHERE occurred>? AND outcome!='success' AND username=?", (time.time() - 300, username)).fetchone()[0]
            kiosk_failures = connection.execute("SELECT count(*) FROM audit WHERE occurred>? AND outcome='denied'", (time.time() - 300,)).fetchone()[0]
        if failures >= 10 or kiosk_failures >= 30:
            self.record(username, 'denied', 'Kiosk sign-in throttled; retry after five minutes')
            raise AuthError('Too many login attempts. Please wait five minutes.', 429)
        master = self.settings.get('KIOSK_MASTER_USERNAME', '').strip().casefold()
        if master and username == master:
            stored_hash = self.settings.get('KIOSK_MASTER_PASSWORD_HASH', '')
            try:
                valid = stored_hash.startswith(('scrypt:', 'pbkdf2:')) and check_password_hash(stored_hash, password)
            except ValueError:
                valid = False
            if not valid:
                self.record(username, 'denied', 'Local master credentials rejected')
                raise AuthError('Incorrect username or password.', 401)
            self.record(username, 'success', 'Local master login; no server approval required')
            user = dict(username=username, role='admin', mode='master')
            remote_token = ''
            expires = time.time() + 8 * 3600
        else:
            try:
                result = self.remote('login', {'username': username, 'password': password})
                remote_token = result.get('token', '')
                expires = float(result.get('expires', 0))
                if not isinstance(remote_token, str) or not remote_token or expires <= time.time() or result.get('username') != username:
                    raise AuthError('The login server returned an invalid session.')
                user = dict(username=username, role=result.get('role', 'staff'), mode='server')
                self.record(username, 'success', 'Server-approved login', synced=True)
            except (ValueError, TypeError):
                self.record(username, 'unavailable', 'Invalid response from login server')
                raise AuthError('The login server returned an invalid session.') from None
            except AuthError as exc:
                self.record(username, 'denied' if exc.status == 401 else 'unavailable',
                            'Server rejected credentials or account' if exc.status == 401 else 'Login server unavailable; access not granted', synced=exc.status == 401)
                raise
        local_token = secrets.token_urlsafe(40)
        with self.db() as connection:
            connection.execute('DELETE FROM sessions WHERE expires < ?', (time.time(),))
            connection.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)',
                               (self.digest(local_token), username, user['role'], user['mode'], remote_token, min(expires, time.time() + 8 * 3600)))
        return local_token, user

    def authorize(self, token):
        if not token:
            raise AuthError('Please sign in to access the forms.', 401)
        with self.db() as connection:
            row = connection.execute('SELECT * FROM sessions WHERE id_hash=? AND expires>?', (self.digest(token), time.time())).fetchone()
        if not row:
            raise AuthError('Your session has expired. Please sign in again.', 401)
        if row['mode'] == 'master':
            if row['username'] != self.settings.get('KIOSK_MASTER_USERNAME', '').strip().casefold():
                raise AuthError('Master configuration changed. Please sign in again.', 401)
        else:
            result = self.remote('check', {'token': row['remote_token']})
            if result.get('username') != row['username']:
                raise AuthError('The login server could not confirm your session.', 401)
        return dict(username=row['username'], role=row['role'], mode=row['mode'])

    def logout(self, token):
        with self.db() as connection:
            row = connection.execute('SELECT remote_token FROM sessions WHERE id_hash=?', (self.digest(token),)).fetchone()
            connection.execute('DELETE FROM sessions WHERE id_hash=?', (self.digest(token),))
        if row and row['remote_token']:
            try:
                self.remote('logout', {'token': row['remote_token']})
            except AuthError:
                pass


def install_auth(app, root):
    auth = KioskAuth(root)
    app.config['KIOSK_AUTH'] = auth
    auth.start_sync()

    @app.errorhandler(AuthError)
    def auth_error(error):
        response = jsonify(error=str(error))
        if error.status == 401:
            response.delete_cookie(COOKIE)
        return response, error.status

    @app.before_request
    def protect_api():
        if not request.path.startswith('/api/') or request.path == '/api/health':
            return None
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.headers.get('X-Kiosk-Request') != '1':
            return jsonify(error='Use the kiosk controls.'), 403
        if request.path not in {'/api/auth/login', '/api/auth/logout', '/api/auth/session'}:
            auth.authorize(request.cookies.get(COOKIE, ''))

    @app.post('/api/auth/login')
    def auth_login():
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        token, user = auth.login(data.get('username'), data.get('password'))
        response = jsonify(user=user)
        # The kiosk is bound to loopback HTTP; the remote authority uses HTTPS.
        response.set_cookie(COOKIE, token, httponly=True, samesite='Strict', max_age=8 * 3600, secure=request.is_secure)
        return response

    @app.get('/api/auth/session')
    def auth_session():
        return jsonify(user=auth.authorize(request.cookies.get(COOKIE, '')))

    @app.post('/api/auth/logout')
    def auth_logout():
        auth.logout(request.cookies.get(COOKIE, ''))
        response = jsonify(success=True)
        response.delete_cookie(COOKIE)
        return response

    return auth
