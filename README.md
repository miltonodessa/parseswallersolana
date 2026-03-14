# Solana Smart Wallet Parser

Парсер смарт-кошельков сети Solana с фильтрами по методике **Froggy v2**.

## Установка

```bash
pip install -r requirements.txt
```

## Настройка

Все настройки — в файле **`settings.py`**. Просто открой и отредактируй:

```python
# Подключение
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
HELIUS_API_KEY = "ВАШ_КЛЮЧ"   # helius.dev
BIRDEYE_API_KEY = "ВАШ_КЛЮЧ"  # birdeye.so

# Фильтры
MIN_ROI             = 50    # мин. ROI %
MIN_WINRATE         = 50    # мин. WinRate %
MAX_FAST_TRADES_PCT = 15    # макс. быстрых сделок < 3 мин, %
MAX_SMTB_PCT        = 10    # макс. SMTB %
MIN_BALANCE_SOL     = 2     # мин. баланс SOL
MIN_TOKENS_TOTAL    = 3     # мин. разных токенов
MIN_TRADES_PER_WEEK = 1     # мин. сделок в неделю
MIN_TOTAL_TRADES    = 5     # мин. сделок всего
```

> **Helius** ([helius.dev](https://helius.dev)) — нужен для полного списка холдеров (1000+). Без него: только топ-20.
> **Birdeye** ([birdeye.so](https://birdeye.so)) — нужен для точных ROI/WinRate. Без него: приблизительный расчёт из транзакций.

## Запуск

```bash
# Парсинг кошельков токена
python main.py parse_token <MINT_ADDRESS>

# Кросс-трейдеры (кошельки из нескольких токенов)
python main.py cross_traders <MINT1> <MINT2> <MINT3>

# Dev-кошельки токенов
python main.py dev_wallets <MINT1> <MINT2>
```

## Фильтры (Froggy v2)

| Параметр | Что значит | Рекомендация |
|---|---|---|
| `MIN_ROI` | Минимальный ROI % | ≥ 50% |
| `MIN_WINRATE` | Мин. % прибыльных сделок | ≥ 50% (минимум 30%) |
| `MAX_FAST_TRADES_PCT` | Макс. % сделок < 3 мин | ≤ 15% (выше = rug/бот) |
| `MAX_SMTB_PCT` | Макс. % "продаж без покупки" | ≤ 10% (~1 из 10 сделок) |
| `MIN_BALANCE_SOL` | Мин. баланс SOL | ≥ 2 SOL |
| `MIN_TOKENS_TOTAL` | Мин. кол-во токенов в истории | ≥ 3 (меньше = свежий кошелёк) |
| `MIN_TRADES_PER_WEEK` | Мин. сделок в неделю | ≥ 1 (идеально 3+) |
| `MIN_TOTAL_TRADES` | Абсолютный минимум сделок | ≥ 5 |

## Структура

```
├── settings.py              ← ВСЕ настройки здесь
├── main.py                  ← запуск
├── config.py                ← внутренние URL (не трогать)
├── requirements.txt
└── solana_parser/
    ├── rpc_client.py        ← RPC + Helius + Birdeye
    ├── token_parser.py      ← холдеры токена
    ├── wallet_analyzer.py   ← метрики и фильтрация
    ├── cross_traders.py     ← кросс-трейдеры
    └── exporter.py          ← CSV / TXT экспорт
```
