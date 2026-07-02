.PHONY: install dev backend frontend stop db-init test lint clean

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

explore:
	env PYTHONPATH=backend $(VENV)/bin/python backend/app/explore.py $(SQL)

lint:
	cd backend && $(VENV)/bin/ruff check .
	cd frontend && npm run lint

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf frontend/dist frontend/node_modules/.vite
