#!/usr/bin/env python3
import random
import time
import sys
from datetime import datetime

# Colores ANSI
V = '\033[92m'  # verde
A = '\033[93m'  # amarillo
R = '\033[91m'  # rojo
C = '\033[96m'  # cian
B = '\033[94m'  # azul
G = '\033[90m'  # gris
N = '\033[0m'   # normal
BOLD = '\033[1m'

def slow_print(text, delay=None):
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(delay if delay else random.uniform(0.005, 0.02))
    print()

def progress_bar(label, duration=None):
    duration = duration or random.uniform(2, 6)
    steps = 30
    for i in range(steps + 1):
        pct = int((i / steps) * 100)
        bar = '█' * i + '░' * (steps - i)
        sys.stdout.write(f'\r{C}{label}{N} [{V}{bar}{N}] {pct}%')
        sys.stdout.flush()
        time.sleep(duration / steps)
    print(f'  {V}✓{N}')

def log(nivel, msg):
    ts = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    colores = {'INFO': B, 'WARN': A, 'ERROR': R, 'OK': V, 'DEBUG': G}
    color = colores.get(nivel, N)
    print(f'{G}[{ts}]{N} {color}{nivel:5}{N} {msg}')
    time.sleep(random.uniform(0.05, 0.4))

# --- Bloques de "trabajo" ---

def compilar():
    slow_print(f'{BOLD}$ npm run build{N}')
    time.sleep(0.5)
    modulos = ['react-dom', 'webpack-cli', 'babel-loader', 'typescript',
               'eslint-plugin', 'postcss', 'tailwindcss', 'vite-plugin']
    for m in random.sample(modulos, 5):
        log('INFO', f'Compilando módulo {C}{m}{N}...')
    progress_bar('Bundling assets')
    log('OK', f'Build completado en {random.randint(12, 45)}.{random.randint(10,99)}s')

def tests():
    slow_print(f'{BOLD}$ pytest --verbose{N}')
    tests_list = [
        'test_user_authentication', 'test_database_connection',
        'test_api_endpoints', 'test_payment_gateway', 'test_cache_layer',
        'test_email_service', 'test_websocket_handler', 'test_rate_limiter',
        'test_jwt_validation', 'test_redis_pipeline'
    ]
    for t in random.sample(tests_list, 7):
        time.sleep(random.uniform(0.1, 0.6))
        if random.random() > 0.9:
            print(f'  {R}✗{N} {t} ... {R}FAILED{N}')
        else:
            print(f'  {V}✓{N} {t} ... {V}PASSED{N} ({random.randint(3, 240)}ms)')
    print(f'\n{V}={"="*40}{N}\n{V}Tests: {random.randint(45,120)} passed, {random.randint(0,2)} failed{N}\n')

def deploy():
    slow_print(f'{BOLD}$ kubectl apply -f production.yaml{N}')
    log('INFO', 'Conectando con cluster de producción...')
    log('INFO', 'Autenticación con IAM: OK')
    log('WARN', 'Detectado desfase de configuración en pod-3')
    progress_bar('Desplegando pods', duration=4)
    log('OK', f'{random.randint(6,12)} pods desplegados correctamente')

def entrenar_ia():
    slow_print(f'{BOLD}$ python train.py --model transformer --epochs 50{N}')
    log('INFO', 'Cargando dataset (2.4M muestras)...')
    log('INFO', 'GPU detectada: NVIDIA A100 (40GB)')
    for epoch in range(1, random.randint(4, 7)):
        loss = round(random.uniform(0.1, 2.5) / epoch, 4)
        acc = round(min(0.99, 0.6 + epoch * 0.08 + random.uniform(-0.05, 0.05)), 4)
        print(f'  Epoch {epoch}/50 - loss: {A}{loss}{N} - accuracy: {V}{acc}{N} - lr: 0.0001')
        time.sleep(random.uniform(0.8, 1.8))

def scan_seguridad():
    slow_print(f'{BOLD}$ nmap -sV -A target.internal{N}')
    puertos = [22, 80, 443, 3306, 5432, 6379, 8080, 9200]
    for p in random.sample(puertos, 5):
        time.sleep(random.uniform(0.3, 0.9))
        servicios = {22:'ssh', 80:'http', 443:'https', 3306:'mysql',
                    5432:'postgres', 6379:'redis', 8080:'http-proxy', 9200:'elastic'}
        print(f'  {V}{p}/tcp{N}   open  {servicios[p]}')

def analisis_datos():
    slow_print(f'{BOLD}$ python analyze.py --dataset Q4_2026.parquet{N}')
    log('INFO', 'Cargando 8.2GB en memoria...')
    log('INFO', 'Detectando outliers con IsolationForest')
    progress_bar('Procesando registros', duration=3)
    log('OK', f'{random.randint(400000, 900000)} registros procesados')
    log('INFO', f'Correlación encontrada: {round(random.uniform(0.7, 0.95), 3)}')

# --- Bucle principal ---

tareas = [compilar, tests, deploy, entrenar_ia, scan_seguridad, analisis_datos]

print(f'\n{V}{BOLD}╔══════════════════════════════════════════╗{N}')
print(f'{V}{BOLD}║   Sesión de trabajo iniciada             ║{N}')
print(f'{V}{BOLD}╚══════════════════════════════════════════╝{N}\n')

try:
    while True:
        tarea = random.choice(tareas)
        tarea()
        pausa = random.uniform(2, 5)
        print(f'\n{G}--- esperando {int(pausa)}s ---{N}\n')
        time.sleep(pausa)
except KeyboardInterrupt:
    print(f'\n\n{A}Sesión finalizada. Buen trabajo hoy 😎{N}\n')