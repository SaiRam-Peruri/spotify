import os
import time
import sqlite3
from typing import Optional
from flask import Flask, jsonify, redirect, request, session, url_for, render_template_string, make_response
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth

load_dotenv()

app = Flask(__name__)

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
        except Exception:
            session.pop("token_info", None)
            return None
    return token_info.get("access_token")

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
    return redirect(_sp_oauth().get_authorize_url())

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

        html = """
        <h1>Your Playlists</h1>
        <p><a href="{{ url_for('playlists') }}?t={{ ts }}">Refresh</a></p>
        <ul>
        {% for p in playlists %}
          <li style="margin-block-end:1rem;">
            {% if p['images'] %}
              <img src="{{ p['images'][0]['url'] }}" style="inline-size:100px;"><br>
            {% endif %}
            <strong>{{ p['name'] }}</strong><br>
            <a href="{{ url_for('view_playlist', playlist_id=p['id']) }}">View Songs</a>
          </li>
        {% endfor %}
        </ul>
        """
        resp = make_response(render_template_string(html, playlists=items, ts=int(time.time())))
        return resp
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

    # Ensure consistent use of logical properties in CSS styles
    html_tracks = """
    <h1>Tracks</h1>
    <p>Click one track to set <b>Start</b>, then another to set <b>End</b>.</p>
    <ul id="trackList" style="list-style:none; padding-inline-start:0;">
    {% for t in tracks %}
      <li data-track-id="{{ t['id'] }}" style="margin-block-end:1rem; cursor:pointer; padding:8px; border-block-end:1px solid #eee;">
        <div><b>{{ t['name'] }}</b> — {{ t['artists'][0]['name'] }}</div>
        {% if t['preview_url'] %}
          <audio controls src="{{ t['preview_url'] }}"></audio>
        {% else %}
          <em>Preview not available. Open in Spotify for full playback.</em> · <a href="https://open.spotify.com/track/{{ t['id'] }}" target="_blank">Open in Spotify</a>
        {% endif %}
        <div style="font-size:12px; color:#666;">Track ID: {{ t['id'] }}</div>
      </li>
    {% endfor %}
    </ul>

    <hr>
    <h2>Build a smooth transition</h2>
    <div style="display:flex; gap:12px; align-items:center; flex-wrap:wrap;">
      <label>Start track ID: <input id="startId" style="inline-size:320px;" placeholder="click a track above" /></label>
      <label>End track ID: <input id="endId" style="inline-size:320px;" placeholder="click a track above" /></label>
      <button id="btnSuggest">Suggest bridge songs</button>
      <button id="btnSavePlaylist" disabled>Save as new playlist</button>
    </div>

    <div id="suggestions" style="margin-block-start:16px;"></div>
    <p style="margin-block-start:24px;"><a href="{{ url_for('playlists') }}">← Back to Playlists</a></p>

    <script>
      let lastSuggestions = [];

      document.querySelectorAll('#trackList li[data-track-id]').forEach(li => {
        li.addEventListener('click', () => {
          const id = li.getAttribute('data-track-id');
          const start = document.getElementById('startId');
          const end = document.getElementById('endId');
          if (!start.value) start.value = id;
          else if (!end.value) end.value = id;
          else start.value = id;
        });
      });

      document.getElementById('btnSuggest').addEventListener('click', async () => {
        const startId = document.getElementById('startId').value.trim();
        const endId = document.getElementById('endId').value.trim();
        const box = document.getElementById('suggestions');
        box.innerHTML = '<p>Loading…</p>';

        if (!startId || !endId) {
          box.innerHTML = '<p style="color:red;">Pick a Start and End track first.</p>';
          return;
        }

        try {
          const res = await fetch('/transition_between', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ track_id_1: startId, track_id_2: endId })
          });

          if (res.status === 401) {
            box.innerHTML = '<p style="color:red;">Session expired. Please <a href="/login">log in</a> again.</p>';
            return;
          }

          const contentType = res.headers.get('content-type') || '';
          if (!contentType.includes('application/json')) {
            box.innerHTML = '<p style="color:red;">Unexpected response. Try reloading the page.</p>';
            return;
          }

          const data = await res.json();
          if (data.error) {
            box.innerHTML = '<p style="color:red;">' + data.error + '</p>';
            return;
          }

          lastSuggestions = data.suggestions || [];
          if (lastSuggestions.length === 0) {
            box.innerHTML = '<p>No bridge suggestions found. Try different start/end songs.</p>';
            return;
          }

          const list = lastSuggestions.map(t => `
            <li style="margin-bottom:12px;">
              <b>${t.name}</b> — ${t.artist || ''}
              ${t.preview_url ? `<br><audio controls src="${t.preview_url}"></audio>` : `<br><em>No 30s preview</em> · <a href="https://open.spotify.com/track/${t.id}" target="_blank">Open in Spotify</a>`}
              <div style="margin-top:6px;">
                <label>Speed:
                  <input type="number" value="1.00" step="0.05" id="speed-${t.id}" style="width:80px;">
                </label>
                <label style="margin-left:8px;">Tempo (+/- bpm):
                  <input type="number" value="0" step="1" id="tempo-${t.id}" style="width:80px;">
                </label>
                <button onclick="saveSetting('${t.id}')">Save</button>
              </div>
            </li>
          `).join('');

          box.innerHTML = `<h3>Suggested bridge songs</h3><ol>${list}</ol>`;
          document.getElementById('btnSavePlaylist').disabled = false;

        } catch (err) {
          console.error(err);
          box.innerHTML = '<p style="color:red;">Network or server error. Check DevTools → Console.</p>';
        }
      });

      async function saveSetting(trackId) {
        const speed = parseFloat(document.getElementById('speed-'+trackId).value);
        const tempo = parseFloat(document.getElementById('tempo-'+trackId).value);
        await fetch('/settings/' + trackId, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ speed, tempo })
        });
        alert('Saved: ' + trackId + ' (speed=' + speed + ', tempo=' + tempo + ')');
      }

      document.getElementById('btnSavePlaylist').addEventListener('click', async () => {
        const startId = document.getElementById('startId').value.trim();
        const endId = document.getElementById('endId').value.trim();
        if (!startId || !endId) { alert('Pick a Start and End track first.'); return; }
        const bridgeIds = (lastSuggestions || []).map(x => x.id);
        const track_ids = [startId, ...bridgeIds, endId];
        const out = await fetch('/create_transition_playlist', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ name: 'Smooth Transition', track_ids })
        });
        const res = await out.json();
        if (res.error) { alert(res.error); return; }
        alert('Created playlist: ' + res.playlist_id);
      });
    </script>
    """
    return make_response(render_template_string(html_tracks, tracks=tracks))

@app.route("/transition_between", methods=["POST"])
def transition_between():
    sp = _spotify_client()
    if not sp:
        return jsonify({"error": "not_authenticated"}), 401
    try:
        data = request.get_json(force=True, silent=True) or {}
        track_id_1 = data.get("track_id_1")
        track_id_2 = data.get("track_id_2")
        if not track_id_1 or not track_id_2:
            return jsonify({"error": "track_id_1 and track_id_2 are required"}), 400

        # Log track IDs for debugging
        app.logger.info(f"Fetching audio features for tracks: {track_id_1}, {track_id_2}")

        f1 = sp.audio_features(track_id_1)
        f2 = sp.audio_features(track_id_2)
        if not f1 or not f2:
            app.logger.error(f"Failed to fetch audio features: {f1}, {f2}")
            return jsonify({"error": "Could not fetch audio features. Ensure the playlist tracks are accessible."}), 403

        avg_tempo = (f1[0]["tempo"] + f2[0]["tempo"]) / 2.0
        avg_energy = (f1[0]["energy"] + f2[0]["energy"]) / 2.0

        rec = sp.recommendations(
            seed_tracks=[track_id_1, track_id_2],
            target_tempo=avg_tempo,
            target_energy=avg_energy,
            limit=10,
        )

        suggestions = [
            {
                "name": t["name"],
                "artist": t["artists"][0]["name"] if t.get("artists") else "",
                "id": t["id"],
                "preview_url": t.get("preview_url"),
            }
            for t in rec.get("tracks", [])
            if t.get("id")
        ]
        return jsonify({"suggestions": suggestions})
    except spotipy.SpotifyException as e:
        app.logger.error(f"Spotify API error: {str(e)}")
        return jsonify({"error": "Spotify API error", "detail": str(e)}), 500
    except Exception as e:
        app.logger.error(f"Server error: {str(e)}")
        return jsonify({"error": "server_error", "detail": str(e)}), 500

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

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
