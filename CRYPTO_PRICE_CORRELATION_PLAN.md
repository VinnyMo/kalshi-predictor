# Crypto Price Correlation Analysis Plan

## Objective
Supplement the Kalshi crypto auto-trading system by collecting actual cryptocurrency price data and correlating it with market outcomes to create more robust trading indicators.

---

## Key Discovery: Kalshi Settlement Methodology

Kalshi uses **CF Benchmarks Real-Time Indexes (CFB RTIs)** for settlement:
- CFB RTI reports a price **once per second**
- At expiration, **60 RTI prices are collected** (last minute before close)
- Settlement price = **arithmetic mean of these 60 prices**
- Source: [Kalshi Crypto Markets Help](https://help.kalshi.com/markets/popular-markets/crypto-markets)

This means knowing the exact price 1 minute before close is critical for predicting outcomes.

---

## Data Source Options

### Option 1: CF Benchmarks API (Ideal but Licensed)
**Pros:**
- Exact same data source Kalshi uses for settlement
- Per-second updates
- BTC: `BRTI` (Real-Time Index), `BRR` (Reference Rate)
- ETH: `ETHUSD_RTI`
- SOL: `SOLUSD_RTI`

**Cons:**
- Requires commercial license
- Contact: licensing@cfbenchmarks.com
- Terms of service prohibit scraping

**Recommendation:** Skip for now unless you want to pursue a license.

---

### Option 2: CoinGecko API (Recommended - Free)
**Endpoint:** `https://api.coingecko.com/api/v3/simple/price`

**Free Tier (Demo Plan):**
- Rate limit: **30 calls/min** (with free API key)
- Monthly cap: 10,000 calls
- Price updates: Every 20-30 seconds
- No per-second data, but good enough for correlation analysis

**Example Request:**
```
GET https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_last_updated_at=true
```

**Response:**
```json
{
  "bitcoin": {"usd": 92533, "last_updated_at": 1768868532},
  "ethereum": {"usd": 3185.39, "last_updated_at": 1768868533},
  "solana": {"usd": 133.43, "last_updated_at": 1768868527}
}
```

**Historical Data (Free Tier):**
- Past 24 hours: 5-minute granularity
- 2-90 days: Hourly granularity
- Enterprise only: Minute-level data

**Setup:**
1. Create free account at [coingecko.com](https://www.coingecko.com)
2. Go to Developer Dashboard
3. Generate Demo API key
4. Use key as query parameter: `?x_cg_demo_api_key=YOUR_KEY`

---

### Option 3: Other Free Alternatives

| API | Rate Limit | Notes |
|-----|------------|-------|
| [CoinMarketCap](https://coinmarketcap.com/api/) | 30/min (basic) | Requires signup |
| [DIA](https://www.diadata.org/free-crypto-api/) | Generous | GraphQL & REST |
| [LiveCoinWatch](https://www.livecoinwatch.com/tools/api) | Varies | Simple REST |

---

## Implementation Plan

### Phase 1: Price Collector Script
**File:** `price_collector.py`

Functionality:
1. Fetch BTC, ETH, SOL prices every 30 seconds from CoinGecko
2. Store in JSON/SQLite with timestamps
3. Run alongside existing `crypto_collector.py`

Data structure:
```python
{
    "timestamp": "2026-01-19T19:00:00+00:00",
    "bitcoin": {"usd": 92533.00, "source_updated_at": 1768868532},
    "ethereum": {"usd": 3185.39, "source_updated_at": 1768868533},
    "solana": {"usd": 133.43, "source_updated_at": 1768868527}
}
```

### Phase 2: Correlation Analyzer
**File:** `price_correlation_analyzer.py`

Analysis goals:
1. **Match price data to market outcomes**
   - Link price at T-15min, T-10min, T-5min, T-1min to Kalshi settlement
   - Identify which price brackets each observation fell into

2. **Calculate correlation metrics**
   - Price movement direction vs market outcome
   - Volatility in final 5 minutes vs prediction accuracy
   - Momentum indicators (rate of change)

3. **Generate enhanced trading rules**
   ```python
   # Example rule structure
   {
       "series": "KXBTC15M",
       "condition": "price_momentum",
       "threshold": {"min_change_pct": -0.05, "max_change_pct": 0.05},
       "time_window_minutes": 5,
       "predicted_side": "stays_in_bracket",
       "confidence": 0.85,
       "sample_count": 150
   }
   ```

### Phase 3: Integration with Auto-Trader
**Updates to:** `crypto_autotrader.py`

New indicators to consider:
1. **Price momentum** - Is price trending toward or away from bracket boundaries?
2. **Volatility spike detection** - High volatility = avoid trading
3. **Bracket distance** - How close is current price to bracket boundaries?
4. **Cross-asset correlation** - If BTC spikes, expect ETH/SOL to follow

---

## Correlation Hypotheses to Test

### Hypothesis 1: Momentum Continuation
If price has been steadily moving in one direction for 10+ minutes, it's likely to continue (or at least not reverse sharply) in the final 5 minutes.

### Hypothesis 2: Volatility Clustering
High volatility periods cluster together. If the last 15-minute window saw a price swing >2%, avoid betting on the next window.

### Hypothesis 3: Mean Reversion Near Boundaries
If price is very close to a bracket boundary with only 2-3 minutes remaining, small mean reversion is more likely than a boundary cross.

### Hypothesis 4: Cross-Asset Leading Indicators
BTC often leads ETH/SOL by 30-60 seconds. A sudden BTC move could predict ETH/SOL movements.

### Hypothesis 5: Time-of-Day Patterns
Certain times (market opens, hourly marks) may have higher volatility. Avoid or exploit these patterns.

---

## Data Collection Requirements

To have meaningful correlation data, we need:
- **Minimum:** 1 week of continuous price collection (672 15-minute periods)
- **Recommended:** 2-4 weeks for statistical significance
- **Storage:** ~1MB per day of JSON data

---

## Project Structure (New Files)

```
/home/maestro/kalshi/
├── price_collector.py           # NEW: Fetches real crypto prices
├── price_correlation_analyzer.py # NEW: Correlates prices with outcomes
├── data/
│   ├── crypto_prices/           # NEW: Price history
│   │   └── prices.json
│   └── crypto_analysis/
│       └── price_correlations.json  # NEW: Correlation results
```

---

## Setup Instructions

### Step 1: Get CoinGecko API Key (Free)
1. Go to https://www.coingecko.com/en/api/pricing
2. Click "Create Free Account"
3. Log in and go to Developer Dashboard
4. Click "+ Add New Key"
5. Copy the API key

### Step 2: Configure Environment
Add to `.env`:
```
COINGECKO_API_KEY=your_demo_api_key_here
```

### Step 3: Run Price Collector
In a new terminal:
```bash
cd /home/maestro/kalshi
source venv/bin/activate
python price_collector.py
```

### Step 4: Collect Data
Let both collectors run for 1-2 weeks while the auto-trader operates. This builds the correlation dataset.

### Step 5: Run Correlation Analysis
```bash
python price_correlation_analyzer.py
```

### Step 6: Update Trading Rules
Review correlation findings and update `crypto_autotrader.py` with new indicators.

---

## Expected Outcomes

1. **Better entry timing** - Know when price momentum favors our position
2. **Volatility filter** - Avoid high-risk periods automatically
3. **Bracket-aware decisions** - Factor in distance to bracket boundaries
4. **Reduced correlated losses** - Detect when all cryptos are moving together

---

## Timeline

| Phase | Task | Duration |
|-------|------|----------|
| 1 | Build price collector | 1 hour |
| 2 | Collect baseline data | 1-2 weeks |
| 3 | Build correlation analyzer | 2-3 hours |
| 4 | Analyze correlations | 1-2 hours |
| 5 | Integrate findings | 2-3 hours |

---

## Next Steps

When ready to proceed:
1. Sign up for CoinGecko Demo API key
2. Let me know, and I'll implement `price_collector.py`
3. Start collecting data alongside existing system

---

## Sources
- [CF Benchmarks Documentation](https://docs.cfbenchmarks.com/api/)
- [Kalshi Crypto Markets Help](https://help.kalshi.com/markets/popular-markets/crypto-markets)
- [CoinGecko API Documentation](https://docs.coingecko.com)
- [CME CF Cryptocurrency Benchmarks](https://www.cmegroup.com/markets/cryptocurrencies/cme-cf-cryptocurrency-benchmarks.html)
