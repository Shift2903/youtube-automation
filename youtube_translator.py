import os
import re
import json
import isodate
import requests 
import time 
import sys
from datetime import datetime, timezone

import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- CONFIGURATION ---
CLIENT_SECRETS_FILE = "client_secrets.json" 
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
API_SERVICE_NAME = "youtube"
API_VERSION = "v3"
TARGET_LANGUAGES_TOP15 = [
    'en', 'es', 'hi', 'ar', 'pt', 'bn', 'ru', 'ja', 'de', 'fr', 'ko', 'tr', 'it', 'vi', 'id'
]
DEFAULT_VIDEO_LANGUAGE = "fr"
MAX_CHARS_PER_REQUEST = 480

# --- FONCTIONS ---
def get_authenticated_service():
    creds = None
    if 'TOKEN_JSON' in os.environ and 'CLIENT_SECRET_JSON' in os.environ:
        print("Authentification via les secrets GitHub...")
        token_info = json.loads(os.environ['TOKEN_JSON'])
        creds = Credentials.from_authorized_user_info(token_info, scopes=SCOPES)
        if creds.expired and creds.refresh_token:
            print("Rafraîchissement du token...")
            creds.refresh(google.auth.transport.requests.Request())
    else:
        print("Authentification via les fichiers locaux...")
        if os.path.exists(TOKEN_FILE):
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(google.auth.transport.requests.Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
    return build(API_SERVICE_NAME, API_VERSION, credentials=creds)

def advanced_translate_mymemory(text, source_language, target_language, email=""):
    if not text: return ""
    emoji_regex = re.compile(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002702-\U000027B0\U000024C2-\U0001F251]+')
    caps_regex = re.compile(r'\b[A-Z]{2,}\b')
    emojis = emoji_regex.findall(text)
    caps_words = caps_regex.findall(text)
    text_with_placeholders = text
    for i, emoji in enumerate(emojis): text_with_placeholders = text_with_placeholders.replace(emoji, f"__EMOJI_{i}__", 1)
    for i, word in enumerate(caps_words): text_with_placeholders = text_with_placeholders.replace(word, f"__CAPS_{i}__", 1)

    def translate_chunk(chunk_to_translate):
        if not chunk_to_translate.strip(): return chunk_to_translate
        retries = 3
        delay = 5
        for i in range(retries):
            try:
                url = f"https://api.mymemory.translated.net/get?q={requests.utils.quote(chunk_to_translate)}&langpair={source_language}|{target_language}"
                if email: url += f"&de={email}"
                response = requests.get(url, timeout=15)
                response.raise_for_status()
                data = response.json()
                return data['responseData']['translatedText']
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 429:
                    print(f"  -> Limite API atteinte. Tentative {i + 1}/{retries} dans {delay}s...")
                    time.sleep(delay)
                    delay *= 2
                else: return chunk_to_translate
            except Exception: return chunk_to_translate
        return chunk_to_translate

    translated_chunks = []
    for line in text_with_placeholders.split('\n'):
        if len(line) < MAX_CHARS_PER_REQUEST:
            translated_chunks.append(translate_chunk(line))
        else:
            sub_chunks = []
            remaining_line = line
            while len(remaining_line) > 0:
                chunk = remaining_line[:MAX_CHARS_PER_REQUEST]
                if len(remaining_line) > MAX_CHARS_PER_REQUEST:
                    last_space = chunk.rfind(' ')
                    if last_space != -1: chunk = chunk[:last_space]
                sub_chunks.append(translate_chunk(chunk))
                remaining_line = remaining_line[len(chunk):].lstrip()
            translated_chunks.append(" ".join(sub_chunks))
    translated_text_with_placeholders = "\n".join(translated_chunks)
    translated_caps_words = [translate_chunk(word) for word in caps_words]
    final_translated_text = translated_text_with_placeholders
    for i, emoji in enumerate(emojis): final_translated_text = final_translated_text.replace(f"__EMOJI_{i}__", emoji, 1)
    for i, translated_word in enumerate(translated_caps_words): final_translated_text = final_translated_text.replace(f"__CAPS_{i}__", translated_word.upper(), 1)
    return final_translated_text

def get_videos_details(youtube, video_ids):
    videos_details = []
    for i in range(0, len(video_ids), 50):
        try:
            response = youtube.videos().list(part="snippet,localizations,contentDetails,status", id=",".join(video_ids[i:i+50])).execute()
            videos_details.extend(response["items"])
        except HttpError as e: print(f"Erreur API (détails vidéos) : {e}")
    return videos_details

def get_all_video_ids_from_playlist(youtube, playlist_id):
    video_ids = []
    next_page_token = None
    try:
        print(f"Récupération de la playlist {playlist_id}...")
        while True:
            response = youtube.playlistItems().list(part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=next_page_token).execute()
            video_ids.extend([item["contentDetails"]["videoId"] for item in response["items"]])
            next_page_token = response.get("nextPageToken")
            if not next_page_token: break
        print(f"{len(video_ids)} vidéo(s) trouvée(s).")
        return video_ids
    except HttpError as e: print(f"Erreur API (playlist) : {e}"); return []

def process_videos(youtube, videos_to_process, email=""):
    if not videos_to_process:
        print("\nAucune vidéo à traduire pour cette sélection.")
        return
    print(f"\n{len(videos_to_process)} vidéo(s) prête(s) à être traduite(s).")
    print(f"\nDébut du traitement...")
    for index, video in enumerate(videos_to_process):
        video_id, original_snippet, original_title = video["id"], video["snippet"], video["snippet"]["title"]
        print("-" * 50 + f"\nVidéo {index + 1}/{len(videos_to_process)}: '{original_title}'")
        original_description = original_snippet.get("description", "")
        if original_description.strip().startswith("Video created by FL Studio"):
            original_description = "Découvrez cette compilation de moments forts sur BeamNG.drive ! Crashs, défis et physique réaliste au rendez-vous.\nN'oubliez pas de liker et de vous abonner pour plus d'aventures !\n#BeamNG #CrashCompilation #Gaming"
        original_lang = original_snippet.get("defaultLanguage", "fr")
        localizations_data = video.get('localizations', {})
        for lang_code in TARGET_LANGUAGES_TOP15:
            if lang_code == original_lang: continue
            print(f"  -> Traduction en '{lang_code}'...")
            translated_title = advanced_translate_mymemory(original_title, original_lang, lang_code, email)
            translated_description = advanced_translate_mymemory(original_description, original_lang, lang_code, email)
            if translated_title is not None and translated_description is not None:
                localizations_data[lang_code] = {"title": translated_title, "description": translated_description}
        if not localizations_data: print("=> Aucune traduction générée."); continue
        try:
            update_parts = ['localizations']
            update_body = {'id': video_id, 'localizations': localizations_data}

            # --- CORRECTION FINALE ---
            if not original_snippet.get('defaultLanguage'):
                print(f"=> Info : Langue par défaut non définie. Ajout de '{DEFAULT_VIDEO_LANGUAGE}'.")
                # On crée un snippet "propre" avec seulement les informations modifiables
                clean_snippet = {
                    'title': original_snippet['title'],
                    'description': original_snippet.get('description', ''),
                    'categoryId': original_snippet['categoryId'],
                    'defaultLanguage': DEFAULT_VIDEO_LANGUAGE
                }
                # On ajoute les tags s'ils existent et ne sont pas vides
                if original_snippet.get('tags'):
                    clean_snippet['tags'] = original_snippet['tags']
                
                update_body['snippet'] = clean_snippet
                update_parts.append('snippet')
            
            youtube.videos().update(part=','.join(update_parts), body=update_body).execute()
            print("=> ✅ Succès ! Traductions ajoutées/mises à jour.")
        except HttpError as e: print(f"=> ❌ Échec de la mise à jour : {e}")

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--auto':
        print("Lancement en mode de surveillance automatique...")
        email_address = os.environ.get('USER_EMAIL', "")
        youtube = get_authenticated_service()
        uploads_playlist_id = youtube.channels().list(part="contentDetails", mine=True).execute()['items'][0]['contentDetails']['relatedPlaylists']['uploads']
        all_video_ids = get_all_video_ids_from_playlist(youtube, uploads_playlist_id)
        if not all_video_ids:
            print("Aucune vidéo trouvée, fin de la tâche.")
            return
        
        all_videos_details = get_videos_details(youtube, all_video_ids)
        
        videos_of_the_day = []
        today_utc = datetime.now(timezone.utc).date()
        print(f"Date du jour (UTC) : {today_utc}. Filtrage des vidéos...")

        for video in all_videos_details:
            publish_time_str = video.get('status', {}).get('publishAt') or video.get('snippet', {}).get('publishedAt')
            if publish_time_str:
                publish_date = datetime.fromisoformat(publish_time_str.replace('Z', '+00:00')).date()
                if publish_date == today_utc:
                    videos_of_the_day.append(video)
        
        print(f"{len(videos_of_the_day)} vidéo(s) publiée(s) ou programmée(s) pour aujourd'hui.")
        videos_to_process = [v for v in videos_of_the_day if not v.get('localizations')]
        print(f"Mode auto : {len(videos_to_process)} nouvelle(s) vidéo(s) à traduire aujourd'hui.")
        process_videos(youtube, videos_to_process, email_address)
        print("Tâche de surveillance automatique terminée.")
        return
    else:
        # Ce mode est pour l'usage local, pour générer le token.json
        print("Lancement en mode interactif pour l'authentification...")
        get_authenticated_service()
        print("\nAuthentification réussie ! Le fichier 'token.json' a été créé ou mis à jour.")
        print("Vous pouvez maintenant fermer cette fenêtre.")

if __name__ == "__main__":
    main()
