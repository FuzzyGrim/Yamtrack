# Local Development Setup Guide

## Prerequisites

Before starting, ensure you have:
- **Python 3.x** (check with `python --version` or `python3 --version`)
- **Node.js and npm** (check with `node --version` and `npm --version`)
- **Redis** (optional for development, but recommended for full functionality)

## Quick Start (Recommended)

The easiest way to start the app is using the provided script:

```bash
./start-dev.sh
```

This will:
1. Start the Tailwind CSS watcher (for automatic CSS compilation)
2. Start the Django development server
3. Both run in the background and can be stopped with Ctrl+C

The app will be available at: **http://127.0.0.1:8000**

## Manual Setup (Step by Step)

If you prefer to set things up manually or the script doesn't work:

### 1. Set Up Python Virtual Environment

```bash
# Create virtual environment (if not already created)
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # On macOS/Linux
# OR
venv\Scripts\activate  # On Windows
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Node Dependencies

```bash
npm install
```

### 4. Set Up Database

```bash
cd src
python manage.py migrate
```

This will create the SQLite database and run all migrations.

### 5. Create a Superuser (Optional, for admin access)

```bash
python manage.py createsuperuser
```

Follow the prompts to create an admin user.

### 6. Start Development Servers

You'll need **two terminal windows**:

**Terminal 1 - CSS Watcher:**
```bash
npm run build-css
```

**Terminal 2 - Django Server:**
```bash
source venv/bin/activate  # If not already activated
cd src
python manage.py runserver
```

## Environment Variables (Optional)

The app uses `python-decouple` for configuration. You can set environment variables or create a `.env` file in the project root. Common variables:

- `DEBUG=True` - Enable debug mode (default: False)
- `SECRET_KEY=your-secret-key` - Django secret key
- `REDIS_URL=redis://localhost:6379` - Redis connection URL (optional for dev)
- `TMDB_API=your-api-key` - TMDB API key (optional)
- `MAL_API=your-api-key` - MyAnimeList API key (optional)
- Other API keys as needed

For development, most of these are optional - the app will use defaults or work without external APIs.

## Redis Setup (Optional but Recommended)

Redis is used for:
- Caching (improves performance)
- Celery task queue (for background tasks)

### Install Redis

**macOS (using Homebrew):**
```bash
brew install redis
brew services start redis
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install redis-server
sudo systemctl start redis
```

**Docker:**
```bash
docker run -d -p 6379:6379 redis:7-alpine
```

Redis will run on `localhost:6379` by default.

## Celery Worker (Optional)

If you want background tasks (notifications, calendar updates) to work:

**Terminal 3 - Celery Worker:**
```bash
source venv/bin/activate
cd src
celery -A config worker -l info
```

**Terminal 4 - Celery Beat (for scheduled tasks):**
```bash
source venv/bin/activate
cd src
celery -A config beat -l info
```

## Troubleshooting

### Port Already in Use

If port 8000 is already in use:
```bash
python manage.py runserver 8001  # Use different port
```

### CSS Not Updating

- Make sure the CSS watcher is running (`npm run build-css`)
- Check that `src/static/css/main.css` exists
- Try running `npm run build-css-prod` to rebuild CSS manually

### Database Errors

If you get database errors:
```bash
cd src
python manage.py migrate  # Run migrations
python manage.py makemigrations  # Create new migrations if needed
```

### Virtual Environment Issues

If Python packages aren't found:
- Make sure virtual environment is activated (`source venv/bin/activate`)
- Reinstall dependencies: `pip install -r requirements.txt`

### Redis Connection Errors

If Redis isn't running:
- The app will still work, but caching won't function
- Background tasks won't run without Celery
- You can ignore Redis errors for basic development

## Development Workflow

1. **Make changes** to Python files or templates
2. **Django auto-reloads** - no restart needed
3. **Make CSS changes** to `src/static/css/input.css`
4. **CSS auto-compiles** - refresh browser to see changes
5. **No `collectstatic` needed** - development server serves files directly

## Useful Commands

```bash
# Run tests
cd src
python manage.py test

# Create migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser

# Django shell
python manage.py shell

# Collect static files (production only)
python manage.py collectstatic

# Build CSS for production
npm run build-css-prod
```

## Accessing the App

Once running:
- **Web Interface**: http://127.0.0.1:8000
- **Admin Panel**: http://127.0.0.1:8000/admin (if superuser created)
- **API**: Various endpoints available (see `src/app/urls.py`)

## Stopping the Servers

If using `start-dev.sh`:
- Press `Ctrl+C` to stop both servers

If running manually:
- Press `Ctrl+C` in each terminal window
- Or find and kill processes:
  ```bash
  # Find Django process
  ps aux | grep runserver
  kill <PID>
  
  # Find CSS watcher
  ps aux | grep tailwindcss
  kill <PID>
  ```

## Next Steps

1. Create an account or login
2. Start tracking media!
3. Check out the documentation in `APP_CORE_LOGIC_DOCUMENTATION.md` for understanding the app structure
