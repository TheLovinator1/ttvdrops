# TTVDrops AI Coding Agent Instructions

## Project Overview
TTVDrops is a Django web application that tracks Twitch drop campaigns and sends notifications when new drops become available. It processes Twitch API data to monitor gaming drops, campaigns, and user subscriptions.

## Architecture & Key Components

### Core Django Structure
- **Config Package**: `config/` - Django settings, URLs, WSGI configuration
- **Twitch App**: `twitch/` - Main application handling drop campaigns, games, organizations
- **Accounts App**: `accounts/` - Custom user model extending Django's AbstractUser
- **Templates**: `templates/` - Django templates with base layout and app-specific views
- **Static Files**: `staticfiles/` - Collected static assets including admin and debug toolbar assets

### Data Model Hierarchy
The application follows a specific data flow: Organization → Game → DropCampaign → TimeBasedDrop → DropBenefit

Key models in `twitch/models.py`:
- `Organization`: Twitch organizations that own games
- `Game`: Games on Twitch with drop campaigns 
- `DropCampaign`: Individual drop campaigns with start/end dates
- `TimeBasedDrop`: Time-based drops within campaigns (require watching X minutes)
- `DropBenefit`: Rewards earned from drops (connected via `DropBenefitEdge`)
- `NotificationSubscription`: User subscriptions to games/organizations for notifications

### Key Data Processing Patterns

#### JSON Import System
The project heavily uses the `import_drops.py` management command to process Twitch API responses:
- Handles various JSON response structures from Twitch GraphQL API
- Automatically categorizes and moves processed files to subdirectories
- Uses `json_repair` library for malformed JSON
- Implements transaction-based imports with error handling

```bash
# Import from single file or directory
uv run python manage.py import_drops path/to/json/file_or_directory
```

#### Development Workflow
Always use `uv` for dependency management and Python execution:
```bash
uv run python manage.py runserver          # Start development server
uv run python manage.py migrate            # Apply migrations
uv run python manage.py makemigrations     # Create new migrations
uv run pytest                              # Run tests
uv run python manage.py import_drops responses/  # Import Twitch data
```

## Project-Specific Conventions

### Code Style & Linting
- **Ruff**: Comprehensive linting with extensive rule set (see `pyproject.toml`)
- **Type Annotations**: All functions require type hints with `from __future__ import annotations`
- **Google Docstring Style**: Use Google-style docstrings for functions/classes
- **Django Stubs**: mypy integration for Django type checking

### Database Configuration
- **SQLite with WAL mode**: Optimized SQLite configuration in `settings.py`
- **Custom data directory**: Uses `platformdirs` for OS-appropriate data storage
- **Timezone-aware**: All datetime fields use Django's timezone utilities

### Testing Setup
- **pytest-django**: Test configuration in `conftest.py` and `pyproject.toml`
- **Parallel execution**: Tests run with `pytest-xdist` using `--reuse-db --no-migrations`
- **Fast password hashing**: MD5 hasher for tests performance

## Important Development Notes

### Environment Variables
Required environment variables (use `.env` file):
- `DJANGO_SECRET_KEY`: Django secret key (required)
- `DEBUG`: Boolean for debug mode (default: "True")
- Email settings: `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, etc.

### File Processing Patterns
When working with the import system:
- JSON files are automatically categorized and moved to `processed/` subdirectories
- Failed imports go to specific directories: `broken/`, `actual_error/`, `we_should_double_check/`
- The system handles multiple Twitch API response formats and GraphQL operation types

### Model Relationships
- Games can have multiple drop campaigns
- Drop campaigns contain time-based drops that unlock benefits
- Users can subscribe to games/organizations for notifications
- Organizations own games (nullable relationship)

### RSS Feeds
The application provides RSS feeds for organizations, games, and campaigns via `twitch/feeds.py`

### Development Tools
- **Debug Toolbar**: Enabled in development for database query analysis
- **Browser Reload**: Automatic page refresh during development
- **Django Extensions**: Consider using for shell_plus and other utilities

## Common Pitfalls
- Always use timezone-aware datetime objects with `django.utils.timezone`
- Import command requires specific JSON structures - check existing patterns in `import_drops.py`
- Model field updates should filter out None values to avoid overwriting existing data
- Use `update_or_create()` pattern consistently for data imports to handle duplicates

## Quick Reference Commands
```bash
# Development setup
uv run python manage.py createsuperuser
uv run python manage.py collectstatic

# Data processing
uv run python manage.py import_drops responses/
uv run python manage.py import_drops single_file.json

# Testing
uv run pytest -v                    # Verbose test output
uv run pytest twitch/tests/         # Test specific app
```