#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Actualizar atributos de categoría en productos publicados
Usa la misma lógica que el script 23 del pipeline (modelos, IA y validación) sobre los productos ya publicados en Odoo.
Los modelos (MODELOS) y las funciones de IA son una copia de 23.Atributos_categoria.py del Importador:
si se cambian allí, hay que copiarlos aquí.

Funcionalidad:
1. Sincroniza en Odoo las categorías de atributo, atributos y valores de MODELOS
2. Descarga todos los productos con is_published activo: nombre, categoría interna (categ_id),
   categorías eCommerce (public_categ_ids), descripciones,
   referencia del proveedor y líneas de atributo
3. Une los atributos de todas las categorías del producto que tienen modelo
4. Salta los productos sin ninguna categoría con modelo y los que ya tienen atributos
   distintos de Marca/Brand
5. Pide a la IA un valor por atributo y solo acepta valores de la lista permitida
6. Añade las líneas de atributo al producto en Odoo sin tocar la línea de Marca/Brand

Uso:
    python Actualizar_atributos_publicados.py        # Producción (pide confirmación)
    python Actualizar_atributos_publicados.py --stg  # Staging

Nota:
- Script independiente, no forma parte del pipeline
- Requiere .env, .promptatributos y comun.py
"""

import os
import re
import sys
import html
import json
import time
import requests
from datetime import datetime

from comun import OdooClient, BASE_DIR, configurar_log, log_message, texto, normalizar

# Log propio
configurar_log("actualizar_atributos_publicados_log.txt")

PROMPT_FILE_PATH = os.path.join(BASE_DIR, ".promptatributos")


def get_dynamic_ollama_url():
    """
    Detecta automáticamente si estamos en Docker o en el host y devuelve la URL apropiada
    """
    try:
        # Detectar si estamos en Docker
        in_docker = os.path.exists('/.dockerenv')
        if not in_docker and os.path.exists('/proc/1/cgroup'):
            with open('/proc/1/cgroup', 'r') as f:
                in_docker = 'docker' in f.read()

        if in_docker:
            return os.getenv('OPENAI_API_URL_DOCKER', 'http://host.docker.internal:11434/v1')
        else:
            return os.getenv('OPENAI_API_URL_HOST', 'http://localhost:11434/v1')
    except Exception as e:
        # Fallback a la URL por defecto
        print(f"No se pudo detectar el entorno, usando URL por defecto: {e}")
        return os.getenv('OPENAI_API_URL', 'http://localhost:11434/v1')


# Configuración de IA
AI_MODEL = os.getenv('OPENAI_MODEL', 'llama3')
base_url = get_dynamic_ollama_url()
API_URL = f"{base_url}/chat/completions" if not base_url.endswith('/chat/completions') else base_url

# Modelos de atributos por categoría: {categoría: {atributo: [valores]}}
# Los nombres de categoría deben coincidir con el último nivel de la categoría de producto en Odoo
MODELOS = {
    "Bandurria": {
        "Tapa": ["Abeto", "Cedro", "Otra"],
        "Tipo de tapa": ["Maciza", "Laminada"],
        "Aros y fondo": ["Ciprés", "Palisandro", "Sapeli", "Caoba", "Arce", "Nogal", "Otra"],
        "Mástil": ["Cedro", "Caoba", "Otra"],
        "Diapasón": ["Ébano", "Palisandro", "Otra"],
        "Electrificada": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Laud": {
        "Tapa": ["Abeto", "Cedro", "Otra"],
        "Tipo de tapa": ["Maciza", "Laminada"],
        "Aros y fondo": ["Ciprés", "Palisandro", "Sapeli", "Caoba", "Arce", "Nogal", "Otra"],
        "Mástil": ["Cedro", "Caoba", "Otra"],
        "Diapasón": ["Ébano", "Palisandro", "Otra"],
        "Electrificada": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Contrabajo": {
        "Medida": ["1/4", "1/2", "3/4", "4/4"],
        "Tapa": ["Maciza", "Laminada"],
        "Diapasón": ["Ébano", "Madera ebanizada", "Otra"],
        "Incluye funda": ["Sí", "No"],
        "Incluye arco": ["Sí", "No"],
    },
    "Violonchelo": {
        "Medida": ["1/8", "1/4", "1/2", "3/4", "4/4"],
        "Tapa": ["Maciza", "Laminada"],
        "Fondo flameado": ["Sí", "No"],
        "Diapasón": ["Ébano", "Madera ebanizada", "Otra"],
        "Incluye arco": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
    },
    "Viola": {
        "Medida": ["11´´", "12´´", "13´´", "14´´", "15´´", "15,5´´", "16´´"],
        "Tapa": ["Maciza", "Laminada"],
        "Fondo flameado": ["Sí", "No"],
        "Diapasón": ["Ébano", "Madera ebanizada", "Otra"],
        "Incluye arco": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
    },
    "Violín": {
        "Medida": ["1/32", "1/16", "1/8", "1/4", "1/2", "3/4", "4/4"],
        "Tapa": ["Maciza", "Laminada"],
        "Fondo flameado": ["Sí", "No"],
        "Diapasón": ["Ébano", "Madera ebanizada", "Otra"],
        "Incluye arco": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
    },
    "Contrabajo eléctrico": {
        "Número de cuerdas": ["4", "5", "6"],
        "Electrónica": ["Pasiva", "Activa"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
        "Incluye arco": ["Sí", "No"],
        "Incluye pica": ["Sí", "No"],
    },
    "Violonchelo eléctrico": {
        "Número de cuerdas": ["4", "5", "6"],
        "Medida": ["3/4", "4/4"],
        "Electrónica": ["Pasiva", "Activa"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
        "Incluye arco": ["Sí", "No"],
        "Incluye pica": ["Sí", "No"],
    },
    "Viola eléctrica": {
        "Número de cuerdas": ["4", "5", "6"],
        "Medida": ["14´´", "15´´", "16´´"],
        "Electrónica": ["Pasiva", "Activa"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
        "Incluye arco": ["Sí", "No"],
    },
    "Violín eléctrico": {
        "Número de cuerdas": ["4", "5", "6"],
        "Medida": ["3/4", "4/4"],
        "Electrónica": ["Pasiva", "Activa"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
        "Incluye arco": ["Sí", "No"],
    },
    "Arco": {
        "Instrumento": ["Contrabajo", "Violonchelo", "Viola", "Violín"],
        "Medida": ["1/32", "1/16", "1/8", "1/4", "1/2", "3/4", "4/4"],
        "Estilo": ["Alemán", "Francés", "Barroco"],
        "Material": ["Pernambuco", "Madera de Brasil", "Fibra de carbono", "Fibra de vidrio", "Composite", "Otra"],
        "Montado": ["Alpaca / Níquel", "Plata", "Oro"],
        "Crin": ["Natural", "Sintética"],
        "Vara": ["Redonda", "Octogonal"],
    },
    "Cuerdas (cuerda frotada)": {
        "Instrumento": ["Contrabajo", "Violonchelo", "Viola", "Violín"],
        "Medida": ["1/8", "1/4", "1/2", "3/4", "4/4"],
        "Núcleo": ["Acero", "Sintético", "Tripa"],
        "Tensión": ["Baja", "Media", "Alta"],
        "Terminación": ["Bola", "Lazo"],
    },
    "Accesorios de cuerda": {
        "Instrumento": ["Contrabajo", "Violonchelo", "Viola", "Violín"],
        "Accesorio": ["Sordina", "Gamuza", "Resina", "Micro-afinador", "Aceite para cuerdas", "Humidificador", "Cordal", "Limpiador para barniz", "Pasta para clavijas", "Apoya-pica", "Aceite para diapasón", "Pijama", "Correa anti-deslizante", "Puente preparado", "Puente virgen", "Matalobos"],
    },
    "Guitarra Española": {
        "Cuerpo": ["Estándar", "Estrecho", "Cutaway", "Híbrida"],
        "Tapa": ["Abeto", "Cedro", "Otra"],
        "Tipo de tapa": ["Maciza", "Laminada"],
        "Cuerpo y aro": ["Palisandro", "Caoba", "Sapeli", "Ciprés", "Arce", "Nogal", "Ovangkol", "Sicomoro", "Otra"],
        "Diapasón": ["Ébano", "Palisandro", "Nogal", "Ovangkol", "Pau Ferro", "Acacia", "Richlite", "Otra"],
        "Electrificada": ["Sí", "No"],
        "Tipo": ["Clásica", "Flamenca"],
        "Incluye funda": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Guitarra Acústica": {
        "Cuerpo": ["Dreadnought", "Jumbo", "OM / Auditorium", "Grand Auditorium", "Concert", "Grand Concert", "Parlour", "Triple Cero (000)", "Travel"],
        "Tapa": ["Abeto", "Cedro", "Caoba", "Koa", "Arce", "Sapeli", "Okume", "Otra"],
        "Tipo de tapa": ["Maciza", "Laminada"],
        "Cuerpo y aros": ["Caoba", "Palisandro", "Sapeli", "Nogal", "Arce", "Ovangkol", "Koa", "Nato", "Meranti", "Ciprés", "Laminado HPL", "Otra"],
        "Diapasón": ["Palisandro", "Arce", "Arce tostado", "Ébano", "Jatoba", "Laurel", "Amaranto", "Pau Ferro", "Roseacer", "Richlite", "Nogal", "Ovangkol", "Otra"],
        "Cutaway": ["Sí", "No"],
        "Electrificada": ["Sí", "No"],
        "Número de cuerdas": ["6", "12"],
        "Color": ["Natural", "Sunburst", "Negro", "Blanco", "Azul", "Rojo", "Marrón"],
        "Incluye funda": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Guitarra Eléctrica": {
        "Cuerpo": ["Aliso", "Fresno", "Caoba", "Tilo", "Álamo", "Arce", "Nato", "Nyatoh", "Okume", "Meranti", "Korina", "Paulownia", "Otra"],
        "Tapa": ["Ninguna", "Arce", "Arce flameado", "Arce acolchado", "Fresno", "Álamo", "Koa", "Nogal", "Otra"],
        "Mástil": ["Arce", "Arce tostado", "Caoba", "Nato", "Nyatoh", "Meranti", "Wengué", "Nogal", "Otra"],
        "Diapasón": ["Palisandro", "Arce", "Arce tostado", "Ébano", "Jatoba", "Laurel", "Amaranto", "Pau Ferro", "Roseacer", "Richlite", "Nogal", "Ovangkol", "Otra"],
        "Trastes": ["21", "22", "24"],
        "Configuración de pastillas": ["SSS", "HSS", "HH", "HSH", "SS", "H", "P90", "HHH"],
        "Puente": ["Fijo", "Trémolo vintage", "Trémolo de doble bloqueo (Floyd Rose)", "Bigsby"],
        "Electrónica": ["Pasiva", "Activa"],
        "Número de cuerdas": ["6", "7", "8", "12"],
        "Color": ["Negro", "Blanco", "Rojo", "Azul", "Sunburst", "Natural", "Verde", "Gris", "Morado", "Dorado", "Plateado", "Amarillo", "Naranja", "Rosa", "Marrón"],
        "Incluye funda": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Bajo Eléctrico": {
        "Cuerpo": ["Aliso", "Fresno", "Caoba", "Tilo", "Álamo", "Arce", "Nato", "Nyatoh", "Okume", "Meranti", "Korina", "Paulownia", "Otra"],
        "Mástil": ["Arce", "Arce tostado", "Caoba", "Nato", "Nyatoh", "Meranti", "Wengué", "Nogal", "Otra"],
        "Diapasón": ["Palisandro", "Arce", "Arce tostado", "Ébano", "Jatoba", "Laurel", "Amaranto", "Pau Ferro", "Roseacer", "Richlite", "Nogal", "Ovangkol", "Otra"],
        "Número de cuerdas": ["4", "5", "6"],
        "Trastes": ["Fretless", "20", "21", "22", "24"],
        "Escala": ["Larga", "Media", "Corta"],
        "Configuración de pastillas": ["P", "J", "PJ", "JJ", "HH", "H"],
        "Electrónica": ["Pasiva", "Activa", "Activa/Pasiva conmutable"],
        "Color": ["Negro", "Blanco", "Rojo", "Azul", "Sunburst", "Natural", "Verde", "Gris", "Morado", "Dorado", "Plateado", "Amarillo", "Naranja", "Rosa", "Marrón"],
        "Incluye funda": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Bajo Acústico": {
        "Tapa": ["Abeto", "Cedro", "Caoba", "Koa", "Arce", "Sapeli", "Okume", "Otra"],
        "Tipo de tapa": ["Maciza", "Laminada"],
        "Cuerpo y aros": ["Caoba", "Palisandro", "Sapeli", "Nogal", "Arce", "Ovangkol", "Koa", "Nato", "Meranti", "Ciprés", "Laminado HPL", "Otra"],
        "Diapasón": ["Palisandro", "Arce", "Arce tostado", "Ébano", "Jatoba", "Laurel", "Amaranto", "Pau Ferro", "Roseacer", "Richlite", "Nogal", "Ovangkol", "Otra"],
        "Número de cuerdas": ["4", "5", "6"],
        "Trastes": ["Fretless", "Con trastes"],
        "Escala": ["Larga", "Media", "Corta"],
        "Electrificado": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Ukelele": {
        "Tamaño": ["Soprano", "Concierto", "Tenor", "Barítono", "Bajo"],
        "Material cuerpo": ["Caoba", "Tilo", "Sapeli", "Koa", "Acacia", "Mango", "Abeto", "Bambú", "Nogal", "Plástico / ABS", "Otra"],
        "Tipo de tapa": ["Maciza", "Laminada"],
        "Electrificado": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Cuerdas": {
        "Instrumento": ["Bajo", "Bandurria", "Guitarra acústica", "Guitarra eléctrica", "Guitarra española", "Guitarra flamenca", "Laúd", "Ukelele"],
        "Material": ["Nylon", "Carbono", "Fósforo bronce", "Bronce 80/20", "Acero inoxidable", "Níquel", "Titanio", "Composite"],
        "Calibre": ["Extra light (.008–.009)", "Light (.010)", "Medium (.011)", "Heavy (.012 o más)"],
        "Tensión": ["Baja", "Normal", "Alta", "Extra alta"],
        "Recubiertas": ["Sí", "No"],
    },
    "Amplificadores Guitarra Eléctrica": {
        "Tipo amplificador": ["Transistor", "Válvulas", "Modelado digital", "Híbrido", "A pilas"],
        "Potencia": ["Hasta 10 W", "11–30 W", "31–60 W", "61–100 W", "Más de 100 W"],
        "Altavoz": ["1x6,5\" o menor", "1x8\"", "1x10\"", "1x12\"", "2x10\"", "2x12\""],
        "Canales": ["1", "2", "3 o más"],
        "Efectos": ["Sí", "No"],
        "Reverb": ["Sí", "No"],
        "Bucle de efectos": ["Sí", "No"],
        "Salida auriculares": ["Sí", "No"],
        "Incluye footswitch": ["Sí", "No"],
    },
    "Amplificadores Guitarra Acústica": {
        "Potencia": ["Hasta 30 W", "31–60 W", "61–120 W", "Más de 120 W"],
        "Altavoz": ["1x6,5\" o menor", "1x8\"", "1x10\"", "2x8\" o más"],
        "Canales": ["1", "2", "3 o más"],
        "Efectos": ["Sí", "No"],
        "Reverb": ["Sí", "No"],
        "Entrada de micrófono": ["Sí", "No"],
        "Funciona a pilas": ["Sí", "No"],
        "Salida auriculares": ["Sí", "No"],
    },
    "Amplificadores Bajo": {
        "Tipo amplificador": ["Transistor", "Válvulas", "Modelado digital", "Híbrido"],
        "Potencia": ["Hasta 50 W", "51–150 W", "151–300 W", "301–500 W", "Más de 500 W"],
        "Altavoz": ["1x8\" o menor", "1x10\"", "1x12\"", "1x15\"", "2x10\"", "4x10\""],
        "Compresor": ["Sí", "No"],
        "Salida DI": ["Sí", "No"],
        "Efectos": ["Sí", "No"],
        "Salida auriculares": ["Sí", "No"],
    },
    "Pedaleras y efectos": {
        "Tipo de efecto": ["Multiefectos", "Distorsión / Overdrive / Fuzz", "Delay", "Reverb", "Modulación", "Compresor", "Wah / Volumen", "Afinador", "Looper", "Ecualizador"],
        "Instrumento": ["Guitarra eléctrica", "Guitarra acústica", "Bajo"],
        "Simulación de amplificador": ["Sí", "No"],
        "True bypass": ["Sí", "No"],
        "USB": ["Sí", "No"],
        "Salida auriculares": ["Sí", "No"],
        "Incluye alimentador": ["Sí", "No"],
        "Funciona a pilas": ["Sí", "No"],
    },
    "Batería acústica": {
        "Material del casco": ["Álamo", "Abedul", "Arce", "Caoba", "Nogal", "Tilo", "Plástico", "Otra"],
        "Acabado de cascos": ["Forrado", "Lacado brillo", "Lacado mate"],
        "Acabado de herrajes": ["Cromado", "Negro", "Dorado"],
        "Color": ["Amarillo", "Ámbar", "Azul", "Blanco", "Bronce", "Cobre", "Cromado", "Dorado", "Gris", "Marrón", "Naranja", "Natural", "Negro", "Plateado", "Rojo", "Rosa", "Verde", "Violeta"],
        "Tamaño del bombo": ["16\"", "18\"", "20\"", "22\"", "24\""],
        "Número de piezas": ["4 piezas", "5 piezas", "6 o más"],
        "Incluye platos": ["Sí", "No"],
        "Incluye herrajes": ["Sí", "No"],
        "Incluye sillín": ["Sí", "No"],
    },
    "Batería electrónica": {
        "Tipo de parche": ["Malla", "Goma"],
        "Pad estéreo": ["Sí", "No"],
        "Incluye rack": ["Sí", "No"],
        "Incluye pedal de bombo": ["Sí", "No"],
        "Incluye auriculares": ["Sí", "No"],
        "Incluye banqueta": ["Sí", "No"],
    },
    "Cajón flamenco": {
        "Tipo de cajón": ["Estándar", "Con pastilla", "Cajón bajo", "De viaje / Mini", "Cajón bongo"],
        "Material del cuerpo": ["Abedul", "MDF", "Okume", "Aliso", "Arce", "Caoba", "Roble", "Sintético", "Otra"],
        "Tapa": ["Abedul", "Nogal", "Roble", "Fresno", "Haya", "Palisandro", "Sapeli", "Álamo", "Ébano", "Otra"],
        "Bordones ajustables": ["Sí", "No"],
        "Refuerzo en graves": ["Sí", "No"],
    },
    "Percusión": {
        "Instrumento": ["Sets de percusión", "Percusión infantil", "Congas", "Bongos", "Djembes", "Tambores", "Cuencos tibetanos", "Claves", "Panderetas", "Shaker", "Cabasas", "Cortinillas", "Cencerros", "Guiros", "Maracas", "Cascabeles", "Silbatos de efecto", "Boomwhackers", "Timbales", "Rototoms", "Darbukas", "Kalimbas", "Instrumentos de samba", "Bolsas y fundas"],
    },
    "Percusión orquestral": {
        "Instrumento": ["Carrillones", "Xilófonos", "Metalófonos", "Marimbas", "Vibráfonos", "Crótalos y chinchines", "Címbalos", "Timbales", "Platillos de orquesta", "Tambores", "Percusión pequeña", "Otra percusión clásica", "Accesorios", "Mazas"],
    },
    "Platillos": {
        "Tipo de platillo": ["Crash", "Hi-Hat", "Ride", "Splash", "China", "Efecto / Stack", "Set de platillos"],
        "Medida": ["8\"", "10\"", "12\"", "13\"", "14\"", "15\"", "16\"", "17\"", "18\"", "19\"", "20\"", "21\"", "22\"", "24\""],
        "Aleación": ["B8 Bronce", "B10 Bronce", "B12 Bronce", "B15 Bronce", "B20 Bronce", "Latón"],
        "Martillado": ["Sí", "No"],
    },
    "Baquetas": {
        "Modelo": ["7A", "5A", "5B", "2B", "Rods", "Escobillas", "Mazas"],
        "Material": ["Hickory", "Arce", "Roble", "Fibra de carbono", "Plástico", "Otra"],
        "Punta": ["Madera", "Nailon"],
        "Forma de punta": ["Barril", "Diamante", "Lágrima", "Ovalada", "Redonda"],
    },
    "Amplificación de batería": {
        "Potencia": ["Hasta 50 W", "51–150 W", "151–500 W", "Más de 500 W"],
        "Altavoz": ["8\"", "10\"", "12\"", "15\""],
        "Canales": ["1", "2", "3 o más"],
        "Entrada de linea": ["Sí", "No"],
        "Conexión de auriculares": ["Sí", "No"],
    },
    "Accesorios y repuestos": {
        "Instrumento": ["Parches", "Correas", "Llaves de afinación", "Baqueteros", "Pads de estudio"],
    },
    "Pianos verticales": {
        "Altura (cm)": ["Hasta 115 cm", "116–122 cm", "123–130 cm", "Más de 130 cm"],
        "Marca de mecánica": ["Renner", "Yamaha", "Kawai", "Seiler", "Langer", "Schulze Pollmann", "Propia"],
        "Color": ["Negro pulido", "Negro mate", "Blanco pulido", "Madera (caoba / nogal / cerezo)"],
        "Sistema silencioso": ["Sí", "No"],
        "Tapa caída lenta": ["Sí", "No"],
        "Estado": ["Nuevo", "Seminuevo"],
    },
    "Pianos de cola": {
        "Longitud": ["Hasta 160 cm", "161–180 cm", "181–210 cm", "Más de 210 cm"],
        "Marca de mecánica": ["Renner", "Yamaha", "Kawai", "Seiler", "Langer", "Schulze Pollmann", "Propia"],
        "Color": ["Negro pulido", "Negro mate", "Blanco pulido", "Madera (caoba / nogal / cerezo)"],
        "Sistema silencioso": ["Sí", "No"],
        "Tapa caída lenta": ["Sí", "No"],
        "Estado": ["Nuevo", "Seminuevo"],
    },
    "Pianos digitales": {
        "Formato": ["Mueble", "Portátil", "Cola digital"],
        "Número de teclas": ["61", "73", "76", "88"],
        "Teclado": ["Compensado", "Contrapesado", "Contrapesado con escape", "Contrapesado con dureza progresiva"],
        "Teclas de madera": ["Sí", "No"],
        "Polifonía": ["Hasta 64", "65–128", "129–256", "Más de 256"],
        "Sonidos": ["Hasta 50", "51–200", "201–500", "Más de 500"],
        "Ritmos / acompañamientos": ["Sí", "No"],
        "Bluetooth": ["Sí", "No"],
        "Conexión": ["USB", "MIDI", "USB y MIDI"],
        "Secuenciador": ["Sí", "No"],
        "Acabado": ["Mate", "Brillante"],
    },
    "Teclados controladores": {
        "Teclas": ["25", "37", "49", "61", "76", "88"],
        "Teclas contrapesadas": ["Sí", "No"],
        "Aftertouch": ["Sí", "No"],
        "Pads": ["Sí", "No"],
        "Faders": ["Ninguno", "1", "2–8", "9 o más"],
        "Conexión": ["USB", "MIDI y USB"],
    },
    "Pianos de escenario": {
        "Número de teclas": ["61", "73", "76", "88"],
        "Teclado": ["Compensado", "Contrapesado", "Contrapesado con escape", "Contrapesado con dureza progresiva"],
        "Teclas de madera": ["Sí", "No"],
        "Polifonía": ["Hasta 64", "65–128", "129–256", "Más de 256"],
        "Sonidos": ["Hasta 50", "51–200", "201–500", "Más de 500"],
        "Ritmos / acompañamientos": ["Sí", "No"],
        "Bluetooth": ["Sí", "No"],
        "Conexión": ["USB", "MIDI", "USB y MIDI"],
        "Secuenciador": ["Sí", "No"],
        "Altavoces integrados": ["Sí", "No"],
    },
    "Sintetizadores": {
        "Teclas": ["Sin teclado", "25", "37", "49", "61", "76", "88"],
        "Polifonía": ["Monofónico", "2–8", "9–32", "33–128", "Más de 128"],
        "Motor de sonido": ["Analógico", "Digital", "Híbrido", "Analógico virtual", "Workstation / Sampler"],
        "Secuenciador": ["Sí", "No"],
        "Arpegiador": ["Sí", "No"],
        "Aftertouch": ["Sí", "No"],
    },
    "Teclados de ritmos": {
        "Teclas": ["61", "76", "88"],
        "Teclas sensibles": ["Sí", "No"],
        "Teclas iluminadas": ["Sí", "No"],
        "Estilos de ritmos": ["Hasta 100", "101–300", "Más de 300"],
        "Sonidos": ["Hasta 200", "201–500", "Más de 500"],
        "Polifonía": ["Hasta 32", "33–64", "Más de 64"],
        "Lecciones de aprendizaje": ["Sí", "No"],
        "Entrada de micrófono": ["Sí", "No"],
        "Conexión": ["USB", "MIDI y USB", "No"],
    },
    "Acordeones": {
        "Tipo": ["Teclas", "Botones", "Diatónico", "Digital"],
        "Cantidad de teclas / botones": ["Hasta 26", "27–34", "35–41", "Más de 41"],
        "Bajos": ["Hasta 48", "60–80", "96", "120 o más"],
        "Coros": ["1", "2", "3", "4", "5"],
        "Registros": ["Sin registros", "1–5", "6–10", "Más de 10"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye funda": ["Sí", "No"],
    },
    "Amplificadores de teclado": {
        "Potencia": ["Hasta 50 W", "51–100 W", "101–200 W", "Más de 200 W"],
        "Altavoz": ["1x8", "1x10", "1x12", "1x15", "1x12 + 1x2"],
        "Canales": ["1", "2", "3–4", "Más de 4"],
        "Efectos": ["Sí", "No"],
    },
    "Micrófonos": {
        "Tipo micrófono": ["Dinámico", "Condensador", "Cinta"],
        "Tamaño diafragma": ["Pequeño", "Grande"],
        "Patrón polar": ["Cardioide", "Supercardioide", "Hipercardioide", "Omnidireccional", "Figura en 8", "Multipatrón conmutable"],
        "Interruptor On/Off": ["Sí", "No"],
        "Filtro de paso alto": ["Sí", "No"],
        "Pad": ["Sí", "No"],
        "USB": ["Sí", "No"],
        "Funciona a válvulas": ["Sí", "No"],
        "Incluye araña": ["Sí", "No"],
        "Incluye pinza": ["Sí", "No"],
        "Incluye cable": ["Sí", "No"],
    },
    "Equipos DJ": {
        "Tipo": ["Controlador todo en uno", "Controlador DJ", "Mesa de mezclas DJ", "Reproductor", "Giradiscos"],
        "Canales": ["2", "4"],
        "Software incluido": ["Serato DJ Lite", "Serato DJ Pro", "rekordbox", "Engine DJ", "Traktor", "djay", "VirtualDJ", "Ninguno"],
        "Salida master": ["XLR", "RCA", "Jack 3,5 mm"],
        "Entrada de micrófono": ["Sí", "No"],
        "Entrada estéreo": ["Sí", "No"],
        "Mixer autónomo": ["Sí", "No"],
        "Reproductor de USB": ["Sí", "No"],
    },
    "Mesas de mezclas": {
        "Tipo": ["Analógica", "Digital"],
        "Canales en paralelo": ["Hasta 4", "5–8", "9–12", "13–16", "Más de 16"],
        "Canales de micrófono": ["Ninguno", "1–2", "3–4", "5–8", "9–16", "Más de 16"],
        "Canales estéreo": ["Ninguno", "1–2", "3–4", "Más de 4"],
        "PreAuxs": ["Ninguno", "1–2", "3–4", "Más de 4"],
        "Efectos": ["Sí", "No"],
        "Phantom +48V": ["Sí", "No"],
        "Interfaz de Audio": ["USB-B", "USB-C", "Thunderbolt", "No"],
        "Grabación multipista": ["Sí", "No"],
        "Grabación a USB/SD": ["Sí", "No"],
        "Reproductor bluetooth": ["Sí", "No"],
        "Montaje en rack": ["Sí", "No"],
    },
    "Auriculares": {
        "Diseño": ["Abierto", "Semiabierto", "Cerrado", "In-ear"],
        "Bluetooth": ["Sí", "No"],
        "Impedancia": ["Hasta 32 Ω", "33–80 Ω", "81–250 Ω", "Más de 250 Ω"],
        "Cable intercambiable": ["Sí", "No"],
        "Adaptador 3.5mm a 6.5mm": ["Sí", "No"],
    },
    "Altavoces": {
        "Tipo": ["Rango completo", "Subwoofer", "Monitor de escenario", "Columna", "Portátil a batería"],
        "Amplificación activa": ["Sí", "No"],
        "Tamaño del woofer (pulgadas)": ["6\"", "8\"", "10\"", "12\"", "15\"", "18\""],
        "Watios RMS": ["Hasta 200 W", "201–500 W", "501–1000 W", "Más de 1000 W"],
        "Material de la caja": ["Plástico", "Madera"],
        "Bluetooth": ["Sí", "No"],
        "Conexión de entrada": ["XLR", "XLR/Jack combo", "Speakon"],
    },
    "Reproductores": {
        "Número de reproductores": ["1", "2"],
        "Reproducción de CD": ["Sí", "No"],
        "Reproducción de USB": ["Sí", "No"],
        "Reproducción de SD": ["Sí", "No"],
        "Reproducción de cassette": ["Sí", "No"],
        "Bluetooth": ["Sí", "No"],
        "Control de pitch": ["Sí", "No"],
    },
    "Procesadores": {
        "Tipo": ["Multiefectos", "Reverb", "Delay", "Compresor / Dinámica", "Ecualizador", "Efectos de voz"],
        "Formato": ["Rack 19\"", "Sobremesa", "Pedal"],
        "Número de efectos": ["Hasta 16", "17–100", "Más de 100"],
        "Pantalla digital": ["Sí", "No"],
    },
    "Grabadoras": {
        "Tipo": ["Portátil", "Multipista de sobremesa"],
        "Micrófono integrado": ["Sí", "No"],
        "Pistas de grabación": ["1–2", "3–4", "5–8", "Más de 8"],
        "Entradas de micrófono": ["Ninguna", "1–2", "3–4", "Más de 4"],
        "Alimentación phantom (+48V)": ["Sí", "No"],
        "Profundidad de bits": ["16 bit", "24 bit", "32 bit"],
        "Frecuencia de muestreo": ["44,1 kHz", "48 kHz", "96 kHz", "192 kHz"],
        "Funciona a pilas": ["Sí", "No"],
    },
    "Etapas de potencia": {
        "Tamaño de rack": ["1U", "2U", "3U", "4U"],
        "Canales": ["1", "2", "4", "8"],
        "Potencia por canal (4 Ω)": ["Hasta 250 W", "251–500 W", "501–1000 W", "Más de 1000 W"],
        "Clase": ["AB", "D", "H"],
        "Modo bridge": ["Sí", "No"],
        "Estable a 2 Ω": ["Sí", "No"],
    },
    "Cables": {
        "Tipo": ["Instrumento", "Micrófono", "Altavoz", "Interconexión / Audio", "MIDI", "USB", "Alimentación"],
        "Longitud": ["Menos de 1 m", "1–3 m", "3,1–6 m", "6,1–10 m", "Más de 10 m"],
        "Tipo de conexión A": ["Jack 6,3 mm TS", "Jack 6,3 mm TRS", "Jack 3,5 mm", "XLR macho", "XLR hembra", "RCA", "MIDI DIN 5", "Speakon", "USB-A", "USB-B", "USB-C", "Schuko", "IEC", "Mini XLR"],
        "Tipo de conexión B": ["Jack 6,3 mm TS", "Jack 6,3 mm TRS", "Jack 3,5 mm", "XLR macho", "XLR hembra", "RCA", "MIDI DIN 5", "Speakon", "USB-A", "USB-B", "USB-C", "Schuko", "IEC", "Mini XLR"],
        "Conector acodado": ["Sí", "No"],
    },
    "Flauta travesera": {
        "Tipo": ["Flauta", "Flautín (piccolo)", "Flauta alto", "Flauta bajo"],
        "Platos": ["Abiertos", "Cerrados"],
        "Llaves": ["Alineadas (inline)", "Desalineadas (offset)"],
        "Mecanismo de Mi": ["Sí", "No"],
        "Pie de Si": ["Sí", "No"],
        "Tipo de cabeza": ["Recta", "Curva"],
        "Material del cuerpo": ["Alpaca plateada", "Plata 925", "Oro"],
        "Material de la cabeza": ["Alpaca plateada", "Plata 925", "Oro"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Flauta dulce": {
        "Tipo de flauta": ["Sopranino", "Soprano", "Alto", "Tenor", "Bajo"],
        "Digitación": ["Barroca", "Alemana"],
        "Material": ["Plástico", "Arce", "Peral", "Granadillo", "Palisandro", "Olivo", "Otra"],
    },
    "Saxofón": {
        "Tipo de saxofón": ["Sopranino", "Soprano", "Alto", "Tenor", "Barítono"],
        "Acabado": ["Lacado dorado", "Lacado claro", "Plateado", "Lacado negro", "Sin lacar", "Vintage"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye boquilla": ["Sí", "No"],
    },
    "Trompeta": {
        "Afinación": ["Sib", "Do", "Mib", "Piccolo"],
        "Tipo de válvulas": ["Pistones", "Cilindros"],
        "Calibre": ["Medio", "Medio-grande", "Grande"],
        "Acabado": ["Lacado", "Plateado", "Dorado", "Sin lacar"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye boquilla": ["Sí", "No"],
    },
    "Clarinete": {
        "Tipo de clarinete": ["Requinto", "Soprano Sib", "La", "Alto", "Bajo", "Contrabajo"],
        "Sistema": ["Boehm (francés)", "Alemán (Oehler)"],
        "Material del cuerpo": ["ABS", "Granadillo", "Ébano", "Ebonita", "Otra"],
        "Número de llaves": ["17", "18", "19", "20 o más"],
        "Acabado de llaves": ["Plateado", "Niquelado"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye boquilla": ["Sí", "No"],
    },
    "Fliscornio": {
        "Tipo de fliscornio": ["Pistones", "Cilindros"],
        "Material de la campana": ["Latón", "Latón dorado", "Alpaca"],
        "Acabado": ["Lacado", "Plateado", "Sin lacar"],
        "Incluye boquilla": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Trompa": {
        "Tipo de trompa": ["Simple FA", "Simple SIB", "Doble"],
        "Campana desmontable": ["Sí", "No"],
        "Material de la campana": ["Latón", "Latón dorado", "Alpaca"],
        "Acabado": ["Lacado", "Plateado", "Sin lacar"],
        "Incluye boquilla": ["Sí", "No"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Trombón": {
        "Tipo de trombón": ["Varas", "Pistones"],
        "Registro": ["Alto", "Tenor", "Tenor con transpositor (Fa)", "Bajo"],
        "Material de la campana": ["Latón", "Latón dorado", "Plástico"],
        "Acabado": ["Lacado", "Plateado", "Sin lacar"],
        "Incluye estuche / funda": ["Sí", "No"],
    },
    "Bombardino": {
        "Tipo de campana": ["Recta", "Curva"],
        "Número de pistones": ["3", "4", "5"],
        "Compensado": ["Sí", "No"],
        "Material de la campana": ["Latón", "Latón dorado", "Alpaca"],
        "Acabado": ["Lacado", "Plateado", "Sin lacar"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Fagot": {
        "Material cuerpo": ["Arce", "Plástico", "Otra"],
        "Número de tudeles": ["1", "2"],
        "Acabado de llaves": ["Plateado", "Niquelado", "Dorado"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye caña": ["Sí", "No"],
    },
    "Oboe": {
        "Material cuerpo": ["Granadillo", "Ébano", "Cocobolo", "Palisandro", "Plástico ABS", "Otra"],
        "Sistema": ["Semiautomático", "Automático"],
        "Acabado de llaves": ["Plateado", "Niquelado", "Dorado"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye caña": ["Sí", "No"],
    },
    "Tuba": {
        "Afinación": ["Sib", "Do", "Mib", "Fa"],
        "Número de pistones": ["3", "4", "5"],
        "Material de la campana": ["Latón", "Latón dorado", "Alpaca"],
        "Acabado": ["Lacado", "Plateado", "Sin lacar"],
        "Incluye estuche": ["Sí", "No"],
        "Incluye boquilla": ["Sí", "No"],
    },
    "Armónicas": {
        "Tipo de armónica": ["Diatónica", "Cromática", "Trémolo", "Octavada"],
        "Afinación": ["C", "D", "E", "F", "G", "A", "B", "Bb", "Eb", "Ab", "Db", "F#"],
        "Peine": ["Plástico", "Madera", "Metal"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Melódicas": {
        "Número de teclas": ["Hasta 32", "33–37", "Más de 37"],
        "Incluye estuche": ["Sí", "No"],
    },
    "Cañas": {
        "Instrumento": ["Saxo alto", "Saxo soprano", "Saxo tenor", "Clarinete", "Requinto", "Fagot", "Dulzaina", "Clarinete bajo", "Saxofón barítono", "Oboe"],
        "Material": ["Madera (caña natural)", "Sintética", "Híbrida"],
        "Dureza": ["1 ¼", "1 ½", "1 ¾", "2", "2 ¼", "2 ½", "2 ¾", "3", "3 ¼", "3 ½", "3 ¾", "4"],
    },
}


def cargar_modelos():
    """
    Devuelve los modelos de atributos definidos en MODELOS.

    Returns:
        dict: {categoría: {atributo: [valores]}}
    """
    total_atributos = sum(len(a) for a in MODELOS.values())
    total_valores = sum(len(v) for a in MODELOS.values() for v in a.values())
    log_message(f"✅ Modelos cargados: {len(MODELOS)} categorías, {total_atributos} atributos, {total_valores} valores")
    return MODELOS


class CategoryAttributeManager(OdooClient):
    def __init__(self):
        super().__init__()
        # {categoría: {atributo: {'id': attribute_id, 'values': {valor: value_id}}}}
        self.odoo_map = {}

    def verify_attribute_category_field(self):
        """Verifica que product.attribute tiene el campo category_id (product.attribute.category)"""
        fields = self.execute('product.attribute', 'fields_get', [['category_id', 'create_variant']], {'attributes': ['type', 'relation']})
        category_field = fields.get('category_id')
        if not category_field or category_field.get('relation') != 'product.attribute.category':
            raise Exception("El modelo product.attribute no tiene el campo 'category_id' (product.attribute.category) en esta versión de Odoo")
        if 'create_variant' not in fields:
            raise Exception("El modelo product.attribute no tiene el campo 'create_variant' en esta versión de Odoo")
        log_message("✅ Campo category_id de product.attribute verificado")

    def sync_attributes(self, modelos):
        """
        Verifica que todas las categorías de atributo, atributos y valores existen en Odoo.
        Crea los que falten. Los atributos se identifican siempre por nombre + categoría.
        """
        log_message("\n🔍 Sincronizando categorías, atributos y valores con Odoo...")
        creadas = {'categorias': 0, 'atributos': 0, 'valores': 0}
        existentes = {'categorias': 0, 'atributos': 0, 'valores': 0}

        # Paso 1: categorías de atributo
        nombres_categorias = list(modelos.keys())
        categorias_odoo = self.execute(
            'product.attribute.category', 'search_read',
            [[['name', 'in', nombres_categorias]]],
            {'fields': ['id', 'name']}
        )
        categoria_ids = {}
        for cat in categorias_odoo:
            if cat['name'] in categoria_ids:
                log_message(f"   ⚠️ Categoría de atributo duplicada en Odoo: '{cat['name']}' (IDs {categoria_ids[cat['name']]} y {cat['id']}). Se usa la primera.")
                continue
            categoria_ids[cat['name']] = cat['id']
        existentes['categorias'] = len(categoria_ids)

        for nombre in nombres_categorias:
            if nombre not in categoria_ids:
                categoria_ids[nombre] = self.execute('product.attribute.category', 'create', [{'name': nombre}])
                creadas['categorias'] += 1
                log_message(f"   📝 Categoría de atributo creada: '{nombre}' (ID: {categoria_ids[nombre]})")

        # Paso 2: atributos (nombre + categoría)
        atributos_odoo = self.execute(
            'product.attribute', 'search_read',
            [[['category_id', 'in', list(categoria_ids.values())]]],
            {'fields': ['id', 'name', 'category_id', 'create_variant']}
        )
        atributos_por_clave = {}
        for attr in atributos_odoo:
            clave = (attr['name'], attr['category_id'][0])
            if clave in atributos_por_clave:
                log_message(f"   ⚠️ Atributo duplicado en Odoo: '{attr['name']}' en categoría ID {clave[1]} (IDs {atributos_por_clave[clave]['id']} y {attr['id']}). Se usa el primero.")
                continue
            atributos_por_clave[clave] = attr

        # Valores existentes de todos los atributos encontrados (una sola llamada)
        valores_por_atributo = {}
        if atributos_por_clave:
            valores_odoo = self.execute(
                'product.attribute.value', 'search_read',
                [[['attribute_id', 'in', [a['id'] for a in atributos_por_clave.values()]]]],
                {'fields': ['id', 'name', 'attribute_id']}
            )
            for v in valores_odoo:
                valores_por_atributo.setdefault(v['attribute_id'][0], {})[v['name']] = v['id']

        for categoria, atributos in modelos.items():
            cat_id = categoria_ids[categoria]
            self.odoo_map[categoria] = {}

            for atributo, valores in atributos.items():
                existente = atributos_por_clave.get((atributo, cat_id))

                if existente:
                    existentes['atributos'] += 1
                    if existente['create_variant'] != 'no_variant':
                        log_message(f"   ⚠️ El atributo '{atributo}' ({categoria}) existe con create_variant='{existente['create_variant']}' en lugar de 'no_variant'. Revísalo en Odoo.")
                    attr_id = existente['id']
                    mapa_valores = valores_por_atributo.get(attr_id, {})
                    existentes['valores'] += sum(1 for v in valores if v in mapa_valores)

                    faltantes = [v for v in valores if v not in mapa_valores]
                    if faltantes:
                        nuevos_ids = self.execute(
                            'product.attribute.value', 'create',
                            [[{'name': v, 'attribute_id': attr_id} for v in faltantes]]
                        )
                        for v, vid in zip(faltantes, nuevos_ids):
                            mapa_valores[v] = vid
                        creadas['valores'] += len(faltantes)
                        log_message(f"   📝 '{atributo}' ({categoria}): {len(faltantes)} valores creados")
                else:
                    # Crear atributo con todos sus valores
                    attr_id = self.execute('product.attribute', 'create', [{
                        'name': atributo,
                        'category_id': cat_id,
                        'create_variant': 'no_variant',
                        'value_ids': [(0, 0, {'name': v}) for v in valores],
                    }])
                    valores_odoo = self.execute(
                        'product.attribute.value', 'search_read',
                        [[['attribute_id', '=', attr_id]]],
                        {'fields': ['id', 'name']}
                    )
                    mapa_valores = {v['name']: v['id'] for v in valores_odoo}
                    creadas['atributos'] += 1
                    creadas['valores'] += len(valores)
                    log_message(f"   📝 Atributo creado: '{atributo}' ({categoria}) ID: {attr_id} con {len(valores)} valores")

                faltan = [v for v in valores if v not in mapa_valores]
                if faltan:
                    raise Exception(f"No se pudieron verificar los valores {faltan} del atributo '{atributo}' ({categoria})")

                self.odoo_map[categoria][atributo] = {'id': attr_id, 'values': {v: mapa_valores[v] for v in valores}}

        log_message(f"\n📊 Resumen de sincronización:")
        log_message(f"   - Categorías de atributo: {creadas['categorias']} creadas, {existentes['categorias']} existentes")
        log_message(f"   - Atributos: {creadas['atributos']} creados, {existentes['atributos']} existentes")
        log_message(f"   - Valores: {creadas['valores']} creados, {existentes['valores']} existentes")


def cargar_prompt():
    """Carga el prompt desde .promptatributos"""
    if not os.path.exists(PROMPT_FILE_PATH):
        raise Exception(f"Archivo de prompt no encontrado: {PROMPT_FILE_PATH}")

    with open(PROMPT_FILE_PATH, 'r', encoding='utf-8') as file:
        prompt = file.read().strip()

    if not prompt:
        raise Exception("El archivo de prompt está vacío")

    return prompt


def llamar_api(messages, max_intentos=3):
    """Llama a la API de chat con reintentos. Devuelve el mensaje de la respuesta."""
    payload = {
        "model": AI_MODEL,
        "messages": messages,
        "max_tokens": 4000,
        "temperature": 0.2,
        "think": True
    }

    for intento in range(max_intentos):
        try:
            response = requests.post(API_URL, json=payload, timeout=300)
            response.raise_for_status()
            response_data = response.json()
            if 'choices' in response_data and response_data['choices']:
                return response_data['choices'][0]['message']
            raise ValueError("Respuesta de API inválida: sin choices")
        except Exception as e:
            log_message(f"    ⚠️ Error de API (intento {intento + 1}/{max_intentos}): {e}")
            if intento < max_intentos - 1:
                time.sleep(2 ** intento)

    return None


def extraer_json(contenido):
    """Extrae el primer objeto JSON de la respuesta de la IA"""
    if not contenido:
        return None
    contenido = re.sub(r'(?s)<think>.*?</think>', '', contenido)
    contenido = re.sub(r'```(?:json)?', '', contenido)
    inicio = contenido.find('{')
    fin = contenido.rfind('}')
    if inicio == -1 or fin == -1:
        return None
    try:
        return json.loads(contenido[inicio:fin + 1])
    except json.JSONDecodeError:
        return None


def seleccionar_valores_con_ia(prompt_base, producto, atributos):
    """
    Pide a la IA un valor por atributo.

    Returns:
        dict: {atributo: valor} con las respuestas de la IA, o None si falla
    """
    contexto = [f"Nombre: {producto['nombre']}"]
    if producto['marca']:
        contexto.append(f"Marca: {producto['marca']}")
    if producto['referencia']:
        contexto.append(f"Referencia del proveedor: {producto['referencia']}")
    if producto['descripcion_breve']:
        contexto.append(f"Descripción breve: {producto['descripcion_breve']}")
    if producto['descripcion_larga']:
        contexto.append(f"Descripción larga: {producto['descripcion_larga']}")

    lista_atributos = json.dumps(atributos, ensure_ascii=False, indent=1)

    messages = [
        {
            "role": "system",
            "content": "Eres un asistente experto en especificaciones de instrumentos musicales y equipos de sonido. Proporciona únicamente la respuesta final en JSON. No muestres tu razonamiento ni texto introductorio."
        },
        {
            "role": "user",
            "content": f"{prompt_base}\n\nPRODUCTO:\n" + "\n".join(contexto) + f"\n\nATRIBUTOS Y VALORES PERMITIDOS:\n{lista_atributos}"
        }
    ]

    mensaje = llamar_api(messages)
    if mensaje is None:
        return None

    return extraer_json(mensaje.get('content', ''))


def validar_respuesta(respuesta, atributos_odoo):
    """
    Convierte la respuesta de la IA en pares [attribute_id, value_id].
    Solo se aceptan valores de la lista permitida; el resto se deja en blanco.
    """
    pares = []
    en_blanco = []

    for atributo, datos in atributos_odoo.items():
        valor = respuesta.get(atributo) if respuesta else None
        if valor is None or not str(valor).strip():
            en_blanco.append(atributo)
            continue

        valores_normalizados = {normalizar(v): vid for v, vid in datos['values'].items()}
        value_id = valores_normalizados.get(normalizar(valor))
        if value_id:
            pares.append([datos['id'], value_id])
        else:
            en_blanco.append(atributo)
            log_message(f"    ⚠️ Valor no permitido para '{atributo}': '{valor}' (se deja en blanco)")

    return pares, en_blanco


BATCH_SIZE = 200
NOMBRES_MARCA = ['Marca', 'Brand', 'marca', 'brand']
CAMPO_REFERENCIA = 'x_studio_referencia_del_proveedor'


def html_a_texto(valor):
    """Convierte HTML a texto plano"""
    if not valor:
        return ''
    valor = re.sub(r'(?is)<(script|style).*?</\1>', ' ', str(valor))
    valor = re.sub(r'<[^>]+>', ' ', valor)
    return ' '.join(html.unescape(valor).split())


def obtener_ids_marca(manager):
    """Devuelve el conjunto de IDs de los atributos Marca/Brand"""
    atributos = manager.execute(
        'product.attribute', 'search_read',
        [[['name', 'in', NOMBRES_MARCA]]],
        {'fields': ['id', 'name']}
    )
    ids = {a['id'] for a in atributos}
    if ids:
        encontrados = ', '.join(f"{a['name']} (ID {a['id']})" for a in atributos)
        log_message(f"✅ Atributos de marca encontrados: {encontrados}")
    else:
        log_message("⚠️ No se encontró el atributo Marca/Brand en Odoo")
    return ids


def obtener_campos_producto(manager):
    """Devuelve los campos a leer de product.template, comprobando los opcionales"""
    campos = ['id', 'name', 'categ_id', 'public_categ_ids', 'website_description', 'website_meta_description', 'attribute_line_ids']
    disponibles = manager.execute('product.template', 'fields_get', [campos + [CAMPO_REFERENCIA]], {'attributes': ['type']})

    faltan = [c for c in campos if c != 'id' and c not in disponibles]
    if faltan:
        raise Exception(f"product.template no tiene los campos requeridos: {faltan}")

    if CAMPO_REFERENCIA in disponibles:
        campos.append(CAMPO_REFERENCIA)
    else:
        log_message(f"⚠️ Campo '{CAMPO_REFERENCIA}' no encontrado en product.template. Se continúa sin referencia del proveedor.")
    return campos


def cargar_categorias(manager, productos):
    """Devuelve {categ_id: nombre del último nivel} para las categorías de los productos"""
    categ_ids = list({p['categ_id'][0] for p in productos if p.get('categ_id')})
    if not categ_ids:
        return {}
    categorias = manager.execute('product.category', 'read', [categ_ids], {'fields': ['id', 'complete_name']})
    return {c['id']: str(c['complete_name']).split(' / ')[-1].strip() for c in categorias if c.get('complete_name')}


def cargar_categorias_publicas(manager, productos):
    """Devuelve {public_categ_id: nombre} para las categorías eCommerce de los productos"""
    public_ids = list({i for p in productos for i in p.get('public_categ_ids', [])})
    if not public_ids:
        return {}
    categorias = manager.execute('product.public.category', 'read', [public_ids], {'fields': ['id', 'name']})
    return {c['id']: str(c['name']).strip() for c in categorias if c.get('name')}


def obtener_categorias_producto(p, categorias, categorias_publicas):
    """Devuelve las categorías del producto (interna + eCommerce) sin repetir, en orden"""
    nombres = []
    if p.get('categ_id'):
        nombres.append(categorias.get(p['categ_id'][0]))
    nombres += [categorias_publicas.get(i) for i in p.get('public_categ_ids', [])]

    resultado = []
    for nombre in nombres:
        if nombre and nombre not in resultado:
            resultado.append(nombre)
    return resultado


def combinar_atributos(odoo_map, categorias_con_modelo):
    """
    Une los atributos de varias categorías en un solo diccionario {nombre: {'id', 'values'}}.
    Si un mismo nombre de atributo aparece en varias categorías, se le añade la categoría
    entre paréntesis para distinguirlos: 'Tapa (Bandurria)'.
    """
    if len(categorias_con_modelo) == 1:
        return odoo_map[categorias_con_modelo[0]]

    apariciones = {}
    for categoria in categorias_con_modelo:
        for atributo in odoo_map[categoria]:
            apariciones[atributo] = apariciones.get(atributo, 0) + 1

    combinados = {}
    for categoria in categorias_con_modelo:
        for atributo, datos in odoo_map[categoria].items():
            clave = f"{atributo} ({categoria})" if apariciones[atributo] > 1 else atributo
            combinados[clave] = datos
    return combinados


def cargar_lineas_atributo(manager, productos):
    """Devuelve {product_tmpl_id: [líneas]} con las líneas de atributo de los productos"""
    line_ids = [lid for p in productos for lid in p.get('attribute_line_ids', [])]
    lineas_por_producto = {}
    if not line_ids:
        return lineas_por_producto
    lineas = manager.execute(
        'product.template.attribute.line', 'read',
        [line_ids], {'fields': ['id', 'product_tmpl_id', 'attribute_id', 'value_ids']}
    )
    for linea in lineas:
        lineas_por_producto.setdefault(linea['product_tmpl_id'][0], []).append(linea)
    return lineas_por_producto


def obtener_marca(manager, lineas, ids_marca):
    """Devuelve el nombre del valor de marca del producto ('' si no tiene)"""
    for linea in lineas:
        if linea['attribute_id'][0] in ids_marca and linea['value_ids']:
            valores = manager.execute('product.attribute.value', 'read', [linea['value_ids'][:1]], {'fields': ['name']})
            if valores:
                return texto(valores[0]['name'])
    return ''


def run():
    """Función principal que ejecuta todo el proceso"""
    print("=" * 60)
    print("ACTUALIZAR ATRIBUTOS DE PRODUCTOS PUBLICADOS")
    print("=" * 60)

    start_time = datetime.now()

    try:
        log_message(f"🤖 IA: modelo {AI_MODEL} en {API_URL}")
        prompt_base = cargar_prompt()

        # Paso 1: Modelos y sincronización con Odoo
        modelos = cargar_modelos()
        manager = CategoryAttributeManager()
        manager.connect_to_odoo()
        manager.verify_attribute_category_field()
        manager.sync_attributes(modelos)

        ids_marca = obtener_ids_marca(manager)
        campos = obtener_campos_producto(manager)

        # Paso 2: Productos publicados
        product_ids = manager.execute('product.template', 'search', [[['is_published', '=', True]]], {'order': 'id'})
        total = len(product_ids)
        log_message(f"\n📖 {total} productos publicados en Odoo")

        if total == 0:
            log_message("ℹ️ No hay productos para procesar")
            return True

        # Contadores
        actualizados = 0
        sin_modelo = 0
        con_atributos = 0
        fallidos = 0
        sin_valores = 0
        atributos_asignados = 0
        atributos_en_blanco = 0
        categorias_sin_modelo = {}
        contador = 0

        # Paso 3: Procesar por lotes
        for inicio in range(0, total, BATCH_SIZE):
            lote_ids = product_ids[inicio:inicio + BATCH_SIZE]
            productos = manager.execute('product.template', 'read', [lote_ids], {'fields': campos})
            categorias = cargar_categorias(manager, productos)
            categorias_publicas = cargar_categorias_publicas(manager, productos)
            lineas_por_producto = cargar_lineas_atributo(manager, productos)

            for p in productos:
                contador += 1
                nombre = texto(p.get('name'))
                log_message(f"\n🔧 Procesando artículo {contador}/{total} (ID {p['id']}): {nombre}")

                try:
                    categorias_producto = obtener_categorias_producto(p, categorias, categorias_publicas)
                    categorias_con_modelo = [c for c in categorias_producto if c in manager.odoo_map]
                    if not categorias_con_modelo:
                        sin_modelo += 1
                        clave = ', '.join(categorias_producto) or '(sin categoría)'
                        categorias_sin_modelo[clave] = categorias_sin_modelo.get(clave, 0) + 1
                        log_message(f"    ⏭️ Categorías '{clave}' sin modelo de atributos")
                        continue

                    lineas = lineas_por_producto.get(p['id'], [])
                    otras = [l['attribute_id'][1] for l in lineas if l['attribute_id'][0] not in ids_marca]
                    if otras:
                        con_atributos += 1
                        log_message(f"    ⏭️ Ya tiene atributos distintos de marca: {', '.join(otras)}")
                        continue

                    log_message(f"    📂 Categorías con modelo: {', '.join(categorias_con_modelo)}")

                    producto = {
                        'nombre': nombre,
                        'marca': obtener_marca(manager, lineas, ids_marca),
                        'referencia': texto(p.get(CAMPO_REFERENCIA) or ''),
                        'descripcion_breve': texto(p.get('website_meta_description') or ''),
                        'descripcion_larga': html_a_texto(p.get('website_description')),
                    }

                    atributos_odoo = combinar_atributos(manager.odoo_map, categorias_con_modelo)
                    atributos_permitidos = {a: list(d['values'].keys()) for a, d in atributos_odoo.items()}

                    respuesta = seleccionar_valores_con_ia(prompt_base, producto, atributos_permitidos)
                    if respuesta is None:
                        fallidos += 1
                        log_message("    ❌ La IA no devolvió una respuesta válida. Producto sin cambios.")
                        continue

                    pares, en_blanco = validar_respuesta(respuesta, atributos_odoo)
                    atributos_en_blanco += len(en_blanco)

                    if not pares:
                        sin_valores += 1
                        log_message("    ℹ️ Ningún atributo con valor válido. Producto sin cambios.")
                        continue

                    manager.execute('product.template', 'write', [[p['id']], {
                        'attribute_line_ids': [
                            (0, 0, {'attribute_id': int(attr_id), 'value_ids': [(6, 0, [int(value_id)])]})
                            for attr_id, value_id in pares
                        ]
                    }])

                    actualizados += 1
                    atributos_asignados += len(pares)
                    log_message(f"    ✅ {len(pares)}/{len(atributos_odoo)} atributos subidos a Odoo")
                    if en_blanco:
                        log_message(f"    ℹ️ En blanco: {', '.join(en_blanco)}")

                except Exception as e:
                    fallidos += 1
                    log_message(f"    ❌ Error procesando el producto: {e}")

        # Resumen final
        duration = datetime.now() - start_time
        log_message("\n" + "=" * 50)
        log_message("📋 RESUMEN FINAL")
        log_message("=" * 50)
        log_message(f"Productos publicados: {total}")
        log_message(f"Productos actualizados: {actualizados}")
        log_message(f"Productos sin modelo de categoría: {sin_modelo}")
        log_message(f"Productos que ya tenían atributos: {con_atributos}")
        log_message(f"Productos sin ningún valor válido: {sin_valores}")
        log_message(f"Productos con error: {fallidos}")
        log_message(f"Atributos asignados: {atributos_asignados}")
        log_message(f"Atributos en blanco: {atributos_en_blanco}")
        if categorias_sin_modelo:
            log_message("Categorías sin modelo de atributos:")
            for cat, num in sorted(categorias_sin_modelo.items(), key=lambda x: -x[1]):
                log_message(f"   - {cat}: {num} productos")
        log_message(f"Tiempo de ejecución: {duration.total_seconds():.2f} segundos")

        return True

    except Exception as e:
        log_message(f"\n❌ Error durante el proceso: {e}")
        return False


def main():
    """Función principal"""
    if not run():
        sys.exit(1)


if __name__ == "__main__":
    main()
