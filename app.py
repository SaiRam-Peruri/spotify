import os
import time
import sqlite3
from typing import Optional
from flask import Flask, jsonify, redirect, request, session, url_for, render_template, make_response
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

app = Flask(__name__, static_folder='static', template_folder='templates')

# Use environment variables for configurable values
DB_PATH = os.getenv("DB_PATH", "settings.db")
SPOTIPY_REDIRECT_URI = os.getenv("REDIRECT_URI")
app.secret_key = os.getenv("SECRET_KEY", "change_me")
app.config["SESSION_COOKIE_NAME"] = "spotify-login"

# Updated SCOPE to include all necessary permissions for Spotify API endpoints
SCOPE = "user-library-read playlist-read-private playlist-read-collaborative playlist-modify-private user-read-playback-state user-read-currently-playing"

def init_db():
    if not os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS song_settings (
                track_id TEXT PRIMARY KEY,
                tempo REAL,
                speed REAL
            )
        """)
        conn.commit()
        conn.close()

init_db()

SPOTIPY_CLIENT_ID = os.getenv("CLIENT_ID")
SPOTIPY_CLIENT_SECRET = os.getenv("CLIENT_SECRET")
SPOTIPY_REDIRECT_URI = os.getenv("REDIRECT_URI")

def _sp_oauth() -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET,
        redirect_uri=SPOTIPY_REDIRECT_URI,
        scope=SCOPE,
        cache_path=None,
        show_dialog=False
    )

def _get_token_info() -> Optional[dict]:
    return session.get("token_info")

def _set_token_info(token_info: dict) -> None:
    session["token_info"] = token_info

def _ensure_token() -> Optional[str]:
    token_info = _get_token_info()
    if not token_info:
        return None
    is_expired = token_info.get("expires_at", 0) - int(time.time()) < 60
    if is_expired:
        oauth = _sp_oauth()
        try:
            token_info = oauth.refresh_access_token(token_info["refresh_token"])
            _set_token_info(token_info)
        except Exception as e:
            app.logger.error(f"Token refresh error: {str(e)}")
            session.pop("token_info", None)
            return None
    return token_info.get("access_token")

def _verify_spotify_connection():
    """Verify that the Spotify connection is working properly."""
    try:
        sp = _spotify_client()
        if not sp:
            return False, "Not authenticated"
        
        # Try a simple API call to verify the connection
        user = sp.current_user()
        return True, f"Connected as {user['display_name']}"
    except spotipy.SpotifyException as e:
        app.logger.error(f"Spotify connection error: {str(e)}")
        return False, f"Spotify API error: {str(e)}"
    except Exception as e:
        app.logger.error(f"Unexpected error verifying Spotify connection: {str(e)}")
        return False, f"Unexpected error: {str(e)}"

def _spotify_client() -> Optional[spotipy.Spotify]:
    access_token = _ensure_token()
    if not access_token:
        return None
    return spotipy.Spotify(auth=access_token)

@app.after_request
def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/")
def root():
    if _ensure_token():
        return redirect(url_for("playlists"))
    return render_template('home.html', login_url=_sp_oauth().get_authorize_url())

# Force reauthorization if .cache is deleted
@app.route("/login")
def login():
    cache_path = _sp_oauth().cache_path
    if cache_path and os.path.exists(cache_path):
        os.remove(cache_path)
    return redirect(_sp_oauth().get_authorize_url())

@app.route("/callback")
def callback():
    oauth = _sp_oauth()
    code = request.args.get("code")
    if not code:
        return "Missing authorization code.", 400
    token_info = oauth.get_access_token(code)
    _set_token_info(token_info)
    return redirect(url_for("playlists"))

# Add caching for playlists
@app.route("/playlists")
def playlists():
    sp = _spotify_client()
    if not sp:
        return redirect(url_for("login"))

    try:
        items = []
        results = sp.current_user_playlists(limit=50, offset=0)
        items.extend(results.get("items", []))
        while results.get("next"):
            results = sp.next(results)
            items.extend(results.get("items", []))

        return render_template('playlists.html', playlists=items, ts=int(time.time()))
    except spotipy.SpotifyException as e:
        return jsonify({"error": "Spotify API error", "detail": str(e)}), 500
    except Exception as e:
        return jsonify({"error": "server_error", "detail": str(e)}), 500

@app.route("/playlist/<playlist_id>")
def view_playlist(playlist_id):
    sp = _spotify_client()
    if not sp:
        return redirect(url_for("login"))

    tracks = []
    results = sp.playlist_items(playlist_id, additional_types=("track",), limit=100)
    while True:
        for it in results.get("items", []):
            t = it.get("track")
            if t:
                tracks.append(t)
        if results.get("next"):
            results = sp.next(results)
        else:
            break

    return render_template('playlist_tracks.html', tracks=tracks)

@app.route("/transition_between", methods=["POST"])
def transition_between():
    sp = _spotify_client()
    if not sp:
        return jsonify({"error": "not_authenticated", "message": "Please log in again"}), 401
    try:
        data = request.get_json(force=True, silent=True) or {}
        
        # New mode: Track-based with tempo/energy if two track_ids provided
        track_ids = data.get("track_ids", [])
        if isinstance(track_ids, list) and len(track_ids) == 2:
            app.logger.info(f"Using track-based transition for tracks: {track_ids}")
            
            # Fetch or use custom audio features (tempo, energy)
            features = []
            for track_id in track_ids:
                # Check DB for custom tempo/speed
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("SELECT tempo, speed FROM song_settings WHERE track_id=?", (track_id,))
                row = c.fetchone()
                conn.close()
                
                if row and row[0] is not None:  # Use custom if available
                    custom_tempo = row[0]
                    # Speed might affect effective tempo, but for simplicity, use tempo directly
                    features.append({"tempo": custom_tempo, "energy": row[1] if row[1] else 0.5})  # Default energy if not set
                    app.logger.info(f"Using custom features for {track_id}: tempo={custom_tempo}")
                else:  # Fetch from Spotify
                    try:
                        feat = sp.audio_features(track_id)[0]
                        if feat:
                            features.append({"tempo": feat["tempo"], "energy": feat["energy"]})
                        else:
                            raise ValueError("No features available")
                    except Exception as e:
                        app.logger.warning(f"Failed to fetch features for {track_id}: {str(e)}")
                        return jsonify({"error": "features_error", "message": "Could not fetch audio features for one or both tracks."}), 400
            
            if len(features) != 2:
                return jsonify({"error": "features_error", "message": "Could not retrieve features for both tracks."}), 400
            
            # Calculate targets
            target_tempo = (features[0]["tempo"] + features[1]["tempo"]) / 2
            target_energy = (features[0]["energy"] + features[1]["energy"]) / 2
            app.logger.info(f"Target tempo: {target_tempo}, Target energy: {target_energy}")
            
            # Check if tracks work with recommendations
            if all(_check_track_for_recommendations(sp, tid) for tid in track_ids):
                try:
                    recs = sp.recommendations(
                        seed_tracks=track_ids,
                        limit=10,
                        target_tempo=target_tempo,
                        target_energy=target_energy
                    )
                    suggestions = []
                    for track in recs.get("tracks", []):
                        suggestions.append({
                            "name": track["name"],
                            "artist": track["artists"][0]["name"] if track.get("artists") else "",
                            "id": track["id"],
                            "preview_url": track.get("preview_url")
                        })
                    
                    if suggestions:
                        return jsonify({"suggestions": suggestions})
                    else:
                        app.logger.warning("No recommendations returned; falling back.")
                except Exception as e:
                    app.logger.error(f"Recommendations error: {str(e)}")
            
            # Fallback if recommendations fail
            app.logger.info("Falling back to popular tracks due to recommendations failure.")

        # Existing logic for genres
        if data.get("use_genres"):
            genres = data.get("genres", ["pop", "rock"])
            app.logger.info(f"Using genre search: {genres}")
            try:
                suggestions = []
                for genre in genres[:3]:
                    search_results = sp.search(q=f"genre:{genre}", type="track", limit=4)
                    for item in search_results.get("tracks", {}).get("items", []):
                        if len(suggestions) >= 10:
                            break
                        suggestions.append({
                            "name": item["name"],
                            "artist": item["artists"][0]["name"] if item.get("artists") else "",
                            "id": item["id"],
                            "preview_url": item.get("preview_url"),
                            "genre": genre
                        })
                if len(suggestions) < 5:
                    playlist_id = "37i9dQZEVXbMDoHDwVN2tF"
                    playlist_tracks = sp.playlist_items(playlist_id, limit=5)
                    for item in playlist_tracks.get("items", []):
                        if item.get("track") and len(suggestions) < 10:
                            track = item["track"]
                            suggestions.append({
                                "name": track["name"],
                                "artist": track["artists"][0]["name"] if track.get("artists") else "",
                                "id": track["id"],
                                "preview_url": track.get("preview_url"),
                                "genre": "popular"
                            })
                if not suggestions:
                    return jsonify({"error": "No tracks found", "message": "Could not find any tracks for the selected genres. Please try different genres."}), 404
                return jsonify({"suggestions": suggestions})
            except Exception as e:
                app.logger.error(f"Genre search error: {str(e)}")
                return jsonify({"error": "Search error", "message": f"Error searching for genres: {str(e)}. Please try different genres."}), 500
        
        # Existing fallback for generic track-based
        try:
            playlist_ids = [
                "37i9dQZEVXbMDoHDwVN2tF",  # Global Top 50
                "37i9dQZF1DXcBWIGoYBM5M",  # Today's Top Hits
                "37i9dQZF1DX0XUsuxWHRQd"   # RapCaviar
            ]
            suggestions = []
            for playlist_id in playlist_ids:
                if len(suggestions) >= 10:
                    break
                playlist_tracks = sp.playlist_items(playlist_id, limit=10)
                for item in playlist_tracks.get("items", []):
                    if item.get("track") and len(suggestions) < 10:
                        track = item["track"]
                        suggestions.append({
                            "name": track["name"],
                            "artist": track["artists"][0]["name"] if track.get("artists") else "",
                            "id": track["id"],
                            "preview_url": track.get("preview_url")
                        })
            if not suggestions:
                return jsonify({"error": "No tracks found", "message": "Could not find any tracks. Please try using genres instead."}), 404
            return jsonify({"suggestions": suggestions})
        except Exception as e:
            app.logger.error(f"Playlist tracks error: {str(e)}")
            return jsonify({"error": "Tracks error", "message": f"Error: {str(e)}. Please try using genres instead."}), 500
    except Exception as e:
        app.logger.error(f"Server error: {str(e)}")
        return jsonify({"error": "server_error", "message": f"Unexpected error: {str(e)}. Please try again."}), 500

@app.route("/settings/<track_id>", methods=["POST"])
def save_song_settings(track_id):
    data = request.get_json(force=True, silent=True) or {}
    tempo = data.get("tempo")
    speed = data.get("speed")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("REPLACE INTO song_settings (track_id, tempo, speed) VALUES (?, ?, ?)", (track_id, tempo, speed))
    conn.commit()
    conn.close()
    return jsonify({"status": "saved", "track_id": track_id, "tempo": tempo, "speed": speed})

@app.route("/settings/<track_id>", methods=["GET"])
def get_song_settings(track_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT tempo, speed FROM song_settings WHERE track_id=?", (track_id,))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({"tempo": row[0], "speed": row[1]})
    else:
        return jsonify({"tempo": None, "speed": None})

@app.route("/create_transition_playlist", methods=["POST"])
def create_transition_playlist():
    sp = _spotify_client()
    if not sp:
        return jsonify({"error": "not_authenticated"}), 401
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "Smooth Transition")
    track_ids = data.get("track_ids", [])
    if not track_ids:
        return jsonify({"error": "track_ids required"}), 400
    user_id = sp.current_user()["id"]
    newp = sp.user_playlist_create(user_id, name, public=False, description="Auto-generated transitions")
    sp.playlist_replace_items(newp["id"], track_ids)
    return jsonify({"playlist_id": newp["id"]})

@app.route("/preview/<track_id>")
def get_preview(track_id):
    sp = _spotify_client()
    if not sp:
        return jsonify({"error": "not_authenticated"}), 401
    track = sp.track(track_id)
    return jsonify({"preview_url": track.get("preview_url")})

@app.route("/api/status")
def api_status():
    is_connected, message = _verify_spotify_connection()
    if is_connected:
        return jsonify({"status": "connected", "message": message})
    else:
        return jsonify({"status": "error", "message": message}), 400

@app.route("/api/search")
def search_tracks():
    sp = _spotify_client()
    if not sp:
        return jsonify({"error": "not_authenticated"}), 401
    
    query = request.args.get("q", "")
    if not query or len(query) < 2:
        return jsonify({"error": "Query must be at least 2 characters"}), 400
    
    try:
        results = sp.search(q=query, type="track", limit=10)
        tracks = []
        
        for item in results["tracks"]["items"]:
            try:
                features = sp.audio_features(item["id"])
                has_features = features and features[0] is not None
            except:
                has_features = False
            
            works_with_recommendations = _check_track_for_recommendations(sp, item["id"])
                
            tracks.append({
                "id": item["id"],
                "name": item["name"],
                "artist": item["artists"][0]["name"] if item["artists"] else "",
                "album": item["album"]["name"] if item["album"] else "",
                "preview_url": item["preview_url"],
                "has_features": has_features,
                "works_with_recommendations": works_with_recommendations
            })
        
        return jsonify({"tracks": tracks})
    except Exception as e:
        app.logger.error(f"Search error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/popular_tracks")
def get_popular_tracks():
    sp = _spotify_client()
    if not sp:
        return jsonify({"error": "not_authenticated"}), 401
    
    try:
        known_working_track_ids = [
            "6DCZcSspjsKoFjzjrWoCdn",  # God's Plan - Drake
            "0e7ipj03S05BNilyu5bRzt",  # rockstar - Post Malone
            "3ee8Jmje8o58CHK66QrVC2",  # Bad Guy - Billie Eilish
            "0VjIjW4GlUZAMYd2vXMi3b",  # Blinding Lights - The Weeknd
            "7qiZfU4dY1lWllzX7mPBI3",  # Shape of You - Ed Sheeran
            "5CtI0qwDJkDQGwXD1H1cLb",  # Despacito - Luis Fonsi
            "1zi7xx7UVEFkmKfv06H8x0",  # One Dance - Drake
            "7KXjTSCq5nL1LoYtL7XAwS"   # HUMBLE. - Kendrick Lamar
        ]
        
        tracks = []
        for track_id in known_working_track_ids:
            try:
                track = sp.track(track_id)
                tracks.append({
                    "id": track["id"],
                    "name": track["name"],
                    "artist": track["artists"][0]["name"] if track.get("artists") else "",
                    "album": track["album"]["name"] if track.get("album") else "",
                    "preview_url": track.get("preview_url")
                })
            except Exception as e:
                app.logger.warning(f"Could not fetch track {track_id}: {str(e)}")
        
        if not tracks:
            return jsonify({"error": "Could not fetch any tracks"}), 500
            
        return jsonify({"tracks": tracks})
    except Exception as e:
        app.logger.error(f"Popular tracks error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/genres")
def get_available_genres():
    sp = _spotify_client()
    if not sp:
        return jsonify({"error": "not_authenticated"}), 401
    
    try:
        genres = sp.recommendation_genre_seeds()
        return jsonify({"genres": genres.get("genres", [])})
    except Exception as e:
        app.logger.error(f"Genre seeds error: {str(e)}")
        fallback_genres = [
            "pop", "rock", "hip-hop", "electronic", "r-n-b", "indie", 
            "jazz", "classical", "country", "reggae", "blues", "metal"
        ]
        return jsonify({"genres": fallback_genres})

# List of track IDs that are known to work with the recommendations API
KNOWN_WORKING_TRACKS = [
    "6DCZcSspjsKoFjzjrWoCdn",  # God's Plan - Drake
    "0e7ipj03S05BNilyu5bRzt",  # rockstar - Post Malone
    "3ee8Jmje8o58CHK66QrVC2",  # Bad Guy - Billie Eilish
    "0VjIjW4GlUZAMYd2vXMi3b",  # Blinding Lights - The Weeknd
    "7qiZfU4dY1lWllzX7mPBI3",  # Shape of You - Ed Sheeran
    "5CtI0qwDJkDQGwXD1H1cLb",  # Despacito - Luis Fonsi
    "1zi7xx7UVEFkmKfv06H8x0",  # One Dance - Drake
    "7KXjTSCq5nL1LoYtL7XAwS"   # HUMBLE. - Kendrick Lamar
]

def _check_track_for_recommendations(sp, track_id):
    """Check if a track works with the recommendations API."""
    if track_id in KNOWN_WORKING_TRACKS:
        return True
    # Perform a minimal test recommendation call
    try:
        sp.recommendations(seed_tracks=[track_id], limit=1)
        return True
    except Exception:
        return False

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)