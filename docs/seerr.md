# Seerr Integration

Seerr integration allows you to request movies and TV shows directly from Yamtrack to your [Overseerr](https://overseerr.dev) or [Jellyseerr](https://github.com/Fallenbagel/jellyseerr) instance.

When you hover over a media card on the home page or media list, the overlay shows a **Request on Seerr** button next to the existing tracking buttons.

## Prerequisites

- A running [Seerr](https://seerr.dev)
- An API key from your Seerr instance

### Getting an API Key

1. Open your Seerr instance in a browser
2. Go to **Settings → General**
3. Scroll to the **API Key** section
4. Copy the API key shown there

## Configuration

The Seerr integration is configured per user in the Yamtrack settings:

1. Click the **Settings** icon in the sidebar, then select **Integrations**
2. Click the **Seerr** tab
3. Enter your Seerr instance URL (e.g., `https://requests.example.com`)
4. Enter the API key you copied from your Seerr instance
5. Click **Save Changes**

Yamtrack will verify the connection when saving. If the connection fails, check that the URL and API key are correct and that your Seerr instance is accessible.

## Usage

Once configured, hover over any movie or TV show card in Yamtrack to reveal the action buttons overlay. The **Request on Seerr** button appears alongside the existing Track, Lists, and History buttons.

Clicking the button sends a request to your Seerr instance for that media. A success or error message is shown at the top of the page.

## Supported Media Types

Only **movies** and **TV shows** can be requested through Seerr. Other media types (anime, manga, games, books, comics, board games) are not supported by the Seerr API.

Media items must be sourced from **TMDB**, the default source for movies and TV shows in Yamtrack  for the request to work. Items from other sources cannot be requested.
