# Infinity Cloud Systems Inventory

A Streamlit inventory management application for:

- HQ Steppes Road
- The Hide Safaris
- Changa Safari Camp

## Technology

- Python and Streamlit
- SQLite persistent database
- Pandas data processing
- Streamlit native reporting charts
- OpenPyXL Excel exports

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

The database is created automatically in `data/camp_inventory.db` and seeded with demonstration stock.
