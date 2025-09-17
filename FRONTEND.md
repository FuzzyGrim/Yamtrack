# Frontend Development Setup

## 🎯 No More Static File Bullshit!

This setup eliminates the need to run `collectstatic` every time you make CSS changes.

## 🚀 Quick Start

### Option 1: One Command (Recommended)
```bash
./start-dev.sh
```
This starts both the CSS watcher and Django server. CSS changes will rebuild automatically.

### Option 2: Manual
```bash
# Terminal 1: Start CSS watcher
npm run build-css

# Terminal 2: Start Django server
source venv/bin/activate
cd src
python manage.py runserver
```

## 📝 Making CSS Changes

1. Edit `src/static/css/input.css`
2. Changes are automatically compiled to `src/static/css/main.css`
3. Refresh your browser - no `collectstatic` needed!

## 🎨 Available CSS Classes

### Custom Classes
- `.glass-card` - Glassmorphism effect
- `.glass-card-strong` - Stronger glass effect
- `.w-poster` - Fixed poster width (320px desktop, 360px mobile)
- `.text-gradient` - Teal to purple gradient text
- `.shadow-glow` - Glowing shadow effect
- `.shadow-elev` - Elevated shadow
- `.rating-bar` - Animated rating bars

### Tailwind Classes
All standard Tailwind classes work, plus custom colors:
- `text-text-primary` - Primary text color (#E8EAED)
- `text-text-muted` - Muted text color (#9AA0A6)
- `text-brand-teal` - Brand teal (#36E0D0)
- `text-brand-purple` - Brand purple (#7B61FF)
- `bg-base` - Base background (#0D0D0F)

## 🔧 Files You Care About

- `src/static/css/input.css` - Edit this for CSS changes
- `src/static/css/main.css` - Auto-generated, don't edit
- `tailwind.config.js` - Tailwind configuration
- `start-dev.sh` - Development startup script

## ✅ What's Fixed

- ❌ No more `collectstatic` bullshit
- ✅ CSS changes rebuild automatically
- ✅ All Tailwind classes work properly
- ✅ Custom components included
- ✅ Development server serves static files directly
- ✅ One command to start everything

## 🚨 Production

For production builds:
```bash
npm run build-css-prod
```
This creates a minified CSS file for production.
