#!/bin/bash

echo "🎨 Starting Tailwind CSS watcher..."
npm run build-css &
CSS_PID=$!

echo "🚀 Starting Django development server..."
source venv/bin/activate
cd src
python manage.py runserver &
DJANGO_PID=$!

echo "✅ Development servers started!"
echo "   - CSS watcher: PID $CSS_PID"
echo "   - Django server: PID $DJANGO_PID"
echo "   - Visit: http://127.0.0.1:8000"
echo ""
echo "Press Ctrl+C to stop both servers"

# Function to kill both processes when script is interrupted
cleanup() {
    echo ""
    echo "🛑 Stopping servers..."
    kill $CSS_PID 2>/dev/null
    kill $DJANGO_PID 2>/dev/null
    echo "✅ Servers stopped"
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

# Wait for both processes
wait
