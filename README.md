# Kalshi Predictor

Python market-analysis and trading experiments. This is source code, not a hosted service. Review configuration and trading behavior before running anything that can submit orders.

## Fresh checkout

Use Python 3.10 or newer. From the repository directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell and use
`Copy-Item .env.example .env` for the last step.

The example uses the demo environment and a $100 **simulated** starting balance.
It deliberately leaves credentials blank. `STARTING_BALANCE` can be omitted or
blank to use 100; otherwise set a numeric dollar amount.

## Run the console

Set `KALSHI_API_KEY_ID` and `KALSHI_PRIVATE_KEY` in your local `.env` using a
matching demo API key pair. `KALSHI_PRIVATE_KEY` is the complete PEM **contents**,
including its BEGIN/END lines, not a path to a key file. Put it in quotes and use
`\n` between lines, or use a quoted multiline value. Keep
`KALSHI_ENVIRONMENT=demo` for initial setup; production uses separate credentials
and endpoints.

```sh
python main.py
```

With blank credentials the console exits with a configuration message explaining
what to fill in. With credentials configured it opens the menu; choose `0` to exit.
Opening the menu does not contact the API. Market browsing and connection checks
do make authenticated requests. Review the trading and auto-trader options before
using them; do not use production credentials for a smoke test.

`COINGECKO_API_KEY` is optional for the console and used by `price_collector.py`.
The standalone collector scripts are separate experiments, not part of this
startup check; in particular, `crypto_collector.py` currently targets production
directly, regardless of `KALSHI_ENVIRONMENT`.

## Offline regression checks

After installing dependencies, run:

```sh
python -m unittest discover -s tests -v
```

These tests copy the source into temporary directories, use only blank or fake
configuration, and block network requests. They cover the copied example, blank
balance defaults, credential guidance, and opening/exiting the menu. They do not
validate real authentication, market data, or trading. `test_connection.py` is a
manual network diagnostic, not an offline test; do not run it as part of this
check.

## Local data

Keep private keys, account data, collected prices and databases outside Git. The
console creates its ignored `data/` directory as needed. No credentials or trading
records are included in the current source tree.

The original repository history is retained. The server checkout had a stale remote name; this repository is the matching canonical source.
