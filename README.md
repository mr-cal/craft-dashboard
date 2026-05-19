# craft-dashboard

Dashboard, insights, and issue triage for the \*craft applications and libraries.

## Overview

craft-dashboard provides:
- **Issue & PR triage dashboard** with LLM-powered scoring and action suggestions
- **Statistics & trends** for open issues, PRs, releases, and dependencies
- **Multi-source data** from GitHub and Launchpad

## Development

### Prerequisites

- Python 3.12+
- PostgreSQL 16+
- uv

### Setup

\`\`\`bash
# Install dependencies
uv sync --group dev

# Set up environment variables
cp .env.example .env
# Edit .env with your database URL and API tokens

# Run database migrations
uv run alembic upgrade head

# Start development server
uv run uvicorn craft_dashboard.app:create_app --factory --reload
\`\`\`

### Testing

\`\`\`bash
make test
\`\`\`

### Linting

\`\`\`bash
make lint
\`\`\`
