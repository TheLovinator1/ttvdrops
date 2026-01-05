# Django Guidelines

## Python Best Practices
- Follow PEP 8 with 120 character line limit
- Use double quotes for Python strings

## Django Best Practices
- Follow Django's "batteries included" philosophy - use built-in features before third-party packages
- Prioritize security and follow Django's security best practices
- Use Django's ORM effectively

## Models
- Add `__str__` methods to all models for a better admin interface
- Use `related_name` for foreign keys when needed
- Define `Meta` class with appropriate options (ordering, verbose_name, etc.)
- Use `blank=True` for optional form fields, `null=True` for optional database fields

## Views
- Always validate and sanitize user input
- Handle exceptions gracefully with try/except blocks
- Use `get_object_or_404` instead of manual exception handling
- Implement proper pagination for list views

## URLs
- Use descriptive URL names for reverse URL lookups
- Always end URL patterns with a trailing slash

## Templates
- Use template inheritance with base templates
- Use template tags and filters for common operations
- Avoid complex logic in templates - move it to views or template tags
- Use static files properly with `{% load static %}`

## Settings
- Use environment variables in a single `settings.py` file
- Never commit secrets to version control

## Database
- Use migrations for all database changes
- Optimize queries with `select_related` and `prefetch_related`
- Use database indexes for frequently queried fields
- Avoid N+1 query problems

## Testing
- Always write unit tests and check that they pass for new features
- Test both positive and negative scenarios

## Pydantic Schemas
- Use `extra="forbid"` in model_config to catch API changes and new fields from external APIs
- Explicitly document all optional fields from different API operation formats
- This ensures validation failures alert you to API changes rather than silently ignoring new data
- When supporting multiple API formats (e.g., ViewerDropsDashboard vs Inventory operations):
  - Make fields optional that differ between formats
  - Use field validators or model validators to normalize data between formats
  - Write tests covering both operation formats to ensure backward compatibility

## Architectural Patterns
- Follow Django's MTV (Model-Template-View) paradigm; keep business logic in models/services and presentation in templates
- Favor fat models and thin views; extract reusable business logic into services/helpers when complexity grows
- Keep forms and serializers (if added) responsible for validation; avoid duplicating validation in views
- Avoid circular dependencies; keep modules cohesive and decoupled
- Use settings modules and environment variables to configure behavior, not hardcoded constants

## Technology Stack
- Python 3, Django, SQLite
- HTML templates with Django templating; static assets served from `static/` and collected to `staticfiles/`
- Management commands in `twitch/management/commands/` for data import and maintenance tasks
- Use `pyproject.toml` + uv for dependency and environment management
- Use `uv run python manage.py <command>` to run Django management commands
