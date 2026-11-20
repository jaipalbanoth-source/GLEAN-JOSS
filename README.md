# Glean Agent Client

Python client + FastAPI backend + React frontend for running agents against a Glean instance.

## Layout

```
.
├── .env                        # secrets, never commit
├── .env.example                # template, commit this
├── .gitignore
├── requirements.txt
├── README.md
├── main.py                     # CLI: one-shot agent prompt
├── backend/
│   ├── __main__.py             # python -m backend
│   ├── main.py                 # FastAPI app factory
│   ├── config.py               # env loading
│   ├── schemas.py              # Pydantic models
│   ├── routers/
│   │   └── agent.py            # /api/v1/health, /api/v1/agents/run
│   ├── services/
│   │   └── agent.py            # request → glean_client glue
│   └── glean_client/
│       ├── client.py           # Glean REST wrapper (runs/wait)
│       └── agents.py           # call / call_with_history / call_json
├── frontend/                   # Vite + React chat UI
└── tests/
    ├── test_client.py          # HTTP client tests (mocked)
    └── test_api.py             # API tests (TestClient)
```

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env         # fill in GLEAN_INSTANCE, GLEAN_API_TOKEN, MY_AGENT_ID
cd frontend && npm install     # once
```

## Usage

Backend (port 8000, docs at http://127.0.0.1:8000/docs):

```bash
python -m backend
```

Frontend (port 5173, proxies /api to the backend):

```bash
cd frontend
npm run dev
```

CLI one-shot:

```bash
python main.py "Summarize the latest support tickets"
```

## Tests

```bash
pytest
```

## API

| Method | Path                  | Body                                      |
| ------ | --------------------- | ----------------------------------------- |
| GET    | `/api/v1/health`      | –                                         |
| POST   | `/api/v1/agents/run`  | `{"prompt": "...", "history": [{"role": "user"\|"assistant", "content": "..."}]}` |
