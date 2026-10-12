# Пульс Проекта (© Alexander Yurasov) — развёртывание на Ubuntu 24.04

Веб-приложение для управления проектами с диаграммами Ганта, ролевой моделью, LDAP-аутентификацией. Бэкенд: Node.js + Express + SQLite. Запуск через gunicorn (Python-обёртка запускает Node.js и проксирует запросы).

## Технологии

- Node.js 20+ / Express
- SQLite (better-sqlite3)
- React + Vite + Tailwind CSS
- Gunicorn (продакшен-обёртка)

---

## Быстрый запуск (временный, без systemd/Nginx)

```bash
cd /puls_proekta
source venv/bin/activate
npm ci
npm run build
npm run test:gantt

gunicorn app:app --bind 0.0.0.0:39663
```

Приложение будет доступно по адресу `http://<IP-сервера>:39663`.

Остановка — `Ctrl+C`. Для постоянной работы — `nohup`:

```bash
nohup gunicorn app:app --bind 0.0.0.0:39663 > /tmp/puls.log 2>&1 &
```

---

## Развёртывание на Ubuntu 24.04

### 1. Установка системных пакетов

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx curl
```

### 2. Установка Node.js 20

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt install -y nodejs
node -v   # должно быть v20.x.x
```

### 3. Создание директории и копирование файлов

```bash
sudo mkdir -p /puls_proekta
sudo chown $USER:$USER /puls_proekta
# Скопируйте содержимое архива в /puls_proekta
unzip puls-server-v2.6.1.zip -d /puls_proekta
```

### 4. Установка зависимостей

```bash
cd /puls_proekta
npm ci
npm run build
npm run test:gantt

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`npm run build` проверяет отслеживаемые production-артефакты и финализирует CSS-слои диаграммы. Полная пересборка React/Express недоступна, поскольку этот репозиторий содержит `dist`, но не содержит исходные каталоги `client`, `server` и `shared`.

### 5. Быстрый запуск

```bash
cd /puls_proekta
source venv/bin/activate
gunicorn app:app --bind 0.0.0.0:39663
```

Должно появиться: `serving on port 5000` (Node.js запускается автоматически при первом запросе).

После первого входа замените первоначальные учётные данные администратора на уникальный пароль и не храните рабочий пароль в документации или репозитории.

### 6. Настройка Gunicorn через systemd

Создайте файл `/etc/systemd/system/puls.service`:

```ini
[Unit]
Description=Puls Proekta (Gunicorn + Node.js)
After=network.target

[Service]
User=root
WorkingDirectory=/puls_proekta
Environment="PATH=/puls_proekta/venv/bin:/usr/bin"
ExecStart=/puls_proekta/venv/bin/gunicorn app:app --bind 0.0.0.0:39663
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Запуск:

```bash
sudo systemctl daemon-reload
sudo systemctl enable puls
sudo systemctl start puls
sudo systemctl status puls
```

### 7. Настройка Nginx (обратный прокси)

Создайте файл `/etc/nginx/sites-available/puls`:

```nginx
server {
    listen 80;
    server_name ваш-домен-или-ip;
    client_max_body_size 20M;

    location / {
        proxy_pass http://127.0.0.1:39663;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Активация:

```bash
sudo ln -s /etc/nginx/sites-available/puls /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

### 8. Настройка HTTPS (опционально)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d ваш-домен
```

---

## Управление

### Перезапуск приложения

```bash
sudo systemctl restart puls
```

### Просмотр логов

```bash
sudo journalctl -u puls -f
```

### Резервное копирование базы данных

```bash
sudo cp /puls_proekta/puls.db /puls_proekta/puls-backup-$(date +%Y%m%d).db
```

### Обновление приложения

```bash
cd /puls_proekta
sudo systemctl stop puls
cp puls.db ../puls.db.bak
git fetch origin
git switch v1.0.1
git pull --ff-only origin v1.0.1
npm ci
npm run build
npm run test:gantt
cp ../puls.db.bak ./puls.db
sudo systemctl start puls
```

---

## Копирование с Windows

```powershell
pscp.exe -P 39651 -r puls-server-v2.6.1.zip yurasov@10.90.0.120:/home/yurasov/
```

## Запуск через tmux (без systemd)

```bash
tmux new -s puls
cd /puls_proekta
source venv/bin/activate
npm ci
npm run build
npm run test:gantt
gunicorn app:app --bind 0.0.0.0:39663
```

Отключиться: `Ctrl+B`, затем `D`. Вернуться: `tmux attach -t puls`. Остановить: `Ctrl+C`, затем `tmux kill-session -t puls`.

---

## Фактическая структура checkout

```text
puls_proekta/
├── app.py
├── requirements.txt
├── dist/
│   ├── index.cjs
│   └── public/
├── script/
│   └── build.mjs
├── tests/
│   └── gantt-layering.test.mjs
├── package.json
└── package-lock.json
```

---

## Проверка v1.0.1

Версия `v1.0.1` исправляет порядок слоёв диаграммы Ганта: линии вех, их подписи и линия текущей даты отображаются поверх полос задач. Изменение не затрагивает API, структуру данных проектов и схему SQLite.

```bash
npm ci
npm run build
npm run test:gantt
node --check dist/index.cjs
python3 -m py_compile app.py
```

Подробные шаги развёртывания, smoke-проверки и отката приведены в `RUNBOOK_v1.0.1.md`.
