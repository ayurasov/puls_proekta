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
npm install
npm run build

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
npm install
npm run build

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 5. Быстрый запуск

```bash
cd /puls_proekta
source venv/bin/activate
gunicorn app:app --bind 0.0.0.0:39663
```

Должно появиться: `serving on port 5000` (Node.js запускается автоматически при первом запросе).

Учётные данные по умолчанию:
- **Логин:** admin
- **Пароль:** pulsbald198!

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
# Сохранить базу
cp puls.db ../puls.db.bak
# Загрузить новый архив
unzip puls-server-vX.X.zip -d .
npm install
npm run build
# Восстановить базу
cp ../puls.db.bak ./puls.db
sudo systemctl start puls
```

---

## Копирование с Windows

```powershell
pscp.exe -P 39651 -r d:\Dev\puls_proekta\puls_proekta yurasov@10.90.0.120:/home/yurasov/
```

## Открываем порт (один раз для внешнего доступа)

sudo ufw allow 39663/tcp

## Запуск через tmux (без systemd)

Альтернатива systemd — запуск в сессии tmux. Сервер продолжает работать после закрытия терминала.

### Создать сессию и запустить

```bash
# Создать сессию
tmux new -s puls

# Внутри сессии:
cd /puls_proekta
source venv/bin/activate
npm install
npm run build

gunicorn app:app --bind 0.0.0.0:39663
```

Приложение доступно по адресу `http://<IP-сервера>:39663`.

### Отключиться от сессии (сервер продолжает работать)

Закройте окно PuTTY или нажмите `Ctrl+B` затем `D`.

### Вернуться в сессию

```bash
tmux attach -t puls
```

### Остановка сервера

```bash
# Подключиться к сессии
tmux attach -t puls

# Нажать Ctrl+C

# Удалить сессию
tmux kill-session -t puls_proekta
```

---

## Структура проекта

```
puls_proekta/
├── app.py              # Python WSGI-обёртка (gunicorn)
├── requirements.txt    # Python-зависимости (gunicorn)
├── dist/
│   ├── index.cjs       # Node.js сервер (собранный)
│   └── public/         # Статические файлы (React)
├── server/             # Исходники бэкенда
├── client/             # Исходники фронтенда
├── shared/             # Общие типы
├── puls.db             # База данных SQLite (создаётся автоматически)
└── package.json
```
