# Solana Smart Wallet Parser

Парсер смарт-кошельков сети Solana с фильтрами по методике **Froggy v2**.

## Установка

```bash
pip install -r requirements.txt
cp .env.example .env
# Заполните API ключи в .env
```

## Конфигурация `.env`

| Переменная | Описание |
|---|---|
| `SOLANA_RPC_URL` | URL RPC-ноды (публичный или Helius) |
| `HELIUS_API_KEY` | Ключ Helius ([helius.dev](https://helius.dev)) — **рекомендуется** |
| `BIRDEYE_API_KEY` | Ключ Birdeye ([birdeye.so](https://birdeye.so)) — для ROI/WinRate |

> **Без Helius** доступны только топ-20 холдеров (лимит стандартного RPC).
> **Без Birdeye** ROI и WinRate вычисляются только через Helius транзакции (приблизительно).

## Использование

### Парсинг кошельков по токену

```bash
python main.py parse_token <MINT_ADDRESS>
```

С кастомными фильтрами:

```bash
python main.py parse_token <MINT> --roi 100 --wr 60 --balance 5 --tokens 5
```

### Поиск кросс-трейдеров

```bash
python main.py cross_traders <MINT1> <MINT2> <MINT3>
```

### Dev-кошельки токенов

```bash
python main.py dev_wallets <MINT1> <MINT2>
```

## Фильтры (Froggy v2)

| Флаг | Метрика | По умолчанию | Описание |
|---|---|---|---|
| `--roi N` | ROI % | 50 | Минимальный ROI |
| `--wr N` | WinRate % | 50 | Минимальный процент прибыльных сделок |
| `--fast N` | Fast Trades % | 15 | **Максимум** сделок < 3 мин (выше → rug/бот) |
| `--smtb N` | SMTB % | 10 | **Максимум** продаж без предшествующей покупки |
| `--balance N` | SOL баланс | 2 | Минимальный баланс SOL |
| `--tokens N` | Tokens Total | 3 | Минимум разных токенов (< 3 = свежий кошелёк) |
| `--freq N` | Trades/week | 1 | Минимум сделок в неделю |
| `--trades N` | Total trades | 5 | Минимум сделок всего |

Все дефолты переопределяются через `.env` (см. `.env.example`).

## Структура проекта

```
├── main.py                   # CLI точка входа
├── config.py                 # Настройки / дефолты фильтров
├── requirements.txt
├── .env.example
├── solana_parser/
│   ├── rpc_client.py         # Async RPC + Helius + Birdeye клиент
│   ├── token_parser.py       # Получение холдеров токена
│   ├── wallet_analyzer.py    # Расчёт метрик + фильтрация
│   ├── cross_traders.py      # Кросс-трейдеры
│   └── exporter.py           # Экспорт CSV / TXT
└── results/                  # Результаты парсинга
```
