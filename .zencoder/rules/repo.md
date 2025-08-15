---
description: Repository Information Overview
alwaysApply: true
---

# Spotify Transition App Information

## Summary
A Flask web application that interacts with the Spotify API to create smooth transitions between songs. The app allows users to select tracks from their playlists and generates bridge songs that create a smooth transition between them based on audio features like tempo and energy.

## Structure
- `.env`: Contains Spotify API credentials and configuration
- `app.py`: Main Flask application with all routes and business logic
- `settings.db`: SQLite database (auto-generated) for storing song settings

## Language & Runtime
**Language**: Python
**Version**: Python 3.x
**Framework**: Flask
**Database**: SQLite

## Dependencies
**Main Dependencies**:
- Flask: Web framework for the application
- Spotipy: Python client for the Spotify Web API
- python-dotenv: For loading environment variables
- SQLite3: For local database storage

## Build & Installation
```bash
pip install flask spotipy python-dotenv
```

## Usage & Operations
```bash
# Set up environment variables in .env file
# CLIENT_ID=your_spotify_client_id
# CLIENT_SECRET=your_spotify_client_secret
# REDIRECT_URI=http://127.0.0.1:5001/callback

# Run the application
python app.py
```

## API Integration
**Spotify API**: The application uses the Spotify Web API through the Spotipy client to:
- Authenticate users via OAuth
- Fetch user playlists and tracks
- Get audio features for tracks
- Generate song recommendations based on audio features
- Create new playlists with transition songs

## Data Storage
**SQLite Database**: Stores song settings including:
- Track ID (primary key)
- Tempo adjustments
- Speed adjustments

## Features
- OAuth authentication with Spotify
- Browse and view user playlists
- Select start and end tracks for transition
- Generate bridge songs based on audio features
- Adjust tempo and speed settings for tracks
- Create new playlists with smooth transitions
- Preview songs with 30-second clips when available