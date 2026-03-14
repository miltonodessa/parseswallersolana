# Solana Wallet Parser

Парсер смарт-кошельков в сети Solana с Telegram-ботом.

## Возможности

- **Парсинг холдеров токена** — получение всех кошельков, державших токен
- **Анализ кошельков** — ROI, WinRate, PnL, количество сделок
- **Фильтрация смарт-кошельков** — по ROI, WinRate, количеству сделок
- **Кросс-трейдеры** — кошельки, торговавшие несколькими токенами
- **Dev-кошельки** — нахождение деплоер-кошельков токенов
- **Экспорт** — CSV и TXT с результатами
- **Telegram-бот** — управление через команды

## Установка

```bash
pip install -r requirements.txt
cp .env.example .env
# Заполните .env своими ключами
```

## Конфигурация `.env`

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота (от @BotFather) |
| `SOLANA_RPC_URL` | URL RPC (публичный или Helius) |
| `HELIUS_API_KEY` | Ключ Helius (для полного парсинга) |
| `BIRDEYE_API_KEY` | Ключ Birdeye (для PnL/ROI) |
| `MIN_ROI_FILTER` | Минимальный ROI % (по умолчанию 50) |
| `MIN_WINRATE_FILTER` | Минимальный WinRate % (по умолчанию 50) |
| `MIN_TRADES_FILTER` | Минимальное количество сделок (по умолчанию 5) |

> **Рекомендация**: Используйте [Helius](https://helius.dev) RPC + API Key для получения полного списка холдеров (до 1000+). Без Helius доступны только топ-20 холдеров через стандартный RPC.

## Запуск

### Telegram-бот
```bash
python main.py bot
```

### CLI

```bash
# Парсинг токена по mint-адресу
python main.py parse_token <MINT_ADDRESS>

# Поиск кросс-трейдеров
python main.py cross_traders <MINT1> <MINT2> <MINT3>

# Экспорт dev-кошельков
python main.py dev_wallets <MINT1> <MINT2>
```

## Команды Telegram-бота

| Команда | Описание |
|---|---|
| `/parse_token <MINT>` | Парсить кошельки по контракту |
| `/parse_cross_traders <M1> <M2>` | Найти кросс-трейдеров |
| `/export_token_devs <M1> <M2>` | Экспорт dev-кошельков |
| `/set_filter roi=100 wr=60 trades=10` | Настроить фильтры |
| `/filters` | Показать текущие фильтры |

## Структура проекта

```
parseswallersolana/
├── main.py                    # Точка входа (бот + CLI)
├── config.py                  # Конфигурация
├── requirements.txt
├── .env.example
├── solana_parser/
│   ├── rpc_client.py          # Solana RPC + Helius + Birdeye
│   ├── token_parser.py        # Парсинг холдеров токена
│   ├── wallet_analyzer.py     # Анализ ROI/WR/PnL
│   ├── cross_traders.py       # Поиск кросс-трейдеров
│   └── exporter.py            # Экспорт в CSV/TXT
├── bot/
│   ├── telegram_bot.py        # Запуск бота
│   └── handlers.py            # Обработчики команд
└── results/                   # Директория для результатов
```
