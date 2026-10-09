#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Utilidades comunes de Odoo_scripts
- Carga el .env de esta carpeta
- Elige entorno: producción por defecto, staging con --stg
- Conexión a Odoo por XML-RPC (pide confirmación en producción y credenciales si fallan)
- Log con timestamp en logs/
- Normalización de texto
"""

import os
import sys
import getpass
import xmlrpc.client
import unicodedata
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv, set_key

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
ENV_PATH = os.path.join(BASE_DIR, ".env")

# Cargar variables de entorno de esta carpeta
load_dotenv(ENV_PATH)

# Entorno: --stg usa las credenciales *_STG del .env. Se quita de sys.argv para no
# interferir con los argumentos propios de cada script (p. ej. --dry-run)
STAGING = '--stg' in sys.argv
if STAGING:
    sys.argv = [a for a in sys.argv if a != '--stg']
SUFIJO_ENV = '_STG' if STAGING else ''
NOMBRE_ENTORNO = 'STAGING' if STAGING else 'PRODUCCIÓN'

LOG_FILE_PATH = None


def configurar_log(nombre_archivo):
    """Define el archivo de log (dentro de logs/) que usará log_message"""
    global LOG_FILE_PATH
    os.makedirs(LOGS_DIR, exist_ok=True)
    LOG_FILE_PATH = os.path.join(LOGS_DIR, nombre_archivo)


def log_message(message):
    """Escribe mensaje al archivo de log con timestamp"""
    try:
        if LOG_FILE_PATH:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(LOG_FILE_PATH, 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] {message}\n")
        print(message)
    except Exception:
        print(message)  # Al menos mostrar en consola si no se puede escribir al log


def texto(valor):
    """Convierte un valor a texto limpio ('' si está vacío)"""
    return str(valor).strip() if valor is not None and pd.notna(valor) else ''


def normalizar(valor):
    """Normaliza texto para comparar valores: minúsculas, sin tildes y sin espacios extra"""
    valor = unicodedata.normalize('NFKD', str(valor))
    valor = ''.join(c for c in valor if not unicodedata.combining(c))
    return ' '.join(valor.lower().split())


class OdooClient:
    def __init__(self):
        self.uid = None
        self.models = None
        self.url = os.getenv(f'ODOO_URL{SUFIJO_ENV}', '')
        self.db = os.getenv(f'ODOO_DB{SUFIJO_ENV}', '')
        self.user = os.getenv(f'ODOO_USER{SUFIJO_ENV}', '')
        self.api_key = os.getenv(f'ODOO_API_KEY{SUFIJO_ENV}', '')

    def _confirmar_entorno(self):
        """Muestra el entorno y, en producción, pide confirmación antes de continuar"""
        if STAGING:
            log_message(f"🧪 Entorno STAGING: {self.url} (base de datos {self.db})")
            return
        log_message("=" * 60)
        log_message("⚠️  ATENCIÓN: ENTORNO DE PRODUCCIÓN")
        log_message(f"⚠️  {self.url} (base de datos {self.db})")
        log_message("⚠️  Usa --stg para ejecutar en staging")
        log_message("=" * 60)
        respuesta = input("¿Continuar en PRODUCCIÓN? (s/N): ").strip().lower()
        if respuesta != 's':
            log_message("❌ Cancelado por el usuario")
            sys.exit(1)

    def _autenticar(self):
        """Intenta autenticar con las credenciales actuales. Devuelve el error o None"""
        if not all([self.url, self.db, self.user, self.api_key]):
            return f"Faltan credenciales de {NOMBRE_ENTORNO} en el archivo .env"
        url = self.url.rstrip('/')
        try:
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
            uid = common.authenticate(self.db, self.user, self.api_key, {})
        except (xmlrpc.client.Error, OSError) as e:
            return f"No se pudo conectar: {e}"
        if not uid:
            return "Error de autenticación. Credenciales incorrectas."
        self.uid = uid
        self.models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
        return None

    def _pedir_credenciales(self):
        """Pide las credenciales por consola (Enter mantiene el valor actual)"""
        print(f"\n🔑 Introduce las credenciales de Odoo {NOMBRE_ENTORNO} (Enter mantiene el valor actual)")
        self.url = input(f"URL [{self.url}]: ").strip() or self.url
        self.db = input(f"Base de datos [{self.db}]: ").strip() or self.db
        self.user = input(f"Usuario [{self.user}]: ").strip() or self.user
        self.api_key = getpass.getpass("API key [oculta, Enter mantiene la actual]: ").strip() or self.api_key

    def _guardar_credenciales(self):
        """Guarda en el .env las credenciales que han funcionado"""
        for clave, valor in (('ODOO_URL', self.url), ('ODOO_DB', self.db),
                             ('ODOO_USER', self.user), ('ODOO_API_KEY', self.api_key)):
            set_key(ENV_PATH, f'{clave}{SUFIJO_ENV}', valor, quote_mode='never')
        log_message(f"💾 Credenciales de {NOMBRE_ENTORNO} actualizadas en {ENV_PATH}")

    def connect_to_odoo(self):
        """Establece conexión con Odoo. Si las credenciales fallan, las pide y las guarda en el .env"""
        self._confirmar_entorno()
        log_message("🔗 Conectando a Odoo...")

        credenciales_nuevas = False
        while True:
            error = self._autenticar()
            if not error:
                break
            log_message(f"❌ {error}")
            try:
                self._pedir_credenciales()
            except (KeyboardInterrupt, EOFError):
                print()
                raise Exception(f"No se pudo conectar a Odoo {NOMBRE_ENTORNO}")
            credenciales_nuevas = True
            log_message("🔗 Reintentando conexión...")

        if credenciales_nuevas:
            # Las credenciales han cambiado: volver a mostrar a dónde vamos a escribir
            self._confirmar_entorno()
            self._guardar_credenciales()

        log_message(f"✅ Conectado a Odoo {NOMBRE_ENTORNO} ({self.url}, base de datos {self.db}) como usuario ID: {self.uid}")

    def execute(self, model, method, args, kwargs=None):
        """Atajo para execute_kw"""
        return self.models.execute_kw(self.db, self.uid, self.api_key, model, method, args, kwargs or {})
