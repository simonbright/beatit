# Contributing to BeatIt

Thank you for your interest in improving BeatIt.

## Getting started

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open http://localhost:8080. Leave `AUTH_USERNAME` and `AUTH_PASSWORD` empty for local development without a login prompt.

## Pull requests

- Keep changes focused and explain the motivation in the PR description.
- Match existing code style and naming in the area you edit.
- Do not commit secrets, `.env`, or patient data.

## PHI and safety

BeatIt is designed for oncology case **research organization**, not clinical record systems.

- Do not open issues or PRs that include real patient names, identifiers, or clinical documents.
- Use synthetic or clearly fictional examples in bug reports and screenshots.

## Medical disclaimer

Contributions that change clinical prompting or output should preserve clear sourcing and the existing medical disclaimer posture. BeatIt does not provide medical advice.
