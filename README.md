# ttvdrops

Get notified when a new drop is available on Twitch

## Features

- Import and track Twitch drop campaigns
- View campaign details including start/end dates and rewards
- Filter campaigns by game and status
- Dashboard with active campaigns and quick stats
- Admin interface for managing campaigns and drops

## Installation

1. Clone the repository
```bash
git clone https://github.com/TheLovinator1/ttvdrops.git
cd ttvdrops
```

2. Install dependencies
```bash
uv sync 
```

3. Set up environment variables by modifying the `.env` file

4. Apply migrations
```bash
uv run python manage.py migrate
```

5. Create a superuser
```bash
uv run python manage.py createsuperuser
```

## Usage

### Running the server
```bash
uv run python manage.py runserver
```

Access the application at http://127.0.0.1:8000/

### Importing drop campaigns
```bash
uv run python manage.py import_drop_campaign path/to/your/json/file.json
```

## Development

```bash
uv run python manage.py createsuperuser
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py collectstatic
uv run python manage.py runserver
uv run pytest
```

## Project Structure

- `twitch/` - Main app for Twitch drop campaigns
  - `models.py` - Database models for campaigns, drops, and benefits
  - `views.py` - Views for displaying campaign data
  - `admin.py` - Admin interface configuration
  - `management/commands/` - Custom management commands
    - `import_drop_campaign.py` - Command for importing JSON data

- `templates/` - HTML templates
  - `twitch/` - App-specific templates
    - `dashboard.html` - Dashboard view
    - `campaign_list.html` - List of all campaigns
    - `campaign_detail.html` - Detailed view of a campaign

## License

See the [LICENSE](LICENSE) file for details.
