# Portfolio Dashboard

Streamlit dashboard for tracking IBKR portfolio with automated CSV ingestion.

## Features
- Auto-process IBKR Flex Query CSVs
- Multi-platform support (IBKR, extensible to Tiger/Futu)
- Options expiry alerts (7d / 14d / 30d)
- Trading performance: Win rate, Profit Factor, Avg Win/Loss
- Cumulative dividends, deposits, realized P&L tracking
- Responsive design (mobile-friendly)

## Setup

```bash
pip install -r requirements.txt
export DASHBOARD_PASSWORD="your_password_here"
streamlit run app.py