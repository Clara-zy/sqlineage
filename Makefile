.PHONY: install dev backend frontend stop db-init import-tests bird-import platform-import preprocess-sql test lint clean

PYTHON = python3.13
VENV = backend/.venv

install:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

dev:
	@echo "Starting backend (port 8000)..."
	env PYTHONPATH=backend $(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
	@echo "Starting frontend (port 5173)..."
	cd frontend && npm run dev &
	@echo "Backend:  http://localhost:8000"
	@echo "Frontend: http://localhost:5173"
	@echo "API Docs: http://localhost:8000/docs"

backend:
	env PYTHONPATH=backend $(VENV)/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

frontend:
	cd frontend && npm run dev

stop:
	@echo "Stopping services..."
	-pkill -f "uvicorn app.main"
	-pkill -f "vite"

db-init:
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/db_init.py

test:
	env PYTHONPATH=backend $(VENV)/bin/pytest backend/tests/$(ARGS) -v

import-tests:
	@echo "Importing test cases from resource/test_sqls.json..."
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/import_tests.py

bird-import:
	@echo "Importing BIRD test cases from resource/BIRD_SUBSET/dev.json..."
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/bird_import.py

platform-import:
	@echo "Importing platform schema from resource/UP_SUBSET/platform_schema..."
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/platform_import.py

preprocess-sql:
	@echo "Cleaning production Hive SQL → *.clean.sql..."
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/sql_preprocess.py --check

import-all: import-tests
	@echo "Importing BIRD test cases on top..."
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/bird_import.py

reset:
	@echo "Clearing lineage data..."
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/clear_lineage.py
	@echo "Re-importing test cases..."
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/import_tests.py
	@echo "Importing BIRD test cases..."
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/bird_import.py
	@echo "Reset complete."

explore:
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/explore.py $(SQL)

lint:
	cd backend && $(VENV)/bin/ruff check .
	cd frontend && npm run lint

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/dist frontend/node_modules/.vite
