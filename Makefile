VENV=/Users/jungjunkim/Projects/CardTraders/.venv
PY=$(VENV)/bin/python
PIP=$(VENV)/bin/pip

dev:
	cd backend && $(PY) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

install:
	cd backend && $(PIP) install -r requirements.txt
